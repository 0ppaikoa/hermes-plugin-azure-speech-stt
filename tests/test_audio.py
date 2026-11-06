"""Audio preparation tests: format passthrough, conversion need, missing-ffmpeg error."""
from __future__ import annotations

import os
import tempfile
import unittest

from _pkg import module  # noqa: E402

_audio = module("audio")
ACCEPTED_SUFFIXES = _audio.ACCEPTED_SUFFIXES
AudioError = _audio.AudioError
find_ffmpeg = _audio.find_ffmpeg
needs_conversion = _audio.needs_conversion
prepared_audio = _audio.prepared_audio


class FormatTests(unittest.TestCase):
    def test_accepted_formats_pass_through(self):
        for suffix in ACCEPTED_SUFFIXES:
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as handle:
                path = handle.name
            try:
                self.assertFalse(needs_conversion(path))
                with prepared_audio(path) as prepared:
                    self.assertEqual(prepared, path)
            finally:
                os.unlink(path)

    def test_other_formats_need_conversion(self):
        for suffix in (".m4a", ".webm", ".ogg", ".opus", ".aac"):
            self.assertTrue(needs_conversion(f"voice{suffix}"))

    def test_missing_ffmpeg_is_a_clear_error(self):
        if find_ffmpeg():
            self.skipTest("ffmpeg is installed on this host")
        with tempfile.NamedTemporaryFile(suffix=".m4a", delete=False) as handle:
            handle.write(b"not really audio")
            path = handle.name
        try:
            with self.assertRaises(AudioError) as ctx:
                with prepared_audio(path):
                    pass
            message = str(ctx.exception)
            self.assertIn("ffmpeg", message)
            self.assertIn("WAV/MP3/FLAC", message)
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
