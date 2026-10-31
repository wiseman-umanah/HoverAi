# HoverAI v2: Execution plan

Date: 2026-10-31. Status: proposal, no code changes yet.
Inputs:
- the v1 issues reported by the team (below),
- [`BUG_ANALYSIS.md`](BUG_ANALYSIS.md), where the D-/B- IDs come from,
- [`AGENTIC_PLAN.md`](AGENTIC_PLAN.md).

This document **sets the order of work**. Where its phases differ from the roadmap in `AGENTIC_PLAN.md`, this one wins. Machine setup is in [`DEV_SETUP.md`](DEV_SETUP.md).

Guiding decisions:
1. **Shortcut-only prototype first.** The wake word goes behind a build flag, off by default, and is iterated on separately later.
2. **Windows is the primary target. Linux (Hyprland/Wayland) is the development and test platform**, since that's this PC. macOS is built in CI only, until someone with a Mac can test.
3. **Rust owns the state; React only renders.** Most overlay bugs come from state being split across the two sides.

---

## 1. Root causes of the reported v1 issues

### Issue 1: Overlay problems ("Esc not responding", "quit but still running")

**Esc not responding**: four separate causes stack up:

| Cause | Evidence |
|---|---|
| Keyboard input depends on the overlay webview having OS focus. The overlay is created with `focus: false`, and Windows' foreground-lock rules often refuse `SetForegroundWindow` for a background process. The 80 ms `sleep` before `set_focus()` in `lib.rs` is a workaround for exactly this, and it's unreliable | `trigger_capture`, `focus_overlay`; results are shown with no focus call at all (D22) |
| The dismiss loop: `dismiss()` → `dismiss_overlay` → `capture-end` → `dismiss()` … forever. After the first Esc, later captures are hidden or cancelled by the loop | D1 |
| The full-screen overlay is made non-click-through when it shows results, so the screen looks frozen | D4 |
| A late result re-opens the overlay after Esc, and a recorder can be left running on a hidden window | D5, D6 |

**"Quit the app but it still runs; had to end it in Task Manager"**:

| Cause | Evidence |
|---|---|
| **Every** window's close button is intercepted and turned into `hide()`, including the onboarding window. "Closing" never quits | `on_window_event` → `prevent_close()` for all windows |
| The only real Quit is the tray menu's "Stop HoverAI". On Windows 11 the tray icon is usually hidden in the overflow (^) menu, so users never find it. There's no Quit in Settings either | `setup_tray` |
| No single-instance guard. Relaunching starts a *second* process, so you end up with several in Task Manager | D19 |
| Logging goes through `println!`/`eprintln!` only. A Windows release build has no console, so there's no record of what happened | — |
| **Needs a repro on Windows:** whether `app.exit(0)` itself can hang (e.g. the cpal/wake-word thread or ONNX Runtime during teardown). The plan below covers it either way with a watchdog | — |

### Issue 2: Screenshots too large → downscaling → the model guesses

- **Too large:** the screenshot is sent as base64 PNG in a *text* form field, which the server caps at 1 MB. 4K or busy screens get rejected with HTTP 400 (B1).
- **Downscaling made it worse:** at 768×432 (and 400×225 for the agent), menu text and icons become unreadable. The model then guesses positions from layout heuristics; `agent.py`'s prompt literally hard-codes "desktop icons are at x≈0.03–0.07". On top of that, the JSON cleanup turns `0.08` into `0.8` (B2).
- **How to understand the screen without guessing** (detail in §3, Phase 3). Give the model **structured facts first and pixels second**:
  1. **Context as text** (cheap, exact): active app/process, window title, cursor position, focused element.
  2. **Accessibility tree**: the OS already knows every button, menu and field, with its name, role and exact pixel bounds. On Windows that's UI Automation (very rich), on macOS AX, on Linux AT-SPI.
  3. **The model points at elements, not pixels**: it answers "element #17". The app looks up #17's real bounds, so the beacon (and later the click) lands exactly. Raw coordinates become a fallback only.
  4. **Images are smarter, not smaller**:
     - capture just the **active window** (or the monitor under the cursor),
     - send it as a JPEG/WebP *file* at the model's native resolution (long edge ~1280–1600 px, not 768),
     - draw numbered marks on the model's copy ("set-of-marks"),
     - offer a **zoom tool**, so the model can request a full-resolution crop of a region before committing.
  5. **On-device OCR** for apps with a poor accessibility tree (canvas apps, games, some Electron apps). Built-in OCR exists on Windows (Windows.Media.Ocr) and macOS (Vision).

### Issue 3: The wake word didn't work
- The detector reads the mic natively (cpal) while the overlay records through the webview (`getUserMedia`), so the two compete for the microphone.
- Sensitivity changes never apply, a dead detector can't be restarted, and one utterance can double-trigger and *close* the overlay it just opened (D12, D13).
- It also drags in **ONNX Runtime** (`livekit-wakeword` → `ort`/`ort-sys`), which downloads native binaries at build time. That's part of the build pain in Issue 4.
- **Decision:** move it behind a Cargo feature `wake-word` that's off by default, and hide its UI toggle. The prototype is shortcut-only. Revisit in Phase 5 with an offline test harness (a set of recorded WAVs → expected detections) so it can be tuned without manual testing.

### Issue 4: Only Windows worked; Intel Mac build failed; Linux very buggy

**Intel Mac:**
- **Root cause found:** `ort-sys` (pulled in by the wake word) downloads prebuilt ONNX Runtime binaries, and its download table (`ort-sys-2.0.0-rc.11/build/download/dist.txt`) has `aarch64-apple-darwin` but **no `x86_64-apple-darwin`**. The Intel build cannot get ONNX Runtime.
- The CI's `macos-13` Intel runner is also the slowest, oldest runner image.
- **Fix:** with the wake word feature-gated off, nothing needs ONNX Runtime. Build Intel and ARM together as `universal-apple-darwin` on the Apple Silicon runner, and drop `macos-13`.

**Linux** has several confirmed causes, all of which affect **this PC** (Hyprland/Wayland):

| Area | v1 implementation | Why it breaks on Wayland |
|---|---|---|
| Global shortcut | `tauri-plugin-global-shortcut` → `global-hotkey 0.8` | Linux backend is **X11-only** (`platform_impl/{windows,macos,x11}`). On Wayland it only fires while an XWayland window has focus, i.e. effectively never |
| Microphone | `getUserMedia` inside WebKitGTK | `wry 0.55` registers no media-permission handler on Linux. WebKitGTK denies or doesn't enable media capture by default, so recording is likely empty. To confirm in Phase 0 |
| Screenshot | `screenshots 0.8` (unmaintained, future-incompat warning) | On Wayland it tries the GNOME Shell D-Bus API and then the xdg portal. On Hyprland the portal may prompt or behave interactively |
| Overlay window | `set_position`, `set_size`, `set_always_on_top` | Wayland compositors ignore client positioning. Hyprland **tiles** new windows unless window rules float and pin them |
| Click-through | `set_ignore_cursor_events` | Implemented in tao via the GTK input region. Should work, but must be verified on Hyprland |

**Windows** worked, but most of the overlay bugs above are Windows-visible too.

---

## 2. Architecture changes that remove whole bug classes

1. **The overlay state machine lives in Rust.**
   - `Idle → Recording → Thinking → Showing → Error`, each with a `capture_id`.
   - The webview sends *intents* (`submit`, `cancel`, `ask_again`) and renders the state Rust pushes.
   - Stale results are dropped by id.
   - This fixes D1, D5, D6, D21 and D24 by construction.
2. **Keys don't depend on focus.**
   - While the overlay is active, Rust temporarily registers **Esc** = cancel as a global shortcut, and **pressing the trigger shortcut again = send** (toggle). They're unregistered on return to `Idle`.
   - Space/Enter are only used as a convenience when the overlay happens to have focus.
3. **Record audio in Rust (cpal)**, encoded as 16 kHz mono WAV/FLAC.
   - Removes `getUserMedia` (fixes the Linux webview mic and the mic contention with the wake word).
   - Removes the `number[]` IPC transfer.
   - Lets the backend **drop ffmpeg/pydub entirely**.
   - The waveform visualiser gets levels from Rust events.
4. **No full-screen overlay for the panel.** The steps panel and error card become a small window sized to its content. Beacons (Phase 3) get their own click-through layer.
5. **Request transport:**
   - multipart *files* for audio (WAV/FLAC) and screenshot (JPEG/WebP), resized client-side,
   - one `reqwest` client with timeouts,
   - a single-flight token manager in Rust (proactive refresh; clear only on 401/403). Fixes B1, D2, D3, D9, D14, D20 and the client side of B5.
6. **Lifecycle:**
   - `tauri-plugin-single-instance` (a second launch focuses the first, or forwards `--trigger`),
   - Quit in the tray and in Settings,
   - closing the onboarding window quits; once onboarded, closing hides, with a one-time "Hover is still running in the tray" notice,
   - an `ExitRequested` handler that unregisters shortcuts, stops audio and closes windows, with a 2 s watchdog that force-exits.
7. **A platform layer** (`src-tauri/src/platform/`), with one implementation each for Windows, macOS, Linux-X11 and Linux-Wayland, covering:
   - `shortcut`:
     - Wayland: the GlobalShortcuts portal via `ashpd`,
     - Hyprland: also supports a compositor keybind that runs `hoverai --trigger`, forwarded by the single-instance plugin,
   - `capture`: `xcap` (the maintained successor of `screenshots`); portal/grim fallback on Wayland,
   - `overlay placement`,
   - later `a11y` and `input`.
8. **Observability:**
   - `tauri-plugin-log` writing to a rotating file in the app's log dir, plus a "Copy diagnostics" button in Settings,
   - backend: a request id for every `/query` and per-stage timings.

---

## 3. Phases

Each phase ends with a **manual test checklist run on Windows and on Hyprland**. The overlay checklist is in §4.

### Phase 0: Machine setup and baseline (1–2 days)
- Follow [`DEV_SETUP.md`](DEV_SETUP.md): Postgres in Docker, Python 3.12 env, `pnpm tauri dev` running on Hyprland, Windows VM ready.
- Reproduce each Issue 1–4 symptom here and note it. Confirm or reject the "likely" items: the WebKitGTK mic, the portal screenshot behaviour on Hyprland, and click-through.
- Check the Render build logs: `render.yaml` runs `apt-get install ffmpeg`, which normally can't run on Render's native Python runtime (no root). If ffmpeg was missing in production, audio conversion failed and users got "empty transcript" 422s.

### Phase 1: Foundations (≈1 week)

**Backend (Python 3.12):**
- Target **Python 3.12** (decided 2026-10-31; 3.15 was dropped to avoid dependency issues). `requires-python = ">=3.12,<3.13"`, `.python-version` = `3.12`, fresh `uv.lock`. Use the same version in the Render Docker image (`python:3.12-slim`).
- Note: `audioop` still exists in 3.12, so `pydub` works today. Removing it is still planned because the client will send WAV/FLAC, and it breaks on any future upgrade to 3.13+.
- Replace or remove dependencies:

  | Package | Action | Reason |
  |---|---|---|
  | `python-jose` | → `PyJWT` | Unmaintained; `jose` has known CVEs in its deps |
  | `pydub` | → remove | It imports `audioop` (removed in Python 3.13), and once the client sends WAV/FLAC no conversion is needed. If one is, call `ffmpeg` via `asyncio.create_subprocess_exec` with a timeout |
  | `python-dotenv` | → remove | Unused; pydantic-settings reads `.env` itself |
  | `aiosqlite` | → move to dev group | Tests only |
  | `httpx` | → keep in main only | Listed twice (main + dev) |
  | `ruff` | → add (dev) | Lint + format |
  | — | Alembic → adopt | Replaces hand-numbered SQL with clashing numbers (B23) and `create_all` in production (B14) |

- Fix B1 (screenshot as `UploadFile` + size caps), B2, B3, B4, B6 and B7.
- Fix the test setup (T1/T2). Add tests for `/query` with mocked providers, the parsers, and the refresh race.
- Deploy with a **Dockerfile** (`python:3.12-slim`) on Render instead of the native runtime. That pins Python and makes system packages (ffmpeg) possible.

**Desktop:**
- ✅ Done 2026-10-31: wake word behind the `wake-word` Cargo feature (off by default, `pnpm dev:wake` to enable), UI toggles hidden; lean dev profile.
- Add single-instance, logging and the lifecycle/quit work (§2.6).
- Rust audio capture; multipart file transport; token manager; HTTP timeouts.
- Replace `screenshots` with `xcap`. Capture the monitor under the cursor (and record its scale factor) and send a resized JPEG.
- CI: run `tsc`, `cargo clippy`, `pytest` and `ruff` on every PR, not only on release tags.

**Exit criteria:**
- A query works end-to-end on Windows and on Hyprland (the latter via a Hyprland keybind → `hoverai --trigger`).
- No 400s on a 4K screenshot.
- Tray/Settings Quit leaves no process behind.
- A second launch doesn't create a second process.

#### Phase 1 status (2026-10-31): implemented, awaiting manual testing

Done:
- **Backend:**
  - Python 3.12 pinned, python-jose replaced by PyJWT, pydub and python-dotenv removed, ruff added.
  - Alembic `0001_baseline`; the old SQL moved to `migrations/legacy/`; `create_all` removed from startup.
  - Bugs fixed: B1, B2, B3, B4, B5 (atomic rotation), B6, B7, B10, B12, B15, T1, T2.
  - 76 tests pass. Dockerfile added and `render.yaml` switched to the Docker runtime. CI `backend` job added.
- **Desktop:**
  - Wake word behind a build flag.
  - Native mic recording (cpal, WAV); xcap capture of the monitor under the cursor (JPEG ≤1600 px); multipart file upload.
  - Single token manager: proactive refresh, single-flight, tokens cleared only on 401/403.
  - HTTP timeouts, single-instance with `--trigger`, file logging.
  - Quit from the tray and Settings, with a 2 s watchdog; closing the onboarding window quits.
  - Session state machine in Rust with session ids, which fixes D1, D5, D6, D21, D23 and D24.
  - Global Esc while the overlay is up; pressing the shortcut again sends.
  - Also fixed: D2, D3, D7, D8, D9, D10, D11, D14, D16, D18, D19, D20, D25, D26.
  - CI `desktop` job added.
- **Hyprland dev setup:** SUPER+SHIFT+H → `hoverai-desktop --trigger`, plus overlay window rules (both in `~/.config/hypr/`, blocks marked `hoverai-dev`).

Not done / carried over:
- **Phase 2:**
  - D4: the panel still sits in a full-monitor window that takes clicks while it's shown.
  - Wayland GlobalShortcuts portal, so Esc/shortcut work without the compositor keybind.
  - D17 (`e.code`-based shortcut capture).
- **Backend:**
  - Refresh-token family revocation on reuse (TODO).
  - B8, B9, B11, B13, B22.
  - `datetime.utcnow` deprecation in the models.
  - Docker image size (1.38 GB).
- **Databases:** existing databases built by the old `create_all` were stamped `0001_baseline` but differ slightly: FKs without `ON DELETE`, `json` instead of `jsonb`, one missing index. Either recreate them (dev) or add a `0002` fix-up migration (production).

### Phase 2: Overlay rewrite (≈1 week)
- The Rust state machine (§2.1), global Esc/toggle (§2.2), and a compact panel window (§2.4).
- Shortcut handling: register-new-then-unregister-old (D10), Rust owns `shortcut_key` (D11), `e.code`-based capture (D17), `test_shortcut` safe for the active key (D18).
- Onboarding/sign-out state fixes (D7, D8).
- Wayland backends: the GlobalShortcuts portal plus Hyprland window rules (float, pin, no focus-steal, no animation) for the overlay class. The rules go into your Hyprland Lua config when we get there.

**Exit criteria:** the §4 checklist passes 100% on Windows and on Hyprland.

#### Phase 2 status (2026-10-31): implemented, awaiting manual testing

Done (on top of what Phase 1 already delivered: the Rust state machine, global Esc, press-again-to-send, D7/D8/D10/D11/D18):
- **D4: the panel no longer blocks the screen.** Only the visible widgets take clicks.
  - Linux: GDK input region.
  - Windows/macOS: cursor poller with whole-window click-through.
  - The full-monitor overlay window stays, which Phase 3 beacons need.
- **D17:** shortcut capture uses `KeyboardEvent.code`, with per-platform Ctrl/Cmd/Super/Option handling (`src/lib/shortcuts.ts`). Settings now shows the shortcut that is actually registered when a change fails.
- **Wayland global shortcut via the XDG GlobalShortcuts portal.**
  - Host-app registration plus an auto-written `.desktop` entry.
  - Verified on Hyprland: `hyprctl globalshortcuts` lists `com.hoverai.desktop:capture`.
  - The onboarding page explains the Wayland/Hyprland binding.
- **Linux rendering:** coloured stripes over cards (WebKitGTK DMA-BUF renderer) fixed by defaulting `WEBKIT_DISABLE_DMABUF_RENDERER=1`.
- **Hyprland overlay rule** sizes the overlay to the monitor (Wayland ignores client positioning).
- **CI:** Windows clippy job, and `-D warnings` on clippy.

Still open:
- Global Esc on Wayland. The portal could expose a second "cancel" shortcut, but that would make GNOME/KDE prompt the user for Esc, so it's deferred. Esc works when the overlay has focus; the shortcut also toggles.
- The §4 checklist on Windows. It needs a Windows build from CI and a run on a Windows machine or VM.

### Phase 3: Perception v1 + beacons, Guide mode (≈2 weeks)
- The desktop collects:
  - active window title, process and cursor position,
  - a pruned accessibility element list: **Windows UI Automation first**, AT-SPI on Linux (GTK/Qt/Chromium/Electron apps expose it; some need `ACCESSIBILITY_ENABLED=1`).
- The screenshot is of the **active window**, with set-of-marks numbering. A `zoom(region)` tool returns a full-resolution crop when the model asks.
- The backend prompt and schema change so each step carries `target: {element_id} | {bbox}`. Move to **tool calling / JSON-schema output** instead of regex parsing.
- The overlay draws a **beacon** at the element's real bounds (physical → CSS pixels, per-monitor DPI) and advances when the screen or a11y state shows the step was done.
- Choose the vision model with a small eval set: 30 screenshots plus requests with known correct elements. Measure element-hit rate for the current Groq model vs. alternatives.

**Exit criteria:** at least 80% of steps highlight the correct element on the eval set; a beacon is visible on Windows and Hyprland.

#### Phase 3 status (2026-10-31): first increment implemented, awaiting testing + eval data

Done:
- **Desktop:**
  - Screen context: focused app and window, cursor, and accessibility elements. Uses AT-SPI on Linux and UI Automation on Windows (Windows is compiled but not yet run on a real machine).
  - All coordinates are normalised to the captured monitor.
  - Numbered marks are drawn on the model's copy of the screenshot.
  - The `context` field is sent with each query.
  - Overlay: a beacon on the current step's target (click-through), step selection, Back/Next, and arrow keys.
  - Fixture dumping via `HOVERAI_FIXTURE_DIR`.
- **Backend:**
  - `context` parsing.
  - Mark-aware prompt; Groq JSON mode with a fallback.
  - `target` per step, with `element_id` resolved to the element's box server-side and a vetted bbox fallback.
  - 1280 px marked screenshots.
  - `eval-grounding` harness plus a synthetic fixture. 134 tests pass.
- **Verified on this machine:**
  - Nautilus produced 32 marks that line up exactly with its controls (the Wayland window-relative offset is correct).
  - One real Groq call on the synthetic fixture picked the correct mark.

Not yet done (next increments):
- **Eval set (exit criterion ≥80 %).** Collect ~30 real cases with `HOVERAI_FIXTURE_DIR`, label `expected`, and run `uv run eval-grounding` against the current Groq model and alternatives.
- **Auto-advance** when the screen or accessibility tree shows the step was done (WS5 verifier). The user steps manually for now.
- **`zoom(region)` tool** for high-resolution crops, and on-device OCR for apps without an accessibility tree (Chromium/Electron, Zed, terminals).
- **Active-window-only capture.** The whole monitor is still sent.
- **Windows UIA validation** on a real Windows machine; macOS AX implementation.
- **Token budget.** About 19 tokens per element; 120 elements plus the image is roughly 4–5k tokens, so Groq free-tier rate limits are still a risk (see the backend notes).

### Phase 4: Builds and platform hardening (≈1 week, can overlap Phase 3)
- **Windows:** CI builds the NSIS/MSI installer on every PR as an artifact; installed and tested in the Windows VM. Optionally add code signing (unsigned installers trigger SmartScreen).
- **macOS:** CI builds `universal-apple-darwin` on the Apple Silicon runner (wake word off). Get one tester with a Mac and add the Accessibility + Screen Recording permission flow.
- **Linux:** AppImage + .deb from CI. Document X11 vs Wayland support. Officially support Hyprland/GNOME/KDE on Wayland via portals.
- Release workflow: drop `macos-13`; cache Rust and pnpm; release profile only on tags, `release-fast` for PR artifacts.

### Phase 5: Wake word iteration, then agentic
- **Wake word:**
  - an offline evaluation harness (WAV fixtures → expected detections),
  - fix D12/D13,
  - share the Rust audio capture from §2.3 (one mic owner),
  - evaluate `livekit-wakeword`'s `ort-tract` backend (pure Rust, no ONNX Runtime download) to restore Intel Mac and simplify builds,
  - turn the feature on per platform once it passes.
- **Agentic:** continue with [`AGENTIC_PLAN.md`](AGENTIC_PLAN.md) Phase 2 (Assist mode: executor, confirmation, kill switch) on top of the Phase 3 perception work.

---

## 4. Overlay test checklist (run on Windows and Hyprland every phase)

1. Trigger → recording widget appears within 300 ms, with a chime.
2. Esc while recording → cancels; the overlay is gone, the mic indicator is off, and other apps take clicks.
3. Trigger again while recording → sends the query.
4. Esc while "thinking" → cancels; the late result does **not** reappear.
5. Result panel shown → the rest of the screen is clickable, and Esc closes the panel even after clicking another app.
6. "Ask another" → the new screenshot does not contain Hover's own panel.
7. Trigger 10× rapidly → no stuck state, no duplicate panels, CPU idle afterwards.
8. Backend down or slow → an error card appears within the timeout and Esc closes it.
9. Tray → Quit and Settings → Quit → no HoverAI process remains (Task Manager / `pgrep hoverai`).
10. Launch twice → one process; the second launch focuses the first.
11. Sign out → onboarding starts at the sign-in screen; the shortcut no longer triggers.
12. Leave it idle for 20 min, then trigger → the query succeeds (token refreshed).
13. Unplug or disable the mic → a clear mic error, not "empty transcript".
14. Multi-monitor: trigger with the cursor on the second monitor → that monitor is captured and the panel appears there.

---

## 5. Open decisions for the team
1. Is the Hyprland keybind (`hoverai --trigger`) acceptable as the dev-time shortcut on Linux while the portal implementation lands?
2. Is there access to a real Windows PC for testing? A VM works but is tight on this machine's 7.7 GB RAM.
3. Who can test on a Mac (Apple Silicon)?
4. Render: OK to switch to Docker deploys? (Pins the Python version and fixes ffmpeg if we keep it.)
5. Vision model budget for Phase 3 evals: stay on the Groq free tier, or allow a paid tier / another provider?
