// A stand-in for the proxy in the editor e2e: the page runs with source=http (editing is off on canned fixture data,
// a fixture id must never reach a live NetBox), and the stub serves every GET from fixtures/ through fixtures/index.json, exactly as the
// fixture source would, unless a test overrides a path (`gets`). For the change routes it answers
// in the contract's shapes: ChangeResult per op with `before` read from the fixtures, `after` = before + fields,
// and the 403 write_disabled ErrorResponse from fixtures/ in dry-run. Validity stays the proxy's job: a test says
// which ops the stub refuses (`refuse`), so the UI's handling of a proxy refusal is exercised, not re-implemented.
import { readFileSync, readdirSync } from 'node:fs';
import type { Page } from '@playwright/test';
import type { ChangeOp, ChangeResult, ChangeSet, ErrorBody, HealthResponse, OpResult } from '../src/contracts/scene.gen';

const FIX = new URL('../../fixtures/', import.meta.url);
const read = (f: string) => JSON.parse(readFileSync(new URL(f, FIX), 'utf8'));
const objects = new Map<string, Record<string, unknown>>();
for (const f of readdirSync(FIX).filter((n) => /^site-\d+\.json$/.test(n))) {
  const s = read(f);
  for (const r of s.racks) objects.set(`rack:${r.id}`, r);
  for (const d of s.devices) objects.set(`device:${d.id}`, d);
  for (const l of s.locations) objects.set(`location:${l.id}`, l);
  for (const c of s.cables) objects.set(`cable:${c.id}`, c);
  objects.set(`site:${s.site.id}`, s.site);
}

export interface Stub {
  /** Every ChangeSet the page sent, by route. */
  readonly sent: { route: 'preview' | 'apply'; body: ChangeSet }[];
  writeEnabled: boolean;
  /** Returns an error message (code conflict) or a whole ErrorBody for ops the "proxy" refuses. */
  refuse: (op: ChangeOp) => string | ErrorBody | null;
  /** Make the next apply fail at op `index` after writing the ops before it (contract failed-write shape). */
  failApplyAt: number | null;
  outcomeUnknown: boolean;
  /** Delay before each /preview answer (a slow proxy: answers arrive after the drop). */
  previewDelayMs: number;
  /** Delay before each /apply answer (an Apply still in flight while the user keeps editing). */
  applyDelayMs: number;
  /** Requests answered so far (equals sent.length once nothing is in flight). */
  answered: number;
  /** GET overrides by path ('/api/device/2'): a body (200) or an error status + ErrorResponse body. */
  readonly gets: Map<string, { status?: number; body: unknown }>;
  /** Every GET path the page read, in order. */
  readonly read: string[];
}

function opResult(op: ChangeOp, index: number, refuse: Stub['refuse']): OpResult {
  const fields = op.fields as Record<string, unknown>;
  const cur = op.target.id === null || typeof op.target.id === 'string' ? null : (objects.get(`${op.target.kind}:${op.target.id}`) ?? null);
  const keys = op.op === 'unplace_device' ? ['rack_id', 'position', 'face'] : Object.keys(fields);
  const before = cur ? Object.fromEntries(keys.map((k) => [k, cur[k] ?? null])) : null;
  const after = op.op === 'unplace_device' ? { rack_id: null, position: null, face: null } : { ...(before ?? {}), ...fields };
  const diff = keys.filter((k) => (before?.[k] ?? null) !== (after as Record<string, unknown>)[k]).map((k) => ({ field: k, before: before?.[k] ?? null, after: (after as Record<string, unknown>)[k] }));
  const why = refuse(op);
  return {
    index,
    op: op.op,
    client_ref: op.client_ref ?? null,
    target: { kind: op.target.kind, id: op.target.id },
    status: why ? 'error' : diff.length ? 'ok' : 'no_change',
    before,
    after: why ? null : after,
    diff: why ? [] : diff,
    error: !why ? null : typeof why === 'string' ? { code: 'conflict', message: why, details: null } : why,
  };
}

export async function stubProxy(page: Page, init: Partial<Pick<Stub, 'writeEnabled' | 'refuse'>> = {}): Promise<Stub> {
  const stub: Stub = {
    sent: [], writeEnabled: init.writeEnabled ?? false, refuse: init.refuse ?? (() => null), failApplyAt: null, outcomeUnknown: false,
    previewDelayMs: 0, applyDelayMs: 0, answered: 0, gets: new Map(), read: [],
  };
  const index = read('index.json').fixtures as { method: string; path: string; status: number; file: string }[];
  await page.route(/\/api\/(scene|device|trace)\//, (r) => {
    const path = new URL(r.request().url()).pathname;
    stub.read.push(path);
    const o = stub.gets.get(path);
    if (o) return r.fulfill({ status: o.status ?? 200, json: o.body });
    const f = index.find((x) => x.method === 'GET' && x.path === path);
    if (!f) return r.fulfill({ status: 404, json: { error: { code: 'not_found', message: `No fixture for GET ${path}.`, details: { path } } } });
    return r.fulfill({ status: f.status, json: read(f.file) });
  });
  await page.route('**/api/health', (r) => {
    const h: HealthResponse = { ...read('health.json'), write_enabled: stub.writeEnabled };
    return r.fulfill({ json: h });
  });
  await page.route('**/api/changes/*', async (r) => {
    const route = r.request().url().endsWith('/apply') ? 'apply' : 'preview';
    const body = r.request().postDataJSON() as ChangeSet;
    stub.sent.push({ route, body });
    if (route === 'preview' && stub.previewDelayMs) await new Promise((res) => setTimeout(res, stub.previewDelayMs));
    if (route === 'apply' && stub.applyDelayMs) await new Promise((res) => setTimeout(res, stub.applyDelayMs));
    stub.answered++;
    if (route === 'apply' && !stub.writeEnabled) return r.fulfill({ status: 403, json: read('error-write-disabled.json') });
    const ops = body.ops.map((op, i) => opResult(op, i, stub.refuse));
    const bad = ops.some((o) => o.status === 'error');
    if (bad) for (const o of ops) if (o.status !== 'error') Object.assign(o, { status: 'skipped', after: null });
    if (route === 'apply' && !bad && stub.failApplyAt !== null) {
      const k = stub.failApplyAt;
      const details = { partial: stub.outcomeUnknown ? null : k > 0, written_ops: [...Array(k).keys()], skipped_ops: ops.slice(k + 1).map((o) => o.index), outcome_unknown: stub.outcomeUnknown, netbox: null };
      ops[k] = { ...ops[k]!, status: 'error', after: null, error: { code: stub.outcomeUnknown ? 'netbox_unreachable' : 'netbox_error', message: 'NetBox refused the write.', details } };
      for (const o of ops.slice(k + 1)) Object.assign(o, { status: 'skipped', after: null });
      return r.fulfill({ json: { mode: 'apply', applied: false, write_enabled: true, ops } satisfies ChangeResult });
    }
    const res: ChangeResult = { mode: route, applied: route === 'apply' && !bad, write_enabled: stub.writeEnabled, ops };
    return r.fulfill({ json: res });
  });
  return stub;
}
