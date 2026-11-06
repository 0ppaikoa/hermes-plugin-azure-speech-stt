"""Unit tests for config resolution and derivation. No network, no Hermes."""
from __future__ import annotations

import unittest

from _pkg import module  # noqa: E402

_config = module("config")
AzureSpeechConfig = _config.AzureSpeechConfig
OneshotConfig = _config.OneshotConfig
RealtimeConfig = _config.RealtimeConfig
ConfigError = _config.ConfigError
DEFAULT_MODEL = _config.DEFAULT_MODEL
DEFAULT_API_KEY_ENV = _config.DEFAULT_API_KEY_ENV
DEFAULT_REALTIME_API_KEY_ENV = _config.DEFAULT_REALTIME_API_KEY_ENV
DEFAULT_REALTIME_MODEL = _config.DEFAULT_REALTIME_MODEL
resolve_api_key = _config.resolve_api_key
realtime_origin = _config.realtime_origin
normalize_base_url = _config.normalize_base_url
normalize_language = _config.normalize_language
split_phrases = _config.split_phrases

ONESHOT = {"base_url": "example-resource"}


def cfg(oneshot=None, realtime=None, **kwargs):
    section = {}
    if oneshot is not False:
        section["oneshot"] = dict(oneshot if oneshot is not None else ONESHOT)
    if realtime is not None:
        section["realtime"] = dict(realtime)
    return AzureSpeechConfig.from_mapping(section, **kwargs)


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
            normalize_base_url("", "stt.azure-speech.oneshot.base_url")
        self.assertIn("stt.azure-speech.oneshot.base_url", str(ctx.exception))


class OneshotTests(unittest.TestCase):
    def test_url(self):
        self.assertEqual(
            cfg().oneshot.url,
            "https://example-resource.cognitiveservices.azure.com"
            "/speechtotext/transcriptions:transcribe?api-version=2025-10-15")

    def test_defaults(self):
        oneshot = cfg().oneshot
        self.assertEqual(oneshot.model, DEFAULT_MODEL)
        self.assertEqual(oneshot.style, "clean")
        self.assertEqual(oneshot.api_key_env, DEFAULT_API_KEY_ENV)

    def test_api_version_is_not_configurable(self):
        c = cfg(oneshot={"base_url": "r", "api_version": "1999-01-01"})
        self.assertIn("api-version=2025-10-15", c.oneshot.url)
        self.assertTrue(any("api_version" in w for w in c.warnings))

    def test_unknown_style_falls_back_with_warning(self):
        c = cfg(oneshot={"base_url": "r", "style": "cleen"})
        self.assertEqual(c.oneshot.style, "clean")
        self.assertTrue(any("cleen" in w for w in c.warnings))


class RealtimeTests(unittest.TestCase):
    def test_absent_section_means_no_live_path(self):
        self.assertIsNone(cfg().realtime)

    def test_host_is_swapped_to_services_ai(self):
        self.assertEqual(realtime_origin("https://r.cognitiveservices.azure.com"),
                         "https://r.services.ai.azure.com")

    def test_url(self):
        c = cfg(realtime={"base_url": "example-resource"})
        self.assertEqual(c.realtime.url,
                         "wss://example-resource.services.ai.azure.com"
                         "/mai/v1/realtime?intent=transcription")

    def test_model_is_the_deployment_verbatim(self):
        c = cfg(realtime={"base_url": "example-resource"})
        self.assertEqual(c.realtime.model, DEFAULT_REALTIME_MODEL)
        self.assertEqual(c.realtime.deployment, DEFAULT_REALTIME_MODEL)

    def test_custom_deployment_is_not_derived(self):
        c = cfg(realtime={"base_url": "example-resource", "model": "my-streaming"})
        self.assertEqual(c.realtime.deployment, "my-streaming")

    def test_default_key_variable_is_separate(self):
        c = cfg(realtime={"base_url": "example-resource"})
        self.assertEqual(c.realtime.api_key_env, DEFAULT_REALTIME_API_KEY_ENV)
        self.assertNotEqual(c.realtime.api_key_env, c.oneshot.api_key_env)


class SectionShapeTests(unittest.TestCase):
    def test_empty_section_is_rejected(self):
        with self.assertRaises(ConfigError) as ctx:
            AzureSpeechConfig.from_mapping({})
        self.assertIn("neither transport", str(ctx.exception))

    def test_realtime_alone_is_valid(self):
        c = cfg(oneshot=False, realtime={"base_url": "example-resource"})
        self.assertIsNone(c.oneshot)
        self.assertEqual(c.realtime.deployment, DEFAULT_REALTIME_MODEL)

    def test_oneshot_alone_is_valid(self):
        c = cfg()
        self.assertIsNotNone(c.oneshot)
        self.assertIsNone(c.realtime)

    def test_flat_keys_are_rejected_with_a_migration_hint(self):
        with self.assertRaises(ConfigError) as ctx:
            AzureSpeechConfig.from_mapping({"base_url": "example-resource"})
        message = str(ctx.exception)
        self.assertIn("base_url", message)
        self.assertIn("oneshot", message)

    def test_each_transport_keeps_its_own_language(self):
        c = cfg(oneshot={"base_url": "r", "language": "ja-JP"},
                realtime={"base_url": "r2", "language": "en-US"})
        self.assertEqual(c.oneshot.language_code, "ja")
        self.assertEqual(c.realtime.language_code, "en")

    def test_section_language_beats_the_caller_hint(self):
        c = cfg(oneshot={"base_url": "r", "language": "ja"}, language="en")
        self.assertEqual(c.oneshot.language_code, "ja")


class LanguageTests(unittest.TestCase):
    def test_region_subtag_is_stripped(self):
        self.assertEqual(normalize_language("ja-JP"), "ja")
        self.assertEqual(normalize_language("en_US"), "en")
        self.assertEqual(normalize_language(""), "")

    def test_empty_language_means_auto(self):
        self.assertEqual(cfg().oneshot.language_code, "")


class PhrasesTests(unittest.TestCase):
    def test_phrases_split_dedupe_and_cap(self):
        self.assertEqual(split_phrases(" a, B ,a\nb ,, c "), ["a", "B", "c"])
        self.assertEqual(len(split_phrases(",".join(f"t{i}" for i in range(80)))), 50)
        self.assertEqual(split_phrases(""), [])


class CredentialTests(unittest.TestCase):
    def test_literal_key_wins_over_env(self):
        c = cfg(oneshot={"base_url": "r", "api_key": "literal", "api_key_env": "MY_VAR"})
        self.assertEqual(resolve_api_key(c.oneshot, lookup=lambda name: "from-env"), "literal")

    def test_default_variable_name(self):
        c = cfg()
        self.assertEqual(resolve_api_key(c.oneshot, lookup=lambda name: name), DEFAULT_API_KEY_ENV)

    def test_custom_variable_name_is_honoured(self):
        c = cfg(oneshot={"base_url": "r", "api_key_env": "ACME_SPEECH_KEY"})
        seen = {}

        def lookup(name):
            seen["name"] = name
            return "secret"

        self.assertEqual(resolve_api_key(c.oneshot, lookup=lookup), "secret")
        self.assertEqual(seen["name"], "ACME_SPEECH_KEY")

    def test_each_transport_resolves_its_own_variable(self):
        c = cfg(realtime={"base_url": "r2"})
        names = []
        resolve_api_key(c.oneshot, lookup=lambda name: names.append(name))
        resolve_api_key(c.realtime, lookup=lambda name: names.append(name))
        self.assertEqual(names, [DEFAULT_API_KEY_ENV, DEFAULT_REALTIME_API_KEY_ENV])

    def test_no_credential_means_entra(self):
        self.assertEqual(resolve_api_key(cfg().oneshot, lookup=lambda name: None), "")


if __name__ == "__main__":
    unittest.main()
