import { useState, useEffect } from 'react'
import { Link } from 'react-router-dom'
import { motion, AnimatePresence } from 'framer-motion'
import HoverAiLogo from './Logo'
import RemixIcon from './RemixIcon'
import { handleAnchorClick } from '@/hooks/useScrollTo'

const NAV_LINKS = [
  { label: 'Features',     href: '#features' },
  { label: 'How it Works', href: '#how-it-works' },
  { label: 'Language',     href: '#languages' },
  { label: 'Pricing',      href: '#pricing' },
  { label: 'FAQ',          href: '#faq' },
]

const SPACER_H = 120
const DOCKED_TOP = 16
const REST_TOP = Math.round((SPACER_H - 52) / 2)

export default function Header() {
  const [open, setOpen]       = useState(false)
  const [scrolled, setScrolled] = useState(false)
  const [isMobile, setIsMobile] = useState(() => window.innerWidth < 1024)

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 10)
    const onResize = () => setIsMobile(window.innerWidth < 1024)
    window.addEventListener('scroll', onScroll, { passive: true })
    window.addEventListener('resize', onResize, { passive: true })
    return () => {
      window.removeEventListener('scroll', onScroll)
      window.removeEventListener('resize', onResize)
    }
  }, [])

  return (
    <header
      className="fixed left-0 right-0 z-50 flex items-center justify-center transition-[top,padding] duration-300 ease-out"
      style={{
        top:          isMobile ? 0 : (scrolled ? DOCKED_TOP : REST_TOP),
        paddingLeft:  (!isMobile && scrolled) ? 16 : 0,
        paddingRight: (!isMobile && scrolled) ? 16 : 0,
      }}
    >
      {/* Pill wrapper */}
      <div
        className='flex items-center justify-between w-full lg:max-w-[90%] p-4 sm:p-5 md:px-6 transition-[border-radius] duration-300 lg:rounded-full'
        style={{
          /* ── Glass material ─────────────────────────────────────────────
           *  Layer A (padding-box): specular sheen — top of pill catches
           *                         overhead ambient light
           *  Layer B (border-box):  directional edge highlight — top-left
           *                         bright, bottom-right dim (light source
           *                         convention: top-left)
           *  Layer C (padding-box): deep indigo tinted fill — real
           *                         transparency + brand identity
           * ──────────────────────────────────────────────────────────────*/
          background: [
            'linear-gradient(180deg, rgba(255,255,255,0.08) 0%, rgba(255,255,255,0.01) 100%) padding-box',
            'linear-gradient(135deg, rgba(255,255,255,0.30) 0%, rgba(255,255,255,0.08) 30%, rgba(255,255,255,0.02) 65%, rgba(255,255,255,0.01) 100%) border-box',
            'rgba(14,10,36,0.52) padding-box',
          ].join(', '),
          border: '1px solid transparent',
          /* ── Backdrop: blur + boost brightness so frost reads on dark bg */
          backdropFilter: 'blur(20px) saturate(160%) brightness(1.08)',
          WebkitBackdropFilter: 'blur(20px) saturate(160%) brightness(1.08)',
          /* ── Shadow: ambient depth + contact + outer rim ────────────── */
          boxShadow: [
            '0 8px 32px rgba(0,0,0,0.50)',
            '0 2px 8px rgba(0,0,0,0.35)',
            '0 0 0 0.5px rgba(255,255,255,0.10)',
          ].join(', '),
        }}
      >
        {/* Logo */}
        <HoverAiLogo />

        {/* Desktop nav links */}
        <nav className="hidden lg:flex items-center gap-10">
          {NAV_LINKS.map(l => (
            <a
              key={l.label}
              href={l.href}
              className="text-[15px] text-white/70 hover:text-white transition-colors"
              onClick={handleAnchorClick}
            >
              {l.label}
            </a>
          ))}
        </nav>

        {/* Right actions */}
        <div className="flex items-center gap-6">
          <Link
            to="/download"
            className="text-[11px] sm:text-[15px] font-semibold px-3 sm:px-6 py-1.5 sm:py-2.5 rounded-full text-white transition-opacity hover:opacity-90 whitespace-nowrap"
            style={{ background: 'linear-gradient(135deg, #615fff 0%, #432dd7 100%)' }}
          >
            <span className="sm:hidden">Get Started</span>
            <span className="hidden sm:inline">Get Started Free</span>
          </Link>

          {/* Mobile hamburger / close */}
          <button
            className="lg:hidden flex flex-col gap-1.25 ml-2 relative w-5 h-5 items-center justify-center"
            onClick={() => setOpen(o => !o)}
            aria-label={open ? 'Close menu' : 'Open menu'}
          >
            <motion.span
              className="absolute w-5 h-0.5 bg-white rounded origin-center"
              animate={{ rotate: open ? 45 : 0, y: open ? 0 : -4 }}
              transition={{ duration: 0.22 }}
            />
            <motion.span
              className="absolute w-5 h-0.5 bg-white rounded"
              animate={{ opacity: open ? 0 : 1, scaleX: open ? 0 : 1 }}
              transition={{ duration: 0.18 }}
            />
            <motion.span
              className="absolute w-5 h-0.5 bg-white rounded origin-center"
              animate={{ rotate: open ? -45 : 0, y: open ? 0 : 4 }}
              transition={{ duration: 0.22 }}
            />
          </button>
        </div>
      </div>

      {/* Mobile fullscreen overlay */}
      <AnimatePresence>
        {open && (
          <motion.div
            className="fixed inset-0 z-40 flex flex-col md:hidden"
            style={{ background: 'rgba(8,8,20,0.97)', backdropFilter: 'blur(20px)', WebkitBackdropFilter: 'blur(20px)' }}
            initial={{ opacity: 0, y: -24 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -16 }}
            transition={{ duration: 0.3, ease: [0.22, 1, 0.36, 1] }}
          >
            {/* Top bar mirrors the header */}
            <div className="flex items-center justify-between px-5 py-4">
              <HoverAiLogo />
              <button
                onClick={() => setOpen(false)}
                className="flex items-center justify-center w-9 h-9 rounded-full"
                style={{ background: 'rgba(255,255,255,0.08)' }}
                aria-label="Close menu"
              >
                <RemixIcon name="ri-close-line" size={20} color="#fff" />
              </button>
            </div>

            {/* Nav links */}
            <nav className="flex flex-col px-6 pt-8 gap-1 flex-1">
              {NAV_LINKS.map((l, i) => (
                <motion.a
                  key={l.label}
                  href={l.href}
                  className="text-[22px] font-semibold text-white/80 hover:text-white py-3 border-b border-white/[0.07] transition-colors"
                  onClick={e => { handleAnchorClick(e); setOpen(false) }}
                  initial={{ opacity: 0, x: -16 }}
                  animate={{ opacity: 1, x: 0 }}
                  transition={{ delay: 0.08 + i * 0.05, duration: 0.28, ease: [0.22, 1, 0.36, 1] }}
                >
                  {l.label}
                </motion.a>
              ))}
            </nav>

            {/* Bottom CTA */}
            <motion.div
              className="px-6 pb-12 pt-6 flex flex-col gap-3"
              initial={{ opacity: 0, y: 16 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: 0.35, duration: 0.3, ease: [0.22, 1, 0.36, 1] }}
            >
              <Link
                to="/download"
                className="w-full flex items-center justify-center gap-2 py-4 rounded-full text-[16px] font-semibold text-white"
                style={{ background: 'linear-gradient(135deg, #615fff 0%, #432dd7 100%)' }}
                onClick={() => setOpen(false)}
              >
                Get Started Free
                <RemixIcon name="ri-arrow-right-line" size={18} color="#fff" />
              </Link>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>
    </header>
  )
}
