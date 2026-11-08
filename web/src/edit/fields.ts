// Info panel content: which contract fields an entity shows, which of them are editable (the update_fields
// whitelist of contracts/api.md, limited to fields the contract entity carries), and how an input's text becomes
// a field value. Pure, so the mapping is unit-tested without a DOM.
import type { Cable, DeviceInfo, UpdateFieldsTarget } from '../contracts/scene.gen';
import { THEME_IDS } from '../contracts/theme';
import type { Entity } from '../engine/entity';

export type InputKind = 'text' | 'number' | 'choice' | 'bool';

export interface Row {
  readonly field: string;
  /** What the panel calls the field (field names without "_id", spaces for underscores). */
  readonly label: string;
  readonly value: unknown;
  /** The value as shown, when it is not the raw value (a rack's name for rack_id). */
  readonly display?: string;
  /** Set when the field is on the update_fields whitelist for this kind. */
  readonly input: InputKind | null;
  /** Options of a 'choice' input; null = the empty choice (the field is nullable). */
  readonly choices?: readonly (string | null)[];
}

export interface PanelModel {
  readonly title: string;
  readonly kind: string;
  /** update_fields target kind, or null when nothing here is editable. */
  readonly editKind: UpdateFieldsTarget['kind'] | null;
  readonly id: number;
  readonly rows: readonly Row[];
  readonly netboxUrl: string | null;
}

/** contracts/api.md update_fields whitelist. */
export const WHITELIST: Readonly<Record<UpdateFieldsTarget['kind'], readonly string[]>> = {
  site: ['name', 'description', 'status', 'latitude', 'longitude', 'iso_theme'],
  location: ['name', 'description', 'status', 'iso_x', 'iso_y', 'iso_w', 'iso_h', 'iso_facing', 'latitude', 'longitude'],
  rack: ['name', 'description', 'status', 'comments', 'iso_x', 'iso_y', 'iso_facing', 'latitude', 'longitude'],
  device: ['name', 'status', 'serial', 'asset_tag', 'description', 'comments'],
  cable: ['label', 'status', 'type', 'color', 'length', 'length_unit'],
  interface: ['description', 'enabled'],
};

const NUMBER = new Set(['latitude', 'longitude', 'iso_x', 'iso_y', 'iso_w', 'iso_h', 'length']);
/** Fields a nullable text input sends as null when emptied (the rest send ""). */
const NULLABLE_TEXT = new Set(['name', 'asset_tag', 'label', 'type', 'color', 'length_unit', 'iso_theme']);

/**
 * NetBox 4.7 status choices per kind (dcim/choices.py). Presentation only: the select offers them, the proxy still
 * judges the value (a site may define more, so the current value is always offered too).
 */
export const STATUS: Readonly<Record<string, readonly string[]>> = {
  site: ['planned', 'staging', 'active', 'decommissioning', 'retired'],
  location: ['planned', 'staging', 'active', 'decommissioning', 'retired'],
  rack: ['reserved', 'available', 'planned', 'active', 'deprecated'],
  device: ['offline', 'active', 'planned', 'staged', 'failed', 'inventory', 'decommissioning'],
  cable: ['connected', 'planned', 'decommissioning'],
};
/** The contract's enums (scene.schema.json), typed against the generated types so drift fails to compile. */
const LENGTH_UNITS: readonly NonNullable<Cable['length_unit']>[] = ['km', 'm', 'cm', 'mi', 'ft', 'in'];
const FACINGS = ['N', 'E', 'S', 'W'] as const;

function inputFor(kind: UpdateFieldsTarget['kind'], field: string, value: unknown): Pick<Row, 'input' | 'choices'> {
  const withCurrent = (c: readonly (string | null)[]) => (typeof value === 'string' && !c.includes(value) ? [...c, value] : c);
  if (field === 'iso_facing') return { input: 'choice', choices: [null, ...FACINGS] };
  if (field === 'iso_theme') return { input: 'choice', choices: withCurrent([null, ...THEME_IDS]) };
  if (field === 'length_unit') return { input: 'choice', choices: withCurrent([null, ...LENGTH_UNITS]) };
  if (field === 'status' && STATUS[kind]) return { input: 'choice', choices: withCurrent(STATUS[kind]) };
  if (field === 'enabled') return { input: 'bool' };
  return { input: NUMBER.has(field) ? 'number' : 'text' };
}

/** Names of the objects that id fields point at (null when not known here): the panel and the basket show these. */
export interface Names {
  rack(id: number): string | null;
  location(id: number): string | null;
  site(id: number): string | null;
}

const LABELS: Readonly<Record<string, string>> = { u_height: 'height (U)', starting_unit: 'first U', primary_ip: 'primary IP', iso_w: 'iso width', iso_h: 'iso depth' };

/** Field name as people read it: "rack_id" -> "rack", "asset_tag" -> "asset tag" (previously: snake_case labels). */
export function fieldLabel(field: string): string {
  return LABELS[field] ?? field.replace(/_id$/, '').replace(/_/g, ' ');
}

/** A value as shown for `field`: id fields show the name of what they point at (previously: "rack_id 3"). */
export function showField(field: string, v: unknown, names: Names | null): string {
  if (typeof v === 'number' && names) {
    const kind = field === 'rack_id' ? 'rack' : field === 'location_id' ? 'location' : field === 'site_id' ? 'site' : null;
    if (kind) return names[kind](v) ?? `${kind} ${v}`;
  }
  return show(v);
}

/** NetBox paths for port kinds (Interface carries no netbox_url of its own). */
const PORT_PATH: Record<string, string> = {
  interface: 'dcim/interfaces',
  front_port: 'dcim/front-ports',
  rear_port: 'dcim/rear-ports',
  console_port: 'dcim/console-ports',
  console_server_port: 'dcim/console-server-ports',
  power_port: 'dcim/power-ports',
  power_outlet: 'dcim/power-outlets',
  power_feed: 'dcim/power-feeds',
  circuit_termination: 'circuits/circuit-terminations',
};

const ends = (t: readonly { device_name: string | null; circuit_cid: string | null; name: string | null }[]) =>
  t.map((e) => `${e.device_name ?? e.circuit_cid ?? '?'} ${e.name ?? ''}`.trim()).join(', ');

/**
 * Panel model for an entity. `info` (device only) adds DeviceInfo fields; `netboxBase` (from /api/health) builds
 * port links. Values are shown as the contract has them.
 */
export function panelModel(e: Entity, info: DeviceInfo | null = null, netboxBase: string | null = null, names: Names | null = null): PanelModel {
  const rows = (kind: UpdateFieldsTarget["kind"] | null, o: object, fields: readonly string[]): Row[] =>
    fields.map((f) => {
      const value = (o as Record<string, unknown>)[f];
      const shown = showField(f, value, names);
      return {
        field: f,
        label: fieldLabel(f),
        value,
        ...(shown !== show(value) ? { display: shown } : {}),
        ...(kind && WHITELIST[kind].includes(f) ? inputFor(kind, f, value) : { input: null }),
      };
    });
  switch (e.kind) {
    case 'site': {
      const d = e.data;
      return { title: d.name, kind: 'site', editKind: 'site', id: d.id, netboxUrl: d.netbox_url, rows: rows('site', d, ['name', 'slug', 'status', 'description', 'latitude', 'longitude', 'iso_theme']) };
    }
    case 'location': {
      const d = e.data;
      return {
        title: d.name,
        kind: 'location',
        editKind: 'location',
        id: d.id,
        netboxUrl: d.netbox_url,
        rows: rows('location', d, ['name', 'slug', 'iso_x', 'iso_y', 'iso_w', 'iso_h', 'iso_facing', 'latitude', 'longitude']),
      };
    }
    case 'rack': {
      const d = e.data;
      return {
        title: d.name,
        kind: 'rack',
        editKind: 'rack',
        id: d.id,
        netboxUrl: d.netbox_url,
        rows: rows('rack', d, ['name', 'status', 'u_height', 'starting_unit', 'location_id', 'iso_x', 'iso_y', 'iso_facing', 'latitude', 'longitude']),
      };
    }
    case 'device': {
      const d = e.data;
      const flat = { ...d, role: d.role.name, device_type: `${d.device_type.manufacturer} ${d.device_type.model} (${d.device_type.u_height}U)`, ...(info ?? {}) };
      const extra = info ? ['serial', 'asset_tag', 'platform', 'description', 'comments'] : [];
      return {
        title: d.name ?? `device ${d.id}`,
        kind: 'device',
        editKind: 'device',
        id: d.id,
        netboxUrl: d.netbox_url,
        rows: rows('device', flat, ['name', 'status', 'role', 'device_type', 'rack_id', 'position', 'face', 'primary_ip', ...extra]),
      };
    }
    case 'cable': {
      const d = e.data;
      const flat = { ...d, a_end: ends(d.a_terminations), b_end: ends(d.b_terminations) };
      return {
        title: d.label ?? `cable ${d.id}`,
        kind: 'cable',
        editKind: 'cable',
        id: d.id,
        netboxUrl: d.netbox_url,
        rows: rows('cable', flat, ['label', 'status', 'type', 'color', 'length', 'length_unit', 'a_end', 'b_end']),
      };
    }
    case 'port': {
      const d = e.data;
      const editable = d.kind === 'interface';
      const flat = { ...d, link_peers: ends(d.link_peers), connected_endpoint: d.connected_endpoint ? ends([d.connected_endpoint]) : null };
      return {
        title: d.name,
        kind: d.kind,
        editKind: editable ? 'interface' : null,
        id: d.id,
        netboxUrl: netboxBase ? `${netboxBase.replace(/\/$/, '')}/${PORT_PATH[d.kind]}/${d.id}/` : null,
        rows: rows(editable ? 'interface' : null, flat, ['kind', 'type', 'enabled', 'mgmt_only', 'description', 'cable_id', 'link_peers', 'connected_endpoint']),
      };
    }
    case 'link': {
      const d = e.data;
      const site = (id: number) => names?.site(id) ?? `site ${id}`;
      const flat = { sites: `${site(d.a_site_id)} - ${site(d.b_site_id)}`, cables: d.cable_ids.join(', '), circuits: d.circuits.map((c) => `${c.cid} (${c.provider}, ${c.status})`).join('; ') };
      return { title: d.circuits.map((c) => c.cid).join(', ') || 'site link', kind: 'link', editKind: null, id: 0, netboxUrl: null, rows: rows(null, flat, ['sites', 'cables', 'circuits']) };
    }
  }
}

/** Text shown for a value in the panel (and in the basket's diff). */
export function show(v: unknown): string {
  if (v === null || v === undefined) return '—';
  if (typeof v === 'string') return v === '' ? '""' : v;
  return JSON.stringify(v);
}

/** Parses an input's raw value; `undefined` = not a valid value for that input (nothing is staged). */
export function parseInput(field: string, input: InputKind, raw: string | boolean): unknown {
  if (input === 'bool') return Boolean(raw);
  const s = String(raw).trim();
  if (input === 'choice') return s === '' ? null : s; // a select offers only its choices; '' is the null choice
  if (input === 'number') {
    if (s === '') return null;
    const n = Number(s);
    if (!Number.isFinite(n)) return undefined;
    return field.startsWith('iso_') ? (Number.isInteger(n) ? n : undefined) : n;
  }
  return s === '' && NULLABLE_TEXT.has(field) ? null : String(raw);
}
