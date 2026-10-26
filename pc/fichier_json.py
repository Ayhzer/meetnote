"""
fichier_json.py - Lecture/ecriture JSON sure, partagee par user_config et history.

- ecrire_atomique : temporaire du meme dossier + os.replace, jamais de fichier tronque.
- lire_ou_ecarter : un fichier illisible est renomme (jamais supprime), jamais ecrase.
"""
import json
import logging
import os
import tempfile
from datetime import datetime

_log = logging.getLogger("meetnote")


def ecrire_atomique(chemin: str, data, **options_json) -> None:
    """Ecrit `data` en JSON dans `chemin` de facon atomique.

    Leve l'exception d'origine en cas d'echec ; l'ancien fichier reste alors intact
    et le temporaire est supprime.
    """
    dossier = os.path.dirname(chemin)
    os.makedirs(dossier, exist_ok=True)
    nom = os.path.splitext(os.path.basename(chemin))[0]
    fd, tmp_path = tempfile.mkstemp(dir=dossier, prefix=f"{nom}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, **options_json)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, chemin)
    except BaseException:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise


def _ecarter(chemin: str) -> None:
    """Renomme un fichier illisible en .corrompu-AAAAMMJJ-HHMMSS.

    Le contenu n'est pas journalise : il peut contenir un secret.
    """
    nom = os.path.basename(chemin)
    cible = f"{chemin}.corrompu-{datetime.now().astimezone():%Y%m%d-%H%M%S}"
    try:
        os.replace(chemin, cible)
        _log.warning("%s illisible : conserve sous %s, valeurs par defaut utilisees",
                     nom, cible)
    except OSError as exc:
        _log.warning("%s illisible et non renomme (%s) : valeurs par defaut utilisees",
                     nom, exc)


def lire_ou_ecarter(chemin: str, type_attendu: type):
    """Lit le JSON de `chemin`. Rend None si le fichier est absent ou a ete ecarte.

    Absent : aucun bruit. Illisible ou racine d'un autre type que `type_attendu` :
    fichier renomme + WARNING sur le logger `meetnote`.
    """
    try:
        with open(chemin, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, type_attendu):
            raise TypeError("racine JSON inattendue")
    except FileNotFoundError:
        return None
    except (ValueError, TypeError):  # JSONDecodeError et UnicodeDecodeError : ValueError
        _ecarter(chemin)
        return None
    return data
