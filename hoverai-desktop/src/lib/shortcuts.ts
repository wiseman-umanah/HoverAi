/**
 * Shortcut capture helpers shared by ShortcutPage (onboarding) and SettingsPage.
 *
 * Combos are built from `KeyboardEvent.code` (the physical key), not `.key`
 * (the produced character): with `.key`, Ctrl+Shift+1 became "!" and
 * Option+S on macOS became "ß", which no accelerator parser accepts.
 *
 * Two string forms:
 *   display     — "Ctrl+Shift+H", "Cmd+Option+K" (shown in the UI, split on "+")
 *   accelerator — "CommandOrControl+Shift+H"      (stored + sent to Rust)
 * On macOS Cmd is CommandOrControl and Ctrl is Control; elsewhere Ctrl is
 * CommandOrControl and the Windows/Super key is Super.
 */

const isMac = /Mac|iPhone|iPad/.test(navigator.userAgent)

const MODIFIER_CODES = new Set([
  'ControlLeft', 'ControlRight', 'ShiftLeft', 'ShiftRight', 'AltLeft', 'AltRight',
  'MetaLeft', 'MetaRight', 'OSLeft', 'OSRight', 'CapsLock', 'Fn', 'FnLock',
])

/** "KeyA" → "A", "Digit1" → "1", "ArrowUp" → "Up"; others unchanged ("F9", "Space"). */
function keyLabel(code: string): string {
  if (code.startsWith('Key')) return code.slice(3)
  if (code.startsWith('Digit')) return code.slice(5)
  if (code.startsWith('Arrow')) return code.slice(5)
  return code
}

/**
 * KeyboardEvent → display combo, or '' if it isn't a usable global shortcut
 * (no modifier, or Shift alone with a normal key — that would block typing).
 */
export function formatCombo(e: KeyboardEvent): string {
  if (!e.code || e.code === 'Unidentified' || MODIFIER_CODES.has(e.code)) return ''
  const mods: string[] = []
  if (isMac) {
    if (e.metaKey) mods.push('Cmd')
    if (e.ctrlKey) mods.push('Ctrl')
    if (e.altKey) mods.push('Option')
  } else {
    if (e.ctrlKey) mods.push('Ctrl')
    if (e.metaKey) mods.push('Super')
    if (e.altKey) mods.push('Alt')
  }
  if (e.shiftKey) mods.push('Shift')
  const isFunctionKey = /^F\d{1,2}$/.test(e.code)
  if (mods.length === 0) return ''
  if (mods.length === 1 && mods[0] === 'Shift' && !isFunctionKey) return ''
  return [...mods, keyLabel(e.code)].join('+')
}

/** Display combo → accelerator stored in settings and registered by Rust. */
export function toAccelerator(display: string): string {
  return display
    .split('+')
    .map((part) => {
      switch (part) {
        case 'Cmd': return 'CommandOrControl'
        case 'Ctrl': return isMac ? 'Control' : 'CommandOrControl'
        case 'Option': return 'Alt'
        case 'Meta': return isMac ? 'CommandOrControl' : 'Super'
        default: return part
      }
    })
    .join('+')
}

/** Accelerator → display combo (inverse of toAccelerator). */
export function displayShortcut(accelerator: string): string {
  return accelerator
    .split('+')
    .map((part) => {
      switch (part) {
        case 'CommandOrControl':
        case 'CmdOrCtrl': return isMac ? 'Cmd' : 'Ctrl'
        case 'Control': return 'Ctrl'
        case 'Alt': return isMac ? 'Option' : 'Alt'
        case 'Super': return isMac ? 'Cmd' : 'Super'
        default: return part
      }
    })
    .join('+')
}
