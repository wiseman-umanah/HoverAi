import { useState, useEffect, useCallback, useRef } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { displayShortcut, formatCombo, toAccelerator } from '@renderer/lib/shortcuts'

interface ShortcutPageProps {
  onComplete: () => void
}

type Status = 'waiting' | 'testing' | 'available' | 'taken' | 'confirmed'

// Suggestions to probe on mount — ordered from "most likely free" to "less likely"
const SUGGESTIONS = [
  'CommandOrControl+F9',
  'CommandOrControl+F10',
  'CommandOrControl+F11',
  'CommandOrControl+Alt+S',
  'CommandOrControl+Alt+C',
  'CommandOrControl+Alt+H',
  'CommandOrControl+Alt+G',
]

// Human-readable label for a suggestion
function suggestLabel(acc: string): string {
  return displayShortcut(acc).replace(/\+/g, ' + ')
}

export default function ShortcutPage({ onComplete }: ShortcutPageProps) {
  const [combo, setCombo] = useState('')
  const [status, setStatus] = useState<Status>('waiting')
  const [suggestions, setSuggestions] = useState<Record<string, boolean>>({})
  const [wayland, setWayland] = useState(false)

  useEffect(() => {
    window.api.platformInfo().then((p) => setWayland(p.wayland)).catch(() => {})
  }, [])
  const ease = [0.22, 1, 0.36, 1] as const
  const testingRef = useRef(false)
  // Keep a stable ref to handleConfirm so the keydown listener can call it
  const confirmRef = useRef<() => void>(() => {})

  useEffect(() => {
    let cancelled = false
    async function probe() {
      for (const key of SUGGESTIONS) {
        if (cancelled) break
        const ok = await window.api.testShortcut(key).catch(() => false)
        setSuggestions((prev) => ({ ...prev, [key]: ok }))
      }
    }
    probe()
    return () => { cancelled = true }
  }, [])

  const testCombo = useCallback(async (displayCombo: string) => {
    if (!displayCombo || testingRef.current) return
    testingRef.current = true
    setStatus('testing')
    const acc = toAccelerator(displayCombo)
    const available = await window.api.testShortcut(acc).catch(() => false)
    setStatus(available ? 'available' : 'taken')
    testingRef.current = false
  }, [])

  // Global keydown: Enter confirms when available, all other combos are recorded
  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Enter') {
        e.preventDefault()
        confirmRef.current()
        return
      }
      e.preventDefault()
      const formatted = formatCombo(e)
      if (!formatted) return
      setCombo(formatted)
      testCombo(formatted)
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [testCombo])

  async function handleConfirm() {
    if (status !== 'available' || !combo) return
    setStatus('testing')
    const ok = await window.api.registerShortcut(toAccelerator(combo)).catch(() => false)
    if (ok) {
      setStatus('confirmed')
      setTimeout(onComplete, 650)
    } else {
      setStatus('taken')
    }
  }

  // Keep ref in sync so the keydown listener always calls the latest version
  confirmRef.current = handleConfirm

  async function pickSuggestion(acc: string) {
    const display = displayShortcut(acc)
    setCombo(display)
    setStatus('testing')
    const ok = await window.api.testShortcut(acc).catch(() => false)
    setStatus(ok ? 'available' : 'taken')
  }

  const statusColor =
    status === 'available' || status === 'confirmed' ? '#22c55e'
    : status === 'taken' ? '#ef4444'
    : 'var(--text-secondary)'

  const statusText =
    status === 'waiting' ? 'Press any key combination, or pick a suggestion below'
    : status === 'testing' ? 'Checking…'
    : status === 'available' ? '✓ Available — press Enter or click Confirm'
    : status === 'taken' ? '✗ Already grabbed by your system — try another'
    : '✓ Registered!'

  const freeSuggestions = SUGGESTIONS.filter((s) => suggestions[s] === true)
  const takenSuggestions = SUGGESTIONS.filter((s) => suggestions[s] === false)
  const pendingSuggestions = SUGGESTIONS.filter((s) => suggestions[s] === undefined)

  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1, transition: { duration: 0.35, ease } }}
      style={{
        display: 'flex',
        flexDirection: 'column',
        height: '100%',
        width: '100%',
        padding: '48px 48px 40px',
        background: 'var(--bg)',
        gap: 32,
        overflowY: 'auto',
        scrollbarWidth: 'none',
        msOverflowStyle: 'none',
      } as React.CSSProperties}
    >
      <style>{`#shortcut-scroll::-webkit-scrollbar { display: none; }`}</style>
      {/* Header */}
      <motion.div
        initial={{ opacity: 0, y: 16 }}
        animate={{ opacity: 1, y: 0, transition: { duration: 0.4, delay: 0.05, ease } }}
      >
        <h1 style={{ fontSize: 34, fontWeight: 700, letterSpacing: '-0.025em', color: 'var(--text-primary)', marginBottom: 10 }}>
          Set your capture shortcut
        </h1>
        <p style={{ fontSize: 15, color: 'var(--text-secondary)', lineHeight: 1.6, maxWidth: 500 }}>
          This is how you call Hover from any app. Press any key combination,
          or pick a free suggestion below.
        </p>
        {wayland && (
          <p style={{ fontSize: 13, color: 'var(--text-secondary)', lineHeight: 1.6, maxWidth: 520, marginTop: 10 }}>
            On Wayland your desktop may ask you to confirm this shortcut. On Hyprland, bind it in
            your config instead: <code>bind = SUPER SHIFT, H, global, com.hoverai.desktop:capture</code>
          </p>
        )}
      </motion.div>

      {/* Key recorder */}
      <motion.div
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0, transition: { duration: 0.4, delay: 0.1, ease } }}
        style={{
          padding: '28px 24px',
          borderRadius: 'var(--radius-xl)',
          background: [
            'linear-gradient(180deg, rgba(255,255,255,0.05) 0%, rgba(255,255,255,0.01) 100%) padding-box',
            'linear-gradient(135deg, rgba(108,99,255,0.3) 0%, rgba(108,99,255,0.06) 50%, rgba(255,255,255,0.01) 100%) border-box',
            'rgba(18,16,38,0.75) padding-box',
          ].join(', '),
          border: '1px solid transparent',
          boxShadow: '0 4px 32px rgba(0,0,0,0.4)',
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          gap: 16,
        }}
      >
        {/* Key chips */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', justifyContent: 'center', minHeight: 48 }}>
          <AnimatePresence mode="wait">
            {combo ? (
              combo.split('+').map((part, i) => (
                <motion.span
                  key={`${combo}-${i}`}
                  initial={{ opacity: 0, scale: 0.8, y: 6 }}
                  animate={{ opacity: 1, scale: 1, y: 0, transition: { duration: 0.18, delay: i * 0.04, ease } }}
                  exit={{ opacity: 0, scale: 0.8, transition: { duration: 0.1 } }}
                  style={{
                    padding: '6px 14px',
                    borderRadius: 8,
                    background: 'rgba(255,255,255,0.07)',
                    border: '1px solid rgba(255,255,255,0.12)',
                    fontSize: 20,
                    fontWeight: 700,
                    color: '#fff',
                    fontFamily: 'inherit',
                  }}
                >
                  {part}
                </motion.span>
              ))
            ) : (
              <motion.span
                key="placeholder"
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                style={{ fontSize: 14, color: 'var(--text-muted)', fontWeight: 500 }}
              >
                — press a key combination —
              </motion.span>
            )}
          </AnimatePresence>
        </div>

        {/* Status */}
        <AnimatePresence mode="wait">
          <motion.p
            key={status}
            initial={{ opacity: 0, y: 3 }}
            animate={{ opacity: 1, y: 0, transition: { duration: 0.18 } }}
            exit={{ opacity: 0, y: -3, transition: { duration: 0.1 } }}
            style={{ fontSize: 13, color: statusColor, fontWeight: 500, textAlign: 'center' }}
          >
            {statusText}
          </motion.p>
        </AnimatePresence>
      </motion.div>

      {/* Suggestions */}
      <motion.div
        initial={{ opacity: 0, y: 16 }}
        animate={{ opacity: 1, y: 0, transition: { duration: 0.4, delay: 0.18, ease } }}
        style={{ display: 'flex', flexDirection: 'column', gap: 10 }}
      >
        <p style={{ fontSize: 11, fontWeight: 700, color: 'var(--text-muted)', letterSpacing: '0.06em', textTransform: 'uppercase' }}>
          Suggestions
          {pendingSuggestions.length > 0 && (
            <span style={{ color: 'var(--text-muted)', fontWeight: 400 }}> — checking {pendingSuggestions.length} more…</span>
          )}
        </p>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
          {SUGGESTIONS.map((acc) => {
            const tested = suggestions[acc] !== undefined
            const free = suggestions[acc] === true
            const taken = suggestions[acc] === false
            return (
              <button
                key={acc}
                onClick={() => free && pickSuggestion(acc)}
                disabled={!free}
                style={{
                  padding: '7px 14px',
                  borderRadius: 8,
                  border: `1px solid ${free ? 'rgba(34,197,94,0.35)' : taken ? 'rgba(255,255,255,0.06)' : 'rgba(255,255,255,0.1)'}`,
                  background: free ? 'rgba(34,197,94,0.08)' : 'rgba(255,255,255,0.03)',
                  color: free ? '#22c55e' : taken ? '#333' : 'var(--text-muted)',
                  fontSize: 12,
                  fontWeight: 600,
                  cursor: free ? 'pointer' : 'default',
                  fontFamily: 'inherit',
                  display: 'flex',
                  alignItems: 'center',
                  gap: 6,
                  transition: 'all 0.15s',
                  textDecoration: taken ? 'line-through' : 'none',
                }}
              >
                {!tested && <span style={{ opacity: 0.4 }}>·</span>}
                {free && <span>✓</span>}
                {taken && <span style={{ opacity: 0.4 }}>✗</span>}
                {suggestLabel(acc)}
              </button>
            )
          })}
        </div>
        {freeSuggestions.length === 0 && takenSuggestions.length === SUGGESTIONS.length && (
          <p style={{ fontSize: 12, color: '#ef4444', marginTop: 4 }}>
            All suggestions are taken on your system. Try <strong>Ctrl + Alt + F9</strong> or a combination with the Super (⊞) key.
          </p>
        )}
      </motion.div>

      {/* Confirm */}
      <motion.div
        initial={{ opacity: 0, y: 12 }}
        animate={{ opacity: 1, y: 0, transition: { duration: 0.4, delay: 0.25, ease } }}
        style={{ marginTop: 'auto', display: 'flex', flexDirection: 'column', gap: 12 }}
      >
        <motion.button
          onClick={handleConfirm}
          whileHover={status === 'available' ? { scale: 1.015 } : {}}
          whileTap={status === 'available' ? { scale: 0.98 } : {}}
          style={{
            width: '100%',
            padding: '15px 0',
            borderRadius: 'var(--radius-full)',
            border: 'none',
            cursor: status === 'available' ? 'pointer' : 'not-allowed',
            background: status === 'confirmed' ? '#22c55e' : 'var(--accent)',
            opacity: status === 'available' || status === 'confirmed' ? 1 : 0.38,
            color: '#fff',
            fontSize: 16,
            fontWeight: 700,
            letterSpacing: '-0.01em',
            transition: 'opacity 0.2s, background 0.3s',
          }}
        >
          {status === 'confirmed' ? '✓ Confirmed!' : 'Confirm shortcut'}
        </motion.button>
        <p style={{ fontSize: 13, color: 'var(--text-secondary)', textAlign: 'center' }}>
          {status === 'available'
            ? `"${combo}" is free — confirm to use it`
            : 'Pick a free suggestion or press your own combo above'}
        </p>
      </motion.div>
    </motion.div>
  )
}
