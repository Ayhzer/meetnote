# PLAN — MeetNote

## L0 — Je sais quelle version j'ai installée

### Recette utilisateur du lot
Je vois le numéro de version dans l'application et il correspond au fichier que j'ai installé.

### Portes de qualité
| # | Commande | Répertoire | Exigence |
|---|---|---|---|
| 1 | `ruff check .` | racine | 0 erreur |
| 2 | `python -m unittest discover -s tests` | racine | vert |

Ce sont les **seules** conditions de clôture d'une tâche. État au cadrage (15/10/2026) : les
deux sont rouges — la porte 2 vise un dossier `tests` qui n'existe pas, la porte 1 lit les
scripts du kit sous `.claude/` (1 760 constats) et la dette héritée de `pc/` (263 constats).
T002 et T003 les remettent au vert ; à partir de T004, toute tâche les laisse vertes.

### Tâches
- [x] T001 🔒 — Relire et commiter le socle actuel, puis poser l'étiquette `v3.2-base` · fichiers : les 7 fichiers modifiés (`CLAUDE.md`, `pc/CHANGELOG.md`, `pc/build_spec.py`, `pc/build_spec_slim.py`, `pc/meetnote-tray.py`, `pc/meetnote.spec`, `pc/whisper_local.py`) + les 3 non suivis `pc/perf_profile.py`, `pc/test_perf_profile.py`, `pc/test_transcription_parallele.py` · fini quand : `git status` ne montre plus de modification sous `pc/` et `git tag` liste `v1.0.0-base` — commit et étiquette sur feu vert de l'utilisateur, à faire avant de lancer `/avancer`
- [x] T002 — Rendre les portes exécutables · fichiers : `ruff.toml` (nouveau, racine — reprend la sélection de règles résolue aujourd'hui, `ruff check --show-settings`, et exclut `.claude/`, `_archive-*/`, `pwa/`), `.claude/kit.json` (porte `test` → `python -m unittest discover -s pc -p "test_*.py"`), `pc/test_transcription_parallele.py` (ignoré sauf si `MEETNOTE_TEST_LENT=1`) · fini quand : la porte test passe en moins d'une minute et `ruff check .` ne remonte plus que des constats de `pc/`
- [x] T003 — Ramener `ruff check .` à zéro sur `pc/` · fichiers : `pc/*.py`, `ruff.toml` · fini quand : les deux portes sont vertes ; corrections sûres appliquées (`I001`, `F401`, `F541`, `RUF100`, `F841`), règles héritées (`BLE001`, `S110`, `DTZ005`, `PLW1510`…) neutralisées par `per-file-ignores` sur les seuls fichiers existants, chaque ligne commentée « dette héritée »
- [x] T004 — Créer une source unique de version et l'afficher dans la fenêtre « À propos » · fichiers : `pc/version.py` (nouveau, `VERSION = "3.3.0"`), `pc/meetnote-tray.py` (l. 102 et `_open_version_window`, titre « À propos »), `pc/CHANGELOG.md` (entrée `[3.3.0]`), `pc/test_version.py` (nouveau) · fini quand : la fenêtre ouverte depuis le menu affiche « MeetNote v3.3.0 » et un test vérifie que `VERSION` est au format x.y.z et a son entrée dans le CHANGELOG
- [x] T005 — Retirer le chemin Python en dur des scripts de build · fichiers : `pc/build_spec.py` (l. 56-58 : `sys.base_prefix`, surchargeable par `MEETNOTE_PYTHON_HOME`), `pc/build_spec_slim.py` si concerné, `pc/test_build_spec.py` (nouveau) · fini quand : aucune occurrence de `apottier` dans `build_spec*.py` et un test couvre la résolution (défaut + variable d'environnement)
- [x] T006 — Inscrire la version dans les propriétés de `MeetNote.exe` et dans le nom du dossier livré · fichiers : `pc/build_spec.py` (génère la ressource de version PyInstaller depuis `pc/version.py`, sortie `dist\MeetNote-<version>\`), `pc/test_build_spec.py` · fini quand : un test vérifie le contenu de la ressource générée ; recette manuelle : clic droit sur l'exe → Propriétés → Détails affiche 3.3.0, comme la fenêtre « À propos »

## Décidé en autonomie
- 15/10/2026 — L0 cadré en premier : c'est le premier lot dans l'ordre du cahier ; la numérotation L0–L6 du cahier est conservée.
- 15/10/2026 — Version affichée `3.3.0`, à la suite de la `3.2` du CHANGELOG (le code affichait encore `3.1`) ; l'étiquette `v1.0.0-base` du cahier reste un repère git du point de départ.
- 15/10/2026 — Dette lint héritée de `pc/` neutralisée par fichier plutôt que corrigée en L0 : 113 `BLE001` dans un fichier de 4 400 lignes ne sont pas une tâche de socle ; le code nouveau reste soumis à toutes les règles.
- 15/10/2026 — Test de transcription parallèle (modèle + ffmpeg, plusieurs minutes) sorti de la porte, conservé derrière `MEETNOTE_TEST_LENT=1`.
- 15/10/2026 — Le nom versionné du zip et de l'installeur viendra en L4, de la même source `pc/version.py`.

- 15/10/2026 — `ruff.toml` fige les 413 règles par défaut de ruff 0.16 (aucune config n'existait, ni projet ni utilisateur) : une montée de version de ruff ne change plus la porte.
- 15/10/2026 — `test_transcription_parallele.py` reçoit une classe `TestCase` sautée sauf `MEETNOTE_TEST_LENT=1` ; l'usage en script reste inchangé.
- 15/10/2026 — T003 : 48 constats corrigés dans le code, 215 neutralisés par fichier ; le B023 de l'historique (Entrée donnait le focus au dernier champ de titre) était un vrai bug, corrigé. Ordre d'import sounddevice → soundcard protégé du tri (`# isort: off`).
- 15/10/2026 — T005 : la résolution de la racine Python vit dans `pc/build_paths.py`, car `build_spec.py` lance le build dès son import et ne peut pas être importé par un test.
- 15/10/2026 — T001 tranchée avec l'utilisateur : deux commits (socle d'avant L0 reconstitué depuis le point de restauration de 12 h 03, étiqueté `v3.2-base` et non `v1.0.0-base` pour rester dans la numérotation 3.x ; puis T002–T005). Seuls `.claude/kit.json` et `.claude/settings.json` sont versionnés ; `pc/meetnote.spec` est sorti du suivi.
- 15/10/2026 — T006 : ressource de version générée par `pc/build_version.py`, écrite hors dépôt (`C:\Temp\meetnote_build\meetnote.version.txt`) ; dossier livré `pc\dist\MeetNote-<version>`, variante slim incluse. L'ancien `pc\dist\MeetNote` reste sur le disque.
- 15/10/2026 — L1 cadré par l'échelle de relance de `/avancer` (backlog L0 coché), à partir du cahier des charges.
- 15/10/2026 — L1 : premier lancement = aucun token Notion **et** assistant jamais terminé, pour ne pas rouvrir l'assistant chez un utilisateur déjà configuré.
- 15/10/2026 — L1 : la base créée porte aussi `Files & media`, que `notion_push.py` écrit déjà pour les pièces jointes.
- 15/10/2026 — L1 : la page de test d'envoi reste dans la base (preuve visible), titrée « Test d'installation MeetNote ».
- 15/10/2026 — L1 : son du PC nul = avertissement, pas refus (poste sans haut-parleur possible) ; seul le micro nul bloque la fin de l'assistant.
- 15/10/2026 — L1 : l'assistant s'ouvre aussi en `--minimized` — sans configuration Notion, l'application ne sert à rien.
- 15/10/2026 — Recette L0 vérifiée sur le build : `pc\dist\MeetNote-3.3.0\MeetNote.exe` porte ProductVersion et FileVersion 3.3.0.

## Découvert en route
- En mode figé, `_get_startup_cmd()` (`pc/meetnote-tray.py:4257`) ne passe pas par le watchdog : à traiter en L4.
- `.claude/journal-qualite.md` : 48 constats en attente.
- La fenêtre « À propos » affiche v3.3.0 au-dessus des notes `RELEASE_NOTES` embarquées dans `meetnote-tray.py`, figées à la 3.1 : elles ne suivent pas `pc/CHANGELOG.md`.
- `notion_push.py:94` lit `config.NOTION_UPLOAD_SEGMENT_MIN`, absent de `config.py` : l'`AttributeError` est avalée par l'`except` de la l. 111, l'envoi de l'audio vers Notion échouerait donc en silence. À vérifier.
- `outlook_cal.get_current_or_next_meeting` essaie EWS avant COM, l'inverse de l'ordre de détection prévu par l'assistant.

## L1 — Un assistant me prépare tout au premier lancement

### Recette utilisateur du lot
Sans rien créer à la main dans Notion, j'ai une base prête et un premier test d'envoi réussi depuis l'assistant.

### Portes de qualité
| # | Commande | Répertoire | Exigence |
|---|---|---|---|
| 1 | `ruff check .` | racine | 0 erreur |
| 2 | `python -m unittest discover -s pc -p "test_*.py"` | racine | vert |

Ce sont les **seules** conditions de clôture d'une tâche. La logique de l'assistant vit dans des
modules purs `pc/assistant_*.py`, testables sans interface ni accès réseau (`requests`, `win32com`,
`keyring` et `sounddevice` mockés), sur le modèle de `perf_profile.py`. `meetnote-tray.py` ne
reçoit que le branchement.

### Tâches
- [ ] T007 — Décrire le schéma de la base et vérifier une base existante · fichiers : `pc/assistant_notion.py` (nouveau), `pc/test_assistant_notion.py` (nouveau) · fini quand : le module expose le schéma attendu (les 8 propriétés du CLAUDE.md **plus** `Files & media` de type files, qu'écrit `notion_push.py:129`), extrait l'identifiant d'une page ou d'une base depuis un lien Notion collé (avec ou sans titre, `?v=`, tirets), et compare le schéma lu d'une base à l'attendu en rendant la liste en clair de ce qui manque ou n'a pas le bon type ; testé sans réseau
- [ ] T008 — Créer la base, lire un schéma et envoyer une page de test via l'API Notion · fichiers : `pc/assistant_notion.py`, `pc/test_assistant_notion.py` · fini quand : trois fonctions prennent le token en paramètre (jamais le `HEADERS` global de `notion_push`) — créer la base « Transcriptions » sous une page parente, lire le schéma d'une base existante, créer une page « Test d'installation MeetNote » — avec la forme de requête de l'API `2026-03-11` vérifiée dans la documentation Notion (bases à sources de données : `initial_data_source`, schéma lu via `data_sources`), et des messages d'erreur en français pour 401 (token invalide) et 404 (page non partagée avec l'intégration) ; testé avec `requests` mocké
- [ ] T009 — Reconnaître un premier lancement et fournir les exclusions par défaut · fichiers : `pc/user_config.py`, `pc/test_user_config.py` (nouveau) · fini quand : `est_premier_lancement()` vaut vrai si aucun token Notion n'est enregistré et que l'assistant n'a jamais été terminé (clé `assistant_termine`), et `EXCLUSIONS_PAR_DEFAUT` porte la liste du cahier (todo, off, perso, privé, prive, déjeuner, dejeuner, trajet, ne pas enregistrer) au format de la clé `auto_start_exclusions` (une ligne par motif) ; testé sur un `settings.json` temporaire (patch de `_PATH`/`_DIR`)
- [ ] T010 — Détecter l'agenda disponible · fichiers : `pc/assistant_agenda.py` (nouveau), `pc/test_assistant_agenda.py` (nouveau) · fini quand : une fonction rend l'un de trois modes — Outlook classique (COM `Outlook.Application` réellement joignable, le Nouvel Outlook échoue proprement), EWS (identifiants présents, via `outlook_cal._get_ews_credentials`), manuel — avec la phrase en clair à afficher pour chacun ; testé avec `win32com` et `keyring` mockés
- [ ] T011 — Mesurer le micro et le son du PC pendant 5 secondes · fichiers : `pc/assistant_audio.py` (nouveau), `pc/test_assistant_audio.py` (nouveau) · fini quand : le calcul de niveau (même formule que `_push_level`, `meetnote-tray.py:494`) et le verdict sont des fonctions pures testées ; la capture micro passe par `sounddevice`, le son du PC par le loopback WASAPI pendant que l'assistant joue lui-même un son court (le loopback bloque quand rien ne joue), avec délai maximal ; le module importe `sounddevice` avant `soundcard` ; verdict : micro nul → refus, son du PC nul → avertissement en clair
- [ ] T012 — Afficher l'écran Notion de l'assistant · fichiers : `pc/assistant_fenetre.py` (nouveau) · fini quand : une fenêtre `Toplevel` aux couleurs des paramètres (`meetnote-tray.py:4296`) propose l'écran 1 — lien de création d'intégration + 3 lignes d'explication (dont « partager la page avec l'intégration »), champ token masqué, champ lien de page parente **ou** de base existante — et, sur « Suivant », crée ou vérifie la base puis envoie la page de test, dans un thread, avec le résultat affiché en clair ; rien n'est enregistré tant que le test d'envoi n'a pas réussi ; la logique vient de `assistant_notion.py`
- [ ] T013 — Ajouter les écrans agenda, exclusions et test audio, et terminer l'assistant · fichiers : `pc/assistant_fenetre.py` · fini quand : l'écran 2 affiche le mode détecté par `assistant_agenda`, l'écran 3 un champ multi-ligne prérempli avec `EXCLUSIONS_PAR_DEFAUT` (ou les exclusions déjà enregistrées), l'écran 4 lance le test de 5 s avec deux vu-mètres et bloque « Terminer » sur un micro nul ; « Terminer » enregistre token, identifiant de base, exclusions et `assistant_termine` via `user_config.save`
- [ ] T014 — Brancher l'assistant dans l'application · fichiers : `pc/meetnote-tray.py` · fini quand : au démarrage, si `est_premier_lancement()`, l'assistant s'ouvre après `_build_window()` (`main()`, l. ~4932), y compris en `--minimized` ; à la fin, `config.NOTION_*`, `notion_push.HEADERS` et `_auto_start_exclusions` sont recalés comme le fait déjà `_save_notion`/`_save_ews` ; l'onglet Général des paramètres porte un bouton « Relancer l'assistant » qui ferme les paramètres puis l'ouvre ; aucun test audio n'est lancé pendant un enregistrement

## Bloqué
_(ce qui attend une réponse sur l'un des quatre cas autorisés)_

## Lot D — durcissement, fin de projet (NON BLOQUANT)
_(rempli par /securiser, jamais en cours de lot)_
