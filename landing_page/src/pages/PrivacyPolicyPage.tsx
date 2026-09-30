import { Link } from 'react-router-dom'
import { motion } from 'framer-motion'
import HoverAiLogo from '@/components/Logo'

const EASE: [number, number, number, number] = [0.22, 1, 0.36, 1]
const LAST_UPDATED = 'June 2025'

const SECTIONS = [
  {
    title: '1. Information We Collect',
    body: [
      'Voice audio: captured only during an active session triggered by your keyboard shortcut. Audio is processed in real time to interpret your command and is not stored on our servers after the session ends.',
      'Screen context: a screenshot of your active window is taken at the moment of your command. It is sent to our inference API solely to identify the relevant on-screen element. Screenshots are discarded immediately after the response is returned — they are never written to disk or logged.',
      'Usage metadata: anonymised event counts (e.g. number of commands per day) to help us improve reliability. No personally identifiable information is included.',
      'Account data: your email address and chosen language profiles, required to manage your subscription.',
    ],
  },
  {
    title: '2. How We Use Your Information',
    body: [
      'To execute the voice command you issued in the moment.',
      'To identify the correct on-screen target and render the guidance beacon.',
      'To maintain your account and subscription.',
      'To send transactional emails (receipts, password resets). We do not send marketing emails without your explicit opt-in.',
      'To monitor aggregate service health — never to build personal profiles.',
    ],
  },
  {
    title: '3. Data Retention',
    body: [
      'Voice audio and screenshots are ephemeral — they exist only for the duration of a single command cycle (typically under two seconds) and are permanently discarded thereafter.',
      'Usage metadata is retained in aggregated, non-identifiable form for up to 12 months.',
      'Account data is retained for as long as your account is active. You may request deletion at any time by contacting us at privacy@hoverai.app.',
    ],
  },
  {
    title: '4. Third-Party Services',
    body: [
      'Hover AI uses the Sahara API for on-device AI inference. Sahara processes voice and screen data under a data processing agreement that prohibits retention and secondary use.',
      'Payments are handled by Stripe. We do not store card details.',
      'We do not sell, rent, or trade your data with advertisers or data brokers, ever.',
    ],
  },
  {
    title: '5. Security',
    body: [
      'All data in transit is encrypted with TLS 1.2 or higher.',
      'Access to production systems is restricted to authorised personnel and protected by multi-factor authentication.',
      'We conduct regular security reviews and promptly address identified vulnerabilities.',
    ],
  },
  {
    title: '6. Your Rights',
    body: [
      'Access: request a copy of the personal data we hold about you.',
      'Correction: ask us to fix inaccurate account information.',
      'Deletion: request permanent removal of your account and all associated data.',
      'Portability: receive your account data in a machine-readable format.',
      'To exercise any of these rights, email privacy@hoverai.app. We will respond within 30 days.',
    ],
  },
  {
    title: '7. Children\'s Privacy',
    body: [
      'Hover AI is not directed at children under the age of 13. We do not knowingly collect personal information from children. If you believe a child has provided us with personal data, please contact us immediately.',
    ],
  },
  {
    title: '8. Changes to This Policy',
    body: [
      'We may update this Privacy Policy from time to time. When we do, we will revise the "Last updated" date at the top of this page and, for material changes, notify you via email or an in-app notice.',
    ],
  },
  {
    title: '9. Contact',
    body: [
      'Hover AI is operated by MacedonLabs. For privacy-related questions or requests, reach us at privacy@hoverai.app.',
    ],
  },
]

export default function PrivacyPolicyPage() {
  return (
    <div className="min-h-screen flex flex-col" style={{ background: '#0a0a0a' }}>

      {/* Header */}
      <header className="px-8 sm:px-12 py-6 flex items-center justify-between border-b" style={{ borderColor: 'rgba(255,255,255,0.07)' }}>
        <Link to="/"><HoverAiLogo /></Link>
        <Link
          to="/"
          className="text-[13px] text-white/50 hover:text-white transition-colors"
        >
          ← Back to Home
        </Link>
      </header>

      {/* Content */}
      <main className="flex-1 w-full max-w-[740px] mx-auto px-6 sm:px-10 py-16">
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.5, ease: EASE }}
        >
          <p className="text-[13px] font-bold tracking-widest uppercase mb-3" style={{ color: '#615fff' }}>
            Legal
          </p>
          <h1 className="text-[clamp(32px,5vw,52px)] font-extrabold text-white leading-tight mb-3">
            Privacy Policy
          </h1>
          <p className="text-[14px] mb-12" style={{ color: 'rgba(255,255,255,0.35)' }}>
            Last updated: {LAST_UPDATED}
          </p>

          <p className="text-[15px] leading-relaxed mb-12" style={{ color: 'rgba(255,255,255,0.60)' }}>
            Hover AI is built on a simple principle: your voice and your screen are yours. We collect
            only what we need to run the product, process it in the moment, and discard it. This
            policy explains exactly what that means in practice.
          </p>

          <div className="flex flex-col gap-10">
            {SECTIONS.map((section, i) => (
              <motion.section
                key={section.title}
                initial={{ opacity: 0, y: 16 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.4, delay: 0.08 + i * 0.04, ease: EASE }}
              >
                <h2 className="text-[18px] font-bold text-white mb-4">{section.title}</h2>
                <ul className="flex flex-col gap-3">
                  {section.body.map((line, j) => (
                    <li key={j} className="flex gap-3 text-[15px] leading-relaxed" style={{ color: 'rgba(255,255,255,0.58)' }}>
                      <span className="mt-[6px] w-1.5 h-1.5 rounded-full shrink-0" style={{ background: '#615fff' }} />
                      {line}
                    </li>
                  ))}
                </ul>
              </motion.section>
            ))}
          </div>

          <div className="mt-16 pt-8" style={{ borderTop: '1px solid rgba(255,255,255,0.08)' }}>
            <p className="text-[14px]" style={{ color: 'rgba(255,255,255,0.35)' }}>
              Questions? Email us at{' '}
              <a href="mailto:privacy@hoverai.app" className="text-white/70 hover:text-white transition-colors underline underline-offset-2">
                privacy@hoverai.app
              </a>
            </p>
          </div>
        </motion.div>
      </main>

      {/* Footer strip */}
      <div className="px-8 sm:px-12 py-5 flex items-center justify-between flex-wrap gap-3" style={{ borderTop: '1px solid rgba(255,255,255,0.07)' }}>
        <p className="text-[13px]" style={{ color: 'rgba(255,255,255,0.35)' }}>
          &copy; {new Date().getFullYear()} Hover AI. All rights reserved.
        </p>
        <div className="flex items-center gap-5">
          <Link to="/privacy-policy" className="text-[13px] text-white/50 hover:text-white transition-colors">Privacy Policy</Link>
          <Link to="/terms" className="text-[13px] text-white/50 hover:text-white transition-colors">Terms of Use</Link>
        </div>
      </div>
    </div>
  )
}
