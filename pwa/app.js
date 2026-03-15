/**
 * app.js — Orchestration principale PWA MeetNote
 */
import { AudioRecorder } from './recorder.js';
import { transcribe }    from './whisper.js';
import { pushToNotion }  from './notion.js';

// ─── DOM refs ───────────────────────────────────────────────────────────────
const btnRecord      = document.getElementById('btn-record');
const iconMic        = document.getElementById('icon-mic');
const iconStop       = document.getElementById('icon-stop');
const statusEl       = document.getElementById('status');

// Sections
const actionBtns     = document.getElementById('action-btns');
const btnStop        = document.getElementById('btn-stop');
const btnCancel      = document.getElementById('btn-cancel');
const transcriptSec  = document.getElementById('transcript-section');

// Source / Transcription controls
const selSource      = document.getElementById('sel-source');
const sourceHint     = document.getElementById('source-hint');
const selModel       = document.getElementById('sel-model');
const modelHint      = document.getElementById('model-hint');
const selLang        = document.getElementById('sel-lang');
const langHint       = document.getElementById('lang-hint');
const selType        = document.getElementById('sel-type');

// NIVEAUX bars
const barLevel       = document.getElementById('bar-level');
const barProg        = document.getElementById('bar-prog');

// Transcript section
const transcriptArea = document.getElementById('transcript');
const participantsEl = document.getElementById('participants');
const btnPush        = document.getElementById('btn-push');
const btnDiscard     = document.getElementById('btn-discard');

// Journal
const journalText    = document.getElementById('journal-text');

// Bottom bar / modal
const btnOpenSettings  = document.getElementById('btn-open-settings');
const modalBackdrop    = document.getElementById('modal-backdrop');
const btnCloseModal    = document.getElementById('btn-close-modal');
const setToken         = document.getElementById('set-token');
const setDb            = document.getElementById('set-db');
const btnSaveNotion    = document.getElementById('btn-save-notion');
const saveStatus       = document.getElementById('save-status');
const outNotion        = document.getElementById('out-notion');
const outDownload      = document.getElementById('out-download');
const tabBtns          = document.querySelectorAll('.tab-btn');
const tabPanels        = document.querySelectorAll('.tab-panel');

// ─── State ───────────────────────────────────────────────────────────────────
const recorder = new AudioRecorder();
let isRecording      = false;
let _recStartTime    = null;
let _vuInterval      = null;
let _detectedLang    = null;
let _detectedModel   = null;

// ─── Hints ───────────────────────────────────────────────────────────────────
const SOURCE_HINTS = {
  micro:    'Capte uniquement votre voix via le micro. Sur mobile, seul le micro est disponible.',
  loopback: 'Capte tous les sons du PC (non disponible sur mobile).',
  mixte:    'Capte le micro ET les sons du PC simultanément (non disponible sur mobile).',
};

const MODEL_HINTS = {
  'Xenova/whisper-tiny':   'Très rapide — qualité limitée (~75 Mo)',
  'Xenova/whisper-base':   'Rapide — qualité correcte (~145 Mo)',
  'Xenova/whisper-small':  'Bon équilibre qualité/vitesse (~460 Mo)',
  'Xenova/whisper-medium': 'Haute qualité — lent sur mobile (~1.5 Go)',
};

const LANG_HINTS = {
  auto: 'Détection automatique — transcrit en langue source, traduit en anglais si non-français',
  fr:   'Français forcé',
  en:   'Anglais forcé',
  es:   'Espagnol forcé',
  de:   'Allemand forcé',
  it:   'Italien forcé',
  pt:   'Portugais forcé',
  nl:   'Néerlandais forcé',
  ja:   'Japonais forcé',
  zh:   'Chinois forcé',
};

// ─── Settings persistence ────────────────────────────────────────────────────
function loadSettings() {
  // Notion
  setToken.value  = localStorage.getItem('mn_token') || '';
  setDb.value     = localStorage.getItem('mn_db')    || '';
  // Main controls
  selModel.value  = localStorage.getItem('mn_model') || 'Xenova/whisper-base';
  selLang.value   = localStorage.getItem('mn_lang')  || 'auto';
  selType.value   = localStorage.getItem('mn_type')  || '';
  // Output mode
  const out = localStorage.getItem('mn_output') || 'notion';
  if (out === 'download') outDownload.checked = true;
  else                    outNotion.checked   = true;
  // Update hints
  updateModelHint();
  updateLangHint();
}

function saveNotion() {
  localStorage.setItem('mn_token', setToken.value.trim());
  localStorage.setItem('mn_db',    setDb.value.trim());
  saveStatus.textContent = '✓ Enregistré';
  saveStatus.style.color = '#00cc66';
  setTimeout(() => { saveStatus.textContent = ''; }, 2000);
}

function updateModelHint() {
  modelHint.textContent = MODEL_HINTS[selModel.value] || '';
  localStorage.setItem('mn_model', selModel.value);
}

function updateLangHint() {
  langHint.textContent = LANG_HINTS[selLang.value] || '';
  localStorage.setItem('mn_lang', selLang.value);
}

selSource.addEventListener('change', () => {
  sourceHint.textContent = SOURCE_HINTS[selSource.value] || '';
});
selModel.addEventListener('change', updateModelHint);
selLang.addEventListener('change', updateLangHint);
selType.addEventListener('change', () => {
  localStorage.setItem('mn_type', selType.value);
});
[outNotion, outDownload].forEach(r => r.addEventListener('change', () => {
  localStorage.setItem('mn_output', r.value);
  updatePushButton();
}));

function updatePushButton() {
  const out = localStorage.getItem('mn_output') || 'notion';
  btnPush.textContent = out === 'download' ? 'Télécharger .txt' : 'Envoyer vers Notion';
}

// ─── Modal & Tabs ─────────────────────────────────────────────────────────────
btnOpenSettings.addEventListener('click', () => {
  modalBackdrop.classList.add('open');
});
btnCloseModal.addEventListener('click', () => {
  modalBackdrop.classList.remove('open');
});
modalBackdrop.addEventListener('click', (e) => {
  if (e.target === modalBackdrop) modalBackdrop.classList.remove('open');
});

tabBtns.forEach(btn => btn.addEventListener('click', () => {
  tabBtns.forEach(b => b.classList.remove('active'));
  tabPanels.forEach(p => p.classList.remove('active'));
  btn.classList.add('active');
  document.getElementById(btn.dataset.tab).classList.add('active');
}));

btnSaveNotion.addEventListener('click', saveNotion);

// ─── Record button (toggle) ───────────────────────────────────────────────────
btnRecord.addEventListener('click', async () => {
  if (!isRecording) {
    await startRecording();
  } else {
    await stopAndTranscribe();
  }
});

btnStop.addEventListener('click', async () => {
  await stopAndTranscribe();
});

btnCancel.addEventListener('click', async () => {
  await cancelRecording();
});

// ─── Recording ───────────────────────────────────────────────────────────────
async function startRecording() {
  try {
    await recorder.start();
    isRecording  = true;
    _recStartTime = new Date();
    _detectedLang = null;

    // UI → recording state
    btnRecord.classList.add('recording');
    iconMic.style.display  = 'none';
    iconStop.style.display = '';
    actionBtns.style.display = '';
    transcriptSec.classList.remove('visible');
    barProg.style.width = '0%';
    setStatus('Enregistrement en cours…');
    journal('');

    // VU-meter
    _vuInterval = setInterval(() => {
      const level = recorder.getLevel ? recorder.getLevel() : 0;
      barLevel.style.width = Math.min(100, Math.round(level * 100)) + '%';
    }, 80);

  } catch (err) {
    journal('Erreur démarrage : ' + err.message);
    setStatus('Erreur micro : ' + err.message);
  }
}

async function cancelRecording() {
  isRecording = false;
  clearInterval(_vuInterval);
  barLevel.style.width = '0%';
  try { await recorder.stop(); } catch (_) {}
  resetRecordingUI();
  setStatus('Annulé.');
}

async function stopAndTranscribe() {
  isRecording = false;
  clearInterval(_vuInterval);
  barLevel.style.width = '0%';
  setStatus('Arrêt de l\'enregistrement…');

  let blob;
  try {
    blob = await recorder.stop();
  } catch (err) {
    journal('Erreur arrêt : ' + err.message);
    setStatus('Erreur arrêt : ' + err.message);
    resetRecordingUI();
    return;
  }

  resetRecordingUI();

  const model = selModel.value;
  const lang  = selLang.value;

  setStatus('Transcription en cours…');
  barProg.style.width = '10%';

  try {
    const text = await transcribe(blob, {
      model,
      language: lang,
      onProgress: (msg, pct) => {
        setStatus(msg);
        if (pct !== undefined) barProg.style.width = pct + '%';
      },
      onLangDetected: (l) => { _detectedLang = l; },
    });

    barProg.style.width = '100%';
    transcriptArea.value = text;
    transcriptSec.classList.add('visible');
    setStatus('Transcript prêt — vérifiez et envoyez.');

  } catch (err) {
    barProg.style.width = '0%';
    journal('Erreur transcription : ' + err.message);
    setStatus('Erreur transcription : ' + err.message);
  }
}

function resetRecordingUI() {
  btnRecord.classList.remove('recording');
  iconMic.style.display   = '';
  iconStop.style.display  = 'none';
  actionBtns.style.display = 'none';
}

// ─── Push / Download ──────────────────────────────────────────────────────────
btnPush.addEventListener('click', async () => {
  const out = localStorage.getItem('mn_output') || 'notion';

  if (out === 'download') {
    downloadTxt();
    return;
  }

  // Notion push
  const token = setToken.value.trim() || localStorage.getItem('mn_token') || '';
  const dbId  = setDb.value.trim()    || localStorage.getItem('mn_db')    || '';

  if (!token || !dbId) {
    setStatus('Renseignez le token et le database ID dans Paramètres.');
    modalBackdrop.classList.add('open');
    return;
  }

  setStatus('Envoi vers Notion…');
  btnPush.disabled = true;

  const durationMin = _recStartTime
    ? (Date.now() - _recStartTime.getTime()) / 60000
    : 0;

  try {
    await pushToNotion({
      token,
      databaseId:    dbId,
      transcript:    transcriptArea.value,
      participants:  participantsEl.value.trim(),
      source:        'Mobile',
      meetingType:   selType.value,
      durationMin:   Math.round(durationMin * 10) / 10,
      whisperModel:  selModel.value.replace('Xenova/whisper-', ''),
      detectedLang:  _detectedLang,
    });
    setStatus('✓ Page créée dans Notion !');
    clearTranscript();
  } catch (err) {
    journal('Erreur Notion : ' + err.message);
    setStatus('Erreur Notion : ' + err.message);
  } finally {
    btnPush.disabled = false;
  }
});

btnDiscard.addEventListener('click', () => {
  clearTranscript();
  setStatus('Annulé.');
});

function downloadTxt() {
  const text   = transcriptArea.value;
  const date   = (_recStartTime || new Date()).toISOString().slice(0, 16).replace('T', '_').replace(':', 'h');
  const fname  = `MeetNote_${date}.txt`;
  const blob   = new Blob([text], { type: 'text/plain;charset=utf-8' });
  const url    = URL.createObjectURL(blob);
  const a      = document.createElement('a');
  a.href       = url;
  a.download   = fname;
  a.click();
  URL.revokeObjectURL(url);
  setStatus(`✓ Fichier téléchargé : ${fname}`);
  clearTranscript();
}

function clearTranscript() {
  transcriptSec.classList.remove('visible');
  transcriptArea.value  = '';
  participantsEl.value  = '';
  barProg.style.width   = '0%';
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

// ─── Init ──────────────────────────────────────────────────────────────────────
loadSettings();
updatePushButton();
sourceHint.textContent = SOURCE_HINTS['micro'];
setStatus('Appuyer pour démarrer');
