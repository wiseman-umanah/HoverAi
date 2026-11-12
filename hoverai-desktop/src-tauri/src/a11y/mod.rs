// ─── Accessibility snapshot ──────────────────────────────────────────────────
//
// Collects the interactive, on-screen UI elements of the currently focused
// window from the OS accessibility API so the model can point at real
// elements instead of guessing pixel positions.
//
//   - Windows: UI Automation (`windows.rs`), physical screen pixels.
//   - Linux:   AT-SPI over D-Bus (`linux.rs`); window-relative on Wayland.
//   - macOS:   not implemented yet (AX later) → always `Err`.
//
// Every backend bounds its work (node budget, depth limit, deadline) and
// returns whatever it collected when the deadline hits; `finalize` then caps
// the list at `MAX_ELEMENTS` and sorts it into reading order.

use std::time::Duration;

#[cfg(target_os = "linux")]
mod linux;
#[cfg(target_os = "macos")]
mod macos;
#[cfg(target_os = "windows")]
mod windows;

/// Coordinate space of `UiElement` rectangles.
#[derive(Debug, Clone, Copy, PartialEq)]
pub enum CoordSpace {
    /// Physical screen pixels (Windows UIA, X11 AT-SPI).
    Screen,
    /// Relative to the focused window's top-left (AT-SPI on Wayland, where
    /// apps don't know their global position). Caller adds the window origin.
    /// Only produced by the Linux backend.
    #[cfg_attr(not(target_os = "linux"), allow(dead_code))]
    WindowRelative,
}

#[derive(Debug, Clone)]
pub struct UiElement {
    /// Normalised role, lower-case: "button", "menu item", "text field", "link", "tab", …
    pub role: String,
    /// Accessible name / label (may be empty for unnamed icons).
    pub name: String,
    pub x: i32,
    pub y: i32,
    pub w: i32,
    pub h: i32,
    pub enabled: bool,
    pub focused: bool,
}

#[derive(Debug, Clone, Default)]
pub struct Snapshot {
    pub app: Option<String>,
    pub window_title: Option<String>,
    pub elements: Vec<UiElement>,
    pub coords: Option<CoordSpace>,
}

/// Max elements returned.
#[allow(dead_code)]
pub(crate) const MAX_ELEMENTS: usize = 200;
/// Max tree nodes inspected per snapshot.
#[allow(dead_code)]
pub(crate) const MAX_NODES: usize = 4000;
/// Max depth below the window walked.
#[allow(dead_code)]
pub(crate) const MAX_DEPTH: usize = 40;
/// Names are truncated to this many characters.
#[allow(dead_code)]
pub(crate) const MAX_NAME_CHARS: usize = 80;

/// Blocking. Snapshot of the focused window's interactive elements, giving up
/// after `timeout`. Must be called while HoverAI's own overlay is hidden.
pub fn snapshot(timeout: Duration) -> Result<Snapshot, String> {
    #[cfg(target_os = "linux")]
    {
        linux::snapshot(timeout)
    }
    #[cfg(target_os = "windows")]
    {
        windows::snapshot(timeout)
    }
    #[cfg(target_os = "macos")]
    {
        macos::snapshot(timeout)
    }
    #[cfg(not(any(target_os = "linux", target_os = "windows", target_os = "macos")))]
    {
        let _ = timeout;
        Err("accessibility snapshot not implemented on this platform".into())
    }
}

/// Collapses whitespace, trims and truncates to `MAX_NAME_CHARS` (char-safe).
#[allow(dead_code)]
pub(crate) fn normalize_name(s: &str) -> String {
    let mut out = String::new();
    for (n, word) in s.split_whitespace().enumerate() {
        if n > 0 {
            out.push(' ');
        }
        out.push_str(word);
    }
    if out.chars().count() > MAX_NAME_CHARS {
        let mut t: String = out.chars().take(MAX_NAME_CHARS - 1).collect();
        t.push('…');
        return t;
    }
    out
}

/// Drops duplicates, caps at `MAX_ELEMENTS` (named first, then larger), and
/// sorts the survivors into reading order (top-to-bottom, left-to-right).
#[allow(dead_code)]
pub(crate) fn finalize(mut snap: Snapshot) -> Snapshot {
    let els = &mut snap.elements;
    // Same role+name+rect reported twice (e.g. a GTK button and its proxy).
    els.sort_by(|a, b| {
        (a.x, a.y, a.w, a.h, &a.role, &a.name).cmp(&(b.x, b.y, b.w, b.h, &b.role, &b.name))
    });
    els.dedup_by(|a, b| {
        a.x == b.x && a.y == b.y && a.w == b.w && a.h == b.h && a.role == b.role && a.name == b.name
    });
    if els.len() > MAX_ELEMENTS {
        els.sort_by_key(|e| {
            (
                e.name.is_empty(),
                std::cmp::Reverse(i64::from(e.w) * i64::from(e.h)),
            )
        });
        els.truncate(MAX_ELEMENTS);
    }
    els.sort_by_key(|e| (e.y, e.x));
    snap
}

#[cfg(test)]
mod tests {
    use super::*;

    fn el(name: &str, x: i32, y: i32, w: i32) -> UiElement {
        UiElement {
            role: "button".into(),
            name: name.into(),
            x,
            y,
            w,
            h: 10,
            enabled: true,
            focused: false,
        }
    }

    #[test]
    fn normalize_name_trims_and_truncates() {
        assert_eq!(normalize_name("  Save \n  As…  "), "Save As…");
        let long = "é".repeat(200);
        let n = normalize_name(&long);
        assert_eq!(n.chars().count(), MAX_NAME_CHARS);
        assert!(n.ends_with('…'));
    }

    #[test]
    fn finalize_caps_dedups_and_orders() {
        let mut elements: Vec<UiElement> = (0..300).map(|i| el("", i, 0, 1)).collect();
        elements.push(el("Big", 5, 50, 100));
        elements.push(el("Big", 5, 50, 100));
        elements.push(el("Top", 9, -1, 2));
        let s = finalize(Snapshot {
            elements,
            ..Default::default()
        });
        assert_eq!(s.elements.len(), MAX_ELEMENTS);
        assert_eq!(s.elements[0].name, "Top");
        assert_eq!(s.elements.iter().filter(|e| e.name == "Big").count(), 1);
        assert!(s.elements.windows(2).all(|w| (w[0].y, w[0].x) <= (w[1].y, w[1].x)));
    }
}
