# MeetNote PC — Changelog

## [3.6.0] — 2026-11-17

### Information des participants
- **Nouvelle case « J'ai informé les participants »**, visible pendant tout enregistrement
  (manuel ou démarré automatiquement). Elle est en couleur d'alerte tant qu'elle n'est pas
  cochée, et **ne bloque ni ne retarde jamais** l'enregistrement. La cocher retient l'heure ;
  la décocher l'efface. Elle est remise à zéro à l'enregistrement suivant.
- **Trace datée** : la confirmation (« ✓ Participants informés le … » ou
  « ⚠ Information non confirmée ») apparaît sur la carte de l'enregistrement dans l'historique,
  et en **première ligne du transcript enregistré sur votre poste**. Elle n'est **jamais**
  envoyée dans Notion : la page Notion reste inchangée.

### Suppression automatique des enregistrements
- **L'audio conservé sur le poste est supprimé 30 jours après sa transcription** et **les
  transcriptions 6 mois après la réunion**. Les fichiers partent **dans la Corbeille de
  Windows** : vous pouvez les restaurer. Un fichier trop gros pour la Corbeille est supprimé
  définitivement par Windows.
- **Durées réglables** dans Paramètres → Général (« Conserver l'audio » et « Conserver la
  transcription », en jours, valeur entière d'au moins 1). Une valeur invalide est refusée et
  l'ancienne est conservée.
- **Historique allégé** à l'échéance des 6 mois : le texte de la transcription, les notes et
  les participants sont effacés ; **le titre, la date, la durée et le lien Notion sont
  conservés**. La carte indique « Audio supprimé le … » et « Transcription purgée le … ».
  « ▶ Play » et « Retranscrire » ne s'affichent plus quand l'audio n'existe plus.
- **Nettoyage complet** : les fichiers qui n'ont plus d'entrée dans l'historique suivent la même
  règle d'après la date de leur nom, et les enregistrements interrompus laissés dans le
  dossier `tmp` sont aussi supprimés après 30 jours.
- La passe s'exécute au démarrage, après chaque traitement, puis toutes les 6 heures. Chaque
  suppression est notée dans le journal (nom du fichier et règle appliquée, jamais le contenu).

### Notion
- **L'audio n'est plus envoyé vers Notion** : seul le texte part. Les pages déjà créées ne
  sont pas modifiées.

### Transcription
- **La détection homme/femme des locuteurs est retirée.** Les intervenants restent distingués
  (`SPKR_1`, `SPKR_2`…, ou leur nom Teams quand il est connu) sans mention de leur sexe.

### Application mobile
- **L'application web mobile (PWA) est retirée** de MeetNote. L'application PC est désormais
  la seule interface. L'option « Mobile » de la propriété Notion `Source` reste disponible :
  rien ne change dans votre base Notion.

---

## [3.5.0] — 2026-11-10

### Import des transcripts Teams
- **Nouveau bouton « 🎙 Transcripts TEAMS »** dans le panneau historique, à côté de
  « 📧 Import mails ». Il liste les **réunions Teams terminées des 15 derniers jours**, lues
  dans votre agenda Outlook, chacune avec son état : « transcript disponible », « pas de
  transcript » ou « déjà importé ».
- Vous cochez les réunions voulues puis cliquez sur **« Importer »** : chaque transcription
  part vers Notion (type `Teams`) ou dans un fichier, selon votre mode de sortie. Un transcript
  déjà importé **n'est plus proposé**.
- La ligne « Participants » reprend les invités de la réunion, **sans adresse e-mail**.

### Connexion Microsoft 365
- **Paramètres → Général, section « Microsoft 365 »** : saisissez l'ID client de l'application
  et l'ID de locataire (fournis par votre administrateur), puis cliquez sur
  **« Se connecter à Microsoft 365 »**. La connexion se fait **une seule fois**, dans le
  navigateur.
- La session est ensuite **conservée chiffrée sur le poste** (protection Windows DPAPI) et se
  rouvre silencieusement au redémarrage de MeetNote.

### Prérequis et limites connues
- **Prérequis côté administrateur** : l'application doit être inscrite dans Entra ID avec les
  permissions déléguées `OnlineMeetings.Read` et `OnlineMeetingTranscript.Read.All`, et
  l'administrateur Teams doit avoir ouvert l'accès aux transcriptions.
- **Pas de transcript si la transcription n'a pas été lancée pendant la réunion** : MeetNote
  ne peut importer que ce que Teams a produit.
- **Pas de nom d'intervenant** dans le texte importé.

---

## [3.4.0] — 2026-10-26

### Installation
- **Installeur par utilisateur** : `MeetNote-<version>-installation.exe` installe MeetNote
  dans `%LOCALAPPDATA%\Programs\MeetNote`, **sans mot de passe administrateur**. Il crée un
  raccourci dans le menu Démarrer (icône Bureau en option). La case « Démarrer avec Windows »
  est cochée par défaut et la case « Lancer MeetNote » est proposée en fin d'installation.
- Le build produit cet installeur **à côté du zip portable** (`MeetNote-slim-<version>-installation.exe`
  pour la variante slim). Il est signé comme l'EXE quand le certificat est présent. Sans
  Inno Setup (6.3 ou plus), le build le signale au bilan sans échouer.
- **Désinstallation** : elle arrête MeetNote, retire le programme et la clé de démarrage, et
  **conserve vos données** en les nommant à l'écran : `%APPDATA%\MeetNote` (réglages,
  historique), `%LOCALAPPDATA%\MeetNote` (modèles Whisper) et `Documents\MeetNote` (audio,
  transcriptions).

### Démarrage avec Windows
- **La clé de démarrage lance désormais `"…\MeetNote.exe" --minimized`**, en version installée
  comme en version portable. L'ancienne valeur était fausse dans le paquet : elle pointait sur
  un script interne au bundle.
- L'application **ne recrée plus la clé d'elle-même** : c'est l'installeur ou la case des
  paramètres qui décide. Une clé qui pointe sur un ancien emplacement de `MeetNote.exe`
  (dossier déplacé, ancienne version) est recalée sur l'emplacement courant.

### Fiabilité
- **Relance après plantage dans le paquet installé** : `MeetNote.exe` se supervise lui-même.
  Un plantage natif (violation d'accès WASAPI) relance l'application en quelques secondes ;
  « Quitter » ne la relance pas. Jusqu'ici, seul le mode source (`watchdog.py`) avait ce filet.
- Un second lancement de MeetNote (raccourci du menu Démarrer) **ne coupe plus l'instance en
  cours** : il lui demande d'afficher sa fenêtre puis se termine. Un enregistrement en cours
  n'est donc plus interrompu. L'installeur reste seul à pouvoir forcer l'arrêt.

---

## [3.3.0] — 2026-10-15

### Interface
- Numéro de version affiché dans la fenêtre « À propos », tiré d'une source unique
  (`version.py`, aussi lue par `build_spec.py`).

### Performance
- **Transcription calibrée sur le poste** : les segments de 10 min sont désormais transcrits
  en parallèle quand la machine peut le tenir, au lieu d'être traités un par un. Mesuré sur
  un Core Ultra 7 268V (8 cœurs), modèle `medium`, 18 min d'audio : **669 s contre 899 s,
  soit 26 % de temps en moins**. Le nombre de flux et de threads vient de `perf_profile.py`.
- Ce n'est **pas** le nombre de threads qui débride la transcription : le décodage Whisper
  est autorégressif et ne se parallélise quasiment pas au sein d'un segment (4, 6 et 8
  threads tiennent en 12 % l'un de l'autre, 8 étant même moins bon que 6 sur un CPU à cœurs
  asymétriques). Le gain vient de traiter plusieurs segments de front.
- **Réglage Settings → Général → Puissance de transcription** : Auto (défaut, s'adapte à la
  charge), Rapide, Économe. L'automatique retombe à un seul flux pendant un enregistrement,
  sur batterie, ou quand le poste est déjà sollicité.
- Un poste de 4 cœurs ou disposant de moins de 1 Go libre par flux garde le comportement
  précédent — un seul flux.

---

## [3.2] — 2026-05-18

### Nouvelles fonctionnalités
- **Arrêt de transcription en cours** : dans le panel historique, le bouton "⏹ Stop transcript" interrompt la transcription Whisper entre deux segments. Le job repasse en statut gris (◷ TRANSCRIPT) — relançable via "📝 Transcribe".
- **Type Notion "Audio"** : les enregistrements audio sont désormais envoyés dans Notion avec la propriété `Type = Audio` (identique au principe des emails envoyés avec `Type = Email`).

### Correctifs
- **Crash "App disparue brutalement" après fin de réunion** : le moniteur de périphérique audio pouvait afficher la dialog "Redémarrer MeetNote ?" pendant que la fenêtre "Transcript Teams" était ouverte — si `grab_set()` avait échoué (mode tray), le guard `grab_current()` ne fonctionnait pas. Désormais, un flag `_modal_dialog_open` empêche tout dialog de type "restart" ou "auto-start" de s'intercaler pendant un dialog modal.
- **Auto-start Outlook pendant le dialog Teams** : `_poll_outlook` pouvait déclencher un auto-start d'une nouvelle réunion pendant que `wait_window()` était actif (`_recording = False`). Ce démarrage fantôme est maintenant bloqué par le même flag.

---

## [3.1] — 2026-05-04

### Correctifs
- Crash après "Stop sans transcription" : l'archivage audio démarre maintenant avant l'ouverture du dialog "coller le transcript Teams", évitant la perte de l'audio.
- Garde de None sur `_source_combo` et `_status_var` dans `_update_window_state()`.
- Moniteur de périphérique audio : ne plus interrompre quand un dialog modal est ouvert.

### Agenda Outlook
- **Liste d'exclusion** : les réunions dont l'intitulé contient un motif configuré (Settings → Outlook → Exclusions) ne déclenchent plus l'auto-start. Un motif par ligne, insensible à la casse.
- **Popup fin de RDV pour enregistrements manuels** : si un RDV Outlook est en cours au démarrage de l'enregistrement, la popup "Fin de réunion imminente" se déclenche à l'approche de la fin.
- **Bouton 🔄 Sync agenda** dans la section MEETING pour forcer immédiatement le polling Outlook (sans attendre le cycle de 60 s).

### UI
- Menu "ℹ v3.1" en bas de la sidebar : affiche cette release note détaillée.

---

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
