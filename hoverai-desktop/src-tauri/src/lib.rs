// ─── HoverAI Tauri backend ────────────────────────────────────────────────────
// All IPC is via #[tauri::command] (invoke) or app_handle.emit() (push events).
//
// Capture session (owned here, the overlay webview only renders it):
//
//   Idle ──trigger──▶ Recording ──submit / trigger──▶ Thinking ──result──▶ Showing
//    ▲                   │ cancel / Esc                  │ cancel / Esc        │ cancel / Esc / trigger
//    └───────────────────┴───────────────────────────────┴─────────────────────┘
//
// Every capture gets a new session id; results and errors for an older id
// are dropped, so a late response can never re-open a dismissed overlay.

mod a11y;
mod audio;
mod auth;
mod capture;
mod context;
mod hit_region;
mod marks;
mod settings;
mod tokens;
mod updater;
#[cfg(target_os = "linux")]
mod wayland_shortcut;

use std::sync::Mutex;
use std::time::Duration;

use reqwest::multipart::{Form, Part};
use reqwest::Client;
use serde::{Deserialize, Serialize};
use tauri::{
    menu::{MenuBuilder, MenuItemBuilder},
    tray::{TrayIconBuilder, TrayIconEvent},
    AppHandle, Emitter, Manager, WebviewWindow,
};
use tauri_plugin_global_shortcut::{GlobalShortcutExt, ShortcutState};

use auth::{AuthError, Authed};

// ─── API base URL ─────────────────────────────────────────────────────────────

pub fn api_base() -> String {
    // VITE_API_BASE is injected by build.rs reading hoverai-desktop/.env,
    // then emitting cargo:rustc-env=VITE_API_BASE=<value>.
    // option_env! picks that up at compile time. Falls back to localhost when
    // unset or empty (CI writes `VITE_API_BASE=` if the secret is missing).
    option_env!("VITE_API_BASE")
        .filter(|v| !v.is_empty())
        .unwrap_or("http://localhost:8000/api/v1")
        .to_string()
}

const QUERY_TIMEOUT: Duration = Duration::from_secs(90);
const STARTUP_PROBE_TIMEOUT: Duration = Duration::from_secs(8);
const ESCAPE: &str = "Escape";

// ─── Shared state ─────────────────────────────────────────────────────────────

#[derive(Clone, Copy, Debug, PartialEq)]
enum Phase {
    Idle,
    Recording,
    Thinking,
    Showing,
}

struct Session {
    id: u64,
    phase: Phase,
}

struct AppState {
    active_shortcut: Mutex<String>,
    http: Client,
    session: Mutex<Session>,
    /// Mic recording for the current capture session.
    recording: Mutex<Option<audio::Recording>>,
    /// Marked screenshot + screen context taken when the current capture started.
    screenshot: Mutex<Option<capture::MarkedScreenshot>>,
    /// Level-only mic stream for the onboarding microphone test.
    mic_monitor: Mutex<Option<audio::Recording>>,
    /// Interactive rectangles reported by the overlay (CSS px); see hit_region.rs.
    hit_regions: Mutex<Vec<hit_region::Rect>>,
}

fn state(app: &AppHandle) -> &AppState {
    app.state::<AppState>().inner()
}

// ─── Overlay window helpers ───────────────────────────────────────────────────

fn get_overlay(app: &AppHandle) -> Option<WebviewWindow> {
    app.get_webview_window("overlay")
}

fn get_settings_win(app: &AppHandle) -> Option<WebviewWindow> {
    app.get_webview_window("settings")
}

fn cursor_point(app: &AppHandle) -> Option<(i32, i32)> {
    app.cursor_position().ok().map(|p| (p.x as i32, p.y as i32))
}

/// The point xcap expects: physical pixels everywhere except macOS (points).
fn capture_point(app: &AppHandle, point: Option<(i32, i32)>) -> Option<(i32, i32)> {
    #[cfg(target_os = "macos")]
    {
        point.map(|(x, y)| {
            let scale = app
                .monitor_from_point(x as f64, y as f64)
                .ok()
                .flatten()
                .map(|m| m.scale_factor())
                .unwrap_or(1.0);
            ((x as f64 / scale) as i32, (y as f64 / scale) as i32)
        })
    }
    #[cfg(not(target_os = "macos"))]
    {
        let _ = app;
        point
    }
}

/// Cover the monitor under `point` (or the primary monitor) with the overlay.
/// Uses Tauri's monitor info, which is in physical pixels on every platform.
fn place_overlay(app: &AppHandle, overlay: &WebviewWindow, point: Option<(i32, i32)>) {
    let monitor = point
        .and_then(|(x, y)| app.monitor_from_point(x as f64, y as f64).ok().flatten())
        .or_else(|| app.primary_monitor().ok().flatten());
    if let Some(m) = monitor {
        let _ = overlay.set_position(tauri::Position::Physical(*m.position()));
        let _ = overlay.set_size(tauri::Size::Physical(*m.size()));
    }
}

async fn show_overlay_focused(overlay: &WebviewWindow) {
    let _ = overlay.set_always_on_top(true);
    let _ = overlay.set_ignore_cursor_events(false);
    hit_region::invalidate();
    // show() MUST come before set_focus() — on Windows the OS will not give
    // keyboard focus to a hidden window, and the short delay lets the
    // compositor finish showing it. Esc no longer depends on this focus
    // (it is a temporary global shortcut), it only helps Space/Enter.
    let _ = overlay.show();
    tokio::time::sleep(Duration::from_millis(80)).await;
    let _ = overlay.set_focus();
}

fn hide_overlay(app: &AppHandle) {
    if let Some(o) = get_overlay(app) {
        let _ = o.hide();
        hit_region::set_click_through(&o);
    }
}

// ─── Temporary global Escape ──────────────────────────────────────────────────
// While the overlay is up, Esc cancels it no matter which window has focus.

fn register_escape(app: &AppHandle) {
    let gs = app.global_shortcut();
    if gs.is_registered(ESCAPE) {
        return;
    }
    let handle = app.clone();
    let res = gs.on_shortcut(ESCAPE, move |_app, _shortcut, event| {
        if event.state() == ShortcutState::Pressed {
            let h = handle.clone();
            // Never touch shortcut registration from inside its own callback.
            tauri::async_runtime::spawn(async move { cancel_capture(&h) });
        }
    });
    if let Err(e) = res {
        log::warn!("[overlay] could not register global Escape: {e}");
    }
}

fn unregister_escape(app: &AppHandle) {
    let gs = app.global_shortcut();
    if gs.is_registered(ESCAPE) {
        let _ = gs.unregister(ESCAPE);
    }
}

// ─── Capture session ──────────────────────────────────────────────────────────

fn phase(app: &AppHandle) -> Phase {
    state(app).session.lock().unwrap().phase
}

/// True if session `id` is still current and in `expected` phase.
fn is_current(app: &AppHandle, id: u64, expected: Phase) -> bool {
    let s = state(app).session.lock().unwrap();
    s.id == id && s.phase == expected
}

/// Shortcut / `--trigger` / tray entry point. Toggles through the session.
pub async fn trigger_capture(app: AppHandle) {
    match phase(&app) {
        Phase::Idle => start_capture(app).await,
        Phase::Recording => submit_capture(app).await,
        Phase::Thinking | Phase::Showing => cancel_capture(&app),
    }
}

async fn start_capture(app: AppHandle) {
    let id = {
        let mut s = state(&app).session.lock().unwrap();
        if s.phase != Phase::Idle {
            return;
        }
        s.id += 1;
        s.phase = Phase::Recording;
        s.id
    };
    log::info!("[capture] #{id} start");
    if let Some(m) = state(&app).mic_monitor.lock().unwrap().take() {
        m.cancel();
    }

    // Screenshot BEFORE the overlay is shown, off the async runtime.
    let point = cursor_point(&app);
    let cap_point = capture_point(&app, point);
    let shot = tauri::async_runtime::spawn_blocking(move || capture::capture_with_context(cap_point))
        .await
        .unwrap_or_else(|e| Err(format!("capture task failed: {e}")));

    let Some(overlay) = get_overlay(&app) else { return };
    place_overlay(&app, &overlay, point);
    register_escape(&app);

    match shot {
        Ok(s) => *state(&app).screenshot.lock().unwrap() = Some(s),
        Err(e) => {
            log::error!("[capture] #{id} {e}");
            show_error(&app, id, format!("Couldn't capture your screen: {e}")).await;
            return;
        }
    }
    if !is_current(&app, id, Phase::Recording) {
        return; // cancelled while capturing
    }

    let app_for_mic = app.clone();
    let rec = tauri::async_runtime::spawn_blocking(move || audio::start(app_for_mic, true))
        .await
        .unwrap_or_else(|e| Err(format!("microphone task failed: {e}")));
    match rec {
        Ok(rec) => {
            if !is_current(&app, id, Phase::Recording) {
                rec.cancel();
                return;
            }
            *state(&app).recording.lock().unwrap() = Some(rec);
        }
        Err(e) => {
            log::error!("[capture] #{id} microphone: {e}");
            show_error(&app, id, format!("Microphone unavailable: {e}")).await;
            return;
        }
    }

    show_overlay_focused(&overlay).await;
    let _ = app.emit_to("overlay", "capture-start", ());
}

async fn submit_capture(app: AppHandle) {
    let id = {
        let mut s = state(&app).session.lock().unwrap();
        if s.phase != Phase::Recording {
            return;
        }
        s.phase = Phase::Thinking;
        s.id
    };
    log::info!("[capture] #{id} submit");
    let _ = app.emit_to("overlay", "capture-submitted", ());
    // While thinking only a small progress bar is drawn; let clicks through.
    if let Some(o) = get_overlay(&app) {
        hit_region::set_click_through(&o);
    }

    let rec = state(&app).recording.lock().unwrap().take();
    let wav = match rec {
        Some(r) => tauri::async_runtime::spawn_blocking(move || r.stop())
            .await
            .unwrap_or_else(|e| Err(format!("recording task failed: {e}"))),
        None => Err("not recording".into()),
    };
    let wav = match wav {
        Ok(w) => w,
        Err(e) => {
            log::error!("[capture] #{id} audio: {e}");
            show_error(
                &app,
                id,
                "I couldn't hear anything — check that your microphone is on and not muted.".into(),
            )
            .await;
            return;
        }
    };
    let Some(shot) = state(&app).screenshot.lock().unwrap().take() else {
        show_error(&app, id, "The screenshot for this question was lost — please try again.".into())
            .await;
        return;
    };

    run_query(app, id, wav, shot).await;
}

/// Cancel/dismiss from any phase. Idempotent; emits `capture-end` so the
/// overlay resets — the overlay must NOT call back into this on that event.
fn cancel_capture(app: &AppHandle) {
    let was = {
        let mut s = state(app).session.lock().unwrap();
        let was = s.phase;
        s.id += 1; // invalidates any in-flight query
        s.phase = Phase::Idle;
        was
    };
    if let Some(r) = state(app).recording.lock().unwrap().take() {
        std::thread::spawn(move || r.cancel());
    }
    state(app).screenshot.lock().unwrap().take();
    state(app).hit_regions.lock().unwrap().clear();
    unregister_escape(app);
    hide_overlay(app);
    let _ = app.emit_to("overlay", "capture-end", ());
    if was != Phase::Idle {
        log::info!("[capture] cancelled (was {was:?})");
    }
}

async fn show_error(app: &AppHandle, id: u64, message: String) {
    {
        let mut s = state(app).session.lock().unwrap();
        if s.id != id {
            return;
        }
        s.phase = Phase::Showing;
    }
    if let Some(r) = state(app).recording.lock().unwrap().take() {
        std::thread::spawn(move || r.cancel());
    }
    if let Some(o) = get_overlay(app) {
        show_overlay_focused(&o).await;
    }
    let _ = app.emit_to("overlay", "query-error", message);
}

// ─── Query pipeline ───────────────────────────────────────────────────────────

/// Where a step points on screen: normalised 0–1 to the captured monitor,
/// which is the monitor the overlay covers.
#[derive(Debug, Deserialize, Serialize, Clone)]
pub struct StepTarget {
    pub element_id: Option<u32>,
    pub x: f64,
    pub y: f64,
    pub w: f64,
    pub h: f64,
}

#[derive(Debug, Deserialize, Serialize, Clone)]
pub struct BeaconStep {
    pub step: u32,
    pub instruction: String,
    pub keys: Option<String>,
    pub tip: Option<String>,
    #[serde(default)]
    pub target: Option<StepTarget>,
}

#[derive(Debug, Deserialize, Serialize, Clone)]
pub struct QueryResponse {
    pub transcript: String,
    pub steps: Vec<BeaconStep>,
    pub summary: String,
    pub speech_b64: String,
    pub conversation_id: String,
}

/// Pull a readable message out of a FastAPI error body.
fn error_detail(body: &str) -> Option<String> {
    let v: serde_json::Value = serde_json::from_str(body).ok()?;
    match &v["detail"] {
        serde_json::Value::String(s) => Some(s.clone()),
        serde_json::Value::Array(items) => items
            .first()
            .and_then(|i| i["msg"].as_str())
            .map(str::to_string),
        _ => None,
    }
}

async fn run_query(app: AppHandle, id: u64, wav: Vec<u8>, shot: capture::MarkedScreenshot) {
    let client = state(&app).http.clone();
    let url = format!("{}/query", api_base());
    let jpeg = shot.jpeg;
    let context_json = serde_json::to_string(&shot.context).unwrap_or_default();
    log::info!(
        "[query] #{id} sending audio={}KB screenshot={}KB",
        wav.len() / 1024,
        jpeg.len() / 1024
    );

    let result = auth::send_authed(&app, &client, |token| {
        let form = Form::new()
            .part(
                "audio",
                Part::bytes(wav.clone())
                    .file_name("audio.wav")
                    .mime_str("audio/wav")
                    .expect("static mime"),
            )
            .part(
                "screenshot",
                Part::bytes(jpeg.clone())
                    .file_name("screen.jpg")
                    .mime_str("image/jpeg")
                    .expect("static mime"),
            )
            .text("context", context_json.clone());
        client.post(&url).bearer_auth(token).multipart(form).timeout(QUERY_TIMEOUT)
    })
    .await;

    let message = match result {
        Ok(Authed::Response(resp)) if resp.status().is_success() => {
            match resp.json::<QueryResponse>().await {
                Ok(q) => {
                    dump_fixture(&jpeg, &context_json, &q);
                    return deliver_result(&app, id, q).await;
                }
                Err(e) => {
                    log::error!("[query] #{id} parse error: {e}");
                    "The server sent a response I couldn't read — please try again.".to_string()
                }
            }
        }
        Ok(Authed::Response(resp)) => {
            let status = resp.status();
            let body = resp.text().await.unwrap_or_default();
            log::error!("[query] #{id} HTTP {status}: {body}");
            error_detail(&body).unwrap_or_else(|| format!("Query failed (HTTP {status})."))
        }
        Ok(Authed::Unauthorized) | Err(AuthError::Rejected) | Err(AuthError::NoSession) => {
            log::warn!("[query] #{id} session no longer valid");
            end_session(&app, false);
            AuthError::Rejected.user_message()
        }
        Err(e @ AuthError::Network(_)) => {
            log::error!("[query] #{id} {e:?}");
            e.user_message()
        }
    };
    show_error(&app, id, message).await;
}

/// Dev aid for the grounding eval set (backend/eval/README.md): when
/// `HOVERAI_FIXTURE_DIR` is set, save each query as a fixture case —
/// screen.jpg (as sent, with marks), context.json, and case.json with the
/// transcript; `expected` is left empty for a human to fill in.
fn dump_fixture(jpeg: &[u8], context_json: &str, resp: &QueryResponse) {
    let Some(root) = std::env::var_os("HOVERAI_FIXTURE_DIR") else { return };
    let stamp = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_millis())
        .unwrap_or_default();
    let dir = std::path::Path::new(&root).join(format!("case-{stamp}"));
    let case = serde_json::json!({
        "request": resp.transcript,
        "language": "",
        "expected": [],
        "observed": resp.steps,
    });
    let result = std::fs::create_dir_all(&dir)
        .and_then(|_| std::fs::write(dir.join("screen.jpg"), jpeg))
        .and_then(|_| std::fs::write(dir.join("context.json"), context_json))
        .and_then(|_| {
            std::fs::write(dir.join("case.json"), serde_json::to_vec_pretty(&case).unwrap_or_default())
        });
    match result {
        Ok(()) => log::info!("[fixture] saved {}", dir.display()),
        Err(e) => log::warn!("[fixture] could not save {}: {e}", dir.display()),
    }
}

async fn deliver_result(app: &AppHandle, id: u64, resp: QueryResponse) {
    {
        let mut s = state(app).session.lock().unwrap();
        if s.id != id || s.phase != Phase::Thinking {
            log::info!("[query] #{id} result dropped (session moved on)");
            return;
        }
        s.phase = Phase::Showing;
    }
    log::info!("[query] #{id} {} steps", resp.steps.len());
    if let Some(o) = get_overlay(app) {
        show_overlay_focused(&o).await;
    }
    let _ = app.emit_to("overlay", "query-result", &resp);
}

// ─── Overlay hit regions ──────────────────────────────────────────────────────

/// Rectangles that should take mouse input right now, or None when the
/// overlay is idle (Rust manages click-through itself then). While thinking
/// only the progress bar is drawn, so everything is click-through.
pub(crate) fn interactive_regions(app: &AppHandle) -> Option<Vec<hit_region::Rect>> {
    match phase(app) {
        Phase::Idle => None,
        Phase::Thinking => Some(Vec::new()),
        Phase::Recording | Phase::Showing => Some(state(app).hit_regions.lock().unwrap().clone()),
    }
}

// ─── Tauri commands ───────────────────────────────────────────────────────────

// ── Capture (from the overlay) ──

/// The overlay reports its interactive widgets after every layout change.
#[tauri::command]
fn overlay_hit_regions(app: AppHandle, rects: Vec<hit_region::Rect>) {
    *state(&app).hit_regions.lock().unwrap() = rects;
    if let (Some(regions), Some(overlay)) = (interactive_regions(&app), get_overlay(&app)) {
        hit_region::apply(&overlay, regions);
    }
}

#[tauri::command]
async fn capture_submit(app: AppHandle) {
    submit_capture(app).await;
}

#[tauri::command]
fn capture_cancel(app: AppHandle) {
    cancel_capture(&app);
}

/// "Ask another question": hide the panel first so the new screenshot shows
/// the user's screen, not HoverAI's own panel.
#[tauri::command]
async fn capture_again(app: AppHandle) {
    if phase(&app) != Phase::Showing {
        return;
    }
    cancel_capture(&app);
    tokio::time::sleep(Duration::from_millis(150)).await;
    start_capture(app).await;
}

// ── Microphone test (onboarding) ──

#[tauri::command]
async fn mic_monitor_start(app: AppHandle) -> Result<(), String> {
    if phase(&app) != Phase::Idle {
        return Err("busy".into());
    }
    if let Some(old) = state(&app).mic_monitor.lock().unwrap().take() {
        old.cancel();
    }
    let a = app.clone();
    let rec = tauri::async_runtime::spawn_blocking(move || audio::start(a, false))
        .await
        .map_err(|e| e.to_string())??;
    *state(&app).mic_monitor.lock().unwrap() = Some(rec);
    Ok(())
}

#[tauri::command]
fn mic_monitor_stop(app: AppHandle) {
    if let Some(rec) = state(&app).mic_monitor.lock().unwrap().take() {
        std::thread::spawn(move || rec.cancel());
    }
}

// ── Permissions ──

#[tauri::command]
async fn request_permission(app: AppHandle, id: String) -> String {
    let ok = match id.as_str() {
        // Opening the device natively triggers the macOS prompt and fails if
        // the OS has no usable input device.
        "microphone" => tauri::async_runtime::spawn_blocking(move || audio::probe(app))
            .await
            .map(|r| r.is_ok())
            .unwrap_or(false),
        // Taking a screenshot triggers the macOS screen-recording prompt.
        "screen" => tauri::async_runtime::spawn_blocking(|| capture::capture(None))
            .await
            .map(|r| r.is_ok_and(|shot| !shot.jpeg.is_empty()))
            .unwrap_or(false),
        _ => false,
    };
    if ok { "granted" } else { "denied" }.to_string()
}

// ── Token storage ──

#[tauri::command]
fn store_tokens(app: AppHandle, access: String, refresh: String) {
    tokens::store(&app, &access, &refresh);
}

/// A usable access token for the renderer's API calls (refreshed if near expiry).
#[tauri::command]
async fn get_access_token(app: AppHandle) -> Option<String> {
    let client = state(&app).http.clone();
    match auth::access_token(&app, &client).await {
        Ok(t) => Some(t),
        // Offline: hand back what we have and let the request decide.
        Err(AuthError::Network(_)) => tokens::get(&app).map(|(a, _)| a),
        Err(_) => None,
    }
}

#[tauri::command]
fn clear_tokens(app: AppHandle) {
    tokens::clear(&app);
}

/// Called by the renderer after a 401. `stale` is the token that was rejected.
#[tauri::command]
async fn refresh_tokens(app: AppHandle, stale: Option<String>) -> Option<String> {
    let client = state(&app).http.clone();
    auth::refresh(&app, &client, stale.as_deref()).await.ok()
}

#[tauri::command]
fn has_session(app: AppHandle) -> bool {
    auth::has_session(&app)
}

// ── Settings ──

#[tauri::command]
fn get_settings(app: AppHandle) -> settings::AppSettings {
    settings::load(&app)
}

#[tauri::command]
fn save_settings(app: AppHandle, s: settings::AppSettings) {
    // The shortcut is owned by the register_* commands; a stale copy in the
    // renderer must not overwrite it.
    let mut s = s;
    s.shortcut_key = settings::load(&app).shortcut_key;
    settings::save(&app, &s);
}

/// Toggle OS launch-at-login via tauri-plugin-autostart.
/// Returns Ok(true/false) reflecting what was actually registered in the OS.
/// Returns Err(message) if the OS call failed — the renderer can surface this.
#[tauri::command]
fn set_launch_at_login(app: AppHandle, enabled: bool) -> Result<bool, String> {
    use tauri_plugin_autostart::ManagerExt;
    let mgr = app.autolaunch();

    let os_result = if enabled {
        mgr.enable().map_err(|e| e.to_string())
    } else {
        mgr.disable().map_err(|e| e.to_string())
    };

    // Whatever happened, persist what the OS actually has so the UI stays consistent.
    let actual = mgr.is_enabled().unwrap_or(false);
    let mut s = settings::load(&app);
    s.launch_at_login = actual;
    settings::save(&app, &s);

    match os_result {
        Ok(_) => {
            log::info!("[autostart] set enabled={enabled} → OS confirmed={actual}");
            Ok(actual)
        }
        Err(e) => {
            log::error!("[autostart] failed to set enabled={enabled}: {e}");
            Err(e)
        }
    }
}

#[tauri::command]
fn get_active_shortcut(app: AppHandle) -> String {
    state(&app).active_shortcut.lock().unwrap().clone()
}

// ── Shortcuts ──

/// Convert Electron-style accelerator to Tauri/tao format.
/// "CommandOrControl" → "CmdOrCtrl", same for others.
fn electron_to_tauri_shortcut(key: &str) -> String {
    key.replace("CommandOrControl", "CmdOrCtrl")
       .replace("Command", "Cmd")
}

#[tauri::command]
fn test_shortcut(app: AppHandle, key: String) -> bool {
    if key.is_empty() || key.contains("Unidentified") {
        return false;
    }
    // Testing the active shortcut must not unregister it.
    if *state(&app).active_shortcut.lock().unwrap() == key {
        return true;
    }
    let tauri_key = electron_to_tauri_shortcut(&key);
    // Try to register; if it works, unregister and return true
    let mgr = app.global_shortcut();
    match mgr.register(tauri_key.as_str()) {
        Ok(_) => {
            let _ = mgr.unregister(tauri_key.as_str());
            true
        }
        Err(_) => false,
    }
}

/// Register `key` as the capture shortcut. The new shortcut is registered
/// BEFORE the old one is released, so a failure leaves the old one working.
fn do_register_shortcut(app: &AppHandle, key: &str) -> bool {
    let tauri_key = electron_to_tauri_shortcut(key);
    let gs = app.global_shortcut();
    let prev = state(app).active_shortcut.lock().unwrap().clone();

    if prev == key && gs.is_registered(tauri_key.as_str()) {
        return true;
    }

    let app_clone = app.clone();
    let result = gs.on_shortcut(tauri_key.as_str(), move |_app, _shortcut, event| {
        if event.state() == ShortcutState::Pressed {
            let app_inner = app_clone.clone();
            tauri::async_runtime::spawn(async move {
                trigger_capture(app_inner).await;
            });
        }
    });

    match result {
        Ok(_) => {
            if !prev.is_empty() && prev != key {
                let _ = gs.unregister(electron_to_tauri_shortcut(&prev).as_str());
            }
            *state(app).active_shortcut.lock().unwrap() = key.to_string();
            log::info!("[shortcut] registered {key}");
            true
        }
        Err(e) => {
            log::error!("[shortcut] register {key} failed: {e}");
            false
        }
    }
}

/// True on Linux Wayland sessions, where global shortcuts go through the
/// XDG portal (wayland_shortcut.rs) instead of tauri-plugin-global-shortcut.
fn is_wayland() -> bool {
    #[cfg(target_os = "linux")]
    {
        wayland_shortcut::is_wayland()
    }
    #[cfg(not(target_os = "linux"))]
    {
        false
    }
}

/// Bind `key` through the platform's extra mechanism (Wayland portal).
fn bind_platform_shortcut(app: &AppHandle, key: &str) {
    #[cfg(target_os = "linux")]
    wayland_shortcut::bind(app.clone(), key);
    #[cfg(not(target_os = "linux"))]
    let _ = (app, key);
}

fn unbind_platform_shortcut() {
    #[cfg(target_os = "linux")]
    wayland_shortcut::unbind();
}

fn unregister_active_shortcut(app: &AppHandle) {
    let prev = std::mem::take(&mut *state(app).active_shortcut.lock().unwrap());
    if !prev.is_empty() {
        let _ = app
            .global_shortcut()
            .unregister(electron_to_tauri_shortcut(&prev).as_str());
    }
}

#[tauri::command]
fn register_shortcut(app: AppHandle, key: String) -> bool {
    // On Wayland the X11 grab (via XWayland) may fail or never fire; the
    // portal binding below is what actually delivers the shortcut there.
    let ok = do_register_shortcut(&app, &key) || is_wayland();
    if ok {
        let mut s = settings::load(&app);
        s.shortcut_key = key.clone();
        settings::save(&app, &s);
        bind_platform_shortcut(&app, &key);
    }
    ok
}

#[derive(Serialize)]
struct PlatformInfo {
    os: &'static str,
    wayland: bool,
}

/// Lets the UI explain platform-specific shortcut behaviour.
#[tauri::command]
fn platform_info() -> PlatformInfo {
    PlatformInfo { os: std::env::consts::OS, wayland: is_wayland() }
}

#[tauri::command]
fn register_shortcut_from_settings(app: AppHandle, key: String) -> bool {
    register_shortcut(app, key)
}

// ── Overlay lifecycle ──

/// Kept for the renderer's `dismissOverlay()`; same as cancel.
#[tauri::command]
fn dismiss_overlay(app: AppHandle) {
    cancel_capture(&app);
}

/// Called after onboarding completes — make sure the hidden overlay is click-through.
#[tauri::command]
fn launch_overlay(app: AppHandle) {
    hide_overlay(&app);
}

// ── Windows ──

#[tauri::command]
fn open_settings_window(app: AppHandle) {
    show_settings(&app);
}

pub(crate) fn show_settings(app: &AppHandle) {
    if let Some(w) = get_settings_win(app) {
        let _ = w.show();
        let _ = w.set_focus();
    }
}

#[tauri::command]
fn close_settings_window(app: AppHandle) {
    if let Some(w) = get_settings_win(&app) {
        let _ = w.hide();
    }
}

fn show_main_window(app: &AppHandle) {
    if let Some(w) = app.get_webview_window("main") {
        let _ = w.show();
        let _ = w.unminimize();
        let _ = w.set_focus();
    }
}

/// Onboarded = signed in AND a shortcut was chosen. Only then does closing
/// the main window keep the app running in the tray.
fn is_onboarded(app: &AppHandle) -> bool {
    auth::has_session(app) && !settings::load(app).shortcut_key.is_empty()
}

// ── Sign out / session end ──

/// Tear down the signed-in state and return to the sign-in screen.
/// `revoke` also revokes the refresh token server-side (explicit sign-out).
fn end_session(app: &AppHandle, revoke: bool) {
    let refresh = tokens::get(app).map(|(_, r)| r);
    tokens::clear(app);
    cancel_capture(app);
    unregister_active_shortcut(app);
    unbind_platform_shortcut();
    close_settings_window(app.clone());
    let _ = app.emit_to("main", "signed-out", ());
    show_main_window(app);

    if let (true, Some(refresh)) = (revoke, refresh) {
        let client = state(app).http.clone();
        tauri::async_runtime::spawn(async move {
            #[derive(Serialize)]
            struct Body {
                refresh_token: String,
            }
            let _ = client
                .post(format!("{}/auth/signout", api_base()))
                .json(&Body { refresh_token: refresh })
                .timeout(Duration::from_secs(10))
                .send()
                .await;
        });
    }
}

#[tauri::command]
fn sign_out(app: AppHandle) {
    log::info!("[auth] sign out");
    end_session(&app, true);
}

// ── Quit ──

/// Release everything and exit. A watchdog force-exits if teardown hangs, so
/// the process never lingers in the background after "Quit".
fn quit(app: &AppHandle) {
    log::info!("[app] quit requested");
    cancel_capture(app);
    if let Some(m) = state(app).mic_monitor.lock().unwrap().take() {
        std::thread::spawn(move || m.cancel());
    }
    unbind_platform_shortcut();
    let _ = app.global_shortcut().unregister_all();
    std::thread::spawn(|| {
        std::thread::sleep(Duration::from_secs(2));
        log::warn!("[app] graceful exit timed out — forcing exit");
        std::process::exit(0);
    });
    app.exit(0);
}

#[tauri::command]
fn quit_app(app: AppHandle) {
    quit(&app);
}

// ─── App entry point ──────────────────────────────────────────────────────────

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    use tauri_plugin_log::{RotationStrategy, Target, TargetKind};

    let http = Client::builder()
        .connect_timeout(Duration::from_secs(10))
        .timeout(Duration::from_secs(60))
        .build()
        .unwrap_or_else(|_| Client::new());

    tauri::Builder::default()
        // Must be the first plugin: a second launch forwards its args here and exits.
        .plugin(tauri_plugin_single_instance::init(|app, args, _cwd| {
            if args.iter().any(|a| a == "--trigger") {
                if is_onboarded(app) {
                    let h = app.clone();
                    tauri::async_runtime::spawn(async move { trigger_capture(h).await });
                } else {
                    show_main_window(app);
                }
            } else if is_onboarded(app) {
                open_settings_window(app.clone());
            } else {
                show_main_window(app);
            }
        }))
        .plugin(
            tauri_plugin_log::Builder::new()
                .level(log::LevelFilter::Info)
                .targets([
                    Target::new(TargetKind::Stdout),
                    Target::new(TargetKind::LogDir { file_name: Some("hoverai".into()) }),
                ])
                .max_file_size(2_000_000)
                .rotation_strategy(RotationStrategy::KeepOne)
                .build(),
        )
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_store::Builder::default().build())
        .plugin(tauri_plugin_global_shortcut::Builder::new().build())
        .plugin(tauri_plugin_autostart::init(
            tauri_plugin_autostart::MacosLauncher::LaunchAgent,
            None,
        ))
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .manage(AppState {
            active_shortcut: Mutex::new(String::new()),
            http,
            session: Mutex::new(Session { id: 0, phase: Phase::Idle }),
            recording: Mutex::new(None),
            screenshot: Mutex::new(None),
            mic_monitor: Mutex::new(None),
            hit_regions: Mutex::new(Vec::new()),
        })
        .invoke_handler(tauri::generate_handler![
            // Permissions
            request_permission,
            mic_monitor_start,
            mic_monitor_stop,
            // Tokens
            store_tokens,
            get_access_token,
            clear_tokens,
            refresh_tokens,
            has_session,
            // Settings
            get_settings,
            save_settings,
            get_active_shortcut,
            set_launch_at_login,
            // Shortcuts
            test_shortcut,
            register_shortcut,
            register_shortcut_from_settings,
            platform_info,
            // Overlay / capture
            capture_submit,
            capture_cancel,
            capture_again,
            overlay_hit_regions,
            dismiss_overlay,
            launch_overlay,
            // Windows
            open_settings_window,
            close_settings_window,
            sign_out,
            quit_app,
            // Updates
            updater::app_version,
            updater::check_for_update,
            updater::install_update,
        ])
        .setup(|app| {
            log::info!(
                "[app] HoverAI {} starting — API {}",
                app.package_info().version,
                api_base()
            );
            setup_tray(app)?;
            hit_region::start_poller(app.handle().clone());
            startup(app.handle().clone());
            updater::check_on_startup(app.handle().clone(), is_onboarded(app.handle()));

            let saved = settings::load(app.handle());

            // Sync autostart entry with the persisted setting so the OS state
            // always matches what the toggle says, even after a reinstall.
            {
                use tauri_plugin_autostart::ManagerExt;
                let mgr = app.autolaunch();
                if saved.launch_at_login {
                    let _ = mgr.enable();
                } else {
                    let _ = mgr.disable();
                }
            }

            Ok(())
        })
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                api.prevent_close();
                let app = window.app_handle();
                match window.label() {
                    // Before onboarding is finished, closing the window means "quit".
                    "main" if !is_onboarded(app) => quit(app),
                    "overlay" => cancel_capture(app),
                    _ => {
                        let _ = window.hide();
                    }
                }
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running HoverAI");
}

// ─── Tray setup ───────────────────────────────────────────────────────────────

fn setup_tray(app: &mut tauri::App) -> Result<(), Box<dyn std::error::Error>> {
    let settings_item = MenuItemBuilder::with_id("settings", "Settings").build(app)?;
    let quit_item = MenuItemBuilder::with_id("quit", "Quit HoverAI").build(app)?;
    let menu = MenuBuilder::new(app)
        .item(&settings_item)
        .separator()
        .item(&quit_item)
        .build()?;

    // Load the 32×32 icon and force-convert to RGBA so Tauri's tray accepts it
    let icon = {
        let img_bytes = include_bytes!("../icons/32x32.png");
        let img = xcap::image::load_from_memory(img_bytes)
            .map_err(|e| format!("tray icon load error: {e}"))?
            .into_rgba8();
        let w = img.width();
        let h = img.height();
        tauri::image::Image::new_owned(img.into_raw(), w, h)
    };

    let _tray = TrayIconBuilder::new()
        .icon(icon)
        .menu(&menu)
        .tooltip("HoverAI")
        .on_menu_event(|app, event| match event.id().as_ref() {
            "settings" => {
                if is_onboarded(app) {
                    open_settings_window(app.clone())
                } else {
                    show_main_window(app)
                }
            }
            "quit" => quit(app),
            _ => {}
        })
        .on_tray_icon_event(|tray, event| {
            if let TrayIconEvent::Click { .. } = event {
                let app = tray.app_handle();
                if is_onboarded(app) {
                    open_settings_window(app.clone());
                } else {
                    show_main_window(app);
                }
            }
        })
        .build(app)?;

    Ok(())
}

// ─── Startup: decide onboarding locally, validate the session in background ──

fn startup(app: AppHandle) {
    // Decide from local state immediately — no network wait before the UI.
    if !is_onboarded(&app) {
        show_main_window(&app);
        return;
    }
    let shortcut = settings::load(&app).shortcut_key;
    if !do_register_shortcut(&app, &shortcut) && !is_wayland() {
        log::error!("[shortcut] could not restore {shortcut} — already taken?");
        // Let the user pick another one in Settings.
        open_settings_window(app.clone());
    }
    bind_platform_shortcut(&app, &shortcut);
    hide_overlay(&app);

    // Validate the session in the background; only an explicit rejection
    // signs the user out. Being offline keeps them signed in.
    tauri::async_runtime::spawn(async move {
        let client = state(&app).http.clone();
        let url = format!("{}/users/me", api_base());
        let result = auth::send_authed(&app, &client, |token| {
            client.get(&url).bearer_auth(token).timeout(STARTUP_PROBE_TIMEOUT)
        })
        .await;
        match result {
            Ok(Authed::Response(r)) => log::info!("[auth] startup check: HTTP {}", r.status()),
            Ok(Authed::Unauthorized) | Err(AuthError::Rejected) | Err(AuthError::NoSession) => {
                log::warn!("[auth] stored session rejected — returning to sign-in");
                end_session(&app, false);
            }
            Err(AuthError::Network(e)) => {
                log::warn!("[auth] startup check offline ({e}) — keeping session");
            }
        }
    });
}
