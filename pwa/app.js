/**
 * app.js — Orchestration principale PWA MeetNote
 */
import { AudioRecorder } from './recorder.js';
import { transcribe } from './whisper.js';
import { pushToNotion } from './notion.js';

// ─── DOM refs ───────────────────────────────────────────────────────────────
const btnRecord       = document.getElementById('btn-record');
const iconMic         = document.getElementById('icon-mic');
const iconStop        = document.getElementById('icon-stop');
const statusEl        = document.getElementById('status');
const settingsEl      = document.getElementById('settings');
const transcriptSec   = document.getElementById('transcript-section');
const transcriptArea  = document.getElementById('transcript');
const participantsEl  = document.getElementById('participants');
const btnPush         = document.getElementById('btn-push');
const btnDiscard      = document.getElementById('btn-discard');

// Settings inputs
const notionTokenEl   = document.getElementById('notion-token');
const notionDbEl      = document.getElementById('notion-db');
const whisperModelEl  = document.getElementById('whisper-model');
const whisperLangEl   = document.getElementById('whisper-lang');

// ─── State ──────────────────────────────────────────────────────────────────
const recorder = new AudioRecorder();
let isRecording = false;

// ─── Persist settings ───────────────────────────────────────────────────────
function loadSettings() {
  notionTokenEl.value  = localStorage.getItem('mn_token') || '';
  notionDbEl.value     = localStorage.getItem('mn_db') || '';
  whisperModelEl.value = localStorage.getItem('mn_model') || 'Xenova/whisper-base';
  whisperLangEl.value  = localStorage.getItem('mn_lang') || 'fr';
}

function saveSettings() {
  localStorage.setItem('mn_token', notionTokenEl.value.trim());
  localStorage.setItem('mn_db', notionDbEl.value.trim());
  localStorage.setItem('mn_model', whisperModelEl.value);
  localStorage.setItem('mn_lang', whisperLangEl.value);
}

[notionTokenEl, notionDbEl, whisperModelEl, whisperLangEl].forEach((el) =>
  el.addEventListener('change', saveSettings)
);

// ─── Record toggle ──────────────────────────────────────────────────────────
btnRecord.addEventListener('click', async () => {
  if (!isRecording) {
    await startRecording();
  } else {
    await stopAndTranscribe();
  }
});

async function startRecording() {
  try {
    await recorder.start();
    isRecording = true;
    btnRecord.classList.add('recording');
    iconMic.style.display = 'none';
    iconStop.style.display = '';
    setStatus('Enregistrement en cours…');
    transcriptSec.classList.remove('visible');
    settingsEl.open = false;
  } catch (err) {
    setStatus('Erreur micro : ' + err.message);
  }
}

async function stopAndTranscribe() {
  isRecording = false;
  btnRecord.classList.remove('recording');
  iconMic.style.display = '';
  iconStop.style.display = 'none';
  setStatus('Arrêt de l\'enregistrement…');

  let blob;
  try {
    blob = await recorder.stop();
  } catch (err) {
    setStatus('Erreur arrêt : ' + err.message);
    return;
  }

  setStatus('Transcription en cours…');
  try {
    const text = await transcribe(blob, {
      model: whisperModelEl.value,
      language: whisperLangEl.value,
      onProgress: setStatus,
    });
    transcriptArea.value = text;
    transcriptSec.classList.add('visible');
    setStatus('Transcript prêt — vérifiez et envoyez.');
  } catch (err) {
    setStatus('Erreur transcription : ' + err.message);
  }
}

// ─── Push to Notion ─────────────────────────────────────────────────────────
btnPush.addEventListener('click', async () => {
  const token = notionTokenEl.value.trim();
  const dbId  = notionDbEl.value.trim();

  if (!token || !dbId) {
    setStatus('Renseignez le token et le database ID Notion.');
    settingsEl.open = true;
    return;
  }

  setStatus('Envoi vers Notion…');
  btnPush.disabled = true;

  try {
    await pushToNotion({
      token,
      databaseId: dbId,
      transcript: transcriptArea.value,
      participants: participantsEl.value.trim(),
      source: 'Mobile',
    });
    setStatus('✓ Page créée dans Notion !');
    transcriptSec.classList.remove('visible');
    transcriptArea.value = '';
    participantsEl.value = '';
  } catch (err) {
    setStatus('Erreur Notion : ' + err.message);
  } finally {
    btnPush.disabled = false;
  }
});

// ─── Discard ─────────────────────────────────────────────────────────────────
btnDiscard.addEventListener('click', () => {
  transcriptSec.classList.remove('visible');
  transcriptArea.value = '';
  participantsEl.value = '';
  setStatus('Annulé.');
});

// ─── Helpers ─────────────────────────────────────────────────────────────────
function setStatus(msg) {
  statusEl.textContent = msg;
}

// ─── Init ────────────────────────────────────────────────────────────────────
loadSettings();
setStatus('Appuyer pour démarrer');
