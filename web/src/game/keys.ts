// Remappable keys. Bindings are KeyboardEvent.code values (layout independent: KeyW is the key at W's place on any
// layout), up to three per action, stored with the player's settings in the game store.
import type { GameSettings, KeyBindings } from '../contracts/scene.gen';

export const KEY_ACTIONS = ['up', 'down', 'left', 'right', 'interact', 'cancel', 'menu'] as const;
export type KeyAction = (typeof KEY_ACTIONS)[number];
export const MAX_KEYS = 3;

export const DEFAULT_KEYS: KeyBindings = {
  up: ['ArrowUp', 'KeyW'],
  down: ['ArrowDown', 'KeyS'],
  left: ['ArrowLeft', 'KeyA'],
  right: ['ArrowRight', 'KeyD'],
  interact: ['Enter', 'Space', 'KeyE'],
  cancel: ['Escape'],
  menu: ['KeyM'],
};

export const DEFAULT_SETTINGS: GameSettings = { hint_level: 'normal', audio: { muted: false, volume: 0.6 }, reduce_motion: false, keys: DEFAULT_KEYS };

export const ACTION_LABELS: Readonly<Record<KeyAction, string>> = {
  up: 'Walk up', down: 'Walk down', left: 'Walk left', right: 'Walk right', interact: 'Talk / choose', cancel: 'Back / close', menu: 'Menu',
};

export function actionOf(keys: KeyBindings, code: string): KeyAction | null {
  return KEY_ACTIONS.find((a) => keys[a].includes(code)) ?? null;
}

/** Codes that are never bound: they belong to the browser or to text entry. */
const RESERVED = /^(Tab|F5|F11|F12|MetaLeft|MetaRight|ContextMenu|OSLeft|OSRight)$/;

/**
 * Binds `code` to `action` as its first key (keeping up to two of its old keys). The code moves away from any other
 * action, unless that would leave the other action with no key at all: then the answer says why and nothing changes.
 */
export function rebind(keys: KeyBindings, action: KeyAction, code: string): { keys: KeyBindings } | { error: string } {
  if (!/^[A-Za-z0-9]{1,24}$/.test(code) || RESERVED.test(code)) return { error: `${code || 'That key'} cannot be bound.` };
  const other = actionOf(keys, code);
  if (other && other !== action && keys[other].length === 1)
    return { error: `${keyName(code)} is the only key for "${ACTION_LABELS[other]}". Give that action another key first.` };
  const next = { ...keys } as Record<KeyAction, string[]>;
  if (other && other !== action) next[other] = keys[other].filter((k) => k !== code);
  next[action] = [code, ...keys[action].filter((k) => k !== code)].slice(0, MAX_KEYS);
  return { keys: next as KeyBindings };
}

/** A readable name for a KeyboardEvent.code. */
export function keyName(code: string): string {
  if (/^Key[A-Z]$/.test(code)) return code.slice(3);
  if (/^Digit\d$/.test(code)) return code.slice(5);
  const arrows: Record<string, string> = { ArrowUp: '↑', ArrowDown: '↓', ArrowLeft: '←', ArrowRight: '→' };
  return arrows[code] ?? code.replace(/(Left|Right)$/, ' $1');
}
