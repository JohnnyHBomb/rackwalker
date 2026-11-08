// The editor's keyboard as one decision table: a key in a state maps to exactly one command. Esc peels one layer at
// a time (drag, tool, panel, trace) and only then lets the view zoom out; during a drag nothing but Esc acts, so the
// basket and the camera never change under the pointer.

export type KeyCommand =
  | 'cancel_drag'
  | 'exit_tool'
  | 'close_panel'
  | 'clear_trace'
  | 'undo'
  | 'unplace'
  | 'rotate'
  | 'trace'
  /** Handled: nothing happens (a key during a drag). */
  | 'swallow'
  /** Not the editor's key: the view handles it (Esc/Backspace zoom out, +/- zoom, Enter). */
  | 'pass';

export interface KeyState {
  readonly dragging: boolean;
  readonly tool: boolean;
  readonly panelOpen: boolean;
  readonly traceOn: boolean;
  readonly selected: 'device' | 'rack' | 'port' | 'other' | null;
}

export function keyCommand(s: KeyState, k: { readonly key: string; readonly ctrl: boolean }): KeyCommand {
  if (k.key === 'Escape') {
    if (s.dragging) return 'cancel_drag';
    if (s.tool) return 'exit_tool';
    if (s.panelOpen) return 'close_panel';
    if (s.traceOn) return 'clear_trace';
    return 'pass';
  }
  if (s.dragging) return 'swallow';
  if (k.ctrl) return k.key === 'z' || k.key === 'Z' ? 'undo' : 'pass';
  if (k.key === 'Delete' && s.selected === 'device') return 'unplace';
  if ((k.key === 'r' || k.key === 'R') && s.selected === 'rack') return 'rotate';
  if ((k.key === 't' || k.key === 'T') && s.selected === 'port') return 'trace';
  return 'pass';
}
