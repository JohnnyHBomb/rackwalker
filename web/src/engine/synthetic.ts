// Synthetic scenes for the performance check: one site, one room, N devices in 42U racks. Deterministic.
import type { Device, Rack, SiteScene, WorldResponse } from '../contracts/scene.gen';
import type { SceneSource } from './source';
import { SceneSourceError } from './source';

const META = { contract_version: '2.2.0', generated_at: '2026-11-07T00:00:00Z', netbox_version: null, source: 'fixture' } as const;
const ROLES = [
  { id: 1, slug: 'switch', name: 'Switch', color: '2196f3' },
  { id: 2, slug: 'server', name: 'Server', color: '4caf50' },
  { id: 3, slug: 'router', name: 'Router', color: 'ff9800' },
  { id: 4, slug: 'patch-panel', name: 'Patch panel', color: '9e9e9e' },
];

/** `devices` 1U devices, 40 per rack, racks on a square-ish room grid with a free aisle every other row. */
export function syntheticSite(devices: number): SiteScene {
  const perRack = 40;
  const nRacks = Math.ceil(devices / perRack);
  const cols = Math.ceil(Math.sqrt(nRacks * 2));
  const rows = Math.ceil(nRacks / cols);
  const site = { id: 1, slug: 'perf', name: 'Perf Site', status: 'active', latitude: 52, longitude: 0, iso_theme: null, country: null, description: '', last_updated: null, netbox_url: null };
  const racks: Rack[] = [];
  const devs: Device[] = [];
  for (let r = 0; r < nRacks; r++)
    racks.push({
      id: r + 1, site_id: 1, location_id: 1, name: `R${String(r + 1).padStart(3, '0')}`, status: 'active', u_height: 42, starting_unit: 1,
      desc_units: false, iso_x: 1 + (r % cols), iso_y: 1 + 2 * Math.floor(r / cols), iso_facing: 'S', latitude: null, longitude: null, last_updated: null, netbox_url: null,
    });
  for (let i = 0; i < devices; i++) {
    const role = ROLES[i % ROLES.length]!;
    devs.push({
      id: i + 1, name: `dev${i + 1}`, role, device_type: { id: 1, manufacturer: 'Synthetic', model: 'S1', slug: 's1', u_height: 1, is_full_depth: false },
      site_id: 1, location_id: 1, rack_id: Math.floor(i / perRack) + 1, position: 1 + (i % perRack), face: 'front', status: 'active',
      primary_ip: null, last_updated: null, netbox_url: null,
    });
  }
  return {
    meta: META,
    site,
    locations: [{ id: 1, site_id: 1, parent_id: null, name: 'Big Hall', slug: 'big-hall', iso_x: 0, iso_y: 0, iso_w: cols + 2, iso_h: 2 * rows + 1, iso_facing: 'S', latitude: null, longitude: null, last_updated: null, netbox_url: null }],
    racks,
    devices: devs,
    cables: [],
  };
}

export class SyntheticSceneSource implements SceneSource {
  private readonly s: SiteScene;
  constructor(devices: number) {
    this.s = syntheticSite(devices);
  }
  async world(): Promise<WorldResponse> {
    return { meta: META, sites: [{ site: this.s.site, counts: { locations: 1, racks: this.s.racks.length, devices: this.s.devices.length, unracked_devices: 0, cables: 0 } }], links: [] };
  }
  async site(id: number): Promise<SiteScene> {
    if (id !== 1) throw new SceneSourceError(404, 'not_found', `Site ${id} does not exist.`);
    return this.s;
  }
  rack(): never {
    throw new SceneSourceError(404, 'not_found', 'The synthetic source serves the world, the site and its room only.');
  }
  device(): never {
    return this.rack();
  }
  trace(): never {
    return this.rack();
  }
}
