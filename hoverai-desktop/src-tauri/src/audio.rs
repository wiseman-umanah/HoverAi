// ─── Microphone capture ───────────────────────────────────────────────────────
//
// Records the default input device natively with cpal instead of the
// webview's getUserMedia. This works the same on every platform — WebKitGTK
// (Linux) denies getUserMedia because wry installs no permission handler —
// and lets us hand the backend a ready-to-use 16 kHz mono PCM16 WAV.
//
// A recording runs on a dedicated thread that owns the cpal stream (streams
// are !Send on some platforms). The thread emits `audio-level` events
// (0.0–1.0, ~20/s) for the visualisers, and on stop returns the samples.
//
// Flow:
//   start()  → thread opens device, signals ready (or error) within 3 s
//   stop()   → thread drops the stream, returns mono f32 at native rate
//            → resample to 16 kHz → PCM16 WAV bytes
//   cancel() → same as stop, samples discarded

use std::{
    sync::{
        mpsc::{self, Receiver, SyncSender},
        Arc, Mutex,
    },
    thread::JoinHandle,
    time::{Duration, Instant},
};

use cpal::{
    traits::{DeviceTrait, HostTrait, StreamTrait},
    SampleFormat,
};
use tauri::{AppHandle, Emitter};

/// Sample rate the backend expects.
const TARGET_SR: u32 = 16_000;
/// Hard cap on a single recording so a forgotten overlay can't grow forever.
const MAX_SECONDS: usize = 120;
/// How often a level event is emitted.
const LEVEL_INTERVAL: Duration = Duration::from_millis(50);

struct Captured {
    samples: Vec<f32>,
    sample_rate: u32,
}

/// A running recording. Must be finished with `stop()` or `cancel()`;
/// dropping it also stops the thread (the stop channel disconnects).
pub struct Recording {
    stop_tx: SyncSender<()>,
    handle: JoinHandle<Captured>,
}

impl Recording {
    /// Stop and return a 16 kHz mono PCM16 WAV. Errors if nothing was captured.
    pub fn stop(self) -> Result<Vec<u8>, String> {
        let _ = self.stop_tx.send(());
        let cap = self
            .handle
            .join()
            .map_err(|_| "recording thread panicked".to_string())?;
        if cap.samples.is_empty() {
            return Err("no audio was captured".into());
        }
        let resampled = resample_linear(&cap.samples, cap.sample_rate, TARGET_SR);
        Ok(encode_wav_pcm16(&resampled, TARGET_SR))
    }

    /// Stop and discard the audio.
    pub fn cancel(self) {
        let _ = self.stop_tx.send(());
        let _ = self.handle.join();
    }
}

/// Start recording from the default input device.
///
/// `keep_samples = false` only emits levels (used by the onboarding mic test).
/// Returns an error string suitable for showing to the user if the device
/// can't be opened.
pub fn start(app: AppHandle, keep_samples: bool) -> Result<Recording, String> {
    let (stop_tx, stop_rx) = mpsc::sync_channel::<()>(1);
    let (ready_tx, ready_rx) = mpsc::sync_channel::<Result<(), String>>(1);

    let handle = std::thread::Builder::new()
        .name("hover-mic".into())
        .spawn(move || run(app, keep_samples, stop_rx, ready_tx))
        .map_err(|e| format!("could not start recording thread: {e}"))?;

    match ready_rx.recv_timeout(Duration::from_secs(3)) {
        Ok(Ok(())) => Ok(Recording { stop_tx, handle }),
        Ok(Err(e)) => {
            let _ = handle.join();
            Err(e)
        }
        Err(_) => {
            let _ = stop_tx.send(());
            Err("microphone did not respond".into())
        }
    }
}

/// Open the default input device briefly to check access (and trigger the
/// macOS permission prompt). Returns Ok if the stream could be started.
pub fn probe(app: AppHandle) -> Result<(), String> {
    let rec = start(app, false)?;
    std::thread::sleep(Duration::from_millis(150));
    rec.cancel();
    Ok(())
}

fn run(
    app: AppHandle,
    keep_samples: bool,
    stop_rx: Receiver<()>,
    ready_tx: SyncSender<Result<(), String>>,
) -> Captured {
    let empty = Captured { samples: Vec::new(), sample_rate: TARGET_SR };

    let opened = open_stream(keep_samples);
    let (stream, buffer, level, sample_rate) = match opened {
        Ok(v) => v,
        Err(e) => {
            log::warn!("[audio] {e}");
            let _ = ready_tx.send(Err(e));
            return empty;
        }
    };
    if let Err(e) = stream.play() {
        let msg = format!("could not start microphone: {e}");
        log::warn!("[audio] {msg}");
        let _ = ready_tx.send(Err(msg));
        return empty;
    }
    let _ = ready_tx.send(Ok(()));
    log::info!("[audio] recording started ({sample_rate} Hz, keep={keep_samples})");

    let started = Instant::now();
    loop {
        match stop_rx.recv_timeout(LEVEL_INTERVAL) {
            Ok(()) | Err(mpsc::RecvTimeoutError::Disconnected) => break,
            Err(mpsc::RecvTimeoutError::Timeout) => {}
        }
        let peak = std::mem::take(&mut *level.lock().unwrap());
        // Speech RMS rarely exceeds ~0.3 — scale up so the bars move visibly.
        let _ = app.emit("audio-level", (peak * 3.0).min(1.0));
        if started.elapsed() > Duration::from_secs(MAX_SECONDS as u64 + 5) {
            // Safety net — the buffer is already capped in the callback.
            break;
        }
    }

    drop(stream); // releases the device → OS mic indicator goes off
    let _ = app.emit("audio-level", 0.0f32);
    let samples = std::mem::take(&mut *buffer.lock().unwrap());
    log::info!("[audio] recording stopped ({:.1}s captured)", samples.len() as f32 / sample_rate as f32);
    Captured { samples, sample_rate }
}

type Opened = (cpal::Stream, Arc<Mutex<Vec<f32>>>, Arc<Mutex<f32>>, u32);

fn open_stream(keep_samples: bool) -> Result<Opened, String> {
    let host = cpal::default_host();
    let device = host
        .default_input_device()
        .ok_or("no microphone found — check that an input device is connected and enabled")?;
    let supported = device
        .default_input_config()
        .map_err(|e| format!("could not read microphone config: {e}"))?;

    let sample_rate = supported.sample_rate().0;
    let channels = supported.channels() as usize;
    let format = supported.sample_format();
    let config = cpal::StreamConfig {
        channels: supported.channels(),
        sample_rate: supported.sample_rate(),
        buffer_size: cpal::BufferSize::Default,
    };
    let max_samples = sample_rate as usize * MAX_SECONDS;

    let buffer = Arc::new(Mutex::new(Vec::<f32>::with_capacity(sample_rate as usize * 10)));
    let level = Arc::new(Mutex::new(0.0f32));

    // Downmix one callback's worth of interleaved frames, update level + buffer.
    let sink = {
        let buffer = buffer.clone();
        let level = level.clone();
        move |mono: &[f32]| {
            if mono.is_empty() {
                return;
            }
            let rms = (mono.iter().map(|s| s * s).sum::<f32>() / mono.len() as f32).sqrt();
            {
                let mut l = level.lock().unwrap();
                if rms > *l {
                    *l = rms;
                }
            }
            if keep_samples {
                let mut b = buffer.lock().unwrap();
                let room = max_samples.saturating_sub(b.len());
                b.extend_from_slice(&mono[..mono.len().min(room)]);
            }
        }
    };

    let err_fn = |e| log::warn!("[audio] stream error: {e}");
    let stream = match format {
        SampleFormat::F32 => device.build_input_stream(
            &config,
            move |data: &[f32], _| sink(&downmix(data, channels, |s| s)),
            err_fn,
            None,
        ),
        SampleFormat::I16 => device.build_input_stream(
            &config,
            move |data: &[i16], _| sink(&downmix(data, channels, |s| s as f32 / 32768.0)),
            err_fn,
            None,
        ),
        SampleFormat::U16 => device.build_input_stream(
            &config,
            move |data: &[u16], _| {
                sink(&downmix(data, channels, |s| (s as f32 - 32768.0) / 32768.0))
            },
            err_fn,
            None,
        ),
        SampleFormat::I32 => device.build_input_stream(
            &config,
            move |data: &[i32], _| sink(&downmix(data, channels, |s| s as f32 / 2_147_483_648.0)),
            err_fn,
            None,
        ),
        other => return Err(format!("unsupported microphone sample format: {other:?}")),
    }
    .map_err(|e| format!("could not open microphone: {e}"))?;

    Ok((stream, buffer, level, sample_rate))
}

fn downmix<T: Copy>(data: &[T], channels: usize, to_f32: impl Fn(T) -> f32) -> Vec<f32> {
    let channels = channels.max(1);
    data.chunks(channels)
        .map(|frame| frame.iter().map(|&s| to_f32(s)).sum::<f32>() / frame.len() as f32)
        .collect()
}

/// Linear-interpolation resampler — plenty for speech going to an STT model.
fn resample_linear(input: &[f32], from_sr: u32, to_sr: u32) -> Vec<f32> {
    if from_sr == to_sr || input.len() < 2 {
        return input.to_vec();
    }
    let out_len = (input.len() as u64 * to_sr as u64 / from_sr as u64) as usize;
    let step = from_sr as f64 / to_sr as f64;
    (0..out_len)
        .map(|i| {
            let pos = i as f64 * step;
            let lo = pos.floor() as usize;
            let hi = (lo + 1).min(input.len() - 1);
            let frac = (pos - lo as f64) as f32;
            input[lo.min(input.len() - 1)] * (1.0 - frac) + input[hi] * frac
        })
        .collect()
}

/// Encode mono f32 samples as a 16-bit PCM WAV file.
fn encode_wav_pcm16(samples: &[f32], sample_rate: u32) -> Vec<u8> {
    let data_len = (samples.len() * 2) as u32;
    let mut out = Vec::with_capacity(44 + data_len as usize);
    out.extend_from_slice(b"RIFF");
    out.extend_from_slice(&(36 + data_len).to_le_bytes());
    out.extend_from_slice(b"WAVEfmt ");
    out.extend_from_slice(&16u32.to_le_bytes()); // fmt chunk size
    out.extend_from_slice(&1u16.to_le_bytes()); // PCM
    out.extend_from_slice(&1u16.to_le_bytes()); // mono
    out.extend_from_slice(&sample_rate.to_le_bytes());
    out.extend_from_slice(&(sample_rate * 2).to_le_bytes()); // byte rate
    out.extend_from_slice(&2u16.to_le_bytes()); // block align
    out.extend_from_slice(&16u16.to_le_bytes()); // bits per sample
    out.extend_from_slice(b"data");
    out.extend_from_slice(&data_len.to_le_bytes());
    for &s in samples {
        let v = (s.clamp(-1.0, 1.0) * 32767.0) as i16;
        out.extend_from_slice(&v.to_le_bytes());
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn wav_header_is_valid() {
        let wav = encode_wav_pcm16(&[0.0, 0.5, -0.5, 1.0], 16_000);
        assert_eq!(&wav[0..4], b"RIFF");
        assert_eq!(&wav[8..16], b"WAVEfmt ");
        assert_eq!(u32::from_le_bytes(wav[24..28].try_into().unwrap()), 16_000);
        assert_eq!(u32::from_le_bytes(wav[40..44].try_into().unwrap()), 8);
        assert_eq!(wav.len(), 44 + 8);
    }

    #[test]
    fn resample_48k_to_16k_keeps_duration() {
        let input = vec![0.25f32; 48_000];
        let out = resample_linear(&input, 48_000, 16_000);
        assert_eq!(out.len(), 16_000);
        assert!(out.iter().all(|&s| (s - 0.25).abs() < 1e-6));
    }

    /// Hardware check — opens the default microphone for ~1 s. Run with:
    /// `cargo test --lib audio::tests::opens_default_microphone -- --ignored --nocapture`
    #[test]
    #[ignore]
    fn opens_default_microphone() {
        let (stream, buffer, level, sr) = open_stream(true).expect("open microphone");
        stream.play().expect("start stream");
        std::thread::sleep(Duration::from_millis(1000));
        drop(stream);
        let n = buffer.lock().unwrap().len();
        println!("{sr} Hz, {n} samples captured, peak level {:.4}", *level.lock().unwrap());
        assert!(n > 0, "no samples captured");
    }

    #[test]
    fn downmix_stereo_averages_channels() {
        let mono = downmix(&[1.0f32, 0.0, 0.5, 0.5], 2, |s| s);
        assert_eq!(mono, vec![0.5, 0.5]);
    }
}
