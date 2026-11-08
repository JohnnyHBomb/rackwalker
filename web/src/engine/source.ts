// SceneSource: the one data interface of the engine. FixtureSceneSource serves fixtures/ (the engine runs on
// them alone); HttpSceneSource talks to the proxy with the routes of contracts/openapi.yaml.
import type {
  DeviceDetail,
  ErrorCode,
  ErrorResponse,
  PortKind,
  RackScene,
  SiteScene,
  TraceResponse,
  WorldResponse,
} from '../contracts/scene.gen';

export interface SceneSource {
  world(): Promise<WorldResponse>;
  site(siteId: number): Promise<SiteScene>;
  rack(rackId: number): Promise<RackScene>;
  device(deviceId: number): Promise<DeviceDetail>;
  trace(kind: PortKind, portId: number): Promise<TraceResponse>;
}

/** A failed read, carrying the contract ErrorBody (api.md "Errors: one shape"). */
export class SceneSourceError extends Error {
  constructor(
    readonly status: number,
    readonly code: ErrorCode,
    message: string,
    readonly details: Record<string, unknown> | null = null,
  ) {
    super(message);
    this.name = 'SceneSourceError';
  }
}

/** The GET paths of the API, shared by both sources so they cannot drift apart. */
export const PATHS = {
  world: () => '/api/scene/world',
  site: (id: number) => `/api/scene/site/${id}`,
  rack: (id: number) => `/api/scene/rack/${id}`,
  device: (id: number) => `/api/device/${id}`,
  trace: (kind: PortKind, id: number) => `/api/trace/${kind}/${id}`,
} as const;

abstract class PathSource implements SceneSource {
  protected abstract get<T>(path: string): Promise<T>;
  world() {
    return this.get<WorldResponse>(PATHS.world());
  }
  site(id: number) {
    return this.get<SiteScene>(PATHS.site(id));
  }
  rack(id: number) {
    return this.get<RackScene>(PATHS.rack(id));
  }
  device(id: number) {
    return this.get<DeviceDetail>(PATHS.device(id));
  }
  trace(kind: PortKind, id: number) {
    return this.get<TraceResponse>(PATHS.trace(kind, id));
  }
}

export interface FixtureIndex {
  fixtures: { method: string; path: string; status: number; file: string }[];
}

/**
 * Serves fixtures/ through fixtures/index.json (request -> file). `load(file)` reads one fixture file; the browser
 * passes a Vite glob loader, the tests read from disk. A GET path that the catalogue does not list answers
 * 404 not_found, like the proxy would; a catalogued non-2xx entry is raised with its own ErrorBody.
 */
export class FixtureSceneSource extends PathSource {
  constructor(
    private readonly index: FixtureIndex,
    private readonly load: (file: string) => Promise<unknown>,
  ) {
    super();
  }
  protected async get<T>(path: string): Promise<T> {
    const entry = this.index.fixtures.find((f) => f.method === 'GET' && f.path === path);
    if (!entry) throw new SceneSourceError(404, 'not_found', `No fixture for GET ${path}.`, { path });
    const body = await this.load(entry.file);
    if (entry.status >= 300) throw errorFrom(entry.status, body);
    return body as T;
  }
}

/** Talks to the proxy. `base` is '' when the page is served next to /api (vite proxy, production). */
export class HttpSceneSource extends PathSource {
  constructor(
    private readonly base = '',
    private readonly fetchFn: typeof fetch = (...a) => fetch(...a),
  ) {
    super();
  }
  protected async get<T>(path: string): Promise<T> {
    let res: Response;
    try {
      res = await this.fetchFn(this.base + path, { headers: { Accept: 'application/json' } });
    } catch (e) {
      throw new SceneSourceError(0, 'netbox_unreachable', `Proxy unreachable: ${String(e)}`);
    }
    const body: unknown = await res.json().catch(() => null);
    if (!res.ok) throw errorFrom(res.status, body);
    return body as T;
  }
}

export function errorFrom(status: number, body: unknown): SceneSourceError {
  const err = (body as Partial<ErrorResponse> | null)?.error;
  if (err && typeof err.code === 'string') return new SceneSourceError(status, err.code, err.message, err.details);
  return new SceneSourceError(status, 'internal', `HTTP ${status} without an ErrorResponse body.`);
}
