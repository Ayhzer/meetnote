/**
 * notion.js — Push vers Notion API depuis le navigateur via proxy CORS Cloudflare Worker
 */

const NOTION_API     = 'https://notion-cors-proxy.pottier-alexandre-01.workers.dev/v1';
const NOTION_VERSION = '2026-03-11';

export async function pushToNotion({
  token,
  databaseId,
  transcript,
  participants = '',
  source = 'Mobile',
  meetingType = '',
  durationMin = 0,
  whisperModel = '',
  detectedLang = null,
  audioBlob = null,
  recordingDate = null,
}) {
  const now   = recordingDate || new Date();
  const title = `Réunion ${now.toLocaleDateString('fr-FR')} ${now.toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' })}`;

  const blocks = _transcriptToBlocks(transcript);

  const props = {
    Titre:  { title:  [{ text: { content: title } }] },
    Date:   { date:   { start: now.toISOString() } },
    Source: { select: { name: source } },
    Statut: { select: { name: 'À traiter' } },
  };

  if (participants) props['Participants'] = { select: { name: participants.slice(0, 100) } };
  if (meetingType)  props['Type']         = { select: { name: meetingType } };
  if (durationMin > 0) props['Durée (min)'] = { number: Math.round(durationMin * 10) / 10 };
  if (whisperModel) props['Modèle Whisper'] = { select: { name: whisperModel } };

  // ── Upload audio si fourni ─────────────────────────────────────────────────
  if (audioBlob) {
    try {
      const fileUploadId = await _uploadAudio(token, audioBlob, now);
      if (fileUploadId) {
        const fname = `meetnote_${now.toISOString().slice(0,19).replace(/[:T]/g,'-')}.webm`;
        props['Enregistrement'] = {
          files: [{
            type: 'file_upload',
            file_upload: { id: fileUploadId },
            name: fname,
          }]
        };
      }
    } catch (e) {
      // Non-bloquant : on continue sans le fichier audio
      console.warn('Audio upload failed:', e.message);
    }
  }

  const payload = {
    parent:     { database_id: databaseId },
    properties: props,
    children:   blocks.slice(0, 100),
  };

  let resp;
  try {
    resp = await fetch(`${NOTION_API}/pages`, {
      method: 'POST',
      headers: _jsonHeaders(token),
      body: JSON.stringify(payload),
    });
  } catch (networkErr) {
    throw new Error(
      'Impossible de contacter Notion (CORS). ' +
      'Vérifiez votre proxy Cloudflare Worker. ' +
      'Détail : ' + networkErr.message
    );
  }

  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}));
    throw new Error(err.message || `Notion API error ${resp.status}`);
  }

  return resp.json();
}

// ── Upload audio en 2 étapes ────────────────────────────────────────────────
async function _uploadAudio(token, blob, date) {
  const fname = `meetnote_${date.toISOString().slice(0,19).replace(/[:T]/g,'-')}.webm`;
  const mime  = blob.type || 'audio/webm';

  // Étape 1 — créer l'objet file_upload
  const r1 = await fetch(`${NOTION_API}/file_uploads`, {
    method: 'POST',
    headers: _jsonHeaders(token),
    body: JSON.stringify({ filename: fname, content_type: mime }),
  });
  if (!r1.ok) {
    const e = await r1.json().catch(() => ({}));
    throw new Error(e.message || `file_upload init error ${r1.status}`);
  }
  const { id: fileUploadId } = await r1.json();

  // Étape 2 — envoyer le binaire (multipart/form-data)
  const form = new FormData();
  form.append('file', blob, fname);

  const r2 = await fetch(`${NOTION_API}/file_uploads/${fileUploadId}/send`, {
    method: 'POST',
    headers: {
      Authorization:    `Bearer ${token}`,
      'Notion-Version': NOTION_VERSION,
      // NE PAS mettre Content-Type ici — le navigateur le met avec le bon boundary
    },
    body: form,
  });
  if (!r2.ok) {
    const e = await r2.json().catch(() => ({}));
    throw new Error(e.message || `file_upload send error ${r2.status}`);
  }

  return fileUploadId;
}

// ── Helpers ──────────────────────────────────────────────────────────────────
function _jsonHeaders(token) {
  return {
    Authorization:    `Bearer ${token}`,
    'Content-Type':   'application/json',
    'Notion-Version': NOTION_VERSION,
  };
}

function _transcriptToBlocks(text) {
  const blocks = [];
  for (const line of text.split('\n')) {
    const trimmed = line.trim();
    if (!trimmed) continue;
    let remaining = trimmed;
    while (remaining.length > 0) {
      blocks.push({
        object: 'block',
        type:   'paragraph',
        paragraph: {
          rich_text: [{ type: 'text', text: { content: remaining.slice(0, 2000) } }],
        },
      });
      remaining = remaining.slice(2000);
    }
  }
  return blocks;
}
