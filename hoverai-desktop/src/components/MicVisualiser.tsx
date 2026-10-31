import { useEffect, useRef } from 'react'

const BAR_COUNT = 28
const BAR_GAP = 3
const BAR_WIDTH = 4
const MIN_H = 3   // px — resting height
const MAX_H = 36  // px — peak height

/**
 * Live microphone bars for the onboarding mic test.
 *
 * The mic is opened natively by Rust (`mic_monitor_start`), not with
 * getUserMedia — WebKitGTK on Linux denies webview media access. Rust emits
 * `audio-level` events (0–1); the bars scroll that level history.
 */
export default function MicVisualiser() {
  const canvasRef = useRef<HTMLCanvasElement>(null)

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const c = canvas.getContext('2d')!
    const levels: number[] = new Array(BAR_COUNT).fill(0)
    let target = 0
    let rafId = 0

    const unsub = window.api.onAudioLevel((l) => { target = l })
    window.api.micMonitorStart().catch((err) => console.warn('[mic] monitor failed:', err))

    function draw() {
      rafId = requestAnimationFrame(draw)
      const next = levels[levels.length - 1] * 0.5 + target * 0.5
      levels.shift(); levels.push(next)

      const { width, height } = canvas!
      c.clearRect(0, 0, width, height)
      levels.forEach((pct, i) => {
        // Slight power curve so quiet sounds still move the bars noticeably
        const h = MIN_H + (MAX_H - MIN_H) * Math.pow(pct, 0.6)
        const x = i * (BAR_WIDTH + BAR_GAP)
        const y = (height - h) / 2  // centre bars vertically
        // Colour: accent purple, opacity driven by height
        const alpha = 0.35 + 0.65 * ((h - MIN_H) / (MAX_H - MIN_H))
        c.fillStyle = `rgba(108, 99, 255, ${alpha.toFixed(2)})`
        c.beginPath()
        c.roundRect(x, y, BAR_WIDTH, h, 2)
        c.fill()
      })
    }
    draw()

    return () => {
      cancelAnimationFrame(rafId)
      unsub()
      window.api.micMonitorStop()
    }
  }, [])

  const totalWidth = BAR_COUNT * (BAR_WIDTH + BAR_GAP) - BAR_GAP

  return (
    <canvas
      ref={canvasRef}
      width={totalWidth}
      height={MAX_H + 4}
      style={{ display: 'block' }}
      aria-hidden
    />
  )
}
