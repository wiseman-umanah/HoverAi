// ─── Global shortcut on Wayland (XDG GlobalShortcuts portal) ─────────────────
//
// tauri-plugin-global-shortcut (global-hotkey) only implements X11 on Linux,
// so on Wayland the capture shortcut never fires. Wayland compositors expose
// global shortcuts through org.freedesktop.portal.GlobalShortcuts instead:
//
//   - GNOME 48+ / KDE: the desktop shows a dialog to confirm (or change) the
//     preferred trigger we pass, then emits Activated when it is pressed.
//   - Hyprland (xdph): shortcuts are registered but keys are bound by the
//     user in the Hyprland config with the `global` dispatcher, e.g.
//     `bind = SUPER SHIFT, H, global, :capture` (see `hyprctl globalshortcuts`).
//     A plain keybind running `hoverai-desktop --trigger` also works.
//
// The portal session lives inside a background task; re-binding (shortcut
// changed) aborts the old task and starts a new session.

use std::sync::Mutex;

use ashpd::desktop::global_shortcuts::{GlobalShortcuts, NewShortcut};
use futures_util::StreamExt;
use tauri::async_runtime::JoinHandle;
use tauri::AppHandle;

const SHORTCUT_ID: &str = "capture";
/// Must match `identifier` in tauri.conf.json (and the .desktop file name).
const APP_ID: &str = "com.hoverai.desktop";

static TASK: Mutex<Option<JoinHandle<()>>> = Mutex::new(None);

pub fn is_wayland() -> bool {
    std::env::var_os("WAYLAND_DISPLAY").is_some()
        || std::env::var("XDG_SESSION_TYPE").is_ok_and(|t| t == "wayland")
}

/// Convert our accelerator ("CommandOrControl+Shift+H") to the XDG shortcuts
/// spec form ("CTRL+SHIFT+h").
fn to_xdg_trigger(accelerator: &str) -> String {
    accelerator
        .split('+')
        .map(|part| match part {
            "CommandOrControl" | "CmdOrCtrl" | "Control" | "Ctrl" => "CTRL".to_string(),
            "Alt" | "Option" => "ALT".to_string(),
            "Shift" => "SHIFT".to_string(),
            "Super" | "Meta" | "Cmd" | "Command" => "LOGO".to_string(),
            "Space" => "space".to_string(),
            key if key.len() == 1 => key.to_lowercase(),
            key => key.to_string(), // F9, Up, Escape … match XKB keysym names
        })
        .collect::<Vec<_>>()
        .join("+")
}

/// (Re)bind the capture shortcut through the portal. No-op off Wayland.
pub fn bind(app: AppHandle, accelerator: &str) {
    if !is_wayland() {
        return;
    }
    let trigger = to_xdg_trigger(accelerator);
    let handle = tauri::async_runtime::spawn(async move {
        if let Err(e) = run(app, &trigger).await {
            log::warn!("[wayland-shortcut] portal unavailable: {e} — use a compositor keybind running `hoverai-desktop --trigger`");
        }
    });
    if let Some(old) = TASK.lock().unwrap().replace(handle) {
        old.abort();
    }
}

/// Stop listening (sign-out / quit).
pub fn unbind() {
    if let Some(old) = TASK.lock().unwrap().take() {
        old.abort();
    }
}

/// Unsandboxed apps must tell the portal who they are before using
/// GlobalShortcuts ("An app id is required"). Once per D-Bus connection.
async fn register_app_id() {
    use std::sync::atomic::{AtomicBool, Ordering};
    static DONE: AtomicBool = AtomicBool::new(false);
    if DONE.swap(true, Ordering::SeqCst) {
        return;
    }
    ensure_desktop_entry();
    let id: ashpd::AppID = APP_ID.parse().expect("valid app id");
    if let Err(e) = ashpd::register_host_app(id).await {
        // Older portals (< 1.19) have no registry; they derive the id themselves.
        log::info!("[wayland-shortcut] host app registration: {e}");
    }
}

/// The portal's host-app registry only accepts an app id that has a matching
/// installed `<app-id>.desktop` file ("App info not found" otherwise).
/// Packaged builds may ship one; for dev builds and unpackaged binaries we
/// write a hidden one pointing at the running executable. NoDisplay keeps it
/// out of launchers. Rewritten only when the content changes.
fn ensure_desktop_entry() {
    let Some(dir) = std::env::var_os("XDG_DATA_HOME")
        .map(std::path::PathBuf::from)
        .or_else(|| std::env::var_os("HOME").map(|h| std::path::Path::new(&h).join(".local/share")))
        .map(|d| d.join("applications"))
    else {
        return;
    };
    let path = dir.join(format!("{APP_ID}.desktop"));
    let system = std::path::Path::new("/usr/share/applications").join(format!("{APP_ID}.desktop"));
    if system.exists() {
        return; // installed package provides it
    }
    let Ok(exe) = std::env::current_exe() else { return };
    let content = format!(
        "[Desktop Entry]\nType=Application\nName=HoverAI\nComment=AI assistant for your screen\n\
         Exec=\"{}\" %U\nTerminal=false\nNoDisplay=true\nCategories=Utility;\n",
        exe.display()
    );
    if std::fs::read_to_string(&path).is_ok_and(|c| c == content) {
        return;
    }
    let _ = std::fs::create_dir_all(&dir);
    match std::fs::write(&path, content) {
        Ok(()) => log::info!("[wayland-shortcut] wrote {}", path.display()),
        Err(e) => log::warn!("[wayland-shortcut] could not write {}: {e}", path.display()),
    }
}

async fn run(app: AppHandle, trigger: &str) -> ashpd::Result<()> {
    register_app_id().await;
    let portal = GlobalShortcuts::new().await?;
    let session = portal.create_session(Default::default()).await?;
    let shortcut = NewShortcut::new(SHORTCUT_ID, "Ask HoverAI about your screen")
        .preferred_trigger(Some(trigger));
    let bound = portal
        .bind_shortcuts(&session, &[shortcut], None, Default::default())
        .await?
        .response()?;
    for s in bound.shortcuts() {
        log::info!(
            "[wayland-shortcut] bound '{}' (requested {trigger}, desktop reports '{}')",
            s.id(),
            s.trigger_description()
        );
    }

    let mut activated = portal.receive_activated().await?;
    while let Some(event) = activated.next().await {
        if event.shortcut_id() == SHORTCUT_ID {
            let h = app.clone();
            tauri::async_runtime::spawn(async move { crate::trigger_capture(h).await });
        }
    }
    drop(session);
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::to_xdg_trigger;

    #[test]
    fn converts_accelerators_to_xdg_triggers() {
        assert_eq!(to_xdg_trigger("CommandOrControl+Shift+H"), "CTRL+SHIFT+h");
        assert_eq!(to_xdg_trigger("Super+Alt+F9"), "LOGO+ALT+F9");
        assert_eq!(to_xdg_trigger("Control+Space"), "CTRL+space");
    }

    /// Desktop check — binds through the real portal. Run with:
    /// `cargo test --lib wayland_shortcut::tests::portal_binds -- --ignored --nocapture`
    /// (on Hyprland, then see `hyprctl globalshortcuts`).
    #[test]
    #[ignore]
    fn portal_binds() {
        use super::*;
        tauri::async_runtime::block_on(async {
            register_app_id().await;
            let portal = GlobalShortcuts::new().await.expect("portal");
            let session = portal.create_session(Default::default()).await.expect("session");
            let shortcut = NewShortcut::new(SHORTCUT_ID, "HoverAI portal test")
                .preferred_trigger(Some("CTRL+SHIFT+h"));
            let bound = portal
                .bind_shortcuts(&session, &[shortcut], None, Default::default())
                .await
                .expect("bind request")
                .response()
                .expect("bind response");
            for s in bound.shortcuts() {
                println!("bound id={} trigger='{}'", s.id(), s.trigger_description());
            }
            assert!(!bound.shortcuts().is_empty());
            let out = std::process::Command::new("hyprctl").arg("globalshortcuts").output();
            if let Ok(o) = out {
                println!("hyprctl globalshortcuts:\n{}", String::from_utf8_lossy(&o.stdout));
            }
        });
    }
}
