"""Test the "Hey Hover" wake-word model on this machine — no compiling, no downloads.

Mirrors the desktop detector (``hoverai-desktop/src-tauri/src/wake_word.rs``, removed
from the app on 2026-11-01 — see git ``3d80d86``)
exactly: a rolling 2 s window at 16 kHz, scored every 500 ms once full; a
detection fires when the score is above the threshold and at least 1.5 s
have passed since the last one; the threshold comes from the app's
"sensitivity" slider (1–10). The mel/embedding models in the Python package
are byte-identical to the ones in the Rust crate, so results carry over.

Modes:
  guided   Prompts you to say "hey hover" N times, then to stay quiet and to
           talk normally; scores everything and prints hit rate + false alarms
           for every sensitivity setting.
  live     Free-form: shows the score live and announces each detection.
  files    Scores WAV files / folders (e.g. ``positives/`` and ``negatives/``,
           or a saved session's ``audio.wav``).

Each live/guided session is saved to ``sessions/<timestamp>/``: ``audio.wav``
(re-score it later with ``files``), ``scores.csv`` (every 500 ms score),
``detections.csv`` and ``summary.json``.

Run through the ``./wake-test`` wrapper (it picks a working Python):
  ./wake-test guided
  ./wake-test live --sensitivity 7
  ./wake-test files positives negatives
"""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import subprocess
import sys
import wave
from dataclasses import dataclass, field
from datetime import datetime
from itertools import pairwise
from pathlib import Path

HERE = Path(__file__).resolve().parent
# The venv from the training container: its python symlink may be dead, but the
# packages (livekit-wakeword, onnxruntime, numpy) work with any CPython 3.12.
for _sp in sorted((HERE / ".venv" / "lib").glob("python3.12/site-packages")):
    sys.path.insert(0, str(_sp))

import numpy as np  # noqa: E402
from livekit.wakeword import WakeWordModel  # noqa: E402

# ── Detector constants — as in wake_word.rs (git 3d80d86) ──────────────────────
SR = 16_000
WINDOW = 32_000  # 2 s
HOP = 8_000  # 500 ms
HOP_S = HOP / SR
COOLDOWN_S = 1.5
MODEL_KEY = "hey_hover"
DEFAULT_MODEL = HERE / "useful_output" / "hey_hover.onnx"
SESSIONS = HERE / "sessions"

# Two detections closer than this are one utterance firing twice (bug D13).
REPEAT_S = 3.0


def sensitivity_to_threshold(sensitivity: int) -> float:
    """Map the app's 1–10 sensitivity to a score threshold (as in wake_word.rs).

    Args:
        sensitivity: Slider value; clamped to 1–10.

    Returns:
        0.70 at 1, ~0.48 at 5, 0.20 at 10 (wake_word.rs comments claim 0.41 at 5 — they are wrong).
    """
    s = min(max(sensitivity, 1), 10)
    return 0.70 - (s - 1) * (0.50 / 9.0)


def detections(scores: list[tuple[float, float]], threshold: float) -> list[tuple[float, float]]:
    """Replay the app's trigger rule over a score series.

    Args:
        scores: ``(time_s, score)`` per 500 ms hop.
        threshold: Score that must be exceeded.

    Returns:
        ``(time_s, score)`` of every detection.
    """
    out: list[tuple[float, float]] = []
    last = -1e9
    for t, s in scores:
        if s > threshold and t - last >= COOLDOWN_S:
            out.append((t, s))
            last = t
    return out


def repeats(dets: list[tuple[float, float]]) -> int:
    """Count detections that follow the previous one within ``REPEAT_S``.

    Args:
        dets: Detections from :func:`detections`.

    Returns:
        Number of probable double triggers.
    """
    return sum(1 for a, b in pairwise(dets) if b[0] - a[0] < REPEAT_S)


class Scorer:
    """Rolling-window scorer fed with 500 ms hops of 16 kHz float audio."""

    def __init__(self, model_path: Path) -> None:
        """Load the classifier.

        Args:
            model_path: Path to ``hey_hover.onnx``.
        """
        self.model = WakeWordModel(models=[model_path])
        self.window = np.zeros(0, dtype=np.float32)

    def push(self, hop: np.ndarray) -> float | None:
        """Add one hop; score once the window is full.

        Args:
            hop: ``HOP`` float32 samples.

        Returns:
            The score, or None while the first 2 s are still filling.
        """
        self.window = np.concatenate([self.window, hop])[-WINDOW:]
        if len(self.window) < WINDOW:
            return None
        # The app converts to i16 before predict; do the same.
        pcm = (np.clip(self.window, -1, 1) * 32767).astype(np.int16)
        return float(self.model.predict(pcm).get(MODEL_KEY, 0.0))


# ── Microphone ───────────────────────────────────────────────────────────────

def open_mic() -> subprocess.Popen[bytes]:
    """Start a 16 kHz mono s16 raw recorder on the default input.

    Returns:
        The recorder process; read raw PCM from its stdout.

    Raises:
        SystemExit: If neither arecord nor pw-record is installed.
    """
    if shutil.which("arecord"):
        cmd = ["arecord", "-q", "-f", "S16_LE", "-r", str(SR), "-c", "1", "-t", "raw"]
    elif shutil.which("pw-record"):
        cmd = ["pw-record", "--raw", "--rate", str(SR), "--channels", "1", "--format", "s16", "-"]
    else:
        raise SystemExit("Need arecord (alsa-utils) or pw-record (pipewire) to use the microphone.")
    return subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)


def read_hop(proc: subprocess.Popen[bytes]) -> tuple[bytes, np.ndarray] | None:
    """Read one 500 ms hop from the recorder.

    Args:
        proc: Recorder from :func:`open_mic`.

    Returns:
        ``(raw_bytes, float32 samples)``, or None when the stream ended.
    """
    assert proc.stdout is not None
    need, buf = HOP * 2, b""
    while len(buf) < need:
        chunk = proc.stdout.read(need - len(buf))
        if not chunk:
            return None
        buf += chunk
    return buf, np.frombuffer(buf, dtype=np.int16).astype(np.float32) / 32768.0


def bar(score: float, threshold: float, width: int = 30) -> str:
    """Render a score bar with the threshold marked.

    Args:
        score: 0–1 score.
        threshold: Threshold to mark with ``|``.
        width: Bar width in characters.

    Returns:
        The bar string.
    """
    filled = round(score * width)
    mark = round(threshold * width)
    cells = ["█" if i < filled else "·" for i in range(width)]
    if 0 <= mark < width:
        cells[mark] = "|"
    return "".join(cells)


# ── Sessions (live + guided) ─────────────────────────────────────────────────

@dataclass
class Phase:
    """One stretch of a guided session."""

    kind: str  # "prompt" | "quiet" | "talk"
    start: float
    end: float
    label: str
    scores: list[tuple[float, float]] = field(default_factory=list)


_PROMPT_HINTS = [
    "normal voice",
    "normal voice",
    "a bit faster",
    "a bit slower",
    "quietly",
    "louder",
    "lean back / further from the mic",
    "while looking away from the mic",
    "normal voice",
    "normal voice",
    "casually, as if mid-sentence: 'ok… hey hover'",
    "normal voice",
]


def build_plan(prompts: int, slot_s: float, quiet_s: float, talk_s: float) -> list[Phase]:
    """Lay out a guided session on the stream timeline.

    Args:
        prompts: How many times to say the phrase.
        slot_s: Seconds per prompt.
        quiet_s: Seconds of silence at the start.
        talk_s: Seconds of normal talk at the end.

    Returns:
        Phases in order. The first 2 s are a warm-up (the window fills).
    """
    t = 2.0
    plan = [Phase("quiet", t, t + quiet_s, "Stay quiet (measuring the room)")]
    t += quiet_s
    for i in range(prompts):
        hint = _PROMPT_HINTS[i % len(_PROMPT_HINTS)]
        plan.append(Phase("prompt", t, t + slot_s, f"[{i + 1}/{prompts}] Say \"hey hover\" — {hint}"))
        t += slot_s
    plan.append(Phase(
        "talk", t, t + talk_s,
        "Now TALK normally for a while — read something aloud, and mix in near-misses: "
        "\"hey Hoover\", \"however\", \"hover over it\", \"hey Siri\", \"hello\". Don't say \"hey hover\".",
    ))
    return plan


def run_session(args: argparse.Namespace, guided: bool) -> None:
    """Record from the mic, score live, save everything, print a report.

    Args:
        args: Parsed CLI arguments.
        guided: Run the prompt plan instead of free-form listening.
    """
    threshold = args.threshold if args.threshold is not None else sensitivity_to_threshold(args.sensitivity)
    plan = build_plan(args.prompts, args.slot, args.quiet, args.talk) if guided else []
    scorer = Scorer(args.model)

    out = SESSIONS / datetime.now().strftime("%Y%m%d-%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    wav = wave.open(str(out / "audio.wav"), "wb")
    wav.setnchannels(1)
    wav.setsampwidth(2)
    wav.setframerate(SR)

    print(f"Model: {args.model}")
    print(f"Threshold {threshold:.2f}" + ("" if args.threshold is not None else f" (sensitivity {args.sensitivity})"))
    print(f"Saving to {out}")
    print("Warming up 2 s…" if guided else "Listening — say \"hey hover\". Ctrl+C to stop.\n")

    proc = open_mic()
    scores: list[tuple[float, float]] = []
    levels: list[float] = []
    live_dets: list[tuple[float, float]] = []
    last_det = -1e9
    phase_idx = -1
    t = 0.0
    try:
        while True:
            hop = read_hop(proc)
            if hop is None:
                print("\nMicrophone stream ended.")
                break
            raw, samples = hop
            wav.writeframes(raw)
            t += HOP_S
            level = float(np.sqrt(np.mean(samples ** 2)))
            levels.append(level)

            if guided:
                while phase_idx + 1 < len(plan) and t >= plan[phase_idx + 1].start:
                    phase_idx += 1
                    print(f"\n\n▶ {plan[phase_idx].label}")
                if phase_idx == len(plan) - 1 and t >= plan[-1].end:
                    break

            score = scorer.push(samples)
            if score is None:
                continue
            scores.append((t, score))
            if guided and 0 <= phase_idx < len(plan) and t < plan[phase_idx].end:
                plan[phase_idx].scores.append((t, score))

            fired = score > threshold and t - last_det >= COOLDOWN_S
            if fired:
                last_det = t
                live_dets.append((t, score))
                again = len(live_dets) > 1 and t - live_dets[-2][0] < REPEAT_S
                print(f"\r{t:7.1f}s  {bar(score, threshold)}  {score:.2f}   "
                      f">>> DETECTED \"hey hover\" ({score:.2f}){'  [repeat — same utterance?]' if again else ''}")
            else:
                meter = "▁▂▃▄▅▆▇█"[min(7, int(level * 40))]
                print(f"\r{t:7.1f}s  {bar(score, threshold)}  {score:.2f}  mic {meter}   ", end="", flush=True)
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        proc.terminate()
        wav.close()

    with open(out / "scores.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["time_s", "score"])
        w.writerows((f"{a:.1f}", f"{b:.4f}") for a, b in scores)
    with open(out / "detections.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["time_s", "score"])
        w.writerows((f"{a:.1f}", f"{b:.4f}") for a, b in live_dets)

    summary: dict = {
        "model": str(args.model),
        "threshold": threshold,
        "duration_s": round(t, 1),
        "detections": [{"time_s": round(a, 1), "score": round(b, 3)} for a, b in live_dets],
        "repeat_detections": repeats(live_dets),
        "max_score": round(max((s for _, s in scores), default=0.0), 3),
        "mic_rms_median": round(float(np.median(levels)) if levels else 0.0, 4),
    }
    print(f"\n── Session: {t:.0f} s, {len(live_dets)} detection(s) at threshold {threshold:.2f}")
    if summary["mic_rms_median"] < 0.0005:
        print("!! The mic looks silent — is it muted? (wpctl set-mute @DEFAULT_AUDIO_SOURCE@ 0)")
    elif summary["mic_rms_median"] > 0.15:
        print("!! The mic is far too loud (even background noise is near full scale), so speech is "
              "buried and clipped and these results are not meaningful. Lower the input gain "
              "(e.g. `alsamixer` → F4: Capture / Mic Boost) and run again.")
    if guided:
        summary["guided"] = guided_report(plan, scores)
    (out / "summary.json").write_text(json.dumps(summary, indent=2))
    print(f"\nSaved: {out}/{{audio.wav, scores.csv, detections.csv, summary.json}}")


def guided_report(plan: list[Phase], scores: list[tuple[float, float]]) -> dict:
    """Print and return hit rate / false alarms per sensitivity for a guided run.

    A prompt counts as a hit if a detection lands inside its slot (+0.5 s for
    the window lag). Any detection in the quiet or talk stretch is a false alarm.

    Args:
        plan: Phases with their scores filled in.
        scores: The full score series.

    Returns:
        A JSON-able report.
    """
    prompts = [p for p in plan if p.kind == "prompt" and p.scores]
    negatives = [p for p in plan if p.kind != "prompt" and p.scores]
    if not prompts:
        print("Not enough audio for a report (stopped too early).")
        return {}

    print("\nPer prompt (best score in the slot):")
    for p in prompts:
        best = max(s for _, s in p.scores)
        print(f"  {p.label[:60]:<60} {best:.2f}  {bar(best, 0.41, 20)}")
    neg_max = max((s for p in negatives for _, s in p.scores), default=0.0)
    print(f"  Highest score while quiet/talking: {neg_max:.2f}")

    rows = []
    print("\nSensitivity  threshold  hits          false alarms  repeats")
    for sens in range(1, 11):
        thr = sensitivity_to_threshold(sens)
        dets = detections(scores, thr)
        hits = sum(
            1 for p in prompts if any(p.start <= t <= p.end + HOP_S for t, _ in dets)
        )
        false = sum(1 for p in negatives for t, _ in dets if p.start <= t <= p.end)
        rep = repeats(dets)
        rows.append({"sensitivity": sens, "threshold": round(thr, 3), "hits": hits,
                     "prompts": len(prompts), "false_alarms": false, "repeats": rep})
        default = "  ← app default" if sens == 5 else ""
        print(f"  {sens:>2}         {thr:.2f}       {hits:>2}/{len(prompts):<2} ({hits / len(prompts):4.0%})"
              f"   {false:>3}           {rep:>3}{default}")
    good = [r for r in rows if r["false_alarms"] == 0 and r["hits"] > 0]
    if not any(r["hits"] for r in rows):
        print("\nNothing was detected at any setting — check the mic level and that you spoke on cue.")
    elif good:
        best = max(good, key=lambda r: (r["hits"], -r["sensitivity"]))
        print(f"\nBest with zero false alarms: sensitivity {best['sensitivity']} "
              f"({best['hits']}/{best['prompts']} hits).")
    else:
        print("\nEvery setting had false alarms during the quiet/talk part.")
    return {"per_sensitivity": rows, "negative_max_score": round(neg_max, 3),
            "prompt_best_scores": [round(max(s for _, s in p.scores), 3) for p in prompts]}


# ── Files ────────────────────────────────────────────────────────────────────

def load_wav(path: Path) -> np.ndarray:
    """Load a PCM WAV as 16 kHz mono float32 (downmix + linear resample).

    Args:
        path: WAV file (8/16/32-bit integer PCM).

    Returns:
        Samples in -1…1.

    Raises:
        ValueError: For non-PCM WAVs (record with ``--format s16``).
    """
    with wave.open(str(path), "rb") as wf:
        ch, width, rate = wf.getnchannels(), wf.getsampwidth(), wf.getframerate()
        data = wf.readframes(wf.getnframes())
    dtype = {1: np.uint8, 2: np.int16, 4: np.int32}.get(width)
    if dtype is None:
        raise ValueError(f"{path}: unsupported sample width {width}")
    x = np.frombuffer(data, dtype=dtype).astype(np.float32)
    x = (x - 128) / 128 if width == 1 else x / float(np.iinfo(dtype).max)
    x = x.reshape(-1, ch).mean(axis=1)
    if rate != SR:
        n = int(len(x) * SR / rate)
        x = np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x).astype(np.float32)
    return x


def score_file(scorer_model: Path, samples: np.ndarray) -> list[tuple[float, float]]:
    """Stream a clip through a fresh scorer, padded so short clips are fully seen.

    1.5 s of silence is prepended (in the app the window is already full of
    earlier audio) and 1 s appended (the phrase must clear the window).

    Args:
        scorer_model: Model path.
        samples: 16 kHz float32 audio.

    Returns:
        ``(time_s, score)`` per hop; times are relative to the clip start.
    """
    pad_head = int(1.5 * SR)
    x = np.concatenate([np.zeros(pad_head, np.float32), samples, np.zeros(SR, np.float32)])
    x = np.concatenate([x, np.zeros((-len(x)) % HOP, np.float32)])
    scorer = Scorer(scorer_model)
    out = []
    for i in range(0, len(x), HOP):
        s = scorer.push(x[i:i + HOP])
        if s is not None:
            out.append(((i + HOP - pad_head) / SR, s))
    return out


def run_files(args: argparse.Namespace) -> None:
    """Score WAV files; summarise positives vs negatives if folders say so.

    A path whose name contains "pos" is treated as positives (each file
    should trigger once), "neg" as negatives (should never trigger).

    Args:
        args: Parsed CLI arguments.
    """
    threshold = args.threshold if args.threshold is not None else sensitivity_to_threshold(args.sensitivity)
    groups: dict[str, list[tuple[Path, list[tuple[float, float]]]]] = {"pos": [], "neg": [], "other": []}
    for root in args.paths:
        files = sorted(root.rglob("*.wav")) if root.is_dir() else [root]
        name = root.name.lower()
        group = "pos" if "pos" in name else "neg" if "neg" in name else "other"
        for f in files:
            try:
                scores = score_file(args.model, load_wav(f))
            except (ValueError, wave.Error, EOFError) as exc:
                print(f"skip {f}: {exc}")
                continue
            groups[group].append((f, scores))
            dets = detections(scores, threshold)
            best = max((s for _, s in scores), default=0.0)
            when = ", ".join(f"{t:.1f}s" for t, _ in dets)
            print(f"{group:>5}  {best:.2f}  {bar(best, threshold, 20)}  {len(dets)} det {when:<24} {f}")

    if not (groups["pos"] or groups["neg"]):
        return
    print("\nSensitivity  threshold  positives caught   negative files that fired")
    for sens in range(1, 11):
        thr = sensitivity_to_threshold(sens)
        caught = sum(1 for _, s in groups["pos"] if detections(s, thr))
        fired = sum(1 for _, s in groups["neg"] if detections(s, thr))
        np_, nn = len(groups["pos"]), len(groups["neg"])
        print(f"  {sens:>2}         {thr:.2f}       {caught:>3}/{np_:<3}             {fired:>3}/{nn:<3}"
              + ("  ← app default" if sens == 5 else ""))


def main() -> None:
    """Parse arguments and dispatch to a mode."""
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--model", type=Path, default=DEFAULT_MODEL, help="classifier ONNX (default: useful_output/hey_hover.onnx)")
    p.add_argument("--sensitivity", type=int, default=5, help="app slider value 1–10 (default 5 → 0.48)")
    p.add_argument("--threshold", type=float, help="raw threshold; overrides --sensitivity")
    sub = p.add_subparsers(dest="mode", required=True)
    g = sub.add_parser("guided", help="prompted test with a hit-rate report")
    g.add_argument("--prompts", type=int, default=12, help="times to say the phrase (default 12)")
    g.add_argument("--slot", type=float, default=5.0, help="seconds per prompt (default 5)")
    g.add_argument("--quiet", type=float, default=10.0, help="seconds of silence first (default 10)")
    g.add_argument("--talk", type=float, default=45.0, help="seconds of normal talk last (default 45)")
    sub.add_parser("live", help="free-form live listening")
    f = sub.add_parser("files", help="score WAV files or folders")
    f.add_argument("paths", type=Path, nargs="+")
    args = p.parse_args()

    if not args.model.exists():
        raise SystemExit(f"Model not found: {args.model}")
    if args.mode == "files":
        run_files(args)
    else:
        run_session(args, guided=args.mode == "guided")


if __name__ == "__main__":
    main()
