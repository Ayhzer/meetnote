/**
 * notion.js — Push vers Notion API depuis le navigateur
 * Note : nécessite un proxy CORS ou l'extension Notion officielle
 * en développement, utiliser un proxy local ou Cloudflare Worker
 */

const NOTION_API = 'https://api.notion.com/v1';

export async function pushToNotion({ token, databaseId, transcript, participants = '', source = 'Mobile' }) {
  const now = new Date();
  const title = `Réunion ${now.toLocaleDateString('fr-FR')} ${now.toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' })}`;

  const blocks = _transcriptToBlocks(transcript);

  const payload = {
    parent: { database_id: databaseId },
    properties: {
      Titre: { title: [{ text: { content: title } }] },
      Date: { date: { start: now.toISOString() } },
      Participants: { rich_text: [{ text: { content: participants } }] },
      Source: { select: { name: source } },
      Tag: { select: { name: 'À traiter' } },
    },
    children: blocks.slice(0, 100),
  };

  let resp;
  try {
    resp = await fetch(`${NOTION_API}/pages`, {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${token}`,
        'Content-Type': 'application/json',
        'Notion-Version': '2022-06-28',
      },
      body: JSON.stringify(payload),
    });
  } catch (networkErr) {
    // CORS block: the browser blocks direct Notion API requests from a web page.
    // Solution: use the PC version of MeetNote (server-side push), or set up a
    // Cloudflare Worker proxy pointing to https://api.notion.com and set
    // NOTION_API above to your worker URL (e.g. https://meetnote.your-name.workers.dev).
    throw new Error(
      'Impossible de contacter Notion (CORS). ' +
      'Utilisez la version PC, ou configurez un proxy Cloudflare Worker. ' +
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
    // Notion paragraph limit: 2000 chars
    let remaining = trimmed;
    while (remaining.length > 0) {
      blocks.push({
        object: 'block',
        type: 'paragraph',
        paragraph: {
          rich_text: [{ type: 'text', text: { content: remaining.slice(0, 2000) } }],
        },
      });
      remaining = remaining.slice(2000);
    }
  }
  return blocks;
}
