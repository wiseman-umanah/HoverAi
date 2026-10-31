// ─── Screen context ───────────────────────────────────────────────────────────
//
// Everything the model gets besides pixels: the focused app/window, cursor
// position and the interactive elements from the accessibility tree — all
// normalised to the captured monitor (0–1, top-left origin) so they line up
// with the screenshot and with the overlay, which covers the same monitor.
//
// Element ids are assigned here in reading order (top→bottom, left→right);
// marks.rs draws the same ids on the screenshot, and the backend answers
// with `target.element_id`.
//
// Geometry sources:
//   - Windows / macOS / X11: xcap (focused window) + accessibility rects in
//     the same physical-pixel space as the xcap monitor.
//   - Hyprland: `hyprctl` (logical layout px) — Wayland clients can't know
//     global positions, so AT-SPI rects arrive window-relative and are
//     offset by the window origin from hyprctl.

use std::time::{Duration, Instant};

use serde::Serialize;

use crate::a11y::{self, CoordSpace};

const A11Y_TIMEOUT: Duration = Duration::from_millis(700);
/// ~19 prompt tokens per element; 120 keeps image + list under Groq's
/// free-tier 7k tokens/minute. a11y::finalize already orders named first.
const MAX_ELEMENTS: usize = 120;

/// A rectangle in some absolute coordinate space.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Frame {
    pub x: f64,
    pub y: f64,
    pub w: f64,
    pub h: f64,
}

impl Frame {
    /// Normalise `r` (same space as `self`) to 0–1 within this frame, clipped.
    /// None if nothing of it is inside.
    fn normalise(&self, r: Frame) -> Option<NRect> {
        let x0 = ((r.x - self.x) / self.w).clamp(0.0, 1.0);
        let y0 = ((r.y - self.y) / self.h).clamp(0.0, 1.0);
        let x1 = ((r.x + r.w - self.x) / self.w).clamp(0.0, 1.0);
        let y1 = ((r.y + r.h - self.y) / self.h).clamp(0.0, 1.0);
        (x1 - x0 > 0.0005 && y1 - y0 > 0.0005).then_some(NRect {
            x: round4(x0),
            y: round4(y0),
            w: round4(x1 - x0),
            h: round4(y1 - y0),
        })
    }
}

fn round4(v: f64) -> f64 {
    (v * 10_000.0).round() / 10_000.0
}

#[derive(Debug, Clone, Copy, Serialize, PartialEq)]
pub struct NRect {
    pub x: f64,
    pub y: f64,
    pub w: f64,
    pub h: f64,
}

#[derive(Debug, Clone, Copy, Serialize)]
pub struct NPoint {
    pub x: f64,
    pub y: f64,
}

#[derive(Debug, Clone, Serialize)]
pub struct ContextElement {
    pub id: u32,
    pub role: String,
    pub name: String,
    #[serde(flatten)]
    pub rect: NRect,
    pub enabled: bool,
    /// Has keyboard focus (only serialised when true).
    #[serde(skip_serializing_if = "std::ops::Not::not")]
    pub focused: bool,
}

/// Serialised as the `context` form field of POST /query.
#[derive(Debug, Clone, Default, Serialize)]
pub struct ScreenContext {
    pub app: Option<String>,
    pub window_title: Option<String>,
    pub window: Option<NRect>,
    pub cursor: Option<NPoint>,
    pub elements: Vec<ContextElement>,
}

/// Geometry of the captured monitor, in each space we may receive rects in.
#[derive(Debug, Clone, Copy)]
pub struct MonitorGeometry {
    /// xcap/OS space: physical px on Windows/X11, points on macOS.
    pub os: Frame,
    /// Hyprland logical layout space, when running under Hyprland.
    pub hypr: Option<Frame>,
}

struct ActiveWindow {
    app: Option<String>,
    title: Option<String>,
    rect: Option<Frame>,
    /// Which space `rect` (and screen-space a11y rects) are in.
    hypr_space: bool,
}

/// Gather context for the monitor described by `geo`. Blocking (~≤0.8 s).
/// `cursor_os` is the cursor in OS space (physical px), if known.
pub fn gather(geo: MonitorGeometry, cursor_os: Option<(f64, f64)>) -> ScreenContext {
    let started = Instant::now();
    let snapshot = match a11y::snapshot(A11Y_TIMEOUT) {
        Ok(s) => Some(s),
        Err(e) => {
            log::info!("[context] accessibility unavailable: {e}");
            None
        }
    };
    let win = active_window(&geo);

    let mut ctx = ScreenContext {
        app: snapshot.as_ref().and_then(|s| s.app.clone()).or(win.app.clone()),
        window_title: snapshot
            .as_ref()
            .and_then(|s| s.window_title.clone())
            .or(win.title.clone()),
        ..Default::default()
    };

    let frame = if win.hypr_space { geo.hypr } else { Some(geo.os) };
    if let (Some(frame), Some(rect)) = (frame, win.rect) {
        ctx.window = frame.normalise(rect);
    }
    ctx.cursor = cursor(&geo, cursor_os);

    if let (Some(snap), Some(frame)) = (snapshot, frame) {
        let mut placed: Vec<(NRect, a11y::UiElement)> = snap
            .elements
            .into_iter()
            .filter_map(|e| {
                let rel = Frame { x: e.x as f64, y: e.y as f64, w: e.w as f64, h: e.h as f64 };
                let abs = match snap.coords {
                    Some(CoordSpace::WindowRelative) => {
                        let origin = win.rect?;
                        Frame { x: origin.x + rel.x, y: origin.y + rel.y, ..rel }
                    }
                    _ => rel,
                };
                frame.normalise(abs).map(|n| (n, e))
            })
            // Skip big containers (documents, scroll panes) — noise as marks.
            .filter(|(n, _)| n.w * n.h < 0.3)
            .collect();
        // Keep the most useful elements if over budget: named first, then larger.
        if placed.len() > MAX_ELEMENTS {
            placed.sort_by(|(ra, a), (rb, b)| {
                a.name.is_empty()
                    .cmp(&b.name.is_empty())
                    .then((rb.w * rb.h).total_cmp(&(ra.w * ra.h)))
            });
            placed.truncate(MAX_ELEMENTS);
        }
        // Reading order: rows of ~1% height, then left→right.
        placed.sort_by(|(a, _), (b, _)| {
            ((a.y * 100.0) as i64, (a.x * 10_000.0) as i64)
                .cmp(&((b.y * 100.0) as i64, (b.x * 10_000.0) as i64))
        });
        ctx.elements = placed
            .into_iter()
            .enumerate()
            .map(|(i, (rect, e))| ContextElement {
                id: i as u32 + 1,
                role: e.role,
                name: e.name,
                rect,
                enabled: e.enabled,
                focused: e.focused,
            })
            .collect();
    }

    log::info!(
        "[context] app={:?} title={:?} elements={} in {} ms",
        ctx.app,
        ctx.window_title,
        ctx.elements.len(),
        started.elapsed().as_millis()
    );
    ctx
}

fn cursor(geo: &MonitorGeometry, cursor_os: Option<(f64, f64)>) -> Option<NPoint> {
    let inside = |f: Frame, (x, y): (f64, f64)| {
        let p = ((x - f.x) / f.w, (y - f.y) / f.h);
        ((0.0..=1.0).contains(&p.0) && (0.0..=1.0).contains(&p.1))
            .then(|| NPoint { x: round4(p.0), y: round4(p.1) })
    };
    if let Some(hypr) = geo.hypr {
        if let Some(p) = hyprland::cursor() {
            return inside(hypr, p);
        }
    }
    cursor_os.and_then(|p| inside(geo.os, p))
}

fn active_window(geo: &MonitorGeometry) -> ActiveWindow {
    if geo.hypr.is_some() {
        if let Some(w) = hyprland::active_window() {
            return w;
        }
    }
    os_active_window()
}

/// Focused window via xcap (Windows, macOS, X11). Skips HoverAI itself.
fn os_active_window() -> ActiveWindow {
    let none = ActiveWindow { app: None, title: None, rect: None, hypr_space: false };
    let Ok(windows) = xcap::Window::all() else { return none };
    for w in windows {
        if !w.is_focused().unwrap_or(false) || w.is_minimized().unwrap_or(false) {
            continue;
        }
        let title = w.title().ok();
        if title.as_deref().is_some_and(|t| t.starts_with("HoverAI")) {
            continue;
        }
        let rect = match (w.x(), w.y(), w.width(), w.height()) {
            (Ok(x), Ok(y), Ok(wd), Ok(ht)) => {
                Some(Frame { x: x as f64, y: y as f64, w: wd as f64, h: ht as f64 })
            }
            _ => None,
        };
        return ActiveWindow { app: w.app_name().ok(), title, rect, hypr_space: false };
    }
    none
}

/// Hyprland IPC via `hyprctl -j` (logical layout coordinates).
pub mod hyprland {
    use super::{ActiveWindow, Frame};
    use serde_json::Value;

    pub fn is_hyprland() -> bool {
        std::env::var_os("HYPRLAND_INSTANCE_SIGNATURE").is_some()
    }

    fn hyprctl(args: &[&str]) -> Option<Value> {
        if !is_hyprland() {
            return None;
        }
        let out = std::process::Command::new("hyprctl").args(args).output().ok()?;
        if !out.status.success() {
            return None;
        }
        serde_json::from_slice(&out.stdout).ok()
    }

    /// Logical frame of the monitor containing logical point (x, y), or the
    /// focused monitor when no point is given.
    pub fn monitor_frame(point: Option<(f64, f64)>) -> Option<Frame> {
        let monitors = hyprctl(&["monitors", "-j"])?;
        let frames = monitors.as_array()?.iter().filter_map(|m| {
            let scale = m["scale"].as_f64().filter(|s| *s > 0.0).unwrap_or(1.0);
            let rotated = m["transform"].as_i64().unwrap_or(0) % 2 == 1;
            let (pw, ph) = (m["width"].as_f64()?, m["height"].as_f64()?);
            let (w, h) = if rotated { (ph, pw) } else { (pw, ph) };
            let frame = Frame { x: m["x"].as_f64()?, y: m["y"].as_f64()?, w: w / scale, h: h / scale };
            Some((frame, m["focused"].as_bool().unwrap_or(false)))
        });
        let frames: Vec<_> = frames.collect();
        if let Some((px, py)) = point {
            if let Some((f, _)) = frames
                .iter()
                .find(|(f, _)| px >= f.x && px < f.x + f.w && py >= f.y && py < f.y + f.h)
            {
                return Some(*f);
            }
        }
        frames.iter().find(|(_, focused)| *focused).or(frames.first()).map(|(f, _)| *f)
    }

    pub fn cursor() -> Option<(f64, f64)> {
        let v = hyprctl(&["cursorpos", "-j"])?;
        Some((v["x"].as_f64()?, v["y"].as_f64()?))
    }

    pub(super) fn active_window() -> Option<ActiveWindow> {
        let w = hyprctl(&["activewindow", "-j"])?;
        let title = w["title"].as_str().map(str::to_string);
        if title.as_deref().is_some_and(|t| t.starts_with("HoverAI")) {
            return None;
        }
        let at = w["at"].as_array()?;
        let size = w["size"].as_array()?;
        let rect = Frame {
            x: at.first()?.as_f64()?,
            y: at.get(1)?.as_f64()?,
            w: size.first()?.as_f64()?,
            h: size.get(1)?.as_f64()?,
        };
        Some(ActiveWindow {
            app: w["class"].as_str().map(str::to_string),
            title,
            rect: Some(rect),
            hypr_space: true,
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn normalise_clips_to_frame() {
        let f = Frame { x: 100.0, y: 0.0, w: 1000.0, h: 500.0 };
        let n = f.normalise(Frame { x: 50.0, y: 250.0, w: 200.0, h: 100.0 }).unwrap();
        assert_eq!(n, NRect { x: 0.0, y: 0.5, w: 0.15, h: 0.2 });
        assert!(f.normalise(Frame { x: 0.0, y: 0.0, w: 50.0, h: 50.0 }).is_none());
    }

    /// Desktop check: prints the context for the focused window.
    /// `cargo test --lib context::tests::gathers_context -- --ignored --nocapture`
    #[test]
    #[ignore]
    fn gathers_context() {
        let hypr = hyprland::monitor_frame(hyprland::cursor());
        let m = xcap::Monitor::all().unwrap().into_iter().next().unwrap();
        let os = Frame {
            x: m.x().unwrap() as f64,
            y: m.y().unwrap() as f64,
            w: m.width().unwrap() as f64,
            h: m.height().unwrap() as f64,
        };
        let ctx = gather(MonitorGeometry { os, hypr }, None);
        println!("{}", serde_json::to_string_pretty(&ctx).unwrap());
    }
}
