"""Azure AI Speech as a Hermes Agent speech-to-text provider.

Registers the provider name ``azure-speech``. One location and one model; everything else is
derived, so the config surface stays at two required keys.

    stt:
      provider: azure-speech
      streaming: true                    # opt in to the live path (voice mode)
      prompt: ""                         # vocabulary hints -> phraseList (file path only)
      azure-speech:
        base_url: https://<resource>.cognitiveservices.azure.com
        model: MAI-Transcribe-2          # or MAI-Transcribe-1.5
        language: ""                     # empty -> automatic detection
        style: clean                     # clean | verbatim
        timeout: 60
        max_retries: 1
        stream_base_url: ""               # only when the live model lives on another resource
        api_key: ""                       # literal key, discouraged
        api_key_env: AZURE_SPEECH_API_KEY # name of the variable holding the key

Derived from those two:

    file endpoint    <base_url>/speechtotext/transcriptions:transcribe?api-version=2025-10-15
    live endpoint    <base_url host .cognitiveservices.azure.com -> .services.ai.azure.com>
                     /mai/v1/realtime?intent=transcription
    live deployment  lower(model) + "-streaming"

Non-secret settings come from ``config.yaml`` only. The one environment read is the credential,
and even its variable name is configurable (``api_key_env``). With no key configured the provider
authenticates with a Microsoft Entra bearer token.
"""

from __future__ import annotations

import logging
import time
from dataclasses import replace
from typing import Any, Optional

try:  # Hermes core
    from agent.transcription_provider import TranscriptionProvider  # type: ignore[assignment]
except Exception:  # pragma: no cover - lets the test suite import without Hermes

    class TranscriptionProvider:  # type: ignore[no-redef]
        @property
        def name(self) -> str:  # pragma: no cover
            raise NotImplementedError

        def transcribe(self, *args: Any, **kwargs: Any) -> dict[str, Any]:  # pragma: no cover
            raise NotImplementedError

        @property
        def streaming_capable(self) -> bool:
            return False

        def open_stream_session(self, *, language: Optional[str] = None,
                                prompt: Optional[str] = None) -> Any:  # pragma: no cover
            raise NotImplementedError


from .audio import AudioError, prepared_audio
from .client import model_choices, transcribe_file
from .config import (
    AzureSpeechConfig,
    ConfigError,
    DEFAULT_API_KEY_ENV,
    DEFAULT_MODEL,
    PROVIDER_NAME,
    resolve_api_key,
)
from .live import AzureSpeechLiveSession, LiveSessionError

logger = logging.getLogger(__name__)


def _error(message: str) -> dict[str, Any]:
    return {"success": False, "transcript": "", "provider": PROVIDER_NAME, "error": message}


def _stt_section() -> dict[str, Any]:
    from tools.transcription_tools import _load_stt_config

    config = _load_stt_config() or {}
    return config if isinstance(config, dict) else {}


LIVE_FAILURE_COOLDOWN_SECONDS = 600.0


class AzureSpeechProvider(TranscriptionProvider):
    """Fast Transcription (file) and Realtime (live) over one Azure Speech resource."""

    def __init__(self) -> None:
        # A live endpoint that derived cleanly but has no deployment behind it fails on every
        # utterance; without a memory of that, each recording would pay the failed handshake
        # (seconds) before Hermes falls back to the file path. One failure buys a cooldown.
        self._live_failed_at: float = 0.0

    @property
    def name(self) -> str:
        return PROVIDER_NAME

    @property
    def display_name(self) -> str:
        return "Azure AI Speech"

    # -- configuration ---------------------------------------------------
    def _config(self, language: str = "") -> AzureSpeechConfig:
        section = _stt_section()
        provider_section = section.get(PROVIDER_NAME) or {}
        provider_section = provider_section if isinstance(provider_section, dict) else {}
        return AzureSpeechConfig.from_mapping(provider_section, language=language)

    def is_available(self) -> bool:
        """No network, never raises — the picker calls this on every paint."""
        try:
            cfg = self._config()
        except ConfigError:
            return False
        except Exception:  # pragma: no cover - defensive
            return False
        if resolve_api_key(cfg):
            return True
        try:
            import azure.identity  # noqa: F401
        except Exception:
            return False
        return True

    def get_setup_schema(self) -> dict[str, Any]:
        return {
            "name": self.display_name,
            "badge": "azure",
            "tag": ("MAI-Transcribe file transcription, plus live streaming when deployed. "
                    "Set stt.azure-speech.base_url in config.yaml; a key is optional here."),
            "env_vars": [
                {"key": DEFAULT_API_KEY_ENV,
                 "prompt": "Azure Speech resource key (blank = Entra ID)",
                 "url": "https://portal.azure.com/", "password": True},
            ],
        }

    def list_models(self) -> list[dict[str, Any]]:
        return model_choices()

    def default_model(self) -> Optional[str]:
        return DEFAULT_MODEL

    # -- file path -------------------------------------------------------
    def transcribe(self, file_path: str, *, model: Optional[str] = None,
                   language: Optional[str] = None, **extra: Any) -> dict[str, Any]:
        try:
            cfg = self._config(language or "")
        except ConfigError as exc:
            return _error(str(exc))
        except Exception as exc:  # pragma: no cover - defensive
            return _error(f"could not read stt.azure-speech: {exc}")

        if model:
            cfg = replace(cfg, model=str(model))
        prompt = str(extra.get("prompt") or _stt_section().get("prompt") or "")
        for warning in cfg.warnings:
            logger.warning("azure-speech: %s", warning)

        try:
            with prepared_audio(file_path) as prepared:
                return transcribe_file(cfg, prepared, prompt=prompt)
        except AudioError as exc:
            return _error(str(exc))
        except Exception as exc:  # pragma: no cover - defensive
            return _error(f"transcription failed: {exc}")

    # -- live path -------------------------------------------------------
    @property
    def streaming_capable(self) -> bool:
        try:
            cfg = self._config()
        except Exception:
            return False
        if not cfg.live_url:
            return False
        if self._live_failed_at and (time.monotonic() - self._live_failed_at
                                     < LIVE_FAILURE_COOLDOWN_SECONDS):
            return False
        return True

    def open_stream_session(self, *, language: Optional[str] = None,
                            prompt: Optional[str] = None) -> AzureSpeechLiveSession:
        cfg = self._config(language or "")
        if prompt:
            logger.info("azure-speech: the live path has no keyword biasing; "
                        "stt.prompt is ignored for this utterance")
        try:
            return AzureSpeechLiveSession(cfg, language=language or "", prompt=prompt or "")
        except LiveSessionError:
            self._live_failed_at = time.monotonic()
            logger.warning("azure-speech: live session failed; using the file path for the next "
                           "%d seconds", int(LIVE_FAILURE_COOLDOWN_SECONDS))
            raise


def register(ctx: Any) -> None:
    ctx.register_transcription_provider(AzureSpeechProvider())


__all__ = ["AzureSpeechProvider", "register", "LiveSessionError"]
