import { useEffect, useRef, useState, useCallback, type RefObject } from 'react'
import type { QueryResponse, BeaconStep, AppSettings } from '@renderer/lib/tauri-api'

// ─── Panel width per overlaySize setting ──────────────────────────────────────
const PANEL_WIDTHS: Record<AppSettings['overlaySize'], number> = {
  compact: 280,
  default: 320,
  large:   400,
}

// ─── State machine ────────────────────────────────────────────────────────────
// idle → recording → loading → showing_steps | error → idle

type OverlayState =
  | { kind: 'idle' }
  | { kind: 'recording'; stream: MediaStream | null }
  | { kind: 'loading' }
  | { kind: 'showing_steps'; resp: QueryResponse }
  | { kind: 'error'; message: string }

export default function OverlayWidget() {
  const [state, setState] = useState<OverlayState>({ kind: 'idle' })

  // Panel position — lifted here so it survives query-to-query without resetting
  const [panelPos, setPanelPos] = useState<{ x: number; y: number } | null>(null)

  // overlaySize from settings — read once on mount
  const [overlaySize, setOverlaySize] = useState<AppSettings['overlaySize']>('default')
  useEffect(() => {
    window.api.getSettings().then((s) => setOverlaySize(s.overlaySize)).catch(() => {})
  }, [])

  // Audio recording refs
  const mediaRecorderRef = useRef<MediaRecorder | null>(null)
  const audioChunksRef = useRef<Blob[]>([])
  const streamRef = useRef<MediaStream | null>(null)

  // When the user hits "Ask another", we take a fresh screenshot and stash it
  // here so submitQuery can send it instead of the original lastScreenshot.
  const freshScreenshotRef = useRef<string | null>(null)

  const startRecording = useCallback(async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      streamRef.current = stream
      const mimeType = [
        'audio/webm;codecs=opus', 'audio/webm',
        'audio/ogg;codecs=opus',  'audio/ogg', 'audio/mp4',
      ].find((m) => MediaRecorder.isTypeSupported(m)) ?? ''
      console.log('[overlay] MediaRecorder mimeType:', mimeType || '(browser default)')
      const recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined)
      audioChunksRef.current = []
      recorder.ondataavailable = (e) => { if (e.data.size > 0) audioChunksRef.current.push(e.data) }
      recorder.start()
      mediaRecorderRef.current = recorder
    } catch (err) {
      console.error('[overlay] failed to start audio recording:', err)
    }
  }, [])

  const stopRecording = useCallback((): Promise<Uint8Array> => {
    return new Promise((resolve) => {
      const recorder = mediaRecorderRef.current
      if (!recorder || recorder.state === 'inactive') { resolve(new Uint8Array(0)); return }
      recorder.onstop = async () => {
        const mimeType = recorder.mimeType || 'audio/webm'
        const blob = new Blob(audioChunksRef.current, { type: mimeType })
        console.log('[overlay] audio blob size:', blob.size, 'bytes, type:', mimeType)
        const buf = await blob.arrayBuffer()
        resolve(new Uint8Array(buf))
        streamRef.current?.getTracks().forEach((t) => t.stop())
        streamRef.current = null
        mediaRecorderRef.current = null
      }
      recorder.stop()
    })
  }, [])

  // Reset local state only — does NOT call dismissOverlay IPC.
  // Used by onCaptureEnd (Tauri already hid the window) to avoid a second
  // dismiss_overlay call that would create a hide/capture-end loop.
  const resetState = useCallback(() => {
    setState({ kind: 'idle' })
    stopRecording().catch(() => {})
  }, [stopRecording])

  const dismiss = useCallback(() => {
    setState({ kind: 'idle' })
    stopRecording().catch(() => {})
    // Tell main to hide the window and unblock the screen.
    // Without this the BrowserWindow stays shown and intercepts input.
    window.api.dismissOverlay()
  }, [stopRecording])

  const submitQuery = useCallback(async () => {
    setState({ kind: 'loading' })
    const audioBytes = await stopRecording()
    // If the user asked a follow-up, send the fresh screenshot via a dedicated
    // IPC field so the main process doesn't re-use the original capture.
    if (freshScreenshotRef.current) {
      window.api.captureDoneWithScreenshot({
        audioData: Array.from(audioBytes),
        screenshotDataUrl: freshScreenshotRef.current,
      })
      freshScreenshotRef.current = null
    } else {
      window.api.captureDone({ audioData: Array.from(audioBytes) })
    }
  }, [stopRecording])

  // ── "Ask another question" from the steps panel ───────────────────────────
  const askAnother = useCallback(async () => {
    playChime()
    // Take a fresh screenshot of the current screen state before the recording
    // widget appears (overlay is already visible so we capture behind it).
    try {
      const dataUrl = await window.api.takeScreenshot()
      freshScreenshotRef.current = `data:image/png;base64,${dataUrl}`
    } catch {
      freshScreenshotRef.current = null
    }
    await startRecording()
    setState({ kind: 'recording', stream: streamRef.current })
    // Ask main to focus the overlay window so Space/Enter/Esc land here.
    // The overlay is already visible — this just transfers keyboard focus.
    window.api.focusOverlay()
  }, [startRecording])

  // ── capture-start / capture-end ──────────────────────────────────────────
  useEffect(() => {
    const unsubStart = window.api.onCaptureStart(async () => {
      playChime()
      await startRecording()
      setState({ kind: 'recording', stream: streamRef.current })
    })
    // capture-end is only emitted by Tauri when the shortcut key is pressed
    // while the overlay is already visible (toggle-dismiss). Tauri has already
    // hidden the window, so we only reset renderer state — no IPC call.
    const unsubEnd = window.api.onCaptureEnd(() => { resetState() })
    return () => { unsubStart(); unsubEnd() }
  }, [startRecording, resetState])

  // ── Query results ─────────────────────────────────────────────────────────
  useEffect(() => {
    const unsubResult = window.api.onQueryResult((resp) => {
      setState({ kind: 'showing_steps', resp })
      if (resp.speech_b64) playAudio(resp.speech_b64)
    })
    const unsubError = window.api.onQueryError((message) => {
      setState({ kind: 'error', message })
    })
    return () => { unsubResult(); unsubError() }
  }, [])

  // ── Keyboard shortcuts ────────────────────────────────────────────────────
  useEffect(() => {
    if (state.kind === 'idle') return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        if (state.kind === 'recording') stopRecording().then(() => window.api.captureDone(null))
        else dismiss()
      }
      if ((e.key === ' ' || e.key === 'Enter') && state.kind === 'recording') {
        e.preventDefault()
        submitQuery()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [state, stopRecording, submitQuery, dismiss])

  // ─── Render ────────────────────────────────────────────────────────────────
  return (
    <div style={{ position: 'fixed', inset: 0, pointerEvents: 'none', zIndex: 9999 }}>
      {state.kind === 'recording' && (
        <RecordingWidget
          stream={state.stream}
          onSubmit={submitQuery}
          onCancel={() => stopRecording().then(() => window.api.captureDone(null))}
        />
      )}

      {state.kind === 'loading' && <LoadingBar />}

      {state.kind === 'showing_steps' && (
        <StepsPanel
          resp={state.resp}
          panelWidth={PANEL_WIDTHS[overlaySize]}
          pos={panelPos}
          setPos={setPanelPos}
          onDismiss={dismiss}
          onAskAnother={askAnother}
        />
      )}

      {state.kind === 'error' && (
        <ErrorCard message={state.message} onDismiss={dismiss} />
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
      source.start()
    })
  } catch (err) { console.error('[overlay] audio playback error:', err) }
}

// ─── Audio visualiser hook ────────────────────────────────────────────────────

function useAudioVisualiser(canvasRef: RefObject<HTMLCanvasElement | null>, stream: MediaStream | null) {
  useEffect(() => {
    if (!stream || !canvasRef.current) return
    const audioCtx = new AudioContext()
    const analyser = audioCtx.createAnalyser()
    analyser.fftSize = 64; analyser.smoothingTimeConstant = 0.75
    const source = audioCtx.createMediaStreamSource(stream)
    source.connect(analyser)
    const canvas = canvasRef.current
    const ctx = canvas.getContext('2d')!
    const bufferLength = analyser.frequencyBinCount
    const dataArray = new Uint8Array(bufferLength)
    let rafId = 0
    const draw = () => {
      rafId = requestAnimationFrame(draw)
      analyser.getByteFrequencyData(dataArray)
      const { width: W, height: H } = canvas
      ctx.clearRect(0, 0, W, H)
      const barW = Math.floor((W - (bufferLength - 1)) / bufferLength)
      let x = 0
      for (let i = 0; i < bufferLength; i++) {
        const pct = dataArray[i] / 255
        const barH = Math.max(3, Math.round(pct * H))
        ctx.fillStyle = `rgba(108,99,255,${(0.45 + pct * 0.55).toFixed(2)})`
        ctx.beginPath(); ctx.roundRect(x, H - barH, barW, barH, 2); ctx.fill()
        x += barW + 1
      }
    }
    draw()
    return () => { cancelAnimationFrame(rafId); source.disconnect(); audioCtx.close() }
  }, [stream, canvasRef])
}

// ─── Recording widget ─────────────────────────────────────────────────────────

function RecordingWidget({ stream, onSubmit, onCancel }: {
  stream: MediaStream | null; onSubmit: () => void; onCancel: () => void
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  useAudioVisualiser(canvasRef, stream)
  return (
    <div style={{ position: 'fixed', inset: 0, userSelect: 'none', pointerEvents: 'none' }}>
      <div style={{
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
          <Kbd>Space</Kbd> or <Kbd>Enter</Kbd> to send · <Kbd>Esc</Kbd> to cancel
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

function StepsPanel({ resp, panelWidth, pos, setPos, onDismiss, onAskAnother }: {
  resp: QueryResponse
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
      <div style={{ position: 'absolute', left: resolvedPos.x, top: resolvedPos.y, pointerEvents: 'auto', userSelect: 'none', animation: 'panel-slide-in 0.25s cubic-bezier(0.22,1,0.36,1) both' }}>
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
                {resp.steps.map((step) => <StepCard key={step.step} step={step} />)}
              </div>
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

function StepCard({ step }: { step: BeaconStep }) {
  return (
    <div style={{ background: 'rgba(255,255,255,0.04)', borderRadius: 10, border: '1px solid rgba(255,255,255,0.07)', padding: '10px 12px' }}>
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

// ─── Error card — no auto-dismiss, requires explicit action ──────────────────

function ErrorCard({ message, onDismiss }: { message: string; onDismiss: () => void }) {
  return (
    <div style={{ position: 'absolute', bottom: 32, left: '50%', transform: 'translateX(-50%)', background: 'rgba(20,10,10,0.96)', border: '1px solid rgba(239,68,68,0.4)', borderRadius: 12, padding: '14px 20px', maxWidth: 400, display: 'flex', alignItems: 'center', gap: 10, pointerEvents: 'auto', boxShadow: '0 4px 24px rgba(0,0,0,0.55)' }}>
      <span style={{ color: '#ef4444', fontSize: 18, flexShrink: 0 }}>⚠</span>
      <div style={{ flex: 1 }}>
        <p style={{ margin: 0, color: '#fff', fontSize: 13, fontWeight: 600 }}>Something went wrong</p>
        <p style={{ margin: '3px 0 0', color: '#888', fontSize: 12 }}>{message}</p>
      </div>
      <button onClick={onDismiss} style={{ background: 'none', border: 'none', color: '#555', fontSize: 18, cursor: 'pointer', padding: '0 2px', flexShrink: 0 }}>×</button>
    </div>
  )
}
