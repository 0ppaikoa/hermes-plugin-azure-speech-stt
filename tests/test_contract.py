"""Conformance tests: the plugin against Hermes' provider contract.

Everything here is about the interface rather than the wire — the parts a refactor of either side
could silently break. Runs offline; ABC-dependent checks skip when Hermes is not importable.
"""
# pyright: reportAttributeAccessIssue=false  (this module monkeypatches test seams by design)
from __future__ import annotations

import inspect
import socket
import unittest

from _pkg import module  # noqa: E402

import _pkg  # noqa: E402

azs = _pkg.PACKAGE

try:  # Hermes core, when the suite runs inside an installation
    from agent.transcription_provider import TranscriptionProvider

    HAVE_CORE = True
except Exception:  # pragma: no cover - standalone runs
    TranscriptionProvider = object  # type: ignore[assignment]
    HAVE_CORE = False


class ProviderIdentityTests(unittest.TestCase):
    def setUp(self):
        azs._stt_section = lambda: {"azure-speech": {"base_url": "example-resource"}}
        self.provider = azs.AzureSpeechProvider()

    def test_name_is_lowercase_and_matches_the_config_section(self):
        self.assertEqual(self.provider.name, "azure-speech")
        self.assertEqual(self.provider.name, self.provider.name.lower().strip())

    def test_display_name_is_set(self):
        self.assertTrue(self.provider.display_name)

    def test_models(self):
        self.assertEqual(self.provider.default_model(), "MAI-Transcribe-2")
        ids = [m["id"] for m in self.provider.list_models()]
        self.assertIn("MAI-Transcribe-2", ids)
        for entry in self.provider.list_models():
            self.assertIn("id", entry)

    def test_setup_schema_shape(self):
        schema = self.provider.get_setup_schema()
        for key in ("name", "badge", "tag", "env_vars"):
            self.assertIn(key, schema)
        self.assertEqual(len(schema["env_vars"]), 1, "only the credential is an env var")
        entry = schema["env_vars"][0]
        self.assertEqual(entry["key"], "AZURE_SPEECH_API_KEY")
        self.assertTrue(entry["prompt"])
        self.assertIs(entry["password"], True)


@unittest.skipUnless(HAVE_CORE, "Hermes core not importable")
class AbcConformanceTests(unittest.TestCase):
    def setUp(self):
        azs._stt_section = lambda: {"azure-speech": {"base_url": "example-resource"}}
        self.provider = azs.AzureSpeechProvider()

    def test_is_a_transcription_provider(self):
        self.assertIsInstance(self.provider, TranscriptionProvider)

    def test_transcribe_signature_matches_the_abc(self):
        ours = inspect.signature(self.provider.transcribe)
        base = inspect.signature(TranscriptionProvider.transcribe)
        # a bound method's signature drops ``self``; the ABC's unbound one keeps it
        self.assertEqual(list(ours.parameters), [p for p in base.parameters if p != "self"])
        self.assertEqual(ours.parameters["file_path"].kind, base.parameters["file_path"].kind)
        for name in ("model", "language"):
            self.assertEqual(ours.parameters[name].kind, base.parameters[name].kind)

    def test_streaming_surface_matches_the_abc(self):
        ours = inspect.signature(self.provider.open_stream_session)
        base = inspect.signature(TranscriptionProvider.open_stream_session)
        self.assertEqual(list(ours.parameters), [p for p in base.parameters if p != "self"])
        self.assertIsInstance(self.provider.streaming_capable, bool)

    def test_stream_session_exposes_the_three_methods(self):
        for method in ("push_audio", "partial_transcript", "finalize"):
            self.assertTrue(callable(getattr(azs.AzureSpeechLiveSession, method)))


class EnvelopeTests(unittest.TestCase):
    def test_missing_config_returns_an_error_envelope(self):
        azs._stt_section = lambda: {}
        result = azs.AzureSpeechProvider().transcribe("/tmp/whatever.flac")
        self.assertEqual(set(result), {"success", "transcript", "provider", "error"})
        self.assertFalse(result["success"])
        self.assertEqual(result["provider"], "azure-speech")
        self.assertIn("base_url", result["error"])

    def test_provider_never_raises_when_the_transport_blows_up(self):
        import tempfile

        azs._stt_section = lambda: {"azure-speech": {"base_url": "example-resource"}}
        provider = azs.AzureSpeechProvider()
        # the package imported the function by name, so that is the seam to patch —
        # patching client.transcribe_file would leave the real caller in place and
        # let the test hit the network (learned the hard way)
        original = azs.transcribe_file

        def boom(*args, **kwargs):
            raise RuntimeError("transport exploded")

        azs.transcribe_file = boom
        handle = tempfile.NamedTemporaryFile(suffix=".flac", delete=False)  # accepted as-is, no ffmpeg
        handle.write(b"irrelevant")
        handle.close()
        try:
            result = provider.transcribe(handle.name)
        finally:
            azs.transcribe_file = original
            import os
            os.unlink(handle.name)
        self.assertFalse(result["success"])
        self.assertIn("transport exploded", result["error"])

    def test_audio_error_is_an_envelope(self):
        azs._stt_section = lambda: {"azure-speech": {"base_url": "example-resource"}}
        provider = azs.AzureSpeechProvider()
        audio_mod = module("audio")
        original = audio_mod.find_ffmpeg
        audio_mod.find_ffmpeg = lambda: None
        try:
            result = provider.transcribe("/tmp/does-not-matter.m4a")
        finally:
            audio_mod.find_ffmpeg = original
        self.assertFalse(result["success"])


class AvailabilityTests(unittest.TestCase):
    def test_is_available_without_config_is_false_and_does_not_raise(self):
        azs._stt_section = lambda: {}
        self.assertIs(azs.AzureSpeechProvider().is_available(), False)

    def test_is_available_opens_no_socket(self):
        azs._stt_section = lambda: {"azure-speech": {"base_url": "example-resource"}}
        provider = azs.AzureSpeechProvider()
        original = socket.socket

        def refuse(*args, **kwargs):
            raise AssertionError("is_available() must not touch the network")

        socket.socket = refuse  # type: ignore[assignment]
        try:
            self.assertIsInstance(provider.is_available(), bool)
        finally:
            socket.socket = original  # type: ignore[assignment]

    def test_failed_live_session_buys_a_cooldown(self):
        azs._stt_section = lambda: {"azure-speech": {"base_url": "example-resource"}}
        provider = azs.AzureSpeechProvider()
        self.assertTrue(provider.streaming_capable)

        original = azs.AzureSpeechLiveSession

        def broken(*args, **kwargs):
            raise azs.LiveSessionError("no deployment behind the derived host")

        azs.AzureSpeechLiveSession = broken
        try:
            with self.assertRaises(azs.LiveSessionError):
                provider.open_stream_session(language="ja")
        finally:
            azs.AzureSpeechLiveSession = original

        self.assertFalse(provider.streaming_capable,
                         "a failed handshake must not be retried on every utterance")

        # once the cooldown lapses the provider offers the live path again
        import time
        provider._live_failed_at = time.monotonic() - (azs.LIVE_FAILURE_COOLDOWN_SECONDS + 1)
        self.assertTrue(provider.streaming_capable)

    def test_streaming_unavailable_without_a_derivable_live_host(self):
        azs._stt_section = lambda: {"azure-speech": {"base_url": "https://custom.example"}}
        provider = azs.AzureSpeechProvider()
        self.assertIs(provider.streaming_capable, False)
        with self.assertRaises(azs.LiveSessionError):
            provider.open_stream_session(language="ja")


if __name__ == "__main__":
    unittest.main()
