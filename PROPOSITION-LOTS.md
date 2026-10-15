# Proposition de lots — MeetNote (paquet partageable)

Générée à partir de `CAHIER-DES-CHARGES-package-partage.md` le 15/10/2026. **Document de travail : rien ici n'est prenable par
`/avancer` ni par la boucle autonome tant qu'un lot n'a pas été recopié, à la main, dans
`PLAN.md`.**

Numérotation : il n'existe pas encore de `PLAN.md` ; la numérotation **L0 → L6 du cahier des
charges est conservée telle quelle**, pour que `/cadrer L1` désigne bien l'assistant de premier
lancement comme l'annonce le prompt de démarrage. L'ordre et les recettes sont ceux du cahier.

## L0 — Je sais quelle version j'ai installée

### Recette utilisateur du lot
**Je vois le numéro de version dans l'application et il correspond au fichier que j'ai installé.**

Couvre les constats 1 et 9 : remise au propre du dépôt (le `git status` montre 7 fichiers modifiés
**et** 3 nouveaux fichiers non suivis — `perf_profile.py` et ses deux tests — à intégrer au même
commit), étiquette `v1.0.0-base`, suppression du chemin Python en dur de `build_spec.py`, numéro
de version unique repris dans l'application et dans les noms des livrables. La fenêtre de
version existe déjà (`_open_version_window`) mais affiche `3.1` en dur alors que le CHANGELOG
est à `3.2`. Les commits restent soumis à l'accord de l'utilisateur.

## L1 — Un assistant me prépare tout au premier lancement

### Recette utilisateur du lot
**Sans rien créer à la main dans Notion, j'ai une base prête et un premier test d'envoi réussi depuis l'assistant.**

Couvre les constats 4, 5 et 6 : assistant en 4 écrans (Notion avec création automatique de la
base ou vérification d'une base existante, détection de l'agenda, exclusions pré-remplies, test
micro + son du PC qui refuse un niveau nul), et bouton « Relancer l'assistant » dans les
paramètres. Ne couvre pas le téléchargement du modèle Whisper (L3) : l'assistant valide l'envoi
Notion, pas encore une transcription.

## L2 — Je ne perds plus une réunion sans le savoir

### Recette utilisateur du lot
**Si mon micro est coupé, je suis prévenu dans les deux minutes ; si j'oublie d'arrêter, l'enregistrement s'arrête tout seul.**

Couvre les constats 7 et 8 : contrôle de silence en début d'enregistrement, arrêt automatique à
fin de créneau + 15 min, arrêt de sécurité sur 30 min de silence, anti-doublon Notion,
marquage « Inexploitable » des transcripts hallucinés — chaque règle avec ses tests unitaires.
Ne cherche pas la cause racine de l'anomalie de captation : il la rend visible au lieu de la
laisser passer en silence.

## L3 — Je télécharge un fichier léger, l'application fait le reste

### Recette utilisateur du lot
**Le fichier à télécharger pèse moins de 300 Mo et l'application récupère toute seule le bon modèle au premier lancement.**

Couvre le constat 2 : plus aucun modèle dans le paquet (fin de la double copie de 1,5 Go), sortie
de `torch` / `llvmlite` s'ils ne servent pas, ffmpeg « essentials », téléchargement du modèle
au premier lancement avec progression, reprise et choix selon la RAM via `perf_profile.py`.
Le retrait des modèles et binaires du bundle fera l'objet d'une seule confirmation (question
autorisée n° 1) ; une éventuelle bibliothèque de téléchargement, d'une autre (n° 2).

## L4 — MeetNote s'installe sans administrateur et démarre avec Windows

### Recette utilisateur du lot
**J'installe sans demander de mot de passe administrateur, je redémarre le PC et MeetNote est dans la barre des tâches.**

Couvre le constat 10 : script Inno Setup versionné, installation par utilisateur, raccourci,
démarrage automatique, désinstallation qui préserve les données de l'utilisateur et le dit,
zip portable. **Point d'attention relevé dans le code** : en mode figé, `_get_startup_cmd()`
(`pc/meetnote-tray.py:4257`) ne passe pas par le watchdog, seul filet contre les plantages natifs
WASAPI que Python ne rattrape pas. Pointer simplement la clé `Run` sur `MeetNote.exe --minimized`
réglerait le chemin mais pas la relance après plantage : `/cadrer` devra prévoir un équivalent
du watchdog dans le paquet installé.

## L5 — Une seule page suffit à mon ami pour s'en servir

### Recette utilisateur du lot
**Avec la seule page du guide, un ami installe et configure MeetNote sans m'appeler.**

Couvre les constats 3 et 11 : `GUIDE-INSTALLATION.md` + PDF (passage de SmartScreen, intégration
Notion, assistant, exclusions, limites), et réécriture générique et anonymisée de
`shared/prompt_agent_claude.md` (plafond de 3 transcripts, règle de confidentialité). Vient après
L1–L4 car les captures doivent montrer l'assistant et l'installeur définitifs. La formulation de
l'avertissement « données de santé » est une question autorisée (n° 4).

## L6 — Le parcours complet marche sur un Windows neuf

### Recette utilisateur du lot
**Sur un Windows neuf, le parcours complet fonctionne du premier coup en suivant le guide.**

Recette de bout en bout dans Windows Sandbox ou une VM — installation, assistant, réunion test de
5 minutes, transcription, arrivée dans Notion, désinstallation — consignée dans `RECETTE.md`
(date, version, captures). Ce qui casse pendant la recette devient des tâches de ce lot. La passe
`/securiser` sur l'ensemble des lots suit sa clôture, elle n'en est pas la condition de sortie.
