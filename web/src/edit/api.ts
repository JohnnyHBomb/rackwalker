// The proxy's change routes (contracts/openapi.yaml): POST /api/changes/preview and /apply, and GET /api/health
// (write mode and the NetBox URL for "open in NetBox"). The proxy is the single source of validity; this client
// only carries bodies and turns non-2xx answers into SceneSourceError (same ErrorBody as the reads).
// A write-enabled proxy previews and applies only for a signed-in player: the session cookie goes along on its own,
// and the session's CSRF token (GET /api/game/session) is sent as X-RackWalker-CSRF. The token is read once before
// the first change request, and read again when the proxy refuses it (a sign-in in another tab replaced the session).
import type { ChangeResult, ChangeSet, HealthResponse } from '../contracts/scene.gen';
import { SceneSourceError, errorFrom } from '../engine/source';

export interface ChangeApi {
  health(): Promise<HealthResponse>;
  preview(cs: ChangeSet): Promise<ChangeResult>;
  apply(cs: ChangeSet): Promise<ChangeResult>;
}

const CSRF_HEADER = 'X-RackWalker-CSRF';

export class HttpChangeApi implements ChangeApi {
  private csrf: string | null = null;
  private csrfRead = false;

  constructor(
    private readonly base = '',
    private readonly fetchFn: typeof fetch = (...a) => fetch(...a),
  ) {}

  private readCsrf(): Promise<string | null> {
    return this.once<{ csrf_token: string | null }>('/api/game/session').then((s) => s?.csrf_token ?? null, () => null);
  }

  private async call<T>(path: string, body?: ChangeSet): Promise<T> {
    if (body && !this.csrfRead) {
      this.csrfRead = true;
      this.csrf = await this.readCsrf();
    }
    try {
      return await this.once<T>(path, body);
    } catch (e) {
      if (!body || !(e instanceof SceneSourceError) || (e.code !== 'forbidden' && e.code !== 'unauthorized')) throw e;
      const before = this.csrf;
      this.csrf = await this.readCsrf();
      if (!this.csrf || this.csrf === before) throw e;
      return this.once<T>(path, body);
    }
  }

  private async once<T>(path: string, body?: ChangeSet): Promise<T> {
    let res: Response;
    try {
      res = await this.fetchFn(this.base + path, {
        method: body ? 'POST' : 'GET',
        credentials: 'same-origin',
        headers: {
          Accept: 'application/json',
          ...(body ? { 'Content-Type': 'application/json' } : {}),
          ...(body && this.csrf ? { [CSRF_HEADER]: this.csrf } : {}),
        },
        ...(body ? { body: JSON.stringify(body) } : {}),
      });
    } catch (e) {
      throw new SceneSourceError(0, 'netbox_unreachable', `Proxy unreachable: ${String(e)}`);
    }
    const json: unknown = await res.json().catch(() => null);
    if (!res.ok) throw errorFrom(res.status, json);
    return json as T;
  }

  health(): Promise<HealthResponse> {
    return this.call('/api/health');
  }
  preview(cs: ChangeSet): Promise<ChangeResult> {
    return this.call('/api/changes/preview', cs);
  }
  apply(cs: ChangeSet): Promise<ChangeResult> {
    return this.call('/api/changes/apply', cs);
  }
}
