import { useState, useEffect } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import MicVisualiser from '@renderer/components/MicVisualiser'

interface PermissionsPageProps {
  onComplete: () => void
}

type PermissionStatus = 'idle' | 'requesting' | 'granted' | 'denied'

interface PermissionItem {
  id: 'microphone' | 'screen'
  label: string
  description: string
  icon: string
}

const PERMISSIONS: PermissionItem[] = [
  {
    id: 'microphone',
    label: 'Microphone',
    description: 'For voice commands',
    icon: 'ri-mic-line',
  },
  {
    id: 'screen',
    label: 'Screen recording',
    description: 'For visual context',
    icon: 'ri-computer-line',
  },
]

export default function PermissionsPage({ onComplete }: PermissionsPageProps) {
  const [statuses, setStatuses] = useState<Record<string, PermissionStatus>>({
    microphone: 'idle',
    screen: 'idle',
  })

  const allGranted = PERMISSIONS.every((p) => statuses[p.id] === 'granted')

  // Enter key triggers Continue when all permissions are granted
  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === 'Enter' && allGranted) onComplete()
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [allGranted, onComplete])

  async function handleToggle(id: string) {
    const current = statuses[id]
    if (current === 'granted') {
      setStatuses((prev) => ({ ...prev, [id]: 'idle' }))
      return
    }
    if (current === 'requesting') return
    setStatuses((prev) => ({ ...prev, [id]: 'requesting' }))
    try {
      // Both checks run natively in Rust: it opens the microphone (cpal) and
      // takes a test screenshot, which also triggers the macOS prompts.
      const result = await window.api.requestPermission(id)
      setStatuses((prev) => ({ ...prev, [id]: result }))
    } catch {
      setStatuses((prev) => ({ ...prev, [id]: 'denied' }))
    }
  }

  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1, transition: { duration: 0.35, ease: [0.22, 1, 0.36, 1] as const } }}
      style={{
        display: 'flex',
        flexDirection: 'column',
        height: '100%',
        width: '100%',
        background: 'var(--bg)',
        overflow: 'hidden',
      }}
    >
      {/* ── Header — fixed, never scrolls ── */}
      <motion.div
        initial={{ opacity: 0, y: 16 }}
        animate={{ opacity: 1, y: 0, transition: { duration: 0.4, delay: 0.05, ease: [0.22, 1, 0.36, 1] as const } }}
        style={{ padding: '52px 48px 0', flexShrink: 0 }}
      >
        <h1 style={{ fontSize: 38, fontWeight: 700, letterSpacing: '-0.025em', color: 'var(--text-primary)', marginBottom: 12 }}>
          Grant device access
        </h1>
        <p style={{ fontSize: 16, color: 'var(--text-secondary)', lineHeight: 1.6, maxWidth: 520 }}>
          Hover AI needs these to hear your commands and see what&apos;s on screen. Nothing leaves
          your device without your ask.
        </p>
      </motion.div>

      {/* ── Scrollable cards area ── */}
      <div
        style={{
          flex: 1,
          overflowY: 'auto',
          overflowX: 'hidden',
          padding: '32px 48px 24px',
          display: 'flex',
          flexDirection: 'column',
          gap: 14,
          // Hide scrollbar cross-browser
          scrollbarWidth: 'none',
          msOverflowStyle: 'none',
        } as React.CSSProperties}
      >
        <style>{`
          .permissions-scroll::-webkit-scrollbar { display: none; }
          @keyframes spin { to { transform: rotate(360deg); } }
        `}</style>

        {PERMISSIONS.map(({ id, label, description, icon }, i) => {
          const status = statuses[id]
          const isOn = status === 'granted'
          const isPending = status === 'requesting'
          const isDenied = status === 'denied'

          return (
            <div key={id}>
              <motion.div
                key={`${id}-card`}
                initial={{ opacity: 0, y: 20 }}
                animate={{ opacity: 1, y: 0, transition: { duration: 0.4, delay: 0.1 + i * 0.08, ease: [0.22, 1, 0.36, 1] as const } }}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 16,
                  padding: '20px 24px',
                  borderRadius: 'var(--radius-lg)',
                  background: [
                    'linear-gradient(180deg, rgba(255,255,255,0.06) 0%, rgba(255,255,255,0.01) 100%) padding-box',
                    'linear-gradient(135deg, rgba(255,255,255,0.20) 0%, rgba(255,255,255,0.06) 35%, rgba(255,255,255,0.01) 70%, rgba(255,255,255,0.005) 100%) border-box',
                    'rgba(18,16,38,0.75) padding-box',
                  ].join(', '),
                  border: isDenied ? '1px solid rgba(239,68,68,0.4)' : '1px solid transparent',
                  boxShadow: '0 4px 24px rgba(0,0,0,0.35), 0 1px 4px rgba(0,0,0,0.25)',
                  transition: 'border-color 0.2s',
                }}
              >
                {/* Icon box */}
                <div
                  style={{
                    width: 52, height: 52,
                    borderRadius: 'var(--radius-md)',
                    background: [
                      'linear-gradient(135deg, rgba(255,255,255,0.14) 0%, rgba(255,255,255,0.03) 60%, rgba(255,255,255,0.01) 100%) border-box',
                      'rgba(255,255,255,0.05) padding-box',
                    ].join(', '),
                    border: '1px solid transparent',
                    display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0,
                  }}
                >
                  <i className={icon} style={{ fontSize: 24, color: isOn ? 'var(--accent)' : isDenied ? '#ef4444' : 'var(--text-secondary)', transition: 'color 0.2s' }} />
                </div>

                {/* Text */}
                <div style={{ flex: 1 }}>
                  <p style={{ fontWeight: 700, fontSize: 17, color: 'var(--text-primary)', marginBottom: 2 }}>{label}</p>
                  <AnimatePresence mode="wait">
                    <motion.p
                      key={status}
                      initial={{ opacity: 0, y: 4 }}
                      animate={{ opacity: 1, y: 0, transition: { duration: 0.2 } }}
                      exit={{ opacity: 0, y: -4, transition: { duration: 0.1 } }}
                      style={{ fontSize: 14, color: isDenied ? '#ef4444' : 'var(--text-secondary)' }}
                    >
                      {isPending ? 'Requesting access…' : isDenied ? 'Access denied — check system settings' : description}
                    </motion.p>
                  </AnimatePresence>
                </div>

                {/* Toggle */}
                <button
                  onClick={() => handleToggle(id)}
                  disabled={isPending}
                  role="switch"
                  aria-checked={isOn}
                  style={{
                    width: 50, height: 28,
                    borderRadius: 'var(--radius-full)',
                    border: 'none',
                    cursor: isPending ? 'wait' : 'pointer',
                    padding: 3,
                    background: isOn ? 'var(--accent)' : isDenied ? 'rgba(239,68,68,0.3)' : '#3a3a3a',
                    transition: 'background 0.25s',
                    display: 'flex', alignItems: 'center',
                    justifyContent: isOn ? 'flex-end' : 'flex-start',
                    flexShrink: 0,
                    opacity: isPending ? 0.6 : 1,
                  }}
                >
                  {isPending ? (
                    <span style={{ width: 22, height: 22, borderRadius: '50%', background: '#fff', display: 'flex', alignItems: 'center', justifyContent: 'center', margin: '0 auto', flexShrink: 0 }}>
                      <i className="ri-loader-4-line" style={{ fontSize: 13, color: '#555', animation: 'spin 0.8s linear infinite' }} />
                    </span>
                  ) : (
                    <motion.span
                      layout
                      transition={{ type: 'spring', stiffness: 500, damping: 30 }}
                      style={{ width: 22, height: 22, borderRadius: '50%', background: '#fff', display: 'block', boxShadow: '0 1px 4px rgba(0,0,0,0.3)', flexShrink: 0 }}
                    />
                  )}
                </button>
              </motion.div>

              {/* Mic visualiser */}
              {id === 'microphone' && (
                <AnimatePresence>
                  {isOn && (
                    <motion.div
                      key="mic-vis"
                      initial={{ opacity: 0, height: 0, marginTop: 0 }}
                      animate={{ opacity: 1, height: 'auto', marginTop: 12, transition: { duration: 0.35, ease: [0.22, 1, 0.36, 1] as const } }}
                      exit={{ opacity: 0, height: 0, marginTop: 0, transition: { duration: 0.2 } }}
                      style={{ overflow: 'hidden' }}
                    >
                      <div
                        style={{
                          display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 10,
                          padding: '16px 24px',
                          borderRadius: 'var(--radius-lg)',
                          background: [
                            'linear-gradient(180deg, rgba(255,255,255,0.04) 0%, rgba(255,255,255,0.01) 100%) padding-box',
                            'linear-gradient(135deg, rgba(108,99,255,0.25) 0%, rgba(108,99,255,0.06) 50%, rgba(255,255,255,0.01) 100%) border-box',
                            'rgba(18,16,38,0.6) padding-box',
                          ].join(', '),
                          border: '1px solid transparent',
                        }}
                      >
                        <p style={{ fontSize: 12, color: 'var(--text-secondary)', letterSpacing: '0.04em', textTransform: 'uppercase', fontWeight: 600 }}>Listening</p>
                        <MicVisualiser />
                      </div>
                    </motion.div>
                  )}
                </AnimatePresence>
              )}
            </div>
          )
        })}

      </div>

      {/* ── CTA — pinned at bottom, never scrolls ── */}
      <motion.div
        initial={{ opacity: 0, y: 16 }}
        animate={{ opacity: 1, y: 0, transition: { duration: 0.4, delay: 0.3, ease: [0.22, 1, 0.36, 1] as const } }}
        style={{ padding: '0 48px 40px', flexShrink: 0, display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 14 }}
      >
        <motion.button
          onClick={allGranted ? onComplete : undefined}
          whileHover={allGranted ? { scale: 1.015 } : {}}
          whileTap={allGranted ? { scale: 0.98 } : {}}
          style={{
            width: '100%',
            padding: '16px 0',
            borderRadius: 'var(--radius-full)',
            border: 'none',
            cursor: allGranted ? 'pointer' : 'not-allowed',
            background: 'var(--accent)',
            opacity: allGranted ? 1 : 0.45,
            color: '#fff',
            fontSize: 17,
            fontWeight: 700,
            letterSpacing: '-0.01em',
            transition: 'opacity 0.25s',
          }}
        >
          Continue
        </motion.button>
        <AnimatePresence mode="wait">
          <motion.p
            key={allGranted ? 'ready' : 'waiting'}
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            style={{ fontSize: 14, color: 'var(--text-secondary)' }}
          >
            {allGranted ? "Permissions granted — press Enter or click Continue \uD83C\uDF89" : 'Enable both permissions to continue'}
          </motion.p>
        </AnimatePresence>
      </motion.div>
    </motion.div>
  )
}
