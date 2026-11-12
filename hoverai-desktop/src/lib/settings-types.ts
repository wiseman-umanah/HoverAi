// AppSettings type — mirrors the Rust AppSettings struct in src-tauri/src/settings.rs
// Field names use camelCase to match the JSON serde output (rename_all = "camelCase")

export type AppSettings = {
  launchAtLogin: boolean
  showInTaskbar: boolean
  overlayOpacity: number       // 0.4 – 1.0
  overlaySize: 'compact' | 'default' | 'large'
  micSensitivity: number       // 1 – 10
  soundEffects: boolean
  notifications: boolean
  language: string
  voiceGender: 'female' | 'male'
  shortcutKey: string          // persisted accelerator e.g. "CmdOrCtrl+Shift+H"
}
