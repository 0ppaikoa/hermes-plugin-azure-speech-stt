"""Manifest and registration tests: what the plugin loader will actually read."""
from __future__ import annotations

import pathlib
import unittest

from _pkg import module  # noqa: E402

try:
    import yaml
except Exception:  # pragma: no cover - yaml ships with Hermes
    yaml = None

MANIFEST = pathlib.Path(__file__).resolve().parent.parent / "plugin.yaml"
ALLOWED_KINDS = {"standalone", "backend", "platform", "model-provider", "exclusive"}


@unittest.skipIf(yaml is None, "PyYAML not installed")
class ManifestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))

    def test_identity(self):
        self.assertEqual(self.manifest["name"], module("config").PROVIDER_NAME)
        self.assertEqual(self.manifest["license"], "MIT")
        self.assertTrue(self.manifest["author"])

    def test_kind_is_one_the_loader_accepts(self):
        self.assertIn(self.manifest["kind"], ALLOWED_KINDS)

    def test_manifest_version_is_supported(self):
        version = self.manifest.get("manifest_version")
        if version is None:
            return  # optional per pm/plugin_declarations.manifest_version_error
        self.assertIsInstance(version, int)
        self.assertLessEqual(version, 2)  # SUPPORTED_MANIFEST_VERSION

    def test_requires_hermes_parses(self):
        self.assertRegex(self.manifest["requires_hermes"], r"^>=?\d")

    def test_only_the_credentials_are_declared_as_env(self):
        entries = self.manifest["optional_env"]
        self.assertEqual([e["name"] for e in entries],
                         ["AZURE_SPEECH_API_KEY", "AZURE_SPEECH_API_KEY_REALTIME"])
        entry = entries[0]
        for key in ("name", "description", "prompt"):
            self.assertTrue(entry.get(key), f"optional_env entry missing {key}")
        self.assertIs(entry["password"], True)

    def test_no_bundled_tools_or_hooks_are_claimed(self):
        self.assertEqual(self.manifest["provides_tools"], [])
        self.assertEqual(self.manifest["provides_hooks"], [])


class RegistrationTests(unittest.TestCase):
    def test_register_hands_one_provider_to_the_context(self):
        import _pkg

        azs = _pkg.PACKAGE
        captured = []

        class FakeContext:
            def register_transcription_provider(self, provider):
                captured.append(provider)

        self.assertTrue(hasattr(azs, "register"), "plugin entry point is register(ctx)")
        azs.register(FakeContext())
        self.assertEqual(len(captured), 1)
        self.assertEqual(captured[0].name, module("config").PROVIDER_NAME)


if __name__ == "__main__":
    unittest.main()
