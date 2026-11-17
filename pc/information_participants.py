"""Confirmation « participants informés » : horodatage et mise en forme.

Fonctions pures, sans tkinter ni disque : testables seules. Le moment de la
confirmation est une chaîne ISO à la seconde (`""` = non confirmé), celle que
porte `_Job.participants_informes_le`.
"""
import datetime

LIBELLE_CASE = "J'ai informé les participants"
_PREFIXE = "Participants informés : "
_NON_CONFIRME = _PREFIXE + "non confirmé"


def horodatage_iso(maintenant: datetime.datetime) -> str:
    """Heure locale sérialisée en ISO, à la seconde (ex. « 2026-11-17T14:32:05 »)."""
    return maintenant.replace(microsecond=0).isoformat()


def ligne_en_tete(iso: str) -> str:
    """Ligne d'en-tête du transcript local : « oui (le JJ/MM/AAAA à HH:MM) » ou non.

    Une chaîne vide ou une date illisible valent « non confirmé » : on n'affirme
    jamais une information que l'on ne peut pas dater.
    """
    try:
        moment = datetime.datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return _NON_CONFIRME
    return f"{_PREFIXE}oui (le {moment:%d/%m/%Y} à {moment:%H:%M})"


def ligne_pour_enregistrement(wav_path: str, iso: str) -> str:
    """Ligne d'en-tête d'un job, ou `""` s'il ne vient pas d'un enregistrement.

    Un import Teams, un mail ou un texte collé n'a pas de fichier audio
    (`wav_path` vide) : l'information des participants ne s'y pose pas.
    """
    if not wav_path:
        return ""
    return ligne_en_tete(iso)


# Clés de couleur des libellés de l'historique (résolues en teintes par l'IHM).
COULEUR_OK = "ok"
COULEUR_ALERTE = "alerte"
COULEUR_INFO = "info"


def _date_iso(iso: str):
    """Date ou datetime ISO -> datetime, ou None si vide/illisible."""
    try:
        return datetime.datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return None


def _libelle_suppression(prefixe: str, iso: str) -> str:
    """« <prefixe> le JJ/MM/AAAA » ; sans date, le fait reste affiché sans elle."""
    moment = _date_iso(iso)
    return f"{prefixe} le {moment:%d/%m/%Y}" if moment else prefixe


def libelles_historique(wav_path: str, informes_le: str,
                        audio_supprime_le: str, transcript_purge_le: str) -> list:
    """Libellés de la carte d'historique : liste de (texte, clé de couleur).

    - Confirmation : seulement pour un enregistrement (`wav_path` non vide) ;
      un import Teams, mail ou texte collé n'a rien à confirmer.
    - Suppressions : affichées dès que le champ est renseigné, quelle que soit
      l'origine du job.
    """
    libelles = []
    if wav_path:
        moment = _date_iso(informes_le)
        if moment:
            libelles.append((
                f"✓ Participants informés le {moment:%d/%m/%Y à %H:%M}", COULEUR_OK))
        else:
            libelles.append(("⚠ Information non confirmée", COULEUR_ALERTE))
    if audio_supprime_le:
        libelles.append((_libelle_suppression("Audio supprimé", audio_supprime_le),
                         COULEUR_INFO))
    if transcript_purge_le:
        libelles.append((_libelle_suppression("Transcription purgée", transcript_purge_le),
                         COULEUR_INFO))
    return libelles
