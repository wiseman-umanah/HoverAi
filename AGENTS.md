# AGENTS.md

This file provides guidance to agents when working with code in this repository.

## Projects in this repository

- `hoverai-desktop/` — the desktop app: **Tauri 2 (Rust) + React 19 + Vite + Tailwind v4 + TypeScript**. (There is no Electron app any more — the old `hover-app/` was replaced; ignore any lingering "Electron" wording.)
- `backend/` — FastAPI + SQLModel API the desktop app talks to (Python 3.12, `uv`). Deployed on Render.
- `wakeword/` — training pipeline + local test harness for the "Hey Hover" wake-word model (livekit-wakeword). **The wake word was removed from the desktop app on 2026-11-01** (the model caught only ~50 % of real-voice prompts); it returns only once a retrained model passes `wakeword/wake-test guided`.
- `landing_page/` — HoverAI marketing site (React 19 + Vite + Tailwind v4). Deployed on Vercel.
- `docs/v2/` — v2 planning (**new sessions: read `HANDOFF.md` first**): `EXECUTION_PLAN.md` (order of work — authoritative), `DEV_SETUP.md` (local machine setup), `BUG_ANALYSIS.md` (known v1 bugs), `AGENTIC_PLAN.md` (agentic target architecture). Check these before working on related areas.
- `.github/workflows/release.yml` — on a `v*` tag, builds the desktop app for Windows, macOS (universal) and Linux via `tauri-action` and creates **draft** GitHub releases. Needs repo secret `VITE_API_BASE` (fails without it).
- `.github/workflows/package.yml` — on PRs touching `hoverai-desktop/` (or manually), builds installers with the `release-fast` profile and uploads them as workflow artifacts for VM/tester installs.

### End-to-end flow
Trigger (global shortcut; on Wayland a compositor keybind running `hoverai-desktop --trigger`, forwarded by the single-instance plugin) → Rust `start_capture` screenshots the monitor under the cursor (xcap) while collecting screen context in parallel (focused app/window, cursor, accessibility elements), draws numbered marks on the elements, encodes a resized JPEG, and starts native mic recording (cpal) → overlay shows the recording widget (levels via `audio-level` events) → Enter / pressing the trigger again → Rust stops recording (16 kHz mono WAV) and POSTs multipart **files** `audio` (audio/wav) + `screenshot` (image/jpeg) + text field `context` (JSON) to `POST /api/v1/query` → backend: Groq Whisper STT ‖ conversation history → Groq multimodal vision LLM → `{summary, steps[]}` → TTS (Sahara for African languages, Groq Orpheus otherwise) → server resolves each step's `target.element_id` to the element's box → Rust emits `query-result` → overlay shows a draggable steps panel, a pulsing **beacon** on the current step's target (click-through), and plays `speech_b64`.

The backend also has `/agent/start` + `/agent/turn` (ReAct loop returning click/type/key actions with normalised x/y) — **not yet wired into the desktop app**, which has no input-automation code.

---

## `landing_page/` — HoverAI Landing Page

Stack: React 19 + Vite 8 + Tailwind CSS v4 + TypeScript 6. Package manager: **pnpm**. All work lives in `landing_page/`.

### Commands (run from `landing_page/`)
```bash
pnpm dev       # dev server on :5173, proxies /api → :8000
pnpm build     # tsc && vite build
pnpm preview   # preview prod build
```
No test runner is configured.

### Critical Non-Obvious Points

#### Tailwind v4 — no config file
Tailwind 4 is used via `@tailwindcss/vite` plugin. All colour tokens are defined inside `@theme {}` blocks in [`landing_page/src/index.css`](landing_page/src/index.css). There is no `tailwind.config.*` file. To add/change a design token, edit the `@theme` block — not any config file.

#### Custom fonts are local `.otf` files, not Google Fonts
Two brand fonts are loaded from `assets/fonts/` via `@font-face` in `index.css`:
- `CreatoDisplay` — primary body/heading font (100–900 weights). Applied globally to `html, body, h1–h6`.
- `NeueMachina` — accent font (300, 400, 800 weights only).
Use utility classes `.font-creato`, `.font-creato-italic`, `.font-machina` rather than raw `font-family` declarations.

#### `@` alias maps to `src/`
`@/components/Foo` → `src/components/Foo`. Configured in both `vite.config.ts` and `tsconfig.json` `paths`. Use `@/` for all intra-src imports. Assets outside `src/` (e.g. images) are imported with relative paths: `../../assets/images/hero.png`.

#### Tool images auto-discovered via `import.meta.glob`
[`src/data/tools.ts`](landing_page/src/data/tools.ts) uses `import.meta.glob('../../assets/images/tools/*.png', { eager: true, query: '?url' })` to build the `TOOLS` array. Drop a `.png` into `assets/images/tools/` and it appears in the marquee automatically — no manual registration needed.

#### Marquee animation is CSS-only (defined in `index.css`)
`.animate-marquee` and `.animate-marquee-reverse` classes are defined in `index.css` (not Tailwind plugins). The marquee requires 4× duplication of items in JSX to appear seamless (`-25%` translateX keyframe assumes 4 copies).

#### Framer Motion page transitions require `AnimatePresence` + keyed routes
`App.tsx` wraps `<Routes>` in `<AnimatePresence mode="wait">` and keys it by `location.pathname`. New pages must include entry/exit `motion.*` props or use the `<FadeUp>` wrapper component for scroll-triggered animations.

#### `FadeUp` component — scroll-triggered, fires once
[`src/components/FadeUp.tsx`](landing_page/src/components/FadeUp.tsx) uses `whileInView` + `viewport={{ once: true }}`. Default ease is `[0.22, 1, 0.36, 1]` (expo out) — use this same curve for any manual `motion.*` transitions to stay consistent.

#### `RemixIcon` component wraps Remix icons
Use [`<RemixIcon name="ri-arrow-right-line" />`](landing_page/src/components/RemixIcon.tsx) — pass the full class name (e.g. `ri-arrow-right-line`), not just the suffix. The remixicon CSS is imported in [`src/main.tsx`](landing_page/src/main.tsx) via `import 'remixicon/fonts/remixicon.css'` — **do not add it to `index.html`**; the `/node_modules/` path breaks in production builds. Without this import, all `ri-*` icons render as blank `<i>` tags with no error.

#### `FoidWordmark` SVG component has two sizing modes
- **Fixed**: pass `height={24}` (number) — scales all 4 letter SVGs proportionally.
- **Fluid**: pass `style={{ width: '90vw' }}` without `height` prop — enters fluid mode using percentage widths. Never mix both.

#### TypeScript strictness
`noUnusedLocals`, `noUnusedParameters`, and `erasableSyntaxOnly` are all `true`. All imports must be used; `enum` is banned (`erasableSyntaxOnly`). Use `type` imports (`import type { Foo }`) for type-only imports (`verbatimModuleSyntax: true`).

#### Deployment
Deployed on Vercel. [`vercel.json`](landing_page/vercel.json) rewrites all routes to `index.html` for SPA routing. Update canonical URL and OG URLs in `index.html` before going live.

#### Background colour
The global background is `var(--color-neutral-950)` (`#0a0a0a`) — near-black. Design assumes dark background; text defaults to `#ffffff`.

#### Design system — strict glassmorphism
All surfaces (header pill, cards, buttons) use the **3-layer background shorthand**: `linear-gradient(...) padding-box, linear-gradient(...) border-box, rgba(...) padding-box` with `border: 1px solid transparent`. This is the canonical pattern — do not use `border-color` or `outline` for glass borders.
`backdropFilter: 'blur(20px) saturate(160%) brightness(1.08)'` is the standard frost recipe; lighter panels may use `blur(12px)` but keep `saturate(160%)`.

#### Platform download icons — Remix icons fill/logos
Use `<i className="ri-windows-fill" />` and `<i className="ri-apple-fill" />` directly (raw `<i>` tags, not `<RemixIcon>`) for platform logos in download buttons — these are in the `fill/logos/` family and render at icon font size via `text-[22px]` class.

#### Image assets — prefer WebP
Hero image is `assets/images/hero.webp`; the download-page background is `assets/images/back.webp` (converted from PNG via ImageMagick `convert`). Always add new images as `.webp`. Import via relative path from `src/`: `import img from '../../assets/images/back.webp'`.

#### Routes
`/` → `LandingPage`, `/download` → `DownloadPage`, `/privacy-policy` → `PrivacyPolicyPage`, `/terms` → `TermsPage` (the desktop app's sign-up screen links to `/terms`). The download page uses a standalone header (logo only, no nav). `App.tsx` catches `*` → `/`. "Get Started Free" everywhere links to `/download`.

---

## `hoverai-desktop/` — Tauri Desktop App

Stack: Tauri 2 + Rust (2021) backend in `src-tauri/`, React 19 + Vite 8 + Tailwind v4 + TypeScript 6 frontend in `src/`. Package manager: **pnpm**. Version lives in both `package.json` and `src-tauri/tauri.conf.json` (+ `Cargo.toml`) — bump all three together.

### Commands (run from `hoverai-desktop/`)
```bash
pnpm tauri dev        # runs `pnpm dev` (Vite on :1420) + builds/launches the Rust app
pnpm typecheck        # tsc --noEmit (frontend only)
pnpm build:win|mac|linux   # typecheck + tauri build for that target (release profile: full LTO, slow)
pnpm build:fast       # Windows build with the `release-fast` profile (no LTO)
cd src-tauri && cargo check   # Rust check
```
No test runner and no linter/formatter config. TS style in this project: single quotes, no semicolons, 2-space indent.
Linux build deps: webkit2gtk-4.1, gtk3, ayatana-appindicator, librsvg, libasound2 (cpal), libsecret (keyring) — see `release.yml`.

### Architecture

#### Three windows, three HTML entry points
Declared in `src-tauri/tauri.conf.json`, built as a Vite multi-page app (`vite.config.ts` → `rollupOptions.input`):
- `src-tauri/src/main.rs` sets `WEBKIT_DISABLE_DMABUF_RENDERER=1` on Linux (unless already set) — WebKitGTK's DMA-BUF renderer draws coloured stripes over animated cards on older Intel/NVIDIA GPUs.
- `main` → `index.html` → `src/main.tsx` → `App.tsx`: onboarding wizard (`AuthPage` → `PermissionsPage` → `ShortcutPage`). Starts hidden (`visible: false`); Rust shows it at startup unless onboarded (= tokens stored **and** a shortcut chosen). Resumes at `permissions` if a session exists; resets to `auth` on the `signed-out` event.
- `overlay` → `overlay.html` → `src/overlay.tsx` → `overlay/OverlayWidget.tsx`: transparent, undecorated, always-on-top, skip-taskbar, initially hidden. Moved/resized to the monitor under the cursor on each capture. **Pure view** — the session state lives in Rust (see below).
- `settings` → `settings.html` → `src/settings.tsx` → `pages/SettingsPage.tsx`: opened from the tray (click or "Settings" menu item). Has a Quit button.
Window close: `main` before onboarding → **quits**; `overlay` → cancels the capture; otherwise hides. Quit (tray "Quit HoverAI", Settings → Quit, `quit_app`) unregisters shortcuts, stops audio, and force-exits after 2 s if teardown hangs.

#### Rust side (`src-tauri/src/`)
- `lib.rs` — `AppState`, the **capture session state machine**, all `#[tauri::command]`s, tray, single-instance/log plugins, startup, quit. Session: `Idle → Recording → Thinking → Showing`; the trigger toggles (Idle→start, Recording→submit, Thinking/Showing→cancel). Every capture bumps `Session.id`; results/errors for an old id are dropped (no stale panels). While the overlay is up, `Escape` is registered as a temporary global shortcut (works without focus on Windows/macOS/X11). `cancel_capture` emits `capture-end`; the overlay must never call back into cancel on that event (that caused the v1 dismiss loop).
- `audio.rs` — native mic capture (cpal) on a dedicated thread; emits `audio-level` (0–1, ~20/s); `stop()` → 16 kHz mono PCM16 WAV. Also used for the onboarding mic test (`mic_monitor_start/stop`) and `request_permission("microphone")`. The webview never uses `getUserMedia` (WebKitGTK denies it on Linux).
- `capture.rs` — xcap capture of the monitor under the cursor (`grim` fallback on Wayland), resized to ≤1600 px long edge, JPEG q80. `capture_with_context` runs `context::gather` in parallel (both before the overlay shows) and draws marks.
- `a11y/` — `snapshot(timeout)`: interactive elements of the focused window. Linux: raw AT-SPI over D-Bus (zbus); on Hyprland the focused window is taken from Hyprland IPC because AT-SPI's "active" flag is unreliable there; rects are `WindowRelative` on Wayland, `Screen` on X11. Windows: UI Automation (`uiautomation` crate; compiled in CI, not yet run on a real machine). macOS: stub. Chromium/Electron apps expose nothing unless started with `--force-renderer-accessibility`; terminals/Zed/games expose nothing — the model then falls back to bbox targets from vision.
- `context.rs` — normalises everything to the captured monitor (0–1): active window (xcap, or `hyprctl` on Hyprland — logical coords), cursor, elements (≤120, named first, then reading order; ids = mark numbers). Serialised as the `context` form field.
- `marks.rs` — draws a coloured box + id tag (built-in 3×5 digit font) per element on the copy sent to the model.
- Grounding eval fixtures: run the app with `HOVERAI_FIXTURE_DIR=/some/dir` and every successful query is saved as `case-<ms>/{screen.jpg, context.json, case.json}`; fill `expected` and run `uv run eval-grounding` in `backend/` (see `backend/eval/README.md`).
- `hit_region.rs` — the overlay window covers the whole monitor, but only elements marked `data-hit` in `OverlayWidget.tsx` take mouse input. The overlay reports their rects (CSS px) via `overlay_hit_regions` after every render. Linux: GDK input region (works on Wayland). Windows/macOS: a 30 ms cursor poller toggles `set_ignore_cursor_events`; any Rust code that changes click-through itself must call `hit_region::invalidate()`.
- `wayland_shortcut.rs` (Linux) — on Wayland the capture shortcut is bound through the XDG **GlobalShortcuts portal** (`ashpd`), shortcut id `capture`. Unsandboxed apps must first register app id `com.hoverai.desktop` with the portal's host Registry, which requires a matching `.desktop` file — `ensure_desktop_entry()` writes a hidden one to `~/.local/share/applications/` unless a package installed it in `/usr/share/applications/`. GNOME/KDE prompt the user to confirm the trigger; Hyprland lists it in `hyprctl globalshortcuts` and the user binds it with `bind = …, global, com.hoverai.desktop:capture`.
- `auth.rs` — the only token manager: proactive refresh (<60 s to expiry), **single-flight** refresh (`REFRESH_LOCK`; refresh tokens rotate on every use), clears tokens only on 401/403 from `/auth/refresh`; `send_authed(build)` rebuilds the request per attempt so multipart works and retries once after a 401.
- `updater.rs` — self-update via `tauri-plugin-updater` from GitHub Releases (`plugins.updater` in `tauri.conf.json`: minisign pubkey + `releases/latest/download/latest.json`). Release builds check 5 s after startup and open Settings if an update exists (Updates card: check / install & restart; events `update-available`, `update-progress`). Only **published** releases count; Linux updates AppImage installs only. `bundle.createUpdaterArtifacts` is on, so any local bundled build (`pnpm build:*`) needs `TAURI_SIGNING_PRIVATE_KEY` set (or `--config '{"bundle":{"createUpdaterArtifacts":false}}'`); `package.yml` turns it off. Release CI needs secrets `TAURI_SIGNING_PRIVATE_KEY` + `TAURI_SIGNING_PRIVATE_KEY_PASSWORD`.
- `tokens.rs` — access/refresh tokens in the OS keychain (`keyring`, service `com.hoverai.desktop`), falling back to `app_data_dir/tokens.json`. `jwt_expiry` decodes `exp` without verifying.
- `settings.rs` — `AppSettings` persisted as JSON at `app_data_dir/settings.json`. `load()` merges saved JSON over defaults so new fields are safe to add. `save_settings` never overwrites `shortcut_key` (owned by `register_shortcut`).
- Logs: `tauri-plugin-log` → stdout + the OS app log dir (`~/.local/share/com.hoverai.desktop/logs/hoverai.log` on Linux, `%LOCALAPPDATA%\com.hoverai.desktop\logs` on Windows). Use `log::info!/warn!/error!`, not `println!`.
- Hardware smoke tests (no windows opened): `cargo test --lib -- --ignored --nocapture` (captures the screen, opens the mic, binds the Wayland portal shortcut, prints the a11y snapshot / screen context of the focused window, writes a marked screenshot to `/tmp/hoverai-marked.jpg`).
- On this Hyprland 0.56 (Lua config) machine `hyprctl dispatch` takes Lua: `hyprctl dispatch 'hl.dsp.focus({ window = "address:0x…" })'`, `hyprctl dispatch 'hl.dsp.window.close()'` — the old `focuswindow class:…` form errors.
- CI (`.github/workflows/ci.yml`): `desktop` (Ubuntu: tsc, `cargo clippy -D warnings`, unit tests) and `desktop-windows` (clippy) — keep clippy warning-free.

#### IPC conventions
- Renderer → Rust: `invoke('snake_case_command', { camelCaseArgs })` — Tauri converts camelCase arg keys to the Rust snake_case parameter names. All wrappers live in `src/lib/tauri-api.ts`, which also installs them as `window.api` (imported for side effects at the top of each entry `.tsx`). Add new commands in three places: the `#[tauri::command]` fn, `generate_handler![...]` in `lib.rs`, and a wrapper + entry in the `tauriApi` object.
- Overlay → Rust intents: `capture_submit`, `capture_cancel`, `capture_again`.
- Rust → renderer events: `capture-start`, `capture-submitted`, `capture-end`, `query-result`, `query-error` (to `overlay`), `signed-out` (to `main`), `audio-level` (all windows). Subscribe via the `on*` helpers in `tauri-api.ts` (they return an unsubscribe fn and are safe to unsubscribe before `listen()` resolves).
- Capability permissions for window ops/plugins are in `src-tauri/capabilities/default.json` — a new window API call from JS needs its permission listed there.

#### Backend API base URL is baked in at compile time
`hoverai-desktop/.env` → `VITE_API_BASE=https://…/api/v1`. Vite injects it into the renderer (`import.meta.env.VITE_API_BASE`, used by `src/lib/api.ts`), and `src-tauri/build.rs` parses the same file and emits `cargo:rustc-env` so Rust reads it via `option_env!` (`api_base()` in `lib.rs`). Both default to `http://localhost:8000/api/v1`. Changing it requires restarting `pnpm tauri dev`. **The webview CSP (`tauri.conf.json` → `security.csp` → `connect-src`) only allows `localhost`, `*.onrender.com` and ngrok domains** — a new backend host must be added there or renderer `fetch` calls fail. For an ngrok base URL, `api.ts` adds `ngrok-skip-browser-warning` (the free tier otherwise returns an HTML interstitial to the webview's fetch).

#### Two HTTP clients
- `src/lib/api.ts` — renderer `fetch` wrapper for auth/users/settings/history/languages. Gets a fresh token via `window.api.getAccessToken()` (Rust refreshes first if needed), on 401 calls `window.api.refreshTokens(rejectedToken)` (single-flight in Rust) and retries once; throws `ApiError` / `AuthExpiredError`.
- Rust (`reqwest`, rustls) — `/query`, `/auth/refresh`, `/auth/signout`, startup `/users/me` probe.

#### Settings live in three places — keep them in sync
`AppSettings` in `src-tauri/src/settings.rs` (serde `camelCase`) ⇄ `src/lib/settings-types.ts` ⇄ backend `UserSettings` model + `schemas/users.py` (snake_case). `SettingsPage` writes locally via `save_settings` and mirrors server-side fields via `PATCH /users/me/settings`; `language` is stored on the user (`PATCH /users/me`), not in `UserSettings`.

#### Shortcuts
Stored in Electron accelerator style (`CommandOrControl+Shift+H`); `electron_to_tauri_shortcut` converts before registering with `tauri-plugin-global-shortcut`. Only one global shortcut is registered at a time (`AppState.active_shortcut`); a new one is registered before the old is released. UI capture lives in `src/lib/shortcuts.ts` (`formatCombo` uses `KeyboardEvent.code`, so Ctrl+Shift+1 stays "1"; Cmd/Ctrl/Super/Option are mapped per platform) — don't re-implement it per page. On Wayland, `register_shortcut` also (re)binds the portal shortcut and succeeds even if the X11 grab fails; `platform_info` tells the UI it's Wayland.

#### Path alias
`@renderer/*` → `src/*` (`vite.config.ts` + `tsconfig.json`). Do not use `@/` here (that's the landing page).

#### Styling
Tailwind v4 via `@import 'tailwindcss'` in `src/assets/main.css` (tokens in its `@theme {}` block) and `overlay.css`; CSS variables in `base.css`. In practice most components use inline `style={{}}` objects — match the surrounding file. Remix icons are loaded via `import 'remixicon/fonts/remixicon.css'` in `main.tsx` / `settings.tsx`; use `<i className="ri-…" />`.

#### Platform notes
- The overlay must `show()` before `set_focus()` and needs the ~80 ms sleep on Windows, otherwise Enter never reaches the webview (Esc is global, so it doesn't depend on focus).
- **Linux/Wayland (dev machine is Hyprland):** `tauri-plugin-global-shortcut` is X11-only, so the shortcut goes through the portal (above) and the temporary global Esc does not work (Esc works when the overlay has focus). On the dev machine a compositor keybind is also set (`~/.config/hypr/bindings.lua`, block `hoverai-dev`: SUPER+SHIFT+H → `target/debug/hoverai-desktop --trigger`); pressing it again sends/dismisses. Hyprland ignores client window positioning, so the overlay's float/pin/size-to-monitor rules are in `~/.config/hypr/hyprland.lua` (block `hoverai-dev`).
- `set_ignore_cursor_events(true)` makes the overlay click-through; every path that hides the overlay must also restore this, or an invisible window can swallow input.
- macOS: `macOSPrivateApi: true` (transparent windows), mic/screen usage strings in `src-tauri/Info.plist`. Screen-recording permission is triggered by taking a screenshot (`request_permission("screen")`).
- Release profile uses `panic = "abort"` — an `unwrap()` panic kills the whole app.
- Dev profile (`Cargo.toml`): no debug info for dependencies, line tables only for our crate, and `opt-level = 3` for the image/PNG/deflate crates (screenshot encoding is unusably slow otherwise). Keep this when touching profiles. Crate downloads live in `~/.cargo/registry` (shared, downloaded once); `target/` is build output only.

---

## `backend/` — FastAPI API

Stack: Python 3.12 (`>=3.12,<3.13`), FastAPI, SQLModel/SQLAlchemy async (asyncpg on Postgres; aiosqlite in tests), Alembic, PyJWT, Groq (Whisper STT, multimodal vision LLM, Orpheus TTS) and Intron Sahara (TTS, optional streaming STT). Package manager: **uv**. `ffmpeg` is only needed for legacy WebM uploads (called via `asyncio.create_subprocess_exec`; no pydub). Deployed as a Docker image (`backend/Dockerfile`, `render.yaml` → `runtime: docker`). See `backend/README.md` for the endpoint table and upload contract.

### Commands (run from `backend/`)
```bash
uv sync               # install deps
uv run hover-dev      # dev server :$PORT (default 8000), reload, debug logs
uv run hover          # prod server 0.0.0.0:$PORT (Docker CMD)
uv run pytest -q      # tests — no .env/network needed (conftest sets dummy keys + in-memory SQLite)
uv run ruff check app tests scripts
uv run pyright app    # type check
uv run eval-grounding # grounding eval on backend/eval/cases — real Groq calls, manual only (see eval/README.md)
uv run alembic upgrade head   # apply migrations (`uv run setup-db` does the same)
```

### Critical Non-Obvious Points
- **All env vars are read only in `app/config.py`** (`settings` singleton, pydantic-settings, reads `.env`). Never call `os.getenv` elsewhere. Required: `DATABASE_URL`, `JWT_SECRET`, `INTRON_API_KEY`, `GROQ_API_KEY`. Keep `.env.example` and `render.yaml` in sync when adding settings.
- Routers in `app/routers/` are mounted in `app/api/v1/router.py` under `/api/v1` (plus `/health` defined there). Business logic lives in `app/services/`; routers stay thin.
- Single shared Groq client: `app/services/llm_client.py` → `groq_client`. Vision model name is `settings.vision_model` (also used by the agent loop).
- LLM output: `vision.py` requests Groq JSON mode (`VISION_JSON_MODE`; a 400 rejecting `response_format` → one retry without it, remembered per model; `json_validate_failed` → its `failed_generation` goes to the parser). Both `vision.py` and `agent.py` still parse with `_extract_json` / `_strip_thinking` (strip `<think>` blocks and code fences), with one "Invalid JSON" retry.
- `/query` writes `QueryLog` and the conversation turn as FastAPI **background tasks** after responding, each with its own `AsyncSessionLocal()` session — don't pass the request session into background tasks.
- **DB sessions:** `get_session` commits only *after* the response is sent, so routes that return tokens `await session.commit()` themselves. `/query`, `/agent/*` and `get_current_user` don't hold a request-scoped session at all — they use short `async with AsyncSessionLocal()` blocks so no pool connection is pinned through STT/vision/TTS.
- **Upload contract** (`/query`, `/agent/start`, `/agent/turn`, helpers in `app/uploads.py` + `app/routers/common.py`): `audio` file part — v2 `audio/wav` 16 kHz mono (passed through), v1 `audio/webm` (ffmpeg); `screenshot` is `UploadFile | str` — v2 JPEG file part or v1 base64 PNG text field; `/query` also takes an optional `context` text field (JSON screen context: app, window_title, window, cursor, ≤200 `elements` with `id`/role/name/normalised box/`enabled`/`focused`; ids = set-of-marks numbers drawn on the screenshot; ≤200 KB, malformed/oversized → ignored with a warning, never an error). Caps: audio 10 MB, screenshot 8 MB → 413. Errors: 400 undecodable audio/image, 422 empty transcript, 429/502 STT provider, 502 vision. `detail` strings are shown in the desktop UI — keep them human-readable. Services take raw image bytes; PIL work runs in `asyncio.to_thread`.
- **Grounding (Phase 3)** lives in `services/grounding.py`: `parse_screen_context` (lenient — clips to 0–1, drops bad elements one by one), `render_context` (compact element list for the prompt, `[12] button "Save" @.10,.20 .05x.03 (focused)`; disabled omitted, ids never renumbered; ~20 tokens/element) and `resolve_targets`. Each response step has an optional `target {element_id, x, y, w, h}` (normalised): a valid id → the server copies the request element's box (model coordinates are never trusted); no id + bbox → clipped fallback with `element_id: null` (w/h ≤ 0 or > 0.9, or pixel values → rejected); unknown id → `null`. Marked screenshots are compressed to `VISION_MARKED_MAX_EDGE` (1280) instead of 768×432 so the mark numbers stay legible. Eval: `scripts/eval_grounding.py` + `eval/cases/` (format in `backend/eval/README.md`; the desktop dumps cases when `HOVERAI_FIXTURE_DIR` is set).
- TTS is time-boxed (`tts_timeout_s` per provider, Sahara then Orpheus) via `services/tts.py:speak`; on failure `speech_b64` is `""`, never an error.
- Conversations: one `Conversation` row per user; rolling summary every 12 unsummarised messages (`services/conversation.py`), with an in-memory fallback if the DB fails.
- Agent sessions (`services/agent.py`) are an **in-process dict** with a 5-minute TTL — only valid with a single worker.
- Language codes: Hover codes (`en-pidgin`, `yo`, `ha`, `ig`, `en`, `fr`, …) are mapped per provider in `stt.py` (`LANGUAGE_MAP`), `sahara_tts.py` (`SAHARA_VOICE_MAP`) and `vision.py` (`_LANGUAGE_CONFIGS` — prompt tone per language). Adding a language means touching all three plus `routers/languages.py`.
- Auth: 15-minute HS256 access JWT (PyJWT) + opaque 90-day refresh token stored in `refresh_tokens` and rotated on every `/auth/refresh` via an atomic `UPDATE … WHERE NOT revoked AND expires_at > now RETURNING` (a second use of the same token → 401). Passwords are limited to 72 UTF-8 bytes (bcrypt); signup → 422, signin → 401.
- Schema: owned by **Alembic** (`alembic/versions/`, async `env.py` reads `settings.database_url`). Nothing is created at app startup. Change a model → `uv run alembic revision --autogenerate -m "…"`, review it, and `uv run alembic check` must report no drift. FK `ondelete` and JSONB are declared on the models so autogenerate sees them. `0001_baseline` = the old SQL files (now `migrations/legacy/`, reference only); a pre-Alembic DB must be `alembic stamp 0001_baseline`'d once, not upgraded. Deploys don't auto-migrate — run `alembic upgrade head` from the Render Shell.
- CI: `.github/workflows/ci.yml` job `backend` runs `ruff check` + `pytest` on every push/PR. Ruff rules are pinned in `pyproject.toml` (`[tool.ruff.lint]`).
- Style: Google-style docstrings on every function, type hints everywhere, module docstrings explaining design decisions.

---

## `wakeword/`

Test a model on your voice with `./wake-test guided` (default `useful_output/hey_hover.onnx`, `--model` for another; no training setup or downloads; see `wakeword/README.md`). Training (run in the GitHub codespace, not on the dev PC): Python 3.12+, `uv`. `uv run python main.py` generates synthetic clips, trains, exports ONNX and evaluates (`useful_output/hey_hover_*.json`). Needs `uv run livekit-wakeword setup --data-dir ./data` first (large downloads). **Re-integrating into the app:** the old detector (`src-tauri/src/wake_word.rs`, `wake-word` Cargo feature, `livekit-wakeword` dep, settings toggle, `resources/hey_hover.onnx`) is in git at `3d80d86`; known issues to fix then: D12, D13, D27. The model's output key must stay `hey_hover`.
