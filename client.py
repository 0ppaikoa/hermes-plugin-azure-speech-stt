"""File transcription against Azure Fast Transcription with the MAI-Transcribe models.

The wire shape is one multipart POST: ``audio`` plus a JSON ``definition``. Response text
lives in ``combinedPhrases[*].text`` (falling back to ``phrases[*].text``). Everything the
API can answer is mapped to the provider envelope instead of raising.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Optional

from .config import (
    OneshotConfig,
    PROVIDER_NAME,
    TOKEN_SCOPE,
    resolve_api_key,
    split_phrases,
)

logger = logging.getLogger(__name__)

_RETRY_STATUS = frozenset({408, 429, 500, 502, 503, 504})


def _envelope_ok(text: str) -> dict[str, Any]:
    return {"success": True, "transcript": text, "provider": PROVIDER_NAME}


def _envelope_error(message: str) -> dict[str, Any]:
    return {"success": False, "transcript": "", "provider": PROVIDER_NAME, "error": message}


def build_definition(cfg: OneshotConfig, *, prompt: str = "") -> dict[str, Any]:
    """The ``definition`` JSON body for one file request."""
    enhanced: dict[str, Any] = {"enabled": True, "model": cfg.model}
    if cfg.style:
        enhanced["modelOptions"] = {"transcribeStyle": cfg.style}
    definition: dict[str, Any] = {"enhancedMode": enhanced}
    if cfg.language_code:
        definition["locales"] = [cfg.language_code]
    phrases = split_phrases(prompt)
    if phrases:
        definition["phraseList"] = {"phrases": phrases}
    return definition


def auth_headers(cfg: OneshotConfig, *, streaming: bool = False) -> dict[str, str]:
    """Authentication headers for one transport: a key when configured, else an Entra token.

    The two transports do not agree on the header name. The file API takes
    ``Ocp-Apim-Subscription-Key``; the realtime websocket takes ``api-key`` and rejects the file
    header with 401 even when the key itself is valid.
    """
    key = resolve_api_key(cfg)
    if key:
        return {"api-key": key} if streaming else {"Ocp-Apim-Subscription-Key": key}
    try:
        from agent.azure_identity_adapter import build_token_provider
    except Exception as exc:  # pragma: no cover - core is always present in Hermes
        raise RuntimeError(
            "no credential found: set stt.azure-speech.api_key, or point "
            "stt.azure-speech.api_key_env at the variable holding the resource key, "
            "or install azure-identity for Entra ID") from exc
    provider = build_token_provider(TOKEN_SCOPE)
    return {"Authorization": f"Bearer {provider()}"}


def _error_from_response(status: int, body: str) -> str:
    detail = ""
    try:
        payload = json.loads(body)
    except Exception:
        payload = None
    if isinstance(payload, dict):
        message = payload.get("message") or (payload.get("error") or {}).get("message")
        code = payload.get("code") or (payload.get("error") or {}).get("code")
        if message:
            detail = f"{code}: {message}" if code else str(message)
    if not detail:
        detail = (body or "").strip()[:300]
    if status in (401, 403):
        return (f"not authorised for this Speech resource (HTTP {status}): {detail}. "
                "For Entra auth the caller needs a Speech data-plane role on the resource.")
    if status == 404:
        return f"endpoint or model not found (HTTP 404): {detail}"
    if status == 429:
        return f"rate limited (HTTP 429): {detail}"
    return f"Azure rejected the request (HTTP {status}): {detail}"


def transcribe_file(
    cfg: OneshotConfig,
    file_path: str,
    *,
    prompt: str = "",
) -> dict[str, Any]:
    """POST one file and return the provider envelope. Never raises."""
    try:
        import requests
    except Exception as exc:  # pragma: no cover
        return _envelope_error(f"the 'requests' package is required for Azure transcription: {exc}")

    try:
        definition = json.dumps(build_definition(cfg, prompt=prompt), ensure_ascii=False)
    except Exception as exc:
        return _envelope_error(f"could not build the request definition: {exc}")

    attempts = max(1, cfg.max_retries + 1)
    last_error = ""
    for attempt in range(attempts):
        try:
            headers = auth_headers(cfg)
        except Exception as exc:
            return _envelope_error(f"authentication failed: {exc}")
        path = Path(file_path)
        try:
            with path.open("rb") as handle:
                response = requests.post(
                    cfg.url,
                    headers=headers,
                    files={"audio": (path.name, handle, "application/octet-stream")},
                    data={"definition": definition},
                    timeout=cfg.timeout,
                )
        except requests.RequestException as exc:
            last_error = f"network error talking to Azure: {exc}"
            if attempt + 1 < attempts:
                time.sleep(1.0 + attempt)
                continue
            return _envelope_error(last_error)

        if response.status_code in _RETRY_STATUS and attempt + 1 < attempts:
            logger.warning("azure-speech: HTTP %s, retrying (%s/%s)",
                           response.status_code, attempt + 1, attempts - 1)
            time.sleep(1.0 + attempt)
            continue

        if response.status_code != 200:
            return _envelope_error(_error_from_response(response.status_code, response.text))

        try:
            payload = response.json()
        except Exception:
            return _envelope_error(f"Azure returned a non-JSON body: {response.text[:200]!r}")

        if isinstance(payload, dict) and (payload.get("code") or payload.get("error")):
            return _envelope_error(_error_from_response(200, response.text))

        text = _extract_text(payload)
        if not text:
            return _envelope_error("Azure returned an empty transcript")
        logger.info("azure-speech: transcribed %s (%d chars, model=%s)",
                    path.name, len(text), cfg.model)
        return _envelope_ok(text)

    return _envelope_error(last_error or "transcription failed")


def _extract_text(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    combined = payload.get("combinedPhrases") or []
    if isinstance(combined, list) and combined:
        joined = "".join(str((c or {}).get("text") or "") for c in combined).strip()
        if joined:
            return joined
    phrases = payload.get("phrases") or []
    if isinstance(phrases, list):
        return "".join(str((p or {}).get("text") or "") for p in phrases).strip()
    return ""


def probe(cfg: OneshotConfig, file_path: str) -> str:
    """Human-readable one-line result for ``hermes azure-speech check --probe``."""
    result = transcribe_file(cfg, file_path)
    if result.get("success"):
        return f"ok: {result['transcript'][:120]!r}"
    return f"failed: {result.get('error')}"


def model_choices() -> list[dict[str, str]]:
    return [{"id": "MAI-Transcribe-2", "display": "MAI-Transcribe-2 (default)"},
            {"id": "MAI-Transcribe-1.5", "display": "MAI-Transcribe-1.5"}]
