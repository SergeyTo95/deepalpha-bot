# Meeting records in the unified chat

This module adds explicit processing of an uploaded recording, a TXT/MD/SRT/VTT
transcript, or pasted text following `Расшифровка:` / `Transcript:`. It uses the
selected Flash, Quantum or PRO model through the existing authenticated chat
quota path. No new service, database, model key or paid fallback is introduced.

## Workflow

1. Attach one file and ask `Подготовь протокол встречи: решения и поручения`.
   Audio attachments enter this flow directly. The attachment bar also offers
   `Протокол встречи`. Meeting intent does not intercept calendar scheduling or
   ordinary medical document interpretation.
2. Audio is transcribed only when the existing free-account transcription flag
   and key are already enabled. Text transcripts work independently of audio
   configuration. Audio accepts MP3/M4A/WAV/OGG/WebM/FLAC up to 15 MiB and twenty
   minutes. Oversized/unsupported recordings require splitting or a transcript.
   There is no silent truncation, video import, speaker identification or joining
   Zoom/Meet/Teams calls in this stage.
3. The complete supplied transcript (up to 30,000 characters) is divided into
   chunks of at most 6,000 characters. Each chunk is extracted with web search
   disabled. Strict JSON accepts discussion highlights, decisions, actions and
   open questions, with literal quotes. Fabricated quotes are excluded and the
   rejection count is shown. A failed/truncated inference prevents a complete
   protocol from being presented. Cross-chunk contradictions still require human
   review; the report identifies this limitation.
4. Each item is a draft interpretation. Quotation presence does not establish
   entailment, correct attribution or transcript accuracy. Names and due dates
   require review. Structured owner fields must appear in the item's evidence.
   Structured deadlines are accepted only when the exact valid ISO date appears
   in the evidence. Relative dates are not resolved from the processing date.
5. `Сохранить протокол` saves the reviewed draft to the existing encrypted Notes
   with a stable meeting ID. Source quotes are included; the full transcript stays
   in encrypted device chat history and can be downloaded separately. An existing
   note is not overwritten on a repeat save. Generic response export still offers
   Word/PDF/Markdown and other existing document formats.
6. Each action's `Сохранить в CRM` opens an explicit searchable contact chooser.
   Namesakes are shown with company/email, no first match is selected silently.
   The user reviews the actual server preview before mutation. Only a CRM task
   is created; it does not create a reminder, send mail, close an incoming message
   or record an actual communication. Stable operation IDs and persisted attempt
   receipts prevent blindly repeating a write after a lost response.
7. `Напомнить` opens the existing Pulse composer with a draft. Missing dates/times
   remain for the user to specify. It does not create a reminder on its own.
   `Черновик письма` produces visibly unsent text; recipients and sends are not
   inferred. Existing calendar actions remain available through their usual flow.

## Server and privacy boundaries

`POST /web-api/v1/meetings/plan` reuses chat session, model authorization and quota
checks. Flash extraction output is capped at 1,536 tokens. Extraction receives
only bounded source text, not connector credentials or unrelated account data.

`POST /web-api/v1/meetings/transcribe` accepts raw audio bytes with origin and
session checks, one concurrent request per gateway and two provider attempts per
owner per hour. The route streams up to 15 MiB without enlarging global JSON
limits. Invalid uploads do not consume the provider-attempt budget. ffprobe checks
duration/audio streams using a forced demuxer and local-file protocol only, with a
ten-second timeout and cancellation cleanup. The Docker image packages ffmpeg for
ffprobe. Temporary input directories are removed after the probe.

The original bytes are sent to the existing configured transcription provider,
without redirects, URLs or paid fallback. The original audio is not stored by
this feature. Audio transcription has a ninety-second provider deadline; the
whole route has a 110-second deadline. Oversized transcripts fail instead of
returning a silently cut result. This is a synchronous request, not a durable
background job; disconnects may require attaching the recording again.

Source transcript and structured protocol remain in the account's encrypted
device chat cache, which is subject to the existing device cache deletion rules.
Notes and CRM records are saved only through explicit reviewed actions, using
their existing encryption and account isolation. Provider retention is governed
by the configured account/provider policy. No source audio or transcript content
is written to application logs. Account change/abort guards stop later actions.

## Verification and rollout

Tests cover complete source coverage, invalid citations/dates/owners, the selected
model, failed/aborted extraction, contact review, uncertain writes and duplicate
attempt receipts; HTTP tests cover source/origin/session, size, duration, rate
limits, real WAV probing, cancellation and chat integration. Live inference and
real speech acceptance still need a connected account; fixtures do not establish
ASR quality or speaker recognition. This branch is stacked on `velia/crm` and
does not itself apply a Railway deployment.
