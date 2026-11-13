import io
import shutil
import tempfile
import unittest
import wave
from pathlib import Path
from scripts.generate_music import write_wav_atomic


@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'requires ffmpeg and ffprobe')
class DurationTests(unittest.TestCase):
    def audio(self, seconds):
        buffer = io.BytesIO()
        with wave.open(buffer, 'wb') as stream:
            stream.setnchannels(1)
            stream.setsampwidth(2)
            stream.setframerate(8000)
            stream.writeframes(b'\0\0' * round(8000 * seconds))
        return buffer.getvalue()

    def test_short_song_rejected_and_existing_output_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'song.wav'
            path.write_bytes(b'existing')
            with self.assertRaisesRegex(ValueError, 'expected 180-second'):
                write_wav_atomic(path, self.audio(30), target_duration=180)
            self.assertEqual(path.read_bytes(), b'existing')
            self.assertEqual(list(path.parent.iterdir()), [path])

    def test_rounding_corrected_to_exactly_180_seconds(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'song.wav'
            write_wav_atomic(path, self.audio(179.8), target_duration=180)
            with wave.open(str(path)) as stream:
                self.assertEqual(stream.getnframes(), 180 * stream.getframerate())
