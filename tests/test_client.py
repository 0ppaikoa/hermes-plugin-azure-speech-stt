"""Unit tests for the file transport: request shape, response parsing, error mapping, retries.

A local HTTP server stands in for Azure; nothing leaves the machine.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

from _pkg import module  # noqa: E402

_client = module("client")
_extract_text = _client._extract_text
build_definition = _client.build_definition
transcribe_file = _client.transcribe_file
AzureSpeechConfig = module("config").AzureSpeechConfig


def make_cfg(port: int, **kwargs: object) -> AzureSpeechConfig:
    base: dict[str, object] = dict(base_url=f"http://127.0.0.1:{port}", api_key="test-key", style="clean")
    base.update(kwargs)
    return AzureSpeechConfig(**base)  # type: ignore[arg-type]


class FakeAzure(BaseHTTPRequestHandler):
    queue: list[tuple[int, dict]] = []
    seen_headers: list[dict] = []
    seen_bodies: list[bytes] = []

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        FakeAzure.seen_bodies.append(self.rfile.read(length))
        FakeAzure.seen_headers.append({k.lower(): v for k, v in self.headers.items()})
        status, payload = FakeAzure.queue.pop(0) if FakeAzure.queue else (200, {})
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args, **kwargs):  # silence the test output
        pass


class DefinitionTests(unittest.TestCase):
    def test_definition_carries_model_style_language_and_phrases(self):
        cfg = AzureSpeechConfig(base_url="https://r.cognitiveservices.azure.com",
                                language="ja-JP", style="clean")
        definition = build_definition(cfg, prompt="alpha, beta")
        self.assertEqual(definition["locales"], ["ja"])
        self.assertEqual(definition["enhancedMode"]["model"], "MAI-Transcribe-2")
        self.assertEqual(definition["enhancedMode"]["modelOptions"]["transcribeStyle"], "clean")
        self.assertEqual(definition["phraseList"]["phrases"], ["alpha", "beta"])

    def test_empty_language_omits_locales(self):
        cfg = AzureSpeechConfig(base_url="https://r.cognitiveservices.azure.com")
        self.assertNotIn("locales", build_definition(cfg))

    def test_no_prompt_omits_phrase_list(self):
        cfg = AzureSpeechConfig(base_url="https://r.cognitiveservices.azure.com")
        self.assertNotIn("phraseList", build_definition(cfg, prompt="  "))

    def test_extract_text_prefers_combined_phrases(self):
        payload = {"combinedPhrases": [{"text": "abc"}], "phrases": [{"text": "zzz"}]}
        self.assertEqual(_extract_text(payload), "abc")

    def test_extract_text_falls_back_to_phrases(self):
        self.assertEqual(_extract_text({"phrases": [{"text": "a"}, {"text": "b"}]}), "ab")
        self.assertEqual(_extract_text({}), "")


class TransportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), FakeAzure)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.audio = tempfile.NamedTemporaryFile(suffix=".flac", delete=False)
        cls.audio.write(b"fake-audio-bytes")
        cls.audio.close()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)
        os.unlink(cls.audio.name)

    def setUp(self):
        FakeAzure.queue = []
        FakeAzure.seen_headers = []
        FakeAzure.seen_bodies = []

    def test_success_returns_transcript_and_sends_key_header(self):
        FakeAzure.queue = [(200, {"combinedPhrases": [{"text": "テスト"}]})]
        result = transcribe_file(make_cfg(self.port), self.audio.name)
        self.assertTrue(result["success"], result)
        self.assertEqual(result["transcript"], "テスト")
        self.assertEqual(result["provider"], "azure-speech")
        self.assertEqual(FakeAzure.seen_headers[0]["ocp-apim-subscription-key"], "test-key")
        self.assertIn(b'name="definition"', FakeAzure.seen_bodies[0])
        self.assertIn(b'name="audio"', FakeAzure.seen_bodies[0])

    def test_400_with_code_is_surfaced(self):
        FakeAzure.queue = [(400, {"code": "InvalidRequest",
                                      "message": "Enhanced mode with model is currently not supported yet."})]
        result = transcribe_file(make_cfg(self.port), self.audio.name)
        self.assertFalse(result["success"])
        self.assertIn("InvalidRequest", result["error"])
        self.assertIn("Enhanced mode", result["error"])

    def test_401_mentions_role(self):
        FakeAzure.queue = [(401, {"code": "Unauthorized", "message": "nope"})]
        result = transcribe_file(make_cfg(self.port), self.audio.name)
        self.assertFalse(result["success"])
        self.assertIn("not authorised", result["error"])

    def test_empty_transcript_is_an_error(self):
        FakeAzure.queue = [(200, {"combinedPhrases": []})]
        result = transcribe_file(make_cfg(self.port), self.audio.name)
        self.assertFalse(result["success"])
        self.assertIn("empty transcript", result["error"])

    def test_retry_then_success(self):
        FakeAzure.queue = [(503, {}), (200, {"combinedPhrases": [{"text": "ok"}]})]
        result = transcribe_file(make_cfg(self.port, max_retries=2), self.audio.name)
        self.assertTrue(result["success"], result)
        self.assertEqual(len(FakeAzure.seen_headers), 2)

    def test_no_retry_when_max_retries_zero(self):
        FakeAzure.queue = [(503, {}), (200, {"combinedPhrases": [{"text": "ok"}]})]
        result = transcribe_file(make_cfg(self.port, max_retries=0), self.audio.name)
        self.assertFalse(result["success"])
        self.assertEqual(len(FakeAzure.seen_headers), 1)

    def test_network_failure_is_an_envelope(self):
        cfg = make_cfg(self.port + 1, timeout=2.0, max_retries=0)
        result = transcribe_file(cfg, self.audio.name)
        self.assertFalse(result["success"])
        self.assertIn("network error", result["error"])


if __name__ == "__main__":
    unittest.main()
