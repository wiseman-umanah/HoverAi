// Prevents additional console window on Windows in release, DO NOT REMOVE!!
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    // WebKitGTK's DMA-BUF renderer corrupts composited layers (coloured
    // horizontal stripes over animated/glass cards) on many Intel/NVIDIA
    // Linux setups. Fall back to the shared-memory renderer unless the user
    // chose explicitly. Must run before any thread or webview starts.
    #[cfg(target_os = "linux")]
    if std::env::var_os("WEBKIT_DISABLE_DMABUF_RENDERER").is_none() {
        std::env::set_var("WEBKIT_DISABLE_DMABUF_RENDERER", "1");
    }

    hoverai_desktop_lib::run()
}
