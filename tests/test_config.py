"""Unit tests for config resolution and derivation. No network, no Hermes."""
from __future__ import annotations

import unittest

from _pkg import module  # noqa: E402

_config = module("config")
AzureSpeechConfig = _config.AzureSpeechConfig
ConfigError = _config.ConfigError
DEFAULT_MODEL = _config.DEFAULT_MODEL
DEFAULT_API_KEY_ENV = _config.DEFAULT_API_KEY_ENV
resolve_api_key = _config.resolve_api_key
foundry_host = _config.foundry_host
normalize_base_url = _config.normalize_base_url
normalize_language = _config.normalize_language
split_phrases = _config.split_phrases


def cfg(section=None, **kwargs):
    return AzureSpeechConfig.from_mapping(section or {}, **kwargs)


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

    def test_stream_base_url_wins(self):
        c = cfg({"base_url": "example-resource", "stream_base_url": "https://stream.example"})
        self.assertEqual(c.live_url, "wss://stream.example/mai/v1/realtime?intent=transcription")

    def test_custom_domain_warns_and_loses_live(self):
        c = cfg({"base_url": "https://speech.example.com"})
        self.assertEqual(c.live_url, "")
        self.assertTrue(any("stream_base_url" in w for w in c.warnings))


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


class CredentialTests(unittest.TestCase):
    def test_literal_key_wins_over_env(self):
        c = cfg({"base_url": "example-resource", "api_key": "literal", "api_key_env": "MY_VAR"})
        self.assertEqual(resolve_api_key(c, lookup=lambda name: "from-env"), "literal")

    def test_default_variable_name(self):
        c = cfg({"base_url": "example-resource"})
        self.assertEqual(c.api_key_env, DEFAULT_API_KEY_ENV)
        self.assertEqual(resolve_api_key(c, lookup=lambda name: name), DEFAULT_API_KEY_ENV)

    def test_custom_variable_name_is_honoured(self):
        c = cfg({"base_url": "example-resource", "api_key_env": "ACME_SPEECH_KEY"})
        seen = {}

        def lookup(name):
            seen["name"] = name
            return "secret"

        self.assertEqual(resolve_api_key(c, lookup=lookup), "secret")
        self.assertEqual(seen["name"], "ACME_SPEECH_KEY")

    def test_no_credential_means_entra(self):
        c = cfg({"base_url": "example-resource"})
        self.assertEqual(resolve_api_key(c, lookup=lambda name: None), "")

    def test_environment_is_never_consulted_for_settings(self):
        import os
        os.environ["AZURE_SPEECH_BASE_URL"] = "https://ignored.example"
        os.environ["HERMES_AZURE_SPEECH_STREAM_BASE"] = "https://ignored.example"
        try:
            with self.assertRaises(ConfigError):
                AzureSpeechConfig.from_mapping({})
            c = cfg({"base_url": "example-resource"})
            self.assertEqual(c.base_url, "https://example-resource.cognitiveservices.azure.com")
            self.assertEqual(c.stream_base_url, "")
        finally:
            os.environ.pop("AZURE_SPEECH_BASE_URL", None)
            os.environ.pop("HERMES_AZURE_SPEECH_STREAM_BASE", None)


if __name__ == "__main__":
    unittest.main()
