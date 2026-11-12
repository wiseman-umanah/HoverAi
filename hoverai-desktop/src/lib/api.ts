/**
 * Typed API client for the HoverAI backend.
 *
 * - All calls include `Authorization: Bearer <access_token>` from the OS keychain via Tauri IPC.
 * - On 401, the client automatically calls POST /auth/refresh, stores the new
 *   token pair, and retries the original request once.
 * - If the refresh also fails, `AuthExpiredError` is thrown — the caller should
 *   redirect to the auth page.
 */

// Renderer process: Vite replaces import.meta.env.VITE_API_BASE at build time.
// Falls back to localhost for safety — override via hoverai-desktop/.env
export const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8000/api/v1'


// ─── Error types ──────────────────────────────────────────────────────────────

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly detail: string,
  ) {
    super(detail)
    this.name = 'ApiError'
  }
}

export class AuthExpiredError extends Error {
  constructor() {
    super('Session expired — please sign in again.')
    this.name = 'AuthExpiredError'
  }
}

// ─── Response types ───────────────────────────────────────────────────────────

export type TokenResponse = {
  access_token: string
  refresh_token: string
  token_type: string
}

export type UserResponse = {
  id: string
  email: string
  full_name: string | null
  language: string
}

export type UserSettingsResponse = {
  overlay_opacity: number
  overlay_size: 'compact' | 'default' | 'large'
  mic_sensitivity: number
  sound_effects: boolean
  notifications: boolean
  launch_at_login: boolean
  show_in_taskbar: boolean
  voice_gender: 'female' | 'male'
}

export type UserSettingsUpdate = Partial<UserSettingsResponse>

export type ConversationMessage = {
  id: string
  role: 'user' | 'assistant'
  content: string
  created_at: string
}

export type ConversationDay = {
  date: string          // "2025-01-15"
  messages: ConversationMessage[]
}

export type ConversationHistory = {
  conversation_id: string
  days: ConversationDay[]
}

export type Language = {
  code: string
  label: string
  default: boolean
}

export type SignupPayload = {
  email: string
  full_name: string
  password: string
  language?: string
  device_id?: string
}

export type SigninPayload = {
  email: string
  password: string
  device_id?: string
}

// ─── Core fetch wrapper ───────────────────────────────────────────────────────

async function request<T>(
  path: string,
  options: RequestInit & { skipAuth?: boolean } = {},
): Promise<T> {
  const { skipAuth = false, ...fetchOptions } = options

  const headers: Record<string, string> = {
    ...(fetchOptions.headers as Record<string, string>),
  }
  // ngrok's free tier answers browser-like requests (the webview's fetch) with
  // an HTML warning page unless this header is present.
  if (API_BASE.includes('.ngrok')) headers['ngrok-skip-browser-warning'] = '1'

  let token: string | null = null
  if (!skipAuth) {
    token = await window.api.getAccessToken()
    if (token) headers['Authorization'] = `Bearer ${token}`
  }

  const resp = await fetch(`${API_BASE}${path}`, { ...fetchOptions, headers })

  // ── 401 → refresh → retry once ───────────────────────────────────────────
  if (resp.status === 401 && !skipAuth) {
    const refreshed = await tryRefresh(token)
    if (!refreshed) throw new AuthExpiredError()

    const retryHeaders = { ...headers, Authorization: `Bearer ${refreshed}` }
    const retry = await fetch(`${API_BASE}${path}`, { ...fetchOptions, headers: retryHeaders })

    if (retry.status === 401) {
      await window.api.clearTokens()
      throw new AuthExpiredError()
    }

    return parseResponse<T>(retry)
  }

  return parseResponse<T>(resp)
}

async function parseResponse<T>(resp: Response): Promise<T> {
  if (resp.status === 204) return undefined as T

  const text = await resp.text()
  let body: unknown
  try {
    body = JSON.parse(text)
  } catch {
    body = text
  }

  if (!resp.ok) {
    const detail =
      typeof body === 'object' && body !== null && 'detail' in body
        ? String((body as { detail: unknown }).detail)
        : `HTTP ${resp.status}`
    throw new ApiError(resp.status, detail)
  }

  return body as T
}

async function tryRefresh(rejected: string | null): Promise<string | null> {
  // Delegates to Rust via the refresh_tokens command. Rust holds the refresh
  // token in the OS keychain and serialises refreshes, so passing the token
  // that was rejected lets it return an already-refreshed one instead of
  // rotating twice.
  try {
    return await window.api.refreshTokens(rejected)
  } catch {
    return null
  }
}

// ─── Auth endpoints ───────────────────────────────────────────────────────────

async function signup(body: SignupPayload): Promise<TokenResponse> {
  return request<TokenResponse>('/auth/signup', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    skipAuth: true,
  })
}

async function signin(body: SigninPayload): Promise<TokenResponse> {
  return request<TokenResponse>('/auth/signin', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    skipAuth: true,
  })
}

// ─── User endpoints ───────────────────────────────────────────────────────────

async function getMe(): Promise<UserResponse> {
  return request<UserResponse>('/users/me')
}

async function patchMe(patch: { language?: string }): Promise<UserResponse> {
  return request<UserResponse>('/users/me', {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(patch),
  })
}

async function getServerSettings(): Promise<UserSettingsResponse> {
  return request<UserSettingsResponse>('/users/me/settings')
}

async function patchServerSettings(patch: UserSettingsUpdate): Promise<UserSettingsResponse> {
  return request<UserSettingsResponse>('/users/me/settings', {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(patch),
  })
}

// ─── Conversation history ──────────────────────────────────────────────────────

async function getHistory(): Promise<ConversationHistory> {
  return request<ConversationHistory>('/conversations/history')
}

// ─── Languages ────────────────────────────────────────────────────────────────

async function getLanguages(): Promise<Language[]> {
  return request<Language[]>('/languages', { skipAuth: true })
}

// ─── Exported API object ──────────────────────────────────────────────────────

export const api = {
  signup,
  signin,
  getMe,
  patchMe,
  getServerSettings,
  patchServerSettings,
  getHistory,
  getLanguages,
}
