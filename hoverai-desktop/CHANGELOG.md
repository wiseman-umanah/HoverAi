# Changelog

All notable changes to HoverAI Desktop are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
Versions follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [0.1.0] — 2025-09-28

First public release of the HoverAI Desktop app, rebuilt from the ground up on **Tauri 2** (replacing the previous Electron prototype).

### Added

#### Core activation
- **Global keyboard shortcut** — user-defined combo registered at the OS level via `tauri-plugin-global-shortcut`. Works as a reliable fallback at all times.
- **"Hey Hover" wake-word detection** — always-on, runs entirely in Rust using `livekit-wakeword` + ONNX Runtime. Ships the trained `hey_hover.onnx` classifier. 1.5 s cooldown after each detection.
- **Mic sensitivity setting** — slider from 1 (hardest to trigger) to 10 (easiest). Maps to a detection threshold (0.70 → 0.20).
- Wake-word detector starts and stops immediately when the toggle changes — no restart required.

#### Capture & AI overlay
- **Transparent overlay window** — frameless, always-on-top, ignores cursor events until activated.
- Screen capture on activation — PNG screenshot sent alongside audio to the backend.
- AI response rendered as step-by-step guidance cards inside the overlay.
- Overlay dismisses on query complete or manual close.

#### Onboarding
- Three-step flow: **Sign in / Sign up → Permissions → Shortcut setup**.
- Enter key advances each step so users never have to leave the keyboard.
- **Permissions page** — microphone and screen recording grants with live status feedback and mic visualiser. Cards area scrolls when content overflows; Continue button always visible.
- Optional **"Hey Hover" wake-word toggle** on the permissions page — default off, persisted immediately.
- **Shortcut recorder** — probes suggested combos for availability on mount, shows free/taken status. Enter key confirms a selected combo.

#### Settings
- Settings window opens from the system tray — loads instantly from local data, server data merges in silently in the background.
- **Language preference** — synced to backend `/users/me`.
- **Voice sensitivity** slider.
- **Always-on wake word** toggle with inline error toast if the mic or ONNX runtime fails.
- **AI voice** — female / male selector.
- **Launch at startup** — powered by `tauri-plugin-autostart`. Confirms actual OS state after toggle; snaps back and shows error if the OS rejects the change.
- **Capture shortcut** — change shortcut from within settings without re-running onboarding.
- **Conversation history** tab — fetches and renders previous sessions grouped by day.
- **Sign out** — clears tokens, unregisters shortcut, returns to onboarding.

#### Security & storage
- **OS credential store** for JWT tokens — Windows Credential Manager, macOS Keychain, Linux Secret Service (via `keyring` crate). Falls back to plain JSON if the secrets daemon is unavailable.
- Tokens are never sent to the renderer as plain text.
- Automatic token refresh on 401 — transparent to the user.

#### System tray
- Tray icon with context menu: Open Settings, Show Overlay, Quit.
- App does not quit when all windows are closed — lives in the tray.

#### Platform support
- **Windows** — NSIS installer + MSI.
- **macOS** — DMG for Apple Silicon (`aarch64`) and Intel (`x86_64`). Microphone and screen recording usage descriptions in `Info.plist`.
- **Linux** — AppImage + deb. Token storage via libsecret / KWallet.

#### CI / release pipeline
- GitHub Actions workflow (`.github/workflows/release.yml`) — builds all four targets in parallel on `v*` tags or manual dispatch.
- `VITE_API_BASE` injected at build time from a GitHub Actions secret — no hardcoded URLs.
- Draft release created automatically; publish manually after review.

#### Developer experience
- `pnpm tauri dev` — hot-reload dev server.
- `pnpm build:fast` — local Windows release build with LTO disabled (~5–8 min vs ~45 min).
- `pnpm build:win / build:mac / build:linux` — full production builds.
- `pnpm typecheck` — TypeScript check with zero errors.
- `VITE_API_BASE` baked in by `build.rs` reading `.env`; falls back to `http://localhost:8000/api/v1`.

### Technical stack

| Layer | Technology |
|---|---|
| Shell | Tauri 2 |
| Frontend | React 19, TypeScript 6, Vite 8, TailwindCSS 4, Framer Motion |
| Backend (Rust) | Tokio, reqwest (rustls), serde, cpal, livekit-wakeword, keyring, screenshots |
| Tauri plugins | global-shortcut, autostart, opener, store, shell |
| Wake-word model | `hey_hover.onnx` — trained ONNX classifier, threshold 0.41 at sensitivity 5 |

---

## [Unreleased]

Nothing yet — changes will accumulate here before the next version.

---

<!-- versions -->
[0.1.0]: https://github.com/wiseman-umanah/HoverAi/releases/tag/v0.1.0
[Unreleased]: https://github.com/wiseman-umanah/HoverAi/compare/v0.1.0...HEAD
