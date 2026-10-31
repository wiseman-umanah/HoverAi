// ─── Settings storage ─────────────────────────────────────────────────────────
// AppSettings persisted as JSON at app_data_dir/settings.json (plain fs).

use serde::{Deserialize, Serialize};
use std::fs;
use std::path::PathBuf;
use tauri::Manager;

/// Overlay size — mirrors backend `Literal["compact", "default", "large"]`.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "lowercase")]
#[derive(Default)]
pub enum OverlaySize {
    Compact,
    #[default]
    Default,
    Large,
}


/// AI voice gender — mirrors backend `Literal["female", "male"]`.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "lowercase")]
#[derive(Default)]
pub enum VoiceGender {
    #[default]
    Female,
    Male,
}


#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct AppSettings {
    pub launch_at_login: bool,
    pub show_in_taskbar: bool,
    pub overlay_opacity: f64,
    pub overlay_size: OverlaySize,
    pub mic_sensitivity: u32,      // 1–10
    pub sound_effects: bool,
    pub notifications: bool,
    pub language: String,
    pub wake_word_enabled: bool,
    pub voice_gender: VoiceGender,
    pub shortcut_key: String,      // e.g. "CmdOrCtrl+Shift+H"
}

impl Default for AppSettings {
    fn default() -> Self {
        AppSettings {
            launch_at_login: false,
            show_in_taskbar: false,
            overlay_opacity: 1.0,
            overlay_size: OverlaySize::Default,
            mic_sensitivity: 5,
            sound_effects: true,
            notifications: true,
            language: "en-pidgin".to_string(),
            wake_word_enabled: false,
            voice_gender: VoiceGender::Female,
            shortcut_key: String::new(),
        }
    }
}

fn settings_path(app: &tauri::AppHandle) -> PathBuf {
    app.path().app_data_dir()
        .expect("no app data dir")
        .join("settings.json")
}

pub fn load(app: &tauri::AppHandle) -> AppSettings {
    let path = settings_path(app);
    let raw = fs::read_to_string(&path).unwrap_or_default();
    if raw.is_empty() {
        return AppSettings::default();
    }
    // Merge with defaults so any missing fields get their default values
    let defaults = serde_json::to_value(AppSettings::default()).unwrap_or_default();
    let mut merged = defaults;
    if let Ok(user) = serde_json::from_str::<serde_json::Value>(&raw) {
        if let (Some(m), Some(u)) = (merged.as_object_mut(), user.as_object()) {
            for (k, v) in u {
                m.insert(k.clone(), v.clone());
            }
        }
    }
    serde_json::from_value(merged).unwrap_or_default()
}

pub fn save(app: &tauri::AppHandle, settings: &AppSettings) {
    let path = settings_path(app);
    if let Some(parent) = path.parent() {
        let _ = fs::create_dir_all(parent);
    }
    if let Ok(content) = serde_json::to_string_pretty(settings) {
        let _ = fs::write(&path, content);
    }
}
