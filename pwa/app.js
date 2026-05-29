/**
 * app.js — Orchestration principale PWA MeetNote (mobile, sans transcription locale)
 * Flow : enregistrement → envoi audio vers Notion → transcription sur PC
 */
import { AudioRecorder } from './recorder.js';
import { pushToNotion }  from './notion.js';

// ─── DOM refs ───────────────────────────────────────────────────────────────
const btnRecord      = document.getElementById('btn-record');
const iconMic        = document.getElementById('icon-mic');
const iconStop       = document.getElementById('icon-stop');
const statusEl       = document.getElementById('status');
const timerEl        = document.getElementById('timer');

// Sections
const actionBtns     = document.getElementById('action-btns');
const btnStopNotion  = document.getElementById('btn-stop-notion');
const btnStopOnly    = document.getElementById('btn-stop-only');
const btnCancel      = document.getElementById('btn-cancel');
const sendSection    = document.getElementById('send-section');
const sendSectionTitle = document.getElementById('send-section-title');
const sendCardTitle  = document.getElementById('send-card-title');
const sendMeta       = document.getElementById('send-meta');

// Import transcript
const tabFile        = document.getElementById('tab-file');
const tabPaste       = document.getElementById('tab-paste');
const panelFile      = document.getElementById('panel-file');
const panelPaste     = document.getElementById('panel-paste');
const importFileInput = document.getElementById('import-file-input');
const btnImportFile  = document.getElementById('btn-import-file');
const importFileName = document.getElementById('import-file-name');
const importPaste    = document.getElementById('import-paste');
const btnImportPaste = document.getElementById('btn-import-paste');

// Meeting info
const selSource      = document.getElementById('sel-source');
const selType        = document.getElementById('sel-type');
const participantsEl = document.getElementById('participants');

// Levels
const barLevel       = document.getElementById('bar-level');

// Send section
const btnPush        = document.getElementById('btn-push');
const btnDownload    = document.getElementById('btn-download');
const btnDiscard     = document.getElementById('btn-discard');

// Journal
const journalText    = document.getElementById('journal-text');

// Settings modal
const btnOpenSettings  = document.getElementById('btn-open-settings');
const modalBackdrop    = document.getElementById('modal-backdrop');
const btnCloseModal    = document.getElementById('btn-close-modal');
const setToken         = document.getElementById('set-token');
const setDb            = document.getElementById('set-db');
const btnSaveNotion    = document.getElementById('btn-save-notion');
const saveStatus       = document.getElementById('save-status');

// ─── State ───────────────────────────────────────────────────────────────────
const recorder    = new AudioRecorder();
let isRecording   = false;
let _recStartTime = null;
let _vuInterval   = null;
let _timerInterval = null;
let _lastAudioBlob = null;
let _lastTranscript = null;   // transcript importé (fichier ou coller)
let _pendingNotion = false;   // stop triggered by "Stop & Notion" button

// ─── Settings ────────────────────────────────────────────────────────────────
function loadSettings() {
  setToken.value = localStorage.getItem('mn_token') || '';
  setDb.value    = localStorage.getItem('mn_db')    || '';
  selType.value  = localStorage.getItem('mn_type')  || '';
}

function saveNotion() {
  localStorage.setItem('mn_token', setToken.value.trim());
  localStorage.setItem('mn_db',    setDb.value.trim());
  saveStatus.textContent = '✓ Enregistré';
  saveStatus.style.color = '#00cc66';
  setTimeout(() => { saveStatus.textContent = ''; }, 2000);
}

selType.addEventListener('change', () => {
  localStorage.setItem('mn_type', selType.value);
});

// ─── Modal ────────────────────────────────────────────────────────────────────
btnOpenSettings.addEventListener('click', () => modalBackdrop.classList.add('open'));
btnCloseModal.addEventListener('click',   () => modalBackdrop.classList.remove('open'));
modalBackdrop.addEventListener('click', (e) => {
  if (e.target === modalBackdrop) modalBackdrop.classList.remove('open');
});
btnSaveNotion.addEventListener('click', saveNotion);

// ─── Import transcript ────────────────────────────────────────────────────────
tabFile.addEventListener('click', () => {
  tabFile.classList.add('active');
  tabPaste.classList.remove('active');
  panelFile.style.display  = '';
  panelPaste.style.display = 'none';
});

tabPaste.addEventListener('click', () => {
  tabPaste.classList.add('active');
  tabFile.classList.remove('active');
  panelPaste.style.display = '';
  panelFile.style.display  = 'none';
  importPaste.focus();
});

btnImportFile.addEventListener('click', () => importFileInput.click());

importFileInput.addEventListener('change', () => {
  const file = importFileInput.files[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = (e) => {
    const text = e.target.result.trim();
    if (!text) { journal('Fichier vide.'); return; }
    _applyImportedTranscript(text, file.name);
  };
  reader.readAsText(file, 'utf-8');
  importFileInput.value = '';
});

btnImportPaste.addEventListener('click', () => {
  const text = importPaste.value.trim();
  if (!text) { journal('Rien à importer : zone de texte vide.'); return; }
  _applyImportedTranscript(text, null);
  importPaste.value = '';
});

function _applyImportedTranscript(text, filename) {
  _lastTranscript = text;
  _lastAudioBlob  = null;

  const wordCount = text.split(/\s+/).filter(Boolean).length;
  const label     = filename ? `📄 ${filename}` : `📋 Texte collé`;
  importFileName.textContent = filename ? `✓ ${filename}` : '';

  sendSectionTitle.textContent = 'TRANSCRIPT IMPORTÉ';
  sendCardTitle.innerHTML = `${label} <span class="badge-transcript">TRANSCRIPT</span>`;
  sendMeta.textContent = `${wordCount} mots · ${formatDate(new Date())}`;
  btnDownload.style.display = 'none';
  sendSection.classList.add('visible');
  setStatus('Transcript prêt — vérifiez les infos de réunion puis envoyez.');
  journal(`Transcript importé : ${wordCount} mots${filename ? ' (' + filename + ')' : ''}.`);
}

// ─── Record button (toggle start/stop+notion) ─────────────────────────────
btnRecord.addEventListener('click', async () => {
  if (!isRecording) {
    await startRecording();
  } else {
    // Main button stops and triggers Notion push
    _pendingNotion = true;
    await stopRecording();
  }
});

btnStopNotion.addEventListener('click', async () => {
  _pendingNotion = true;
  await stopRecording();
});

btnStopOnly.addEventListener('click', async () => {
  _pendingNotion = false;
  await stopRecording();
});

btnCancel.addEventListener('click', async () => {
  await cancelRecording();
});

// ─── Recording ───────────────────────────────────────────────────────────────
async function startRecording() {
  try {
    await recorder.start();
    isRecording   = true;
    _recStartTime = new Date();
    _pendingNotion = false;
    _lastAudioBlob = null;

    // UI → recording state
    btnRecord.classList.add('recording');
    iconMic.style.display  = 'none';
    iconStop.style.display = '';
    actionBtns.style.display = '';
    sendSection.classList.remove('visible');
    setStatus('Enregistrement en cours…');
    journal('');

    // VU-meter
    _vuInterval = setInterval(() => {
      const level = recorder.getLevel ? recorder.getLevel() : 0;
      barLevel.style.width = Math.min(100, Math.round(level * 100)) + '%';
    }, 80);

    // Timer
    _timerInterval = setInterval(() => {
      const elapsed = Math.floor((Date.now() - _recStartTime.getTime()) / 1000);
      const m = String(Math.floor(elapsed / 60)).padStart(2, '0');
      const s = String(elapsed % 60).padStart(2, '0');
      timerEl.textContent = `${m}:${s}`;
    }, 1000);

  } catch (err) {
    journal('Erreur démarrage : ' + err.message);
    setStatus('Erreur micro : ' + err.message);
  }
}

async function stopRecording() {
  isRecording = false;
  clearInterval(_vuInterval);
  clearInterval(_timerInterval);
  barLevel.style.width = '0%';
  setStatus('Arrêt…');

  let blob;
  try {
    blob = await recorder.stop();
    _lastAudioBlob = blob;
  } catch (err) {
    journal('Erreur arrêt : ' + err.message);
    setStatus('Erreur arrêt : ' + err.message);
    resetRecordingUI();
    return;
  }

  resetRecordingUI();

  const durationMin = _recStartTime
    ? (Date.now() - _recStartTime.getTime()) / 60000
    : 0;
  const dStr = durationMin >= 1
    ? `${Math.round(durationMin)} min`
    : `${Math.round(durationMin * 60)} sec`;

  sendSectionTitle.textContent = 'ENREGISTREMENT PRÊT';
  sendCardTitle.innerHTML = 'Audio capturé <span class="badge-pc">À TRANSCRIRE SUR PC</span>';
  sendMeta.textContent = `Durée : ${dStr}  ·  ${formatDate(_recStartTime)}`;
  btnDownload.style.display = '';
  sendSection.classList.add('visible');
  setStatus('Enregistrement terminé.');

  if (_pendingNotion) {
    await pushAudioToNotion();
  }
}

async function cancelRecording() {
  isRecording = false;
  clearInterval(_vuInterval);
  clearInterval(_timerInterval);
  barLevel.style.width = '0%';
  timerEl.textContent = '';
  try { await recorder.stop(); } catch (_) {}
  _lastAudioBlob = null;
  resetRecordingUI();
  setStatus('Annulé.');
}

function resetRecordingUI() {
  btnRecord.classList.remove('recording');
  iconMic.style.display   = '';
  iconStop.style.display  = 'none';
  actionBtns.style.display = 'none';
  timerEl.textContent = '';
}

// ─── Push / Download ──────────────────────────────────────────────────────────
btnPush.addEventListener('click', pushAudioToNotion);

btnDownload.addEventListener('click', () => {
  if (!_lastAudioBlob) return;
  const date  = (_recStartTime || new Date()).toISOString().slice(0, 16).replace('T', '_').replace(':', 'h');
  const ext   = _lastAudioBlob.type.includes('ogg') ? 'ogg'
              : _lastAudioBlob.type.includes('mp4') ? 'mp4' : 'webm';
  const fname = `MeetNote_${date}.${ext}`;
  const url   = URL.createObjectURL(_lastAudioBlob);
  const a     = document.createElement('a');
  a.href      = url;
  a.download  = fname;
  a.click();
  URL.revokeObjectURL(url);
  journal(`Audio téléchargé : ${fname}`);
});

btnDiscard.addEventListener('click', () => {
  clearSendSection();
  setStatus('Annulé.');
});

async function pushAudioToNotion() {
  const token = setToken.value.trim() || localStorage.getItem('mn_token') || '';
  const dbId  = setDb.value.trim()    || localStorage.getItem('mn_db')    || '';

  if (!token || !dbId) {
    setStatus('Renseignez le token et le database ID dans Paramètres.');
    modalBackdrop.classList.add('open');
    return;
  }

  const isTranscriptMode = !_lastAudioBlob && _lastTranscript;

  if (!_lastAudioBlob && !_lastTranscript) {
    setStatus('Aucun contenu à envoyer.');
    return;
  }

  setStatus('Envoi vers Notion…');
  btnPush.disabled = true;

  const durationMin = _recStartTime
    ? (Date.now() - _recStartTime.getTime()) / 60000
    : 0;

  try {
    if (isTranscriptMode) {
      await pushToNotion({
        token,
        databaseId:   dbId,
        transcript:   _lastTranscript,
        participants: participantsEl.value.trim(),
        source:       'Transcript importé',
        meetingType:  selType.value,
        durationMin:  0,
        whisperModel: '',
        recordingDate: new Date(),
        audioBlob:    null,
        status:       'Transcrit',
      });
      setStatus('✓ Transcript envoyé vers Notion !');
      journal('Transcript envoyé vers Notion avec statut "Transcrit".');
    } else {
      await pushToNotion({
        token,
        databaseId:   dbId,
        transcript:   'Audio enregistré sur mobile — à transcrire sur PC',
        participants: participantsEl.value.trim(),
        source:       'Mobile',
        meetingType:  selType.value,
        durationMin:  Math.round(durationMin * 10) / 10,
        whisperModel: '',
        recordingDate: _recStartTime,
        audioBlob:    _lastAudioBlob,
        status:       'À transcrire sur PC',
      });
      setStatus('✓ Audio envoyé vers Notion ! Ouvrez MeetNote PC pour transcrire.');
      journal('Audio envoyé vers Notion avec statut "À transcrire sur PC".');
    }
    clearSendSection();
  } catch (err) {
    journal('Erreur Notion : ' + err.message);
    setStatus('Erreur Notion : ' + err.message);
  } finally {
    btnPush.disabled = false;
  }
}

function clearSendSection() {
  sendSection.classList.remove('visible');
  participantsEl.value = '';
  _lastAudioBlob  = null;
  _lastTranscript = null;
  _recStartTime   = null;
  btnDownload.style.display = '';
  importFileName.textContent = '';
}

// ─── Helpers ──────────────────────────────────────────────────────────────────
function setStatus(msg) {
  statusEl.textContent = msg;
}

function journal(msg) {
  if (!msg) { journalText.textContent = ''; return; }
  const ts = new Date().toLocaleTimeString('fr-FR');
  journalText.textContent += `[${ts}] ${msg}\n`;
  journalText.scrollTop = journalText.scrollHeight;
}

function formatDate(d) {
  if (!d) return '';
  return d.toLocaleDateString('fr-FR', { day: '2-digit', month: '2-digit', year: 'numeric' })
    + ' ' + d.toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' });
}

// ─── Init ──────────────────────────────────────────────────────────────────────
loadSettings();
setStatus('Appuyer pour démarrer');
