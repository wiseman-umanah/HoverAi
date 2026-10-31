// ─── Accessibility snapshot — Windows (UI Automation) ────────────────────────
//
//   1. `GetForegroundWindow` → UIA element (+ owning process name for `app`).
//   2. Breadth-first walk of the *control view* below it. Each expanded node
//      costs one cross-process call: `BuildUpdatedCache` with a cache request
//      of scope `Children`, which returns all children with Name, ControlType,
//      BoundingRectangle, IsEnabled, HasKeyboardFocus, IsOffscreen, … already
//      cached. Offscreen subtrees are pruned.
//   3. The walk runs on its own thread (fresh COM MTA apartment). The caller
//      waits until the deadline and returns whatever was collected; a UIA call
//      stuck on a hung app can't block the caller past `timeout` (the worker
//      notices the cancel flag after that call returns and exits).
//
// BoundingRectangle is in physical screen pixels → `CoordSpace::Screen`
// (HoverAI is per-monitor DPI aware via Tauri, so these match xcap's capture).
//
// Limitations: apps that implement neither UIA nor MSAA (some games, custom
// canvas UIs) expose only the window; Chromium/Electron expose web content
// once UIA asks for it (usually immediate, sometimes sparse on first call).

use std::collections::VecDeque;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::mpsc;
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use uiautomation::core::UICacheRequest;
use uiautomation::types::{ControlType, Handle, TreeScope, UIProperty};
use uiautomation::{UIAutomation, UIElement};
use windows::core::PWSTR;
use windows::Win32::Foundation::CloseHandle;
use windows::Win32::System::Threading::{
    OpenProcess, QueryFullProcessImageNameW, PROCESS_NAME_WIN32, PROCESS_QUERY_LIMITED_INFORMATION,
};
use windows::Win32::UI::WindowsAndMessaging::{GetForegroundWindow, GetWindowThreadProcessId};

use super::{finalize, normalize_name, CoordSpace, Snapshot, UiElement, MAX_DEPTH, MAX_NODES};

/// Max children followed per node (huge lists/grids).
const MAX_CHILDREN: usize = 300;

#[derive(Default)]
struct Partial {
    app: Option<String>,
    window_title: Option<String>,
    started: bool,
    elements: Vec<UiElement>,
}

pub fn snapshot(timeout: Duration) -> Result<Snapshot, String> {
    let deadline = Instant::now() + timeout;
    let partial = Arc::new(Mutex::new(Partial::default()));
    let cancel = Arc::new(AtomicBool::new(false));
    let (tx, rx) = mpsc::channel::<Result<(), String>>();

    {
        let partial = Arc::clone(&partial);
        let cancel = Arc::clone(&cancel);
        std::thread::Builder::new()
            .name("a11y-uia".into())
            .spawn(move || {
                let r = walk(&partial, &cancel, deadline);
                let _ = tx.send(r);
            })
            .map_err(|e| format!("a11y: spawn UIA thread: {e}"))?;
    }

    let outcome = rx.recv_timeout(deadline.saturating_duration_since(Instant::now()));
    cancel.store(true, Ordering::Relaxed);
    if let Ok(Err(e)) = outcome {
        return Err(e);
    }
    if let Err(mpsc::RecvTimeoutError::Disconnected) = outcome {
        return Err("a11y: UIA worker exited unexpectedly".into());
    }
    let mut p = partial.lock().unwrap_or_else(|e| e.into_inner());
    if !p.started {
        return Err("a11y: timed out reading the foreground window".into());
    }
    Ok(finalize(Snapshot {
        app: p.app.take(),
        window_title: p.window_title.take(),
        elements: std::mem::take(&mut p.elements),
        coords: Some(CoordSpace::Screen),
    }))
}

/// How a control type is treated when collecting elements.
#[derive(Clone, Copy, PartialEq)]
enum Kind {
    /// Always a click/type target; its subtree is not walked.
    Leaf,
    /// Always a target, but may contain further targets (menu items, tree items…).
    Target,
    /// Only a target if named and focusable/invokable (text, images, groups…).
    Maybe,
    /// Pure container: walked, never reported.
    Container,
    /// Not reported and not walked.
    Skip,
}

fn classify(ct: ControlType, password: bool) -> (&'static str, Kind) {
    use Kind::*;
    match ct {
        ControlType::Button => ("button", Leaf),
        ControlType::CheckBox => ("check box", Leaf),
        ControlType::ComboBox => ("combo box", Leaf),
        ControlType::Edit if password => ("password field", Leaf),
        ControlType::Edit => ("text field", Leaf),
        ControlType::Hyperlink => ("link", Leaf),
        ControlType::Image => ("image", Maybe),
        ControlType::ListItem => ("list item", Target),
        ControlType::MenuItem => ("menu item", Target),
        ControlType::RadioButton => ("radio button", Leaf),
        ControlType::Slider => ("slider", Leaf),
        ControlType::Spinner => ("spin button", Leaf),
        ControlType::TabItem => ("tab", Target),
        ControlType::Text => ("text", Maybe),
        ControlType::TreeItem => ("tree item", Target),
        ControlType::Custom => ("element", Maybe),
        ControlType::Group => ("group", Maybe),
        ControlType::DataItem => ("data item", Target),
        ControlType::SplitButton => ("split button", Leaf),
        ControlType::HeaderItem => ("column header", Leaf),
        ControlType::Pane => ("pane", Maybe),
        ControlType::ProgressBar
        | ControlType::ScrollBar
        | ControlType::ToolTip
        | ControlType::Thumb
        | ControlType::Separator => ("", Skip),
        _ => ("", Container),
    }
}

fn cached_bool(el: &UIElement, prop: UIProperty) -> bool {
    el.get_cached_property_value(prop)
        .ok()
        .and_then(|v| TryInto::<bool>::try_into(&v).ok())
        .unwrap_or(false)
}

fn cache_request(automation: &UIAutomation) -> uiautomation::Result<UICacheRequest> {
    let req = automation.create_cache_request()?;
    for p in [
        UIProperty::Name,
        UIProperty::ControlType,
        UIProperty::BoundingRectangle,
        UIProperty::IsEnabled,
        UIProperty::HasKeyboardFocus,
        UIProperty::IsKeyboardFocusable,
        UIProperty::IsOffscreen,
        UIProperty::IsPassword,
        UIProperty::IsInvokePatternAvailable,
        UIProperty::ProcessId,
    ] {
        req.add_property(p)?;
    }
    // Default tree filter is the control view.
    Ok(req)
}

fn process_name(pid: u32) -> Option<String> {
    // SAFETY: plain Win32 calls; the handle is closed before returning and the
    // buffer outlives the call.
    unsafe {
        let h = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, false, pid).ok()?;
        let mut buf = [0u16; 1024];
        let mut len = buf.len() as u32;
        let ok = QueryFullProcessImageNameW(h, PROCESS_NAME_WIN32, PWSTR(buf.as_mut_ptr()), &mut len);
        let _ = CloseHandle(h);
        ok.ok()?;
        let path = String::from_utf16_lossy(buf.get(..len as usize)?);
        let file = path.rsplit(['\\', '/']).next().unwrap_or(&path).to_string();
        Some(
            file.strip_suffix(".exe")
                .or_else(|| file.strip_suffix(".EXE"))
                .unwrap_or(&file)
                .to_string(),
        )
    }
}

fn walk(partial: &Mutex<Partial>, cancel: &AtomicBool, deadline: Instant) -> Result<(), String> {
    let automation = UIAutomation::new()
        .or_else(|_| UIAutomation::new_direct())
        .map_err(|e| format!("a11y: UI Automation unavailable: {e}"))?;
    let req = cache_request(&automation).map_err(|e| format!("a11y: cache request: {e}"))?;
    let children_req = cache_request(&automation).map_err(|e| format!("a11y: cache request: {e}"))?;
    children_req
        .set_tree_scope(TreeScope::Children)
        .map_err(|e| format!("a11y: cache request: {e}"))?;

    // SAFETY: no preconditions; returns a null HWND when nothing is foreground.
    let hwnd = unsafe { GetForegroundWindow() };
    if hwnd.is_invalid() {
        return Err("a11y: no foreground window".into());
    }
    let mut pid = 0u32;
    // SAFETY: `pid` outlives the call.
    unsafe { GetWindowThreadProcessId(hwnd, Some(&mut pid)) };
    let app = if pid != 0 { process_name(pid) } else { None };

    let window = automation
        .element_from_handle_build_cache(Handle::from(hwnd), &req)
        .map_err(|e| format!("a11y: foreground window not accessible: {e}"))?;
    let title = normalize_name(&window.get_cached_name().unwrap_or_default());
    if title == "HoverAI"
        || app
            .as_deref()
            .is_some_and(|a| a.to_ascii_lowercase().starts_with("hoverai"))
    {
        return Err("a11y: the foreground window is HoverAI itself".into());
    }
    let win_rect = window
        .get_cached_bounding_rectangle()
        .ok()
        .map(|r| (r.get_left(), r.get_top(), r.get_right(), r.get_bottom()))
        .filter(|r| r.2 > r.0 && r.3 > r.1);

    {
        let mut p = partial.lock().unwrap_or_else(|e| e.into_inner());
        p.app = app;
        p.window_title = Some(title).filter(|t| !t.is_empty());
        p.started = true;
    }

    let mut queue: VecDeque<(UIElement, usize)> = VecDeque::new();
    queue.push_back((window, 0));
    let mut visited = 0usize;
    let expired = || cancel.load(Ordering::Relaxed) || Instant::now() >= deadline;

    while let Some((node, depth)) = queue.pop_front() {
        if expired() || visited >= MAX_NODES {
            break;
        }
        let Ok(updated) = node.build_updated_cache(&children_req) else { continue };
        let children = updated.get_cached_children().unwrap_or_default();
        let mut found = Vec::new();
        for child in children.into_iter().take(MAX_CHILDREN) {
            visited += 1;
            if cached_bool(&child, UIProperty::IsOffscreen) {
                continue;
            }
            let Ok(ct) = child.get_cached_control_type() else { continue };
            let (role, kind) = classify(ct, child.is_cached_password().unwrap_or(false));
            if kind == Kind::Skip {
                continue;
            }
            if kind != Kind::Leaf && depth + 1 < MAX_DEPTH {
                queue.push_back((child.clone(), depth + 1));
            }
            if kind == Kind::Container {
                continue;
            }
            let name = normalize_name(&child.get_cached_name().unwrap_or_default());
            if kind == Kind::Maybe
                && (name.is_empty()
                    || !(child.is_cached_keyboard_focusable().unwrap_or(false)
                        || cached_bool(&child, UIProperty::IsInvokePatternAvailable)))
            {
                continue;
            }
            let Ok(r) = child.get_cached_bounding_rectangle() else { continue };
            let (l, t, rt, b) = (r.get_left(), r.get_top(), r.get_right(), r.get_bottom());
            if rt <= l || b <= t {
                continue;
            }
            if let Some((wl, wt, wr, wb)) = win_rect {
                if rt <= wl || l >= wr || b <= wt || t >= wb {
                    continue;
                }
            }
            found.push(UiElement {
                role: role.to_string(),
                name,
                x: l,
                y: t,
                w: rt - l,
                h: b - t,
                enabled: child.is_cached_enabled().unwrap_or(true),
                focused: child.has_cached_keyboard_focus().unwrap_or(false),
            });
        }
        if !found.is_empty() {
            let mut p = partial.lock().unwrap_or_else(|e| e.into_inner());
            p.elements.extend(found);
        }
    }
    Ok(())
}
