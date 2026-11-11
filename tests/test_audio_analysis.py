import tempfile
import unittest
import wave
from pathlib import Path

import numpy as np

from scripts.audio_analysis import load_audio


class AudioAnalysisTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)

    def tearDown(self):
        self.temporary_directory.cleanup()

    def write_pcm16(self, name, samples, sample_rate=8000, channels=1):
        path = self.root / name
        with wave.open(str(path), "wb") as output:
            output.setnchannels(channels)
            output.setsampwidth(2)
            output.setframerate(sample_rate)
            output.writeframes((np.asarray(samples) * 32767).astype(np.int16).tobytes())
        return path

    def test_low_sine_has_dominant_low_band_and_complete_timeline(self):
        sample_rate = 8000
        time = np.arange(sample_rate) / sample_rate
        samples = np.empty(sample_rate, dtype=np.float32)
        split = int(sample_rate * 0.75)
        samples[:split] = 0.5 * np.sin(2 * np.pi * 100 * time[:split])
        samples[split:] = 0.5 * np.sin(2 * np.pi * 3000 * time[split:])
        path = self.write_pcm16("bands.wav", samples, sample_rate)

        result = load_audio(path, fps=10)

        self.assertEqual(result["frames"], 10)
        self.assertAlmostEqual(result["duration"], 1.0)
        self.assertGreater(float(result["low"].mean()), float(result["high"].mean()))
        self.assertAlmostEqual(float(result["low"].max()), 1.0)
        self.assertAlmostEqual(float(result["rms"].max()), 1.0)

    def test_partial_final_frame_is_retained(self):
        sample_rate = 8000
        samples = np.zeros(8400, dtype=np.float32)
        path = self.write_pcm16("partial.wav", samples, sample_rate)

        result = load_audio(path, fps=10)

        self.assertEqual(result["frames"], 11)
        self.assertEqual(float(result["rms"].max()), 0.0)

    def test_rejects_non_16_bit_audio(self):
        path = self.root / "8bit.wav"
        with wave.open(str(path), "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(1)
            output.setframerate(8000)
            output.writeframes(bytes([128] * 80))

        with self.assertRaisesRegex(ValueError, "16-bit PCM"):
            load_audio(path, fps=10)

    def test_rejects_empty_audio(self):
        path = self.write_pcm16("empty.wav", [])

        with self.assertRaisesRegex(ValueError, "no audio samples"):
            load_audio(path)

    def test_onsets_follow_percussive_attacks_and_silence_stays_quiet(self):
        samples = np.zeros(16000)
        for start in (2000, 6000, 10000, 14000):
            t = np.arange(400) / 8000
            samples[start:start + 400] = np.sin(2 * np.pi * 100 * t) * np.exp(-t * 60)
        result = load_audio(self.write_pcm16("beats.wav", samples), fps=24)
        peaks = np.flatnonzero(result["onset"])
        self.assertEqual(len(peaks), 4)
        np.testing.assert_allclose(peaks / 24, [0.25, 0.75, 1.25, 1.75], atol=1/24)
        silent = load_audio(self.write_pcm16("silence.wav", np.zeros(8000)))
        self.assertEqual(float(silent["pulse"].max()), 0)


if __name__ == "__main__":
    unittest.main()
