import { Link } from 'react-router-dom'
import { motion } from 'framer-motion'
import HoverAiLogo from '@/components/Logo'
import RemixIcon from '@/components/RemixIcon'
import backImg from '../../assets/images/back.webp'

const EASE: [number, number, number, number] = [0.22, 1, 0.36, 1]

const glassCard: React.CSSProperties = {
  background: [
    'linear-gradient(180deg, rgba(255,255,255,0.07) 0%, rgba(255,255,255,0.02) 100%) padding-box',
    'linear-gradient(135deg, rgba(255,255,255,0.22) 0%, rgba(255,255,255,0.06) 50%, rgba(255,255,255,0.01) 100%) border-box',
    'rgba(12,10,28,0.82) padding-box',
  ].join(', '),
  border: '1px solid transparent',
  backdropFilter: 'blur(20px) saturate(160%)',
  WebkitBackdropFilter: 'blur(20px) saturate(160%)',
  boxShadow: '0 8px 32px rgba(0,0,0,0.50), 0 0 0 0.5px rgba(255,255,255,0.08)',
}

const glassBtn: React.CSSProperties = {
  background: [
    'linear-gradient(135deg, rgba(255,255,255,0.18) 0%, rgba(255,255,255,0.05) 100%) padding-box',
    'linear-gradient(135deg, rgba(255,255,255,0.28) 0%, rgba(255,255,255,0.06) 100%) border-box',
    'rgba(255,255,255,0.05) padding-box',
  ].join(', '),
  border: '1px solid transparent',
  backdropFilter: 'blur(16px)',
  WebkitBackdropFilter: 'blur(16px)',
}

export default function DownloadPage() {
  return (
    <div
      className="min-h-screen flex flex-col"
      style={{
        background:
          'radial-gradient(ellipse 130% 100% at 95% 100%, #2a1b6e 0%, #15103a 35%, #0a0a0a 62%)',
      }}
    >
      {/* ── Right half background image (desktop only, fixed behind content) */}
      <div
        aria-hidden="true"
        className="hidden lg:block fixed top-0 right-0 w-[48%] h-full pointer-events-none"
        style={{
          backgroundImage: `url(${backImg})`,
          backgroundSize: 'cover',
          backgroundPosition: 'left center',
          zIndex: 0,
        }}
      />

      <div className="flex-1 flex flex-col lg:flex-row relative" style={{ zIndex: 1 }}>

        {/* ── Left column ──────────────────────────────────────────────── */}
        <div className="flex flex-col justify-center px-8 sm:px-12 lg:px-20 lg:w-[52%] gap-5 pt-10 pb-10 lg:pb-16">

          {/* Logo */}
          <div className="mb-4 lg:mb-8">
            <HoverAiLogo />
          </div>

          {/* Live badge */}
          <motion.div
            className="flex items-center gap-3 flex-wrap"
            initial={{ opacity: 0, y: 14 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.45, delay: 0.05, ease: EASE }}
          >
            <span
              className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-[13px] font-semibold"
              style={{ background: '#1a4d2e', color: '#4ade80' }}
            >
              <span
                className="w-1.5 h-1.5 rounded-full shrink-0"
                style={{ background: '#4ade80', boxShadow: '0 0 6px #4ade80' }}
              />
              Live
            </span>
            <span className="text-[13px] text-white/55">
              Hover AI Desktop Client v1 . 0 is now available for Windows &amp; macOS
            </span>
          </motion.div>

          {/* Headline */}
          <motion.h1
            className="font-extrabold leading-[1.08] tracking-tight text-white"
            style={{ fontSize: 'clamp(30px, 5vw, 58px)' }}
            initial={{ opacity: 0, y: 22 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.55, delay: 0.12, ease: EASE }}
          >
            Control your workspace<br />
            with voice commands and<br />
            real-time visual guidance.
          </motion.h1>

          {/* Body copy */}
          <motion.p
            className="text-[15px] text-white/50 leading-relaxed max-w-[520px]"
            initial={{ opacity: 0, y: 14 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.48, delay: 0.2, ease: EASE }}
          >
            Hover AI sits discreetly in your menu bar or system tray. Speak naturally in local
            languages or code-switched dialects, execute complex system tasks, and receive
            instant on-screen visual assistance without breaking your flow.
          </motion.p>

          {/* Download buttons */}
          <motion.div
            className="flex items-stretch gap-4 flex-wrap"
            initial={{ opacity: 0, y: 14 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.48, delay: 0.28, ease: EASE }}
          >
            <a
              href="https://github.com/wiseman-umanah/HoverAi/releases/download/v0.1.0/HoverAI_0.1.0_x64-setup.exe"
              className="inline-flex items-center gap-3 px-6 py-3.5 rounded-[18px] text-[14px] font-bold text-white transition-opacity hover:opacity-90"
              style={{ background: 'linear-gradient(135deg, #615fff 0%, #432dd7 100%)', minWidth: 185 }}
            >
              <i className="ri-windows-fill text-[20px] shrink-0" />
              <span className="leading-snug">
                Download for<br />
                <span className="text-[15px] font-extrabold">Windows (.exe)</span>
              </span>
            </a>

            <a
              href="https://github.com/wiseman-umanah/HoverAi/releases/download/v0.1.0/HoverAI_0.1.0_aarch64.dmg"
              className="inline-flex items-center gap-3 px-6 py-3.5 rounded-[18px] text-[14px] font-bold text-white transition-opacity hover:opacity-90"
              style={{
                background: [
                  'linear-gradient(135deg, rgba(255,255,255,0.09) 0%, rgba(255,255,255,0.03) 100%) padding-box',
                  'linear-gradient(135deg, rgba(255,255,255,0.22) 0%, rgba(255,255,255,0.06) 100%) border-box',
                  'rgba(8,6,20,0.55) padding-box',
                ].join(', '),
                border: '1px solid transparent',
                backdropFilter: 'blur(12px)',
                WebkitBackdropFilter: 'blur(12px)',
                minWidth: 185,
              }}
            >
              <i className="ri-apple-fill text-[20px] shrink-0" />
              <span className="leading-snug">
                Download for<br />
                <span className="text-[15px] font-extrabold">macOS (.dmg)</span>
              </span>
            </a>
          </motion.div>

          {/* Info card + back button — visible on mobile only, below the buttons */}
          <motion.div
            className="flex flex-col gap-4 mt-2 lg:hidden"
            initial={{ opacity: 0, y: 14 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.5, delay: 0.38, ease: EASE }}
          >
            <div className="rounded-2xl p-4 max-w-[420px]" style={glassCard}>
              <div className="flex items-center gap-2 mb-2 flex-wrap">
                <RemixIcon name="ri-translate-2" size={16} color="rgba(255,255,255,0.65)" />
                <span className="text-[12px] text-white/55">Code-switched query detected</span>
                <span
                  className="ml-auto text-[10px] font-semibold px-2 py-0.5 rounded-full whitespace-nowrap"
                  style={{ background: '#14532d', color: '#4ade80' }}
                >
                  Sahara API v2.5 · 98.4%
                </span>
              </div>
              <p className="text-[12px] text-white/65 leading-relaxed">
                Supports Windows 10/11 (64-bit) and macOS 12+
                (Universal Apple Silicon &amp; Intel) • Free 14-day trial included.
              </p>
            </div>

            <Link
              to="/"
              className="self-start inline-flex items-center gap-2 px-5 py-2.5 rounded-full text-[13px] font-semibold text-white/80 hover:text-white transition-colors"
              style={glassBtn}
            >
              Back To Landing Page
            </Link>
          </motion.div>
        </div>

        {/* ── Right — overlay cards on top of the background image ─────── */}
        <div className="hidden lg:flex relative flex-1 items-end justify-end pb-10 pr-10 gap-4 flex-col">

          {/* Info card */}
          <motion.div
            className="max-w-[300px] rounded-2xl p-4"
            style={glassCard}
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.5, delay: 0.45, ease: EASE }}
          >
            <div className="flex items-center gap-2 mb-2 flex-wrap">
              <RemixIcon name="ri-translate-2" size={16} color="rgba(255,255,255,0.65)" />
              <span className="text-[12px] text-white/55">Code-switched query detected</span>
              <span
                className="ml-auto text-[10px] font-semibold px-2 py-0.5 rounded-full whitespace-nowrap"
                style={{ background: '#14532d', color: '#4ade80' }}
              >
                Sahara API v2.5 · 98.4%
              </span>
            </div>
            <p className="text-[12px] text-white/65 leading-relaxed">
              Supports Windows 10/11 (64-bit) and macOS 12+
              (Universal Apple Silicon &amp; Intel) • Free 14-day trial included.
            </p>
          </motion.div>

          {/* Back to Landing Page */}
          <motion.div
            initial={{ opacity: 0, y: 14 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.45, delay: 0.6, ease: EASE }}
          >
            <Link
              to="/"
              className="inline-flex items-center gap-2 px-5 py-2.5 rounded-full text-[13px] font-semibold text-white/80 hover:text-white transition-colors"
              style={glassBtn}
            >
              Back To Landing Page
            </Link>
          </motion.div>
        </div>

      </div>
    </div>
  )
}
