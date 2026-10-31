// ─── Accessibility snapshot — macOS (stub) ───────────────────────────────────
// AX (AXUIElement) support comes later; callers fall back to vision only.

use std::time::Duration;

use super::Snapshot;

pub fn snapshot(_timeout: Duration) -> Result<Snapshot, String> {
    Err("accessibility snapshot not implemented on macOS yet".into())
}
