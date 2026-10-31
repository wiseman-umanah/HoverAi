import { useEffect, useLayoutEffect, useRef, useState, useCallback, type RefObject } from 'react'
import type { QueryResponse, BeaconStep, AppSettings, HitRect } from '@renderer/lib/tauri-api'

// ─── Panel width per overlaySize setting ──────────────────────────────────────
const PANEL_WIDTHS: Record<AppSettings['overlaySize'], number> = {
  compact: 280,
  default: 320,
  large:   400,
}

// ─── View state ───────────────────────────────────────────────────────────────
// The capture session (recording, screenshot, query, cancellation) is owned by
// Rust (src-tauri/src/lib.rs). This component only renders what Rust reports:
//   capture-start → recording, capture-submitted → loading,
//   query-result → showing_steps, query-error → error, capture-end → idle.
// User actions are sent back as intents: captureSubmit / captureCancel /
// captureAgain. Never call captureCancel() in response to capture-end.

// ─── Hit regions ──────────────────────────────────────────────────────────────
// The overlay window covers the whole monitor but only elements marked
// `data-hit` take mouse input (Rust: hit_region.rs). Report their rectangles
// whenever layout may have changed. While the steps panel is being dragged the
// whole window is interactive so a fast drag can't slip outside the region.

let lastSentRegions = ''
let dragActive = false

function reportHitRegions(force = false): void {
  const rects: HitRect[] = dragActive
    ? [{ x: 0, y: 0, w: window.innerWidth, h: window.innerHeight }]
    : Array.from(document.querySelectorAll<HTMLElement>('[data-hit]')).map((el) => {
        const r = el.getBoundingClientRect()
        return { x: r.left, y: r.top, w: r.width, h: r.height }
      })
  const key = JSON.stringify(rects.map((r) => [r.x, r.y, r.w, r.h].map(Math.round)))
  if (!force && key === lastSentRegions) return
  lastSentRegions = key
  window.api.overlayHitRegions(rects)
}

type OverlayState =
  | { kind: 'idle' }
  | { kind: 'recording' }
  | { kind: 'loading' }
  | { kind: 'showing_steps'; resp: QueryResponse; active: number }
  | { kind: 'error'; message: string }

export default function OverlayWidget() {
  const [state, setState] = useState<OverlayState>({ kind: 'idle' })

  // Panel position — lifted here so it survives query-to-query without resetting
  const [panelPos, setPanelPos] = useState<{ x: number; y: number } | null>(null)

  // Settings read on mount: panel size + whether to play the chime
  const [overlaySize, setOverlaySize] = useState<AppSettings['overlaySize']>('default')
  const soundEffectsRef = useRef(true)
  useEffect(() => {
    window.api.getSettings().then((s) => {
      setOverlaySize(s.overlaySize)
      soundEffectsRef.current = s.soundEffects
    }).catch(() => {})
  }, [])

  const submit = useCallback(() => {
    setState({ kind: 'loading' })
    window.api.captureSubmit()
  }, [])

  const cancel = useCallback(() => {
    setState({ kind: 'idle' })
    window.api.captureCancel()
  }, [])

  const askAnother = useCallback(() => {
    window.api.captureAgain()
  }, [])

  // ── Session events from Rust ──────────────────────────────────────────────
  useEffect(() => {
    const unsubs = [
      window.api.onCaptureStart(() => {
        if (soundEffectsRef.current) playChime()
        setState({ kind: 'recording' })
      }),
      window.api.onCaptureSubmitted(() => setState({ kind: 'loading' })),
      window.api.onCaptureEnd(() => setState({ kind: 'idle' })),
      window.api.onQueryResult((resp) => {
        setState({ kind: 'showing_steps', resp, active: 0 })
        if (resp.speech_b64) playAudio(resp.speech_b64)
      }),
      window.api.onQueryError((message) => setState({ kind: 'error', message })),
    ]
    return () => unsubs.forEach((u) => u())
  }, [])

  // Select which step's beacon is shown (clamped to the step list).
  const setActive = useCallback((i: number) => {
    setState((s) => s.kind === 'showing_steps'
      ? { ...s, active: Math.max(0, Math.min(s.resp.steps.length - 1, i)) }
      : s)
  }, [])

  // ── Keyboard (when the overlay has focus; Esc also works globally via Rust) ─
  useEffect(() => {
    if (state.kind === 'idle') return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault()
        cancel()
      } else if ((e.key === ' ' || e.key === 'Enter') && state.kind === 'recording') {
        e.preventDefault()
        submit()
      } else if (state.kind === 'showing_steps' && (e.key === 'ArrowDown' || e.key === 'ArrowRight')) {
        e.preventDefault()
        setActive(state.active + 1)
      } else if (state.kind === 'showing_steps' && (e.key === 'ArrowUp' || e.key === 'ArrowLeft')) {
        e.preventDefault()
        setActive(state.active - 1)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [state, submit, cancel, setActive])

  // Rust resets the input region whenever it shows the overlay, so always
  // re-send on a state change; other renders (drag, resize) send on change.
  useLayoutEffect(() => { reportHitRegions(true) }, [state])
  useLayoutEffect(() => { reportHitRegions() })
  useEffect(() => {
    const onResize = () => reportHitRegions()
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [])

  // ─── Render ────────────────────────────────────────────────────────────────
  return (
    <div style={{ position: 'fixed', inset: 0, pointerEvents: 'none', zIndex: 9999 }}>
      {state.kind === 'recording' && (
        <RecordingWidget onSubmit={submit} onCancel={cancel} />
      )}

      {state.kind === 'loading' && <LoadingBar />}

      {state.kind === 'showing_steps' && state.resp.steps[state.active]?.target && (
        <Beacon step={state.resp.steps[state.active]} />
      )}

      {state.kind === 'showing_steps' && (
        <StepsPanel
          resp={state.resp}
          active={state.active}
          onSelect={setActive}
          panelWidth={PANEL_WIDTHS[overlaySize]}
          pos={panelPos}
          setPos={setPanelPos}
          onDismiss={cancel}
          onAskAnother={askAnother}
        />
      )}

      {state.kind === 'error' && (
        <ErrorCard message={state.message} onDismiss={cancel} />
      )}
    </div>
  )
}

// ─── Audio helpers ────────────────────────────────────────────────────────────

function playChime(): void {
  try {
    const ctx = new AudioContext()
    const tones = [880, 1320]
    tones.forEach((freq, i) => {
      const osc = ctx.createOscillator()
      const gain = ctx.createGain()
      osc.type = 'sine'
      osc.frequency.value = freq
      gain.gain.setValueAtTime(0, ctx.currentTime + i * 0.09)
      gain.gain.linearRampToValueAtTime(0.18, ctx.currentTime + i * 0.09 + 0.01)
      gain.gain.linearRampToValueAtTime(0, ctx.currentTime + i * 0.09 + 0.08)
      osc.connect(gain); gain.connect(ctx.destination)
      osc.start(ctx.currentTime + i * 0.09)
      osc.stop(ctx.currentTime + i * 0.09 + 0.09)
    })
    setTimeout(() => ctx.close(), 400)
  } catch { /* swallow */ }
}

function playAudio(base64Wav: string): void {
  try {
    const binary = atob(base64Wav)
    const bytes = new Uint8Array(binary.length)
    for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i)
    const ctx = new AudioContext()
    ctx.decodeAudioData(bytes.buffer, (buffer) => {
      const source = ctx.createBufferSource()
      source.buffer = buffer
      source.connect(ctx.destination)
      source.onended = () => ctx.close()
      source.start()
    })
  } catch (err) { console.error('[overlay] audio playback error:', err) }
}

// ─── Level visualiser — bars scroll with the mic level reported by Rust ───────

function useLevelBars(canvasRef: RefObject<HTMLCanvasElement | null>, barCount: number) {
  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')!
    const levels: number[] = new Array(barCount).fill(0)
    let target = 0
    let rafId = 0
    const unsub = window.api.onAudioLevel((l) => { target = l })
    const draw = () => {
      rafId = requestAnimationFrame(draw)
      // Ease towards the latest level, then scroll the history left
      const next = levels[levels.length - 1] * 0.5 + target * 0.5
      levels.shift(); levels.push(next)
      const { width: W, height: H } = canvas
      ctx.clearRect(0, 0, W, H)
      const barW = Math.max(2, Math.floor((W - (barCount - 1)) / barCount))
      levels.forEach((pct, i) => {
        const barH = Math.max(3, Math.round(Math.pow(pct, 0.6) * H))
        ctx.fillStyle = `rgba(108,99,255,${(0.45 + pct * 0.55).toFixed(2)})`
        ctx.beginPath(); ctx.roundRect(i * (barW + 1), (H - barH) / 2, barW, barH, 2); ctx.fill()
      })
    }
    draw()
    return () => { cancelAnimationFrame(rafId); unsub() }
  }, [canvasRef, barCount])
}

// ─── Recording widget ─────────────────────────────────────────────────────────

function RecordingWidget({ onSubmit, onCancel }: {
  onSubmit: () => void; onCancel: () => void
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  useLevelBars(canvasRef, 32)
  return (
    <div style={{ position: 'fixed', inset: 0, userSelect: 'none', pointerEvents: 'none' }}>
      <div data-hit style={{
        position: 'absolute', bottom: 40, left: '50%', transform: 'translateX(-50%)',
        width: 300, background: 'rgba(10,10,14,0.92)',
        border: '1px solid rgba(108,99,255,0.55)', borderRadius: 18,
        padding: '16px 20px', display: 'flex', flexDirection: 'column', gap: 12,
        pointerEvents: 'auto', boxShadow: '0 8px 32px rgba(0,0,0,0.6)',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <span style={{ width: 8, height: 8, borderRadius: '50%', background: '#ef4444', flexShrink: 0, animation: 'mic-pulse 1s ease-in-out infinite', display: 'block' }} />
          <span style={{ color: '#fff', fontSize: 14, fontWeight: 600 }}>Listening…</span>
          <span style={{ marginLeft: 'auto', color: '#555', fontSize: 11 }}>HoverAI</span>
        </div>
        <canvas ref={canvasRef} width={260} height={40} style={{ width: '100%', height: 40, borderRadius: 8, display: 'block' }} />
        <p style={{ color: '#666', fontSize: 12, margin: 0, textAlign: 'center' }}>
          Shortcut again or <Kbd>Enter</Kbd> to send · <Kbd>Esc</Kbd> to cancel
        </p>
        <button onClick={onSubmit} style={{ padding: '10px 0', borderRadius: 999, border: 'none', background: '#6c63ff', color: '#fff', fontSize: 14, fontWeight: 700, cursor: 'pointer', width: '100%' }}>
          Done — send query ↵
        </button>
        <button onClick={onCancel} style={{ padding: '6px 0', borderRadius: 999, border: '1px solid rgba(255,255,255,0.1)', background: 'transparent', color: '#666', fontSize: 13, cursor: 'pointer', width: '100%' }}>
          Cancel
        </button>
      </div>
      <style>{`@keyframes mic-pulse { 0%,100%{opacity:1} 50%{opacity:0.15} }`}</style>
    </div>
  )
}

function Kbd({ children }: { children: React.ReactNode }) {
  return (
    <kbd style={{ background: 'rgba(255,255,255,0.1)', border: '1px solid rgba(255,255,255,0.2)', borderRadius: 4, padding: '1px 5px', fontSize: 11, fontFamily: 'inherit', color: '#ccc' }}>
      {children}
    </kbd>
  )
}

// ─── Loading bar ──────────────────────────────────────────────────────────────

function LoadingBar() {
  return (
    <div style={{ position: 'absolute', right: 0, top: '50%', transform: 'translateY(-50%)', width: 4, height: 120, borderRadius: '4px 0 0 4px', background: 'linear-gradient(180deg,#6c63ff,#a78bfa)', animation: 'bar-pulse 1.2s ease-in-out infinite', pointerEvents: 'none' }}>
      <style>{`@keyframes bar-pulse { 0%,100%{opacity:0.5;height:80px} 50%{opacity:1;height:140px} }`}</style>
    </div>
  )
}

// ─── Steps panel — draggable, position persisted across queries ───────────────

function StepsPanel({ resp, active, onSelect, panelWidth, pos, setPos, onDismiss, onAskAnother }: {
  resp: QueryResponse
  active: number
  onSelect: (i: number) => void
  panelWidth: number
  pos: { x: number; y: number } | null
  setPos: (p: { x: number; y: number }) => void
  onDismiss: () => void
  onAskAnother: () => void
}) {
  // On first mount (or if pos was never set), anchor to right edge, vertically centred.
  const resolvedPos = pos ?? {
    x: window.innerWidth - panelWidth,
    y: Math.max(0, Math.round(window.innerHeight / 2 - 300)),
  }

  const dragging = useRef(false)
  const dragStart = useRef({ mx: 0, my: 0, px: 0, py: 0 })

  const onMouseDown = (e: React.MouseEvent) => {
    if ((e.target as HTMLElement).tagName === 'BUTTON') return
    e.preventDefault()
    dragging.current = true
    dragActive = true
    reportHitRegions()
    dragStart.current = { mx: e.clientX, my: e.clientY, px: resolvedPos.x, py: resolvedPos.y }
    const onMove = (ev: MouseEvent) => {
      if (!dragging.current) return
      setPos({
        x: Math.max(0, Math.min(window.innerWidth - panelWidth, dragStart.current.px + (ev.clientX - dragStart.current.mx))),
        y: Math.max(0, Math.min(window.innerHeight - 80, dragStart.current.py + (ev.clientY - dragStart.current.my))),
      })
    }
    const onUp = () => {
      dragging.current = false
      dragActive = false
      reportHitRegions()
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mouseup', onUp)
    }
    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', onUp)
  }

  return (
    <>
      <style>{`
        @keyframes panel-slide-in {
          from { opacity: 0; transform: translateX(24px); }
          to   { opacity: 1; transform: translateX(0); }
        }
        .hover-steps-scroll { scrollbar-width: thin; scrollbar-color: rgba(108,99,255,0.4) transparent; }
        .hover-steps-scroll::-webkit-scrollbar { width: 4px; }
        .hover-steps-scroll::-webkit-scrollbar-track { background: transparent; }
        .hover-steps-scroll::-webkit-scrollbar-thumb { background: rgba(108,99,255,0.4); border-radius: 4px; }
      `}</style>
      <div data-hit onAnimationEnd={() => reportHitRegions()} style={{ position: 'absolute', left: resolvedPos.x, top: resolvedPos.y, pointerEvents: 'auto', userSelect: 'none', animation: 'panel-slide-in 0.25s cubic-bezier(0.22,1,0.36,1) both' }}>
        <div style={{ position: 'relative', width: panelWidth }}>
          <div
            className="hover-steps-scroll"
            style={{ width: panelWidth, maxHeight: '80vh', overflowY: 'auto', background: 'rgba(18,18,22,0.96)', backdropFilter: 'blur(14px)', border: '1px solid rgba(255,255,255,0.1)', borderRadius: 16, padding: '0 0 16px', boxShadow: '0 8px 36px rgba(0,0,0,0.65)' }}
          >
            {/* Drag handle / header */}
            <div onMouseDown={onMouseDown} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '14px 16px 12px', cursor: 'grab', borderBottom: '1px solid rgba(255,255,255,0.07)', position: 'sticky', top: 0, background: 'rgba(18,18,22,0.98)', borderRadius: '16px 16px 0 0', zIndex: 1 }}>
              <span style={{ color: '#444', fontSize: 14, letterSpacing: 1, lineHeight: 1, flexShrink: 0 }}>⠿</span>
              <span style={{ color: '#6c63ff', fontSize: 15, lineHeight: 1, flexShrink: 0 }}>✦</span>
              <span style={{ color: '#fff', fontSize: 14, fontWeight: 700, flex: 1 }}>HoverAI</span>
              <button onClick={onDismiss} style={{ background: 'none', border: 'none', color: '#666', fontSize: 18, cursor: 'pointer', lineHeight: 1, padding: '2px 4px', borderRadius: 4 }} title="Dismiss (Esc)">×</button>
            </div>

            <div style={{ padding: '14px 16px 0' }}>
              {resp.transcript && (
                <p style={{ margin: '0 0 14px', fontSize: 12, color: '#888', lineHeight: 1.5, borderBottom: '1px solid rgba(255,255,255,0.07)', paddingBottom: 12 }}>
                  &ldquo;{resp.transcript}&rdquo;
                </p>
              )}
              <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
                {resp.steps.map((step, i) => (
                  <StepCard key={step.step} step={step} active={i === active} onClick={() => onSelect(i)} />
                ))}
              </div>
              {resp.steps.length > 1 && (
                <div style={{ display: 'flex', gap: 8, marginTop: 12 }}>
                  <button
                    onClick={() => onSelect(active - 1)}
                    disabled={active === 0}
                    style={{ flex: 1, padding: '8px 0', borderRadius: 999, border: '1px solid rgba(255,255,255,0.12)', background: 'transparent', color: active === 0 ? '#444' : '#ccc', fontSize: 13, cursor: active === 0 ? 'default' : 'pointer' }}
                  >
                    ← Back
                  </button>
                  <button
                    onClick={() => onSelect(active + 1)}
                    disabled={active === resp.steps.length - 1}
                    style={{ flex: 1, padding: '8px 0', borderRadius: 999, border: '1px solid rgba(108,99,255,0.5)', background: 'rgba(108,99,255,0.15)', color: active === resp.steps.length - 1 ? '#555' : '#c4b5fd', fontSize: 13, fontWeight: 600, cursor: active === resp.steps.length - 1 ? 'default' : 'pointer' }}
                  >
                    Next step →
                  </button>
                </div>
              )}
              {/* Ask another question — stays in the same overlay, no hide/show */}
              <button
                onClick={onAskAnother}
                style={{
                  marginTop: 16, width: '100%', padding: '10px 0',
                  borderRadius: 999, border: 'none', background: '#6c63ff',
                  color: '#fff', fontSize: 13, fontWeight: 700, cursor: 'pointer',
                  letterSpacing: '0.01em',
                }}
              >
                🎙 Ask another question
              </button>
              <p style={{ margin: '10px 0 0', fontSize: 11, color: '#444', textAlign: 'center' }}>
                Press <Kbd>Esc</Kbd> or click × to dismiss
              </p>
            </div>
          </div>

          {/* Scroll fade — hints there is more content below */}
          <div style={{ position: 'absolute', bottom: 0, left: 0, right: 0, height: 40, borderRadius: '0 0 16px 16px', background: 'linear-gradient(to bottom, transparent, rgba(18,18,22,0.96))', pointerEvents: 'none' }} />
        </div>
      </div>
    </>
  )
}

function StepCard({ step, active, onClick }: { step: BeaconStep; active: boolean; onClick: () => void }) {
  return (
    <div
      onClick={onClick}
      style={{
        background: active ? 'rgba(108,99,255,0.12)' : 'rgba(255,255,255,0.04)',
        borderRadius: 10,
        border: active ? '1px solid rgba(108,99,255,0.6)' : '1px solid rgba(255,255,255,0.07)',
        padding: '10px 12px',
        cursor: 'pointer',
        transition: 'background 0.15s, border-color 0.15s',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: 8 }}>
        <span style={{ flexShrink: 0, width: 22, height: 22, borderRadius: '50%', background: 'rgba(108,99,255,0.25)', border: '1px solid rgba(108,99,255,0.5)', color: '#a78bfa', fontSize: 11, fontWeight: 700, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
          {step.step}
        </span>
        <span style={{ color: '#e5e7eb', fontSize: 13, lineHeight: 1.5, flex: 1 }}>{step.instruction}</span>
      </div>
      {step.keys && (
        <div style={{ marginTop: 6, marginLeft: 30 }}>
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4, background: 'rgba(108,99,255,0.15)', border: '1px solid rgba(108,99,255,0.3)', borderRadius: 6, padding: '2px 8px', fontSize: 11, color: '#a78bfa' }}>
            ⌨ {step.keys}
          </span>
        </div>
      )}
      {step.tip && (
        <p style={{ margin: '6px 0 0', marginLeft: 30, fontSize: 11, color: '#6b7280', lineHeight: 1.4 }}>💡 {step.tip}</p>
      )}
    </div>
  )
}

// ─── Beacon — highlights the current step's target on screen ──────────────────
// Coordinates are normalised to the captured monitor, which is the monitor
// this full-screen overlay covers. Not a hit region: clicks pass through to
// the element underneath, so the user can act on it directly.

function Beacon({ step }: { step: BeaconStep }) {
  const t = step.target!
  const pad = 6
  const left = t.x * window.innerWidth - pad
  const top = t.y * window.innerHeight - pad
  const width = t.w * window.innerWidth + pad * 2
  const height = t.h * window.innerHeight + pad * 2
  const badgeAbove = top > 28
  return (
    <div key={step.step} style={{ position: 'absolute', left, top, width, height, pointerEvents: 'none' }}>
      <style>{`
        @keyframes beacon-pulse {
          0%   { box-shadow: 0 0 0 0 rgba(108,99,255,0.65), 0 0 18px rgba(108,99,255,0.55); }
          70%  { box-shadow: 0 0 0 14px rgba(108,99,255,0), 0 0 18px rgba(108,99,255,0.55); }
          100% { box-shadow: 0 0 0 0 rgba(108,99,255,0), 0 0 18px rgba(108,99,255,0.55); }
        }
        @keyframes beacon-in { from { opacity: 0; transform: scale(1.25); } to { opacity: 1; transform: scale(1); } }
      `}</style>
      <div style={{
        position: 'absolute', inset: 0, borderRadius: 10,
        border: '3px solid #8b7dff', background: 'rgba(108,99,255,0.08)',
        animation: 'beacon-in 0.25s cubic-bezier(0.22,1,0.36,1) both, beacon-pulse 1.6s ease-out 0.25s infinite',
      }} />
      <span style={{
        position: 'absolute', left: -2, [badgeAbove ? 'bottom' : 'top']: height + 4,
        background: '#6c63ff', color: '#fff', fontSize: 12, fontWeight: 700,
        borderRadius: 999, padding: '2px 9px', whiteSpace: 'nowrap',
        boxShadow: '0 2px 10px rgba(0,0,0,0.45)',
      }}>
        Step {step.step}
      </span>
    </div>
  )
}

// ─── Error card — no auto-dismiss, requires explicit action ──────────────────

function ErrorCard({ message, onDismiss }: { message: string; onDismiss: () => void }) {
  return (
    <div data-hit style={{ position: 'absolute', bottom: 32, left: '50%', transform: 'translateX(-50%)', background: 'rgba(20,10,10,0.96)', border: '1px solid rgba(239,68,68,0.4)', borderRadius: 12, padding: '14px 20px', maxWidth: 400, display: 'flex', alignItems: 'center', gap: 10, pointerEvents: 'auto', boxShadow: '0 4px 24px rgba(0,0,0,0.55)' }}>
      <span style={{ color: '#ef4444', fontSize: 18, flexShrink: 0 }}>⚠</span>
      <div style={{ flex: 1 }}>
        <p style={{ margin: 0, color: '#fff', fontSize: 13, fontWeight: 600 }}>Something went wrong</p>
        <p style={{ margin: '3px 0 0', color: '#888', fontSize: 12 }}>{message}</p>
      </div>
      <button onClick={onDismiss} style={{ background: 'none', border: 'none', color: '#555', fontSize: 18, cursor: 'pointer', padding: '0 2px', flexShrink: 0 }}>×</button>
    </div>
  )
}
