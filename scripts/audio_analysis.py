"""PCM audio analysis shared by Blender rendering and lightweight unit tests."""

from __future__ import annotations

import argparse
import json
import math
import wave
from pathlib import Path
from typing import Any

import numpy as np


def _normalized(values: np.ndarray) -> np.ndarray:
    maximum = float(values.max()) if values.size else 0.0
    return values / maximum if maximum > 0 else values


def load_audio(path: str | Path, fps: int = 24) -> dict[str, Any]:
    """Return per-frame RMS and low/mid/high FFT band energies.

    The input should be mono 44.1 kHz signed 16-bit PCM. Multi-channel PCM is
    accepted and downmixed as a convenience, but other sample widths are
    rejected explicitly so ffmpeg normalization remains a visible pipeline step.
    """
    if fps <= 0:
        raise ValueError("fps must be positive")

    with wave.open(str(path), "rb") as source:
        sample_rate = source.getframerate()
        sample_count = source.getnframes()
        channels = source.getnchannels()
        sample_width = source.getsampwidth()
        raw = source.readframes(sample_count)

    if sample_width != 2:
        raise ValueError(f"analysis.wav must be 16-bit PCM, got sampwidth={sample_width}")
    if sample_rate <= 0 or channels <= 0:
        raise ValueError("analysis.wav has invalid audio metadata")

    data = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if channels > 1:
        data = data.reshape(-1, channels).mean(axis=1)
    if data.size == 0:
        raise ValueError("analysis.wav contains no audio samples")

    frame_count = max(1, math.ceil(data.size / sample_rate * fps))
    window_size = max(2, round(sample_rate / fps))
    frequencies = np.fft.rfftfreq(window_size, d=1.0 / sample_rate)
    low_mask = frequencies < 250
    mid_mask = (frequencies >= 250) & (frequencies < 2000)
    high_mask = frequencies >= 2000

    rms = np.zeros(frame_count, dtype=np.float32)
    low = np.zeros(frame_count, dtype=np.float32)
    mid = np.zeros(frame_count, dtype=np.float32)
    high = np.zeros(frame_count, dtype=np.float32)
    flux = np.zeros(frame_count, dtype=np.float32)
    previous = np.zeros(frequencies.size)

    for frame in range(frame_count):
        start = math.floor(frame * sample_rate / fps)
        end = math.floor((frame + 1) * sample_rate / fps)
        chunk = data[start:end]
        if chunk.size < window_size:
            chunk = np.pad(chunk, (0, window_size - chunk.size))
        elif chunk.size > window_size:
            chunk = chunk[:window_size]

        rms[frame] = np.sqrt(np.mean(chunk**2))
        spectrum = np.abs(np.fft.rfft(chunk * np.hanning(window_size)))
        flux[frame] = np.maximum(spectrum - previous, 0).sum()
        previous = spectrum
        low[frame] = spectrum[low_mask].mean() if low_mask.any() else 0.0
        mid[frame] = spectrum[mid_mask].mean() if mid_mask.any() else 0.0
        high[frame] = spectrum[high_mask].mean() if high_mask.any() else 0.0

    # Adaptive onset detection: transient attacks, not a synthetic BPM clock.
    onset = np.zeros(frame_count, dtype=np.float32)
    last = -fps
    for frame in range(frame_count):
        history = flux[max(0, frame - fps):frame]
        threshold = max(float(np.median(history)) * 1.8 if history.size else 0, float(flux.max()) * 0.08)
        if (flux[frame] > threshold and flux[frame] >= flux[max(0, frame - 1)]
                and flux[frame] >= flux[min(frame_count - 1, frame + 1)]
                and frame - last >= max(1, round(fps * 0.18))):
            onset[frame] = flux[frame]
            last = frame
    onset = _normalized(onset)
    pulse = onset.copy()
    for frame in range(1, frame_count):
        pulse[frame] = max(pulse[frame], pulse[frame - 1] * math.exp(-1 / (fps * 0.12)))

    return {
        "fps": fps,
        "frames": frame_count,
        "duration": data.size / sample_rate,
        "rms": _normalized(rms),
        "low": _normalized(low),
        "mid": _normalized(mid),
        "high": _normalized(high),
        "onset": onset,
        "pulse": pulse,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect per-frame audio energies.")
    parser.add_argument("path", type=Path, help="16-bit PCM WAV file")
    parser.add_argument("--fps", type=int, default=24)
    args = parser.parse_args()
    result = load_audio(args.path, args.fps)
    summary = {
        "fps": result["fps"],
        "frames": result["frames"],
        "duration": result["duration"],
        "peak": {
            band: float(result[band].max())
            for band in ("rms", "low", "mid", "high")
        },
    }
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
