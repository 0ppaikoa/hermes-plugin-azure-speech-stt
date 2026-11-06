"""Audio preparation: Azure's Fast Transcription accepts WAV, MP3 and FLAC only.

Anything else (the desktop app's m4a, a browser's WebM/Opus) is transcoded to 16 kHz mono
FLAC. If no ffmpeg binary is reachable the caller gets a clear error rather than a silently
degraded transcription.
"""

from __future__ import annotations

import glob
import os
import shutil
import subprocess
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

ACCEPTED_SUFFIXES = frozenset({".wav", ".mp3", ".flac"})
CANDIDATE_GLOBS = (
    "/data/tools/ffmpeg*/bin/ffmpeg",
    "/opt/homebrew/bin/ffmpeg",
    "/usr/local/bin/ffmpeg",
    "/usr/bin/ffmpeg",
)


class AudioError(RuntimeError):
    """The file cannot be handed to the API as-is and could not be converted."""


def find_ffmpeg() -> Optional[str]:
    """PATH first, then the tool directories Hermes provisions."""
    found = shutil.which("ffmpeg")
    if found:
        return found
    for pattern in CANDIDATE_GLOBS:
        for candidate in sorted(glob.glob(pattern), reverse=True):
            if os.access(candidate, os.X_OK):
                return candidate
    return None


def needs_conversion(path: str) -> bool:
    return Path(path).suffix.lower() not in ACCEPTED_SUFFIXES


def _to_flac(source: str, destination: str) -> None:
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        raise AudioError(
            "audio needs converting to WAV/MP3/FLAC for Azure, but ffmpeg was not found "
            "(looked on PATH and in /data/tools/ffmpeg*)")
    command = [ffmpeg, "-y", "-v", "error", "-i", source,
               "-ar", "16000", "-ac", "1", "-c:a", "flac", destination]
    try:
        subprocess.run(command, check=True, capture_output=True, timeout=300)
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or b"").decode("utf-8", "replace").strip()
        raise AudioError(f"ffmpeg failed to convert the audio: {detail or 'no stderr'}") from exc
    except subprocess.TimeoutExpired as exc:
        raise AudioError("ffmpeg timed out converting the audio") from exc
    if not Path(destination).is_file() or Path(destination).stat().st_size == 0:
        raise AudioError("ffmpeg produced no output file")


@contextmanager
def prepared_audio(path: str) -> Iterator[str]:
    """Yield a path the API will accept; convert into a temporary directory when needed."""
    if not needs_conversion(path):
        yield path
        return
    with tempfile.TemporaryDirectory(prefix="azure-speech-") as workdir:
        destination = os.path.join(workdir, "audio.flac")
        _to_flac(path, destination)
        yield destination
