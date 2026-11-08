// The change basket: staged edits as an ordered ChangeSet (contracts/api.md "Changes: preview and apply"). The
// proxy plans ops in order against its own copy of NetBox state, so the basket never merges or rewrites ops: one
// user action = one op, undo removes it (and any later op that names an object it creates).
import type { ChangeOp, ChangeSet, Device } from '../contracts/scene.gen';

export interface Staged {
  readonly op: ChangeOp;
  /** One line for the basket ("ND-R2 -> tile 4,3"). */
  readonly label: string;
  /** place_device: the device as the drag saw it (the staged view needs it in racks that did not hold it). */
  readonly device?: Device;
  /** place_device: the destination rack's location (NetBox moves the device there too). */
  readonly rackLocationId?: number | null;
}

/** "@ref" values an op reads (target id, rack_id, location_id). */
export function refsOf(op: ChangeOp): string[] {
  const vals: unknown[] = [op.target.id, (op.fields as Record<string, unknown>).rack_id, (op.fields as Record<string, unknown>).location_id];
  return vals.filter((v): v is string => typeof v === 'string' && v.startsWith('@')).map((v) => v.slice(1));
}

export class Basket {
  private items: Staged[] = [];
  private seq = 0;
  private readonly listeners = new Set<() => void>();

  get entries(): readonly Staged[] {
    return this.items;
  }
  get size(): number {
    return this.items.length;
  }
  onChange(f: () => void): () => void {
    this.listeners.add(f);
    return () => this.listeners.delete(f);
  }
  private emit(): void {
    for (const f of this.listeners) f();
  }

  /** A client_ref unique in this basket, for create_rack. */
  nextRef(): string {
    return `newrack${++this.seq}`;
  }

  add(s: Staged): void {
    this.items = [...this.items, s];
    this.emit();
  }

  /** Removes op `index` and every later op that depends on an object it creates (transitively). */
  undo(index: number): void {
    if (index < 0 || index >= this.items.length) return;
    const gone = new Set<string>();
    const keep: Staged[] = [];
    this.items.forEach((s, i) => {
      const drop = i === index || refsOf(s.op).some((r) => gone.has(r));
      if (drop && s.op.client_ref) gone.add(s.op.client_ref);
      if (!drop) keep.push(s);
    });
    this.items = keep;
    this.emit();
  }

  undoLast(): void {
    this.undo(this.items.length - 1);
  }

  /** Drops the ops at these indexes (stale ops a reload replaces). */
  removeIndexes(indexes: readonly number[]): void {
    this.settleWritten(indexes);
  }

  /**
   * After an apply that stopped part way: drops the ops NetBox already wrote and points every op left at the racks
   * those ops created (`created`: client_ref -> NetBox id). The ops left then stand on their own (previously: a
   * leftover op still named "@newrack1", whose create_rack had left the basket, so the proxy refused it as not_found
   * and the staged view drew the device unracked).
   */
  settleWritten(indexes: readonly number[], created: ReadonlyMap<string, number> = new Map()): void {
    const set = new Set(indexes);
    const real = (v: unknown) => (typeof v === 'string' && v.startsWith('@') && created.has(v.slice(1)) ? created.get(v.slice(1))! : v);
    this.items = this.items
      .filter((_, i) => !set.has(i))
      .map((s) => {
        if (!refsOf(s.op).some((r) => created.has(r))) return s;
        const f = { ...(s.op.fields as Record<string, unknown>) };
        for (const k of ['rack_id', 'location_id']) if (k in f) f[k] = real(f[k]);
        return { ...s, op: { ...s.op, target: { ...s.op.target, id: real(s.op.target.id) }, fields: f } as ChangeOp };
      });
    this.emit();
  }

  clear(): void {
    this.items = [];
    this.emit();
  }

  /** The ChangeSet to send (plus an optional candidate op at the end, for a drag preview); null when empty. */
  changeSet(candidate?: ChangeOp, reason?: string): ChangeSet | null {
    const ops = [...this.items.map((s) => s.op), ...(candidate ? [candidate] : [])];
    if (!ops.length) return null;
    return { ops: ops as ChangeSet['ops'], ...(reason ? { reason } : {}) };
  }
}
