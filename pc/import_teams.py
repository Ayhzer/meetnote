"""
import_teams.py — Liste des réunions Teams récentes qui ont un transcript (Graph).

Alimente la fenêtre « Transcripts TEAMS » : lit l'agenda des derniers jours, garde les
réunions terminées qui portent un lien Teams, interroge Graph pour chacune et en
déduit un état. La logique (filtrage, état, identifiant stable) est en fonctions pures ;
`charger` est la seule qui fait des appels, et reçoit ses dépendances en paramètres
pour être testée sans réseau ni Outlook.
"""
import datetime
import hashlib
import logging
from collections.abc import Callable, Sequence
from typing import NamedTuple

import graph_auth
import graph_teams
import source_agenda
import transcript_teams
import user_config

_log = logging.getLogger("meetnote")

# Profondeur de lecture de l'agenda : les transcripts Teams ne restent pas disponibles
# indéfiniment et l'utilisateur importe en général dans les jours qui suivent.
JOURS_RECUL = 15
LONGUEUR_EMPREINTE = 8  # caractères hexadécimaux du SHA-1 conservés dans l'identifiant

ETAT_DISPONIBLE = "disponible"
ETAT_ABSENT = "absent"
ETAT_IMPORTE = "importe"

# Réunions listées mais jamais cochées d'office (constat P0 n° 4, 10/11/2026).
MOTIF_PRIVEE = "privée"
MOTIF_EXCLUE = "exclue"

LIBELLES_ETATS = {
    ETAT_DISPONIBLE: "transcript disponible",
    ETAT_ABSENT: "pas de transcript",
    ETAT_IMPORTE: "déjà importé",
}

MSG_NON_CONFIGURE = ("Microsoft Graph n'est pas configuré : renseignez l'identifiant "
                     "d'application et de locataire dans Paramètres.")
MSG_CONNEXION_REQUISE = " Connectez-vous depuis Paramètres, puis rouvrez cette fenêtre."
MSG_AGENDA_INJOIGNABLE = ("Agenda injoignable : ni Exchange (EWS) ni Outlook ne "
                          "répondent. Vérifiez votre connexion puis réessayez.")
MSG_AUCUNE_REUNION = (f"Aucune réunion Teams terminée dans les {JOURS_RECUL} derniers "
                      "jours.")

_STATUT_INTROUVABLE = 404

# Valeurs des jobs créés par l'import (même vocabulaire que le reste de l'application).
TYPE_REUNION_TEAMS = "Teams"
MODELE_TEAMS = "Teams"  # pas de Whisper ici : l'origine du texte tient lieu de modèle
SORTIE_NOTION = "notion"
SECONDES_PAR_MINUTE = 60


class LigneReunion(NamedTuple):
    """Une réunion de la liste, avec ce qu'il faut pour l'importer (T051)."""
    debut: datetime.datetime
    fin: datetime.datetime
    objet: str
    organisateur: str
    join_url: str
    etat: str
    reunion_id: str = ""     # identifiant Graph de la réunion en ligne
    transcript_id: str = ""  # transcript à importer ("" si aucun)
    job_id: str = ""         # identifiant stable du job d'import ("" si aucun)
    invites: tuple[str, ...] = ()  # invités de l'agenda, sans adresse e-mail
    motif_sensible: str = ""  # MOTIF_PRIVEE / MOTIF_EXCLUE, "" si la réunion est banale

    @property
    def cochable(self) -> bool:
        """Seule une réunion disponible et pas encore importée peut être cochée."""
        return self.etat == ETAT_DISPONIBLE

    @property
    def pre_cochee(self) -> bool:
        """Cochée d'office à l'ouverture : cochable et ni privée ni exclue. Une
        réunion sensible reste cochable à la main, mais jamais importée par défaut."""
        return self.cochable and not self.motif_sensible

    @property
    def libelle_etat(self) -> str:
        libelle = LIBELLES_ETATS[self.etat]
        if self.cochable and self.motif_sensible:
            return f"{libelle} · {self.motif_sensible} — non cochée"
        return libelle


class ResultatListe(NamedTuple):
    """`message` vide : liste complète. Sinon il explique pourquoi elle est
    vide ou partielle, en français, pour affichage tel quel."""
    lignes: list
    message: str = ""


def periode(maintenant: datetime.datetime) -> tuple[datetime.datetime,
                                                    datetime.datetime]:
    """Fenêtre d'agenda lue : de `JOURS_RECUL` jours avant `maintenant` à lui."""
    return maintenant - datetime.timedelta(days=JOURS_RECUL), maintenant


def reunions_a_interroger(evenements: Sequence[dict],
                          maintenant: datetime.datetime) -> list[dict]:
    """Événements terminés (fin <= maintenant) avec un lien Teams, récents d'abord."""
    retenus = [e for e in evenements
               if (e.get("join_url") or "").strip() and e["end"] <= maintenant]
    return sorted(retenus, key=lambda e: e["start"], reverse=True)


def identifiant_import(debut: datetime.datetime, transcript_id: str) -> str:
    """Identifiant stable du job : `<AAAAMMJJ_HHMMSS>_teams_<empreinte du transcript>`.

    Reproductible d'un lancement à l'autre : c'est ce qui permet de reconnaître un
    transcript déjà importé sans ajouter de champ à l'historique.
    """
    empreinte = hashlib.sha1(transcript_id.encode("utf-8"),
                             usedforsecurity=False).hexdigest()
    return f"{debut:%Y%m%d_%H%M%S}_teams_{empreinte[:LONGUEUR_EMPREINTE]}"


def identifiants_importes(entrees: Sequence[dict]) -> set[str]:
    """Identifiants des jobs de l'historique (`history.load()`)."""
    return {e["id"] for e in entrees if isinstance(e.get("id"), str)}


def choisir_transcript(debut: datetime.datetime, transcripts: Sequence[dict],
                       importes: set[str]) -> tuple[str, str, str]:
    """(état, transcript_id, job_id) pour une réunion.

    `transcripts` : du plus ancien au dernier créé. Si l'un d'eux est déjà importé la
    réunion l'est ; sinon c'est le dernier créé qui est proposé (arrêt et reprise de
    la transcription en cours de réunion : le dernier est le plus complet).
    """
    if not transcripts:
        return ETAT_ABSENT, "", ""
    for transcript in transcripts:
        job_id = identifiant_import(debut, transcript["id"])
        if job_id in importes:
            return ETAT_IMPORTE, transcript["id"], job_id
    dernier = transcripts[-1]["id"]
    return ETAT_DISPONIBLE, dernier, identifiant_import(debut, dernier)


def libelle_creneau(debut: datetime.datetime, fin: datetime.datetime) -> str:
    """« 10/11/2026 14:00–15:00 » pour l'affichage d'une ligne."""
    return f"{debut:%d/%m/%Y %H:%M}–{fin:%H:%M}"


def motif_sensible(evenement: dict, exclusions: Sequence[str]) -> str:
    """Motif pour lequel la réunion n'est pas cochée d'office, ou "".

    Même critère que le reste de l'application : « privée » = clé `prive` de
    l'événement (`calendar_digest`) ; « exclue » = l'objet, en minuscules, contient
    un motif de `exclusions` (déjà en minuscules, cf. `user_config.liste_exclusions`),
    comme `_is_auto_start_excluded` du tray, qui n'est pas importable.
    """
    if evenement.get("prive"):
        return MOTIF_PRIVEE
    objet = (evenement.get("subject") or "").lower()
    if any(motif in objet for motif in exclusions):
        return MOTIF_EXCLUE
    return ""


def _ligne(evenement: dict, etat: str, reunion_id: str = "", transcript_id: str = "",
           job_id: str = "", exclusions: Sequence[str] = ()) -> LigneReunion:
    return LigneReunion(evenement["start"], evenement["end"],
                        (evenement.get("subject") or "").strip() or "(sans objet)",
                        (evenement.get("organizer") or "").strip(),
                        evenement["join_url"].strip(), etat,
                        reunion_id, transcript_id, job_id,
                        tuple(source_agenda.invites_de_reunion(evenement)),
                        motif_sensible(evenement, exclusions))


def _analyser_reunion(evenement: dict, jeton: str, importes: set[str], graphe,
                      reunions_connues: dict,
                      exclusions: Sequence[str] = ()) -> LigneReunion:
    """État d'une réunion. Une réunion inconnue de Graph est « sans transcript » ;
    toute autre `ErreurGraph` remonte. `reunions_connues` évite de redemander l'id
    d'une série récurrente dont toutes les occurrences portent le même lien."""
    url = evenement["join_url"].strip()
    if url not in reunions_connues:
        reunions_connues[url] = graphe.reunion_en_ligne(url, jeton)
    reunion_id = reunions_connues[url]
    if not reunion_id:
        return _ligne(evenement, ETAT_ABSENT, exclusions=exclusions)
    try:
        trouves = graphe.transcripts(reunion_id, evenement["start"],
                                     evenement["end"], jeton)
    except graph_teams.ErreurGraph as exc:
        if exc.statut != _STATUT_INTROUVABLE:
            raise
        trouves = []
    etat, transcript_id, job_id = choisir_transcript(evenement["start"], trouves,
                                                     importes)
    return _ligne(evenement, etat, reunion_id, transcript_id, job_id, exclusions)


def charger(maintenant: datetime.datetime, importes: set[str],
            sources: Sequence | None = None,
            obtenir_jeton: Callable = graph_auth.obtenir_jeton,
            graphe=graph_teams,
            progression: Callable[[int, int], None] | None = None,
            exclusions: Sequence[str] | None = None) -> ResultatListe:
    """Liste des réunions Teams récentes avec leur état. Appels réseau : hors thread UI.

    `progression(fait, total)` est appelée après chaque réunion interrogée. Une panne
    Graph en cours de route rend les lignes déjà établies et le message d'erreur.
    `exclusions` : motifs d'objet jamais cochés d'office ; par défaut, ceux des
    réglages (`auto_start_exclusions`).
    """
    if exclusions is None:
        exclusions = user_config.liste_exclusions(
            user_config.get("auto_start_exclusions", ""))
    if not graph_auth.identifiants_saisis(user_config.get("graph_client_id", ""),
                                          user_config.get("graph_tenant_id", "")):
        return ResultatListe([], MSG_NON_CONFIGURE)
    resultat = obtenir_jeton()
    if not resultat.jeton:
        return ResultatListe([], graph_auth.message_connexion(resultat)
                             + "." + MSG_CONNEXION_REQUISE)
    debut, fin = periode(maintenant)
    chaine = source_agenda.sources_par_defaut() if sources is None else sources
    retenue = source_agenda.premiere_source(chaine, debut, fin)
    if retenue is None:
        return ResultatListe([], MSG_AGENDA_INJOIGNABLE)
    a_interroger = reunions_a_interroger(retenue[1], maintenant)
    if not a_interroger:
        return ResultatListe([], MSG_AUCUNE_REUNION)
    lignes, reunions_connues = [], {}
    for rang, evenement in enumerate(a_interroger, start=1):
        try:
            lignes.append(_analyser_reunion(evenement, resultat.jeton, importes,
                                            graphe, reunions_connues, exclusions))
        except graph_teams.ErreurGraph as exc:
            _log.warning("[Import Teams] arrêt après %d/%d réunion(s) : %s",
                         rang - 1, len(a_interroger), exc)
            return ResultatListe(lignes, f"Microsoft Graph : {exc}")
        if progression is not None:
            progression(rang, len(a_interroger))
    return ResultatListe(lignes)



class ResultatImport(NamedTuple):
    """`jobs` : paramètres de `_Job` prêts à l'emploi ; `erreurs` : une phrase par
    réunion non importée (objet et cause, en français, pour affichage tel quel)."""
    jobs: list
    erreurs: list


def duree_minutes(debut: datetime.datetime, fin: datetime.datetime) -> float:
    """Durée de la réunion en minutes (jamais négative)."""
    secondes = (fin - debut).total_seconds()
    return max(0.0, round(secondes / SECONDES_PAR_MINUTE, 1))


def parametres_job(ligne: LigneReunion, texte: str, mode_sortie: str) -> dict:
    """Paramètres d'un job « transcript seul » pour une réunion Teams. Fonction pure.

    Pas d'audio (`wav_path` vide) ni de transcription à faire : le job entre dans la
    file à l'étape Notion (ou fichier). La ligne « Participants » ne porte que des
    noms, jamais d'adresse e-mail ; l'identifiant stable rend l'import idempotent.
    """
    notion = mode_sortie == SORTIE_NOTION
    return {
        "id": ligne.job_id,
        "wav_path": "",
        "start_time": ligne.debut,
        "duration_min": duree_minutes(ligne.debut, ligne.fin),
        "model_name": MODELE_TEAMS,
        "language": "",
        "meeting_type": TYPE_REUNION_TEAMS,
        "output_mode": mode_sortie,
        "meeting_name": ligne.objet,
        "participants_invites": list(ligne.invites),
        "status_audio": "done",
        "status_transcript": "done",
        "status_notion": "pending" if notion else "skipped",
        "transcript": source_agenda.avec_ligne_participants(texte, ligne.invites),
    }


def preparer_imports(lignes: Sequence[LigneReunion], jeton: str, mode_sortie: str,
                     graphe=graph_teams) -> ResultatImport:
    """Télécharge et convertit le transcript de chaque réunion cochée. Appels réseau :
    hors thread UI. Une réunion en échec (erreur Graph, transcript vide) est signalée
    dans `erreurs` sans empêcher les suivantes."""
    jobs, erreurs = [], []
    for ligne in lignes:
        try:
            vtt = graphe.contenu(ligne.reunion_id, ligne.transcript_id, jeton)
        except graph_teams.ErreurGraph as exc:
            # Journal d'accès : identifiant et code seulement, jamais le message brut.
            _log.warning("[Import Teams] transcript non lu : %s (HTTP %s, %s)",
                         ligne.job_id, exc.statut or "-", exc.code or "-")
            erreurs.append(f"{ligne.objet} : {exc}")
            continue
        texte = transcript_teams.vtt_vers_texte(vtt)
        if not texte.strip():
            _log.info("[Import Teams] transcript lu mais vide : %s", ligne.job_id)
            erreurs.append(f"{ligne.objet} : le transcript est vide")
            continue
        _log.info("[Import Teams] transcript lu : %s", ligne.job_id)
        jobs.append(parametres_job(ligne, texte, mode_sortie))
    return ResultatImport(jobs, erreurs)
