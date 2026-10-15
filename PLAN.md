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
- [ ] T001 🔒 — Relire et commiter le socle actuel, puis poser l'étiquette `v1.0.0-base` · fichiers : les 7 fichiers modifiés (`CLAUDE.md`, `pc/CHANGELOG.md`, `pc/build_spec.py`, `pc/build_spec_slim.py`, `pc/meetnote-tray.py`, `pc/meetnote.spec`, `pc/whisper_local.py`) + les 3 non suivis `pc/perf_profile.py`, `pc/test_perf_profile.py`, `pc/test_transcription_parallele.py` · fini quand : `git status` ne montre plus de modification sous `pc/` et `git tag` liste `v1.0.0-base` — commit et étiquette sur feu vert de l'utilisateur, à faire avant de lancer `/avancer`
- [x] T002 — Rendre les portes exécutables · fichiers : `ruff.toml` (nouveau, racine — reprend la sélection de règles résolue aujourd'hui, `ruff check --show-settings`, et exclut `.claude/`, `_archive-*/`, `pwa/`), `.claude/kit.json` (porte `test` → `python -m unittest discover -s pc -p "test_*.py"`), `pc/test_transcription_parallele.py` (ignoré sauf si `MEETNOTE_TEST_LENT=1`) · fini quand : la porte test passe en moins d'une minute et `ruff check .` ne remonte plus que des constats de `pc/`
- [x] T003 — Ramener `ruff check .` à zéro sur `pc/` · fichiers : `pc/*.py`, `ruff.toml` · fini quand : les deux portes sont vertes ; corrections sûres appliquées (`I001`, `F401`, `F541`, `RUF100`, `F841`), règles héritées (`BLE001`, `S110`, `DTZ005`, `PLW1510`…) neutralisées par `per-file-ignores` sur les seuls fichiers existants, chaque ligne commentée « dette héritée »
- [x] T004 — Créer une source unique de version et l'afficher dans la fenêtre « À propos » · fichiers : `pc/version.py` (nouveau, `VERSION = "3.3.0"`), `pc/meetnote-tray.py` (l. 102 et `_open_version_window`, titre « À propos »), `pc/CHANGELOG.md` (entrée `[3.3.0]`), `pc/test_version.py` (nouveau) · fini quand : la fenêtre ouverte depuis le menu affiche « MeetNote v3.3.0 » et un test vérifie que `VERSION` est au format x.y.z et a son entrée dans le CHANGELOG
- [x] T005 — Retirer le chemin Python en dur des scripts de build · fichiers : `pc/build_spec.py` (l. 56-58 : `sys.base_prefix`, surchargeable par `MEETNOTE_PYTHON_HOME`), `pc/build_spec_slim.py` si concerné, `pc/test_build_spec.py` (nouveau) · fini quand : aucune occurrence de `apottier` dans `build_spec*.py` et un test couvre la résolution (défaut + variable d'environnement)
- [ ] T006 — Inscrire la version dans les propriétés de `MeetNote.exe` et dans le nom du dossier livré · fichiers : `pc/build_spec.py` (génère la ressource de version PyInstaller depuis `pc/version.py`, sortie `dist\MeetNote-<version>\`), `pc/test_build_spec.py` · fini quand : un test vérifie le contenu de la ressource générée ; recette manuelle : clic droit sur l'exe → Propriétés → Détails affiche 3.3.0, comme la fenêtre « À propos »

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

## Découvert en route
- `pc/meetnote.spec` est un fichier généré par `build_spec.py`, suivi par git, et contient des chemins personnels (`C:/Users/apottier/...`). Le sortir du suivi relève de la question « suppression ».
- En mode figé, `_get_startup_cmd()` (`pc/meetnote-tray.py:4257`) ne passe pas par le watchdog : à traiter en L4.
- `.claude/journal-qualite.md` : 48 constats en attente.
- La fenêtre « À propos » affiche v3.3.0 au-dessus des notes `RELEASE_NOTES` embarquées dans `meetnote-tray.py`, figées à la 3.1 : elles ne suivent pas `pc/CHANGELOG.md`.

## Bloqué
_(ce qui attend une réponse sur l'un des quatre cas autorisés)_

## Lot D — durcissement, fin de projet (NON BLOQUANT)
_(rempli par /securiser, jamais en cours de lot)_
