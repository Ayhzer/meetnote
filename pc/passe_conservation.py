"""
Passe de conservation : applique `retention.py` en supprimant vraiment.

Ce module orchestre sans rien connaître de tkinter, du disque ni de Windows : la
liste des fichiers, la suppression (Corbeille), la mise à jour de l'historique et le
journal sont injectés. Il est donc testable avec des jobs factices.

Une passe : (1) calcule tout ce qui est à supprimer, (2) envoie chaque fichier à la
Corbeille, (3) met à jour les jobs. Un échec sur un fichier est compté et journalisé,
il n'interrompt jamais la passe. Chaque chemin est revérifié sous l'un des trois
dossiers de MeetNote juste avant d'être supprimé (défense en profondeur : le calcul
de `retention.py` est déjà borné, mais une erreur de ce côté ne doit jamais atteindre
un fichier extérieur). Le journal ne reçoit que des noms de fichiers et des règles,
jamais le contenu d'une transcription.
"""
import dataclasses
import datetime
import os
from collections.abc import Callable, Iterable

import retention

MOTIF_HORS_DOSSIERS = "chemin hors des dossiers de MeetNote"
TAILLE_INCONNUE = "taille inconnue"
_OCTETS_PAR_KO = 1024
_OCTETS_PAR_MO = 1024 * 1024


def taille_lisible(octets: int) -> str:
    """Taille en Ko ou en Mo, pour repérer un fichier trop gros pour la Corbeille."""
    if octets >= _OCTETS_PAR_MO:
        return f"{octets / _OCTETS_PAR_MO:.1f} Mo"
    return f"{max(1, round(octets / _OCTETS_PAR_KO))} Ko" if octets else "0 Ko"


@dataclasses.dataclass
class BilanPasse:
    """Compteurs d'une passe."""
    audios: int = 0
    transcriptions: int = 0
    orphelins: int = 0
    echecs: int = 0

    @property
    def a_change(self) -> bool:
        """Vrai si l'historique ou le disque a été modifié."""
        return bool(self.audios or self.transcriptions or self.orphelins)

    def resume(self) -> str:
        return (f"Conservation : {self.audios} audio(s), {self.transcriptions} "
                f"transcription(s), {self.orphelins} orphelin(s) supprimés, "
                f"{self.echecs} échec(s)")


class _Passe:
    """État d'une passe en cours (bilan, dépendances injectées)."""

    def __init__(self, dossiers: tuple[str, str, str],
                 supprimer: Callable[[str], str | None],
                 mettre_a_jour: Callable[[object], None], journal, existe,
                 jour: str, taille: Callable[[str], int] = os.path.getsize):
        self.dossiers = dossiers
        self.taille = taille
        self.supprimer = supprimer
        self.mettre_a_jour = mettre_a_jour
        self.journal = journal
        self.existe = existe
        self.jour = jour
        self.bilan = BilanPasse()

    def envoyer(self, chemin: str, regle: str) -> bool:
        """Envoie le fichier à la Corbeille ; False (échec compté) s'il est refusé."""
        nom = os.path.basename(chemin)
        if not any(retention.est_sous_dossier(chemin, d) for d in self.dossiers):
            return self._echec(nom, MOTIF_HORS_DOSSIERS)
        taille = self._taille_avant_suppression(chemin)
        try:
            motif = self.supprimer(chemin)
        except Exception as exc:  # noqa: BLE001 - un échec ne doit pas stopper la passe
            motif = f"{type(exc).__name__}: {exc}"
        if motif:
            return self._echec(nom, motif)
        self.journal.info("Corbeille : %s (%s, %s)", nom, regle, taille)
        return True

    def _taille_avant_suppression(self, chemin: str) -> str:
        """Taille lue juste avant l'envoi : Windows supprime pour de bon un fichier
        trop gros pour la Corbeille, la ligne de journal doit le rendre repérable."""
        try:
            return taille_lisible(self.taille(chemin))
        except OSError:
            return TAILLE_INCONNUE

    def _echec(self, nom: str, motif: str) -> bool:
        self.bilan.echecs += 1
        self.journal.warning("Corbeille : échec pour %s (%s)", nom, motif)
        return False

    def enregistrer(self, job) -> None:
        """Met l'historique à jour ; un échec d'écriture est compté, pas propagé."""
        try:
            self.mettre_a_jour(job)
        except OSError as exc:
            self.bilan.echecs += 1
            self.journal.warning("Historique non mis à jour (%s)", type(exc).__name__)

    def audios(self, a_supprimer: list[retention.AudioASupprimer]) -> None:
        for audio in a_supprimer:
            if self.existe(audio.chemin):
                if not self.envoyer(audio.chemin, audio.regle):
                    continue
                self.bilan.audios += 1
            else:
                # Déjà disparu (supprimé à la main) : on le consigne une fois pour
                # toutes, sinon chaque passe le retenterait indéfiniment.
                self.journal.info("Audio déjà absent : %s (%s)",
                                  os.path.basename(audio.chemin), audio.regle)
            audio.job.audio_supprime_le = self.jour
            self.enregistrer(audio.job)

    def transcriptions(self, a_purger: list[retention.TranscriptionAPurger]) -> None:
        for purge in a_purger:
            if purge.chemin and self.existe(purge.chemin):
                if not self.envoyer(purge.chemin, purge.regle):
                    continue  # le .txt est encore là : on ne vide pas l'historique
            else:
                self.journal.info("Transcription purgée de l'historique : %s (%s)",
                                  getattr(purge.job, "id", "?"), purge.regle)
            for champ in retention.champs_a_vider_purge():
                valeur = getattr(purge.job, champ, "")
                setattr(purge.job, champ, [] if isinstance(valeur, list) else "")
            purge.job.transcript_purge_le = self.jour
            self.bilan.transcriptions += 1
            self.enregistrer(purge.job)

    def transcriptions_differees(self, jobs: list) -> None:
        """Journalise les purges différées : identifiant seul, jamais de contenu."""
        for job in jobs:
            self.journal.info("purge différée : transcription non transmise (%s)",
                              getattr(job, "id", "?"))

    def orphelins(self, orphelins: list[retention.Orphelin]) -> None:
        for orphelin in orphelins:
            if self.envoyer(orphelin.chemin, orphelin.regle):
                self.bilan.orphelins += 1


def executer_passe(jobs: Iterable, maintenant: datetime.datetime,
                   retention_audio_jours: int, retention_transcript_jours: int,
                   dossier_archives: str, dossier_transcripts: str, dossier_tmp: str,
                   lister: Callable[[str], list[str]],
                   supprimer: Callable[[str], str | None],
                   mettre_a_jour: Callable[[object], None], journal,
                   ids_occupes: Iterable[str] = (),
                   enregistrement_en_cours: bool = False,
                   existe: Callable[[str], bool] = os.path.exists,
                   taille: Callable[[str], int] = os.path.getsize) -> BilanPasse:
    """Exécute une passe de conservation et rend son bilan.

    `jobs` sont modifiés en place (`audio_supprime_le`, `transcript_purge_le`, champs
    vidés) puis passés à `mettre_a_jour`. `supprimer(chemin)` rend None si le fichier
    est parti à la Corbeille, sinon le motif de l'échec. `lister(dossier)` rend les
    noms de fichiers du dossier ; `journal` est un `logging.Logger`. Pendant un
    enregistrement, aucun audio n'est supprimé (règle de `retention.py`).
    `taille(chemin)` rend la taille en octets, journalisée à chaque envoi.
    """
    liste_jobs = list(jobs)
    occupes = list(ids_occupes)
    noms_archives = lister(dossier_archives)
    noms_transcripts = lister(dossier_transcripts)
    noms_tmp = lister(dossier_tmp)

    audios = retention.audios_a_supprimer(
        liste_jobs, maintenant, retention_audio_jours, dossier_archives,
        retention.existence_par_noms(noms_transcripts), occupes,
        enregistrement_en_cours)
    purges = retention.transcriptions_a_purger(
        liste_jobs, maintenant, retention_transcript_jours, dossier_transcripts,
        occupes)
    differees = retention.transcriptions_differees(
        liste_jobs, maintenant, retention_transcript_jours, occupes)
    orphelins = retention.orphelins(
        liste_jobs, maintenant, retention_audio_jours, retention_transcript_jours,
        dossier_archives, noms_archives, dossier_transcripts, noms_transcripts,
        dossier_tmp, noms_tmp)
    if enregistrement_en_cours:
        # Ne rien toucher à `tmp/` ni aux archives pendant qu'un enregistrement écrit.
        orphelins = []

    passe = _Passe((dossier_archives, dossier_transcripts, dossier_tmp), supprimer,
                   mettre_a_jour, journal, existe, maintenant.date().isoformat(),
                   taille)
    passe.audios(audios)
    passe.transcriptions(purges)
    passe.transcriptions_differees(differees)
    passe.orphelins(orphelins)
    journal.info(passe.bilan.resume())
    return passe.bilan
