# hermes-plugin-azure-speech-stt

Azure AI Speech as a speech-to-text provider for [Hermes Agent](https://github.com/NousResearch/hermes-agent).
Registers the provider name `azure-speech`.

Two transports, one provider:

- **File** — Fast Transcription (`/speechtotext/transcriptions:transcribe`) with the
  MAI-Transcribe models. Keyword biasing, word timestamps and diarization live here.
- **Live** — Realtime WebSocket (`/mai/v1/realtime`) with `MAI-Transcribe-2-Streaming`, for
  partial text while the user is still speaking. Used by Hermes' voice mode; every other
  surface falls back to the file path.

No SDK. One multipart POST and one WebSocket. Auth is a resource key or a Microsoft Entra
token (`azure-identity`).

## Install

```bash
hermes plugins install 0ppaikoa/hermes-plugin-azure-speech-stt --enable
```

Restart the gateway after installing.

## Configure

One location, one model. Everything else is derived.

```yaml
stt:
  provider: azure-speech
  streaming: true                      # opt in to the live path (voice mode)
  prompt: ""                           # vocabulary hints -> phraseList (file path only)
  azure-speech:
    base_url: https://<resource>.cognitiveservices.azure.com
    model: MAI-Transcribe-2            # or MAI-Transcribe-1.5
    language: ""                       # empty -> automatic detection
    style: clean                       # clean | verbatim
    timeout: 60
    max_retries: 1
    api_key: ""                        # empty -> AZURE_SPEECH_API_KEY -> Entra ID
```

| key | required | default |
| --- | --- | --- |
| `stt.azure-speech.base_url` | yes | — (accepts a bare resource name too) |
| `stt.azure-speech.model` | no | `MAI-Transcribe-2` |
| `stt.azure-speech.language` | no | `""` (automatic) |
| `stt.azure-speech.style` | no | `clean` |
| `stt.azure-speech.timeout` | no | `60` |
| `stt.azure-speech.max_retries` | no | `1` |
| `stt.azure-speech.api_key` | no | `""` |

Derived, never configured:

```
file endpoint    <base_url>/speechtotext/transcriptions:transcribe?api-version=2025-10-15
live endpoint    <base_url host, .cognitiveservices.azure.com -> .services.ai.azure.com>
                 /mai/v1/realtime?intent=transcription
live deployment  lower(model) + "-streaming"
```

Environment overrides (config.yaml wins over these):

| variable | purpose |
| --- | --- |
| `AZURE_SPEECH_BASE_URL` | base URL when the config section omits it |
| `AZURE_SPEECH_API_KEY` | resource key |
| `HERMES_AZURE_SPEECH_STREAM_BASE` | live origin when the streaming deployment sits on another resource |
| `HERMES_AZURE_SPEECH_STREAM_DEPLOYMENT` | live deployment name when it differs from `<model>-streaming` |
| `HERMES_AZURE_SPEECH_API_VERSION` | pin the API version |

## Capability matrix

```
                      file    live
phrase biasing        yes     no
word timestamps       yes     no
diarization           yes     no
partial text          no      yes
final after speech    seconds ~0.2 s
```

`stt.prompt` is ignored on the live path — the service offers no biasing there. The plugin
logs that once per utterance instead of dropping it silently.

## Requirements

- `requests` and `websockets` (both ship with Hermes).
- **ffmpeg** for any input that is not WAV, MP3 or FLAC — the desktop app records m4a.
  Without ffmpeg the provider returns a clear error rather than an empty transcript.
- For Entra ID auth the caller needs a Speech data-plane role on the resource. A resource
  key avoids that entirely.

## Notes from building it

- The live transport has no server-side VAD: the client commits at the end of speech.
  Sending audio much faster than real time overflows the service buffer and **silently
  truncates** the transcript, so the sender never runs more than 2 seconds ahead.
- `session.update` must complete before the first append; settings cannot change afterwards.
- Sessions are capped at one hour by the service.

## Tests

```bash
python -m unittest discover -s tests
```

The suite runs offline against a local HTTP stand-in; no Azure calls, no credentials.

## Licence

MIT for this plugin. MAI-Transcribe is a Microsoft preview service under its own terms and
pricing; this project is not affiliated with Microsoft. See `NOTICE`.
