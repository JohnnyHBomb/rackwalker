// The proxy's change routes (contracts/openapi.yaml): POST /api/changes/preview and /apply, and GET /api/health
// (write mode and the NetBox URL for "open in NetBox"). The proxy is the single source of validity; this client
// only carries bodies and turns non-2xx answers into SceneSourceError (same ErrorBody as the reads).
import type { ChangeResult, ChangeSet, HealthResponse } from '../contracts/scene.gen';
import { SceneSourceError, errorFrom } from '../engine/source';

export interface ChangeApi {
  health(): Promise<HealthResponse>;
  preview(cs: ChangeSet): Promise<ChangeResult>;
  apply(cs: ChangeSet): Promise<ChangeResult>;
}

export class HttpChangeApi implements ChangeApi {
  constructor(
    private readonly base = '',
    private readonly fetchFn: typeof fetch = (...a) => fetch(...a),
  ) {}

  private async call<T>(path: string, body?: ChangeSet): Promise<T> {
    let res: Response;
    try {
      res = await this.fetchFn(this.base + path, {
        method: body ? 'POST' : 'GET',
        headers: { Accept: 'application/json', ...(body ? { 'Content-Type': 'application/json' } : {}) },
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
