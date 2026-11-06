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
DEFAULT_REALTIME_API_KEY_ENV = "AZURE_SPEECH_API_KEY_REALTIME"
DEFAULT_REALTIME_MODEL = "MAI-Transcribe-2-Streaming"
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


def realtime_origin(base_url: str) -> str:
    """Accept either host form; return the ``.services.ai.azure.com`` origin."""
    parts = urlsplit(base_url)
    host = parts.hostname or ""
    if host.endswith(COG_SUFFIX):
        host = host[: -len(COG_SUFFIX)] + FOUNDRY_SUFFIX
    return f"https://{host}" if host else ""


@dataclass(frozen=True)
class OneshotConfig:
    """One request, one transcript: audio that has already been recorded."""

    base_url: str
    model: str = DEFAULT_MODEL
    language: str = ""
    style: str = DEFAULT_STYLE
    timeout: float = DEFAULT_TIMEOUT
    max_retries: int = DEFAULT_MAX_RETRIES
    api_key: str = ""
    api_key_env: str = DEFAULT_API_KEY_ENV
    warnings: tuple[str, ...] = field(default=())

    @property
    def url(self) -> str:
        # The version is a code constant: the request body this module builds is the one that
        # version accepts. A user-settable version could point the code at a wire shape it does
        # not know, so the key is not read (see the warning in from_mapping).
        return f"{self.base_url}{FILE_PATH}?api-version={DEFAULT_API_VERSION}"

    @property
    def language_code(self) -> str:
        return normalize_language(self.language)

    @classmethod
    def from_mapping(cls, section: Mapping[str, Any], *,
                     language: str = "") -> "OneshotConfig":
        warnings: list[str] = []
        base_url = normalize_base_url(_clean(section.get("base_url")),
                                      "stt.azure-speech.oneshot.base_url")

        model = _clean(section.get("model")) or DEFAULT_MODEL
        if model not in KNOWN_MODELS:
            warnings.append(
                f"model {model!r} is not one of {', '.join(KNOWN_MODELS)}; continuing anyway")

        if "api_version" in section:
            warnings.append(
                f"api_version is fixed by the plugin ({DEFAULT_API_VERSION}); "
                "the configured value is ignored")

        style = (_clean(section.get("style")) or DEFAULT_STYLE).lower()
        if style not in STYLES:
            warnings.append(f"style {style!r} is not clean|verbatim; falling back to {DEFAULT_STYLE}")
            style = DEFAULT_STYLE

        return cls(
            base_url=base_url,
            model=model,
            language=_clean(section.get("language")) or _clean(language),
            style=style,
            timeout=_as_float(section.get("timeout"), DEFAULT_TIMEOUT),
            max_retries=_as_int(section.get("max_retries"), DEFAULT_MAX_RETRIES),
            api_key=_clean(section.get("api_key")),
            api_key_env=_clean(section.get("api_key_env")) or DEFAULT_API_KEY_ENV,
            warnings=tuple(warnings),
        )


@dataclass(frozen=True)
class RealtimeConfig:
    """A live session. ``model`` is the deployment name, used verbatim."""

    base_url: str
    model: str = DEFAULT_REALTIME_MODEL
    language: str = ""
    api_key: str = ""
    api_key_env: str = DEFAULT_REALTIME_API_KEY_ENV
    warnings: tuple[str, ...] = field(default=())

    @property
    def url(self) -> str:
        origin = realtime_origin(self.base_url)
        if not origin:
            return ""
        scheme, _, host = origin.partition("://")
        return f"{'wss' if scheme == 'https' else 'ws'}://{host}{LIVE_PATH}?{LIVE_QUERY}"

    @property
    def deployment(self) -> str:
        return self.model

    @property
    def language_code(self) -> str:
        return normalize_language(self.language)

    @classmethod
    def from_mapping(cls, section: Mapping[str, Any], *,
                     language: str = "") -> "RealtimeConfig":
        warnings: list[str] = []
        base_url = normalize_base_url(_clean(section.get("base_url")),
                                      "stt.azure-speech.realtime.base_url")
        origin = realtime_origin(base_url)
        if not origin:
            warnings.append(
                "realtime.base_url has no host: the live path is unavailable")

        return cls(
            base_url=origin or base_url,
            model=_clean(section.get("model")) or DEFAULT_REALTIME_MODEL,
            language=_clean(section.get("language")) or _clean(language),
            api_key=_clean(section.get("api_key")),
            api_key_env=_clean(section.get("api_key_env")) or DEFAULT_REALTIME_API_KEY_ENV,
            warnings=tuple(warnings),
        )


@dataclass(frozen=True)
class AzureSpeechConfig:
    """The resolved ``stt.azure-speech`` section: one configuration per transport."""

    oneshot: Optional[OneshotConfig] = None
    realtime: Optional[RealtimeConfig] = None
    warnings: tuple[str, ...] = field(default=())

    @classmethod
    def from_mapping(cls, section: Optional[Mapping[str, Any]], *,
                     language: str = "") -> "AzureSpeechConfig":
        """Resolve one ``stt.azure-speech`` section. Raises :class:`ConfigError`.

        Reads the config mapping only: no environment, no network.
        """
        section = section if isinstance(section, Mapping) else {}

        legacy = [key for key in ("base_url", "stream_base_url", "model", "style", "timeout",
                                  "max_retries", "api_version", "api_key", "api_key_env")
                  if key in section]
        if legacy:
            raise ConfigError(
                "stt.azure-speech uses flat keys that are no longer read ("
                + ", ".join(sorted(legacy))
                + "): move them under stt.azure-speech.oneshot / .realtime")

        warnings: list[str] = []

        oneshot: Optional[OneshotConfig] = None
        raw_oneshot = section.get("oneshot")
        if raw_oneshot is not None:
            if not isinstance(raw_oneshot, Mapping):
                raise ConfigError("stt.azure-speech.oneshot must be a mapping")
            oneshot = OneshotConfig.from_mapping(raw_oneshot, language=language)
            warnings.extend(oneshot.warnings)

        realtime: Optional[RealtimeConfig] = None
        raw_realtime = section.get("realtime")
        if raw_realtime is not None:
            if not isinstance(raw_realtime, Mapping):
                raise ConfigError("stt.azure-speech.realtime must be a mapping")
            realtime = RealtimeConfig.from_mapping(raw_realtime, language=language)
            warnings.extend(realtime.warnings)

        if oneshot is None and realtime is None:
            raise ConfigError(
                "stt.azure-speech configures neither transport: set oneshot.base_url, "
                "realtime.base_url, or both")

        return cls(oneshot=oneshot, realtime=realtime, warnings=tuple(warnings))


def resolve_api_key(cfg: Any,
                    *, lookup: Optional[Callable[[str], Optional[str]]] = None) -> str:
    """The credential: a literal ``api_key`` wins, otherwise the variable named by ``api_key_env``.

    Works on either transport's config. ``api_key_env`` exists so an existing environment (a
    container, a systemd unit) can keep the variable name it already uses. An empty result means
    "authenticate with Microsoft Entra ID".
    """
    if getattr(cfg, "api_key", ""):
        return cfg.api_key
    read = lookup or _hermes_env_lookup
    return _clean(read(getattr(cfg, "api_key_env", DEFAULT_API_KEY_ENV)))
