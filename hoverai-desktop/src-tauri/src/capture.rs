// ─── Screen capture ───────────────────────────────────────────────────────────
//
// Captures the monitor under a given point (the cursor) with xcap, then
// downscales and JPEG-encodes it client-side so uploads stay small (a few
// hundred KB) instead of multi-MB base64 PNGs that the server rejected.
//
// Backends (via xcap): Windows GDI/DXGI, macOS CoreGraphics, Linux X11, and
// Linux Wayland (wlr-screencopy / portal). On wlroots compositors (Hyprland,
// Sway) we fall back to the `grim` CLI if xcap fails.
//
// `capture_with_context` additionally gathers the screen context (focused
// window + accessibility elements, context.rs) in parallel — both must run
// before the overlay is shown — and draws numbered marks (marks.rs) on the
// copy sent to the model.

use std::io::Cursor;

use xcap::image::{self, imageops::FilterType, DynamicImage, RgbaImage};
use xcap::Monitor;

use crate::context::{self, Frame, MonitorGeometry, ScreenContext};

/// Longest edge sent to the backend. Large enough to keep UI text legible.
const MAX_EDGE: u32 = 1600;
const JPEG_QUALITY: u8 = 80;

pub struct Screenshot {
    pub jpeg: Vec<u8>,
}

/// Capture the monitor containing `point` (physical pixels, or None for the
/// primary monitor) and return a resized JPEG.
pub fn capture(point: Option<(i32, i32)>) -> Result<Screenshot, String> {
    let rgba = capture_rgba(pick_monitor(point))?;
    Ok(Screenshot { jpeg: encode_jpeg(rgba)? })
}

pub struct MarkedScreenshot {
    /// JPEG with numbered marks drawn on the accessibility elements.
    pub jpeg: Vec<u8>,
    pub context: ScreenContext,
}

/// Screenshot + screen context of the monitor under `point`, marks drawn.
pub fn capture_with_context(point: Option<(i32, i32)>) -> Result<MarkedScreenshot, String> {
    let monitor = pick_monitor(point);
    let geometry = monitor.as_ref().ok().and_then(|m| {
        Some(MonitorGeometry {
            os: Frame {
                x: m.x().ok()? as f64,
                y: m.y().ok()? as f64,
                w: m.width().ok()? as f64,
                h: m.height().ok()? as f64,
            },
            hypr: context::hyprland::monitor_frame(context::hyprland::cursor()),
        })
    });
    let cursor = point.map(|(x, y)| (x as f64, y as f64));

    // Accessibility can take a few hundred ms — run it alongside the capture.
    let (rgba, ctx) = std::thread::scope(|scope| {
        let ctx = scope.spawn(move || geometry.map(|g| context::gather(g, cursor)).unwrap_or_default());
        let rgba = capture_rgba(monitor);
        (rgba, ctx.join().unwrap_or_default())
    });
    let mut rgba = rgba?;
    crate::marks::draw(&mut rgba, &ctx.elements);
    Ok(MarkedScreenshot { jpeg: encode_jpeg(rgba)?, context: ctx })
}

fn capture_rgba(monitor: Result<Monitor, String>) -> Result<RgbaImage, String> {
    match monitor.and_then(|m| m.capture_image().map_err(|e| e.to_string())) {
        Ok(img) => Ok(img),
        Err(e) => {
            log::warn!("[capture] xcap failed: {e}");
            #[cfg(target_os = "linux")]
            if let Some(img) = grim_fallback() {
                return Ok(img);
            }
            Err(format!("screen capture failed: {e}"))
        }
    }
}

fn pick_monitor(point: Option<(i32, i32)>) -> Result<Monitor, String> {
    if let Some((x, y)) = point {
        if let Ok(m) = Monitor::from_point(x, y) {
            return Ok(m);
        }
    }
    let monitors = Monitor::all().map_err(|e| format!("could not list monitors: {e}"))?;
    let mut fallback = None;
    for m in monitors {
        if m.is_primary().unwrap_or(false) {
            return Ok(m);
        }
        fallback.get_or_insert(m);
    }
    fallback.ok_or_else(|| "no monitors found".to_string())
}

#[cfg(target_os = "linux")]
fn grim_fallback() -> Option<RgbaImage> {
    std::env::var_os("WAYLAND_DISPLAY")?;
    let out = std::process::Command::new("grim")
        .args(["-t", "png", "-"])
        .output()
        .ok()?;
    if !out.status.success() {
        log::warn!("[capture] grim failed: {}", String::from_utf8_lossy(&out.stderr).trim());
        return None;
    }
    log::info!("[capture] used grim fallback");
    image::load_from_memory_with_format(&out.stdout, image::ImageFormat::Png)
        .ok()
        .map(|i| i.into_rgba8())
}

fn encode_jpeg(rgba: RgbaImage) -> Result<Vec<u8>, String> {
    let (w, h) = rgba.dimensions();
    let img = DynamicImage::ImageRgba8(rgba);
    let img = if w.max(h) > MAX_EDGE {
        // Triangle (bilinear) is ~5× faster than Lanczos and fine for UI text at this size.
        img.resize(MAX_EDGE, MAX_EDGE, FilterType::Triangle)
    } else {
        img
    };
    let rgb = img.into_rgb8();
    let mut buf = Cursor::new(Vec::with_capacity(400 * 1024));
    let encoder = image::codecs::jpeg::JpegEncoder::new_with_quality(&mut buf, JPEG_QUALITY);
    rgb.write_with_encoder(encoder)
        .map_err(|e| format!("jpeg encode failed: {e}"))?;
    log::info!(
        "[capture] {w}x{h} → {}x{} JPEG, {} KB",
        rgb.width(),
        rgb.height(),
        buf.get_ref().len() / 1024
    );
    Ok(buf.into_inner())
}

#[cfg(test)]
mod tests {
    use super::*;
    use xcap::image::ImageFormat;

    #[test]
    fn large_capture_is_downscaled_to_max_edge() {
        let img = RgbaImage::from_pixel(3840, 2160, image::Rgba([40, 40, 40, 255]));
        let jpeg = encode_jpeg(img).unwrap();
        let decoded = image::load_from_memory_with_format(&jpeg, ImageFormat::Jpeg).unwrap();
        assert_eq!((decoded.width(), decoded.height()), (1600, 900));
    }

    /// Hardware check — captures the real screen. Run with:
    /// `cargo test --lib capture::tests::captures_real_screen -- --ignored --nocapture`
    #[test]
    #[ignore]
    fn captures_real_screen() {
        let shot = capture(None).expect("screen capture");
        let img = image::load_from_memory_with_format(&shot.jpeg, ImageFormat::Jpeg).unwrap();
        println!("captured {}x{}, {} KB", img.width(), img.height(), shot.jpeg.len() / 1024);
        assert!(img.width() > 0 && shot.jpeg.len() > 1000);
    }

    /// Desktop check — marked screenshot + context of the focused window,
    /// saved to /tmp/hoverai-marked.jpg. Run with:
    /// `cargo test --lib capture::tests::captures_marked_screen -- --ignored --nocapture`
    #[test]
    #[ignore]
    fn captures_marked_screen() {
        let shot = capture_with_context(None).expect("capture");
        std::fs::write("/tmp/hoverai-marked.jpg", &shot.jpeg).unwrap();
        println!(
            "app={:?} title={:?} elements={} → /tmp/hoverai-marked.jpg",
            shot.context.app,
            shot.context.window_title,
            shot.context.elements.len()
        );
    }
}
