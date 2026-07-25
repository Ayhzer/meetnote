# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the app

```bat
cd pc
start.bat          # Launches meetnote-tray.py via py -3.12 (no console window)
stop.bat           # Kills the process
```

To run directly (with console output for debugging):
```bat
py -3.12 pc\meetnote-tray.py
```

## Building the distributable

```bat
cd pc
py -3.12 build_spec.py    # Generates meetnote.spec, runs PyInstaller, signs EXE
```

Output lands in `pc\dist\MeetNote\`. The build script also copies Whisper models from the HF cache (`~/.cache/huggingface/hub/`) into the bundle. The `build_spec_slim.py` variant omits some models for a smaller build.

## Architecture

Two independent frontends share a Notion database as the backend:

**PC app (`pc/`)** — Windows tray application (Python 3.12, tkinter):
- `meetnote-tray.py` — monolithic entry point (~4400 lines). Contains: UI, recording engine, job queue, transcription pipeline, history panel. All UI state is module-level globals.
- `config.py` — **gitignored**, must be created manually before first run (see README). Contains audio/path/Whisper/Notion settings.
- `user_config.py` — runtime settings persisted to `%APPDATA%/MeetNote/settings.json` (Notion token, database ID, EWS credentials, exclusion lists). Loaded at startup and overrides `config.py`.
- `history.py` — JSON persistence of job history at `%APPDATA%/MeetNote/history.json` (100 entries max).
- `notion_push.py` — Notion REST API (v2026-03-11): creates pages, uploads audio via 2-step file_uploads API, handles batching (100-block limit).
- `outlook_cal.py` — Calendar integration: tries EWS via `exchangelib` first, falls back to COM/MAPI via `win32com`. Filters OOO, all-day, and >12h events.
- `ews_cal.py` — EWS-specific calendar access (Nouvel Outlook / Exchange on-premise via NTLM).
- `calendar_digest.py` — Builds a 15-day agenda from Outlook and pushes it to Notion once per day.
- `teams_roster.py` — Reads Teams log files to extract participant names. `TeamsRosterWatcher` polls every 15s during recording.
- `outlook_mail.py` — Outlook mail reading (for attachments feature).
- `whisper_local.py` — Standalone Whisper helper (used outside the tray context).

**PWA (`pwa/`)** — Installable web app (no build step):
- `app.js` — Main application logic
- `recorder.js` — MediaRecorder-based audio capture
- `whisper.js` / `whisper_local.py` — Whisper WASM transcription via Transformers.js
- `notion.js` — Notion push from browser
- `sw.js` — Service worker for offline/PWA install

**Shared:**
- `shared/prompt_agent_claude.md` — Claude Desktop prompt for automated Notion processing

## Job pipeline

Jobs flow through these states: `queued → running → done / error` for each of the two independent steps: `status_transcript` and `status_notion`. A single daemon worker thread (`_worker_loop`) processes the queue sequentially to avoid CPU overload. Jobs can be re-queued from the history panel for any step.

Pipeline steps in order:
1. Archive audio: WAV → Opus 24 kbps via ffmpeg (fallback: copy as WAV) → `Documents/MeetNote/audio/`
2. Transcribe: faster-whisper (CPU, int8). Splits audio at 10-min segments. Applies MFCC+DBSCAN diarization (no external deps) with H/F gender detection via pitch F0.
3. Save transcript: `Documents/MeetNote/transcripts/transcript_YYYYMMDD_HHMMSS.txt`
4. Push to Notion (or save to file if output_mode = "fichier")

## Audio capture

Three modes (WASAPI loopback via `pyaudiowpatch`, fallback to `soundcard`):
- Mic only: `sounddevice` callback
- Loopback: WASAPI loopback thread
- Mixed: two parallel reader threads, merged 60% mic / 40% loopback every 100ms

Audio processing: `scipy.signal.resample_poly` for resampling, `noisereduce` for denoising (stationary, prop_decrease=0.75).

## Configuration files (not in git)

| File | Location | Purpose |
|------|----------|---------|
| `pc/config.py` | repo root | Audio/path/Whisper defaults |
| `settings.json` | `%APPDATA%/MeetNote/` | User settings (token, DB ID, EWS) |
| `history.json` | `%APPDATA%/MeetNote/` | Job history |

EWS credentials (password) are stored in the Windows Credential Manager via `keyring`.

## Key dependencies

- `faster-whisper` — local transcription (models downloaded to `~/.cache/huggingface/hub/`)
- `pyaudiowpatch` — WASAPI loopback (not in requirements.txt, installed separately)
- `pycaw` — Windows master volume control
- `exchangelib` — EWS calendar access
- `pywin32` — COM/MAPI Outlook and Win32 APIs
- `ffmpeg` — Audio compression and format conversion (must be in PATH or bundled)

`pyaudiowpatch` is a runtime dependency for loopback recording but is not listed in `requirements.txt` — install manually if needed.

## Notion database schema

The target database needs these exact property names: `Titre` (title), `Date` (date), `Participants` (select), `Source` (select: PC/Mobile/Agenda), `Statut` (select: À traiter/Traité), `Type` (select), `Durée (min)` (number), `Modèle Whisper` (select).
