// ─── HoverAI Tauri backend ────────────────────────────────────────────────────
// Replaces hover-app/src/main/index.ts entirely.
// All IPC is via #[tauri::command] (invoke) or app_handle.emit() (push events).

mod tokens;
mod settings;
mod http_client;
mod wake_word;

use std::sync::Mutex;
use base64::Engine;
use reqwest::Client;
use serde::{Deserialize, Serialize};
use tauri::{
    menu::{MenuBuilder, MenuItemBuilder},
    tray::{TrayIconBuilder, TrayIconEvent},
    AppHandle, Emitter, Manager, WebviewWindow,
};
use tauri_plugin_global_shortcut::{GlobalShortcutExt, ShortcutState};

// ─── API base URL ─────────────────────────────────────────────────────────────

pub fn api_base() -> String {
    // VITE_API_BASE is injected by build.rs reading hoverai-desktop/.env,
    // then emitting cargo:rustc-env=VITE_API_BASE=<value>.
    // option_env! picks that up at compile time. Falls back to localhost.
    option_env!("VITE_API_BASE")
        .unwrap_or("http://localhost:8000/api/v1")
        .to_string()
}

// ─── Shared state ─────────────────────────────────────────────────────────────

struct AppState {
    active_shortcut: Mutex<String>,
    last_screenshot: Mutex<String>,  // base64 PNG dataUrl
    http_client: Client,
    /// When Some, the wake-word detector is running. Drop or send to stop it.
    wake_word_stop: Mutex<Option<std::sync::mpsc::SyncSender<wake_word::StopSignal>>>,
}

// ─── Screenshots ──────────────────────────────────────────────────────────────

/// Capture the screen under the current cursor position.
/// Returns a base64-encoded PNG (without data: prefix).
fn capture_screenshot_b64() -> Result<String, String> {
    use screenshots::Screen;
    use std::io::Cursor;
    let screens = Screen::all().map_err(|e| format!("screen list error: {e}"))?;
    // Pick primary screen — multi-monitor refinement can follow
    let screen = screens.into_iter().next().ok_or("no screens found")?;
    let image = screen.capture().map_err(|e| format!("capture error: {e}"))?;
    // screenshots returns an ImageBuffer<Rgba<u8>>; encode it as PNG via the `image` crate
    let mut buf = Cursor::new(Vec::new());
    image.write_to(&mut buf, screenshots::image::ImageFormat::Png)
        .map_err(|e| format!("png encode error: {e}"))?;
    Ok(base64::engine::general_purpose::STANDARD.encode(buf.into_inner()))
}

/// Returns a data: URL suitable for passing to the renderer.
fn capture_screenshot_data_url() -> Result<String, String> {
    let b64 = capture_screenshot_b64()?;
    Ok(format!("data:image/png;base64,{b64}"))
}

// ─── Overlay window helpers ───────────────────────────────────────────────────

fn get_overlay(app: &AppHandle) -> Option<WebviewWindow> {
    app.get_webview_window("overlay")
}

fn get_settings_win(app: &AppHandle) -> Option<WebviewWindow> {
    app.get_webview_window("settings")
}

// ─── Trigger capture ──────────────────────────────────────────────────────────

async fn trigger_capture(app: AppHandle) {
    let state = app.state::<AppState>();

    // If overlay is already visible, shortcut press dismisses it
    if let Some(overlay) = get_overlay(&app) {
        if overlay.is_visible().unwrap_or(false) {
            let _ = overlay.hide();
            let _ = overlay.set_ignore_cursor_events(true);
            let _ = app.emit_to("overlay", "capture-end", ());
            return;
        }
    }

    // Take screenshot BEFORE showing overlay
    match capture_screenshot_data_url() {
        Ok(data_url) => {
            *state.last_screenshot.lock().unwrap() = data_url.clone();
            let shortcut = state.active_shortcut.lock().unwrap().clone();

            if let Some(overlay) = get_overlay(&app) {
                // Resize overlay to cover the primary monitor exactly
                if let Ok(screens) = screenshots::Screen::all() {
                    if let Some(screen) = screens.into_iter().next() {
                        let di = screen.display_info;
                        let _ = overlay.set_position(tauri::Position::Physical(
                            tauri::PhysicalPosition { x: di.x, y: di.y },
                        ));
                        let _ = overlay.set_size(tauri::Size::Physical(
                            tauri::PhysicalSize { width: di.width, height: di.height },
                        ));
                    }
                }
                let _ = overlay.set_always_on_top(true);
                // Ensure overlay receives mouse AND keyboard events
                let _ = overlay.set_ignore_cursor_events(false);
                // show() MUST come before set_focus() — on Windows the OS
                // will not give keyboard focus to a hidden window.
                let _ = overlay.show();
                // Small delay lets the Windows compositor finish the show
                // operation; without it set_focus() is sometimes ignored and
                // Space/Enter never reach the webview's keydown handler.
                tokio::time::sleep(std::time::Duration::from_millis(80)).await;
                let _ = overlay.set_focus();
                let _ = app.emit_to("overlay", "capture-start", CaptureStartPayload {
                    data_url,
                    shortcut_key: shortcut,
                });
            }
        }
        Err(e) => {
            eprintln!("[capture] screenshot failed: {e}");
        }
    }
}

#[derive(Clone, Serialize)]
struct CaptureStartPayload {
    data_url: String,
    shortcut_key: String,
}

// ─── Query pipeline ───────────────────────────────────────────────────────────

#[derive(Debug, Deserialize, Serialize, Clone)]
pub struct BeaconStep {
    pub step: u32,
    pub instruction: String,
    pub keys: Option<String>,
    pub tip: Option<String>,
}

#[derive(Debug, Deserialize, Serialize, Clone)]
pub struct QueryResponse {
    pub transcript: String,
    pub steps: Vec<BeaconStep>,
    pub summary: String,
    pub speech_b64: String,
    pub conversation_id: String,
    // benchmark is not always present — make it optional so a missing field
    // doesn't cause the whole deserialization to fail
    #[serde(default)]
    pub benchmark: Option<serde_json::Value>,
}

async fn run_query_pipeline(app: AppHandle, audio_data: Vec<u8>, screenshot_data_url: String) {
    let state = app.state::<AppState>();
    let client = state.http_client.clone();
    let api_base = api_base();

    // Convert dataUrl → raw PNG bytes → base64 string for the form field
    let screenshot_b64 = if let Some(comma_pos) = screenshot_data_url.find(',') {
        screenshot_data_url[comma_pos + 1..].to_string()
    } else {
        screenshot_data_url.clone()
    };

    // Build multipart/form-data.
    // reqwest multipart bodies are streaming — they cannot be cloned or inspected
    // as raw bytes after construction, so we must NOT pass them through
    // fetch_with_auth (which tries to re-build the request from body bytes and
    // silently drops streaming bodies). Instead we attach the auth header directly
    // and call .send() here. A 401 mid-query is not expected (the token was just
    // validated at capture time), so no retry logic is needed here.
    let token = tokens::get(&app).map(|(a, _)| a);

    let screenshot_part = reqwest::multipart::Part::text(screenshot_b64)
        .mime_str("text/plain")
        .unwrap();

    let audio_part = reqwest::multipart::Part::bytes(audio_data)
        .file_name("audio.webm")
        .mime_str("audio/webm")
        .unwrap();

    let form = reqwest::multipart::Form::new()
        .part("audio", audio_part)
        .part("screenshot", screenshot_part);

    let mut request = client
        .post(format!("{api_base}/query"))
        .multipart(form);

    if let Some(tok) = token {
        request = request.bearer_auth(tok);
    }

    match request.send().await.map_err(|e| format!("network error: {e}")) {
        Ok(resp) if resp.status().is_success() => {
            match resp.json::<QueryResponse>().await {
                Ok(query_resp) => {
                    // Show overlay in pass-through mode — the renderer uses
                    // set_cursor_passthrough to toggle hit-testing as the cursor
                    // enters/leaves the panel, so clicks outside the panel reach
                    // the underlying application windows.
                    if let Some(overlay) = get_overlay(&app) {
                        let _ = overlay.set_ignore_cursor_events(true);
                        let _ = overlay.show();
                    }
                    let _ = app.emit_to("overlay", "query-result", &query_resp);
                }
                Err(e) => {
                    eprintln!("[query] parse error: {e}");
                    show_overlay_for_error(&app);
                    let _ = app.emit_to("overlay", "query-error", format!("Response parse error: {e}"));
                }
            }
        }
        Ok(resp) => {
            let status = resp.status();
            let body = resp.text().await.unwrap_or_else(|_| format!("HTTP {status}"));
            eprintln!("[query] error {status}: {body}");
            show_overlay_for_error(&app);
            let _ = app.emit_to("overlay", "query-error", format!("Query failed: {body}"));
        }
        Err(e) => {
            eprintln!("[query] network error: {e}");
            show_overlay_for_error(&app);
            let _ = app.emit_to("overlay", "query-error", "Network error — check backend is running.".to_string());
        }
    }
}

fn show_overlay_for_error(app: &AppHandle) {
    if let Some(overlay) = get_overlay(app) {
        // Same pass-through default as query-result
        let _ = overlay.set_ignore_cursor_events(true);
        let _ = overlay.show();
    }
}

// ─── Tauri commands ───────────────────────────────────────────────────────────

// ── Permissions ──

#[tauri::command]
async fn request_permission(id: String) -> String {
    match id.as_str() {
        "microphone" => {
            #[cfg(target_os = "windows")]
            {
                // On Windows the browser API handles mic permission.
                // Triggering getUserMedia from the renderer is sufficient.
                "granted".to_string()
            }
            #[cfg(target_os = "macos")]
            {
                // macOS: trigger AVCaptureDevice access request
                // The system dialog fires when the renderer calls getUserMedia.
                "granted".to_string()
            }
            #[cfg(not(any(target_os = "windows", target_os = "macos")))]
            {
                "granted".to_string()
            }
        }
        "screen" => {
            // Taking a screenshot triggers the macOS screen recording permission dialog.
            match capture_screenshot_b64() {
                Ok(_) => "granted".to_string(),
                Err(_) => "denied".to_string(),
            }
        }
        _ => "denied".to_string(),
    }
}

// ── Token storage ──

#[tauri::command]
fn store_tokens(app: AppHandle, access: String, refresh: String) {
    tokens::store(&app, &access, &refresh);
}

#[tauri::command]
fn get_access_token(app: AppHandle) -> Option<String> {
    tokens::get(&app).map(|(a, _)| a)
}

#[tauri::command]
fn clear_tokens(app: AppHandle) {
    tokens::clear(&app);
}

#[tauri::command]
async fn refresh_tokens(app: AppHandle) -> Option<String> {
    let state = app.state::<AppState>();
    let client = state.http_client.clone();
    let api_base = api_base();

    let (_, refresh) = tokens::get(&app)?;

    #[derive(Serialize)]
    struct Body { refresh_token: String }

    let resp = client
        .post(format!("{api_base}/auth/refresh"))
        .json(&Body { refresh_token: refresh })
        .send()
        .await
        .ok()?;

    if !resp.status().is_success() {
        tokens::clear(&app);
        return None;
    }

    #[derive(Deserialize)]
    struct Resp { access_token: String, refresh_token: String }
    let data: Resp = resp.json().await.ok()?;
    tokens::store(&app, &data.access_token, &data.refresh_token);
    Some(data.access_token)
}

// ── Settings ──

#[tauri::command]
fn get_settings(app: AppHandle) -> settings::AppSettings {
    settings::load(&app)
}

#[tauri::command]
fn save_settings(app: AppHandle, s: settings::AppSettings) {
    let wake_enabled = s.wake_word_enabled;
    settings::save(&app, &s);
    apply_wake_word_setting(&app, wake_enabled);
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

    match os_result {
        Ok(_) => {
            // Confirm what the OS actually has registered now
            let actual = mgr.is_enabled().unwrap_or(false);
            let mut s = settings::load(&app);
            s.launch_at_login = actual;
            settings::save(&app, &s);
            println!("[autostart] set enabled={enabled} → OS confirmed={actual}");
            Ok(actual)
        }
        Err(e) => {
            eprintln!("[autostart] failed to set enabled={enabled}: {e}");
            // Revert the setting to what the OS actually has so the UI stays consistent
            let actual = mgr.is_enabled().unwrap_or(false);
            let mut s = settings::load(&app);
            s.launch_at_login = actual;
            settings::save(&app, &s);
            Err(e)
        }
    }
}

/// Start the detector if `enabled` is true and it isn't running; stop it if false.
fn apply_wake_word_setting(app: &AppHandle, enabled: bool) {
    let state = app.state::<AppState>();
    let mut guard = state.wake_word_stop.lock().unwrap();
    if enabled {
        if guard.is_none() {
            let tx = wake_word::start(app.clone());
            *guard = Some(tx);
            println!("[wake-word] started");
        }
    } else {
        // Dropping the sender signals the detector thread to stop.
        *guard = None;
        println!("[wake-word] stopped");
    }
}

/// IPC command so the renderer can toggle wake-word without a full settings save.
#[tauri::command]
fn set_wake_word_enabled(app: AppHandle, enabled: bool) {
    let mut s = settings::load(&app);
    s.wake_word_enabled = enabled;
    settings::save(&app, &s);
    apply_wake_word_setting(&app, enabled);
}

#[tauri::command]
fn get_active_shortcut(app: AppHandle) -> String {
    let state = app.state::<AppState>();
    let shortcut = state.active_shortcut.lock().unwrap().clone();
    shortcut
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

fn do_register_shortcut(app: &AppHandle, key: &str) -> bool {
    let tauri_key = electron_to_tauri_shortcut(key);
    let state = app.state::<AppState>();

    // Unregister previous
    {
        let prev = state.active_shortcut.lock().unwrap().clone();
        if !prev.is_empty() {
            let prev_tauri = electron_to_tauri_shortcut(&prev);
            let _ = app.global_shortcut().unregister(prev_tauri.as_str());
        }
    }

    let app_clone = app.clone();
    let result = app.global_shortcut().on_shortcut(
        tauri_key.as_str(),
        move |_app, _shortcut, event| {
            if event.state() == ShortcutState::Pressed {
                let app_inner = app_clone.clone();
                tauri::async_runtime::spawn(async move {
                    trigger_capture(app_inner).await;
                });
            }
        },
    );

    match result {
        Ok(_) => {
            *state.active_shortcut.lock().unwrap() = key.to_string();
            true
        }
        Err(e) => {
            eprintln!("[shortcut] register failed: {e}");
            false
        }
    }
}

#[tauri::command]
fn register_shortcut(app: AppHandle, key: String) -> bool {
    let ok = do_register_shortcut(&app, &key);
    if ok {
        let mut s = settings::load(&app);
        s.shortcut_key = key.clone();
        settings::save(&app, &s);
    }
    ok
}

#[tauri::command]
fn register_shortcut_from_settings(app: AppHandle, key: String) -> bool {
    let ok = do_register_shortcut(&app, &key);
    if ok {
        let mut s = settings::load(&app);
        s.shortcut_key = key.clone();
        settings::save(&app, &s);
    }
    ok
}

// ── Overlay lifecycle ──

/// Called by the renderer when the cursor enters/leaves a clickable panel.
/// `passthrough = true`  → OS forwards all clicks to windows underneath.
/// `passthrough = false` → OS routes clicks to this overlay (panel is active).
#[tauri::command]
fn set_cursor_passthrough(app: AppHandle, passthrough: bool) {
    if let Some(overlay) = get_overlay(&app) {
        let _ = overlay.set_ignore_cursor_events(passthrough);
    }
}

#[tauri::command]
async fn focus_overlay(app: AppHandle) {
    if let Some(overlay) = get_overlay(&app) {
        let _ = overlay.set_ignore_cursor_events(false);
        let _ = overlay.show();
        tokio::time::sleep(std::time::Duration::from_millis(80)).await;
        let _ = overlay.set_focus();
    }
}

#[tauri::command]
fn dismiss_overlay(app: AppHandle) {
    if let Some(overlay) = get_overlay(&app) {
        let _ = overlay.hide();
        let _ = overlay.set_ignore_cursor_events(true);
        // Do NOT emit capture-end here — this command is always called *from* the
        // renderer (dismiss button / cancel), so the renderer has already reset its
        // own state. Emitting capture-end back would trigger a second dismiss()
        // call which re-invokes dismiss_overlay, creating a loop that can leave
        // the window in a broken visible/hidden state and block the next shortcut.
    }
}

#[tauri::command]
fn move_overlay(app: AppHandle, x: i32, y: i32) {
    if let Some(overlay) = get_overlay(&app) {
        let _ = overlay.set_position(tauri::Position::Physical(tauri::PhysicalPosition { x, y }));
    }
}

// ── Capture done (audio from renderer → start query pipeline) ──

#[tauri::command]
async fn capture_done(app: AppHandle, audio_data: Vec<u8>) {
    // Hide/blur overlay so query pipeline runs while target app has focus
    if let Some(overlay) = get_overlay(&app) {
        let _ = overlay.set_ignore_cursor_events(true);
    }
    let state = app.state::<AppState>();
    let screenshot = state.last_screenshot.lock().unwrap().clone();
    let app_clone = app.clone();
    tauri::async_runtime::spawn(async move {
        run_query_pipeline(app_clone, audio_data, screenshot).await;
    });
}

#[tauri::command]
async fn capture_done_with_screenshot(
    app: AppHandle,
    audio_data: Vec<u8>,
    screenshot_data_url: String,
) {
    if let Some(overlay) = get_overlay(&app) {
        let _ = overlay.set_ignore_cursor_events(true);
    }
    let app_clone = app.clone();
    tauri::async_runtime::spawn(async move {
        run_query_pipeline(app_clone, audio_data, screenshot_data_url).await;
    });
}

// ── Screenshot ──

#[tauri::command]
fn take_screenshot() -> Result<String, String> {
    capture_screenshot_b64()
}

// ── Settings window ──

#[tauri::command]
fn open_settings_window(app: AppHandle) {
    if let Some(w) = get_settings_win(&app) {
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

// ── Sign out ──

#[tauri::command]
async fn sign_out(app: AppHandle) {
    let state = app.state::<AppState>();
    let client = state.http_client.clone();
    let api_base = api_base();

    // Fire-and-forget: revoke refresh token
    if let Some((_, refresh)) = tokens::get(&app) {
        #[derive(Serialize)]
        struct Body { refresh_token: String }
        let _ = client
            .post(format!("{api_base}/auth/signout"))
            .json(&Body { refresh_token: refresh })
            .send()
            .await;
    }

    tokens::clear(&app);

    // Unregister shortcut
    {
        let prev = state.active_shortcut.lock().unwrap().clone();
        if !prev.is_empty() {
            let prev_tauri = electron_to_tauri_shortcut(&prev);
            let _ = app.global_shortcut().unregister(prev_tauri.as_str());
            *state.active_shortcut.lock().unwrap() = String::new();
        }
    }

    // Close settings window
    close_settings_window(app.clone());

    // Show main onboarding window
    if let Some(main_win) = app.get_webview_window("main") {
        let _ = main_win.show();
        let _ = main_win.set_focus();
    }
}

// ── launch-overlay (called after onboarding completes) ──

#[tauri::command]
fn launch_overlay(app: AppHandle) {
    // Pre-warm the overlay window
    if let Some(overlay) = get_overlay(&app) {
        // Already created by config, just ensure it's ready
        let _ = overlay.set_ignore_cursor_events(true);
    }
}

// ─── App entry point ──────────────────────────────────────────────────────────

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_store::Builder::default().build())
        .plugin(tauri_plugin_global_shortcut::Builder::new().build())
        .plugin(tauri_plugin_autostart::init(
            tauri_plugin_autostart::MacosLauncher::LaunchAgent,
            None,
        ))
        .plugin(tauri_plugin_shell::init())
        .manage(AppState {
            active_shortcut: Mutex::new(String::new()),
            last_screenshot: Mutex::new(String::new()),
            http_client: Client::new(),
            wake_word_stop: Mutex::new(None),
        })
        .invoke_handler(tauri::generate_handler![
            // Permissions
            request_permission,
            // Tokens
            store_tokens,
            get_access_token,
            clear_tokens,
            refresh_tokens,
            // Settings
            get_settings,
            save_settings,
            get_active_shortcut,
            set_wake_word_enabled,
            set_launch_at_login,
            // Shortcuts
            test_shortcut,
            register_shortcut,
            register_shortcut_from_settings,
            // Overlay
            set_cursor_passthrough,
            focus_overlay,
            dismiss_overlay,
            move_overlay,
            launch_overlay,
            // Capture
            capture_done,
            capture_done_with_screenshot,
            take_screenshot,
            // Windows
            open_settings_window,
            close_settings_window,
            sign_out,
        ])
        .setup(|app| {
            setup_tray(app)?;
            restore_shortcut_on_startup(app);

            let saved = settings::load(&app.handle());

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

            // Start wake-word detector off the main thread — cpal + ONNX Runtime
            // load native DLLs that must not block or crash the Tauri event loop.
            if saved.wake_word_enabled {
                let handle = app.handle().clone();
                tauri::async_runtime::spawn_blocking(move || {
                    apply_wake_word_setting(&handle, true);
                });
            }
            Ok(())
        })
        .on_window_event(|window, event| {
            // Prevent app from quitting when all windows close — tray keeps it alive
            if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                // Only hide, don't destroy
                let _ = window.hide();
                api.prevent_close();
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running HoverAI");
}

// ─── Tray setup ───────────────────────────────────────────────────────────────

fn setup_tray(app: &mut tauri::App) -> Result<(), Box<dyn std::error::Error>> {
    let settings_item = MenuItemBuilder::with_id("settings", "Settings").build(app)?;
    let quit_item = MenuItemBuilder::with_id("quit", "Stop HoverAI").build(app)?;
    let menu = MenuBuilder::new(app)
        .item(&settings_item)
        .separator()
        .item(&quit_item)
        .build()?;

    // Load the 32×32 icon and force-convert to RGBA so Tauri's tray accepts it
    let icon = {
        let img_bytes = include_bytes!("../icons/32x32.png");
        let img = screenshots::image::load_from_memory(img_bytes)
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
            "settings" => open_settings_window(app.clone()),
            "quit" => app.exit(0),
            _ => {}
        })
        .on_tray_icon_event(|tray, event| {
            if let TrayIconEvent::Click { .. } = event {
                let app = tray.app_handle();
                open_settings_window(app.clone());
            }
        })
        .build(app)?;

    Ok(())
}

// ─── Startup: restore saved shortcut + validate token ─────────────────────────

fn restore_shortcut_on_startup(app: &mut tauri::App) {
    let app_handle = app.handle().clone();
    tauri::async_runtime::spawn(async move {
        // Validate token against server
        let should_skip_onboarding = validate_token_on_startup(&app_handle).await;

        if should_skip_onboarding {
            // Restore saved shortcut
            let saved = settings::load(&app_handle);
            if !saved.shortcut_key.is_empty() {
                let ok = do_register_shortcut(&app_handle, &saved.shortcut_key);
                if ok {
                    println!("[shortcut] restored on startup: {}", saved.shortcut_key);
                } else {
                    eprintln!("[shortcut] could not restore {} — already taken", saved.shortcut_key);
                }
            }
            // Hide main window (onboarding), pre-warm overlay
            if let Some(main_win) = app_handle.get_webview_window("main") {
                let _ = main_win.hide();
            }
            if let Some(overlay) = get_overlay(&app_handle) {
                let _ = overlay.set_ignore_cursor_events(true);
            }
        }
        // If token invalid, main window stays visible (onboarding shows)
    });
}

async fn validate_token_on_startup(app: &AppHandle) -> bool {
    let tokens_opt = tokens::get(app);
    let (access, _) = match tokens_opt {
        Some(t) => t,
        None => return false,
    };

    // Check local expiry first
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs() as i64;

    if tokens::jwt_expiry(&access) <= now + 60 {
        tokens::clear(app);
        return false;
    }

    // Probe server
    let state = app.state::<AppState>();
    let client = state.http_client.clone();
    let api_base = api_base();

    let request = client
        .get(format!("{api_base}/users/me"))
        .header("Authorization", format!("Bearer {access}"));

    match http_client::fetch_with_auth(app, &client, request).await {
        Ok(resp) if resp.status().is_success() => {
            println!("[auth] server confirmed valid token — skipping onboarding");
            true
        }
        Ok(resp) => {
            // Server explicitly rejected the token (401/403/etc.) — clear it
            println!("[auth] server rejected token ({}) — clearing", resp.status());
            tokens::clear(app);
            false
        }
        Err(e) => {
            // Network unreachable (backend down, no internet, ngrok offline).
            // Do NOT clear the token — the JWT is still locally valid.
            // Trust it and skip onboarding; the first real API call will surface
            // the error to the user with a proper error message.
            eprintln!("[auth] could not reach server: {e} — trusting local token");
            true
        }
    }
}
