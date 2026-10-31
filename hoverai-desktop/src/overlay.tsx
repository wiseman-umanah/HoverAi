import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './assets/overlay.css'
import '@renderer/lib/tauri-api'  // installs window.api
import OverlayWidget from '@renderer/overlay/OverlayWidget'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <OverlayWidget />
  </StrictMode>
)
