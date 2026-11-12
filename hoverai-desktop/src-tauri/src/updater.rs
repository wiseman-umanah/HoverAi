// ─── Auto-update ──────────────────────────────────────────────────────────────
// tauri-plugin-updater against GitHub Releases: `release.yml` attaches signed
// update bundles + `latest.json` to each `v*` release, and the endpoint in
// tauri.conf.json → plugins.updater points at the latest *published* release.
// Updates are verified with the minisign public key in the same config; the
// private key only exists as the TAURI_SIGNING_PRIVATE_KEY repo secret.
//
// Everything runs on the Rust side (no JS plugin/capabilities needed):
//   - a quiet check shortly after startup (release builds only); if an update
//     exists it opens Settings, whose Updates card offers "Install & restart"
//   - `check_for_update` / `install_update` commands for that card
// Linux: only AppImage installs can update themselves; .deb installs get an
// error from the plugin and must be updated by hand.

use std::sync::Mutex;
use std::time::Duration;

use serde::Serialize;
use tauri::{AppHandle, Emitter};
use tauri_plugin_updater::{Update, UpdaterExt};

/// The update found by the last check, kept so "install" doesn't re-download
/// the manifest.
static PENDING: Mutex<Option<Update>> = Mutex::new(None);

#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct UpdateInfo {
    pub version: String,
    pub current_version: String,
    pub notes: Option<String>,
}

impl From<&Update> for UpdateInfo {
    fn from(u: &Update) -> Self {
        Self {
            version: u.version.clone(),
            current_version: u.current_version.clone(),
            notes: u.body.clone(),
        }
    }
}

async fn check(app: &AppHandle) -> Result<Option<UpdateInfo>, String> {
    let update = app
        .updater()
        .map_err(|e| e.to_string())?
        .check()
        .await
        .map_err(|e| e.to_string())?;
    let info = update.as_ref().map(UpdateInfo::from);
    *PENDING.lock().unwrap() = update;
    Ok(info)
}

/// Background check a few seconds after startup. Skipped in debug builds, which
/// are never installed copies and have nothing to update.
pub fn check_on_startup(app: AppHandle, onboarded: bool) {
    if cfg!(debug_assertions) {
        return;
    }
    tauri::async_runtime::spawn(async move {
        tokio::time::sleep(Duration::from_secs(5)).await;
        match check(&app).await {
            Ok(Some(info)) => {
                log::info!("[update] {} available (running {})", info.version, info.current_version);
                // During onboarding Settings would cover the wizard; the
                // Updates card will show it the next time Settings opens.
                if onboarded {
                    let _ = app.emit_to("settings", "update-available", info);
                    crate::show_settings(&app);
                }
            }
            Ok(None) => log::info!("[update] up to date"),
            Err(e) => log::warn!("[update] check failed: {e}"),
        }
    });
}

#[tauri::command]
pub fn app_version(app: AppHandle) -> String {
    app.package_info().version.to_string()
}

#[tauri::command]
pub async fn check_for_update(app: AppHandle) -> Result<Option<UpdateInfo>, String> {
    check(&app).await.inspect_err(|e| log::warn!("[update] check failed: {e}"))
}

/// Download + install the pending update (checking first if needed), emitting
/// `update-progress` (0–1, or -1 when the size is unknown) to Settings, then
/// restart. On Windows the installer closes the app itself.
#[tauri::command]
pub async fn install_update(app: AppHandle) -> Result<(), String> {
    let pending = PENDING.lock().unwrap().take();
    let update = match pending {
        Some(u) => u,
        None => {
            check(&app).await?;
            PENDING.lock().unwrap().take().ok_or("HoverAI is already up to date.")?
        }
    };
    log::info!("[update] installing {}", update.version);

    let mut downloaded: u64 = 0;
    let progress_app = app.clone();
    update
        .download_and_install(
            move |chunk, total| {
                downloaded += chunk as u64;
                let p = total.map_or(-1.0, |t| downloaded as f64 / t.max(1) as f64);
                let _ = progress_app.emit_to("settings", "update-progress", p);
            },
            || log::info!("[update] downloaded, installing"),
        )
        .await
        .map_err(|e| {
            log::error!("[update] install failed: {e}");
            format!("Update failed: {e}")
        })?;

    log::info!("[update] installed, restarting");
    app.restart();
}
