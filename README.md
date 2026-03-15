# MeetNote

Enregistrement de réunions → Transcription Whisper → Notion

## Architecture

- **PC** : Icône systray Windows, enregistrement audio via sounddevice, transcription faster-whisper locale, push Notion
- **PWA** : Application web installable (Android/iOS/PC), enregistrement via MediaRecorder, transcription Whisper WASM via Transformers.js, push Notion

## Setup

### 1. PC

```bash
pip install -r requirements.txt
```

Remplir `pc/config.py` :
```python
NOTION_TOKEN = "secret_..."
NOTION_DATABASE_ID = "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
```

Lancer :
```
double-clic start.bat
```

### 2. PWA

Ouvrir `pwa/index.html` dans Chrome (ou via Live Server VS Code).

Sur Android : Chrome → "Ajouter à l'écran d'accueil"

Renseigner dans les Paramètres de l'appli :
- Notion Token
- Database ID

### 3. Notion

Créer la database **Transcripts** avec ces propriétés :

| Propriété | Type |
|-----------|------|
| Titre | title |
| Date | date |
| Participants | text |
| Source | select : PC / Mobile |
| Tag | select : À traiter / Traité |

Créer une intégration sur [notion.so/my-integrations](https://www.notion.so/my-integrations) et partager la database avec elle.

### 4. Agent Claude (traitement automatique)

Claude Desktop + MCP Notion → coller le contenu de `shared/prompt_agent_claude.md`

## Utilisation PC

1. Double-clic `start.bat`
2. Icône apparaît dans le systray
3. Clic droit → **Démarrer l'enregistrement**
4. Après la réunion → **Arrêter et transcrire**
5. La page est créée automatiquement dans Notion avec le tag "À traiter"
6. Lancer l'agent Claude pour le traitement

## Utilisation Mobile (PWA)

1. Ouvrir l'appli
2. Appuyer sur le bouton rouge pour démarrer
3. Appuyer à nouveau pour arrêter
4. Vérifier/éditer le transcript
5. **Envoyer vers Notion**
