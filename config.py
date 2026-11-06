"""Configuration resolution for the ``stt.azure-speech`` provider.

One location (``base_url``), one model. Everything else is derived:

    file endpoint   <base_url>/speechtotext/transcriptions:transcribe?api-version=...
    live endpoint   <base_url host, .cognitiveservices.azure.com -> .services.ai.azure.com>
                    /mai/v1/realtime?intent=transcription
    live deployment  lower(model) + "-streaming"

Precedence is config.yaml > environment > constant. Nothing here touches the network.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Sequence
from urllib.parse import urlsplit

PROVIDER_NAME = "azure-speech"

DEFAULT_MODEL = "MAI-Transcribe-2"
DEFAULT_API_VERSION = "2025-10-15"
DEFAULT_TIMEOUT = 60.0
DEFAULT_MAX_RETRIES = 1
DEFAULT_STYLE = "clean"
STYLES = ("clean", "verbatim")
KNOWN_MODELS = ("MAI-Transcribe-2", "MAI-Transcribe-1.5")
TOKEN_SCOPE = "https://cognitiveservices.azure.com/.default"

FILE_PATH = "/speechtotext/transcriptions:transcribe"
LIVE_PATH = "/mai/v1/realtime"
LIVE_QUERY = "intent=transcription"
COG_SUFFIX = ".cognitiveservices.azure.com"
FOUNDRY_SUFFIX = ".services.ai.azure.com"

ENV_BASE_URL = "AZURE_SPEECH_BASE_URL"
ENV_API_KEY = "AZURE_SPEECH_API_KEY"
ENV_API_VERSION = "HERMES_AZURE_SPEECH_API_VERSION"
ENV_STREAM_BASE = "HERMES_AZURE_SPEECH_STREAM_BASE"
ENV_STREAM_DEPLOYMENT = "HERMES_AZURE_SPEECH_STREAM_DEPLOYMENT"

MAX_PHRASES = 50


class ConfigError(ValueError):
    """The section is present but unusable; the message names the offending key."""


def _clean(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _as_float(value: Any, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _as_int(value: Any, default: int) -> int:
    try:
        parsed = int(float(value))
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def normalize_base_url(raw: str) -> str:
    """Accept a bare resource name or a full URL; return a bare https/wss origin."""
    value = _clean(raw)
    if not value:
        raise ConfigError(
            "stt.azure-speech.base_url is empty: set it to the resource URL, "
            "e.g. https://<resource>.cognitiveservices.azure.com")
    if "://" not in value:
        value = f"https://{value}{COG_SUFFIX}"
    parts = urlsplit(value)
    if parts.scheme not in ("https", "wss"):
        raise ConfigError(f"stt.azure-speech.base_url must be https: got scheme {parts.scheme!r}")
    if not parts.hostname:
        raise ConfigError("stt.azure-speech.base_url has no host")
    if parts.path not in ("", "/") or parts.query or parts.fragment:
        raise ConfigError("stt.azure-speech.base_url must be an origin, without path or query")
    return f"https://{parts.hostname}"


def foundry_host(base_url: str) -> str:
    """``https://r.cognitiveservices.azure.com`` -> ``https://r.services.ai.azure.com``."""
    parts = urlsplit(base_url)
    host = parts.hostname or ""
    if host.endswith(COG_SUFFIX):
        return f"https://{host[: -len(COG_SUFFIX)]}{FOUNDRY_SUFFIX}"
    return ""


def normalize_language(raw: str) -> str:
    """``ja-JP`` -> ``ja``; ``en_US`` -> ``en``; unknown shapes pass through lowercased."""
    value = _clean(raw).replace("_", "-")
    if not value:
        return ""
    return value.split("-")[0].lower()


def split_phrases(prompt: str, *, limit: int = MAX_PHRASES) -> list[str]:
    """Comma or newline separated vocabulary hints, deduped, capped."""
    seen: dict[str, str] = {}
    for chunk in _clean(prompt).replace("\n", ",").split(","):
        term = chunk.strip()
        if term and term.lower() not in seen:
            seen[term.lower()] = term
    terms = list(seen.values())
    return terms[:limit]


@dataclass(frozen=True)
class AzureSpeechConfig:
    base_url: str
    model: str = DEFAULT_MODEL
    language: str = ""
    api_key: str = ""
    timeout: float = DEFAULT_TIMEOUT
    max_retries: int = DEFAULT_MAX_RETRIES
    style: str = DEFAULT_STYLE
    api_version: str = DEFAULT_API_VERSION
    stream_base: str = ""
    stream_deployment: str = ""
    warnings: tuple[str, ...] = field(default=())

    # -- derived ---------------------------------------------------------
    @property
    def file_url(self) -> str:
        return f"{self.base_url}{FILE_PATH}?api-version={self.api_version}"

    @property
    def live_url(self) -> str:
        base = self.stream_base or foundry_host(self.base_url)
        if not base:
            return ""
        scheme, _, host = base.partition("://")
        return f"{'wss' if scheme == 'https' else 'ws'}://{host}{LIVE_PATH}?{LIVE_QUERY}"

    @property
    def live_deployment(self) -> str:
        return self.stream_deployment or f"{self.model.lower()}-streaming"

    @property
    def language_code(self) -> str:
        return normalize_language(self.language)

    # -- construction ----------------------------------------------------
    @classmethod
    def from_mapping(
        cls,
        section: Optional[Mapping[str, Any]],
        *,
        language: str = "",
        env: Optional[Mapping[str, str]] = None,
    ) -> "AzureSpeechConfig":
        """Resolve one ``stt.azure-speech`` section. Raises :class:`ConfigError`."""
        env = os.environ if env is None else env
        section = section if isinstance(section, Mapping) else {}
        warnings: list[str] = []

        raw_base = _clean(section.get("base_url")) or _clean(env.get(ENV_BASE_URL, ""))
        base_url = normalize_base_url(raw_base)

        model = _clean(section.get("model")) or _clean(env.get("HERMES_AZURE_SPEECH_MODEL", "")) or DEFAULT_MODEL
        if model not in KNOWN_MODELS:
            warnings.append(f"model {model!r} is not one of {', '.join(KNOWN_MODELS)}; continuing anyway")

        style = (_clean(section.get("style")) or DEFAULT_STYLE).lower()
        if style not in STYLES:
            warnings.append(f"style {style!r} is not clean|verbatim; falling back to {DEFAULT_STYLE}")
            style = DEFAULT_STYLE

        lang = _clean(section.get("language")) or _clean(language)
        api_key = _clean(section.get("api_key")) or _clean(env.get(ENV_API_KEY, ""))
        api_version = (_clean(section.get("api_version"))
                       or _clean(env.get(ENV_API_VERSION, ""))
                       or DEFAULT_API_VERSION)

        stream_base = _clean(env.get(ENV_STREAM_BASE, ""))
        if stream_base:
            stream_base = normalize_base_url(stream_base)
        else:
            stream_base = ""
        if not stream_base and not foundry_host(base_url):
            warnings.append(
                "base_url is not a *.cognitiveservices.azure.com host and "
                f"{ENV_STREAM_BASE} is unset: the live path is unavailable")

        return cls(
            base_url=base_url,
            model=model,
            language=lang,
            api_key=api_key,
            timeout=_as_float(section.get("timeout"), DEFAULT_TIMEOUT),
            max_retries=_as_int(section.get("max_retries"), DEFAULT_MAX_RETRIES),
            style=style,
            api_version=api_version,
            stream_base=stream_base,
            stream_deployment=_clean(env.get(ENV_STREAM_DEPLOYMENT, "")),
            warnings=tuple(warnings),
        )
