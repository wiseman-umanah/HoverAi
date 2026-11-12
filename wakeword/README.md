# Hey Hover wake word

This directory trains and exports a custom LiveKit wake-word model for the
phrase `hey hover`.

## Requirements

- Python 3.12 or newer
- [`uv`](https://docs.astral.sh/uv/)
- Enough disk space for the LiveKit data assets and generated clips

All commands below should be run from this directory:

```bash
cd /workspaces/HoverAi/wakeword
```

## Install

Create the virtual environment and install the locked dependencies:

```bash
uv sync
```

Download the Piper speech model and the supporting LiveKit wake-word data:

```bash
uv run livekit-wakeword setup --data-dir ./data
```

This downloads the Piper model, validation features, room impulse responses,
and background audio. The ACAV100M feature set is approximately 17 GB. If it
is not needed for a particular workflow, it can be skipped with:

```bash
uv run livekit-wakeword setup --data-dir ./data --skip-acav
```

The Piper files must be available at these paths because the training script
uses the default relative data directory:

```text
data/piper/en-us-libritts-high.pt
data/piper/en-us-libritts-high.json
```

## Train and compile

The pipeline is configured in [`main.py`](main.py). It uses the phrase
`hey hover`, generates 5,000 samples, trains for 30,000 steps, exports an
ONNX model, and evaluates the result:

```bash
uv run python main.py
```

The script runs these LiveKit stages in order:

1. Generate synthetic positive and adversarial negative clips.
2. Augment the clips and extract features.
3. Train the classifier.
4. Export the trained classifier to ONNX.
5. Evaluate the exported model.

The ONNX export is the compiled model to use for deployment. The export path
is printed at the end of the run.

## Generated data

The raw generated dataset is stored under:

```text
output/hey_hover/
├── positive_train/
├── positive_test/
├── negative_train/
├── negative_test/
├── background_train/
└── background_test/
```

Keep the positive and negative train/test sets separate when inspecting or
rebuilding the dataset. Do not use the same clips in both train and test.

## Deployment files

The useful deployment and evaluation files have been extracted into
`useful_output/`:

| File | Purpose |
| --- | --- |
| `hey_hover.onnx` | Compiled ONNX wake-word model for integration |
| `hey_hover.pt` | PyTorch version of the trained model |
| `hey_hover_det.png` | Detection error trade-off plot |
| `hey_hover_eval.json` | Evaluation output |
| `hey_hover_metrics.json` | Detailed evaluation metrics |

For Electron or another LiveKit client, copy or reference
`useful_output/hey_hover.onnx`. The large `output/` directory contains the
training clips and intermediate artifacts and is not required at runtime.

## Useful CLI commands

To see the stages provided by the installed LiveKit package:

```bash
uv run livekit-wakeword --help
```

Individual stages are also available through the CLI, but this repository's
`main.py` is the reproducible pipeline because it keeps the model name,
phrase, sample count, and training steps in one place.

## Test the model on your own voice (no training setup needed)

`test_model.py` replays the detector the desktop app used (removed from the app on 2026-11-01; code in git at `3d80d86`) exactly (2 s window scored
every 500 ms, 1.5 s cooldown, threshold from the 1–10 sensitivity slider)
against your microphone or WAV files. It reuses the packages already in
`.venv` with any Python 3.12 (the `./wake-test` wrapper finds one), so it needs
no compiling and no downloads. The mel/embedding models are byte-identical to
the ones in the Rust crate, so results carry over to the app.

```bash
wpctl set-mute @DEFAULT_AUDIO_SOURCE@ 0   # make sure the mic isn't muted
./wake-test guided          # ~2 min: prompts you 12×, then quiet + normal talk
./wake-test live            # free-form; shows the score live, Ctrl+C to stop
./wake-test files sessions/<id>/audio.wav     # re-score a saved session
./wake-test files positives negatives         # folders of WAV clips
```

`guided` prints a table of hits / false alarms / double triggers for every
sensitivity value. Each session is saved under `sessions/<timestamp>/`
(`audio.wav`, `scores.csv`, `detections.csv`, `summary.json`; git-ignored).
Record extra clips with
`pw-record --rate 16000 --channels 1 --format s16 clip.wav`.
