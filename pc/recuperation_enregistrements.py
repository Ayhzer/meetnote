"""
Détection des enregistrements interrompus par un arrêt brutal.

Pendant un enregistrement, l'audio est écrit au fil de l'eau dans
`<TEMP_DIR>/rec_AAAAMMJJ_HHMMSS.wav`, et l'en-tête WAV est recalé toutes les 5 s :
après un plantage natif ou un `taskkill` (installeur), le fichier reste donc lisible
mais n'est référencé par aucun job. Ce module repère ces fichiers ; il ne fait que
LIRE : il ne supprime, ne déplace ni ne modifie jamais rien. Mettre le job en file
est l'affaire de l'appelant.

Module pur (sans tkinter ni état global) : l'horloge (secondes epoch, comme
`time.time`) et le listage du dossier sont injectables pour les tests.
"""
import dataclasses
import datetime
import os
import re
import time
import wave

PREFIXE_ENREGISTREMENT = "rec_"
FORMAT_HORODATAGE = "%Y%m%d_%H%M%S"
_MOTIF_NOM = re.compile(r"^rec_(\d{8}_\d{6})\.wav$", re.IGNORECASE)

# En-tête WAV canonique : un fichier qui n'a pas dépassé cette taille
# ne contient aucun échantillon.
TAILLE_ENTETE_WAV = 44
# Un fichier modifié plus récemment est peut-être encore écrit par une autre instance.
AGE_MINIMUM_S = 10
# Préfixe des archives définitives (voir `_archive_audio`) :
# `meetnote_<horodatage>[_titre].<ext>`.
PREFIXE_ARCHIVE = "meetnote_"


@dataclasses.dataclass(frozen=True)
class Candidat:
    """Un enregistrement à reprendre."""
    chemin: str
    debut: datetime.datetime
    duree_s: float


@dataclasses.dataclass(frozen=True)
class Ecarte:
    """Un fichier `rec_*.wav` laissé en place, avec la raison à journaliser."""
    chemin: str
    raison: str


def normaliser_chemin(chemin: str) -> str:
    """Forme comparable de deux chemins : absolu, séparateurs et casse normalisés."""
    return os.path.normcase(os.path.abspath(chemin))


def extraire_date(nom_fichier: str) -> datetime.datetime | None:
    """Date de début tirée de `rec_AAAAMMJJ_HHMMSS.wav`, ou None si absente."""
    trouve = _MOTIF_NOM.match(nom_fichier)
    if not trouve:
        return None
    try:
        # Heure locale naïve voulue : c'est celle du nom de fichier.
        return datetime.datetime.strptime(  # noqa: DTZ007
            trouve.group(1), FORMAT_HORODATAGE)
    except ValueError:
        return None


def titre_recupere(debut: datetime.datetime) -> str:
    """Titre par défaut d'un job récupéré."""
    return f"Enregistrement récupéré du {debut.strftime('%d/%m/%Y %H:%M')}"


def lire_duree(chemin: str) -> tuple[float | None, str]:
    """Durée en secondes d'après l'en-tête WAV : (durée, "") ou (None, raison)."""
    try:
        with wave.open(chemin, "rb") as wav:
            frames = wav.getnframes()
            cadence = wav.getframerate()
    except (wave.Error, EOFError, OSError) as exc:
        return None, f"en-tête WAV illisible ({type(exc).__name__})"
    if cadence <= 0:
        return None, "fréquence d'échantillonnage invalide"
    if frames <= 0:
        return None, "aucun échantillon audio"
    return frames / cadence, ""


def _est_deja_archive(debut: datetime.datetime, noms_archives) -> bool:
    prefixe = f"{PREFIXE_ARCHIVE}{debut.strftime(FORMAT_HORODATAGE)}".lower()
    return any(nom.lower().startswith(prefixe) for nom in noms_archives)


def _examiner(chemin, debut, maintenant, references, debuts_references,
              noms_archives):
    """Candidat, Ecarte, ou None si le fichier est déjà pris en charge."""
    if normaliser_chemin(chemin) in references:
        return None
    if debut is not None and (debut in debuts_references
                              or _est_deja_archive(debut, noms_archives)):
        # Un job (ou son archive) existe déjà pour cet enregistrement : le WAV de tmp
        # n'est qu'un reliquat, pas une nouvelle réunion.
        return None
    if debut is None:
        return Ecarte(chemin, "date illisible dans le nom du fichier")
    try:
        taille = os.path.getsize(chemin)
        age_s = maintenant - os.path.getmtime(chemin)
    except OSError as exc:
        return Ecarte(chemin, f"fichier inaccessible ({type(exc).__name__})")
    if taille <= TAILLE_ENTETE_WAV:
        return Ecarte(chemin, f"fichier vide ({taille} octets)")
    if age_s < AGE_MINIMUM_S:
        return Ecarte(chemin, f"modifié depuis moins de {AGE_MINIMUM_S} s")
    duree, raison = lire_duree(chemin)
    if duree is None:
        return Ecarte(chemin, raison)
    return Candidat(chemin, debut, duree)


def chercher(dossier: str, chemins_references=(), debuts_references=(),
             noms_archives=(), maintenant=time.time,
             lister=os.listdir) -> tuple[list[Candidat], list[Ecarte]]:
    """Cherche dans `dossier` les `rec_*.wav` orphelins.

    chemins_references : `wav_path` de tous les jobs de l'historique.
    debuts_references  : `start_time` des jobs (un job existe pour cet enregistrement
                         même si son `wav_path` pointe désormais vers l'archive).
    noms_archives      : noms de fichiers du dossier d'archives audio.
    Retourne (candidats du plus ancien au plus récent, fichiers écartés avec raison).
    Un dossier absent ou illisible donne deux listes vides. Les autres fichiers
    (`whisper_*.wav`, etc.) sont ignorés sans bruit.
    """
    try:
        noms = lister(dossier)
    except OSError:
        return [], []
    references = {normaliser_chemin(c) for c in chemins_references if c}
    debuts = {d.replace(microsecond=0) for d in debuts_references}
    archives = list(noms_archives)
    instant = maintenant()
    candidats: list[Candidat] = []
    ecartes: list[Ecarte] = []
    for nom in sorted(noms):
        bas = nom.lower()
        if not (bas.startswith(PREFIXE_ENREGISTREMENT) and bas.endswith(".wav")):
            continue
        resultat = _examiner(os.path.join(dossier, nom), extraire_date(nom),
                             instant, references, debuts, archives)
        if isinstance(resultat, Candidat):
            candidats.append(resultat)
        elif isinstance(resultat, Ecarte):
            ecartes.append(resultat)
    candidats.sort(key=lambda c: c.debut)
    return candidats, ecartes
