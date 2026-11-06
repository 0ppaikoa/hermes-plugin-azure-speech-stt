"""Unit tests for config resolution and derivation. No network, no Hermes."""
from __future__ import annotations

import unittest

from _pkg import module  # noqa: E402

_config = module("config")
AzureSpeechConfig = _config.AzureSpeechConfig
ConfigError = _config.ConfigError
DEFAULT_MODEL = _config.DEFAULT_MODEL
foundry_host = _config.foundry_host
normalize_base_url = _config.normalize_base_url
normalize_language = _config.normalize_language
split_phrases = _config.split_phrases


def cfg(section=None, **kwargs):
    return AzureSpeechConfig.from_mapping(section or {}, env={}, **kwargs)


class BaseUrlTests(unittest.TestCase):
    def test_bare_resource_name_expands(self):
        self.assertEqual(normalize_base_url("example-resource"),
                         "https://example-resource.cognitiveservices.azure.com")

    def test_full_url_kept_without_trailing_slash(self):
        self.assertEqual(normalize_base_url("https://r.cognitiveservices.azure.com/"),
                         "https://r.cognitiveservices.azure.com")

    def test_path_is_rejected(self):
        with self.assertRaises(ConfigError):
            normalize_base_url("https://r.cognitiveservices.azure.com/openai/v1")

    def test_http_is_rejected(self):
        with self.assertRaises(ConfigError):
            normalize_base_url("http://r.cognitiveservices.azure.com")

    def test_missing_is_rejected_with_key_name(self):
        with self.assertRaises(ConfigError) as ctx:
            normalize_base_url("")
        self.assertIn("stt.azure-speech.base_url", str(ctx.exception))


class DerivationTests(unittest.TestCase):
    def test_file_url(self):
        c = cfg({"base_url": "https://r.cognitiveservices.azure.com"})
        self.assertEqual(
            c.file_url,
            "https://r.cognitiveservices.azure.com/speechtotext/transcriptions:transcribe"
            "?api-version=2025-10-15")

    def test_live_host_swap(self):
        self.assertEqual(foundry_host("https://r.cognitiveservices.azure.com"),
                         "https://r.services.ai.azure.com")

    def test_live_deployment_derived_from_model(self):
        c = cfg({"base_url": "r"})
        self.assertEqual(c.model, DEFAULT_MODEL)
        self.assertEqual(c.live_deployment, "mai-transcribe-2-streaming")

    def test_live_url(self):
        c = cfg({"base_url": "r"})
        self.assertEqual(c.live_url,
                         "wss://r.services.ai.azure.com/mai/v1/realtime?intent=transcription")

    def test_live_base_override_wins(self):
        c = AzureSpeechConfig.from_mapping(
            {"base_url": "example-resource"}, env={"HERMES_AZURE_SPEECH_STREAM_BASE": "https://stream.example"})
        self.assertEqual(c.live_url, "wss://stream.example/mai/v1/realtime?intent=transcription")

    def test_custom_domain_warns_and_loses_live(self):
        c = cfg({"base_url": "https://speech.example.com"})
        self.assertEqual(c.live_url, "")
        self.assertTrue(any("HERMES_AZURE_SPEECH_STREAM_BASE" in w for w in c.warnings))


class LanguageTests(unittest.TestCase):
    def test_region_subtag_is_stripped(self):
        self.assertEqual(normalize_language("ja-JP"), "ja")
        self.assertEqual(normalize_language("en_US"), "en")
        self.assertEqual(normalize_language(""), "")

    def test_empty_language_means_auto(self):
        self.assertEqual(cfg({"base_url": "r"}).language_code, "")


class StyleAndPhraseTests(unittest.TestCase):
    def test_unknown_style_falls_back_with_warning(self):
        c = cfg({"base_url": "r", "style": "cleen"})
        self.assertEqual(c.style, "clean")
        self.assertTrue(any("cleen" in w for w in c.warnings))

    def test_phrases_split_dedupe_and_cap(self):
        self.assertEqual(split_phrases(" a, B ,a\nb ,, c "), ["a", "B", "c"])
        self.assertEqual(len(split_phrases(",".join(f"t{i}" for i in range(80)))), 50)
        self.assertEqual(split_phrases(""), [])


class PrecedenceTests(unittest.TestCase):
    def test_config_beats_env(self):
        c = AzureSpeechConfig.from_mapping(
            {"base_url": "from-config"}, env={"AZURE_SPEECH_BASE_URL": "from-env"})
        self.assertEqual(c.base_url, "https://from-config.cognitiveservices.azure.com")

    def test_env_used_when_config_missing(self):
        c = AzureSpeechConfig.from_mapping({}, env={"AZURE_SPEECH_BASE_URL": "from-env"})
        self.assertEqual(c.base_url, "https://from-env.cognitiveservices.azure.com")

    def test_api_key_from_env(self):
        c = AzureSpeechConfig.from_mapping(
            {"base_url": "r"}, env={"AZURE_SPEECH_API_KEY": "k"})
        self.assertEqual(c.api_key, "k")


if __name__ == "__main__":
    unittest.main()
