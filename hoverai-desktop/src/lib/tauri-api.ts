/**
 * tauri-api.ts — drop-in replacement for the Electron window.api / preload bridge.
 *
 * Every function here has the same signature as the old window.api entry so
 * call sites throughout the renderer need zero changes (they import this module
 * instead of calling window.api).
 *
 * Request-response channels → invoke()  from @tauri-apps/api/core
 * Push events from Rust     → listen()  from @tauri-apps/api/event
 */

import { invoke } from '@tauri-apps/api/core'
import { listen, type UnlistenFn } from '@tauri-apps/api/event'
import { openUrl } from '@tauri-apps/plugin-opener'
import type { AppSettings } from './settings-types'

// ─── Re-export types the renderer uses ───────────────────────────────────────

export type { AppSettings }

// ─── External links ──────────────────────────────────────────────────────────

export function openExternal(url: string): Promise<void> {
  return openUrl(url)
}

// ─── Permissions ─────────────────────────────────────────────────────────────

export function requestPermission(id: string): Promise<'granted' | 'denied'> {
  return invoke<'granted' | 'denied'>('request_permission', { id })
}

// ─── Overlay launch (called after onboarding) ────────────────────────────────

export function launchOverlay(): void {
  invoke('launch_overlay').catch(console.error)
}

// ─── Shortcut management ─────────────────────────────────────────────────────

export function testShortcut(key: string): Promise<boolean> {
  return invoke<boolean>('test_shortcut', { key })
}

export function registerShortcut(key: string): Promise<boolean> {
  return invoke<boolean>('register_shortcut', { key })
}

export function registerShortcutFromSettings(key: string): Promise<boolean> {
  return invoke<boolean>('register_shortcut_from_settings', { key })
}

export function getActiveShortcut(): Promise<string> {
  return invoke<string>('get_active_shortcut')
}

// ─── Settings ────────────────────────────────────────────────────────────────

export function getSettings(): Promise<AppSettings> {
  return invoke<AppSettings>('get_settings')
}

export function saveSettings(settings: AppSettings): Promise<void> {
  return invoke<void>('save_settings', { s: settings })
}

export function setWakeWordEnabled(enabled: boolean): Promise<void> {
  return invoke<void>('set_wake_word_enabled', { enabled })
}

/** Returns the actual OS state after the call. Throws a string error if the OS rejected it. */
export function setLaunchAtLogin(enabled: boolean): Promise<boolean> {
  return invoke<boolean>('set_launch_at_login', { enabled })
}

// ─── Token storage ───────────────────────────────────────────────────────────

export function storeTokens(access: string, refresh: string): Promise<void> {
  return invoke<void>('store_tokens', { access, refresh })
}

export function getAccessToken(): Promise<string | null> {
  return invoke<string | null>('get_access_token')
}

export function clearTokens(): Promise<void> {
  return invoke<void>('clear_tokens')
}

export function refreshTokens(): Promise<string | null> {
  return invoke<string | null>('refresh_tokens')
}

// ─── Screenshot ──────────────────────────────────────────────────────────────

export function takeScreenshot(): Promise<string> {
  return invoke<string>('take_screenshot')
}

// ─── Capture lifecycle ───────────────────────────────────────────────────────

export type CaptureDonePayload = { audioData: number[] } | null
export type CaptureDoneWithScreenshotPayload = {
  audioData: number[]
  screenshotDataUrl: string
}

export function captureDone(result: CaptureDonePayload): void {
  if (!result) {
    // Cancelled — just dismiss the overlay
    invoke('dismiss_overlay').catch(console.error)
    return
  }
  invoke('capture_done', { audioData: result.audioData }).catch(console.error)
}

export function captureDoneWithScreenshot(result: CaptureDoneWithScreenshotPayload): void {
  invoke('capture_done_with_screenshot', {
    audioData: result.audioData,
    screenshotDataUrl: result.screenshotDataUrl,
  }).catch(console.error)
}

// ─── Push events: capture start / end ────────────────────────────────────────

export type CaptureStartPayload = { data_url: string; shortcut_key: string }

export function onCaptureStart(
  cb: (screenshotDataUrl: string, shortcutKey: string) => void,
): () => void {
  let unlisten: UnlistenFn | null = null
  listen<CaptureStartPayload>('capture-start', (event) => {
    cb(event.payload.data_url, event.payload.shortcut_key)
  }).then((fn) => {
    unlisten = fn
  })
  return () => { unlisten?.() }
}

export function onCaptureEnd(cb: () => void): () => void {
  let unlisten: UnlistenFn | null = null
  listen<void>('capture-end', () => cb()).then((fn) => {
    unlisten = fn
  })
  return () => { unlisten?.() }
}

// ─── Push events: query result / error ───────────────────────────────────────

export type BeaconStep = {
  step: number
  instruction: string
  keys: string | null
  tip: string | null
}

export type QueryResponse = {
  transcript: string
  steps: BeaconStep[]
  summary: string
  speech_b64: string
  conversation_id: string
  benchmark?: Record<string, unknown>
}

export function onQueryResult(cb: (resp: QueryResponse) => void): () => void {
  let unlisten: UnlistenFn | null = null
  listen<QueryResponse>('query-result', (event) => cb(event.payload)).then((fn) => {
    unlisten = fn
  })
  return () => { unlisten?.() }
}

export function onQueryError(cb: (message: string) => void): () => void {
  let unlisten: UnlistenFn | null = null
  listen<string>('query-error', (event) => cb(event.payload)).then((fn) => {
    unlisten = fn
  })
  return () => { unlisten?.() }
}

export function onWakeWordError(cb: (message: string) => void): () => void {
  let unlisten: UnlistenFn | null = null
  listen<string>('wake-word-error', (event) => cb(event.payload)).then((fn) => {
    unlisten = fn
  })
  return () => { unlisten?.() }
}

// ─── Overlay controls ────────────────────────────────────────────────────────

export function setCursorPassthrough(passthrough: boolean): void {
  invoke('set_cursor_passthrough', { passthrough }).catch(console.error)
}

export function focusOverlay(): void {
  invoke('focus_overlay').catch(console.error)
}

export function dismissOverlay(): void {
  invoke('dismiss_overlay').catch(console.error)
}

// ─── Settings / main window controls ─────────────────────────────────────────

export function closeSettingsWindow(): void {
  invoke('close_settings_window').catch(console.error)
}

export function signOut(): void {
  invoke('sign_out').catch(console.error)
}

// ─── Compatibility shim: expose as window.api for renderer code ───────────────
// The copied renderer files call window.api.*  — this shim satisfies them
// without any changes to those files.

declare global {
  interface Window {
    api: typeof tauriApi
    // window.electron.ipcRenderer.send('close-main-window') is used in App.tsx
    electron: {
      ipcRenderer: {
        send: (channel: string) => void
      }
    }
  }
}

const tauriApi = {
  openExternal,
  requestPermission,
  launchOverlay,
  testShortcut,
  registerShortcut,
  registerShortcutFromSettings,
  getActiveShortcut,
  getSettings,
  saveSettings,
  setWakeWordEnabled,
  setLaunchAtLogin,
  storeTokens,
  getAccessToken,
  clearTokens,
  refreshTokens,
  takeScreenshot,
  captureDone,
  captureDoneWithScreenshot,
  onCaptureStart,
  onCaptureEnd,
  onQueryResult,
  onQueryError,
  onWakeWordError,
  setCursorPassthrough,
  focusOverlay,
  dismissOverlay,
  closeSettingsWindow,
  signOut,
}

// Install window.api shim so copied renderer code works without modification
if (typeof window !== 'undefined') {
  window.api = tauriApi

  // Shim for window.electron.ipcRenderer.send('close-main-window') in App.tsx
  // In Tauri we just hide the main window instead
  window.electron = {
    ipcRenderer: {
      send: (channel: string) => {
        if (channel === 'close-main-window') {
          // Hide the onboarding window — Rust will show it again on sign-out
          import('@tauri-apps/api/webviewWindow').then(async ({ WebviewWindow }) => {
            const win = await WebviewWindow.getByLabel('main')
            win?.hide().catch(console.error)
          })
        }
      },
    },
  }
}

export default tauriApi
