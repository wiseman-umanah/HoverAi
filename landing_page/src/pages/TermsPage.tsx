import { Link } from 'react-router-dom'
import { motion } from 'framer-motion'
import HoverAiLogo from '@/components/Logo'

const EASE: [number, number, number, number] = [0.22, 1, 0.36, 1]
const LAST_UPDATED = 'June 2025'

const SECTIONS = [
  {
    title: '1. Acceptance of Terms',
    body: [
      'By downloading, installing, or using Hover AI ("the App"), you agree to be bound by these Terms of Use ("Terms"). If you do not agree, do not use the App.',
      'These Terms apply to all versions of Hover AI, including the desktop client and any future web or mobile interfaces.',
    ],
  },
  {
    title: '2. Eligibility',
    body: [
      'You must be at least 13 years of age to use Hover AI. By using the App, you represent that you meet this requirement.',
      'If you are using Hover AI on behalf of an organisation, you represent that you have authority to bind that organisation to these Terms.',
    ],
  },
  {
    title: '3. Licence Grant',
    body: [
      'Subject to your compliance with these Terms, MacedonLabs grants you a limited, non-exclusive, non-transferable, revocable licence to install and use the App on devices you own or control, for your personal or internal business purposes.',
      'You may not sublicense, sell, resell, transfer, assign, or otherwise exploit the App for commercial purposes without our prior written consent.',
    ],
  },
  {
    title: '4. Permitted & Prohibited Use',
    body: [
      'Permitted: using Hover AI to execute voice commands, navigate desktop applications, and receive on-screen guidance for lawful tasks.',
      'Prohibited: reverse-engineering, decompiling, or disassembling the App; using the App to infringe any intellectual property rights; circumventing rate limits or security measures; using the App to capture or transmit another person\'s screen or voice without their consent.',
      'Prohibited: using the App in any way that violates applicable law, including privacy regulations.',
    ],
  },
  {
    title: '5. Subscriptions & Payments',
    body: [
      'Hover AI offers a free Personal tier and a paid Creator Pro tier. Pricing and feature details are described on the Pricing page.',
      'Paid subscriptions are billed monthly or annually in advance via Stripe. All fees are non-refundable except where required by applicable law.',
      'We reserve the right to change pricing with 30 days\' notice. Continued use after the notice period constitutes acceptance of the new pricing.',
      'You may cancel your subscription at any time. Cancellation takes effect at the end of the current billing period; you retain access until then.',
    ],
  },
  {
    title: '6. Intellectual Property',
    body: [
      'The App, including its code, design, branding, and all associated content, is the exclusive property of MacedonLabs and is protected by copyright, trademark, and other intellectual property laws.',
      'Nothing in these Terms transfers ownership of any intellectual property to you.',
      'You retain all rights to content you create using Hover AI. You grant MacedonLabs a limited licence to process your voice and screen data solely to provide the service as described in our Privacy Policy.',
    ],
  },
  {
    title: '7. Disclaimer of Warranties',
    body: [
      'Hover AI is provided "as is" and "as available" without warranties of any kind, express or implied, including but not limited to merchantability, fitness for a particular purpose, and non-infringement.',
      'We do not warrant that the App will be error-free, uninterrupted, or free of viruses or other harmful components.',
      'Voice recognition and on-screen guidance are probabilistic — accuracy is high but not guaranteed. Always verify critical actions before confirming them.',
    ],
  },
  {
    title: '8. Limitation of Liability',
    body: [
      'To the fullest extent permitted by law, MacedonLabs shall not be liable for any indirect, incidental, special, consequential, or punitive damages arising out of or related to your use of Hover AI.',
      'Our total liability to you for any claim arising from these Terms or the App shall not exceed the amount you paid us in the three months preceding the claim.',
    ],
  },
  {
    title: '9. Termination',
    body: [
      'We may suspend or terminate your access to Hover AI at any time, with or without cause, with reasonable notice.',
      'You may terminate your account at any time by deleting the App and contacting us at support@hoverai.app.',
      'Upon termination, your licence ends immediately and you must cease all use of the App.',
    ],
  },
  {
    title: '10. Governing Law',
    body: [
      'These Terms are governed by the laws of the Federal Republic of Nigeria, without regard to conflict-of-law principles.',
      'Any disputes shall be resolved by binding arbitration in Lagos, Nigeria, except that either party may seek injunctive relief in a court of competent jurisdiction.',
    ],
  },
  {
    title: '11. Changes to These Terms',
    body: [
      'We may revise these Terms at any time. When we do, we will update the "Last updated" date and, for material changes, notify you via email or in-app notice at least 14 days in advance.',
      'Continued use of Hover AI after the effective date of the revised Terms constitutes acceptance.',
    ],
  },
  {
    title: '12. Contact',
    body: [
      'Questions about these Terms? Reach us at legal@hoverai.app or write to MacedonLabs, Lagos, Nigeria.',
    ],
  },
]

export default function TermsPage() {
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
            Terms of Use
          </h1>
          <p className="text-[14px] mb-12" style={{ color: 'rgba(255,255,255,0.35)' }}>
            Last updated: {LAST_UPDATED}
          </p>

          <p className="text-[15px] leading-relaxed mb-12" style={{ color: 'rgba(255,255,255,0.60)' }}>
            These Terms of Use govern your access to and use of Hover AI, a voice-driven desktop
            assistant built by MacedonLabs. Please read them carefully before using the App.
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
              <a href="mailto:legal@hoverai.app" className="text-white/70 hover:text-white transition-colors underline underline-offset-2">
                legal@hoverai.app
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
