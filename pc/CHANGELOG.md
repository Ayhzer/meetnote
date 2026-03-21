# MeetNote PC — Changelog

## [3.0] — 2026-03-21

### UI — Refonte "Digital Control Room" (Stitch design)
- Layout 2 colonnes avec sidebar de navigation gauche fixe
- Nouvelle palette de couleurs Material Design (surface hierarchy)
- Boutons d'action avec états visuels distincts (rouge/vert/violet/gris)
- Indicateur de statut (dot coloré) : gris/vert/jaune selon l'état
- System Console avec timestamps et tags colorés (erreurs en rouge)
- Fenêtre historique redesignée : cartes avec badges statut colorés

### Nouvelles fonctionnalités
- **Étapes indépendantes** : audio, transcription, Notion — chacune relançable depuis l'historique
- **"Stop without transcribing"** : archive l'audio, diffère la transcription
- **Import audio externe** : bouton dans l'historique, supporte wav/mp3/opus/ogg/m4a/flac/webm/mp4
- **Retranscription avec modèle différent** : combo modèle sur chaque carte de l'historique
- **Suppression d'entrées** depuis l'historique
- **Timestamps [HH:MM:SS]** dans les transcripts (par segment Whisper)
- **Agenda Outlook** : pré-remplissage automatique du nom de réunion (polling 60s, filtre OOO/allday/durée > 12h)
- **Slider REC GAIN** (0.5x–4x) : gain numérique appliqué aux samples, toutes sources
- **Slider WIN VOLUME** : contrôle du volume master Windows via pycaw
- **Instance unique** : au démarrage, l'instance précédente est automatiquement terminée (PID file)

### Qualité audio & transcription
- Resampling scipy `resample_poly` (remplace np.interp)
- Débruitage `noisereduce` (prop_decrease=0.75, stationary)
- Mix Mixte : 60% mic / 40% loopback (voix moins atténuée)
- Whisper : beam_size=5, no_speech_threshold=0.6, vad min_silence=500ms, condition_on_previous_text=False
- Détection RIFF magic bytes pour éviter l'erreur "file does not start with RIFF id" sur fichiers opus renommés en .wav

### Corrections
- Status "Processing N job(s)" ne se remettait pas à "Idle" après la fin des jobs
- Fichiers opus archivés avec extension .wav causaient une erreur à la retranscription
- Nom de réunion Outlook retournait "OFF" (bloc hors-bureau) au lieu du vrai RDV
- S_TOP non défini dans _open_settings causait une NameError

---

## [2.0] — 2026-03-17

### Nouveautés

#### Traitement en arrière-plan (job queue)
- L'enregistrement et la transcription sont maintenant **entièrement découplés**.
- Cliquer « Arrêter et transcrire » place le job en file d'attente et libère immédiatement l'interface.
- Il est possible de **démarrer un nouvel enregistrement pendant qu'un job est en cours** de transcription ou d'envoi.
- Un worker daemon unique traite les jobs séquentiellement (un à la fois) pour éviter la surcharge CPU.
- L'indicateur de statut affiche le nombre de jobs en attente (`⚙ N job(s) en traitement…`).

#### Prévention de veille étendue
- La prévention de veille Windows (`SetThreadExecutionState`) couvre maintenant **toute la durée du traitement** (transcription + upload Notion), pas seulement l'enregistrement.
- La veille est réactivée uniquement quand le worker a terminé le job.

#### Archive audio locale
- Après chaque transcription, l'audio est **automatiquement archivé** dans `Documents\MeetNote\audio\`.
- Si ffmpeg est disponible : compressé en Opus 24 kbps (≈ 10 Mo/heure).
- Sinon : copie WAV brut.
- Format du nom : `meetnote_YYYYMMDD_HHMMSS.opus` (ou `.wav`).

#### Découpage audio pour Notion
- Les enregistrements longs sont automatiquement **découpés en segments de 10 minutes** avant upload vers Notion.
- Chaque segment est uploadé séparément et apparaît comme un fichier distinct sur la page Notion.
- Utilise `ffmpeg segment` + `ffprobe` pour détecter la durée.
- Si ffprobe est absent, la durée est estimée d'après la taille du fichier.
- ffmpeg et ffprobe sont **embarqués dans le bundle** standalone.

#### Correction des répétitions (transcription longue)
- `condition_on_previous_text=False` : élimine les boucles de répétition du décodeur Whisper.
- Découpage en segments de 10 minutes pour la transcription : chaque segment est traité indépendamment, ce qui prévient la dérive du contexte sur les longues conversations.

### Corrections
- La veille PC ne s'activait plus après l'arrêt de l'enregistrement, avant la fin de la transcription.
- Le fichier WAV temporaire était supprimé avant d'être archivé localement.

### Architecture
```
Enregistrement (thread audio)
    ↓ _do_stop_transcribe()
    ↓ Crée un _Job, l'ajoute à _job_queue
    ↓ Signal _work_event
Worker thread (_worker_loop)
    ↓ _prevent_sleep()
    ↓ _process_job(job)
        ├── _transcribe_job()          ← Whisper (segmenté, sans répétitions)
        ├── _archive_audio()           ← Copie locale dans Documents/MeetNote/audio/
        └── push_to_notion() ou save   ← Upload découpé en segments si nécessaire
    ↓ _allow_sleep()
```
