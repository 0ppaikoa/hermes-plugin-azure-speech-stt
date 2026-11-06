"""Configuration resolution for the ``stt.azure-speech`` provider.

One location, one model. Everything else is derived:

    file endpoint   <base_url>/speechtotext/transcriptions:transcribe?api-version=...
    live endpoint   <base_url host, .cognitiveservices.azure.com -> .services.ai.azure.com>
                    /mai/v1/realtime?intent=transcription
    live deployment  lower(model) + "-streaming"

Non-secret values live in ``config.yaml`` only — this module never reads the environment.
The single exception is a credential, whose variable *name* is itself configurable
(``api_key_env``, default ``AZURE_SPEECH_API_KEY``). Resolution happens in
:func:`resolve_api_key` through Hermes' profile-scoped ``get_env_value``, so a multiplexed
gateway cannot read another profile's secret. Nothing here touches the network.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Optional
from urllib.parse import urlsplit

PROVIDER_NAME = "azure-speech"

DEFAULT_MODEL = "MAI-Transcribe-2"
DEFAULT_API_VERSION = "2025-10-15"
DEFAULT_TIMEOUT = 60.0
DEFAULT_MAX_RETRIES = 1
DEFAULT_STYLE = "clean"
DEFAULT_API_KEY_ENV = "AZURE_SPEECH_API_KEY"
STYLES = ("clean", "verbatim")
KNOWN_MODELS = ("MAI-Transcribe-2", "MAI-Transcribe-1.5")
TOKEN_SCOPE = "https://cognitiveservices.azure.com/.default"

FILE_PATH = "/speechtotext/transcriptions:transcribe"
LIVE_PATH = "/mai/v1/realtime"
LIVE_QUERY = "intent=transcription"
COG_SUFFIX = ".cognitiveservices.azure.com"
FOUNDRY_SUFFIX = ".services.ai.azure.com"

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


def normalize_base_url(raw: str, key: str = "stt.azure-speech.base_url") -> str:
    """Accept a bare resource name or a full URL; return an https origin."""
    value = _clean(raw)
    if not value:
        raise ConfigError(
            f"{key} is empty: set it to the resource URL, "
            "e.g. https://<resource>.cognitiveservices.azure.com")
    if "://" not in value:
        value = f"https://{value}{COG_SUFFIX}"
    parts = urlsplit(value)
    if parts.scheme not in ("https", "wss"):
        raise ConfigError(f"{key} must be https: got scheme {parts.scheme!r}")
    if not parts.hostname:
        raise ConfigError(f"{key} has no host")
    if parts.path not in ("", "/") or parts.query or parts.fragment:
        raise ConfigError(f"{key} must be an origin, without path or query")
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


def _hermes_env_lookup(name: str) -> Optional[str]:
    """Profile-scoped credential read; None when Hermes is unavailable (tests)."""
    try:
        from hermes_cli.config import get_env_value
    except Exception:  # pragma: no cover - Hermes is present in production
        return None
    try:
        return get_env_value(name)
    except Exception:  # pragma: no cover
        return None


@dataclass(frozen=True)
class AzureSpeechConfig:
    base_url: str
    model: str = DEFAULT_MODEL
    language: str = ""
    style: str = DEFAULT_STYLE
    timeout: float = DEFAULT_TIMEOUT
    max_retries: int = DEFAULT_MAX_RETRIES
    api_version: str = DEFAULT_API_VERSION
    stream_base_url: str = ""
    api_key: str = ""
    api_key_env: str = DEFAULT_API_KEY_ENV
    warnings: tuple[str, ...] = field(default=())

    # -- derived ---------------------------------------------------------
    @property
    def file_url(self) -> str:
        return f"{self.base_url}{FILE_PATH}?api-version={self.api_version}"

    @property
    def live_url(self) -> str:
        base = self.stream_base_url or foundry_host(self.base_url)
        if not base:
            return ""
        scheme, _, host = base.partition("://")
        return f"{'wss' if scheme == 'https' else 'ws'}://{host}{LIVE_PATH}?{LIVE_QUERY}"

    @property
    def live_deployment(self) -> str:
        return f"{self.model.lower()}-streaming"

    @property
    def language_code(self) -> str:
        return normalize_language(self.language)

    # -- construction ----------------------------------------------------
    @classmethod
    def from_mapping(cls, section: Optional[Mapping[str, Any]], *,
                     language: str = "") -> "AzureSpeechConfig":
        """Resolve one ``stt.azure-speech`` section. Raises :class:`ConfigError`.

        Reads the config mapping only: no environment, no network.
        """
        section = section if isinstance(section, Mapping) else {}
        warnings: list[str] = []

        base_url = normalize_base_url(_clean(section.get("base_url")))

        model = _clean(section.get("model")) or DEFAULT_MODEL
        if model not in KNOWN_MODELS:
            warnings.append(
                f"model {model!r} is not one of {', '.join(KNOWN_MODELS)}; continuing anyway")

        style = (_clean(section.get("style")) or DEFAULT_STYLE).lower()
        if style not in STYLES:
            warnings.append(f"style {style!r} is not clean|verbatim; falling back to {DEFAULT_STYLE}")
            style = DEFAULT_STYLE

        raw_stream = _clean(section.get("stream_base_url"))
        stream_base_url = ""
        if raw_stream:
            stream_base_url = normalize_base_url(raw_stream, "stt.azure-speech.stream_base_url")
        elif not foundry_host(base_url):
            warnings.append(
                "base_url is not a *.cognitiveservices.azure.com host and "
                "stt.azure-speech.stream_base_url is unset: the live path is unavailable")

        return cls(
            base_url=base_url,
            model=model,
            language=_clean(section.get("language")) or _clean(language),
            style=style,
            timeout=_as_float(section.get("timeout"), DEFAULT_TIMEOUT),
            max_retries=_as_int(section.get("max_retries"), DEFAULT_MAX_RETRIES),
            api_version=_clean(section.get("api_version")) or DEFAULT_API_VERSION,
            stream_base_url=stream_base_url,
            api_key=_clean(section.get("api_key")),
            api_key_env=_clean(section.get("api_key_env")) or DEFAULT_API_KEY_ENV,
            warnings=tuple(warnings),
        )


def resolve_api_key(cfg: AzureSpeechConfig,
                    *, lookup: Optional[Callable[[str], Optional[str]]] = None) -> str:
    """The credential: a literal ``api_key`` wins, otherwise the variable named by ``api_key_env``.

    ``api_key_env`` exists so an existing environment (a container, a systemd unit) can keep the
    variable name it already uses. An empty result means "authenticate with Microsoft Entra ID".
    """
    if cfg.api_key:
        return cfg.api_key
    read = lookup or _hermes_env_lookup
    return _clean(read(cfg.api_key_env))
