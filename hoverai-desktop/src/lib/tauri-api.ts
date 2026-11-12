/**
 * tauri-api.ts — the renderer's bridge to the Rust backend, exposed as window.api.
 *
 * Request-response channels → invoke()  from @tauri-apps/api/core
 * Push events from Rust     → listen()  from @tauri-apps/api/event
 */

import { invoke } from '@tauri-apps/api/core'
import { listen, type UnlistenFn } from '@tauri-apps/api/event'
import { getCurrentWebviewWindow } from '@tauri-apps/api/webviewWindow'
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

export type PlatformInfo = { os: string; wayland: boolean }

/** OS + whether this is a Linux Wayland session (shortcuts go via the desktop portal). */
export function platformInfo(): Promise<PlatformInfo> {
  return invoke<PlatformInfo>('platform_info')
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

/** Returns the actual OS state after the call. Throws a string error if the OS rejected it. */
export function setLaunchAtLogin(enabled: boolean): Promise<boolean> {
  return invoke<boolean>('set_launch_at_login', { enabled })
}

// ─── Token storage ───────────────────────────────────────────────────────────

export function storeTokens(access: string, refresh: string): Promise<void> {
  return invoke<void>('store_tokens', { access, refresh })
}

/** A usable access token — Rust refreshes it first if it is about to expire. */
export function getAccessToken(): Promise<string | null> {
  return invoke<string | null>('get_access_token')
}

export function clearTokens(): Promise<void> {
  return invoke<void>('clear_tokens')
}

/** Refresh after a 401. Pass the rejected token so concurrent callers share one refresh. */
export function refreshTokens(stale?: string | null): Promise<string | null> {
  return invoke<string | null>('refresh_tokens', { stale: stale ?? null })
}

export function hasSession(): Promise<boolean> {
  return invoke<boolean>('has_session')
}

// ─── Event subscription helper ───────────────────────────────────────────────
// listen() resolves asynchronously; if the caller unsubscribes before it does,
// the listener must still be removed once it arrives.

function subscribe<T>(event: string, cb: (payload: T) => void): () => void {
  let unlisten: UnlistenFn | null = null
  let cancelled = false
  listen<T>(event, (e) => cb(e.payload)).then((fn) => {
    if (cancelled) fn()
    else unlisten = fn
  })
  return () => {
    cancelled = true
    unlisten?.()
  }
}

// ─── Capture session (state lives in Rust) ───────────────────────────────────

/** Stop recording and send the question. */
export function captureSubmit(): void {
  invoke('capture_submit').catch(console.error)
}

/** Cancel recording / dismiss the panel. */
export function captureCancel(): void {
  invoke('capture_cancel').catch(console.error)
}

/** From the steps panel: take a fresh screenshot and record a new question. */
export function captureAgain(): void {
  invoke('capture_again').catch(console.error)
}

export type HitRect = { x: number; y: number; w: number; h: number }

/**
 * Report the overlay's interactive rectangles (CSS px). Everything outside
 * them is click-through, so the user can keep working in the app underneath.
 */
export function overlayHitRegions(rects: HitRect[]): void {
  invoke('overlay_hit_regions', { rects }).catch(console.error)
}

/** Kept for older call sites — same as captureCancel(). */
export function dismissOverlay(): void {
  captureCancel()
}

export function onCaptureStart(cb: () => void): () => void {
  return subscribe<void>('capture-start', () => cb())
}

export function onCaptureSubmitted(cb: () => void): () => void {
  return subscribe<void>('capture-submitted', () => cb())
}

/** Rust hid the overlay — reset the UI. Do NOT call captureCancel() from here. */
export function onCaptureEnd(cb: () => void): () => void {
  return subscribe<void>('capture-end', () => cb())
}

/** Microphone level 0–1, ~20 per second, while any recording/mic test runs. */
export function onAudioLevel(cb: (level: number) => void): () => void {
  return subscribe<number>('audio-level', cb)
}

// ─── Microphone test (onboarding) ────────────────────────────────────────────

export function micMonitorStart(): Promise<void> {
  return invoke<void>('mic_monitor_start')
}

export function micMonitorStop(): void {
  invoke('mic_monitor_stop').catch(console.error)
}

// ─── Push events: query result / error ───────────────────────────────────────

/** Where a step points: normalised 0–1 to the monitor the overlay covers. */
export type StepTarget = {
  element_id: number | null
  x: number
  y: number
  w: number
  h: number
}

export type BeaconStep = {
  step: number
  instruction: string
  keys: string | null
  tip: string | null
  target?: StepTarget | null
}

export type QueryResponse = {
  transcript: string
  steps: BeaconStep[]
  summary: string
  speech_b64: string
  conversation_id: string
}

export function onQueryResult(cb: (resp: QueryResponse) => void): () => void {
  return subscribe<QueryResponse>('query-result', cb)
}

export function onQueryError(cb: (message: string) => void): () => void {
  return subscribe<string>('query-error', cb)
}

/** Emitted to the main window when the session ends (sign-out or rejected token). */
export function onSignedOut(cb: () => void): () => void {
  return subscribe<void>('signed-out', () => cb())
}

// ─── Settings / main window controls ─────────────────────────────────────────

export function closeSettingsWindow(): void {
  invoke('close_settings_window').catch(console.error)
}

export function signOut(): void {
  invoke('sign_out').catch(console.error)
}

/** Fully quit HoverAI (stops shortcuts, mic, and the background process). */
export function quitApp(): void {
  invoke('quit_app').catch(console.error)
}

/** Hide the onboarding window — Rust shows it again on sign-out. */
export function closeMainWindow(): void {
  getCurrentWebviewWindow().hide().catch(console.error)
}

// ─── API transport ───────────────────────────────────────────────────────────

export interface ApiFetchResponse {
  status: number
  body: string
}

/** Send an API request from Rust (see `api_fetch` in lib.rs); `path` is relative to the API base. */
export function apiFetch(
  method: string,
  path: string,
  headers: Record<string, string>,
  body: string | null,
): Promise<ApiFetchResponse> {
  return invoke<ApiFetchResponse>('api_fetch', { method, path, headers, body })
}

// ─── Updates (tauri-plugin-updater, driven from Rust — see updater.rs) ───────

export interface UpdateInfo {
  version: string
  currentVersion: string
  notes: string | null
}

export function appVersion(): Promise<string> {
  return invoke<string>('app_version')
}

/** Null when up to date. Rejects with a readable message if the check failed. */
export function checkForUpdate(): Promise<UpdateInfo | null> {
  return invoke<UpdateInfo | null>('check_for_update')
}

/** Downloads, installs and restarts the app; only resolves/rejects on failure. */
export function installUpdate(): Promise<void> {
  return invoke<void>('install_update')
}

/** Rust found an update at startup and opened Settings. */
export function onUpdateAvailable(cb: (info: UpdateInfo) => void): () => void {
  return subscribe<UpdateInfo>('update-available', cb)
}

/** Download progress 0–1, or -1 when the size is unknown. */
export function onUpdateProgress(cb: (progress: number) => void): () => void {
  return subscribe<number>('update-progress', cb)
}

// ─── window.api ──────────────────────────────────────────────────────────────

declare global {
  interface Window {
    api: typeof tauriApi
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
  platformInfo,
  getSettings,
  saveSettings,
  setLaunchAtLogin,
  storeTokens,
  getAccessToken,
  clearTokens,
  refreshTokens,
  hasSession,
  captureSubmit,
  captureCancel,
  captureAgain,
  overlayHitRegions,
  dismissOverlay,
  onCaptureStart,
  onCaptureSubmitted,
  onCaptureEnd,
  onAudioLevel,
  micMonitorStart,
  micMonitorStop,
  onQueryResult,
  onQueryError,
  onSignedOut,
  closeSettingsWindow,
  signOut,
  quitApp,
  closeMainWindow,
  apiFetch,
  appVersion,
  checkForUpdate,
  installUpdate,
  onUpdateAvailable,
  onUpdateProgress,
}

if (typeof window !== 'undefined') {
  window.api = tauriApi
}

export default tauriApi
