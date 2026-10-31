// ─── Overlay hit regions ──────────────────────────────────────────────────────
//
// The overlay window covers the whole monitor (Phase 3 beacons will be drawn
// anywhere on it), but only the visible widgets — recording card, steps
// panel, error card — may take mouse input. Everywhere else, clicks must
// reach the app underneath (bug D4: v1 blocked the whole screen).
//
// The overlay reports its interactive rectangles (CSS px, relative to the
// window) after every render via the `overlay_hit_regions` command.
//
// - Linux (X11 + Wayland): the GDK input region is set to exactly those
//   rectangles, so the compositor routes clicks outside them elsewhere.
// - Windows / macOS: no per-region input shape for transparent windows, so a
//   poller toggles whole-window click-through depending on whether the
//   global cursor is inside one of the rectangles.

use std::sync::atomic::{AtomicU64, Ordering};

use serde::Deserialize;
use tauri::{AppHandle, WebviewWindow};

/// Bumped whenever lib.rs changes the overlay's click-through state itself
/// (show/hide/thinking), so the poller re-applies instead of trusting its cache.
static EPOCH: AtomicU64 = AtomicU64::new(0);

pub fn invalidate() {
    EPOCH.fetch_add(1, Ordering::SeqCst);
}

#[derive(Debug, Clone, Copy, Deserialize, PartialEq)]
pub struct Rect {
    pub x: f64,
    pub y: f64,
    pub w: f64,
    pub h: f64,
}

impl Rect {
    #[cfg_attr(target_os = "linux", allow(dead_code))]
    fn contains(&self, px: f64, py: f64) -> bool {
        px >= self.x && px <= self.x + self.w && py >= self.y && py <= self.y + self.h
    }
}

/// Restrict the overlay's mouse input to `rects` (empty = fully click-through).
#[cfg(target_os = "linux")]
pub fn apply(overlay: &WebviewWindow, rects: Vec<Rect>) {
    let win = overlay.clone();
    let _ = overlay.run_on_main_thread(move || {
        use gtk::cairo::{RectangleInt, Region};
        use gtk::prelude::*;

        let Ok(gtk_window) = win.gtk_window() else { return };
        let Some(gdk_window) = gtk_window.window() else { return };
        let region = Region::create();
        for r in &rects {
            let rect = RectangleInt::new(
                r.x.floor() as i32,
                r.y.floor() as i32,
                r.w.ceil() as i32,
                r.h.ceil() as i32,
            );
            let _ = region.union_rectangle(&rect);
        }
        // Same trick tao uses for "ignore all": a 1×1 region at the corner.
        if rects.is_empty() {
            let _ = region.union_rectangle(&RectangleInt::new(0, 0, 1, 1));
        }
        gdk_window.input_shape_combine_region(&region, 0, 0);
    });
}

#[cfg(not(target_os = "linux"))]
pub fn apply(_overlay: &WebviewWindow, _rects: Vec<Rect>) {
    // Windows/macOS: handled continuously by `start_poller`.
}

/// Windows/macOS: toggle click-through as the cursor enters/leaves a region.
#[cfg(not(target_os = "linux"))]
pub fn start_poller(app: AppHandle) {
    use std::time::Duration;
    use tauri::Manager;

    std::thread::Builder::new()
        .name("hover-hit-test".into())
        .spawn(move || {
            let mut last: Option<bool> = None;
            let mut seen_epoch = EPOCH.load(Ordering::SeqCst);
            loop {
                std::thread::sleep(Duration::from_millis(30));
                let epoch = EPOCH.load(Ordering::SeqCst);
                if epoch != seen_epoch {
                    seen_epoch = epoch;
                    last = None;
                }
                let Some(overlay) = app.get_webview_window("overlay") else { continue };
                let Some(rects) = crate::interactive_regions(&app) else {
                    last = None; // overlay idle/hidden — Rust manages click-through
                    continue;
                };
                let (Ok(cursor), Ok(origin), Ok(scale)) = (
                    app.cursor_position(),
                    overlay.outer_position(),
                    overlay.scale_factor(),
                ) else {
                    continue;
                };
                let cx = (cursor.x - origin.x as f64) / scale;
                let cy = (cursor.y - origin.y as f64) / scale;
                let ignore = !rects.iter().any(|r| r.contains(cx, cy));
                if last != Some(ignore) {
                    let _ = overlay.set_ignore_cursor_events(ignore);
                    last = Some(ignore);
                }
            }
        })
        .expect("spawn hit-test thread");
}

#[cfg(target_os = "linux")]
pub fn start_poller(_app: AppHandle) {}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn contains_is_inclusive_of_edges() {
        let r = Rect { x: 10.0, y: 10.0, w: 100.0, h: 50.0 };
        assert!(r.contains(10.0, 10.0));
        assert!(r.contains(110.0, 60.0));
        assert!(!r.contains(9.9, 30.0));
        assert!(!r.contains(50.0, 60.1));
    }
}
