# HoverAI v2: Making Hover fully agentic

Status: plan / proposal, 2026-10-31. Companion to [`BUG_ANALYSIS.md`](BUG_ANALYSIS.md).
Fix the Critical items there first, since several of them (screenshot size limit, coordinate-corrupting regex) would break an agent loop on day one.

---

## 1. Where v1 actually is

| Capability | v1 state |
|---|---|
| Hear the user | ✅ Shortcut or "Hey Hover" wake word → record → Groq Whisper / Sahara |
| See the screen | ⚠️ One screenshot of the **primary monitor**, downscaled to 768×432 JPEG. No window/app metadata, no cursor position (the README claims it, but the code doesn't do it) |
| Decide | ⚠️ One-shot: the LLM writes a numbered text list of steps. The plan is never revised after the first look |
| Show (beacon) | ❌ Steps have no coordinates. The overlay is a text panel, not a beacon on the target element |
| Act | ❌ The desktop app cannot click, type or press keys. No input-injection code exists |
| Agent loop | ⚠️ Backend has `/agent/start` + `/agent/turn` (ReAct, one action per turn, normalised x/y/w/h), but the desktop app never calls it. Sessions live in an in-process dict, and coordinates are corrupted by `_extract_json` (see bug B2) |
| Verify | ❌ `action_result` (`success / no_change / error`) is expected from the client, but nothing computes it |
| Memory | ⚠️ Text-only rolling conversation summary per user. No task memory or app knowledge |
| Safety | ❌ No confirmation, kill switch, sensitive-app guard, or action audit |

So "fully agentic" means building the **act → observe → verify** half on the desktop, and upgrading **perception** so the model can target elements reliably.

---

## 2. Target experience

Three user-selectable autonomy modes (a setting, and also switchable by voice: "just show me" / "do it for me"):

1. **Guide**: today's behaviour, with real beacons. Hover highlights the exact element for each step, waits for the user to perform it, detects that the screen changed, then moves to the next step.
2. **Assist** (default for v2): Hover shows the beacon plus "I'm going to click *Export*. OK?" and acts on Enter/"yes". Risky actions always ask.
3. **Autopilot**: Hover performs the whole task, narrating progress. Interruptible at any moment by Esc, the shortcut, or saying "stop". Risky actions still ask.

The core loop is the same in all three modes. Only the "who executes the action" step changes.

---

## 3. Architecture

```
┌──────────────────────── Desktop (Tauri) ────────────────────────┐
│  Trigger (shortcut / wake word / "Ask another")                 │
│        │                                                        │
│  Perception  ── screenshot (all monitors, DPI-aware)            │
│              ── accessibility tree (UIA / AX / AT-SPI)          │
│              ── active window, app name, cursor pos             │
│        │  observation{ image, elements[], window, monitors }    │
│        ▼                                                        │
│  Agent client  ◀──── WebSocket /agent/session ────▶  Backend    │
│        │  action{ type, target_element_id | bbox, ... }         │
│        ▼                                                        │
│  Safety gate (mode, risk class, blocklist, confirm UI)          │
│        ▼                                                        │
│  Overlay beacon  ──► Executor (enigo / OS APIs)                 │
│        ▼                                                        │
│  Verifier (screen diff + a11y diff) → action_result             │
└─────────────────────────────────────────────────────────────────┘
┌──────────────────────── Backend (FastAPI) ──────────────────────┐
│  Session store (Postgres/Redis, user-bound, resumable)          │
│  Planner/actor LLM with tool calling (structured, no regex)     │
│  Grounding: element-ID selection; bbox fallback                 │
│  TTS streaming, STT, conversation + task memory, audit log      │
└─────────────────────────────────────────────────────────────────┘
```

### Key decisions

**D1. Keep the loop client-driven, but move it to one WebSocket.**
The desktop owns the screen and the input, so it has to drive. Replace `POST /agent/turn` round-trips with a single WebSocket per task. Each turn then costs one LLM call, not HTTP setup + auth + multipart parsing. The socket also lets the server push progress text and streamed TTS, and lets the client send "user interrupted" mid-turn.

**D2. Ground on elements, not on raw pixel guesses.**
Vision LLMs are poor at outputting precise coordinates (the v1 agent prompt already contains hand-tuned "desktop icons are at x≈0.03–0.07" hacks). Instead:
1. The desktop collects interactive elements from the OS accessibility API: name, role, bounds, enabled/focused state. If a11y data is thin (Electron apps, canvases, games), it falls back to OCR text boxes.
2. It sends the element list and draws numbered marks on the screenshot ("set-of-marks").
3. The model answers `click(element_id=17)`. The desktop resolves the ID to exact bounds, so the beacon and the click are pixel-accurate.
4. A raw `bbox` stays as a fallback for things with no element (e.g. a spot on a canvas).

**D3. Real tool calling, not regex-parsed JSON.**
Define the actions as tools/function schemas (OpenAI-compatible `tools`, which Groq supports, or a provider with native computer-use tools). This removes `_extract_json`, the retries, and the class of bugs behind B2/B11.

**D4. Pick the model for grounding + tool use, and abstract the provider.**
The current `qwen/qwen3.8-27b` on Groq's free tier has a 7k input-tokens-per-minute (ITPM) limit, which forces 400×225 screenshots. That is too small to read UI text. Add a thin `LLMProvider` interface (`services/llm_client.py` is the natural home) and evaluate:
- a frontier model with native computer-use / tool support,
- a GUI-grounding-specialised open model (e.g. UI-TARS / Qwen-VL grounding variants) if self-hosting,
- Groq on a paid tier for speed.

Choose with the eval harness (§7), not by feel. Budget for higher-resolution crops: send a full low-res frame plus a high-res crop around the region of interest.

**D5. Persist sessions.** Store agent sessions in Postgres (or Redis if it gets added) keyed by `session_id` **and `user_id`**. That makes them survive restarts and multi-worker deploys, and lets a task resume after reconnect.

---

## 4. Workstreams

### WS1: Desktop executor (Rust) *(new)*
- Add the `enigo` crate (cross-platform mouse/keyboard) behind an `executor.rs` module. Commands: `move`, `click{button,count}`, `type_text`, `key_combo`, `scroll`, `drag`.
- **Coordinate mapping**: normalised or element bounds → physical pixels per monitor, accounting for DPI scale (`screenshots::Screen.display_info.scale_factor`) and monitor origin offsets. Unit-test this mapping; it's the #1 source of "clicked the wrong place".
- Before injecting, make the overlay click-through (`set_ignore_cursor_events(true)`) and keep it from stealing focus, otherwise the click lands on the overlay. Restore the previous foreground window focus before typing.
- Higher-level actions that are more reliable than clicks: `open_app(name)`, `open_url(url)`, `focus_window(title)`, `hotkey(...)` (via `tauri-plugin-opener` / OS APIs).
- Platform constraints to design around:
  - **macOS**: needs the Accessibility permission (`AXIsProcessTrusted`) for input injection. Add it to the onboarding Permissions page next to Screen Recording.
  - **Windows**: UIPI blocks injecting into elevated (admin) windows. Detect it and tell the user.
  - **Linux/Wayland**: global input injection and screenshots are restricted (they need the `xdg-desktop-portal` RemoteDesktop/ScreenCast portals, or `ydotool`). The `screenshots` crate and `enigo` work on X11. Decide whether Linux v2 is X11-only or portal-based, which matters because the dev machine runs Hyprland/Wayland.

### WS2: Perception (Rust) *(new)*
- **All monitors**: capture the monitor under the cursor (or all monitors), and record each monitor's bounds and scale.
- **Context**: active window title, process/app name, cursor position. These are cheap and greatly help the model.
- **Accessibility tree** per OS:
  - Windows UI Automation (`uiautomation` / `windows` crates)
  - macOS AXUIElement (`accessibility` / objc bindings)
  - Linux AT-SPI (`atspi` crate)
  - Return a pruned list of interactive, on-screen elements with stable IDs and bounds.
- **OCR fallback**: Windows.Media.Ocr / macOS Vision, or a bundled ONNX OCR model, for apps with poor a11y.
- **Set-of-marks renderer**: draw numbered boxes on the screenshot copy sent to the model (never on the user's screen).
- **Privacy**: blur/omit password fields (a11y exposes `isPassword`) and allow per-app opt-out.

### WS3: Overlay: beacons and agent UI (React)
- **Beacon component**: an animated ring/box at element bounds (convert physical → CSS pixels with `devicePixelRatio`), with an arrow plus a label for the instruction. Multi-monitor needs one overlay window per monitor, or an overlay that moves to the active monitor.
- **Agent HUD**: current step, progress, "Stop" button, confirm/deny prompt, and a scrolling action log.
- New overlay states: `planning → awaiting_confirm → executing → verifying → done | needs_user | error`. Rewrite `OverlayWidget` as an explicit reducer or state machine. The v1 component already has state races (see the desktop bugs).
- Barge-in: wake word or shortcut during a task means pause and listen.

### WS4: Agent brain (backend)
- **Tool schema** (replaces `AgentAction`):
  `click(target)`, `double_click`, `right_click`, `type(text, target?)`, `key(combo)`, `scroll(direction, amount, target?)`, `drag(from, to)`, `open_app(name)`, `open_url(url)`, `focus_window(title)`, `wait(ms | until_text)`, `ask_user(question)`, `say(text)`, `done(summary, success)`.
  `target` = `{element_id}` | `{bbox}`.
- **Plan then act**: the first turn produces a short high-level plan, shown to the user in Guide/Assist modes. Each turn then picks the next action given the plan, history and observation, and can revise the plan.
- **Context management**: keep the last observation image, older turns as text (action + verified result + key a11y deltas), plus the plan. This generalises v1's `_prune_history`.
- **Stopping**: `MAX_AGENT_TURNS` is 6 today, which is too low for real tasks. Make it ~25, with loop detection (same action + no change ×2 → change strategy or `ask_user`).
- **Language**: keep the per-language tone prompts from `vision.py` for `say` / `instruction` text. Tool arguments (e.g. text to type) must stay verbatim.
- **Streaming TTS** of `say` while the next action executes.
- Fold `/query` (Guide mode) and `/agent` into **one pipeline**. Guide is just autonomy=guide, so there's one prompt and one parser to maintain.

### WS5: Verification and recovery (desktop)
- After each action, wait for UI to settle (a11y events, or until two consecutive frames are equal, with a cap of ~1.5 s, instead of a fixed 600 ms), then compute:
  - a screen diff (perceptual hash / pixel delta in the target region),
  - an a11y diff (focused element changed, new window, element disappeared).
- Send a structured result: `{changed: bool, focus_changed, new_windows, error}`. This replaces the free-text `success / no_change / error`.
- Guide mode uses the same verifier to detect that *the user* completed the step and auto-advance.

### WS6: Safety and trust
- **Risk classes per action**, decided on the desktop (never trust the model alone). The following always require explicit confirmation, even in Autopilot:
  - sending messages/email,
  - deleting,
  - purchases or payments,
  - typing into password fields,
  - running terminal commands,
  - closing unsaved work,
  - system settings.
- **Blocklist / allowlist** of apps where Hover may act (default-deny: password managers, banking, Hover's own windows).
- **Kill switch**: Esc / shortcut / "stop" halts instantly. The executor checks a cancellation flag before every input event.
- **Audit log**: a local, user-viewable history of actions with thumbnails, stored server-side only with opt-in. Update the privacy policy and README claims accordingly.
- **Prompt-injection hygiene**: on-screen text is untrusted data. The system prompt must say so, and risky actions derived from on-screen instructions need confirmation.

### WS7: Memory and knowledge
- **Task memory** within a session: what's done, what failed.
- **User memory** (opt-in): preferred apps, frequent tasks, language mix.
- **App playbooks**: a small retrieval store of known-good procedures for top apps (Excel, Word, Figma, VS Code, Chrome, WhatsApp Desktop). Inject one when the active app matches. This is a cheap big win in reliability.

### WS8: Evals, observability, latency
- **Replay harness**: record (observation, action, result) traces from internal dogfooding, then replay them offline against prompt/model changes.
- **Task suite**: ~50 scripted tasks across target apps and languages (including Pidgin/Yorùbá/Hausa/Igbo voice input). Metrics: success rate, steps, wrong-click rate, time to completion, cost.
- **Per-turn latency budget**: target < 2.5 s from observation to action, made up of:
  - capture + a11y < 300 ms
  - upload < 200 ms (JPEG, not base64 PNG)
  - LLM < 1.5 s
  - execute + settle < 500 ms

  Instrument every stage, both client and server.

---

## 5. Prerequisites from the bug analysis

Fix these before starting WS1:
- the screenshot transport: JPEG, sent as a file part, size-capped (bug B1 / D-series),
- the `_extract_json` regexes that corrupt coordinates (B2),
- overlay click-through / focus state bugs (desktop findings),
- blocking calls on the event loop (B4),
- the DB session lifetime in `/query` and the agent routes (B6),
- token refresh race (B5 + desktop dual refresh).

---

## 6. Phased roadmap

| Phase | Goal | Contents | Exit criteria |
|---|---|---|---|
| **0. Stabilise v1** (1–2 wks) | v1 reliable | Critical/High bugs; screenshot transport; tests for `/query` + parsing; CI typecheck + `cargo check` + pytest | No 400s on 4K screens; crash-free sessions |
| **1. Beacons (Guide mode)** (2–3 wks) | Show, don't tell | WS2 (multi-monitor, window context, a11y on Windows first), set-of-marks, element-ID grounding in `/query`, WS3 beacon, WS5 verifier for auto-advance | ≥80% of steps highlight the correct element on the task suite |
| **2. Assist mode** (3–4 wks) | Hover acts with confirmation | WS1 executor (Windows first), WebSocket session, tool-calling schema, persisted sessions, WS6 confirmation + kill switch + audit | ≥70% task success with ≤1 user correction |
| **3. Autopilot** (3–4 wks) | Multi-step autonomy | Plan-then-act, loop detection, streaming TTS, barge-in, app playbooks, macOS executor + AX tree | ≥60% end-to-end success on suite; p50 turn < 2.5 s |
| **4. Breadth** (ongoing) | Platforms + memory | Linux (X11 / portals), user memory, more languages, model/provider tuning via evals | — |

Windows comes first because v1 already ships there, and UI Automation gives the richest a11y tree.

---

## 7. Open questions for the team
1. Default autonomy mode for new users: Guide or Assist?
2. Model/provider: stay on Groq (cost/speed) or move the agent to a stronger computer-use-capable model (accuracy)? Should we fund a paid tier?
3. Linux target for v2: X11-only, Wayland via portals, or defer?
4. Server-side storage of action logs and screenshots: off entirely, or opt-in for quality improvement? The current privacy copy promises nothing is stored.
5. Should Autopilot ever run without the overlay visible (background tasks)? Recommendation: no, not for v2.
