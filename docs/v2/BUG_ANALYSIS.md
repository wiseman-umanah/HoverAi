# HoverAI v1: Bug analysis and fix plan

Date: 2026-10-31. Scope: `hoverai-desktop/` (Tauri + React) and `backend/` (FastAPI).
Method: static code review (no runtime repro). The backend tests could not be run because `backend/.venv` points at a Python 3.11 interpreter that is no longer installed (`uv sync` will rebuild it).
Line numbers are against the working tree as of this document.
**Confirmed** = verified by reading the code (and, where noted, reproduced in isolation). **Plausible** = depends on platform or library behaviour not verified locally.

The top 6 items explain most of the "v1 is buggy" reports: queries failing on big screens (B1), the overlay getting stuck or flickering (D1), queries failing 15 minutes after launch (D2), being forced to re-onboard (D3), the screen being blocked by the overlay (D4), and the agent clicking the wrong place (B2).

---

## Critical

### B1. Large screenshots → HTTP 400 before any handler code runs. **Confirmed**
`backend/app/routers/query.py:56`, `routers/agent.py:53,125`; client `hoverai-desktop/src-tauri/src/lib.rs` (`run_query_pipeline`)
- `screenshot: str = Form(...)` is a text form field. Starlette caps a non-file part at **1 MB** (`max_part_size=1024*1024` in `starlette/requests.py`), and FastAPI doesn't raise it.
- The desktop sends a full-resolution PNG as base64 text.
- **Failure:** any HiDPI/4K or visually busy 1080p screen produces more than 1 MB of base64, and the response is `400 Part exceeded maximum size`.
- **Fix:**
  - Client: downscale and encode as JPEG (the server shrinks it to 768×432 anyway) and send it as a **file part**.
  - Server: accept `screenshot: UploadFile`, with an explicit size cap.

### B2. Agent JSON "cleanup" corrupts coordinates and strings. **Confirmed (reproduced)**
`backend/app/services/agent.py:258,260`
- The regexes strip leading zeros anywhere: `{"x": 0.08, "w": 0.05, "text": "call 0803 at 10:05"}` becomes `{"x": 0.8, "w": 0.5, "text": "call 803 at 10:5"}`.
- **Failure:** every small coordinate is off by 10×, so the agent always misses its target.
- **Fix:** delete both regexes. Longer term, use structured tool calls (see AGENTIC_PLAN D3).

### D1. Dismissing the overlay starts an endless dismiss loop. **Confirmed**
`hoverai-desktop/src/overlay/OverlayWidget.tsx:79,128`, `src-tauri/src/lib.rs:504` (`dismiss_overlay`), and also `trigger_capture`
- `dismiss()` → `invoke('dismiss_overlay')` → Rust hides the overlay and emits `capture-end` → `onCaptureEnd` → `dismiss()` → … with no guard.
- **Failure:** after the first Esc or ×, the IPC loops forever, burning CPU. Later captures get hidden or cancelled by the in-flight loop, so the overlay flickers and recordings die.
- **Fix:**
  - Rust: don't emit `capture-end` back to the overlay when the overlay itself requested the dismiss (split the command, or skip the emit when already hidden).
  - Renderer: make `dismiss` idempotent (guard with a ref).

### D2. Queries fail once the access token is older than 15 minutes. **Confirmed**
`src-tauri/src/lib.rs:157` (`run_query_pipeline`); backend `jwt_expire_minutes = 15`
- The pipeline sends the stored access token and never refreshes. A 401 is shown to the user as `Query failed: …`. It only recovers if the user happens to open Settings, since `api.ts` refreshes there.
- **Fix:** before building the form, refresh if `jwt_expiry(access) < now + 60`. On a 401, refresh and resend (the audio and screenshot bytes are still in hand).

### D3. Startup discards the session instead of refreshing it. **Confirmed**
`src-tauri/src/lib.rs:795`
- An expired access token causes `tokens::clear` and full onboarding, even though a 90-day refresh token exists.
- **Failure:** every launch more than 15 minutes after the last refresh (e.g. every morning with autostart) means sign in, permissions and shortcut again.
- **Fix:** try a refresh. Clear tokens only if the refresh returns 401/403.

---

## High

### D4. The steps panel and error card block the entire monitor. **Confirmed**
`lib.rs` (`trigger_capture`, `run_query_pipeline`, `show_overlay_for_error`)
- The overlay window is full-screen and is set to `set_ignore_cursor_events(false)` when it shows results. CSS `pointer-events: none` does not make an OS window click-through.
- **Failure:** the user can't perform the step they were just told to do.
- **Fix:** shrink the window to the panel bounds, or toggle ignore-cursor-events on panel mouseenter/mouseleave.

### D5. Recorder orphaned on a hidden window; mic stays on. **Confirmed**
`OverlayWidget.tsx:42` (`startRecording`), the capture-start handler
- `capture-end` can arrive while `getUserMedia` is pending. The recording then starts on a hidden window. `startRecording` also never stops a previous stream, so it leaks.
- **Fix:** keep a capture-generation counter and check it after the await. Stop any existing stream first.

### D6. A stale result pops up after the user cancels. **Confirmed**
`lib.rs` (`run_query_pipeline` shows the overlay on result), `OverlayWidget.tsx:134`
- Esc during loading hides the overlay. The late result re-shows it, or overwrites a new recording that is in progress.
- **Fix:** give each query an id in `AppState` and drop results whose id doesn't match. Also accept `query-result` only while in the `loading` state.

### D7. Sign-out leaves stale state behind. **Confirmed**
`src/App.tsx:9`, `lib.rs:570` (`sign_out`)
- The main window is hidden, not reloaded, so `step` stays `'shortcut'` and the user lands on the shortcut page with no tokens.
- The wake-word detector keeps running, so "Hey Hover" still opens the overlay and every query returns 401.
- **Fix:** emit a `signed-out` event that resets onboarding to `'auth'`, and stop the wake word in `sign_out`.

### D8. Quitting mid-onboarding leaves an "invisible" app. **Confirmed**
`lib.rs:753` (`restore_shortcut_on_startup`)
- A valid token is enough to skip onboarding even if no shortcut was ever chosen. The user is then left with a tray icon only.
- **Fix:** skip onboarding only if the token is valid **and** onboarding completed (`shortcut_key` non-empty or an explicit flag).

### D9. Any non-2xx from `/auth/refresh` logs the user out. **Confirmed**
`lib.rs:310`, `http_client.rs:83`
- A Render cold-start 502/503 wipes the tokens.
- **Fix:** clear tokens only on 401/403.

### B3. Passwords over 72 bytes → 500. **Confirmed (bcrypt 5 raises)**
`backend/app/auth.py:43,56`, `schemas/auth.py:21`
- bcrypt 5 raises instead of truncating. The schema allows 128 characters, and multibyte characters push the byte length further.
- **Fix:** validate the byte length (`<= 72`) in both signup and signin, or pre-hash with SHA-256 before bcrypt.

### B4. Blocking work on the event loop. **Confirmed**
`routers/query.py:93`, `routers/agent.py:93`, `services/agent.py:365,424`
- `convert_to_wav` (an ffmpeg subprocess via pydub) and the agent's PIL `_compress` run synchronously in async handlers.
- **Failure:** on the single Render worker, every request stalls all the others.
- **Fix:** wrap both in `asyncio.to_thread(...)`.

### B5. Refresh-token rotation race and no reuse detection. **Confirmed**
`routers/auth.py:229-254`
- Read, check, then revoke is not atomic, so two concurrent refreshes both succeed.
- **Interaction with the desktop:** the desktop has **two** refresh paths (Rust `http_client.rs` / `refresh_tokens`, and renderer `api.ts`), which can fire together. Two refreshes with the same token log the user out after one of them rotates it.
- Reusing a revoked token isn't treated as theft.
- **Fix:**
  - Server: an atomic `UPDATE … SET revoked=true WHERE token=:t AND NOT revoked RETURNING …`. On reuse of a revoked token, revoke all of that user's/device's tokens.
  - Client: a single-flight refresh in Rust (a mutex around refresh). The renderer should always go through it (it already calls `window.api.refreshTokens`, so the only change needed is serialising inside Rust).

### B6. DB session commits after the response and is held through the whole pipeline. **Confirmed**
`app/db.py` (`get_session` commits after `yield`), `routers/query.py`, `routers/agent.py`
- FastAPI runs dependency teardown after sending the response.
- Auth routes can return tokens whose commit later fails.
- `/query` holds a pooled connection for the entire STT, vision and TTS run plus the background tasks. About 5–7 concurrent queries exhaust the pool.
- **Fix:** commit explicitly in the auth routes before returning. In `/query` and the agent routes, read settings with a short-lived `AsyncSessionLocal()` instead of `Depends(get_session)`.

---

## Medium

| ID | Where | Problem | Fix |
|---|---|---|---|
| D10 | `lib.rs:431` `do_register_shortcut`, `SettingsPage.tsx` | The old shortcut is unregistered *before* the new one is registered. On failure the user has no shortcut, the UI still shows the old one, and the result is ignored | Register the new one first, then unregister the old. Roll back on failure and surface the error |
| D11 | `SettingsPage.tsx` → `save_settings` | Rust writes `shortcut_key`, but the renderer's copy is stale. The next toggle saves the whole struct and reverts the shortcut on restart | `save_settings` preserves `shortcut_key` (Rust owns it), or update local state |
| D12 | `wake_word.rs`, `lib.rs` `apply_wake_word_setting` | The sensitivity threshold is read only at detector start. A detector that died leaves `Some(tx)`, so it can't be re-enabled. A cpal stream error just logs and the loop spins forever | Restart on sensitivity change, clear the slot when the thread exits, treat stream errors as fatal |
| D13 *(plausible)* | `wake_word.rs:32` | The cooldown (1.5 s) is shorter than the time the phrase stays in the window (2 s + hop), so it can double-trigger. The second trigger *dismisses* the overlay the first opened | Cooldown ≥ 2.5 s, clear the window after a detection, ignore wake triggers while the overlay is visible |
| D27 | `wake_word.rs` `sensitivity_to_threshold` | Comments say sensitivity 5 → 0.41 (the eval's optimal threshold) but the formula gives 0.48, so the default is stricter than intended | Choose the mapping from `wakeword/wake-test guided` results on real voices |
| D28 | `lib.rs` `hide_overlay` (Linux) | App aborted at the end of onboarding: `launch_overlay` hid the never-shown overlay and called `set_ignore_cursor_events(true)`; tao 0.35 unwraps the missing GdkWindow inside a GTK callback → `panic_cannot_unwind` → SIGABRT | **Fixed 2026-11-01:** `hit_region::set_click_through` (guarded GDK input region on Linux) |
| D14 | `lib.rs:635` `Client::new()` | No HTTP timeout, so a hung backend leaves the overlay "loading" forever | `Client::builder().timeout(60s)`, with a shorter timeout for the startup probe |
| D15 | `SettingsPage.tsx` | Server settings are merged into the UI but never saved or applied locally. On a new machine the wake-word toggle shows ON while it's actually off | Persist and apply after the merge |
| D16 *(plausible)* | `lib.rs` `trigger_capture` | `screenshots` `DisplayInfo` is in points on macOS but is used as a `PhysicalSize`, so on Retina the overlay covers a quarter of the screen | Use Tauri's `primary_monitor()` / `current_monitor()` sizes |
| D17 | `ShortcutPage.tsx`, `SettingsPage.tsx` `formatCombo` | Uses `e.key`, so Ctrl+Shift+1 becomes `!` and Option+S becomes `ß`, which are rejected as "taken". Ctrl is mapped to Cmd on Mac | Build the combo from `e.code` and keep Ctrl and Meta distinct |
| D18 *(plausible)* | `lib.rs:415` `test_shortcut` | Testing the currently active key may unregister it | Return true early when the key equals `active_shortcut` |
| D19 | `lib.rs` builder | No single-instance guard. A relaunch gives two tray icons, two wake-word detectors and shortcut conflicts | `tauri-plugin-single-instance` |
| D20 | `http_client.rs:37` | The retry copies the original headers (including the old `Authorization`) and then `bearer_auth` **appends** a second one (confirmed in reqwest 0.12 `header_sensitive` → `append`). The server reads the stale token, so the startup probe clears tokens right after a successful refresh | Remove `AUTHORIZATION` from the copied headers |
| B7 | `routers/query.py:93-109`, `services/stt.py` | WAV conversion failure → empty bytes sent to Whisper. Provider errors and rate limits are swallowed to `""` and all reported as 422 "empty transcript" | 400 on bad audio. Raise provider errors and map them to 502/503/429 |
| B8 | `services/conversation.py:170,209` | Summarises at 12 messages, folding 8, but sends only the last 4. Up to 6 messages are in neither the summary nor the context | Send all unsummarised messages (≤10), or summarise once the count exceeds `LIVE_WINDOW` — **Fixed 2026-10-31:** context now carries every unsummarised message (≤12). |
| B9 | `services/conversation.py` `save_turn` | Concurrent turns can summarise the same rows twice. The LLM call runs inside an open transaction | Row lock / advisory lock; claim rows with `UPDATE … RETURNING`; call the LLM outside the transaction — **Fixed 2026-10-31:** LLM call outside any transaction; batch claimed with `UPDATE … AND NOT summarised RETURNING`, duplicate discarded. |
| B10 | `services/sahara_tts.py`, `routers/query.py` TTS step | The default language (`en-pidgin`) always goes to Sahara. Worst case ≈65 s plus the Orpheus fallback, so a query can exceed 100 s | `asyncio.wait_for` with a total TTS budget (~8 s); tighter per-call timeouts |
| B11 | `services/agent.py:303-306,535` | `float(None)` or an unknown `action` raises outside the `try` → 502 and a leaked session. Invalid JSON is never retried. `bool("false")` is `True` | Validate inside the `try`, retry on parse errors, strict bool coercion — **Fixed 2026-10-31:** strict float/bool coercion, unknown action → corrective retry once, session dropped on failure. |
| B12 | `routers/query.py:91`, `routers/agent.py:91` | Unbounded `audio.read()` | Cap the size (e.g. 10 MB); body limit at the proxy/middleware |
| B13 | `schemas/users.py:80-82`, `PATCH /users/me` | No bounds on `overlay_opacity` / `mic_sensitivity`, and any `language` string is accepted | `Field(ge, le)`; validate against `LANGUAGE_MAP` — **Fixed 2026-10-31:** `Field(ge, le)` on opacity/sensitivity; `LanguageCode` validated against `LANGUAGE_MAP` on PATCH /users/me and signup. |

## Low

Desktop:
- **D21:** Alt+F4 / Cmd+W on the overlay hides it without cleanup, so the recorder keeps running.
- **D22:** Results are shown without focusing the overlay, so Esc may go to another app.
- **D23 (plausible):** "Ask another" screenshots include Hover's own panel.
- **D24:** A `getUserMedia` failure is swallowed, so the user sees "empty transcript" instead of a mic error.
- **D25:** The startup token probe has no timeout. On a cold backend, the main window can hide while the user is typing in sign-in.
- **D26:** `on*` listeners in `tauri-api.ts` leak if cleanup runs before `listen()` resolves (StrictMode / dev only).
- **D27 (plausible):** A partial keychain write leaves a stale refresh token that is preferred over the fallback file.

Backend:
- **B14:** `create_db_and_tables()` runs in production. Tables created that way differ from the migrations (no `ON DELETE`, no CHECKs, JSON vs JSONB), and `create_all` never adds new columns.
- **B15:** `/health` returns HTTP 200 `"status": "ok"` even when the DB is down (`api/v1/router.py:44`).
- **B16:** Emails aren't normalised (`John@x.com` ≠ `john@x.com`).
- **B17:** Signin timing reveals whether an account exists.
- **B18:** Refresh tokens are never pruned. Signin doesn't revoke the device's previous token.
- **B19:** `QueryLog.vision_response` / `beacon_steps` are typed `str` but store a dict/list.
- **B20:** Agent sessions aren't bound to a user. `/agent/start` ignores the user's saved language.
- **B21:** `/conversations/history` returns a random `conversation_id` when the user has none.
- **B22:** The Sahara streaming STT's `subprocess.run(ffmpeg)` blocks and has no timeout (feature flag off by default). **Fixed 2026-10-31:** uses the async, time-boxed `convert_to_wav` (WAV uploads skip ffmpeg).
- **B23:** Migration filename collisions (`002_*` ×2, `003_*` ×2), so the apply order is ambiguous.

Tests / tooling:
- **T1:** `pytest.ini` uses TOML syntax (`testpaths = ["tests"]`) inside an INI file. It should be `testpaths = tests`.
- **T2 (plausible):** `conftest.py` shares one aiosqlite session across event loops, and the in-memory SQLite has no `StaticPool`. This risks "different loop" / "no such table" errors.
- **T3:** No tests for `/query`, the agent parser (it would have caught B2), refresh races or summarisation. The desktop has no tests at all.
- **T4:** CI (`release.yml`) runs only the TS typecheck before building. Add `cargo clippy` / `cargo test` and the backend `pytest` + `pyright` on PRs.

---

## Fix plan

**Sprint 1: make v1 usable (≈1 week)**
1. B1 screenshot transport (client JPEG + file part, server `UploadFile` + cap).
2. D1 dismiss loop, D4 click-through, D5/D6 overlay state machine. Rewrite `OverlayWidget` state handling as a reducer with a capture/query id.
3. D2/D3/D9/D20 + B5: a single Rust token manager (proactive refresh, single-flight, clear only on 401/403), used by `/query`, startup and the renderer.
4. B2 delete the regexes; B4 `to_thread`; B6 session lifetime.
5. D14 HTTP timeout, B10 TTS budget, B7 error mapping. Together these make failures fast and well-labelled.

**Sprint 2: correctness and polish (≈1 week)**
6. D7/D8 onboarding/sign-out state; D10/D11/D17/D18 shortcuts; D12/D13 wake word; D19 single instance.
7. B3 password bytes, B8/B9 conversation memory, B11 agent parsing, B12/B13 validation.
8. T1/T2 fix the test setup. Add tests for B1/B2/B5/B8. Add a CI job for pytest, pyright, `cargo clippy` and `tsc`.

**Backlog:** the Low items, plus B14 (drop `create_all` in production and adopt Alembic, or a numbered migration runner).
