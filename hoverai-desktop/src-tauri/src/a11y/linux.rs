// ─── Accessibility snapshot — Linux (AT-SPI over D-Bus) ──────────────────────
//
// Talks raw AT-SPI D-Bus methods with zbus (no libatspi / atspi crate):
//   1. session bus `org.a11y.Bus.GetAddress` (or `$AT_SPI_BUS_ADDRESS`) → the
//      accessibility bus, a separate D-Bus daemon.
//   2. Registry root `GetChildren` → one entry per application. Each app is
//      probed in parallel (name, pid, top-level windows + state + title) with
//      a short per-app timeout so one hung app can't eat the budget. The
//      focused window is picked from the compositor's answer when available
//      (Hyprland IPC: pid, then title), else by the AT-SPI ACTIVE state.
//   3. Breadth-first walk of that window with a bounded number of concurrent
//      calls, a node budget, a depth limit and a hard deadline. Subtrees that
//      aren't SHOWING are pruned. Whatever was collected when the deadline hits
//      is returned.
//
// Everything runs on a private current-thread tokio runtime created per call,
// so the caller just needs a plain blocking thread (`spawn_blocking`).
//
// Coordinates: on Wayland clients don't know their global position, so
// `GetExtents(SCREEN)` is meaningless; we ask for WINDOW coordinates and report
// `CoordSpace::WindowRelative` (the caller adds the window's origin, e.g. from
// the compositor). Note GTK's "window" origin is the toplevel surface, which
// for client-side decorated windows may include the shadow margin. On X11 we
// ask for SCREEN coordinates.
//
// Known limitations (not fixed here):
//   - Chromium/Electron/Brave only expose their tree when started with
//     `--force-renderer-accessibility` (or when they detect a screen reader);
//     otherwise only the frame is visible → zero elements.
//   - Terminals like Alacritty/kitty/foot, and most games/canvas apps, expose
//     nothing at all → "no active window" error.
//   - Qt apps need `QT_LINUX_ACCESSIBILITY_ALWAYS_ON=1` on some setups.
//   - Firefox exposes a11y only once an AT client has connected; first call
//     may be sparse.

use std::collections::VecDeque;
use std::time::{Duration, Instant};

use tokio::task::JoinSet;
use tokio::time::{timeout, timeout_at, Instant as TokioInstant};
use zbus::zvariant::{OwnedObjectPath, OwnedValue};
use zbus::Connection;

use super::{
    finalize, normalize_name, CoordSpace, Snapshot, UiElement, MAX_DEPTH, MAX_NODES,
};

const ACCESSIBLE: &str = "org.a11y.atspi.Accessible";
const COMPONENT: &str = "org.a11y.atspi.Component";
const ROOT_PATH: &str = "/org/a11y/atspi/accessible/root";
const REGISTRY: &str = "org.a11y.atspi.Registry";

/// Concurrent in-flight node fetches during the walk.
const CONCURRENCY: usize = 24;
/// Max children followed per node (huge lists/tables).
const MAX_CHILDREN: usize = 300;
/// Max children followed per node that MANAGES_DESCENDANTS (virtual lists).
const MAX_CHILDREN_MANAGED: usize = 100;
/// Per-application time limit while looking for the active window.
const APP_PROBE_TIMEOUT: Duration = Duration::from_millis(150);

// AtspiCoordType
const COORD_SCREEN: u32 = 0;
const COORD_WINDOW: u32 = 1;

// AtspiStateType bit numbers
const ST_ACTIVE: u32 = 1;
const ST_DEFUNCT: u32 = 6;
const ST_EDITABLE: u32 = 7;
const ST_ENABLED: u32 = 8;
const ST_FOCUSABLE: u32 = 11;
const ST_FOCUSED: u32 = 12;
const ST_SENSITIVE: u32 = 24;
const ST_SHOWING: u32 = 25;
const ST_VISIBLE: u32 = 30;
const ST_MANAGES_DESCENDANTS: u32 = 31;

type Ref = (String, OwnedObjectPath);

/// How a role is treated when collecting elements.
#[derive(Clone, Copy, PartialEq)]
enum Kind {
    /// Always a click/type target; its subtree is not walked.
    Leaf,
    /// Always a target, but may contain further targets (menus, list items…).
    Target,
    /// Only a target if named and focusable/actionable (labels, images, cells…).
    Maybe,
    /// Pure container / decoration: walked, never reported.
    Container,
    /// Not reported and not walked (scroll bars, tooltips, separators…).
    Skip,
}

/// AtspiRole number → (normalised role name, kind).
fn classify(role: u32, editable: bool) -> (&'static str, Kind) {
    use Kind::*;
    match role {
        7 => ("check box", Leaf),
        8 => ("check menu item", Leaf),
        11 => ("combo box", Target),
        12 => ("date field", Leaf),
        13 => ("icon", Maybe),
        15 => ("dial", Leaf),
        10 | 57 => ("column header", Target),
        26 => ("icon", Maybe),
        27 => ("image", Maybe),
        29 => ("label", Maybe),
        32 => ("list item", Target),
        33 => ("menu", Target),
        35 => ("menu item", Target),
        37 => ("tab", Target),
        40 => ("password field", Leaf),
        43 => ("button", Leaf),
        44 => ("radio button", Leaf),
        45 => ("radio menu item", Leaf),
        51 => ("slider", Leaf),
        52 => ("spin button", Leaf),
        56 => ("table cell", Maybe),
        61 if editable => ("text field", Leaf),
        61 => ("text", Maybe),
        62 => ("toggle button", Leaf),
        76 => ("combo box", Target),
        79 => ("text field", Leaf),
        88 => ("link", Leaf),
        90 => ("row", Maybe),
        91 => ("tree item", Target),
        114 => ("rating", Leaf),
        129 => ("menu button", Leaf),
        130 => ("switch", Leaf),
        20 | 39 | 85 | 116 | 67 | 99 | 109 | 110 | 73 | 83 => ("group", Maybe),
        42 | 48 | 50 | 64 | 74 | 103 => ("", Skip),
        _ => ("", Container),
    }
}

fn has(state: &[u32], bit: u32) -> bool {
    state
        .get((bit / 32) as usize)
        .is_some_and(|w| w & (1 << (bit % 32)) != 0)
}

fn is_wayland() -> bool {
    std::env::var_os("WAYLAND_DISPLAY").is_some_and(|v| !v.is_empty())
}

pub fn snapshot(timeout: Duration) -> Result<Snapshot, String> {
    let deadline = Instant::now() + timeout;
    let rt = tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()
        .map_err(|e| format!("a11y: tokio runtime: {e}"))?;
    let hint = hyprland_focus_hint();
    rt.block_on(async move {
        let deadline = TokioInstant::from_std(deadline);
        match timeout_at(deadline, connect()).await {
            Ok(Ok(conn)) => run(&conn, deadline, hint).await,
            Ok(Err(e)) => Err(e),
            Err(_) => Err("a11y: timed out connecting to the AT-SPI bus".into()),
        }
    })
}

async fn connect() -> Result<Connection, String> {
    let address = match std::env::var("AT_SPI_BUS_ADDRESS") {
        Ok(a) if !a.is_empty() => a,
        _ => {
            let session = Connection::session()
                .await
                .map_err(|e| format!("a11y: session bus: {e}"))?;
            let reply = session
                .call_method(
                    Some("org.a11y.Bus"),
                    "/org/a11y/bus",
                    Some("org.a11y.Bus"),
                    "GetAddress",
                    &(),
                )
                .await
                .map_err(|e| format!("a11y: AT-SPI bus not available (org.a11y.Bus): {e}"))?;
            reply
                .body()
                .deserialize::<String>()
                .map_err(|e| format!("a11y: bad GetAddress reply: {e}"))?
        }
    };
    zbus::connection::Builder::address(address.as_str())
        .map_err(|e| format!("a11y: bad bus address {address:?}: {e}"))?
        .build()
        .await
        .map_err(|e| format!("a11y: connect to AT-SPI bus: {e}"))
}

// ─── Raw AT-SPI calls ────────────────────────────────────────────────────────

async fn call<B, R>(
    conn: &Connection,
    dest: &str,
    path: &str,
    iface: &str,
    method: &str,
    body: &B,
) -> Result<R, String>
where
    B: serde::Serialize + zbus::zvariant::DynamicType,
    R: serde::de::DeserializeOwned + zbus::zvariant::Type,
{
    let msg = conn
        .call_method(Some(dest), path, Some(iface), method, body)
        .await
        .map_err(|e| e.to_string())?;
    msg.body().deserialize::<R>().map_err(|e| e.to_string())
}

async fn get_children(conn: &Connection, r: &Ref) -> Vec<Ref> {
    call::<_, Vec<Ref>>(conn, &r.0, r.1.as_str(), ACCESSIBLE, "GetChildren", &())
        .await
        .unwrap_or_default()
}

async fn get_state(conn: &Connection, r: &Ref) -> Vec<u32> {
    call::<_, Vec<u32>>(conn, &r.0, r.1.as_str(), ACCESSIBLE, "GetState", &())
        .await
        .unwrap_or_default()
}

async fn get_role(conn: &Connection, r: &Ref) -> Option<u32> {
    call::<_, u32>(conn, &r.0, r.1.as_str(), ACCESSIBLE, "GetRole", &())
        .await
        .ok()
}

async fn get_name(conn: &Connection, r: &Ref) -> String {
    let v = call::<_, OwnedValue>(
        conn,
        &r.0,
        r.1.as_str(),
        "org.freedesktop.DBus.Properties",
        "Get",
        &(ACCESSIBLE, "Name"),
    )
    .await;
    v.ok()
        .and_then(|v| String::try_from(v).ok())
        .unwrap_or_default()
}

async fn get_extents(conn: &Connection, r: &Ref, coord: u32) -> Option<(i32, i32, i32, i32)> {
    call::<_, (i32, i32, i32, i32)>(conn, &r.0, r.1.as_str(), COMPONENT, "GetExtents", &(coord,))
        .await
        .ok()
}

async fn has_action(conn: &Connection, r: &Ref) -> bool {
    call::<_, Vec<String>>(conn, &r.0, r.1.as_str(), ACCESSIBLE, "GetInterfaces", &())
        .await
        .map(|ifs| ifs.iter().any(|i| i == "org.a11y.atspi.Action"))
        .unwrap_or(false)
}

// ─── Active window search ────────────────────────────────────────────────────

struct Window {
    r: Ref,
    app: String,
    title: String,
}

/// What the compositor says is focused. AT-SPI's ACTIVE state is unreliable on
/// some Wayland compositors (on Hyprland a GTK4 window can be focused without
/// ACTIVE while another keeps a stale ACTIVE), so when the compositor can tell
/// us, that wins. Only Hyprland is supported for now; elsewhere we fall back
/// to the ACTIVE state (reliable on X11, GNOME and KDE).
struct FocusHint {
    pid: u32,
    title: String,
    class: String,
}

/// Asks Hyprland's IPC socket for the active window (`j/activewindow`).
fn hyprland_focus_hint() -> Option<FocusHint> {
    use std::io::{Read, Write};
    let sig = std::env::var("HYPRLAND_INSTANCE_SIGNATURE").ok()?;
    let runtime = std::env::var("XDG_RUNTIME_DIR").ok()?;
    let path = std::path::Path::new(&runtime)
        .join("hypr")
        .join(sig)
        .join(".socket.sock");
    let mut sock = std::os::unix::net::UnixStream::connect(path).ok()?;
    let t = Some(Duration::from_millis(100));
    sock.set_read_timeout(t).ok()?;
    sock.set_write_timeout(t).ok()?;
    sock.write_all(b"j/activewindow").ok()?;
    let mut buf = Vec::new();
    sock.take(256 * 1024).read_to_end(&mut buf).ok()?;
    let v: serde_json::Value = serde_json::from_slice(&buf).ok()?;
    let pid = u32::try_from(v.get("pid")?.as_i64()?).ok()?;
    let s = |k: &str| v.get(k).and_then(|x| x.as_str()).unwrap_or("").to_string();
    Some(FocusHint {
        pid,
        title: s("title"),
        class: s("class"),
    })
}

/// HoverAI's own process/app/class name (Tauri binary, product name, app id).
fn is_hoverai_app(s: &str) -> bool {
    matches!(
        s.to_ascii_lowercase().as_str(),
        "hoverai" | "hoverai-desktop" | "hoverai_desktop" | "com.hoverai.desktop"
    )
}

/// Titles of HoverAI's own windows (see tauri.conf.json). Case-sensitive so a
/// project folder named "HoverAi" open in an editor isn't mistaken for us.
fn is_hoverai_title(s: &str) -> bool {
    matches!(s, "HoverAI" | "HoverAI Overlay" | "HoverAI Settings")
}

struct AppWindow {
    r: Ref,
    state: Vec<u32>,
    title: String,
}

struct AppInfo {
    name: String,
    pid: Option<u32>,
    windows: Vec<AppWindow>,
}

/// Name, process id and top-level windows (with state and title) of one app.
async fn probe_app(conn: Connection, app: Ref) -> AppInfo {
    let pid_arg = (app.0.as_str(),);
    let pid_fut = call::<_, u32>(
        &conn,
        "org.freedesktop.DBus",
        "/org/freedesktop/DBus",
        "org.freedesktop.DBus",
        "GetConnectionUnixProcessID",
        &pid_arg,
    );
    let (name, pid, children) =
        tokio::join!(get_name(&conn, &app), pid_fut, get_children(&conn, &app));
    let mut windows = Vec::new();
    for w in children.into_iter().take(32) {
        let (state, title) = tokio::join!(get_state(&conn, &w), get_name(&conn, &w));
        if !has(&state, ST_DEFUNCT) {
            windows.push(AppWindow { r: w, state, title });
        }
    }
    AppInfo {
        name,
        pid: pid.ok(),
        windows,
    }
}

fn choose_window(apps: Vec<AppInfo>, hint: Option<FocusHint>) -> Result<Window, String> {
    let pick = |app: &AppInfo, w: &AppWindow| Window {
        r: w.r.clone(),
        app: app.name.clone(),
        title: w.title.clone(),
    };
    if let Some(h) = hint {
        if h.pid == std::process::id() || is_hoverai_app(&h.class) || is_hoverai_title(&h.title) {
            return Err("a11y: the focused window is HoverAI itself".into());
        }
        // Same process: prefer the window with the focused title, then ACTIVE, then showing.
        if let Some(app) = apps.iter().find(|a| a.pid == Some(h.pid) && !a.windows.is_empty()) {
            let w = app
                .windows
                .iter()
                .find(|w| w.title == h.title)
                .or_else(|| app.windows.iter().find(|w| has(&w.state, ST_ACTIVE)))
                .or_else(|| app.windows.iter().find(|w| has(&w.state, ST_SHOWING)))
                .or_else(|| app.windows.first());
            if let Some(w) = w {
                let mut win = pick(app, w);
                if win.app.is_empty() {
                    win.app = h.class;
                }
                return Ok(win);
            }
        }
        // Different pid namespace (Flatpak): match by title.
        if !h.title.is_empty() {
            for app in &apps {
                if let Some(w) = app.windows.iter().find(|w| w.title == h.title) {
                    return Ok(pick(app, w));
                }
            }
        }
        return Err(format!(
            "a11y: focused window ({}) does not expose AT-SPI",
            if h.class.is_empty() { &h.title } else { &h.class }
        ));
    }
    for app in &apps {
        if is_hoverai_app(&app.name) || app.pid == Some(std::process::id()) {
            continue;
        }
        if let Some(w) = app
            .windows
            .iter()
            .find(|w| has(&w.state, ST_ACTIVE) && !is_hoverai_title(&w.title))
        {
            return Ok(pick(app, w));
        }
    }
    Err("a11y: no active window exposes AT-SPI".into())
}

async fn find_active_window(
    conn: &Connection,
    deadline: TokioInstant,
    hint: Option<FocusHint>,
) -> Result<Window, String> {
    let root: Ref = (
        REGISTRY.to_string(),
        OwnedObjectPath::try_from(ROOT_PATH).map_err(|e| e.to_string())?,
    );
    let apps = timeout_at(deadline, get_children(conn, &root))
        .await
        .map_err(|_| "a11y: timed out listing applications".to_string())?;
    if apps.is_empty() {
        return Err("a11y: no applications registered on the AT-SPI bus".into());
    }
    let mut set = JoinSet::new();
    for app in apps {
        let conn = conn.clone();
        // A hung app must not eat the whole budget: skip it after a short wait.
        set.spawn(async move { timeout(APP_PROBE_TIMEOUT, probe_app(conn, app)).await.ok() });
    }
    let mut infos = Vec::new();
    loop {
        match timeout_at(deadline, set.join_next()).await {
            Ok(Some(Ok(Some(info)))) => infos.push(info),
            Ok(Some(_)) => {}
            Ok(None) => break,
            Err(_) => return Err("a11y: timed out looking for the active window".into()),
        }
    }
    choose_window(infos, hint)
}

// ─── Walk ────────────────────────────────────────────────────────────────────

struct NodeOut {
    children: Vec<Ref>,
    element: Option<UiElement>,
    /// This node is (or is inside) a reported target; its descendants'
    /// labels/images are part of it and not reported separately.
    in_target: bool,
}

type Rect = (i32, i32, i32, i32);

fn intersects(a: Rect, b: Rect) -> bool {
    a.0 < b.0 + b.2 && b.0 < a.0 + a.2 && a.1 < b.1 + b.3 && b.1 < a.1 + a.3
}

async fn visit(
    conn: Connection,
    r: Ref,
    walk_children: bool,
    coord: u32,
    window: Option<Rect>,
    inside: bool,
) -> NodeOut {
    // Role + state first: most nodes are containers, which then only need
    // their children (3 round trips instead of 5 — GTK answers serially).
    let (role, state) = tokio::join!(get_role(&conn, &r), get_state(&conn, &r));
    let none = NodeOut {
        children: Vec::new(),
        element: None,
        in_target: inside,
    };
    let Some(role) = role else { return none };
    if has(&state, ST_DEFUNCT) || !has(&state, ST_SHOWING) {
        return none;
    }
    let (role_name, kind) = classify(role, has(&state, ST_EDITABLE));
    let descend = walk_children && !matches!(kind, Kind::Leaf | Kind::Skip);
    let candidate = has(&state, ST_VISIBLE) && matches!(kind, Kind::Leaf | Kind::Target | Kind::Maybe);

    let children_fut = async {
        if !descend {
            return Vec::new();
        }
        let mut c = get_children(&conn, &r).await;
        let cap = if has(&state, ST_MANAGES_DESCENDANTS) {
            MAX_CHILDREN_MANAGED
        } else {
            MAX_CHILDREN
        };
        c.truncate(cap);
        c
    };
    let element_fut = async {
        if !candidate {
            return None;
        }
        let (name, rect) = if kind == Kind::Maybe {
            // Only named, focusable-or-actionable labels/images/cells count,
            // and not when they're just the content of a button/list item.
            if inside {
                return None;
            }
            let name = normalize_name(&get_name(&conn, &r).await);
            if !name.chars().any(char::is_alphanumeric) {
                return None;
            }
            if !has(&state, ST_FOCUSABLE) && !has_action(&conn, &r).await {
                return None;
            }
            (name, get_extents(&conn, &r, coord).await?)
        } else {
            let (name, rect) = tokio::join!(get_name(&conn, &r), get_extents(&conn, &r, coord));
            (normalize_name(&name), rect?)
        };
        let (x, y, w, h) = rect;
        if w <= 0 || h <= 0 {
            return None;
        }
        if let Some(win) = window {
            if !intersects((x, y, w, h), win) {
                return None;
            }
        }
        Some(UiElement {
            role: role_name.to_string(),
            name,
            x,
            y,
            w,
            h,
            enabled: has(&state, ST_ENABLED) || has(&state, ST_SENSITIVE),
            focused: has(&state, ST_FOCUSED),
        })
    };
    let (children, element) = tokio::join!(children_fut, element_fut);
    NodeOut {
        children,
        in_target: inside || (kind == Kind::Target && element.is_some()),
        element,
    }
}

async fn run(
    conn: &Connection,
    deadline: TokioInstant,
    hint: Option<FocusHint>,
) -> Result<Snapshot, String> {
    let win = find_active_window(conn, deadline, hint).await?;
    let wayland = is_wayland();
    let coord = if wayland { COORD_WINDOW } else { COORD_SCREEN };

    // Window bounds in the same coordinate space, to drop scrolled-out items.
    let window_rect = timeout_at(deadline, get_extents(conn, &win.r, coord))
        .await
        .ok()
        .flatten()
        .filter(|r| r.2 > 0 && r.3 > 0);

    let mut elements = Vec::new();
    let mut queue: VecDeque<(Ref, usize, bool)> = get_children_at(conn, &win.r, deadline)
        .await
        .into_iter()
        .map(|c| (c, 1, false))
        .collect();
    let mut visited = 0usize;
    let mut set: JoinSet<(NodeOut, usize)> = JoinSet::new();

    loop {
        while set.len() < CONCURRENCY && visited < MAX_NODES {
            let Some((r, depth, inside)) = queue.pop_front() else { break };
            visited += 1;
            let conn = conn.clone();
            let walk = depth < MAX_DEPTH;
            set.spawn(async move { (visit(conn, r, walk, coord, window_rect, inside).await, depth) });
        }
        if set.is_empty() {
            break;
        }
        match timeout_at(deadline, set.join_next()).await {
            Ok(Some(Ok((out, depth)))) => {
                if let Some(e) = out.element {
                    elements.push(e);
                }
                let inside = out.in_target;
                queue.extend(out.children.into_iter().map(|c| (c, depth + 1, inside)));
            }
            Ok(Some(Err(_))) => {}
            Ok(None) => break,
            Err(_) => {
                log::debug!("a11y: deadline hit after {visited} nodes");
                break;
            }
        }
    }
    set.abort_all();

    Ok(finalize(Snapshot {
        app: Some(win.app).filter(|s| !s.is_empty()),
        window_title: Some(normalize_name(&win.title)).filter(|s| !s.is_empty()),
        elements,
        coords: Some(if wayland {
            CoordSpace::WindowRelative
        } else {
            CoordSpace::Screen
        }),
    }))
}

async fn get_children_at(conn: &Connection, r: &Ref, deadline: TokioInstant) -> Vec<Ref> {
    timeout_at(deadline, get_children(conn, r))
        .await
        .unwrap_or_default()
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Prints the snapshot of whatever window is focused (e.g. this terminal,
    /// or another app focused with `hyprctl dispatch focuswindow`).
    /// `cargo test --lib a11y -- --ignored --nocapture`
    #[test]
    #[ignore]
    fn a11y_snapshot_focused_window() {
        let ms: u64 = std::env::var("A11Y_TIMEOUT_MS")
            .ok()
            .and_then(|v| v.parse().ok())
            .unwrap_or(700);
        let t = Instant::now();
        let r = crate::a11y::snapshot(Duration::from_millis(ms));
        let elapsed = t.elapsed();
        match r {
            Ok(s) => {
                println!(
                    "app={:?} title={:?} coords={:?} elements={} elapsed={}ms",
                    s.app,
                    s.window_title,
                    s.coords,
                    s.elements.len(),
                    elapsed.as_millis()
                );
                for e in s.elements.iter().take(40) {
                    println!(
                        "  {:<14} {:<40} @{},{} {}x{}{}{}",
                        e.role,
                        format!("{:?}", e.name),
                        e.x,
                        e.y,
                        e.w,
                        e.h,
                        if e.enabled { "" } else { " [disabled]" },
                        if e.focused { " [focused]" } else { "" }
                    );
                }
            }
            Err(e) => println!("error after {}ms: {e}", elapsed.as_millis()),
        }
    }
}
