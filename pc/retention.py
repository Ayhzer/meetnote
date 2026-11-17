"""
Calcul de ce qui doit être supprimé au titre de la conservation bornée (RGPD).

Module PUR : aucun accès disque, aucune horloge. Tout est passé en argument
(`maintenant`, listes de noms de fichiers, dossiers, durées en jours, prédicat
d'existence). Il ne supprime rien : il rend des décisions, chacune accompagnée de la
règle appliquée (texte court, destiné au journal). Supprimer — vers la Corbeille — et
mettre l'historique à jour est l'affaire de l'appelant.

Les « jobs » sont n'importe quel objet portant les attributs de `_Job`
(`meetnote-tray.py`) : lus par `getattr`, avec une valeur par défaut sûre. Ce ne sont
PAS des dicts d'historique. Les dates sont des `datetime` naïfs en heure locale, comme
`start_time` et les horodatages des noms de fichiers.

Règles (« plus de N jours » = strictement au-delà : à N jours pile, rien n'est
supprimé) :
  1. audio d'un job transcrit : N = `retention_audio_jours` après la fin de la
     transcription ;
  2. transcription d'un job : N = `retention_transcript_jours` après la réunion ;
  3. fichiers sans job (sortis des 100 entrées de l'historique), d'après la date
     de leur nom.
"""
import dataclasses
import datetime
import os
import re
from collections.abc import Callable, Iterable

from chemin_transcript import EXTENSION_TRANSCRIPT
from recuperation_enregistrements import (
    FORMAT_HORODATAGE,
    PREFIXE_ARCHIVE,
    PREFIXE_ENREGISTREMENT,
    PREFIXE_TRANSCRIPT,
    normaliser_chemin,
)

RETENTION_AUDIO_JOURS_DEFAUT = 30
RETENTION_TRANSCRIPT_JOURS_DEFAUT = 180

# Champs d'historique vidés lors d'une purge de transcription. Titre, date, durée,
# statuts et lien Notion ne sont volontairement pas dedans : ils restent.
CHAMPS_PURGE_TRANSCRIPT = (
    "transcript",
    "whisper_transcript",
    "teams_transcript",
    "notes",
    "teams_participants",
    "participants_invites",
)

EXTENSIONS_ARCHIVE = (".opus", ".wav")
EXTENSION_ENREGISTREMENT = ".wav"
_MOTIF_HORODATAGE = r"(\d{8}_\d{6})"


@dataclasses.dataclass(frozen=True)
class AudioASupprimer:
    """Audio d'un job à envoyer à la Corbeille."""
    job: object
    chemin: str
    regle: str


@dataclasses.dataclass(frozen=True)
class TranscriptionAPurger:
    """Transcription d'un job à purger.

    `chemin` est le `.txt` à supprimer, ou "" s'il est absent ou hors du dossier des
    transcriptions : l'historique est alors purgé sans toucher à aucun fichier.
    """
    job: object
    chemin: str
    regle: str


@dataclasses.dataclass(frozen=True)
class Orphelin:
    """Fichier qu'aucun job ne référence, à envoyer à la Corbeille."""
    chemin: str
    regle: str


def champs_a_vider_purge() -> tuple[str, ...]:
    """Noms des champs de job à remettre à vide lors d'une purge de transcription."""
    return CHAMPS_PURGE_TRANSCRIPT


def est_sous_dossier(chemin: str, dossier: str) -> bool:
    """Le chemin est-il strictement sous le dossier (casse et séparateurs normalisés) ?

    Un chemin ou un dossier vide et un composant `..` dans le chemin sont refusés.
    Le dossier n'est pas « sous » lui-même.
    """
    if not chemin or not dossier:
        return False
    if ".." in re.split(r"[\\/]", chemin):
        return False
    racine = normaliser_chemin(dossier).rstrip("\\/")
    return normaliser_chemin(chemin).startswith(racine + os.sep)


def _ecoule(reference: datetime.datetime, maintenant: datetime.datetime,
            jours: int) -> bool:
    """Plus de `jours` jours entre `reference` et `maintenant` (limite exclue)."""
    return maintenant - reference > datetime.timedelta(days=jours)


def _debut(job) -> datetime.datetime | None:
    debut = getattr(job, "start_time", None)
    return debut if isinstance(debut, datetime.datetime) else None


def _fin_transcription(job) -> datetime.datetime | None:
    """Référence de la rétention audio : début de la réunion + sa durée."""
    debut = _debut(job)
    if debut is None:
        return None
    try:
        minutes = float(getattr(job, "duration_min", 0) or 0)
    except (TypeError, ValueError):
        minutes = 0.0
    return debut + datetime.timedelta(minutes=minutes)


def existence_par_noms(noms_presents: Iterable[str]) -> Callable[[str], bool]:
    """Prédicat d'existence bâti sur la liste des noms de fichiers d'un dossier.

    Compare le nom de base, sans tenir compte de la casse (Windows).
    """
    presents = {nom.lower() for nom in noms_presents}
    return lambda chemin: os.path.basename(chemin).lower() in presents


def audios_a_supprimer(jobs: Iterable, maintenant: datetime.datetime,
                       retention_audio_jours: int, dossier_archives: str,
                       transcript_existe: Callable[[str], bool],
                       ids_occupes: Iterable[str] = (),
                       enregistrement_en_cours: bool = False) -> list[AudioASupprimer]:
    """Audios archivés dont la durée de conservation est dépassée.

    Un audio n'est retenu que si le job a une transcription terminée, non vide ET
    toujours présente sur disque (`transcript_existe`, cf. `existence_par_noms`) :
    jamais d'audio supprimé sans qu'il reste de quoi le remplacer. Sont exclus : un
    audio déjà supprimé, un job en file ou en cours (`ids_occupes`), un `wav_path`
    hors du dossier d'archives, et tout, tant qu'un enregistrement est en cours.
    """
    if enregistrement_en_cours:
        return []
    occupes = set(ids_occupes)
    retenus = []
    for job in jobs:
        if getattr(job, "id", None) in occupes:
            continue
        if getattr(job, "audio_supprime_le", ""):
            continue
        if getattr(job, "status_transcript", "") != "done":
            continue
        if not (getattr(job, "transcript", "") or "").strip():
            continue
        chemin_transcript = getattr(job, "transcript_path", "")
        if not chemin_transcript or not transcript_existe(chemin_transcript):
            continue
        wav_path = getattr(job, "wav_path", "")
        if not est_sous_dossier(wav_path, dossier_archives):
            continue
        fin = _fin_transcription(job)
        if fin is None or not _ecoule(fin, maintenant, retention_audio_jours):
            continue
        retenus.append(AudioASupprimer(
            job, wav_path,
            f"audio de plus de {retention_audio_jours} j après la transcription"))
    return retenus


def transcriptions_a_purger(jobs: Iterable, maintenant: datetime.datetime,
                            retention_transcript_jours: int,
                            dossier_transcripts: str,
                            ids_occupes: Iterable[str] = ()
                            ) -> list[TranscriptionAPurger]:
    """Jobs dont la réunion a plus de `retention_transcript_jours` jours.

    Exclus : un job déjà purgé (`transcript_purge_le`) et un job en file ou en cours
    (`ids_occupes`) — on ne vide pas ce que le worker est en train d'écrire.
    """
    occupes = set(ids_occupes)
    retenus = []
    for job in jobs:
        if getattr(job, "id", None) in occupes:
            continue
        if getattr(job, "transcript_purge_le", ""):
            continue
        debut = _debut(job)
        if debut is None or not _ecoule(debut, maintenant, retention_transcript_jours):
            continue
        chemin = getattr(job, "transcript_path", "")
        if not est_sous_dossier(chemin, dossier_transcripts):
            chemin = ""
        retenus.append(TranscriptionAPurger(
            job, chemin, f"transcription de plus de {retention_transcript_jours} j "
                         "après la réunion"))
    return retenus


def _horodatage_du_nom(nom: str, prefixe: str,
                       extensions: tuple[str, ...]) -> str | None:
    """Horodatage `AAAAMMJJ_HHMMSS` d'un nom `<prefixe><horodatage>[suffixe]<ext>`."""
    motif = (f"^{re.escape(prefixe)}{_MOTIF_HORODATAGE}.*"
             f"({'|'.join(re.escape(e) for e in extensions)})$")
    trouve = re.match(motif, nom, re.IGNORECASE)
    if not trouve:
        return None
    try:
        # Validité de la date : 20261399_999999 ne doit pas être « daté ».
        datetime.datetime.strptime(  # noqa: DTZ007
            trouve.group(1), FORMAT_HORODATAGE)
    except ValueError:
        return None
    return trouve.group(1)


def _date_horodatage(horodatage: str) -> datetime.datetime:
    # Heure locale naïve voulue : c'est celle du nom de fichier.
    return datetime.datetime.strptime(horodatage, FORMAT_HORODATAGE)  # noqa: DTZ007


def _references(jobs: list) -> tuple[set[str], set[str]]:
    """Chemins normalisés référencés par les jobs, et horodatages de leurs réunions."""
    chemins = set()
    horodatages = set()
    for job in jobs:
        for champ in ("wav_path", "transcript_path"):
            chemin = getattr(job, champ, "")
            if chemin:
                chemins.add(normaliser_chemin(chemin))
        debut = _debut(job)
        if debut is not None:
            horodatages.add(debut.strftime(FORMAT_HORODATAGE))
    return chemins, horodatages


def _transcription_presente(horodatage: str, noms_transcripts: list[str]) -> bool:
    attendu = f"{PREFIXE_TRANSCRIPT}{horodatage}".lower()
    return any(nom.lower().startswith(attendu) for nom in noms_transcripts)


def _orphelins_du_dossier(dossier: str, noms: Iterable[str], prefixe: str,
                          extensions: tuple[str, ...], chemins_job: set[str],
                          horodatages_job: set[str] | None,
                          jours_pour: Callable[[str], tuple[int, str]],
                          maintenant: datetime.datetime) -> list[Orphelin]:
    """Orphelins d'un dossier. `horodatages_job` None : seul le chemin protège."""
    retenus = []
    for nom in sorted(noms):
        horodatage = _horodatage_du_nom(nom, prefixe, extensions)
        if horodatage is None:
            continue  # nom sans date lisible : jamais retenu
        chemin = os.path.join(dossier, nom)
        if normaliser_chemin(chemin) in chemins_job:
            continue
        if horodatages_job is not None and horodatage in horodatages_job:
            continue
        jours, regle = jours_pour(horodatage)
        if _ecoule(_date_horodatage(horodatage), maintenant, jours):
            retenus.append(Orphelin(chemin, regle))
    return retenus


def orphelins(jobs: Iterable, maintenant: datetime.datetime,
              retention_audio_jours: int, retention_transcript_jours: int,
              dossier_archives: str, noms_archives: Iterable[str],
              dossier_transcripts: str, noms_transcripts: Iterable[str],
              dossier_tmp: str, noms_tmp: Iterable[str]) -> list[Orphelin]:
    """Fichiers sans job des trois dossiers de MeetNote, d'après la date de leur nom.

    Un fichier d'archives ou de transcriptions est orphelin si aucun job ne le
    référence (`wav_path`/`transcript_path`) NI ne partage son horodatage (début de
    réunion). Pour `tmp/`, seule la référence par chemin protège : un `rec_*.wav` dont
    le job existe déjà n'est qu'un reliquat de l'enregistrement, pas à garder.

    Durées : audio d'archives à `retention_audio_jours` si une transcription de même
    horodatage est présente, sinon à `retention_transcript_jours` ; `rec_*.wav` de
    `tmp/` à `retention_audio_jours` ; transcription à `retention_transcript_jours`.
    """
    liste_jobs = list(jobs)
    chemins, horodatages = _references(liste_jobs)
    transcripts = list(noms_transcripts)

    def jours_archive(horodatage: str) -> tuple[int, str]:
        if _transcription_presente(horodatage, transcripts):
            regle = (f"audio sans job de plus de {retention_audio_jours} j "
                     "(transcription présente)")
            return retention_audio_jours, regle
        regle = (f"audio sans job de plus de {retention_transcript_jours} j "
                 "(sans transcription)")
        return retention_transcript_jours, regle

    def jours_tmp(_horodatage: str) -> tuple[int, str]:
        return (retention_audio_jours,
                f"enregistrement temporaire de plus de {retention_audio_jours} j")

    def jours_transcript(_horodatage: str) -> tuple[int, str]:
        return (retention_transcript_jours,
                f"transcription sans job de plus de {retention_transcript_jours} j")

    return (
        _orphelins_du_dossier(dossier_archives, noms_archives, PREFIXE_ARCHIVE,
                              EXTENSIONS_ARCHIVE, chemins, horodatages,
                              jours_archive, maintenant)
        + _orphelins_du_dossier(dossier_transcripts, transcripts, PREFIXE_TRANSCRIPT,
                                (EXTENSION_TRANSCRIPT,), chemins, horodatages,
                                jours_transcript, maintenant)
        + _orphelins_du_dossier(dossier_tmp, noms_tmp, PREFIXE_ENREGISTREMENT,
                                (EXTENSION_ENREGISTREMENT,), chemins, None,
                                jours_tmp, maintenant)
    )
