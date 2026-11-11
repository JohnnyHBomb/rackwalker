// The proxy's game routes (contracts/openapi.yaml, /api/game/*): who is playing, and the game's own state (save
// slots, settings, dialogue flags). The session is an HttpOnly cookie the page never sees; every write carries the
// session's CSRF token in the X-RackWalker-CSRF header. Game state lives in the proxy's SQLite file, never in NetBox.
import type { Catalogue, ChangeResult, Flags, FlagsPatch, GameSession, GameSettings, GameState, GapList, PackList, Quest, QuestAnswer, QuestBoard, QuestScope, QuestVerdict, SaveData, SaveSlot, SaveSlotId } from '../contracts/scene.gen';
import { SceneSourceError, errorFrom } from '../engine/source';

export const CSRF_HEADER = 'X-RackWalker-CSRF';

export interface GameApi {
  session(): Promise<GameSession>;
  signIn(token: string): Promise<GameSession>;
  signOut(): Promise<GameSession>;
  state(): Promise<GameState>;
  save(slot: SaveSlotId, data: SaveData): Promise<SaveSlot>;
  settings(s: GameSettings): Promise<GameSettings>;
  flags(patch: FlagsPatch['flags']): Promise<Flags>;
  /** Quests (contract 2.3.0): the log for a site or the whole estate, on the player's NetBox or the practice island. */
  questBoard(scope: QuestScope, siteId?: number | null, fresh?: boolean): Promise<QuestBoard>;
  questAnswer(a: QuestAnswer): Promise<Quest>;
  questPreview(scope: QuestScope, questId: string): Promise<ChangeResult>;
  questApply(scope: QuestScope, questId: string): Promise<QuestVerdict>;
  questVerify(questId: string): Promise<QuestVerdict>;
  practiceReset(): Promise<QuestBoard>;
  bossAck(scope: QuestScope, siteId: number): Promise<QuestBoard>;
  gaps(siteId: number, offset?: number, limit?: number): Promise<GapList>;
  /** The device catalogue (contract 2.5.0): the models of the player's own NetBox, verified where the device-type library confirms them. */
  catalogue(fresh?: boolean): Promise<Catalogue>;
  /** The packs the game ships with: what each is complete for, and where it came from. */
  packs(): Promise<PackList>;
}

export class HttpGameApi implements GameApi {
  private csrf: string | null = null;

  constructor(
    private readonly base = '',
    private readonly fetchFn: typeof fetch = (...a) => fetch(...a),
  ) {}

  private async call<T>(method: string, path: string, body?: unknown): Promise<T> {
    let res: Response;
    try {
      res = await this.fetchFn(this.base + path, {
        method,
        credentials: 'same-origin',
        headers: {
          Accept: 'application/json',
          ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}),
          ...(method !== 'GET' ? { [CSRF_HEADER]: this.csrf ?? 'sign-in' } : {}),
        },
        ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
      });
    } catch (e) {
      throw new SceneSourceError(0, 'netbox_unreachable', `Proxy unreachable: ${String(e)}`);
    }
    const json: unknown = await res.json().catch(() => null);
    if (!res.ok) throw errorFrom(res.status, json);
    if (json === null || typeof json !== 'object') throw new SceneSourceError(res.status, 'internal', 'The proxy sent an answer that is not a JSON object.');
    return json as T;
  }

  private keep(s: GameSession): GameSession {
    this.csrf = s.csrf_token;
    return s;
  }

  async session(): Promise<GameSession> {
    return this.keep(await this.call<GameSession>('GET', '/api/game/session'));
  }
  async signIn(token: string): Promise<GameSession> {
    return this.keep(await this.call<GameSession>('POST', '/api/game/session', { token }));
  }
  async signOut(): Promise<GameSession> {
    return this.keep(await this.call<GameSession>('DELETE', '/api/game/session'));
  }
  state(): Promise<GameState> {
    return this.call('GET', '/api/game/state');
  }
  save(slot: SaveSlotId, data: SaveData): Promise<SaveSlot> {
    return this.call('PUT', `/api/game/saves/${slot}`, data);
  }
  settings(s: GameSettings): Promise<GameSettings> {
    return this.call('PUT', '/api/game/settings', s);
  }
  flags(patch: FlagsPatch['flags']): Promise<Flags> {
    return this.call('PATCH', '/api/game/flags', { flags: patch } satisfies FlagsPatch);
  }
  questBoard(scope: QuestScope, siteId: number | null = null, fresh = false): Promise<QuestBoard> {
    return this.call('GET', `/api/game/quests?scope=${scope}${siteId ? `&site_id=${siteId}` : ''}${fresh ? '&fresh=true' : ''}`);
  }
  questAnswer(a: QuestAnswer): Promise<Quest> {
    return this.call('POST', '/api/game/quests/answer', a);
  }
  questPreview(scope: QuestScope, questId: string): Promise<ChangeResult> {
    return this.call('POST', '/api/game/quests/preview', { scope, quest_id: questId });
  }
  questApply(scope: QuestScope, questId: string): Promise<QuestVerdict> {
    return this.call('POST', '/api/game/quests/apply', { scope, quest_id: questId });
  }
  questVerify(questId: string): Promise<QuestVerdict> {
    return this.call('POST', '/api/game/quests/verify', { scope: 'netbox', quest_id: questId });
  }
  practiceReset(): Promise<QuestBoard> {
    return this.call('POST', '/api/game/practice/reset', {});
  }
  bossAck(scope: QuestScope, siteId: number): Promise<QuestBoard> {
    return this.call('POST', '/api/game/boss/ack', { scope, site_id: siteId });
  }
  gaps(siteId: number, offset = 0, limit = 100): Promise<GapList> {
    return this.call('GET', `/api/quests/gaps/${siteId}?offset=${offset}&limit=${limit}`);
  }
  catalogue(fresh = false): Promise<Catalogue> {
    return this.call('GET', `/api/game/catalogue${fresh ? '?fresh=true' : ''}`);
  }
  packs(): Promise<PackList> {
    return this.call('GET', '/api/packs');
  }
}
