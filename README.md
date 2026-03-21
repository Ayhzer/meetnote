# MeetNote

Enregistrement de réunions → Transcription Whisper → Notion

## Architecture

- **PC** : Icône systray Windows, enregistrement audio via sounddevice, transcription faster-whisper locale, push Notion
- **PWA** : Application web installable (Android/iOS/PC), enregistrement via MediaRecorder, transcription Whisper WASM via Transformers.js, push Notion

## Setup

### 1. PC

Dépendances :
```bash
pip install -r requirements.txt
```

Le fichier `requirements.txt` inclut : `faster-whisper`, `sounddevice`, `soundcard`, `pystray`, `Pillow`, `requests`, `pywin32`, `pycaw`, `noisereduce`, `scipy`, `psutil`.

Lancer :
```
double-clic start.bat
```

#### Interface — "Digital Control Room"

L'application s'ouvre dans une fenêtre tkinter avec une icône systray. L'interface adopte un design Stitch "Digital Control Room" en 2 colonnes :

- **Sidebar de navigation gauche** : Settings, Audio, Transcripts, History, Quit
- **Colonne gauche** :
  - **Audio Source** : sélection Mic / Loopback / Mixed
  - **Meeting** : nom de réunion auto-rempli depuis le calendrier Outlook (win32com, polling 60 s, filtre OOO/allday/durée > 12 h)
  - **Transcription** : choix du modèle Whisper et de la langue
  - **Levels** : MIC LEVEL (vu-mètre), TRANSCRIPTION progress, slider REC GAIN (0.5×–4×), slider WIN VOLUME (via pycaw)
- **Colonne droite** :
  - Boutons d'action : START RECORDING, Stop and transcribe, Stop without transcribing, Cancel
  - Indicateur de statut (dot coloré) : gris / vert / jaune selon l'état
  - System Console avec timestamps et tags colorés (erreurs en rouge)

#### Étapes indépendantes

Les trois étapes sont découplées et chacune est relançable indépendamment depuis l'historique :
1. **Enregistrement audio** → archivé dans `Documents\MeetNote\audio\` (opus via ffmpeg ou wav en fallback)
2. **Transcription** (faster-whisper, local) → sauvegardée dans `Documents\MeetNote\transcripts\` avec timestamps `[HH:MM:SS]` par segment
3. **Upload Notion** → ou export fichier local selon le mode choisi dans les paramètres

#### Fenêtre Historique

Accessible via la sidebar (History). Affiche des cartes avec :
- Badges de statut colorés : AUDIO / TRANSCRIPT / NOTION
- Boutons contextuels : Transcribe (avec sélecteur de modèle), Re-transcribe, → Notion, View txt, Open Notion
- Bouton Delete (suppression de l'entrée)
- Bouton Import audio (supporte wav, mp3, opus, ogg, m4a, flac, webm, mp4)

#### Paramètres

Modal Settings : Notion token (avec toggle afficher/masquer), Database ID, mode de sortie (Notion ou fichier local uniquement).

#### Instance unique

Au démarrage, l'instance précédente est automatiquement terminée via un fichier PID.

#### Qualité audio

- Resampling via `scipy.signal.resample_poly`
- Débruitage via `noisereduce` (prop_decrease=0.75, stationary)
- Mix Mixte : 60 % mic / 40 % loopback

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
2. L'icône apparaît dans le systray et la fenêtre s'ouvre
3. Sélectionner la source audio (Mic / Loopback / Mixed)
4. Vérifier/saisir le nom de la réunion (auto-rempli depuis Outlook si disponible)
5. Cliquer **START RECORDING**
6. En fin de réunion :
   - **Stop and transcribe** : archive l'audio et lance la transcription immédiatement
   - **Stop without transcribing** : archive l'audio uniquement, transcription à relancer plus tard depuis l'historique
7. Après transcription, cliquer **→ Notion** depuis la carte historique pour uploader
8. Lancer l'agent Claude pour le traitement

## Utilisation Mobile (PWA)

1. Ouvrir l'appli
2. Appuyer sur le bouton rouge pour démarrer
3. Appuyer à nouveau pour arrêter
4. Vérifier/éditer le transcript
5. **Envoyer vers Notion**
