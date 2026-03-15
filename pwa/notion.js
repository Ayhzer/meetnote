/**
 * notion.js — Push vers Notion API depuis le navigateur
 * Note : nécessite un proxy CORS (Cloudflare Worker) pointant vers api.notion.com
 * En développement, configurer NOTION_PROXY_URL ci-dessous.
 */

// Si vous avez un Cloudflare Worker proxy, remplacez par son URL :
// ex: 'https://meetnote-proxy.your-name.workers.dev'
const NOTION_API = 'https://api.notion.com/v1';

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
}) {
  const now   = new Date();
  const title = `Réunion ${now.toLocaleDateString('fr-FR')} ${now.toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' })}`;

  const blocks = _transcriptToBlocks(transcript);

  const props = {
    Titre:  { title:  [{ text: { content: title } }] },
    Date:   { date:   { start: now.toISOString() } },
    Source: { select: { name: source } },
    Statut: { select: { name: 'À traiter' } },
  };

  if (participants) {
    props['Participants'] = { select: { name: participants.slice(0, 100) } };
  }
  if (meetingType) {
    props['Type'] = { select: { name: meetingType } };
  }
  if (durationMin > 0) {
    props['Durée (min)'] = { number: Math.round(durationMin * 10) / 10 };
  }
  if (whisperModel) {
    props['Modèle Whisper'] = { select: { name: whisperModel } };
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
      headers: {
        Authorization:    `Bearer ${token}`,
        'Content-Type':   'application/json',
        'Notion-Version': '2022-06-28',
      },
      body: JSON.stringify(payload),
    });
  } catch (networkErr) {
    // CORS block: the browser blocks direct Notion API requests.
    // Solution: set up a Cloudflare Worker proxy pointing to https://api.notion.com
    // and update NOTION_API above to your worker URL.
    throw new Error(
      'Impossible de contacter Notion (CORS). ' +
      'Configurez un proxy Cloudflare Worker et mettez à jour NOTION_API dans notion.js. ' +
      'Détail : ' + networkErr.message
    );
  }

  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}));
    throw new Error(err.message || `Notion API error ${resp.status}`);
  }

  return resp.json();
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
