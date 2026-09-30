import { Routes, Route, Navigate, useLocation } from 'react-router-dom'
import { AnimatePresence } from 'framer-motion'
import LandingPage from './pages/LandingPage.tsx'
import DownloadPage from './pages/DownloadPage.tsx'
import PrivacyPolicyPage from './pages/PrivacyPolicyPage.tsx'
import TermsPage from './pages/TermsPage.tsx'
import CursorDot from './components/CursorDot.tsx'


export default function App() {
  const location = useLocation()
  return (
    <>
      <CursorDot />
      <AnimatePresence mode="wait">
        <Routes location={location} key={location.pathname}>
          {/* Landing page */}
          <Route path="/" element={<LandingPage />} />

          {/* Download page */}
          <Route path="/download" element={<DownloadPage />} />

          {/* Legal pages */}
          <Route path="/privacy-policy" element={<PrivacyPolicyPage />} />
          <Route path="/terms" element={<TermsPage />} />

          {/* Catch-all */}
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </AnimatePresence>
    </>
  )
}
