import { Link } from 'react-router-dom'
import { motion } from 'framer-motion'

const BRAND = '#615fff'
const FOOTER_BG = '#3730a3'
const EASE: [number, number, number, number] = [0.22, 1, 0.36, 1]

/* ── HoverAi wordmark paths (viewBox 0 0 87 23) ── */
const WORDMARK_PATHS = [
  /* H */ 'M11.9308 14.0531H8.85246V8.43183H3.07829V14.0531H0V0.439758H3.07829V5.7933H8.85246V0.439758H11.9308V14.0531Z',
  /* o */ 'M18.5743 14.2443C15.2666 14.2443 13.4119 12.1984 13.4119 9.12014C13.4119 6.04186 15.2666 3.99604 18.5743 3.99604C21.882 3.99604 23.7366 6.04186 23.7366 9.12014C23.7366 12.1984 21.882 14.2443 18.5743 14.2443ZM18.5743 12.0455C20.0083 12.0455 20.8113 11.1086 20.8113 9.12014C20.8113 7.13169 20.0083 6.19482 18.5743 6.19482C17.1403 6.19482 16.3373 7.13169 16.3373 9.12014C16.3373 11.1086 17.1403 12.0455 18.5743 12.0455Z',
  /* v */ 'M30.7845 14.0531H27.2282L23.6911 4.18724H26.7311L29.0064 11.5292L31.2816 4.18724H34.3217L30.7845 14.0531Z',
  /* e */ 'M39.4492 14.2443C36.1415 14.2443 34.2869 12.1984 34.2869 9.12014C34.2869 6.0801 36.0841 3.99604 39.3345 3.99604C42.5849 3.99604 44.3821 6.09922 44.3821 9.04367C44.3821 9.31134 44.363 9.52166 44.3248 9.77022H37.2122C37.2696 11.4336 38.0917 12.294 39.4492 12.294C40.4817 12.294 41.2082 11.7587 41.4759 10.8792H44.1718C43.7512 12.925 42.2025 14.2443 39.4492 14.2443ZM39.3345 5.94626C38.1491 5.94626 37.4225 6.63457 37.2122 7.89648H41.4568C41.2465 6.63457 40.5199 5.94626 39.3345 5.94626Z',
  /* r */ 'M48.6109 14.0531H45.6855V4.18724H48.4579V6.30953C48.955 4.58875 49.8154 4.0534 51.1155 4.0534C51.3832 4.0534 51.8421 4.11076 52.0333 4.18724V6.73017L51.0008 6.71105C49.5477 6.71105 48.6109 7.74352 48.6109 9.54078V14.0531Z',
  /* A */ 'M66.1912 14.0531H63.0365L62.1569 11.5292H56.5357L55.6562 14.0531H52.5014L57.4535 0.439758H61.2392L66.1912 14.0531ZM61.4113 9.1775L59.3463 3.19301L57.2814 9.1775H61.4113Z',
  /* i */ 'M70.0273 14.0531H67.1019V4.18724H70.0273V14.0531ZM68.5551 2.94446C67.5035 2.94446 66.9108 2.3135 66.9108 1.47223C66.9108 0.63096 67.5035 7.62939e-06 68.5551 7.62939e-06C69.6258 7.62939e-06 70.2185 0.63096 70.2185 1.47223C70.2185 2.3135 69.6258 2.94446 68.5551 2.94446Z',
]

export default function LandingFooter() {
  return (
    <footer style={{ background: FOOTER_BG, overflow: 'hidden' }}>

      <div
        aria-hidden="true"
        className="relative w-full select-none"
        style={{
          height: 'clamp(80px, 27vw, 420px)',
          overflow: 'visible',
        }}
      >
        {/* Logo mark — rotated, left-anchored, bleeds above */}
        <div
          style={{
            position: 'absolute',
            top:    'clamp(-20px, calc(-72 / 393 * 27vw), -46px)',
            left:   '0.33%',
            width:  '42.2%',   /* 603/1429 */
            transform: 'rotate(-4.58deg)',
            transformOrigin: 'top left',
          }}
        >
          <svg
            viewBox="0 0 57 32"
            fill="none"
            xmlns="http://www.w3.org/2000/svg"
            style={{ width: '100%', height: 'auto', display: 'block' }}
          >
            <path
              d="M51.2865 8.93283C53.184 10.1486 53.2719 12.2467 51.4827 13.6191C49.3816 15.2308 49.4848 17.6947 51.7131 19.1223L54.8222 21.1143C56.7198 22.33 56.8076 24.4281 55.0184 25.8006L51.1841 28.7418C49.3949 30.1142 46.4062 30.2413 44.5086 29.0256L31.8688 20.9275C29.9712 19.7118 29.8834 17.6137 31.6726 16.2412C33.7737 14.6295 33.6705 12.1657 31.4421 10.738L28.333 8.74607C26.4355 7.53034 26.3476 5.43221 28.1368 4.05976L31.9711 1.11856C33.7603 -0.253887 36.7491 -0.380937 38.6466 0.834791L51.2865 8.93283Z"
              fill={BRAND}
            />
            <path
              d="M24.4362 10.882C26.3337 12.0978 26.4216 14.1959 24.6324 15.5684C22.7103 17.0427 22.8047 19.2967 24.8432 20.6027L28.7887 23.1305C30.6863 24.3462 30.7741 26.4444 28.9849 27.8168L25.1506 30.758C23.3614 32.1305 20.3726 32.2575 18.4751 31.0418L5.83526 22.9438C3.93769 21.728 3.84985 19.6299 5.63904 18.2574C7.56114 16.7831 7.46676 14.5291 5.42825 13.2231L1.48267 10.6952C-0.414855 9.47949 -0.502734 7.38135 1.28646 6.00891L5.12078 3.06771C6.90997 1.69528 9.89871 1.56825 11.7963 2.78394L24.4362 10.882Z"
              fill={BRAND}
            />
          </svg>
        </div>

        {/* HoverAi wordmark — drawn once on scroll-into-view */}
        {/*
            Figma: left 695/1429 ≈ 48.6%  top 45/393 ≈ 11.5%
                   width 738/1429 ≈ 51.6%
        */}
        <motion.div
          style={{
            position: 'absolute',
            top:   '11.5%',
            left:  '48.6%',
            width: '51%',
          }}
          initial="hidden"
          whileInView="visible"
          viewport={{ once: true, amount: 0.4 }}
        >
          <svg
            viewBox="0 0 87 23"
            fill="none"
            xmlns="http://www.w3.org/2000/svg"
            style={{ width: '100%', height: 'auto', display: 'block' }}
          >
            {WORDMARK_PATHS.map((d, i) => (
              <motion.path
                key={i}
                d={d}
                fill={BRAND}
                variants={{
                  hidden:  { opacity: 0, pathLength: 0 },
                  visible: {
                    opacity: 1,
                    pathLength: 1,
                    transition: {
                      pathLength: { duration: 0.1, delay: i * 1.0, ease: EASE },
                      opacity:    { duration: 0.01, delay: i * 0.07 },
                    },
                  },
                }}
              />
            ))}
          </svg>
        </motion.div>
      </div>

      {/* ── Links + tagline ─────────────────────────────────────────── */}
      <div className="max-w-[90%] mx-auto px-5 sm:px-10 pt-10 pb-12 md:grid md:grid-cols-[1fr_2fr] gap-10 sm:gap-30">
        {/* Tagline */}
        <p className="text-[15px] leading-relaxed mb-5" style={{ color: 'rgba(255,255,255,0.55)' }}>
          An agentic voice assistant for African creators — built to listen the way you actually speak.
        </p>

        {/* Nav columns */}
        <div className="flex flex-col sm:flex-row justify-between gap-16 sm:gap-20">
          {/* Product */}
          <div>
            <p className="text-[15px] font-semibold text-white mb-8">Product</p>
            <ul className="flex flex-col gap-2.5">
              {[['Features','#features'],['Pricing','#pricing'],['Language','#languages'],['Download','/download']].map(([label, href]) => (
                <li key={label}>
                  {href.startsWith('/') ? (
                    <Link to={href} className="text-[14px] transition-colors hover:text-white" style={{ color: 'rgba(255,255,255,0.55)' }}>{label}</Link>
                  ) : (
                    <a href={href} className="text-[14px] transition-colors hover:text-white" style={{ color: 'rgba(255,255,255,0.55)' }}>{label}</a>
                  )}
                </li>
              ))}
            </ul>
          </div>

          {/* Company */}
          <div>
            <p className="text-[15px] font-semibold text-white mb-8">Company</p>
            <ul className="flex flex-col gap-2.5">
              {[['About','#'],['Contact','#']].map(([label, href]) => (
                <li key={label}>
                  <a href={href} className="text-[14px] transition-colors hover:text-white" style={{ color: 'rgba(255,255,255,0.55)' }}>{label}</a>
                </li>
              ))}
            </ul>
          </div>

          {/* Legal */}
          <div>
            <p className="text-[15px] font-semibold text-white mb-8">Legal</p>
            <ul className="flex flex-col gap-2.5">
              {[['Privacy Policy','/privacy-policy'],['Terms of Use','/terms']].map(([label, href]) => (
                <li key={label}>
                  <Link to={href} className="text-[14px] transition-colors hover:text-white" style={{ color: 'rgba(255,255,255,0.55)' }}>{label}</Link>
                </li>
              ))}
            </ul>
          </div>
        </div>
      </div>

      {/* ── Bottom bar ──────────────────────────────────────────────── */}
      <div
        className="max-w-[90%] mx-auto px-5 sm:px-10 py-5 flex items-center justify-between flex-wrap gap-3"
        style={{ borderTop: '1px solid rgba(255,255,255,0.18)' }}
      >
        <p className="text-[15px]" style={{ color: 'rgba(255,255,255,0.55)' }}>
          &copy; {new Date().getFullYear()} Hover AI. All rights reserved.
        </p>
        <p className="text-[15px] font-semibold text-white">
          Powered by MacedonLabs
        </p>
      </div>

    </footer>
  )
}
