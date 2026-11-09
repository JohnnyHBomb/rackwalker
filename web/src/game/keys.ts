// Remappable keys. Bindings are KeyboardEvent.code values (layout independent: KeyW is the key at W's place on any
// layout), up to three per action, stored with the player's settings in the game store.
import type { GameSettings, KeyBindings } from '../contracts/scene.gen';

export const KEY_ACTIONS = ['up', 'down', 'left', 'right', 'interact', 'cancel', 'menu', 'zoom_in', 'zoom_out', 'search', 'rotate', 'trace', 'unrack', 'undo'] as const;
export type KeyAction = (typeof KEY_ACTIONS)[number];
export const MAX_KEYS = 3;

export const DEFAULT_KEYS: Required<KeyBindings> = {
  up: ['ArrowUp', 'KeyW'],
  down: ['ArrowDown', 'KeyS'],
  left: ['ArrowLeft', 'KeyA'],
  right: ['ArrowRight', 'KeyD'],
  interact: ['Enter', 'Space', 'KeyE'],
  cancel: ['Escape', 'Backspace'],
  menu: ['KeyM'],
  zoom_in: ['Equal', 'NumpadAdd'],
  zoom_out: ['Minus', 'NumpadSubtract'],
  search: ['Slash'],
  rotate: ['KeyR'],
  trace: ['KeyT'],
  unrack: ['Delete'],
  undo: ['KeyZ'],
};

/**
 * Inside a building the isometric view and the editor listen for these fixed keys; the shell turns the player's own
 * key for each action into this one, so every key used inside a building follows the remap.
 */
export const ISO_KEYS: Readonly<Partial<Record<KeyAction, { key: string; code: string; ctrl?: boolean }>>> = {
  interact: { key: 'Enter', code: 'Enter' },
  cancel: { key: 'Escape', code: 'Escape' },
  zoom_in: { key: '+', code: 'Equal' },
  zoom_out: { key: '-', code: 'Minus' },
  search: { key: '/', code: 'Slash' },
  rotate: { key: 'r', code: 'KeyR' },
  trace: { key: 't', code: 'KeyT' },
  unrack: { key: 'Delete', code: 'Delete' },
  undo: { key: 'z', code: 'KeyZ', ctrl: true },
};
/** The fixed keys of ISO_KEYS (and their other spellings) the view and editor would act on by themselves. */
export const ISO_FIXED = new Set(['Escape', 'Backspace', 'Enter', 'Equal', 'Minus', 'NumpadAdd', 'NumpadSubtract', 'Slash', 'KeyR', 'KeyT', 'Delete', 'KeyZ']);
/** The characters (KeyboardEvent.key) the view and editor act on, whatever key types them on the player's layout. */
export const ISO_FIXED_KEYS = new Set(['Escape', 'Backspace', 'Enter', '+', '=', '-', '/', 'r', 'R', 't', 'T', 'Delete', 'z', 'Z']);

export const DEFAULT_SETTINGS: GameSettings = { hint_level: 'normal', audio: { muted: false, volume: 0.6 }, reduce_motion: false, keys: DEFAULT_KEYS };

export const ACTION_LABELS: Readonly<Record<KeyAction, string>> = {
  up: 'Walk up', down: 'Walk down', left: 'Walk left', right: 'Walk right', interact: 'Talk / choose / go in', cancel: 'Back / close', menu: 'Menu',
  zoom_in: 'Zoom in (inside)', zoom_out: 'Zoom out (inside)', search: 'Search (inside)', rotate: 'Rotate a rack', trace: 'Trace a port',
  unrack: 'Unrack a device', undo: 'Undo (with Ctrl)',
};

export function actionOf(keys: KeyBindings, code: string): KeyAction | null {
  return KEY_ACTIONS.find((a) => keys[a]?.includes(code)) ?? null; // a missing action (older settings) has no key
}

/** Codes that are never bound: they belong to the browser or to text entry. */
const RESERVED = /^(Tab|F5|F11|F12|MetaLeft|MetaRight|ContextMenu|OSLeft|OSRight)$/;

/**
 * Binds `code` to `action` as its first key (keeping up to two of its old keys). The code moves away from any other
 * action, unless that would leave the other action with no key at all: then the answer says why and nothing changes.
 */
export function rebind<K extends KeyBindings>(keys: K, action: KeyAction, code: string): { keys: K } | { error: string } {
  if (!/^[A-Za-z0-9]{1,24}$/.test(code) || RESERVED.test(code)) return { error: `${code || 'That key'} cannot be bound.` };
  const other = actionOf(keys, code);
  if (other && other !== action && keys[other]?.length === 1)
    return { error: `${keyName(code)} is the only key for "${ACTION_LABELS[other]}". Give that action another key first.` };
  const next = { ...keys } as Record<KeyAction, string[]>;
  if (other && other !== action) next[other] = (keys[other] ?? []).filter((k) => k !== code);
  next[action] = [code, ...(keys[action] ?? []).filter((k) => k !== code)].slice(0, MAX_KEYS);
  return { keys: next as K };
}

/** The keys bound to `action`, for text the player reads ("↑/W"); an unknown action stays as written. */
export function keysLabel(keys: KeyBindings, action: string): string {
  const list = (KEY_ACTIONS as readonly string[]).includes(action) ? keys[action as KeyAction] : null;
  return list?.length ? list.map(keyName).join('/') : action;
}

/** A readable name for a KeyboardEvent.code. */
export function keyName(code: string): string {
  if (/^Key[A-Z]$/.test(code)) return code.slice(3);
  if (/^Digit\d$/.test(code)) return code.slice(5);
  const arrows: Record<string, string> = { ArrowUp: '↑', ArrowDown: '↓', ArrowLeft: '←', ArrowRight: '→' };
  return arrows[code] ?? code.replace(/(Left|Right)$/, ' $1');
}
