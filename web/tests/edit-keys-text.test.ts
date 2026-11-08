import { describe, expect, it } from 'vitest';
import type { ChangeResult, OpResult, Termination } from '../src/contracts/scene.gen';
import { keyCommand, type KeyCommand, type KeyState } from '../src/edit/keys';
import { endName, failedApplyText, netboxReason, staleIndexes } from '../src/edit/text';

describe('keyboard decision table', () => {
  const base: KeyState = { dragging: false, tool: false, panelOpen: false, traceOn: false, selected: null };
  const k = (key: string, ctrl = false) => ({ key, ctrl });

  it('Esc peels one layer at a time: drag, tool, panel, trace, then the view zooms out', () => {
    const all: KeyState = { dragging: true, tool: true, panelOpen: true, traceOn: true, selected: 'rack' };
    expect(keyCommand(all, k('Escape'))).toBe('cancel_drag');
    expect(keyCommand({ ...all, dragging: false }, k('Escape'))).toBe('exit_tool');
    expect(keyCommand({ ...all, dragging: false, tool: false }, k('Escape'))).toBe('close_panel');
    expect(keyCommand({ ...base, traceOn: true }, k('Escape'))).toBe('clear_trace');
    expect(keyCommand(base, k('Escape'))).toBe('pass');
  });

  it('during a drag only Esc acts: every other key is swallowed (the basket and camera stay put)', () => {
    const d: KeyState = { ...base, dragging: true, panelOpen: true, selected: 'device' };
    for (const key of ['z', 'Delete', 'r', 't', 'Backspace', '+', '-', 'Enter', 'a']) {
      expect(keyCommand(d, k(key))).toBe('swallow');
      expect(keyCommand(d, k(key, true))).toBe('swallow');
    }
  });

  it('exhaustive: each selection kind gets only its own command', () => {
    const sels: KeyState['selected'][] = ['device', 'rack', 'port', 'other', null];
    const want: Record<string, Partial<Record<string, KeyCommand>>> = {
      Delete: { device: 'unplace' },
      r: { rack: 'rotate' },
      R: { rack: 'rotate' },
      t: { port: 'trace' },
      T: { port: 'trace' },
    };
    for (const sel of sels)
      for (const [key, by] of Object.entries(want)) expect(keyCommand({ ...base, panelOpen: sel !== null, selected: sel }, k(key))).toBe(by[String(sel)] ?? 'pass');
    for (const sel of sels) {
      expect(keyCommand({ ...base, selected: sel }, k('z', true))).toBe('undo');
      expect(keyCommand({ ...base, selected: sel }, k('Z', true))).toBe('undo');
      expect(keyCommand({ ...base, selected: sel }, k('r', true))).toBe('pass'); // Ctrl+R is the browser's
      expect(keyCommand({ ...base, selected: sel }, k('Backspace'))).toBe('pass');
    }
  });
});

const op = (index: number, status: OpResult['status'], error: OpResult['error'] = null): OpResult => ({
  index,
  op: 'place_device',
  client_ref: null,
  target: { kind: 'device', id: 10 + index },
  status,
  before: null,
  after: null,
  diff: [],
  error,
});
const result = (ops: OpResult[]): ChangeResult => ({ mode: 'apply', applied: false, write_enabled: true, ops });

describe('failed apply text (1-based op numbers only)', () => {
  it('NetBox 400 on op 3 after two writes: NetBox reason, written and skipped ops, never the proxy 0-based text', () => {
    const details = { partial: true, written_ops: [0, 1], skipped_ops: [3], outcome_unknown: false, netbox: { asset_tag: ['device with this asset tag already exists.'] } };
    const msg = "NetBox refused op 2 (place_device): NetBox answered HTTP 400. Ops [0, 1] were already written and stay in NetBox (see its changelog); ops [3] were skipped.";
    const t = failedApplyText(result([op(0, 'ok'), op(1, 'ok'), op(2, 'error', { code: 'validation_failed', message: msg, details }), op(3, 'skipped')]));
    expect(t).toBe('NetBox refused op 3 (place_device): asset_tag: device with this asset tag already exists. Ops 1, 2 were written and left the basket. Op 4 was not sent and stays in the basket.');
    expect(t).not.toMatch(/\[|op 0|Ops 0/);
  });

  it('first op refused: nothing written; one written op is singular', () => {
    const d0 = { partial: false, written_ops: [], skipped_ops: [1, 2], outcome_unknown: false, netbox: 'bad gateway' };
    expect(failedApplyText(result([op(0, 'error', { code: 'netbox_error', message: 'x op 0', details: d0 }), op(1, 'skipped'), op(2, 'skipped')]))).toBe(
      'NetBox refused op 1 (place_device): bad gateway. Nothing was written. Ops 2, 3 were not sent and stay in the basket.',
    );
    const d1 = { partial: true, written_ops: [0], skipped_ops: [], outcome_unknown: false };
    expect(failedApplyText(result([op(0, 'ok'), op(1, 'error', { code: 'netbox_error', message: 'm', details: d1 })]))).toBe(
      'NetBox refused op 2 (place_device): netbox_error. Op 1 was written and left the basket.',
    );
  });

  it('unknown outcome: says so and asks for the changelog', () => {
    const d = { partial: null, written_ops: [], skipped_ops: [], outcome_unknown: true };
    expect(failedApplyText(result([op(0, 'error', { code: 'netbox_unreachable', message: 'm', details: d })]))).toBe(
      'NetBox did not answer op 1 (place_device): it may or may not be written. The scene was reloaded; check the NetBox changelog before you apply again.',
    );
  });

  it('a planning refusal (no write details) quotes the proxy with a 1-based op number', () => {
    expect(failedApplyText(result([op(0, 'skipped'), op(1, 'error', { code: 'conflict', message: 'U42 is occupied by device 1', details: { field: 'position' } })]))).toBe(
      'Nothing was written: the proxy refused op 2 (the reason is in the list).',
    );
    const lock = { code: 'conflict' as const, message: 'Device nd-srv05 was changed in NetBox after you loaded it: reload, then stage this change again.', details: { field: 'if_unmodified_since' } };
    const r = result([op(0, 'skipped'), op(1, 'error', lock)]);
    expect(staleIndexes(r)).toEqual([1]);
    expect(failedApplyText(r)).toBe('Nothing was written: op 2 was staged on data that changed in NetBox since you loaded it. Reload from NetBox, then stage it again.');
    expect(staleIndexes(result([op(0, 'error', { code: 'conflict', message: 'U42 is occupied', details: { field: 'position' } })]))).toEqual([]);
  });

  it('netboxReason flattens NetBox error bodies', () => {
    expect(netboxReason({ detail: 'Permission denied.' })).toBe('Permission denied.');
    expect(netboxReason({ name: ['too long'], __all__: ['dup'] })).toBe('name: too long; dup');
    expect(netboxReason(null)).toBeNull();
  });
});

describe('trace end names', () => {
  const t = (o: Partial<Termination>): Termination => ({ kind: 'interface', id: 1, name: null, device_id: null, device_name: null, circuit_id: null, circuit_cid: null, site_id: null, ...o }) as Termination;
  it('patch panel ports say front/rear; circuits name their cid and term side', () => {
    expect(endName(t({ kind: 'rear_port', name: '1', device_name: 'nd-pp1' }))).toBe('nd-pp1 rear 1');
    expect(endName(t({ kind: 'front_port', name: '1', device_name: 'nd-pp1' }))).toBe('nd-pp1 front 1');
    expect(endName(t({ kind: 'interface', name: 'Te1/0/20', device_name: 'nd-core1' }))).toBe('nd-core1 Te1/0/20');
    expect(endName(t({ kind: 'circuit_termination', name: 'A', circuit_cid: 'EXC-1001', circuit_id: 1 }))).toBe('circuit EXC-1001 term A');
  });
});
