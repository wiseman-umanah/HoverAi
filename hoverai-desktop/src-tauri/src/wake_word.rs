// ─── Wake-word detection ──────────────────────────────────────────────────────
//
// Uses the livekit-wakeword Rust crate which handles the full pipeline
// internally — melspectrogram, speech embedding, classifier.
//
// We only ship hey_hover.onnx in resources/.
// melspectrogram.onnx and embedding_model.onnx are bundled inside the crate.
//
// Flow:
//   cpal (native SR, any format, any channels)
//     → downmix to mono f32 → ring buffer
//     → every 500 ms drain native samples, linear-interpolate to 16 kHz
//     → maintain rolling 2-second window (32 000 samples at 16 kHz)
//     → WakeWordModel::predict() on the full window
//     → score > threshold → trigger_from_wake_word() + 1.5 s cooldown
//
// Stopping: drop the SyncSender returned by start(). The detector thread
// checks the channel every loop iteration and exits cleanly, which drops
// the cpal stream and releases the microphone.

use std::{
    sync::{Arc, Mutex},
    time::{Duration, Instant},
};

use cpal::{SampleFormat, traits::{DeviceTrait, HostTrait, StreamTrait}};
use livekit_wakeword::wakeword::WakeWordModel;
use tauri::{AppHandle, Emitter};

// ── Constants ─────────────────────────────────────────────────────────────────

const COOLDOWN: Duration = Duration::from_millis(1500);
/// The model expects 16 kHz mono input.
const MODEL_SR: u32 = 16_000;
/// 2-second window the model scores (32 000 samples at 16 kHz).
const WINDOW_SAMPLES: usize = 32_000;
/// New 16 kHz samples accumulated per inference call (500 ms).
const HOP_SAMPLES_16K: usize = 8_000;

// ── Helpers ───────────────────────────────────────────────────────────────────

/// Map mic_sensitivity 1–10 to a detection threshold.
/// sensitivity=1  → threshold=0.70 (hardest to trigger)
/// sensitivity=5  → threshold=0.41 (optimal from eval)
/// sensitivity=10 → threshold=0.20 (easiest to trigger)
fn sensitivity_to_threshold(sensitivity: u32) -> f32 {
    let s = sensitivity.clamp(1, 10) as f32;
    // Linear: 0.70 at s=1, 0.41 at s=5, 0.20 at s=10
    0.70 - (s - 1.0) * (0.50 / 9.0)
}

fn append_mono_f32(ring: &mut Vec<f32>, data: &[f32], channels: usize) {
    for frame in data.chunks(channels) {
        ring.push(frame.iter().sum::<f32>() / channels as f32);
    }
}

fn append_mono_i16(ring: &mut Vec<f32>, data: &[i16], channels: usize) {
    for frame in data.chunks(channels) {
        let sum: f32 = frame.iter().map(|&s| s as f32 / 32768.0).sum();
        ring.push(sum / channels as f32);
    }
}

fn append_mono_u16(ring: &mut Vec<f32>, data: &[u16], channels: usize) {
    for frame in data.chunks(channels) {
        let sum: f32 = frame.iter().map(|&s| (s as f32 - 32768.0) / 32768.0).sum();
        ring.push(sum / channels as f32);
    }
}

// ── Stop signal ───────────────────────────────────────────────────────────────

pub struct StopSignal;

// ── Public API ────────────────────────────────────────────────────────────────

pub fn start(app: AppHandle) -> std::sync::mpsc::SyncSender<StopSignal> {
    let (tx, rx) = std::sync::mpsc::sync_channel::<StopSignal>(1);
    std::thread::spawn(move || {
        if let Err(e) = run_detector(app.clone(), rx) {
            eprintln!("[wake-word] detector error: {e}");
            // Emit to all windows so the frontend can show a toast
            let _ = app.emit("wake-word-error", e);
        }
        // Stream is dropped here → mic indicator extinguishes on Windows
        println!("[wake-word] microphone released");
    });
    tx
}

// ── Detector ─────────────────────────────────────────────────────────────────

fn run_detector(
    app: AppHandle,
    stop_rx: std::sync::mpsc::Receiver<StopSignal>,
) -> Result<(), String> {
    use tauri::Manager;

    // ── Resolve model path ──
    let model_path = app
        .path()
        .resolve("resources/hey_hover.onnx", tauri::path::BaseDirectory::Resource)
        .map_err(|e| format!("resolve model path: {e}"))?;

    // ── Read threshold from current settings ──
    let threshold = {
        let s = crate::settings::load(&app);
        let t = sensitivity_to_threshold(s.mic_sensitivity);
        println!("[wake-word] mic_sensitivity={} → threshold={t:.3}", s.mic_sensitivity);
        t
    };

    // ── Open microphone ──
    let host = cpal::default_host();
    let device = host.default_input_device().ok_or("no input device")?;
    let supported = device
        .default_input_config()
        .map_err(|e| format!("default input config: {e}"))?;

    let in_sr = supported.sample_rate().0 as usize;
    let in_channels = supported.channels() as usize;
    let sample_fmt = supported.sample_format();
    println!("[wake-word] mic: {in_sr} Hz, {in_channels} ch, fmt={sample_fmt:?}");

    let stream_cfg = cpal::StreamConfig {
        channels: supported.channels(),
        sample_rate: supported.sample_rate(),
        buffer_size: cpal::BufferSize::Default,
    };

    // ── Load WakeWordModel at 16 kHz ──
    // We downsample ourselves so the crate's internal resampler is a no-op.
    let model_path_str = model_path
        .to_str()
        .ok_or("model path is not valid UTF-8")?;

    let model = Arc::new(Mutex::new(
        WakeWordModel::new(&[model_path_str], MODEL_SR)
            .map_err(|e| format!("load WakeWordModel: {e}"))?,
    ));
    println!("[wake-word] model loaded (window={WINDOW_SAMPLES}, hop={HOP_SAMPLES_16K})");

    // Native samples per 500 ms hop
    let native_hop = HOP_SAMPLES_16K * in_sr / MODEL_SR as usize;

    // ── Ring buffer: native-rate f32 mono ──
    let ring: Arc<Mutex<Vec<f32>>> = Arc::new(Mutex::new(Vec::with_capacity(in_sr * 4)));

    // ── cpal stream — handle all common sample formats ──
    let stream = {
        let err_fn = |e| eprintln!("[wake-word] cpal error: {e}");
        match sample_fmt {
            SampleFormat::F32 => {
                let ring_w = ring.clone();
                device.build_input_stream(
                    &stream_cfg,
                    move |data: &[f32], _| {
                        append_mono_f32(&mut ring_w.lock().unwrap(), data, in_channels);
                    },
                    err_fn, None,
                )
            }
            SampleFormat::I16 => {
                let ring_w = ring.clone();
                device.build_input_stream(
                    &stream_cfg,
                    move |data: &[i16], _| {
                        append_mono_i16(&mut ring_w.lock().unwrap(), data, in_channels);
                    },
                    err_fn, None,
                )
            }
            SampleFormat::U16 => {
                let ring_w = ring.clone();
                device.build_input_stream(
                    &stream_cfg,
                    move |data: &[u16], _| {
                        append_mono_u16(&mut ring_w.lock().unwrap(), data, in_channels);
                    },
                    err_fn, None,
                )
            }
            fmt => return Err(format!("unsupported mic sample format: {fmt:?}")),
        }
        .map_err(|e| format!("build stream: {e}"))?
    };
    stream.play().map_err(|e| format!("stream play: {e}"))?;

    // ── Rolling 2-second window at 16 kHz ──
    // Starts empty — wait for real audio before first inference to avoid
    // false-positive on cold-start silence.
    let mut window_16k: Vec<f32> = Vec::with_capacity(WINDOW_SAMPLES);
    let mut hops_filled: usize = 0;
    let hops_to_fill = WINDOW_SAMPLES / HOP_SAMPLES_16K; // 4

    let mut last_trigger = Instant::now()
        .checked_sub(COOLDOWN)
        .unwrap_or_else(Instant::now);

    // ── Inference loop ──
    loop {
        // Stop check (non-blocking)
        match stop_rx.try_recv() {
            Ok(_) | Err(std::sync::mpsc::TryRecvError::Disconnected) => break,
            Err(std::sync::mpsc::TryRecvError::Empty) => {}
        }

        // Drain one hop of native-rate samples
        let native_samples: Vec<f32> = {
            let mut r = ring.lock().unwrap();
            if r.len() < native_hop {
                drop(r);
                std::thread::sleep(Duration::from_millis(5));
                continue;
            }
            r.drain(..native_hop).collect()
        };

        // Linear interpolation: native_hop → HOP_SAMPLES_16K at 16 kHz
        let hop_16k: Vec<f32> = (0..HOP_SAMPLES_16K)
            .map(|i| {
                let pos = i as f64 * (native_hop - 1) as f64 / (HOP_SAMPLES_16K - 1) as f64;
                let lo = pos.floor() as usize;
                let hi = (lo + 1).min(native_hop - 1);
                let frac = (pos - lo as f64) as f32;
                native_samples[lo] * (1.0 - frac) + native_samples[hi] * frac
            })
            .collect();

        // Fill or slide the window
        if window_16k.len() < WINDOW_SAMPLES {
            window_16k.extend_from_slice(&hop_16k);
            hops_filled += 1;
            if hops_filled < hops_to_fill {
                continue; // still warming up
            }
            // Window just became full — fall through to score
        } else {
            window_16k.drain(..HOP_SAMPLES_16K);
            window_16k.extend_from_slice(&hop_16k);
        }

        // Convert to i16 for predict()
        let window_i16: Vec<i16> = window_16k
            .iter()
            .map(|&s| (s * 32767.0).clamp(-32768.0, 32767.0) as i16)
            .collect();

        // Score
        let mut mdl = model.lock().unwrap();
        match mdl.predict(&window_i16) {
            Ok(preds) => {
                if let Some(&score) = preds.get("hey_hover") {
                    if score > threshold {
                        let now = Instant::now();
                        if now.duration_since(last_trigger) >= COOLDOWN {
                            last_trigger = now;
                            println!("[wake-word] *** DETECTED *** score={score:.3}");
                            let app_clone = app.clone();
                            tauri::async_runtime::spawn(async move {
                                crate::trigger_from_wake_word(app_clone).await;
                            });
                        }
                    }
                }
            }
            Err(e) => eprintln!("[wake-word] predict error: {e}"),
        }
    }

    // Explicitly drop the stream here so the OS mic indicator goes off
    // as soon as the thread exits, not when it's eventually GC'd.
    drop(stream);
    println!("[wake-word] detector stopped");
    Ok(())
}
