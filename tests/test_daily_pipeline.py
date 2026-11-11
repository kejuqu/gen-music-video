import os
import subprocess
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PIPELINE = PROJECT_ROOT / "scripts" / "daily_pipeline.sh"


class DailyPipelineTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.bin = self.root / "bin"
        self.output_root = self.root / "output"
        self.bin.mkdir()
        self.output_root.mkdir()
        self._write_fake_commands()

    def tearDown(self):
        self.temporary_directory.cleanup()

    def _executable(self, name, content):
        path = self.bin / name
        path.write_text("#!/bin/bash\n" + content, encoding="utf-8")
        path.chmod(0o755)

    def _write_fake_commands(self):
        self._executable("ffprobe", "exit 0\n")
        self._executable("curl", "exit 0\n")
        self._executable("ollama", "exit 0\n")
        self._executable("yovoice", "exit 0\n")
        self._executable(
            "ffmpeg",
            'for LAST; do :; done\nprintf fake > "$LAST"\n',
        )
        self._executable(
            "blender",
            '''while [ "$#" -gt 0 ]; do
  if [ "$1" = "--output" ]; then
    printf fake > "$2"
    exit 0
  fi
  shift
done
exit 1
''',
        )
        self._executable(
            "python3",
            '''if [ "${1:-}" = "-c" ]; then
  exit 0
fi
SCRIPT="$1"
shift
if [[ "$SCRIPT" == *video_filename.py ]]; then
  printf 'Test_Song_%s.mp4\n' "$DATE"
  exit 0
fi
if [ "${FAIL_ZH:-0}" = "1" ] && [ "${LYRICS_LANG:-}" = "zh" ]; then
  exit 1
fi
while [ "$#" -gt 0 ]; do
  if [ "$1" = "--output" ]; then
    printf fake > "$2"
    exit 0
  fi
  shift
done
exit 1
''',
        )

    def _run(self, **extra_environment):
        environment = os.environ.copy()
        environment.update(
            {
                "PATH": f"{self.bin}:/usr/bin:/bin",
                "PYTHON": str(self.bin / "python3"),
                "BLENDER": str(self.bin / "blender"),
                "MUSIC_VIDEO_ROOT": str(self.output_root),
                "YOVOICE_API_TOKEN": "test-token",
                "DATE": "2026-11-10",
                "LANGS": "zh en",
            }
        )
        environment.update(extra_environment)
        return subprocess.run(
            ["bash", str(PIPELINE)],
            cwd=PROJECT_ROOT,
            env=environment,
            text=True,
            capture_output=True,
            timeout=20,
        )

    def test_failed_chinese_job_does_not_block_english_job(self):
        result = self._run(FAIL_ZH="1")

        self.assertEqual(result.returncode, 1)
        english_video = self.output_root / "2026-11-10" / "en" / "Test_Song_2026-11-10.mp4"
        chinese_video = self.output_root / "2026-11-10" / "zh" / "Test_Song_2026-11-10.mp4"
        self.assertTrue(english_video.exists())
        self.assertFalse(chinese_video.exists())
        log = (self.output_root / "_logs" / "2026-11-10.log").read_text(encoding="utf-8")
        self.assertIn("[zh] FAILED", log)
        self.assertIn("[en] OK", log)
        self.assertIn("fail=1", log)

    def test_invalid_language_fails_before_generating_outputs(self):
        result = self._run(LANGS="zh fr")

        self.assertEqual(result.returncode, 1)
        self.assertFalse((self.output_root / "2026-11-10" / "zh").exists())
        log = (self.output_root / "_logs" / "2026-11-10.log").read_text(encoding="utf-8")
        self.assertIn("unsupported language", log)

    def test_explicit_environment_overrides_config_file(self):
        config = self.root / "pipeline.env"
        config.write_text('LANGS="zh en"\nDATE="2020-01-01"\n', encoding="utf-8")

        result = self._run(PIPELINE_ENV_FILE=str(config), LANGS="zh")

        self.assertEqual(result.returncode, 0)
        self.assertTrue((self.output_root / "2026-11-10" / "zh" / "Test_Song_2026-11-10.mp4").exists())
        self.assertFalse((self.output_root / "2026-11-10" / "en").exists())
        self.assertFalse((self.output_root / "2020-01-01").exists())

    def test_healthy_yovoice_service_does_not_require_cli(self):
        (self.bin / "yovoice").unlink()

        result = self._run(LANGS="zh")

        self.assertEqual(result.returncode, 0)
        self.assertTrue((self.output_root / "2026-11-10" / "zh" / "Test_Song_2026-11-10.mp4").exists())


if __name__ == "__main__":
    unittest.main()
