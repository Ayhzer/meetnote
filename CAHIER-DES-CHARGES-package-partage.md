# Cahier des charges — MeetNote : paquet partageable « prêt à l'emploi »

> À utiliser dans VS Code avec Claude Code, dans le dépôt `C:\DEV\meetnote`.
> Cycle prévu : `/reflechir` sur ce fichier → `/cadrer` lot par lot → `/avancer` → `/verifier` → `/securiser` en fin de parcours.
> Rédigé le 15/10/2026 à partir d'une analyse du dépôt (commit `7db4fc2` + modifications non commitées).

---

## 0. Prompt de démarrage à coller dans Claude Code

```
Lis CLAUDE.md, .claude/regles/regles-dev.md puis CAHIER-DES-CHARGES-package-partage.md.
Objectif : rendre MeetNote installable par un ami sur un autre PC Windows, sans moi,
sans Python et sans connaissances techniques.
Lance /reflechir sur ce cahier des charges pour proposer la suite de lots, en respectant
l'ordre et les recettes utilisateur indiqués. Ne touche pas encore PLAN.md.
```

Puis, lot par lot : `/cadrer L1`, `/avancer`, `/verifier`, et ainsi de suite.

---

## 1. Contexte

MeetNote est une application Windows (barre des tâches) qui enregistre les réunions de l'agenda Outlook, les transcrit en local avec faster-whisper, puis envoie le texte dans une base Notion. Des routines Claude traitent ensuite cette base.

Un exécutable PyInstaller existe (`pc\dist\MeetNote\MeetNote.exe`, 4,2 Go), mais il ne peut pas être donné tel quel :

| # | Constat | Conséquence pour un tiers |
|---|---|---|
| 1 | L'exe date du 29/08 ; le dernier commit du 11/08 ; 7 fichiers modifiés non commités | Contenu de l'exe non traçable |
| 2 | 4,2 Go : modèles Whisper présents deux fois (`models\medium` 1,5 Go **et** `_internal\faster_whisper_models` 1,5 Go), `torch` 365 Mo, `llvmlite` 115 Mo, `ffmpeg.exe` + `ffprobe.exe` 232 Mo chacun | Impossible à envoyer simplement |
| 3 | Signature par certificat auto-signé (`CN=MeetNote`) | Alerte SmartScreen / antivirus |
| 4 | Base Notion à créer à la main avec 8 propriétés aux noms exacts | Échec silencieux à la moindre faute de frappe |
| 5 | Agenda : Outlook classique (COM) ou EWS uniquement | Pas d'auto-start avec le nouvel Outlook, Gmail, Outlook.com |
| 6 | Liste d'exclusions vide par défaut | Les créneaux perso sont enregistrés |
| 7 | Pas d'arrêt automatique en fin de réunion (simple fenêtre d'avertissement) | Enregistrements de plusieurs heures de bruit |
| 8 | Anomalie de captation ouverte : transcripts vides ou hallucinés (« Thanks for watching! ») | Perte de réunions sans alerte |
| 9 | `build_spec.py` l. 56 : chemin Python en dur `C:\Users\apottier\...` | Build impossible sur un autre poste |
| 10 | `_get_startup_cmd()` en mode figé passe un chemin de script interne à l'exe | Démarrage avec Windows à vérifier |
| 11 | `shared/prompt_agent_claude.md` date d'avril, sans rapport avec la doctrine actuelle | Partie « routines Claude » inutilisable |

## 2. Objectif et utilisateur cible

**Utilisateur cible** : un ami, sous Windows 10 ou 11, à l'aise avec un PC mais pas développeur. Il a un compte Notion (offre gratuite possible) et éventuellement un compte Claude.

**Recette globale** : « Je télécharge un fichier, je l'installe sans droits administrateur, un assistant me guide pour connecter Notion, je fais un test de micro, et ma prochaine réunion de l'agenda est enregistrée, transcrite et envoyée dans Notion sans que j'y touche. »

## 3. Décisions déjà prises (ne pas reposer la question)

- **Cible Windows uniquement.** La PWA reste hors périmètre de ce chantier.
- **Installation par utilisateur, sans droits admin**, dans `%LOCALAPPDATA%\Programs\MeetNote`.
- **Installeur Inno Setup** (outil externe de build, pas une dépendance Python). Livrable alternatif : un zip portable.
- **Modèles Whisper téléchargés au premier lancement**, pas embarqués. Choix proposé selon la RAM : `small` sous 8 Go, `medium` au-delà. Le bundle ne contient plus aucun modèle.
- **ffmpeg** : passer à une build « essentials » (environ 80 Mo), ou télécharger au premier lancement.
- **Signature** : on garde le certificat auto-signé. Le guide explique comment passer l'alerte SmartScreen. Un certificat payant est hors périmètre.
- **Aucun secret, aucune donnée personnelle dans le paquet.** Tout réglage reste dans `%APPDATA%\MeetNote\` sur le poste de l'utilisateur.
- **Exclusions par défaut** (modifiables) : `todo`, `off`, `perso`, `privé`, `prive`, `déjeuner`, `dejeuner`, `trajet`, `ne pas enregistrer`.
- **Arrêt automatique** : fin du créneau agenda + 15 minutes, délai réglable ; la fenêtre d'avertissement actuelle reste.

## 4. Lots

Chaque lot est une tranche utilisable. Il se clôt sur sa recette utilisateur et sur les trois portes de qualité (lint, tests, build).

### L0 — Socle propre et traçable
- Relire les 7 fichiers modifiés non commités, les commiter (ou les écarter) avec un message clair.
- Étiqueter la version de départ (`v1.0.0-base`).
- Retirer le chemin en dur de `build_spec.py` (détection via `sys.base_prefix` ou variable d'environnement).
- Afficher le numéro de version dans la fenêtre « À propos » et dans le nom du zip et de l'installeur.

**Recette** : « Je vois le numéro de version dans l'application et il correspond au fichier que j'ai installé. »

### L1 — Assistant de premier lancement
Au premier démarrage (pas de `settings.json`), un assistant en 4 écrans :
1. **Notion** : coller le token de l'intégration (avec un lien et 3 lignes d'explication), puis coller le lien d'une page Notion parente. L'assistant **crée lui-même la base « Transcriptions »** avec le schéma exact (`Titre`, `Date`, `Participants`, `Source`, `Statut`, `Type`, `Durée (min)`, `Modèle Whisper`) et en enregistre l'ID. Si l'utilisateur fournit une base existante : vérifier le schéma et lister clairement ce qui manque.
2. **Agenda** : détecter automatiquement Outlook classique (COM), sinon proposer EWS, sinon annoncer « mode manuel » en clair.
3. **Exclusions** : pré-remplies avec la liste par défaut, modifiables.
4. **Test micro et son du PC** : enregistrer 5 secondes, afficher les niveaux, refuser de terminer si le niveau est nul (cause n° 1 des transcripts vides).

Ajouter un bouton « Relancer l'assistant » dans les paramètres.

**Recette** : « Sans rien créer à la main dans Notion, j'ai une base prête et un premier test d'envoi réussi depuis l'assistant. »

### L2 — Fiabilité de captation
- **Contrôle de silence** : si le niveau reste sous un seuil pendant les 2 premières minutes d'un enregistrement, notification Windows + ligne rouge dans la console + indicateur dans l'historique.
- **Arrêt automatique** à fin de créneau + 15 min (réglable), avec transcription lancée normalement.
- **Arrêt de sécurité** sur silence prolongé (30 min sans signal, réglable).
- **Anti-doublon** avant envoi Notion (même titre et même date de début → mise à jour, pas de création).
- **Garde-fou anti-hallucination** : un transcript dont plus de la moitié du texte est fait de phrases connues de Whisper sur silence (« Thanks for watching! », « Sous-titres réalisés par… ») est marqué « Inexploitable » au lieu d'être envoyé comme normal.
- Tests unitaires pour chacune de ces règles.

**Recette** : « Si mon micro est coupé, je suis prévenu dans les deux minutes ; si j'oublie d'arrêter, l'enregistrement s'arrête tout seul. »

### L3 — Paquet léger
- Supprimer les modèles du bundle et la duplication `models\` / `_internal\faster_whisper_models`.
- Identifier qui importe `torch` et `llvmlite` ; les exclure s'ils ne servent pas (ou remplacer l'appel concerné).
- ffmpeg essentials, ffprobe seulement s'il est indispensable.
- Téléchargement du modèle au premier lancement, avec barre de progression, reprise sur coupure et choix automatique selon la RAM (s'appuyer sur `perf_profile.py`).
- Cible : moins de 400 Mo installé hors modèle.

**Recette** : « Le fichier à télécharger pèse moins de 300 Mo et l'application récupère toute seule le bon modèle au premier lancement. »

### L4 — Installeur et démarrage avec Windows
- Script Inno Setup versionné dans le dépôt (`pc\installer\meetnote.iss`) : installation par utilisateur, raccourci menu Démarrer, case « Démarrer avec Windows » cochée par défaut.
- Corriger `_get_startup_cmd()` en mode figé : la clé `Run` doit pointer sur `MeetNote.exe --minimized`, rien d'autre.
- Le désinstalleur retire la clé `Run` et les fichiers du programme, **sans toucher** à `%APPDATA%\MeetNote\` ni à `Documents\MeetNote\` (données de l'utilisateur) ; il le dit à l'écran.
- Produire aussi un zip portable.

**Recette** : « J'installe sans demander de mot de passe administrateur, je redémarre le PC et MeetNote est dans la barre des tâches. »

### L5 — Guide et kit Notion + Claude
- `GUIDE-INSTALLATION.md` (et sa version PDF) : une page, avec captures. Contenu : passer SmartScreen, créer l'intégration Notion, lancer l'assistant, régler les exclusions, ce que fait et ne fait pas l'outil.
- `shared/prompt_agent_claude.md` réécrit en version générique et anonymisée : traitement des transcripts « À traiter » → compte rendu, actions, statut « Traité ». Avec plafond de 3 transcripts par exécution et règle de confidentialité (pas de données de santé ou RH hors base dédiée).
- Section « Limites » : Windows uniquement ; agenda limité à Outlook classique ou EWS ; Microsoft a annoncé la fin d'EWS sur Exchange Online ; modèle `medium` lent sur une petite machine ; ne pas utiliser pour des réunions contenant des données de santé sans avis du DPO.

**Recette** : « Avec la seule page du guide, un ami installe et configure MeetNote sans m'appeler. »

### L6 — Recette sur un poste vierge
- Test dans Windows Sandbox (ou une VM) : installation, assistant, enregistrement d'une réunion test de 5 minutes, transcription, arrivée dans Notion, désinstallation.
- Consigner le résultat dans `RECETTE.md` avec date, version et captures.
- Puis `/securiser` sur l'ensemble des lots.

**Recette** : « Sur un Windows neuf, le parcours complet fonctionne du premier coup en suivant le guide. »

## 5. Hors périmètre

- PWA mobile.
- Remplacement d'EWS par Microsoft Graph (à traiter dans un chantier dédié).
- Certificat de signature commercial.
- Transcription sur GPU.
- Diarisation avec noms réels (le croisement avec la transcription Teams reste manuel).

## 6. Questions qui pourront légitimement remonter (cf. règles §6)

- **Dépendance** : si une bibliothèque de téléchargement ou de notification Windows est nécessaire, la proposer avec une alternative sans dépendance.
- **Suppression** : retrait des modèles et des binaires du bundle (confirmer une fois en L3).
- **Règle ambiguë** : formulation de l'avertissement « données de santé » dans le guide.
