"""
Délais et nettoyage des conversions ffmpeg (archive Opus, décodage pour Whisper).

Un délai fixe de 300 s tronquait l'archive d'un enregistrement de 9 h 21 à 7 h 49
(27/10/2026, sur batterie) : ffmpeg tué, l'Opus partiel restait dans `audio` comme
s'il était complet. Le délai suit désormais la durée de l'audio, et une sortie
partielle est retirée.

Module pur (sans tkinter ni état global), testable sans ffmpeg.
"""
import logging
import os

from recuperation_enregistrements import lire_duree

_log = logging.getLogger(__name__)

# Plancher : les enregistrements courts gardent le délai historique.
DELAI_MINIMUM_S = 300
# Vitesse de conversion la plus lente tolérée, en multiple du temps réel.
# Mesuré le 27/10/2026 : ~94x pour l'encodage Opus, sur batterie ; 10x laisse
# une marge de près de dix fois avant de déclarer ffmpeg bloqué.
VITESSE_MINIMALE = 10
# Débit le plus bas des sources non WAV (archives Opus 24 kb/s), en octets par
# seconde : estimer la durée d'un fichier compressé par sa taille la surestime
# pour tout format plus riche, donc allonge le délai, jamais ne le raccourcit.
DEBIT_MINIMAL_OCTETS_S = 3000


def duree_estimee_s(chemin: str) -> float:
    """Durée d'après l'en-tête WAV, à défaut d'après la taille du fichier."""
    duree, _ = lire_duree(chemin)
    if duree is not None:
        return duree
    try:
        return os.path.getsize(chemin) / DEBIT_MINIMAL_OCTETS_S
    except OSError:
        return 0.0


def delai_conversion_s(duree_s: float) -> float:
    """Délai accordé à ffmpeg pour convertir `duree_s` secondes d'audio."""
    return max(float(DELAI_MINIMUM_S), duree_s / VITESSE_MINIMALE)


def retirer_sortie_partielle(chemin: str) -> bool:
    """Supprime la sortie d'une conversion échouée ; True si un fichier a été retiré.

    Ne concerne que le fichier que ffmpeg était en train d'écrire : la source n'est
    jamais touchée par l'appelant.
    """
    if not os.path.isfile(chemin):
        return False
    try:
        os.remove(chemin)
    except OSError as exc:
        _log.warning("Sortie partielle non retirée : %s (%s)", chemin, exc)
        return False
    return True
