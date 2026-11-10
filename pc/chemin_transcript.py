"""
Choix du nom de fichier d'une transcription locale, sans jamais en écraser une autre.

Le nom de base est `transcript_<AAAAMMJJ_HHMMSS>.txt`, tiré de l'heure de début du job
(motif reconnu par `recuperation_enregistrements`, qui teste le préfixe). Deux jobs
peuvent avoir la même heure de début — import Teams de deux réunions sur le même
créneau d'agenda, toujours rond — : le second reçoit alors un suffixe `_2`, `_3`…
placé après l'horodatage, donc compatible avec ce motif.

Module pur (sans tkinter ni état global) : testable avec un dossier temporaire.
"""
import datetime
import os

from recuperation_enregistrements import FORMAT_HORODATAGE, PREFIXE_TRANSCRIPT

EXTENSION_TRANSCRIPT = ".txt"


def _meme_chemin(a: str, b: str) -> bool:
    """Deux chemins désignent-ils le même fichier (casse et séparateurs normalisés) ?"""
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def choisir_chemin_transcript(dossier: str, debut: datetime.datetime,
                              chemin_propre: str = "") -> str:
    """Chemin libre pour la transcription d'un job démarré à `debut`.

    `chemin_propre` est le fichier déjà attribué à ce job : une régénération le
    réécrit tel quel. Tout autre fichier existant est laissé intact.
    """
    base = f"{PREFIXE_TRANSCRIPT}{debut.strftime(FORMAT_HORODATAGE)}"
    rang = 1
    while True:
        suffixe = "" if rang == 1 else f"_{rang}"
        chemin = os.path.join(dossier, f"{base}{suffixe}{EXTENSION_TRANSCRIPT}")
        if not os.path.exists(chemin):
            return chemin
        if chemin_propre and _meme_chemin(chemin, chemin_propre):
            return chemin
        rang += 1
