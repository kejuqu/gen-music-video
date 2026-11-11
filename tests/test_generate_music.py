import json
import unittest
from pathlib import Path
from unittest.mock import patch

import requests

from scripts.generate_music import api_base, build_payload, generation_id


EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "lyrics.zh.json"


class MusicTests(unittest.TestCase):
    def setUp(self):
        self.data = json.loads(EXAMPLE.read_text(encoding="utf-8"))

    def test_payload_keeps_language_and_english_style(self):
        payload = build_payload(self.data)
        self.assertEqual(payload["language"], "zh")
        self.assertEqual(payload["text"], self.data["style"])
        self.assertEqual(payload["lyrics"], self.data["lyrics"])
        self.assertEqual(payload["modelId"], "ace-step-1.5-turbo-q8")
        self.assertFalse(payload["instrumental"])
        self.assertEqual(payload["modelOptions"]["ace_step"]["duration_seconds"], 180)

    def test_missing_language_is_rejected(self):
        del self.data["_lang"]
        with self.assertRaisesRegex(ValueError, "_lang"):
            build_payload(self.data)

    def test_generation_id_reads_header(self):
        response = requests.Response()
        response.headers["X-Yovoice-Generation-ID"] = "abc123"
        self.assertEqual(generation_id(response), "abc123")

    def test_generation_id_supports_json_fallback(self):
        response = requests.Response()
        response.status_code = 200
        response._content = b'{"generationId":"xyz"}'
        response.headers["Content-Type"] = "application/json"
        self.assertEqual(generation_id(response), "xyz")

    def test_api_base_uses_explicit_environment(self):
        with patch.dict("os.environ", {"YOVOICE_API_BASE": "http://localhost:9999/"}):
            self.assertEqual(api_base(), "http://localhost:9999")


if __name__ == "__main__":
    unittest.main()
