# HoverAI Desktop

AI-powered on-screen assistant. Activate with a keyboard shortcut, speak or type your request, and get step-by-step visual guidance overlaid directly on your screen.

Built with **Tauri 2**, **React 19**, **TypeScript 6**, and **Rust**.

---

## Table of Contents

- [Features](#features)
- [Architecture](#architecture)
- [Prerequisites](#prerequisites)
- [Getting Started](#getting-started)
- [Environment Variables](#environment-variables)
- [Project Structure](#project-structure)
- [IPC Commands](#ipc-commands)
- [Build](#build)
- [CI / Releases](#ci--releases)
- [Known Constraints](#known-constraints)

---

## Features

| Feature | Detail |
|---|---|
| **Transparent overlay** | Frameless, always-on-top window that captures audio + screenshot and streams AI guidance back. |
| **Global shortcut** | User-defined keyboard combo registered via `tauri-plugin-global-shortcut`. |
| **Guided onboarding** | Auth → Permissions (mic + screen) → Shortcut setup. Enter key advances each step. |
| **Settings sync** | Local `settings.json` + server `/users/me/settings`. Settings page loads instantly from local data; server data merges in silently. |
| **Secure token storage** | JWTs stored in the OS credential store (Windows Credential Manager / macOS Keychain / Linux Secret Service) via the `keyring` crate, with a plain-JSON fallback. |
| **Launch at startup** | Powered by `tauri-plugin-autostart`. Toggle in Settings → System. OS state is confirmed and snapped back if the write fails. |
| **Conversation history** | History tab in Settings fetches and renders previous sessions from the backend. |
| **Multi-platform** | Windows (NSIS + MSI), macOS (universal DMG — Apple Silicon + Intel), Linux (AppImage + deb). |

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│  Renderer (Vite + React 19 + TypeScript)                │
│                                                         │
│  index.html  →  App.tsx       (onboarding flow)         │
│  overlay.html → OverlayWidget (transparent capture UI)  │
│  settings.html → SettingsPage (settings + history)      │
│                                                         │
│  src/lib/tauri-api.ts  ─── window.api shim              │
│  src/lib/api.ts        ─── HTTP client (typed)          │
└────────────────────┬────────────────────────────────────┘
                     │  invoke() / emit()
┌────────────────────▼────────────────────────────────────┐
│  Rust backend  (src-tauri/src/)                         │
│                                                         │
│  lib.rs        ─── 40+ IPC commands, tray, shortcuts   │
│  tokens.rs     ─── OS keyring + JWT decode              │
│  settings.rs   ─── settings.json read/write             │
│  http_client.rs─── fetchWithAuth + token refresh        │
└─────────────────────────────────────────────────────────┘
```

**Three windows, one preload:**
- `main` — onboarding (hidden after login)
- `overlay` — transparent fullscreen capture layer (always-on-top)
- `settings` — frameless settings panel (opened from tray)

**API base URL** is baked into the binary at compile time by `build.rs` reading `.env`. Changing `.env` requires restarting `pnpm dev`.

---

## Prerequisites

| Tool | Version | Notes |
|---|---|---|
| [Rust](https://rustup.rs) | stable | `rustup update stable` |
| [Node.js](https://nodejs.org) | ≥ 20 | |
| [pnpm](https://pnpm.io) | ≥ 9 | `npm i -g pnpm` |
| Tauri CLI | bundled | installed via `devDependencies` |
| **Windows only** | [WebView2](https://developer.microsoft.com/en-us/microsoft-edge/webview2/) | Usually pre-installed on Windows 11 |
| **Windows only** | [Visual Studio C++ build tools](https://visualstudio.microsoft.com/visual-cpp-build-tools/) | Needed by Rust linker |
| **Linux only** | `libwebkit2gtk-4.1-dev`, `libasound2-dev`, `libsecret-1-dev` | See CI workflow for full list |

---

## Getting Started

```bash
# 1. Clone and enter the desktop app directory
git clone https://github.com/your-org/hoverai.git
cd hoverai/hoverai-desktop

# 2. Install JS dependencies
pnpm install

# 3. Create your .env (copy the example and fill in your backend URL)
cp .env.example .env
# Edit .env — set VITE_API_BASE to your deployed backend URL

# 4. Start dev server + Tauri hot-reload
pnpm tauri dev
```

The first `tauri dev` compiles all Rust dependencies (~3–5 min). Subsequent runs use the incremental cache and are near-instant.

---

## Environment Variables

Create `hoverai-desktop/.env` (gitignored):

```env
# Backend API base URL — baked into the binary at compile time.
# Restart pnpm dev after changing this.
VITE_API_BASE=https://your-backend.onrender.com/api/v1
```

> `.env.example` contains a template. The file is read by `build.rs` and emitted as a `cargo:rustc-env` variable, making it available in Rust via `option_env!("VITE_API_BASE")` and in the renderer via `import.meta.env.VITE_API_BASE`.

---

## Project Structure

```
hoverai-desktop/
├── .env.example              # Copy to .env — fill in VITE_API_BASE
├── index.html                # Onboarding window entry
├── overlay.html              # Overlay window entry
├── settings.html             # Settings window entry
├── vite.config.ts            # Multi-page Vite config
├── src/
│   ├── App.tsx               # Onboarding state machine (auth → permissions → shortcut)
│   ├── overlay.tsx           # Overlay entry point
│   ├── settings.tsx          # Settings entry point
│   ├── pages/
│   │   ├── AuthPage.tsx      # Sign in / sign up
│   │   ├── PermissionsPage.tsx  # Mic + screen grants
│   │   ├── ShortcutPage.tsx  # Keyboard shortcut recorder
│   │   └── SettingsPage.tsx  # Full settings + conversation history
│   ├── lib/
│   │   ├── api.ts            # Typed HTTP client (all backend calls)
│   │   ├── tauri-api.ts      # window.api shim + Tauri event listeners
│   │   └── settings-types.ts # AppSettings TypeScript type
│   ├── components/           # LangDropdown, MicVisualiser, AuthPanel, …
│   └── assets/               # base.css (design tokens), fonts
└── src-tauri/
    ├── Cargo.toml            # Rust dependencies + build profiles
    ├── tauri.conf.json       # Window config, CSP, bundle settings
    ├── Info.plist            # macOS privacy usage descriptions
    ├── build.rs              # Reads .env → cargo:rustc-env
    ├── capabilities/
    │   └── default.json      # Tauri capability permissions
    └── src/
        ├── lib.rs            # Main backend — all IPC commands, tray, shortcuts
        ├── tokens.rs         # OS keyring token storage
        ├── settings.rs       # settings.json persistence
        └── http_client.rs    # fetchWithAuth + automatic token refresh
```

---

## IPC Commands

All renderer → Rust communication goes through `window.api` (defined in [`tauri-api.ts`](src/lib/tauri-api.ts)).

| Command | Direction | Purpose |
|---|---|---|
| `get_settings` / `save_settings` | invoke | Read / write `settings.json` |
| `set_launch_at_login` | invoke | Toggle OS autostart entry + persist. Returns actual OS state. |
| `store_tokens` / `get_access_token` / `clear_tokens` / `refresh_tokens` | invoke | OS keyring token management |
| `request_permission` | invoke | Trigger OS mic / screen permission prompt |
| `test_shortcut` / `register_shortcut` / `register_shortcut_from_settings` | invoke | Shortcut availability + registration |
| `get_active_shortcut` | invoke | Read the currently registered accelerator string |
| `launch_overlay` | invoke | Show overlay + create tray after onboarding |
| `focus_overlay` / `dismiss_overlay` | invoke | Show / hide overlay |
| `capture_done` / `capture_done_with_screenshot` | invoke | Submit recorded audio (+ optional screenshot) |
| `take_screenshot` | invoke | Capture current screen as base64 PNG |
| `open_settings_window` / `close_settings_window` | invoke | Show / hide settings window |
| `sign_out` | invoke | Clear tokens, unregister shortcut, show onboarding |
| `capture-start` | Rust → renderer | Screenshot dataUrl + shortcut key (starts capture UI) |
| `capture-end` | Rust → renderer | Dismiss overlay |
| `query-result` | Rust → renderer | AI response payload |
| `query-error` | Rust → renderer | Error string from backend |

---

## Build

### Development

```bash
pnpm tauri dev          # Hot-reload dev build (debug profile)
pnpm typecheck          # TypeScript type check only
```

### Local release builds

```bash
# Fast local build — LTO disabled, ~5–8 min, slightly larger binary
pnpm build:fast

# Full production build — full LTO, ~45 min, smallest binary
pnpm build:win          # Windows → .exe + NSIS installer
pnpm build:mac          # macOS → .app + .dmg  (run on a Mac)
pnpm build:linux        # Linux → .AppImage + .deb
```

#### Outputs

| Platform | Output location |
|---|---|
| Windows | `src-tauri/target/x86_64-pc-windows-msvc/release/bundle/nsis/*.exe` |
| macOS | `src-tauri/target/aarch64-apple-darwin/release/bundle/dmg/*.dmg` |
| Linux | `src-tauri/target/x86_64-unknown-linux-gnu/release/bundle/appimage/*.AppImage` |

#### Speeding up Windows builds (WiX pre-download)

Tauri downloads the WiX toolset on first build. Pre-download it once to avoid network timeouts:

```powershell
$wixDir = "$env:USERPROFILE\.wix\bin"
New-Item -ItemType Directory -Force -Path $wixDir | Out-Null
Invoke-WebRequest `
  "https://github.com/wixtoolset/wix3/releases/download/wix3141rtm/wix314-binaries.zip" `
  -OutFile "$env:TEMP\wix.zip"
Expand-Archive "$env:TEMP\wix.zip" -DestinationPath $wixDir -Force
[System.Environment]::SetEnvironmentVariable("WIX", $wixDir, "User")
```

Close and reopen your terminal after running this.

---

## CI / Releases

Three GitHub Actions workflows (all under [`.github/workflows/`](../.github/workflows/)):

| Workflow | When | What |
|---|---|---|
| `ci.yml` | every push / PR | Backend ruff + pytest; desktop tsc, clippy (Linux + Windows), unit tests |
| `package.yml` | PRs touching `hoverai-desktop/`, or manually | Installers built with the `release-fast` profile (no LTO), uploaded as **artifacts** on the run page: Windows NSIS + MSI, macOS universal DMG, Linux AppImage + deb. Kept 14 days. |
| `release.yml` | `v*` tags, or manually | Full `release` profile, same platforms; creates a **draft** GitHub release |

### One-time setup

Add this secret in **GitHub → repo → Settings → Secrets and variables → Actions**:

| Secret | Value |
|---|---|
| `VITE_API_BASE` | `https://your-backend.onrender.com/api/v1` |
| `TAURI_SIGNING_PRIVATE_KEY` | Contents of the private key from `pnpm tauri signer generate` (signs auto-updates; `release.yml` fails without it) |
| `TAURI_SIGNING_PRIVATE_KEY_PASSWORD` | Its password (empty if none) |

`release.yml` fails if it is missing. `package.yml` only warns and builds against `http://localhost:8000` (this also happens for PRs from forks, which never receive secrets).

### Test a PR build

Open the PR's **Package** run → *Artifacts* → download `hoverai-windows-<sha>` (or the macOS / Linux one) → install it in the Windows VM / on the test machine. Builds are unsigned: Windows SmartScreen needs *More info → Run anyway*, macOS needs right-click → *Open*.

### Auto-updates

Installed apps check `https://github.com/TheCodeCheff123/HoverAi/releases/latest/download/latest.json` a few seconds after starting (release builds only) and from **Settings → Updates**. To ship an update:

1. Bump the version in `package.json`, `src-tauri/tauri.conf.json` and `src-tauri/Cargo.toml` (it must be higher than the installed one).
2. Tag and push (below) — `release.yml` builds, signs the update bundles and attaches `latest.json` to a **draft** release.
3. **Publish** the draft. Only then do installed apps see it.

Windows and macOS installs update in place; on Linux only the **AppImage** can update itself (`.deb` installs need a manual reinstall). Never lose the private key: copies signed with it can't accept updates from a new key and would need a manual reinstall.

### Trigger a release

```bash
git tag v1.0.0
git push origin v1.0.0
```

This creates a **draft release** with installers for Windows, macOS (universal: Apple Silicon + Intel) and Linux attached. Review the draft and publish when ready.

You can also trigger a build manually from **GitHub → Actions → Release → Run workflow**.

---

## Linux: X11 vs Wayland

| | X11 | Wayland |
|---|---|---|
| Capture shortcut | `tauri-plugin-global-shortcut` (X11 key grab) | XDG **GlobalShortcuts portal**, shortcut id `capture`. GNOME/KDE ask the user to confirm the key; Hyprland lists it in `hyprctl globalshortcuts` and needs `bind = …, global, com.hoverai.desktop:capture`. Fallback on any compositor: a keybind running `hoverai-desktop --trigger`. |
| Esc to dismiss | Global (works without focus) | Only while the overlay has focus |
| Screenshot | xcap | xcap, `grim` fallback (wlroots compositors) |
| Accessibility elements | AT-SPI, screen coordinates | AT-SPI, window-relative coordinates (+ Hyprland IPC for the focused window) |
| Click-through overlay | GDK input region | GDK input region |
| Window placement | App positions the overlay | Hyprland ignores client positioning — needs float/pin/size window rules |

Tested so far: **Hyprland 0.56 (Wayland)** only. GNOME and KDE on Wayland and any X11 session are expected to work through the paths above but are **untested**. In all sessions, Chromium/Electron apps expose accessibility elements only when started with `--force-renderer-accessibility`; terminals, Zed and games expose none (the model falls back to vision-only targets).

---

## Known Constraints

| Area | Detail |
|---|---|
| **Cross-compilation** | You cannot build macOS installers from Windows. Each platform must be built on its native OS (or use the CI runners). |
| **WiX timeout** | On slow connections, the WiX download during `pnpm build:win` may time out. Pre-download WiX using the PowerShell snippet above. |
| **Render cold start** | The free-tier Render.com backend sleeps after inactivity. The first API call after sleep takes 30–50 s. The settings page loads instantly from local data; server data merges in once it responds. |
| **Linux Secret Service** | Token storage falls back to plain JSON if `libsecret` / KWallet is not running (e.g. minimal desktop environments). |
| **macOS notarisation** | Unsigned macOS builds will show a Gatekeeper warning. To notarise, add the Apple signing secrets to the CI workflow (see commented-out env vars in `release.yml`). |
