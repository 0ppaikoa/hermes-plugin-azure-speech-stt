"""Live transcription over the Azure Realtime API (MAI-Transcribe-2-Streaming).

Sync facade over one websocket: the plugin contract is ``push_audio`` / ``partial_transcript`` /
``finalize``, all called from the caller's thread, while a worker thread owns the event loop.

Wire facts this encodes (verified against a real session on a Foundry resource):
  * audio is raw PCM16 LE mono 16 kHz, base64, sent with ``input_audio_buffer.append``;
  * there is no server-side VAD here — the client commits with ``input_audio_buffer.commit``;
  * ``.delta`` carries newly finalised text, ``.intermediate`` replaces the provisional suffix,
    ``.completed`` carries the whole transcript since the last commit;
  * sending far faster than real time overflowed the server buffer and silently truncated the
    transcript, so this session never sends more than ``max_ahead_seconds`` ahead of arrival.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import threading
import time
from typing import Any, Optional

from .config import AzureSpeechConfig, PROVIDER_NAME, TOKEN_SCOPE, resolve_api_key

logger = logging.getLogger(__name__)

READY_TIMEOUT = 25.0
FINAL_TIMEOUT = 30.0
MAX_AHEAD_SECONDS = 2.0
BYTES_PER_SECOND = 32000  # 16 kHz mono s16le


class LiveSessionError(RuntimeError):
    """The live session could not be established; callers fall back to the file path."""


def _envelope(text: str) -> dict[str, Any]:
    return {"success": True, "transcript": text, "provider": PROVIDER_NAME}


def _error(message: str) -> dict[str, Any]:
    return {"success": False, "transcript": "", "provider": PROVIDER_NAME, "error": message}


class AzureSpeechLiveSession:
    """One utterance: push PCM, read partials, finalize into the provider envelope."""

    def __init__(self, cfg: AzureSpeechConfig, *, language: str = "", prompt: str = "") -> None:
        if not cfg.live_url:
            raise LiveSessionError("no live endpoint could be derived from base_url")
        self._cfg = cfg
        # Resolve the resource key while the caller's scope is still active. The connection is
        # established later on this object's own thread, where a fail-closed multi-profile host
        # (Hermes' web surface) refuses an unscoped secret read and the session would silently
        # fall back to Entra ID. An empty value keeps the lazy Entra path below.
        self._api_key = resolve_api_key(cfg, streaming=True)
        self._language = language or cfg.language_code
        self._prompt = prompt
        self._confirmed: list[str] = []
        self._suffix = ""
        self._state_lock = threading.Lock()
        self._ready = threading.Event()
        self._done = threading.Event()
        self._commit_sent = threading.Event()
        self._error_text = ""
        self._final = ""
        self._closed = False
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._queue: Optional[asyncio.Queue] = None
        self._ws: Any = None
        self._sent_bytes = 0
        self._pushed_bytes = 0
        self._started_at = 0.0
        self._thread = threading.Thread(target=self._thread_main, daemon=True,
                                        name="azure-speech-live")
        self._thread.start()
        if not self._ready.wait(READY_TIMEOUT):
            raise LiveSessionError(self._error_text or "live session did not become ready")

    # -- public API ------------------------------------------------------
    def push_audio(self, chunk: bytes) -> None:
        if self._closed or self._loop is None or self._queue is None or not chunk:
            return
        try:
            asyncio.run_coroutine_threadsafe(self._queue.put(bytes(chunk)), self._loop)
            self._pushed_bytes += len(chunk)
        except RuntimeError:
            pass

    def partial_transcript(self) -> str:
        with self._state_lock:
            return "".join(self._confirmed) + self._suffix

    def finalize(self) -> dict[str, Any]:
        try:
            if self._error_text:
                return _error(self._error_text)
            if self._loop is not None and self._queue is not None:
                # The sentinel is queued BEHIND the audio, so the sender drains every chunk
                # before it commits. Committing early truncates the transcript (observed).
                asyncio.run_coroutine_threadsafe(self._queue.put(None), self._loop)
                remaining = max(0, self._pushed_bytes - self._sent_bytes) / BYTES_PER_SECOND
                self._commit_sent.wait(FINAL_TIMEOUT + remaining)
            if not self._done.wait(FINAL_TIMEOUT):
                return _error("live session did not return a final transcript")
            if self._final:
                return _envelope(self._final)
            with self._state_lock:
                # Some sessions only ever produce provisional text: a language mismatch leaves
                # .intermediate events with no .delta/.completed, so _confirmed stays empty while
                # the user has already seen the words on screen. Return those words instead of an
                # error that forces a caller-side fallback the resource may not support.
                fallback = ("".join(self._confirmed) + self._suffix).strip()
            return _envelope(fallback) if fallback else _error(
                self._error_text or "live session produced no transcript")
        finally:
            self._shutdown()

    # -- worker ----------------------------------------------------------
    def _thread_main(self) -> None:
        try:
            asyncio.run(self._amain())
        except Exception as exc:  # noqa: BLE001 - surfaced through the envelope
            self._error_text = self._error_text or f"{type(exc).__name__}: {exc}"
            self._ready.set()
            self._done.set()
            logger.warning("azure-speech live session failed: %s", exc)

    async def _amain(self) -> None:
        from websockets.asyncio.client import connect

        headers = await asyncio.get_running_loop().run_in_executor(None, self._auth_headers)
        async with connect(self._cfg.live_url, additional_headers=headers,
                           max_size=None, ping_interval=20, open_timeout=15) as ws:
            self._ws = ws
            await self._await_event(ws, "session.created")
            await ws.send(json.dumps({
                "type": "session.update",
                "event_id": "configure-1",
                "session": {
                    "type": "transcription",
                    "audio": {"input": {
                        "format": {"type": "audio/pcm", "rate": 16000},
                        "transcription": {"model": self._cfg.live_deployment,
                                          **({"language": self._language} if self._language else {})},
                        "turn_detection": None,
                    }},
                },
            }, ensure_ascii=False))
            await self._await_event(ws, "session.updated")

            loop = asyncio.get_running_loop()
            self._loop = loop
            self._queue = asyncio.Queue()
            self._started_at = time.monotonic()
            self._ready.set()

            sender = loop.create_task(self._sender(ws))
            try:
                await self._receiver(ws)
            finally:
                sender.cancel()
                self._closed = True

    def _auth_headers(self) -> dict[str, str]:
        from .client import auth_headers

        if self._api_key:
            return {"api-key": self._api_key}
        return auth_headers(self._cfg, streaming=True)

    async def _await_event(self, ws: Any, wanted: str) -> dict[str, Any]:
        deadline = time.monotonic() + READY_TIMEOUT
        while time.monotonic() < deadline:
            raw = await asyncio.wait_for(ws.recv(), timeout=READY_TIMEOUT)
            event = json.loads(raw)
            kind = event.get("type")
            if kind == wanted:
                return event
            if kind == "error":
                raise LiveSessionError(json.dumps(event.get("error") or event, ensure_ascii=False)[:300])
        raise LiveSessionError(f"timed out waiting for {wanted}")

    async def _sender(self, ws: Any) -> None:
        assert self._queue is not None
        try:
            while True:
                chunk = await self._queue.get()
                if chunk is None:
                    # Every queued chunk has been sent; ask the service to finalise.
                    await ws.send(json.dumps({"type": "input_audio_buffer.commit"}))
                    self._commit_sent.set()
                    return
                # Never run more than MAX_AHEAD_SECONDS ahead of the audio clock.
                target = self._started_at + (self._sent_bytes + len(chunk)) / BYTES_PER_SECOND
                delay = target - time.monotonic()
                if delay > MAX_AHEAD_SECONDS:
                    await asyncio.sleep(delay - MAX_AHEAD_SECONDS)
                await ws.send(json.dumps({"type": "input_audio_buffer.append",
                                          "audio": base64.b64encode(chunk).decode("ascii")}))
                self._sent_bytes += len(chunk)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            self._error_text = self._error_text or f"send failed: {exc}"
            self._commit_sent.set()
            self._done.set()

    async def _receiver(self, ws: Any) -> None:
        try:
            while True:
                raw = await ws.recv()
                event = json.loads(raw)
                kind = str(event.get("type") or "")
                if kind.endswith(".delta"):
                    with self._state_lock:
                        self._confirmed.append(str(event.get("delta") or ""))
                        self._suffix = ""
                elif kind.endswith(".intermediate"):
                    with self._state_lock:
                        self._suffix = str(event.get("intermediate") or "")
                elif kind.endswith(".completed"):
                    self._final = str(event.get("transcript") or "")
                    self._done.set()
                    return
                elif kind == "error" or kind.endswith(".failed"):
                    self._error_text = json.dumps(event.get("error") or event,
                                                  ensure_ascii=False)[:300]
                    self._done.set()
                    return
        except Exception as exc:  # noqa: BLE001
            if not self._done.is_set():
                self._error_text = self._error_text or f"receive failed: {exc}"
                self._done.set()

    def _shutdown(self) -> None:
        self._closed = True
        if self._loop is not None and self._queue is not None:
            try:
                asyncio.run_coroutine_threadsafe(self._queue.put(None), self._loop)
            except RuntimeError:
                pass
        if self._ws is not None and self._loop is not None:
            try:
                asyncio.run_coroutine_threadsafe(self._ws.close(), self._loop)
            except RuntimeError:
                pass
