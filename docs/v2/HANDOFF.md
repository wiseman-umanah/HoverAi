# HoverAI v2 — Session handoff (2026-10-31)

Read this first in a new chat, then `AGENTS.md` (architecture + gotchas) and
`docs/v2/EXECUTION_PLAN.md` (phase order + per-phase status sections).

## State
- Phase 1–3 work is committed on `master` (`f7e9084`, `3d80d86`). Later: backend bug fixes B8/B9/B11/B13/B22 + `utcnow` (uncommitted at time of writing).
- Phases 1 and 2 are implemented; a first Phase 3 increment (grounding + beacons) is implemented. **None has been hand-tested by the user yet** (only automated tests + hardware smoke tests).
- Checks passing at handoff: desktop `pnpm typecheck`, `cargo clippy --all-targets -D warnings` (with and without `--features wake-word`), `cargo test --lib` (10 pass, 6 ignored hardware tests); backend `uv run pytest -q` (162 pass), `uv run ruff check app tests scripts`, `uv run pyright app` clean (0 errors).

## Docs (all in repo)
| File | What |
|---|---|
| `AGENTS.md` | Up-to-date architecture for desktop + backend; read before editing |
| `docs/v2/EXECUTION_PLAN.md` | Phase plan; "Phase N status" sections list done/open per phase |
| `docs/v2/BUG_ANALYSIS.md` | v1 bug list (B*/D* IDs referenced everywhere) |
| `docs/v2/AGENTIC_PLAN.md` | Target agentic architecture (later phases) |
| `docs/v2/DEV_SETUP.md` | Setting up this PC |
| `backend/README.md`, `backend/eval/README.md` | API/upload contract, eval harness |

## What was done (short)
- **Cleanup:** removed local-LLM/Ollama mode and other dead code; AGENTS.md rewritten (Tauri, not Electron).
- **Phase 1:** backend on Python 3.12, PyJWT, no pydub/dotenv, Alembic (`0001_baseline`), Docker deploy, upload contract (WAV + JPEG files, size caps, readable errors), atomic refresh rotation, CI. Desktop: wake word behind Cargo feature `wake-word` (off), native mic (cpal), xcap capture → JPEG, single token manager, single-instance + `--trigger`, file logs, real Quit, Rust-owned capture session state machine (fixed v1 Esc/dismiss loop/stale results).
- **Phase 2:** click-through except overlay widgets (GDK input region on Linux, cursor poller on Win/mac), `e.code` shortcut capture (`src/lib/shortcuts.ts`), Wayland global shortcut via XDG portal (`wayland_shortcut.rs`, writes `~/.local/share/applications/com.hoverai.desktop.desktop`), WebKitGTK stripe fix (`WEBKIT_DISABLE_DMABUF_RENDERER=1` in `main.rs`).
- **Phase 3 (increment 1):** `a11y/` (AT-SPI Linux, UIA Windows — Windows never run), `context.rs`, `marks.rs` (numbered set-of-marks), `context` form field, backend mark-aware prompt + JSON mode + `target` per step (`services/grounding.py`), overlay beacon + step navigation, fixture dump (`HOVERAI_FIXTURE_DIR`) + `uv run eval-grounding`. Verified: Nautilus → 32 correctly aligned marks.

## Machine / environment (user's PC)
- Omarchy Linux, Hyprland 0.56 **Wayland, Lua config**; 4 threads, 7.7 GB RAM; Intel Haswell iGPU. Windows is the product's primary target (no Windows machine verified yet).
- Python 3.12.15 (uv) for backend; Postgres 18 in Docker container `hoverai-pg` (`postgresql://hoverai:hoverai@localhost:5432/hoverai_dev`, migrated with Alembic).
- Hyprland config edits (blocks marked `hoverai-dev`): `~/.config/hypr/bindings.lua` SUPER+SHIFT+H → `…/target/debug/hoverai-desktop --trigger`; `~/.config/hypr/hyprland.lua` overlay float/pin/size rules. `hyprctl dispatch` needs Lua syntax: `hl.dsp.focus({ window = "address:0x…" })`.
- Run: `cd backend && uv run hover-dev` ; `cd hoverai-desktop && HOVERAI_FIXTURE_DIR=~/hover-fixtures pnpm tauri dev`. Logs: `~/.local/share/com.hoverai.desktop/logs/hoverai.log`. User's mic was muted at OS level (`wpctl set-mute @DEFAULT_AUDIO_SOURCE@ 0`).
- Builds: use `CARGO_BUILD_JOBS=3`, debug builds only; `CARGO_NET_OFFLINE=true` avoids network.

## Open items / next steps
1. User hand-tests Phases 1–3 (checklist: EXECUTION_PLAN §4 + Phase 3 test steps) and reports issues.
2. Phase 4 (CI packaging) written — see "Phase 4 status" in EXECUTION_PLAN; needs a push to run, then Windows VM testing of the artifact.
3. Phase 3 remaining: collect ~30 eval fixtures, measure grounding hit rate (target ≥80 %), auto-advance steps, zoom/OCR for apps without a11y, active-window-only capture, Windows UIA validation, macOS AX.
4. Deploy prerequisites: production DB `alembic stamp 0001_baseline` (+ a `0002` fix-up for old create_all drift), Render Docker deploy.
5. Phase 5: **wake word removed from the desktop app (2026-11-01)** after `wakeword/wake-test guided` on the user's voice caught only 6/12 even at the most sensitive setting (model trained on synthetic US-English voices only; the mic was also over-gained). Training dir + test harness kept; re-integration notes in AGENTS.md `wakeword/`. User does NOT want heavy local builds/downloads for it. Next: **5b agentic**.
6. **Auto-update** (2026-11-12): `updater.rs` + Settings → Updates; pubkey in `tauri.conf.json`; user must add repo secrets `TAURI_SIGNING_PRIVATE_KEY` (+ `_PASSWORD`) before the first `v*` tag. First release that can update itself = the first one built with this code; earlier installs need one manual reinstall.
7. Known open bugs: refresh-token family revocation, Wayland global Esc.

## Working preferences learned
- User is cost/rate-limit conscious: **avoid spawning subagents** unless clearly worth it; do work inline, keep tool output small.
- User wants plans before big changes and states phase scope; follow `EXECUTION_PLAN.md` order.
- **Never commit or push** — the user commits their own work.
