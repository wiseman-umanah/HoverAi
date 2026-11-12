import { useState, useEffect, useCallback, useRef } from 'react'
import { motion } from 'framer-motion'
import type { AppSettings } from '@renderer/lib/settings-types'
import LangDropdown, { toDropdownLanguage, type Language } from '@renderer/components/LangDropdown'
import { api, ApiError } from '@renderer/lib/api'
import { displayShortcut, formatCombo, toAccelerator } from '@renderer/lib/shortcuts'
import type { UserResponse, ConversationHistory, ConversationDay, ConversationMessage } from '@renderer/lib/api'
import { setLaunchAtLogin, type UpdateInfo } from '@renderer/lib/tauri-api'

// ─── Main component ───────────────────────────────────────────────────────────

type Tab = 'settings' | 'history'

export default function SettingsPage() {
  const [activeTab, setActiveTab] = useState<Tab>('settings')
  const [settings, setSettings] = useState<AppSettings | null>(null)
  const [user, setUser] = useState<UserResponse | null>(null)
  const [languages, setLanguages] = useState<Language[]>([])
  const [langOpen, setLangOpen] = useState(false)
  const [shortcutMode, setShortcutMode] = useState(false)
  const [shortcutCombo, setShortcutCombo] = useState('')
  const [shortcutStatus, setShortcutStatus] = useState<
    'idle' | 'listening' | 'testing' | 'available' | 'taken'
  >('idle')
  const testingRef = useRef(false)
  const [autostartError, setAutostartError] = useState<string | null>(null)

  // Fetch available languages on mount — no auth required
  useEffect(() => {
    api.getLanguages().then((list) => {
      setLanguages(list.map(toDropdownLanguage))
    }).catch(() => {/* leave empty on error */})
  }, [])

  // Load settings every time the window gains focus so data is always fresh
  // when the user opens settings from the tray (the window is pre-created at
  // startup and reused — its mount-time useEffect would only fire once).
  const loadSettings = useCallback(() => {
    // Phase 1 — local IPC (no network) → renders the page immediately
    Promise.all([
      window.api.getSettings(),
      window.api.getActiveShortcut(),
    ]).then(([localSettings, shortcut]) => {
      setSettings(localSettings)
      const resolvedShortcut = shortcut || localSettings.shortcutKey
      if (resolvedShortcut) setShortcutCombo(displayShortcut(resolvedShortcut))

      // Phase 2 — server data in the background, silently merged in
      Promise.all([
        api.getMe().catch(() => null),
        api.getServerSettings().catch(() => null),
      ]).then(([serverUser, serverSettings]) => {
        if (serverUser) setUser(serverUser)
        setSettings((prev) => {
          if (!prev) return prev
          return {
            ...prev,
            ...(serverUser ? { language: serverUser.language } : {}),
            ...(serverSettings
              ? {
                  overlayOpacity: serverSettings.overlay_opacity,
                  overlaySize: serverSettings.overlay_size,
                  micSensitivity: serverSettings.mic_sensitivity,
                  soundEffects: serverSettings.sound_effects,
                  notifications: serverSettings.notifications,
                  launchAtLogin: serverSettings.launch_at_login,
                  showInTaskbar: serverSettings.show_in_taskbar,
                  voiceGender: serverSettings.voice_gender,
                }
              : {}),
          }
        })
      })
    }).catch((err) => {
      console.error('[settings] failed to load local settings:', err)
      setSettings({
        launchAtLogin: false, showInTaskbar: false, overlayOpacity: 1,
        overlaySize: 'default', micSensitivity: 5, soundEffects: true,
        notifications: true, language: 'en',
        voiceGender: 'female', shortcutKey: '',
      })
    })
  }, [])

  // Run on first mount AND every time the window is focused
  useEffect(() => {
    loadSettings()
    window.addEventListener('focus', loadSettings)
    return () => window.removeEventListener('focus', loadSettings)
  }, [loadSettings])

  // Keyboard listener for shortcut recording
  useEffect(() => {
    if (!shortcutMode) return
    const onKeyDown = async (e: KeyboardEvent) => {
      e.preventDefault()
      const combo = formatCombo(e)
      if (!combo) return
      setShortcutCombo(combo)
      setShortcutMode(false)
      if (testingRef.current) return
      testingRef.current = true
      setShortcutStatus('testing')
      const acc = toAccelerator(combo)
      const available = await window.api.testShortcut(acc).catch(() => false)
      setShortcutStatus(available ? 'available' : 'taken')
      testingRef.current = false
      if (available) {
        const ok = await window.api.registerShortcutFromSettings(acc).catch(() => false)
        setShortcutStatus(ok ? 'idle' : 'taken')
        // Show what Rust actually has registered — the old shortcut stays
        // active if the new one failed.
        const active = await window.api.getActiveShortcut().catch(() => '')
        if (active) setShortcutCombo(displayShortcut(active))
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [shortcutMode])

  const update = useCallback(
    <K extends keyof AppSettings>(key: K, value: AppSettings[K]) => {
      if (!settings) return
      const next = { ...settings, [key]: value }
      setSettings(next)
      // Persist locally
      window.api.saveSettings(next)
      // Map camelCase key to snake_case server field and sync (fire and forget)
      // language lives on User model (PATCH /users/me), everything else on UserSettings
      if (key === 'language') {
        api.patchMe({ language: value as string }).catch((err) => {
          if (!(err instanceof ApiError)) console.error('[settings] language sync failed:', err)
        })
        return
      }
      // launchAtLogin requires an OS-level call — delegate to the Rust command.
      // The command returns the actual OS state. If it fails, snap the toggle back.
      if (key === 'launchAtLogin') {
        setLaunchAtLogin(value as boolean)
          .then((actual) => {
            // Snap to what the OS confirmed (may differ from what was requested)
            setSettings((prev) => prev ? { ...prev, launchAtLogin: actual } : prev)
            setAutostartError(null)
          })
          .catch((err: unknown) => {
            const msg = typeof err === 'string' ? err : 'Could not update startup setting'
            setAutostartError(msg)
            // Revert the toggle — OS rejected the change
            setSettings((prev) => prev ? { ...prev, launchAtLogin: !value } : prev)
            setTimeout(() => setAutostartError(null), 6000)
          })
        return
      }
      const keyToServerField: Partial<Record<keyof AppSettings, string>> = {
        overlayOpacity: 'overlay_opacity',
        overlaySize: 'overlay_size',
        micSensitivity: 'mic_sensitivity',
        soundEffects: 'sound_effects',
        notifications: 'notifications',
        launchAtLogin: 'launch_at_login',
        showInTaskbar: 'show_in_taskbar',
        voiceGender: 'voice_gender',
      }
      const serverField = keyToServerField[key]
      if (serverField) {
        api.patchServerSettings({ [serverField]: value }).catch((err) => {
          if (!(err instanceof ApiError)) console.error('[settings] server sync failed:', err)
        })
      }
    },
    [settings]
  )

  if (!settings) {
    return (
      <div
        style={{
          width: '100%',
          height: '100%',
          background: '#1c1c1e',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
        }}
      >
        <i className="ri-loader-4-line" style={{ fontSize: 24, color: 'var(--text-muted)', animation: 'spin 0.8s linear infinite' }} />
        <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
      </div>
    )
  }


  return (
    <div
      style={{
        width: '100%',
        height: '100%',
        background: '#1c1c1e',
        display: 'flex',
        flexDirection: 'column',
        overflow: 'hidden',
      }}
    >
      {/* ── Title bar (drag region + close) ───────────────────────────── */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          padding: '24px 24px 0',
          flexShrink: 0,
          WebkitAppRegion: 'drag',
        } as React.CSSProperties}
      >
        <h1
          style={{
            fontSize: 28,
            fontWeight: 700,
            letterSpacing: '-0.02em',
            color: 'var(--text-primary)',
          }}
        >
          Settings
        </h1>
        <button
          onClick={() => window.api.closeSettingsWindow()}
          style={{
            width: 32,
            height: 32,
            borderRadius: '50%',
            border: 'none',
            background: 'rgba(255,255,255,0.08)',
            color: 'var(--text-secondary)',
            cursor: 'pointer',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            fontSize: 18,
            WebkitAppRegion: 'no-drag',
          } as React.CSSProperties}
        >
          ✕
        </button>
      </div>

      {/* ── Tab bar ───────────────────────────────────────────────────── */}
      <div
        style={{
          display: 'flex',
          gap: 4,
          padding: '16px 16px 0',
          flexShrink: 0,
          WebkitAppRegion: 'no-drag',
        } as React.CSSProperties}
      >
        {(['settings', 'history'] as Tab[]).map((tab) => (
          <button
            key={tab}
            onClick={() => setActiveTab(tab)}
            style={{
              padding: '8px 18px',
              borderRadius: 'var(--radius-full)',
              border: 'none',
              background: activeTab === tab ? 'var(--accent)' : 'rgba(255,255,255,0.06)',
              color: activeTab === tab ? '#fff' : 'var(--text-secondary)',
              fontSize: 14,
              fontWeight: 600,
              cursor: 'pointer',
              transition: 'background 0.15s, color 0.15s',
              textTransform: 'capitalize',
            }}
          >
            {tab}
          </button>
        ))}
      </div>

      {/* ── Settings tab ──────────────────────────────────────────────── */}
      {activeTab === 'settings' && (
      <div
        style={{
          flex: 1,
          overflowY: 'auto',
          overflowX: 'hidden',
          padding: '0 16px 24px',
          display: 'flex',
          flexDirection: 'column',
          gap: 8,
        }}
      >
        {/* ── Language preference ─────────────────────────────────────── */}
        <SectionLabel>Language preference</SectionLabel>
        <div>
          <LangDropdown
            value={settings.language}
            open={langOpen}
            onToggle={() => setLangOpen((v) => !v)}
            onSelect={(l) => {
              update('language', l.value)
              setLangOpen(false)
            }}
            languages={languages}
            variant="solid"
          />
        </div>

        {/* ── Capture shortcut ────────────────────────────────────────── */}
        <Card>
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              gap: 12,
            }}
          >
            {/* Key chips */}
            <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
              {shortcutMode ? (
                <span style={{ fontSize: 14, color: 'var(--accent)', fontWeight: 600 }}>
                  Press a key combo…
                </span>
              ) : shortcutCombo ? (
                shortcutCombo.split('+').map((part, i, arr) => (
                  <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                    <KeyChip>{part}</KeyChip>
                    {i < arr.length - 1 && (
                      <span style={{ color: 'var(--text-secondary)', fontSize: 14, fontWeight: 500 }}>
                        +
                      </span>
                    )}
                  </div>
                ))
              ) : (
                <span style={{ fontSize: 14, color: 'var(--text-muted)' }}>No shortcut set</span>
              )}
              {shortcutStatus === 'testing' && (
                <i
                  className="ri-loader-4-line"
                  style={{
                    fontSize: 14,
                    color: 'var(--text-muted)',
                    animation: 'spin 0.8s linear infinite',
                  }}
                />
              )}
              {shortcutStatus === 'taken' && (
                <span style={{ fontSize: 12, color: '#ef4444' }}>Already taken</span>
              )}
            </div>

            {/* Change button */}
            <button
              onClick={() => {
                setShortcutMode(true)
                setShortcutStatus('listening')
              }}
              style={{
                padding: '10px 22px',
                borderRadius: 'var(--radius-full)',
                border: '1px solid rgba(255,255,255,0.12)',
                background: shortcutMode ? 'var(--accent)' : 'rgba(255,255,255,0.06)',
                color: shortcutMode ? '#fff' : 'var(--text-primary)',
                fontSize: 15,
                fontWeight: 600,
                cursor: 'pointer',
                whiteSpace: 'nowrap',
                flexShrink: 0,
                transition: 'background 0.15s, border-color 0.15s',
              }}
            >
              {shortcutMode ? 'Listening…' : 'Change'}
            </button>
          </div>
          <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
        </Card>

        {/* ── Voice gender ─────────────────────────────────────────────── */}
        <Card>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
            <div>
              <p style={{ fontSize: 16, fontWeight: 700, color: 'var(--text-primary)' }}>
                AI voice
              </p>
              <p style={{ fontSize: 14, color: 'var(--text-secondary)', marginTop: 3 }}>
                Voice used for spoken responses
              </p>
            </div>
            <div style={{ display: 'flex', gap: 6 }}>
              {(['female', 'male'] as const).map((gender) => (
                <button
                  key={gender}
                  onClick={() => update('voiceGender', gender)}
                  style={{
                    padding: '8px 16px',
                    borderRadius: 'var(--radius-full)',
                    border: settings.voiceGender === gender
                      ? '1px solid var(--accent)'
                      : '1px solid rgba(255,255,255,0.12)',
                    background: settings.voiceGender === gender
                      ? 'rgba(108,99,255,0.18)'
                      : 'rgba(255,255,255,0.04)',
                    color: settings.voiceGender === gender
                      ? 'var(--text-primary)'
                      : 'var(--text-secondary)',
                    fontSize: 14,
                    fontWeight: 600,
                    cursor: 'pointer',
                    transition: 'all 0.15s',
                    textTransform: 'capitalize',
                  }}
                >
                  {gender}
                </button>
              ))}
            </div>
          </div>
        </Card>

        {/* ── System ──────────────────────────────────────────────────── */}
        <SectionLabel icon="ri-settings-3-line">System</SectionLabel>
        <Card>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
            <div>
              <p style={{ fontSize: 16, fontWeight: 700, color: 'var(--text-primary)' }}>
                Launch at startup
              </p>
              <p style={{ fontSize: 14, color: 'var(--text-secondary)', marginTop: 3 }}>
                Start HoverAI automatically when you log in
              </p>
            </div>
            <Toggle
              checked={settings.launchAtLogin}
              onChange={(v) => update('launchAtLogin', v)}
            />
          </div>
          {autostartError && (
            <p style={{ marginTop: 8, fontSize: 13, color: '#f87171' }}>
              ⚠ {autostartError}
            </p>
          )}
        </Card>

        <UpdatesCard />

        {/* ── Account ─────────────────────────────────────────────────── */}
        <SectionLabel icon="ri-user-3-line">Account</SectionLabel>
        <Card>
          <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
            {/* Avatar — initials from name */}
            <div
              style={{
                width: 44,
                height: 44,
                borderRadius: '50%',
                background: 'linear-gradient(135deg, #6c63ff 0%, #8b5cf6 100%)',
                flexShrink: 0,
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                fontSize: 16,
                fontWeight: 700,
                color: '#fff',
              }}
            >
              {user?.full_name
                ? user.full_name.split(' ').map((n) => n[0]).join('').slice(0, 2).toUpperCase()
                : '?'}
            </div>

            {/* Info */}
            <div style={{ flex: 1, minWidth: 0 }}>
              <p
                style={{
                  fontSize: 16,
                  fontWeight: 700,
                  color: 'var(--text-primary)',
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                  whiteSpace: 'nowrap',
                }}
              >
                {user?.full_name ?? user?.email ?? '—'}
              </p>
              <p style={{ fontSize: 13, color: 'var(--text-secondary)', marginTop: 2 }}>
                {user?.email ?? 'Loading…'}
              </p>
            </div>

            {/* Sign out */}
            <button
              onClick={() => window.api.signOut()}
              style={{
                padding: '9px 18px',
                borderRadius: 'var(--radius-full)',
                border: '1px solid rgba(255,255,255,0.12)',
                background: 'rgba(255,255,255,0.05)',
                color: 'var(--text-primary)',
                fontSize: 14,
                fontWeight: 600,
                cursor: 'pointer',
                flexShrink: 0,
                transition: 'background 0.15s',
              }}
              onMouseEnter={(e) => {
                e.currentTarget.style.background = 'rgba(255,255,255,0.1)'
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.background = 'rgba(255,255,255,0.05)'
              }}
            >
              Sign Out
            </button>
          </div>
        </Card>

        {/* ── Quit ────────────────────────────────────────────────────── */}
        <Card>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 16 }}>
            <div>
              <p style={{ fontSize: 16, fontWeight: 700, color: 'var(--text-primary)' }}>
                Quit HoverAI
              </p>
              <p style={{ fontSize: 14, color: 'var(--text-secondary)', marginTop: 3 }}>
                Closing this window keeps HoverAI running in the tray. Quit stops it completely.
              </p>
            </div>
            <button
              onClick={() => window.api.quitApp()}
              style={{
                padding: '9px 18px',
                borderRadius: 'var(--radius-full)',
                border: '1px solid rgba(239,68,68,0.35)',
                background: 'rgba(239,68,68,0.08)',
                color: '#f87171',
                fontSize: 14,
                fontWeight: 600,
                cursor: 'pointer',
                flexShrink: 0,
              }}
            >
              Quit
            </button>
          </div>
        </Card>
      </div>
      )}

      {/* ── History tab ───────────────────────────────────────────────── */}
      {activeTab === 'history' && <HistoryPanel />}
    </div>
  )
}

// ─── Sub-components ───────────────────────────────────────────────────────────

function Card({ children }: { children: React.ReactNode }) {
  return (
    <div
      style={{
        background: '#2c2c2e',
        borderRadius: 'var(--radius-lg)',
        padding: '16px 18px',
      }}
    >
      {children}
    </div>
  )
}

function SectionLabel({
  children,
  icon,
}: {
  children: React.ReactNode
  icon?: string
}) {
  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: 7,
        padding: '10px 4px 4px',
        fontSize: 14,
        fontWeight: 500,
        color: 'var(--text-secondary)',
        letterSpacing: '0.01em',
      }}
    >
      {icon && <i className={icon} style={{ fontSize: 16 }} />}
      {children}
    </div>
  )
}

function KeyChip({ children }: { children: React.ReactNode }) {
  return (
    <span
      style={{
        padding: '6px 14px',
        borderRadius: 10,
        background: 'rgba(255,255,255,0.08)',
        border: '1px solid rgba(255,255,255,0.12)',
        fontSize: 16,
        fontWeight: 700,
        color: 'var(--text-primary)',
        display: 'inline-block',
      }}
    >
      {children}
    </span>
  )
}

function Toggle({ checked, onChange }: { checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <button
      role="switch"
      aria-checked={checked}
      onClick={() => onChange(!checked)}
      style={{
        width: 52,
        height: 30,
        borderRadius: 'var(--radius-full)',
        border: 'none',
        cursor: 'pointer',
        padding: 3,
        background: checked ? 'var(--accent)' : '#3a3a3c',
        transition: 'background 0.25s',
        display: 'flex',
        alignItems: 'center',
        justifyContent: checked ? 'flex-end' : 'flex-start',
        flexShrink: 0,
      }}
    >
      <motion.span
        layout
        transition={{ type: 'spring', stiffness: 500, damping: 30 }}
        style={{
          width: 24,
          height: 24,
          borderRadius: '50%',
          background: '#fff',
          display: 'block',
          boxShadow: '0 1px 4px rgba(0,0,0,0.3)',
          flexShrink: 0,
        }}
      />
    </button>
  )
}

// ─── Updates card ─────────────────────────────────────────────────────────────

type UpdateState =
  | { kind: 'idle' | 'checking' | 'current' }
  | { kind: 'available'; info: UpdateInfo }
  | { kind: 'installing'; info: UpdateInfo; progress: number }
  | { kind: 'error'; message: string }

function UpdatesCard() {
  const [version, setVersion] = useState('')
  const [state, setState] = useState<UpdateState>({ kind: 'idle' })

  const check = useCallback(() => {
    setState({ kind: 'checking' })
    window.api.checkForUpdate()
      .then((info) => setState(info ? { kind: 'available', info } : { kind: 'current' }))
      .catch((e) => setState({ kind: 'error', message: `Couldn't check for updates: ${String(e)}` }))
  }, [])

  useEffect(() => {
    window.api.appVersion().then(setVersion).catch(() => {})
    const offAvailable = window.api.onUpdateAvailable((info) => setState({ kind: 'available', info }))
    const offProgress = window.api.onUpdateProgress((progress) =>
      setState((s) => (s.kind === 'installing' ? { ...s, progress } : s)))
    return () => { offAvailable(); offProgress() }
  }, [])

  function install(info: UpdateInfo) {
    setState({ kind: 'installing', info, progress: 0 })
    // Resolves only if something went wrong — on success the app restarts.
    window.api.installUpdate().catch((e) => setState({ kind: 'error', message: String(e) }))
  }

  const status =
    state.kind === 'checking' ? 'Checking…'
    : state.kind === 'current' ? 'You have the latest version.'
    : state.kind === 'available' ? `Version ${state.info.version} is available.`
    : state.kind === 'installing'
      ? state.progress < 0 ? 'Downloading update…' : `Downloading update… ${Math.round(state.progress * 100)}%`
    : state.kind === 'error' ? state.message
    : 'Updates are installed from HoverAI releases.'

  const buttonStyle: React.CSSProperties = {
    padding: '9px 18px',
    borderRadius: 'var(--radius-full)',
    border: '1px solid rgba(255,255,255,0.12)',
    background: 'rgba(255,255,255,0.05)',
    color: 'var(--text-primary)',
    fontSize: 14,
    fontWeight: 600,
    cursor: 'pointer',
    flexShrink: 0,
  }

  return (
    <Card>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 16 }}>
        <div style={{ minWidth: 0 }}>
          <p style={{ fontSize: 16, fontWeight: 700, color: 'var(--text-primary)' }}>
            Updates{version && <span style={{ fontWeight: 500, color: 'var(--text-secondary)' }}> · v{version}</span>}
          </p>
          <p style={{ fontSize: 14, color: state.kind === 'error' ? '#f87171' : 'var(--text-secondary)', marginTop: 3 }}>
            {status}
          </p>
        </div>
        {state.kind === 'available' ? (
          <button
            onClick={() => install(state.info)}
            style={{ ...buttonStyle, background: 'var(--accent)', border: '1px solid transparent', color: '#fff' }}
          >
            Install & restart
          </button>
        ) : (
          <button
            onClick={check}
            disabled={state.kind === 'checking' || state.kind === 'installing'}
            style={{ ...buttonStyle, opacity: state.kind === 'checking' || state.kind === 'installing' ? 0.5 : 1 }}
          >
            Check for updates
          </button>
        )}
      </div>
      {state.kind === 'installing' && state.progress >= 0 && (
        <div style={{ marginTop: 12, height: 4, borderRadius: 2, background: 'rgba(255,255,255,0.08)', overflow: 'hidden' }}>
          <div style={{ width: `${state.progress * 100}%`, height: '100%', background: 'var(--accent)', transition: 'width 0.2s' }} />
        </div>
      )}
    </Card>
  )
}

// ─── History panel ────────────────────────────────────────────────────────────

function HistoryPanel() {
  const [history, setHistory] = useState<ConversationHistory | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    setLoading(true)
    setError(null)
    api.getHistory()
      .then((h) => { setHistory(h); setLoading(false) })
      .catch((err: unknown) => {
        setError(err instanceof Error ? err.message : 'Failed to load history')
        setLoading(false)
      })
  }, [])

  if (loading) {
    return (
      <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        <i className="ri-loader-4-line" style={{ fontSize: 24, color: 'var(--text-muted)', animation: 'spin 0.8s linear infinite' }} />
      </div>
    )
  }

  if (error) {
    return (
      <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 24 }}>
        <p style={{ fontSize: 14, color: '#ef4444', textAlign: 'center' }}>{error}</p>
      </div>
    )
  }

  if (!history || history.days.length === 0) {
    return (
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', padding: 32, gap: 10 }}>
        <i className="ri-chat-history-line" style={{ fontSize: 36, color: 'var(--text-muted)' }} />
        <p style={{ fontSize: 14, color: 'var(--text-secondary)', textAlign: 'center', lineHeight: 1.6, maxWidth: 260 }}>
          No conversation history yet. Start using Hover to see your sessions here.
        </p>
      </div>
    )
  }

  return (
    <div style={{ flex: 1, overflowY: 'auto', overflowX: 'hidden', padding: '8px 16px 24px', display: 'flex', flexDirection: 'column', gap: 4 }}>
      {history.days.map((day) => <DayGroup key={day.date} day={day} />)}
    </div>
  )
}

function DayGroup({ day }: { day: ConversationDay }) {
  const label = formatDayLabel(day.date)
  return (
    <div>
      <SectionLabel>{label}</SectionLabel>
      <Card>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          {day.messages.map((msg) => <MessageRow key={msg.id} message={msg} />)}
        </div>
      </Card>
    </div>
  )
}

function MessageRow({ message }: { message: ConversationMessage }) {
  const isUser = message.role === 'user'
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
      <span style={{
        fontSize: 11,
        fontWeight: 700,
        letterSpacing: '0.06em',
        textTransform: 'uppercase',
        color: isUser ? 'var(--text-muted)' : 'var(--accent)',
      }}>
        {isUser ? 'You' : 'Hover'}
      </span>
      <p style={{
        margin: 0,
        fontSize: isUser ? 13 : 14,
        color: isUser ? 'var(--text-secondary)' : 'var(--text-primary)',
        lineHeight: 1.55,
        fontWeight: isUser ? 400 : 500,
      }}>
        {message.content}
      </p>
    </div>
  )
}

function formatDayLabel(dateStr: string): string {
  // dateStr is "2025-01-15" UTC
  const [year, month, day] = dateStr.split('-').map(Number)
  const date = new Date(Date.UTC(year, month - 1, day))
  const todayUtc = new Date()
  const todayStr = `${todayUtc.getUTCFullYear()}-${String(todayUtc.getUTCMonth() + 1).padStart(2, '0')}-${String(todayUtc.getUTCDate()).padStart(2, '0')}`
  const yesterdayUtc = new Date(Date.UTC(todayUtc.getUTCFullYear(), todayUtc.getUTCMonth(), todayUtc.getUTCDate() - 1))
  const yesterdayStr = `${yesterdayUtc.getUTCFullYear()}-${String(yesterdayUtc.getUTCMonth() + 1).padStart(2, '0')}-${String(yesterdayUtc.getUTCDate()).padStart(2, '0')}`
  if (dateStr === todayStr) return 'Today'
  if (dateStr === yesterdayStr) return 'Yesterday'
  return date.toLocaleDateString(undefined, { month: 'long', day: 'numeric', year: 'numeric' })
}
