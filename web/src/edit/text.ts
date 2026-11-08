// Texts the editor builds from proxy answers. Pure, so the wording is unit-tested. Op numbers shown to the user are
// 1-based everywhere (the basket list is numbered from 1); the contract's `index` and `written_ops` are 0-based.
import type { ChangeResult, Termination } from '../contracts/scene.gen';

interface FailDetails {
  written_ops?: number[];
  skipped_ops?: number[];
  outcome_unknown?: boolean;
  netbox?: unknown;
}

const opList = (ix: readonly number[]) => `${ix.length === 1 ? 'Op' : 'Ops'} ${ix.map((i) => i + 1).join(', ')}`;

/** NetBox's own error body in one line ({"asset_tag": ["... already exists."]} -> "asset_tag: ... already exists."). */
export function netboxReason(body: unknown): string | null {
  if (typeof body === 'string') return body.trim() || null;
  if (Array.isArray(body)) return body.map((b) => netboxReason(b)).filter(Boolean).join('; ') || null;
  if (body && typeof body === 'object') {
    const parts = Object.entries(body as Record<string, unknown>).map(([k, v]) => {
      const r = netboxReason(v);
      return r === null ? null : k === 'detail' || k === 'non_field_errors' || k === '__all__' ? r : `${k}: ${r}`;
    });
    return parts.filter(Boolean).join('; ') || null;
  }
  return null;
}

/**
 * The basket message for an apply that did not complete (`applied` false). The proxy's own error message numbers
 * ops from 0, so it is not quoted when the details say what happened; NetBox's reason is quoted instead.
 */
export function failedApplyText(r: ChangeResult): string {
  const failed = r.ops.find((o) => o.status === 'error');
  if (!failed?.error) return 'Nothing was written: the proxy refused the change set.';
  const n = failed.index + 1;
  const d = (failed.error.details ?? {}) as FailDetails;
  if (d.written_ops === undefined && d.outcome_unknown === undefined) {
    // Refused in the validation pass: each refused op shows its own reason in the list, so it is not repeated here.
    const stale = staleIndexes(r);
    if (stale.length)
      return `Nothing was written: ${opList(stale).toLowerCase()} ${stale.length === 1 ? 'was' : 'were'} staged on data that changed in NetBox since you loaded it. Reload from NetBox, then stage ${stale.length === 1 ? 'it' : 'them'} again.`;
    return `Nothing was written: the proxy refused op ${n} (the reason is in the list).`;
  }
  const written = d.written_ops ?? [];
  const skipped = d.skipped_ops ?? [];
  const head = d.outcome_unknown
    ? `NetBox did not answer op ${n} (${failed.op}): it may or may not be written.`
    : `NetBox refused op ${n} (${failed.op}): ${(netboxReason(d.netbox) ?? failed.error.code).replace(/\.?$/, '.')}`;
  const done = written.length ? ` ${opList(written)} ${written.length === 1 ? 'was' : 'were'} written and left the basket.` : d.outcome_unknown ? '' : ' Nothing was written.';
  const rest = skipped.length ? ` ${opList(skipped)} ${skipped.length === 1 ? 'was' : 'were'} not sent and ${skipped.length === 1 ? 'stays' : 'stay'} in the basket.` : '';
  const check = d.outcome_unknown ? ' The scene was reloaded; check the NetBox changelog before you apply again.' : '';
  return head + done + rest + check;
}

/** Ops refused by the optimistic lock (conflict on if_unmodified_since): staged on data NetBox has since changed. */
export function staleIndexes(r: ChangeResult | null): number[] {
  return (r?.ops ?? []).filter((o) => o.error?.code === 'conflict' && o.error.details?.field === 'if_unmodified_since').map((o) => o.index);
}

const SIDE: Partial<Record<Termination['kind'], string>> = { front_port: 'front ', rear_port: 'rear ' };

/** A cable end as the trace panel names it: patch-panel ports say front/rear (their names are often just "1"). */
export function endName(e: Termination): string {
  if (e.circuit_cid) return `circuit ${e.circuit_cid} term ${e.name ?? e.id}`;
  return `${e.device_name ?? '?'} ${SIDE[e.kind] ?? ''}${e.name ?? `${e.kind} ${e.id}`}`;
}
