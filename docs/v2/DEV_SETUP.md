# Dev setup for this PC (Omarchy · Hyprland · Wayland)

Checked on 2026-10-31. Machine: 4 CPU threads, 7.7 GB RAM, 212 GB free, Intel Haswell iGPU + NVIDIA GF117 (old Fermi, use the Intel GPU), KVM available.

## Already installed ✅

| Need | Found |
|---|---|
| Rust toolchain | rustc/cargo 1.99.1, rustup 1.29 |
| Node + pnpm | Node 26.12, pnpm 12.15 |
| Python | **3.12.15** (uv-managed; used by `backend/`), 3.14 system (owned by pacman, don't touch), uv 0.13.5 |
| Tauri Linux libs | webkit2gtk-4.1 2.54, gtk3, ayatana-appindicator, librsvg, alsa, openssl, pipewire |
| Keyring | gnome-keyring 51 + libsecret (running) |
| Portals | xdg-desktop-portal 1.22, **xdg-desktop-portal-hyprland 1.4.1**, -gtk (running) |
| Accessibility | at-spi2-core 2.62 (needed for Phase 3 on Linux) |
| Media | ffmpeg 9.0, grim/slurp (Wayland screenshots), wtype |
| DB client / containers | psql 18.6, Docker 29.9 |

`hoverai-desktop`: `pnpm typecheck` and `cargo check` already pass here.

## To do

### 1. Docker (for local Postgres)
Docker is installed but the service is **inactive**, and your user is **not in the `docker` group**.
```bash
sudo systemctl enable --now docker
sudo usermod -aG docker $USER      # then log out/in (or reboot)
docker run --rm hello-world         # verify
```

### 2. Postgres 18 in a container
```bash
docker run -d --name hoverai-pg --restart unless-stopped \
  -e POSTGRES_USER=hoverai -e POSTGRES_PASSWORD=hoverai -e POSTGRES_DB=hoverai_dev \
  -p 127.0.0.1:5432:5432 -v hoverai-pg:/var/lib/postgresql postgres:18
psql postgresql://hoverai:hoverai@localhost:5432/hoverai_dev -c 'select version();'
```
(Postgres 18 images expect the volume at `/var/lib/postgresql`, not `/var/lib/postgresql/data`.)

### 3. Backend on Python 3.12
`backend/.python-version` is `3.12` and `.venv` has been rebuilt on 3.12.15.
In `backend/.env`, point `DATABASE_URL` at the local container (the `.env.example` placeholder host `host` fails DNS):
```bash
DATABASE_URL=postgresql://hoverai:hoverai@localhost:5432/hoverai_dev
```
Then:
```bash
cd backend
uv sync
uv run hover-dev              # http://localhost:8000/docs; tables are created on startup
uv run pytest
```
- Optional global tools: `uv tool install ruff`.

### 4. Desktop app
```bash
cd hoverai-desktop
pnpm install                                   # already done once
echo 'VITE_API_BASE=http://localhost:8000/api/v1' > .env   # local backend
pnpm tauri dev
```

Rust builds on 4 threads / 7.7 GB RAM:
- Crates are downloaded **once** into `~/.cargo/registry` (~84 MB compressed, shared by all projects). `target/` is only compiled output; `cargo clean` costs a rebuild, not a re-download. To guarantee no network use, run with `CARGO_NET_OFFLINE=true`.
- `Cargo.toml` dev profile already strips debug info from dependencies, which makes `target/` several GB smaller.
- Keep **debug** builds for daily work. Never run the LTO `release` profile locally (it is ~45 min even on faster machines).
- If the machine swaps during builds, set `export CARGO_BUILD_JOBS=3`.
- Faster linking: `sudo pacman -S mold`, then in `hoverai-desktop/src-tauri/.cargo/config.toml`:
  ```toml
  [target.x86_64-unknown-linux-gnu]
  linker = "clang"
  rustflags = ["-C", "link-arg=-fuse-ld=mold"]
  ```
  (needs `clang`: `sudo pacman -S clang`). Optional, but it cuts incremental rebuilds noticeably.
- `src-tauri/target` is ~3 GB with the lean dev profile. Run `cargo clean` occasionally.

The app sets `WEBKIT_DISABLE_DMABUF_RENDERER=1` itself on Linux; without it, cards showed coloured horizontal stripes on this Intel GPU. If windows still render blank, flicker or crash:
```bash
WEBKIT_DISABLE_COMPOSITING_MODE=1 pnpm tauri dev
```

### 5. Hyprland-specific bits (done in Phase 1–2, listed so you know what's coming)
- **Shortcut on Wayland:** the current global-shortcut plugin is X11-only and will not fire here. Until the portal backend lands, we'll add a Hyprland keybind that runs `hoverai --trigger`. Your Hyprland config is Lua (`~/.config/hypr/bindings.lua`), so we'll write that line together then.
- **Overlay window rules:** float, pin, no focus-steal and no animation for the `HoverAI Overlay` window, otherwise Hyprland tiles it.
- **Screenshots:** go through the xdg portal (xdph) or wlr-screencopy (`grim` already works). The first capture may show a permission prompt.
- Check the portals any time with: `systemctl --user status xdg-desktop-portal-hyprland`.

### 6. Windows testing (primary target)
Real hardware is best. If none is available, use a KVM VM (`/dev/kvm` is present):
```bash
sudo pacman -S qemu-full virt-manager dnsmasq     # or: yay -S quickemu && quickget windows 11
sudo usermod -aG libvirt $USER && sudo systemctl enable --now libvirtd
```
- Give the VM **4 GB RAM / 2 vCPU** max, and run it only while testing. With 7.7 GB total, don't run the VM *and* a Rust build at the same time.
- **Don't compile inside the VM.** Get the Windows installer from CI (Phase 4 makes every PR produce an artifact) and just install and test it.
- Optional later: cross-compile the NSIS installer locally with `cargo-xwin` (`tauri build --runner cargo-xwin --target x86_64-pc-windows-msvc`). Slow on this machine; CI is preferred.

### 7. macOS
Nothing to install here. macOS builds come from CI (`universal-apple-darwin` on GitHub's Apple Silicon runner) and need a person with a Mac to test.

### 8. Accounts and secrets
- Groq API key, Intron (Sahara) API key → `backend/.env`.
- GitHub repo secret `VITE_API_BASE` (already used by `release.yml`).
- Render: switching to a Docker deploy to pin Python 3.12 (see the execution plan, Phase 1).

## Quick health check (after steps 1–4)
```bash
docker ps --filter name=hoverai-pg                   # Postgres up
curl -s localhost:8000/api/v1/health                 # {"status":"ok",...,"db":"ok"}
cd hoverai-desktop && pnpm typecheck && (cd src-tauri && cargo check)
```
