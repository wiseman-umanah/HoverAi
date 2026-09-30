import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { motion, AnimatePresence } from 'framer-motion'
import LandingFooter from '@/components/LandingFooter'
import RemixIcon from '@/components/RemixIcon'
import FadeUp from '@/components/FadeUp'
import hero from '../../assets/images/hero.webp'
import { TOOLS } from '@/data/tools'
import { FEATURES, STEPS, PLANS, FAQS, LANGUAGES } from '@/data/landing'
import Header from '@/components/Header'
import { handleAnchorClick } from '@/hooks/useScrollTo'

/* ── Ease curve used throughout ─────────────────────────────────────── */
const EASE: [number, number, number, number] = [0.22, 1, 0.36, 1]

/* ── Count-up hook ──────────────────────────────────────────────────── */
function useCountUp(target: number, duration = 1800) {
  const [count, setCount] = useState(0)
  const ref = useRef<HTMLSpanElement>(null)

  useEffect(() => {
    const el = ref.current
    if (!el) return
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (!entry.isIntersecting) return
        observer.disconnect()
        const start = performance.now()
        const tick = (now: number) => {
          const progress = Math.min((now - start) / duration, 1)
          const ease = 1 - Math.pow(1 - progress, 3)
          setCount(Math.floor(ease * target))
          if (progress < 1) requestAnimationFrame(tick)
          else setCount(target)
        }
        requestAnimationFrame(tick)
      },
      { threshold: 0.5 },
    )
    observer.observe(el)
    return () => observer.disconnect()
  }, [target, duration])

  return { count, ref }
}

/* ════════════════════════════════════════════════════════════════════ */
export default function LandingPage() {
  const { count, ref } = useCountUp(4000)
  const midpoint = Math.ceil(TOOLS.length / 2)
  const firstHalf  = TOOLS.slice(0, midpoint)
  const secondHalf = TOOLS.slice(midpoint)

  /* FAQ open state */
  const [openFaq, setOpenFaq] = useState<number | null>(null)

  return (
    <div className="min-h-screen flex flex-col" style={{ background: '#0a0a0a' }}>

   <section id="home" aria-label="Hero">
		{/* header handler */}
		<div className=' h-16 sm:h-30 w-full'>
		<Header />
		</div>

		{/* hero and picture handler */}
		<div className="w-full max-w-[90%] mx-auto px-5 sm:px-8 pt-10 grid grid-cols-1 md:grid-cols-[1fr_1fr] gap-10 items-center">
				
			<div>
			{/* Social proof */}
			<motion.div
				className="flex items-center gap-2 mb-6 flex-wrap"
				initial={{ opacity: 0, y: 16 }}
				animate={{ opacity: 1, y: 0 }}
				transition={{ duration: 0.5, delay: 0.1, ease: EASE }}
			>
				<div className="flex -space-x-2 shrink-0">
					{[0, 1, 2].map(i => (
						<img
						key={i}
						src="/avatar.png"
						alt="Hover AI user"
						width={28}
						height={28}
						loading="lazy"
						decoding="async"
						className="w-7 h-7 rounded-full border-2 object-cover"
						style={{ borderColor: '#0a0a0a', zIndex: 3 - i }}
						/>
					))}
					</div>
				<span className="text-[15px] text-white/70">
				Trusted by <span ref={ref} className="text-white font-semibold">{count.toLocaleString()}+</span> creators building in Lagos, Nairobi &amp; Accra
				</span>
			</motion.div>

			{/* Headline */}
			<motion.h1
				className="font-extrabold leading-[1.06] tracking-tight text-white mb-5"
				style={{ fontSize: 'clamp(42px, 6vw, 80px)' }}
				initial={{ opacity: 0, y: 24 }}
				animate={{ opacity: 1, y: 0 }}
				transition={{ duration: 0.6, delay: 0.18, ease: EASE }}
			>
				Talk to your screen<br />
				the way you{' '}
				<span
				style={{
					backgroundImage: 'linear-gradient(90deg, #ffffff 0%, #7c86ff 35%, #24f992 70%)',
					WebkitBackgroundClip: 'text',
					WebkitTextFillColor: 'transparent',
					backgroundClip: 'text',
				}}
				>
				actually
				</span>
				<span
				style={{
					backgroundImage: 'linear-gradient(90.18deg, #FFFFFF 0.16%, #7C86FF 26.35%, #24F992 63.32%)',
					WebkitBackgroundClip: 'text',
					WebkitTextFillColor: 'transparent',
					backgroundClip: 'text',
				}}
				>
				{' '}talk{' '}
				</span>

				<svg
					viewBox="0 0 408 41"
					fill="none"
					xmlns="http://www.w3.org/2000/svg"
					style={{
						display: 'inline-block',
						width: 'clamp(200px, 30vw, 420px)',
						height: '0.8em',
						verticalAlign: 'middle',
						overflow: 'visible',
					}}
				>
					<defs>
						<linearGradient id="stroke-grad" x1="1.31406" y1="20.3755" x2="408" y2="20.3755" gradientUnits="userSpaceOnUse">
							<stop stopColor="#ffffff" />
							<stop offset="0.41" stopColor="#7c86ff" />
							<stop offset="1" stopColor="#24f992" />
						</linearGradient>
					</defs>
					<motion.path
						d="M1.31406 35.7516C45.9807 23.5849 142.714 0.551555 172.314 5.75155C201.914 10.9516 155.647 27.9182 128.814 35.7516C183.981 23.5849 316.914 0.551554 407.314 5.75155"
						stroke="url(#stroke-grad)"
						strokeWidth="8"
						strokeLinecap="round"
						fill="none"
						initial={{ pathLength: 0, opacity: 0 }}
						animate={{ pathLength: [0, 1, 1, 0], opacity: [0, 1, 1, 0] }}
						transition={{
							duration: 3,
							ease: 'easeInOut',
							repeat: Infinity,
							repeatDelay: 0.6,
							times: [0, 0.45, 0.7, 1],
						}}
					/>
				</svg>
			</motion.h1>

			{/* Sub */}
			<motion.p
				className="text-[17px] text-white/60 leading-relaxed mb-8 max-w-[560px]"
				initial={{ opacity: 0, y: 16 }}
				animate={{ opacity: 1, y: 0 }}
				transition={{ duration: 0.5, delay: 0.28, ease: EASE }}
			>
				Hover AI listens to code-switched voice commands, sees exactly what's on your screen, and draws a glowing beacon to the button you need — mid-sentence, mid-language, mid-task.
			</motion.p>

			{/* CTAs */}
			<motion.div
				className="flex items-center gap-3 flex-wrap"
				initial={{ opacity: 0, y: 16 }}
				animate={{ opacity: 1, y: 0 }}
				transition={{ duration: 0.5, delay: 0.38, ease: EASE }}
			>
				<Link
				to="/download"
				className="inline-flex items-center gap-2 px-7 py-3.5 rounded-full text-[16px] font-semibold text-white transition-opacity hover:opacity-90 whitespace-nowrap"
				style={{ background: 'linear-gradient(135deg, #615fff 0%, #432dd7 100%)' }}
				>
				Get Started Free
				<RemixIcon name="ri-arrow-right-line" size={18} color="#fff" />
				</Link>
				<a
				href="#how-it-works"
				onClick={handleAnchorClick}
				className="inline-flex items-center gap-2 px-6 py-3.5 rounded-full text-[16px] font-semibold text-white/80 hover:text-white transition-colors whitespace-nowrap"
				style={{
				  background: [
				    'linear-gradient(135deg, rgba(255,255,255,0.18) 0%, rgba(255,255,255,0.04) 50%, rgba(255,255,255,0.01) 100%) border-box',
				    'rgba(255,255,255,0.04) padding-box',
				  ].join(', '),
				  border: '1.5px solid transparent',
				}}
				>
				<RemixIcon name="ri-play-circle-line" size={16} color="currentColor" />
				See it work
				</a>
			</motion.div>
			</div>

			{/* Right — hero image (cinematic stagger) */}
			<div className="relative flex items-center justify-center">
				{/* Layer 0 — ambient glow behind the image, enters last */}
				<motion.div
					aria-hidden="true"
					className="absolute inset-0 pointer-events-none"
					initial={{ opacity: 0, scale: 0.7 }}
					animate={{ opacity: 1, scale: 1 }}
					transition={{ duration: 1.1, delay: 0.82, ease: EASE }}
					style={{
						background: 'radial-gradient(ellipse 70% 55% at 55% 50%, rgba(97,95,255,0.18) 0%, transparent 70%)',
					}}
				/>
				{/* Layer 1 — image frame slides up from slight depth */}
				<motion.div
					className="relative w-full"
					initial={{ opacity: 0, y: 40, scale: 0.94 }}
					animate={{ opacity: 1, y: 0, scale: 1 }}
					transition={{ duration: 0.72, delay: 0.32, ease: EASE }}
				>
					{/* Layer 2 — image itself fades in and sharpens up slightly after the frame */}
					<motion.img
						src={hero}
						alt="Hover AI interface showing code-switched voice guidance on a desktop screen"
						className="w-full object-contain md:max-h-[calc(100vh-220px)]"
						width={900}
						height={600}
						loading="eager"
						fetchPriority="high"
						decoding="async"
						initial={{ opacity: 0, scale: 1.04, filter: 'blur(6px)' }}
						animate={{ opacity: 1, scale: 1, filter: 'blur(0px)' }}
						transition={{ duration: 0.65, delay: 0.55, ease: EASE }}
					/>
				</motion.div>
			</div>
		</div>

		{/* tools marquee */}
		<div className="w-full overflow-hidden py-6">
			<MarqueeRow items={firstHalf} reverse={false} />
			<MarqueeRow items={secondHalf} reverse={true} />
		</div>
	  </section>

      <section id="features" className="w-full max-w-[90%] mx-auto px-5 sm:px-8 py-24">
        <FadeUp>
          <p className="text-[14px] font-bold tracking-widest uppercase mb-3" style={{ color: '#615fff' }}>
            Features
          </p>
          <h2 className="text-[clamp(32px,4.5vw,54px)] font-extrabold text-white leading-tight mb-4">
            Built for how creators<br />actually speak.
          </h2>
          <p className="text-[17px] text-white/50 max-w-[460px] leading-relaxed mb-12">
            Not another rigid voice command list. Hover AI understands intent, sees context, and shows — not just tells.
          </p>
        </FadeUp>

        {/* Row 1 — left narrow, right wide */}
        <div className="grid grid-cols-1 md:grid-cols-[3fr_1fr] gap-7 mb-7">
          {FEATURES.slice(0, 2).map((f, i) => (
            <FadeUp key={f.title} delay={i * 0.08}>
              <FeatureCard f={f} />
            </FadeUp>
          ))}
        </div>
        {/* Row 2 — left wide, right narrow */}
        <div className="grid grid-cols-1 md:grid-cols-[1fr_3fr] gap-7">
          {FEATURES.slice(2, 4).map((f, i) => (
            <FadeUp key={f.title} delay={(i + 2) * 0.08}>
              <FeatureCard f={f} />
            </FadeUp>
          ))}
        </div>
      </section>

      <section id="how-it-works" className="w-full max-w-[90%] mx-auto px-5 sm:px-8 py-24">
        <FadeUp>
          <p className="text-[14px] font-bold tracking-widest uppercase mb-3" style={{ color: '#615fff' }}>
            How it Works
          </p>
          <h2 className="text-[clamp(32px,4.5vw,54px)] font-extrabold text-white leading-tight mb-16">
            From "how do I…" to done, in<br />three beats.
          </h2>
        </FadeUp>

        <div className="grid grid-cols-1 md:grid-cols-3 gap-16">
          {STEPS.map((s, i) => (
            <FadeUp key={s.num} delay={i * 0.1}>
              <div>
                <div
                  className="w-16 h-16 rounded-full flex items-center justify-center mb-5 text-[20px] font-bold text-white"
                  style={{ background: 'linear-gradient(135deg, #615fff 0%, #432dd7 100%)' }}
                >
                  {s.num}
                </div>
                <h3 className="text-[19px] font-bold text-white mb-3">{s.title}</h3>
                <p className="text-[16px] text-white/50 leading-relaxed">{s.body}</p>
              </div>
            </FadeUp>
          ))}
        </div>
      </section>

      <section id="languages" className="w-full max-w-[90%] mx-auto px-5 sm:px-8 pb-24">
              <FadeUp>
                <div
                  className="relative rounded-3xl p-8 md:p-16 flex flex-col md:flex-row md:items-center justify-between gap-20 overflow-hidden"
                  style={{
                    background: 'var(--color-neutral-900)',
                    border: '1px solid var(--color-neutral-800)',
                  }}
                >
                  {/* Glow ellipse — top-right */}
                  <div
                    aria-hidden="true"
                    style={{
                      position: 'absolute',
                      top: '-152px',
                      right: '-100px',
                      width: '700px',
                      height: '700px',
                      borderRadius: '50%',
                      background: '#9999FF',
                      filter: 'blur(900px)',
                      opacity: 1,
                      pointerEvents: 'none',
                    }}
                  />
      
                  {/* Left */}
                  <div className="relative md:max-w-[90%]">
                    <p className="text-[14px] font-bold tracking-widest uppercase mb-3" style={{ color: '#615fff' }}>
                      Languages
                    </p>
                    <h2 className="text-[clamp(28px,4vw,46px)] font-extrabold text-white mb-4">
                      Built on real speech.
                    </h2>
                    <p className="text-[16px] text-white/50 leading-relaxed">
                      Six language profiles today, growing every quarter — trained on real code-switched speech, not textbook translations.
                    </p>
                  </div>
      
                  {/* Right — language pills */}
                  <div className="relative flex flex-wrap gap-6">
                    {LANGUAGES.map(l => (
                      <span
                        key={l.label}
                        className="inline-flex items-center gap-2 px-5 py-2.5 rounded-full text-[15px] font-medium text-white"
                        style={{
                          background: [
                            'linear-gradient(135deg, rgba(255,255,255,0.16) 0%, rgba(255,255,255,0.04) 50%, rgba(255,255,255,0.01) 100%) border-box',
                            'rgba(255,255,255,0.06) padding-box',
                          ].join(', '),
                          border: '1px solid transparent',
                        }}
                      >
                        <span role="img" aria-label={l.label}>{l.flag}</span>
                        {l.label}
                      </span>
                    ))}
                    <span
                      className="inline-flex items-center gap-1 px-5 py-2.5 rounded-full text-[15px] font-medium text-white/50"
                      style={{
                        background: [
                          'linear-gradient(135deg, rgba(255,255,255,0.10) 0%, rgba(255,255,255,0.02) 60%, rgba(255,255,255,0.005) 100%) border-box',
                          'transparent padding-box',
                        ].join(', '),
                        border: '1px solid transparent',
                      }}
                    >
                      <RemixIcon name="ri-add-line" size={14} color="currentColor" />
                      more coming
                    </span>
                  </div>
                </div>
              </FadeUp>
	</section>

      <section id="pricing" className="w-full max-w-[90%] mx-auto px-5 sm:px-8 py-24">
        <FadeUp className="text-center mb-12">
          <p className="text-[14px] font-bold tracking-widest uppercase mb-3" style={{ color: '#615fff' }}>
            Pricing
          </p>
          <h2 className="text-[clamp(32px,4.5vw,56px)] font-extrabold text-white leading-tight">
            Start free. Upgrade when it's<br />part of your workflow.
          </h2>
        </FadeUp>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-10 max-w-[800px] mx-auto">
          {PLANS.map((plan, i) => (
            <FadeUp key={plan.name} delay={i * 0.1}>
              <div
                className="rounded-4xl pt-16 pb-8 px-8 h-full flex flex-col relative"
                style={plan.popular ? {
                  /* Pro card — stays solid indigo, intentional visual dominance */
                  background: 'var(--color-indigo-950)',
                  border: '1px solid var(--color-indigo-400)',
                } : {
                  /* Free card — glass fill; edge highlight makes it distinct
                   * from the page without competing with the Pro card */
                  background: [
                    'linear-gradient(180deg, rgba(255,255,255,0.07) 0%, rgba(255,255,255,0.01) 100%) padding-box',
                    'linear-gradient(135deg, rgba(255,255,255,0.20) 0%, rgba(255,255,255,0.06) 35%, rgba(255,255,255,0.01) 70%, rgba(255,255,255,0.005) 100%) border-box',
                    'rgba(255,255,255,0.03) padding-box',
                  ].join(', '),
                  border: '1px solid transparent',
                  boxShadow: '0 4px 24px rgba(0,0,0,0.30), 0 1px 4px rgba(0,0,0,0.20)',
                }}
              >
				<div className='flex justify-between items-start'>
					<div>
						<p className="text-[19px] font-bold text-white mb-1">{plan.name}</p>
						<p className="text-[15px] text-white/50 mb-6">{plan.sub}</p>
					</div>

					{plan.popular && (
						<span
							className="text-[13px] font-bold px-3 py-1 rounded-full text-white"
							style={{ background: 'var(--color-indigo-400)' }}
						>
							Most Popular
						</span>
					)}
				</div>

				            {/* Price */}
				            <div className="flex items-baseline gap-1 mb-7">
				              <span className="text-[52px] font-extrabold text-white leading-none">{plan.price}</span>
				              <span className="text-[16px] text-white/50">{plan.period}</span>
				            </div>

				            {/* CTA */}
				            <Link
				              to="/download"
				              className="w-full py-3.5 rounded-full text-[16px] font-semibold mb-7 transition-opacity hover:opacity-90 text-center block"
				               style={plan.ctaStyle as React.CSSProperties}
				             >
				               {plan.cta}
				             </Link>

                {/* Features */}
                <ul className="flex flex-col gap-3 mt-auto">
                  {plan.features.map(f => (
                    <li key={f} className="flex items-center gap-2 text-[15px] text-white/70">
                      <RemixIcon name="ri-check-line" size={17} color="#00A63E" />
                      {f}
                    </li>
                  ))}
                </ul>
              </div>
            </FadeUp>
          ))}
        </div>
      </section>

      <section id="faq" className="w-full max-w-[90%] lg:max-w-[50%] mx-auto px-5 sm:px-8 py-24">
        <FadeUp>
          <p className="text-[14px] font-bold tracking-widest uppercase mb-3" style={{ color: '#615fff' }}>
            FAQ
          </p>
          <h2 className="text-[clamp(32px,4.5vw,54px)] font-extrabold text-white mb-10">
            Good to know.
          </h2>
        </FadeUp>

        <div className="flex flex-col gap-3">
          {FAQS.map((item, i) => (
            <FadeUp key={item.q} delay={i * 0.06}>
              <div
                className="rounded-2xl overflow-hidden"
                style={{
                  /* Glass edge only — no backdrop-filter, rows sit on the page.
                   * Edge highlight separates rows from the near-black background. */
                  background: [
                    'linear-gradient(180deg, rgba(255,255,255,0.05) 0%, rgba(255,255,255,0.01) 100%) padding-box',
                    'linear-gradient(135deg, rgba(255,255,255,0.16) 0%, rgba(255,255,255,0.05) 35%, rgba(255,255,255,0.01) 75%, rgba(255,255,255,0.004) 100%) border-box',
                    'rgba(20,18,30,0.70) padding-box',
                  ].join(', '),
                  border: '1px solid transparent',
                  boxShadow: '0 2px 12px rgba(0,0,0,0.25)',
                }}
              >
                <button
                  className="w-full flex items-center justify-between p-6 text-left"
                  onClick={() => setOpenFaq(openFaq === i ? null : i)}
                >
                  <span className="text-[16px] font-medium text-white/80">{item.q}</span>
                  <span className="ml-4 shrink-0 transition-transform duration-200" style={{ transform: openFaq === i ? 'rotate(45deg)' : 'rotate(0deg)' }}>
                    <RemixIcon name="ri-add-fill" size={18} color="rgba(255,255,255,0.45)" />
                  </span>
                </button>
                <AnimatePresence initial={false}>
                  {openFaq === i && (
                    <motion.div
                      initial={{ height: 0, opacity: 0 }}
                      animate={{ height: 'auto', opacity: 1 }}
                      exit={{ height: 0, opacity: 0 }}
                      transition={{ duration: 0.28, ease: EASE }}
                      style={{ overflow: 'hidden' }}
                    >
                      <p className="px-6 pb-6 text-[15px] text-white/50 leading-relaxed">
                        {item.a}
                      </p>
                    </motion.div>
                  )}
                </AnimatePresence>
              </div>
            </FadeUp>
          ))}
        </div>
      </section>

      <section className="w-full max-w-[90%] mx-auto px-5 sm:px-8 pb-24">
        <FadeUp>
          <div
            className="rounded-[48px] px-8 py-20 flex flex-col items-center text-center"
            style={{
              background: 'linear-gradient(135deg, #6366f1 0%, #22c55e 100%)',
            }}
          >
            <h2 className="text-[clamp(28px,4.5vw,52px)] font-extrabold text-white leading-tight mb-4 max-w-[560px]">
              Your screen is about to start listening properly.
            </h2>
            <p className="text-[17px] text-white/80 mb-8">
              Free to start. No credit card. Set up your shortcut in under a minute.
            </p>
            <Link
              to="/download"
              className="inline-flex items-center gap-2 px-8 py-3.5 rounded-full text-[16px] font-semibold text-indigo-700 bg-white hover:bg-white/90 transition-colors whitespace-nowrap"
            >
              Get Started Free
              <RemixIcon name="ri-arrow-right-line" size={17} color="currentColor" />
            </Link>
          </div>
        </FadeUp>
      </section>

      {/* ── Footer ────────────────────────────────────────────────── */}
      <LandingFooter />
    </div>
  )
}

/* ── Marquee row helper ─────────────────────────────────────────────── */

/* ── Feature card ───────────────────────────────────────────────────── */
function FeatureCard({ f }: { f: typeof FEATURES[number] }) {
  return (
    <div
      className="rounded-2xl p-8 h-full flex flex-col"
      style={{
        /* Glass fill — no backdrop-filter (cards aren't floating layers),
         * edge highlight + tinted fill gives depth on the dark page */
        background: [
          'linear-gradient(180deg, rgba(255,255,255,0.06) 0%, rgba(255,255,255,0.01) 100%) padding-box',
          'linear-gradient(135deg, rgba(255,255,255,0.18) 0%, rgba(255,255,255,0.06) 30%, rgba(255,255,255,0.01) 70%, rgba(255,255,255,0.005) 100%) border-box',
          `${f.accent ? 'rgba(18,16,44,0.80)' : 'rgba(255,255,255,0.03)'} padding-box`,
        ].join(', '),
        border: '1px solid transparent',
        boxShadow: '0 4px 24px rgba(0,0,0,0.35), 0 1px 4px rgba(0,0,0,0.25)',
        minHeight: 260,
      }}
    >
      {/* Icon chip */}
      <div
        className="w-12 h-12 rounded-xl flex items-center justify-center mb-auto"
        style={{ background: f.iconBg }}
      >
        <RemixIcon name={f.icon} color={f.iconColor} />
      </div>
      <h3 className="text-[18px] font-bold text-white mb-2">{f.title}</h3>
      <p className="text-[15px] leading-relaxed" style={{ color: 'rgba(255,255,255,0.45)' }}>{f.body}</p>
    </div>
  )
}


function MarqueeRow({ items, reverse }: { items: typeof TOOLS; reverse: boolean }) {
  const repeated = [...items, ...items, ...items, ...items]
  return (
    <div
      className="relative overflow-hidden py-3 sm:py-5"
      style={{
        maskImage: 'linear-gradient(to right, transparent 0%, black 8%, black 92%, transparent 100%)',
        WebkitMaskImage: 'linear-gradient(to right, transparent 0%, black 8%, black 92%, transparent 100%)',
      }}
    >
      <div className={`${reverse ? 'animate-marquee-reverse' : 'animate-marquee'} flex gap-7 sm:gap-12`}>
        {repeated.map((tool, i) =>
          tool.logo ? (
            <div
              key={`${tool.name}-${i}`}
              className="flex items-center gap-2 sm:gap-3 shrink-0 opacity-50 hover:opacity-100 transition-opacity"
            >
              <img src={tool.logo} alt={tool.name} className="h-5 sm:h-11 w-auto object-contain" loading="lazy" decoding="async" />
              <span className="text-[13px] sm:text-[16px] text-white/70 whitespace-nowrap">{tool.name}</span>
            </div>
          ) : null,
        )}
      </div>
    </div>
  )
}
