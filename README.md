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

One section per transport. Each transport keeps its own resource, model, language and
credential, because a resource key opens only its own resource and the streaming model is not
offered in every region.

```yaml
stt:
  provider: azure-speech
  streaming: true                      # opt in to the live path (voice mode)
  prompt: ""                           # vocabulary hints -> phraseList (oneshot path only)
  azure-speech:
    oneshot:                           # audio that has already been recorded
      base_url: https://<resource>.cognitiveservices.azure.com
      model: MAI-Transcribe-2          # or MAI-Transcribe-1.5
      language: "auto"                 # "" -> the server decides; set "ja" for Japanese
      style: clean                     # clean | verbatim
      timeout: 60
      max_retries: 1
      api_key: ""                      # literal key (discouraged)
      api_key_env: AZURE_SPEECH_API_KEY
    realtime:                          # the microphone
      base_url: https://<resource>.cognitiveservices.azure.com
      model: MAI-Transcribe-2-Streaming   # the deployment name, used verbatim
      language: "auto"
      api_key_env: AZURE_SPEECH_API_KEY_REALTIME
```

At least one section must be present. `realtime` alone gives live dictation only; `oneshot` alone
means no live path; both together give live dictation plus a file fallback.

| key | required | default |
| --- | --- | --- |
| `stt.azure-speech.oneshot.base_url` | yes, if `oneshot` is present | — (a bare resource name is accepted) |
| `stt.azure-speech.oneshot.model` | no | `MAI-Transcribe-2` |
| `stt.azure-speech.oneshot.language` | no | `""` (the server decides) |
| `stt.azure-speech.oneshot.style` | no | `clean` |
| `stt.azure-speech.oneshot.timeout` | no | `60` |
| `stt.azure-speech.oneshot.max_retries` | no | `1` |
| `stt.azure-speech.oneshot.api_key_env` | no | `AZURE_SPEECH_API_KEY` |
| `stt.azure-speech.realtime.base_url` | yes, if `realtime` is present | — |
| `stt.azure-speech.realtime.model` | no | `MAI-Transcribe-2-Streaming` |
| `stt.azure-speech.realtime.language` | no | `""` (the server decides) |
| `stt.azure-speech.realtime.api_key_env` | no | `AZURE_SPEECH_API_KEY_REALTIME` |

`style` selects how the service cleans the text (`clean` / `verbatim`). The API version is a
code constant, not a setting: the request body this plugin builds is the one that version accepts,
so pointing it elsewhere could only break the wire shape. A configured `api_version` is ignored
with a warning.

Derived, never configured:

```
oneshot endpoint   <oneshot.base_url>/speechtotext/transcriptions:transcribe?api-version=…
realtime endpoint  <realtime.base_url host, .cognitiveservices.azure.com -> .services.ai.azure.com>
                   /mai/v1/realtime?intent=transcription
```

Both transports read `base_url` the same way: a bare resource name expands to
`https://<name>.cognitiveservices.azure.com`, and the realtime host is rewritten to
`.services.ai.azure.com` (either host form is accepted there).

Environment: **credentials only.**

Non-secret settings are read from `config.yaml` alone — the provider never consults the
environment for a URL, a model name or a version. The only environment reads are credentials, and
even their variable names are config values:

```yaml
stt:
  azure-speech:
    oneshot:
      api_key_env: ACME_SPEECH_KEY                  # default AZURE_SPEECH_API_KEY
    realtime:
      api_key_env: ACME_SPEECH_KEY_STREAMING        # default AZURE_SPEECH_API_KEY_REALTIME
```

Resolution order for the credential, per transport: literal `api_key` -> the variable named by
that section's `api_key_env` (read through Hermes' profile-scoped `get_env_value`) -> Microsoft
Entra ID. `api_key: "${MY_VAR}"` also works, since Hermes expands `${...}` references in config
values.

Language: `""` means "let the server decide", and the server's default is English. Set `ja` (or
`ja-JP`; the region subtag is dropped) for Japanese audio — otherwise the model maps Japanese
speech onto English words.

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
- A live handshake that fails (for example a derived host with no deployment behind it) suppresses
  the live path for ten minutes, so a misconfigured `realtime.base_url` cannot cost seconds on every
  utterance; transcription falls back to the file transport in the meantime.

## Tests

```bash
python -m unittest discover -s tests
```

The suite runs offline against a local HTTP stand-in; no Azure calls, no credentials.

## Licence

MIT for this plugin. MAI-Transcribe is a Microsoft preview service under its own terms and
pricing; this project is not affiliated with Microsoft. See `NOTICE`.
