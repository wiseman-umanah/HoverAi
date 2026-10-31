// ─── Token storage ────────────────────────────────────────────────────────────
// Stores JWT access + refresh tokens in the OS credential store:
//   Windows  → Windows Credential Manager (DPAPI-encrypted)
//   macOS    → Keychain
//   Linux    → libsecret / KWallet
//
// Falls back to a plain JSON file in app data dir if the credential store is
// unavailable (e.g. headless CI, some minimal Linux distros without a secrets
// daemon). The fallback path is the same behaviour as before this upgrade, so
// existing installs won't lose their session.
//
// Public surface is unchanged — callers use store() / get() / clear() exactly
// as before.

use std::fs;
use std::path::PathBuf;
use serde::{Deserialize, Serialize};
use tauri::Manager;

// ── Keyring constants ─────────────────────────────────────────────────────────

const SERVICE: &str = "com.hoverai.desktop";
const ACCESS_USER: &str = "access_token";
const REFRESH_USER: &str = "refresh_token";

// ── Fallback path (plain JSON) ────────────────────────────────────────────────

#[derive(Serialize, Deserialize)]
struct TokenFile {
    access: String,
    refresh: String,
}

fn fallback_path(app: &tauri::AppHandle) -> PathBuf {
    app.path().app_data_dir()
        .expect("no app data dir")
        .join("tokens.json")
}

fn fallback_store(app: &tauri::AppHandle, access: &str, refresh: &str) {
    let path = fallback_path(app);
    if let Some(parent) = path.parent() {
        let _ = fs::create_dir_all(parent);
    }
    let content = serde_json::to_string(&TokenFile {
        access: access.to_string(),
        refresh: refresh.to_string(),
    }).unwrap_or_default();
    let _ = fs::write(&path, content);
}

fn fallback_get(app: &tauri::AppHandle) -> Option<(String, String)> {
    let raw = fs::read_to_string(fallback_path(app)).ok()?;
    let tf: TokenFile = serde_json::from_str(&raw).ok()?;
    Some((tf.access, tf.refresh))
}

fn fallback_clear(app: &tauri::AppHandle) {
    let _ = fs::remove_file(fallback_path(app));
}

// ── Public API ────────────────────────────────────────────────────────────────

/// Persist tokens to the OS credential store.
/// On failure (daemon not running, sandboxing, etc.) writes the fallback file.
pub fn store(app: &tauri::AppHandle, access: &str, refresh: &str) {
    let access_entry = keyring::Entry::new(SERVICE, ACCESS_USER);
    let refresh_entry = keyring::Entry::new(SERVICE, REFRESH_USER);

    let ok = access_entry
        .and_then(|e| e.set_password(access))
        .and(refresh_entry)
        .and_then(|e| e.set_password(refresh))
        .is_ok();

    if !ok {
        eprintln!("[tokens] keyring unavailable — writing fallback file");
        fallback_store(app, access, refresh);
    } else {
        // Clean up any stale fallback file so we don't read stale data on get()
        let _ = fs::remove_file(fallback_path(app));
    }
}

/// Read tokens from the OS credential store, falling back to the JSON file.
pub fn get(app: &tauri::AppHandle) -> Option<(String, String)> {
    let access = keyring::Entry::new(SERVICE, ACCESS_USER)
        .ok()
        .and_then(|e| e.get_password().ok());
    let refresh = keyring::Entry::new(SERVICE, REFRESH_USER)
        .ok()
        .and_then(|e| e.get_password().ok());

    if let (Some(a), Some(r)) = (access, refresh) {
        return Some((a, r));
    }

    // Keyring miss — try the fallback file (covers first run after upgrade)
    fallback_get(app)
}

/// Delete tokens from both the credential store and the fallback file.
pub fn clear(app: &tauri::AppHandle) {
    let _ = keyring::Entry::new(SERVICE, ACCESS_USER)
        .and_then(|e| e.delete_credential());
    let _ = keyring::Entry::new(SERVICE, REFRESH_USER)
        .and_then(|e| e.delete_credential());
    fallback_clear(app);
}

// ── JWT helpers ───────────────────────────────────────────────────────────────

/// Decode the `exp` claim from a JWT without verifying the signature.
pub fn jwt_expiry(token: &str) -> i64 {
    let parts: Vec<&str> = token.split('.').collect();
    if parts.len() < 2 {
        return 0;
    }
    let payload = parts[1];
    let padded = match payload.len() % 4 {
        2 => format!("{}==", payload),
        3 => format!("{}=", payload),
        _ => payload.to_string(),
    };
    use base64::Engine;
    let decoded = base64::engine::general_purpose::URL_SAFE_NO_PAD
        .decode(payload)
        .or_else(|_| base64::engine::general_purpose::URL_SAFE.decode(&padded))
        .unwrap_or_default();
    let json: serde_json::Value = serde_json::from_slice(&decoded).unwrap_or_default();
    json["exp"].as_i64().unwrap_or(0)
}
