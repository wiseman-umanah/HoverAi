import './assets/main.css'
import 'remixicon/fonts/remixicon.css'
import '@renderer/lib/tauri-api'  // installs window.api

import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>
)
