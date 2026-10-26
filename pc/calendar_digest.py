"""
calendar_digest.py — Génère et pousse un agenda prévisionnel 15 jours vers Notion.

Format transcript optimisé pour le prompt secrétariat Claude.

Fonctions publiques :
  run_digest(days=15)  → construit le transcript, le pousse vers Notion, retourne l'URL.
  build_transcript(events, days)  → retourne uniquement le texte formaté (pour tests/debug).
"""
import datetime
import logging
import os
import sys
from collections.abc import Callable, Sequence

sys.path.insert(0, os.path.dirname(__file__))
import fichier_json
import source_agenda

log = logging.getLogger(__name__)

# Journal de la chaîne de sources, partagé avec le cache d'agenda (lignes « [Agenda] … »).
log_agenda = logging.getLogger("meetnote.agenda")

# Fichier d'état pour le mécanisme anti-doublon quotidien
_STATE_FILE = os.path.join(os.path.expanduser("~"), "Documents", "MeetNote", "agenda_digest_state.json")
_CLE_DATE = "last_run_date"      # date du dernier succès
_CLE_ECHEC = "dernier_echec"     # heure du dernier échec « sources indisponibles »

# Délai avant de relire les sources après un échec (évite une lecture + un job par minute)
DELAI_NOUVEL_ESSAI = datetime.timedelta(minutes=30)


class SourcesIndisponibles(Exception):
    """Aucune source d'agenda n'a répondu ; `prochain_essai` = heure du prochain essai automatique."""

    def __init__(self, prochain_essai: datetime.datetime):
        super().__init__(f"Agenda injoignable, nouvel essai à {prochain_essai:%H:%M}")
        self.prochain_essai = prochain_essai

# Objet affiché à la place de celui d'une réunion marquée privée dans Outlook
_OBJET_PRIVE = "Privé"

_JOURS_FR   = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]
_MOIS_FR    = ["", "janvier", "février", "mars", "avril", "mai", "juin",
               "juillet", "août", "septembre", "octobre", "novembre", "décembre"]


# ─────────────────────────────────────────────────────────────────────────────
# État (last-run date)
# ─────────────────────────────────────────────────────────────────────────────

def _lire_etat() -> dict:
    """Contenu du fichier d'état ({} s'il est absent ou illisible, ce dernier étant mis de côté)."""
    return fichier_json.lire_ou_ecarter(_STATE_FILE, dict) or {}


def get_last_run_date() -> datetime.date | None:
    """Retourne la date de la dernière exécution, ou None si jamais exécuté."""
    try:
        return datetime.date.fromisoformat(_lire_etat().get("last_run_date", ""))
    except (TypeError, ValueError):
        return None


def _save_last_run_date(d: datetime.date):
    """Enregistre la date de succès ; efface au passage l'échec « sources indisponibles »."""
    fichier_json.ecrire_atomique(_STATE_FILE, {_CLE_DATE: d.isoformat()})


def _memoriser_echec_sources(maintenant: datetime.datetime) -> None:
    """Mémorise l'heure d'un échec « sources indisponibles », sans toucher à la date de succès."""
    etat = _lire_etat()
    etat[_CLE_ECHEC] = maintenant.isoformat(timespec="seconds")
    try:
        fichier_json.ecrire_atomique(_STATE_FILE, etat)
    except OSError as e:
        log.warning("calendar_digest: échec d'écriture de l'état (%s)", e)


def dernier_echec_sources() -> datetime.datetime | None:
    """Heure du dernier échec « sources indisponibles », ou None (absent d'un ancien fichier)."""
    try:
        return datetime.datetime.fromisoformat(_lire_etat().get(_CLE_ECHEC, ""))
    except (TypeError, ValueError):
        return None


def prochain_essai(maintenant: datetime.datetime) -> datetime.datetime | None:
    """Heure du prochain essai autorisé après un échec récent, ou None s'il n'y a pas de délai en cours."""
    echec = dernier_echec_sources()
    if echec is None or not echec <= maintenant < echec + DELAI_NOUVEL_ESSAI:
        return None
    return echec + DELAI_NOUVEL_ESSAI


def digest_already_run_today(aujourdhui: datetime.date | None = None) -> bool:
    return get_last_run_date() == (aujourdhui or datetime.date.today())


def notion_configure(token: str | None, database_id: str | None) -> bool:
    """Vrai si le jeton Notion ET l'identifiant de base sont renseignés (non vides).

    Sans eux le push échoue en 401 et laisse un job « Agenda 15 j » en erreur dans
    l'historique : le digest ne doit alors ni partir ni créer de job. Ce n'est pas un
    échec de sources, donc aucun délai de nouvel essai n'est mémorisé.
    """
    return bool((token or "").strip()) and bool((database_id or "").strip())


def digest_a_tenter(maintenant: datetime.datetime | None = None) -> bool:
    """Vrai si le digest n'est pas fait aujourd'hui et qu'aucun échec récent n'impose d'attendre."""
    maintenant = maintenant or datetime.datetime.now()
    return not digest_already_run_today(maintenant.date()) and prochain_essai(maintenant) is None


# ─────────────────────────────────────────────────────────────────────────────
# Formatage du transcript
# ─────────────────────────────────────────────────────────────────────────────

def _fmt_date_long(d: datetime.date) -> str:
    """Ex: 'Lundi 17 avril 2026'"""
    return f"{_JOURS_FR[d.weekday()]} {d.day} {_MOIS_FR[d.month]} {d.year}"


def _fmt_week_header(monday: datetime.date) -> str:
    """Ex: 'SEMAINE 17 — 14 au 18 avril 2026'"""
    sunday  = monday + datetime.timedelta(days=6)
    week_no = monday.isocalendar()[1]
    if monday.month == sunday.month:
        span = f"{monday.day} au {sunday.day} {_MOIS_FR[monday.month]} {monday.year}"
    else:
        span = f"{monday.day} {_MOIS_FR[monday.month]} au {sunday.day} {_MOIS_FR[sunday.month]} {sunday.year}"
    return f"SEMAINE {week_no} — {span}"


def _fmt_duration(minutes: float) -> str:
    h = int(minutes) // 60
    m = int(minutes) % 60
    if h and m:
        return f"{h}h{m:02d}"
    if h:
        return f"{h}h"
    return f"{m}min"


def build_transcript(events: list, days: int = 15, generated_at: datetime.datetime = None) -> str:
    """
    Construit le transcript agenda au format secrétariat.

    Format spécifié (à intégrer dans le prompt secrétariat) :
    ──────────────────────────────────────────────────────────
    Bloc d'en-tête : ligne "AGENDA PRÉVISIONNEL — Du … au …"
    Chaque semaine : ligne "SEMAINE N — …"
    Chaque jour    : ligne "▸ JOUR JJ mois AAAA" puis 0..N blocs RDV
    Chaque RDV     : (réunion privée : seule la première ligne, objet « Privé »)
      [HH:MM → HH:MM]  Xmin|XhY  |  <Sujet>
      Lieu        : <lieu ou "(aucun)">
      Organisateur: <nom ou "(non renseigné)">
      Participants: <Nom1 ; Nom2> (requis) / <Nom1> (optionnel)
      Récurrent   : oui / non
      Description : <texte tronqué à 600 caractères>  (ligne omise si vide)
    ──────────────────────────────────────────────────────────
    """
    now    = generated_at or datetime.datetime.now()
    today  = now.date()
    end_d  = today + datetime.timedelta(days=days - 1)

    # Index des events par date
    by_date: dict[datetime.date, list] = {}
    for ev in events:
        d = ev["start"].date()
        by_date.setdefault(d, []).append(ev)
    for d in by_date:
        by_date[d].sort(key=lambda e: e["start"])

    lines = []
    lines.append("=" * 62)
    lines.append(f"AGENDA PRÉVISIONNEL — Du {_fmt_date_long(today)}")
    lines.append(f"                      au {_fmt_date_long(end_d)}")
    lines.append(f"Généré le : {now.strftime('%d/%m/%Y à %H:%M')} | Source : Outlook")
    lines.append("=" * 62)

    # Parcours jour par jour, groupé par semaine
    current_monday = None
    current_date = today
    while current_date <= end_d:
        # Entête de semaine (lundi)
        monday = current_date - datetime.timedelta(days=current_date.weekday())
        if monday != current_monday:
            current_monday = monday
            lines.append("")
            lines.append(_fmt_week_header(monday))
            lines.append("─" * 50)

        # Entête du jour
        lines.append(f"▸ {_fmt_date_long(current_date).upper()}")

        day_events = by_date.get(current_date, [])
        if not day_events:
            lines.append("  (aucun rendez-vous)")
        else:
            for ev in day_events:
                dur_min = (ev["end"] - ev["start"]).total_seconds() / 60
                time_range = (f"[{ev['start'].strftime('%H:%M')} → "
                              f"{ev['end'].strftime('%H:%M')}]")
                if ev.get("prive"):
                    # Créneau seul : ni objet, ni lieu, ni corps, ni participants.
                    lines.append(f"  {time_range}  {_fmt_duration(dur_min)}  |  {_OBJET_PRIVE}")
                    lines.append("")
                    continue
                lines.append(
                    f"  {time_range}  {_fmt_duration(dur_min)}  |  {ev['subject']}"
                )

                loc = (ev.get("location") or "").strip() or "(aucun)"
                lines.append(f"  Lieu        : {loc}")

                org = (ev.get("organizer") or "").strip() or "(non renseigné)"
                lines.append(f"  Organisateur: {org}")

                req  = [n for n in (ev.get("required") or []) if n]
                opt  = [n for n in (ev.get("optional") or []) if n]
                if req:
                    lines.append(f"  Participants requis   : {' ; '.join(req)}")
                else:
                    lines.append("  Participants requis   : (aucun)")
                if opt:
                    lines.append(f"  Participants optionnels: {' ; '.join(opt)}")

                recur = "oui" if ev.get("is_recurring") else "non"
                lines.append(f"  Récurrent   : {recur}")

                body = (ev.get("body") or "").strip()
                if body:
                    # Retour à la ligne tous les ~90 chars pour lisibilité dans Notion
                    wrapped = []
                    while len(body) > 90:
                        cut = body.rfind(" ", 0, 90)
                        if cut == -1:
                            cut = 90
                        wrapped.append(body[:cut])
                        body = body[cut:].lstrip()
                    if body:
                        wrapped.append(body)
                    lines.append(f"  Description : {wrapped[0]}")
                    for w in wrapped[1:]:
                        lines.append(f"                {w}")

                lines.append("")  # ligne vide entre RDV

        lines.append("")
        current_date += datetime.timedelta(days=1)

    lines.append("=" * 62)
    lines.append("FIN DE L'AGENDA PRÉVISIONNEL")
    lines.append("=" * 62)
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# Point d'entrée principal
# ─────────────────────────────────────────────────────────────────────────────

def run_digest(days: int = 15, force: bool = False,
               sources: Sequence[source_agenda.Source] | None = None,
               horloge: Callable[[], datetime.datetime] = datetime.datetime.now) -> str | None:
    """
    Récupère les RDV, formate le transcript, pousse vers Notion.
    Retourne l'URL Notion de la page créée, ou None (déjà fait, délai de nouvel essai
    en cours, ou échec du push Notion).
    force=True : ignore le check "déjà fait aujourd'hui" et le délai de nouvel essai.

    Les RDV viennent de la chaîne de sources de `source_agenda` (index → EWS → COM),
    lue sur [maintenant, maintenant + days jours] sans toucher au cache des 24 h.
    Si aucune source ne répond, rien n'est poussé, la date du jour n'est pas enregistrée
    mais l'heure de l'échec l'est (nouvel essai automatique après DELAI_NOUVEL_ESSAI) et
    `SourcesIndisponibles` est levée ; une source qui répond « aucune réunion » donne en
    revanche un agenda vide, poussé normalement.
    """
    now = horloge()
    if not force and not digest_a_tenter(now):
        log.info("calendar_digest: digest déjà effectué aujourd'hui ou nouvel essai différé, ignoré.")
        return None

    # Importé ici : notion_push exige config.py dès son import, inutile pour construire l'agenda.
    from notion_push import push_to_notion

    log.info("calendar_digest: récupération des RDV (%d jours)…", days)
    chaine = source_agenda.sources_par_defaut() if sources is None else sources
    retenue = source_agenda.premiere_source(chaine, now, now + datetime.timedelta(days=days))
    if retenue is None:
        log_agenda.warning("[Agenda] digest source=aucune échec (rien poussé vers Notion) jours=%d", days)
        _memoriser_echec_sources(now)
        raise SourcesIndisponibles(now + DELAI_NOUVEL_ESSAI)
    nom_source, events = retenue
    log_agenda.info("[Agenda] digest source=%s réunions=%d jours=%d", nom_source, len(events), days)
    log.info("calendar_digest: %d RDV récupérés.", len(events))

    transcript = build_transcript(events, days=days, generated_at=now)

    today_str = now.strftime("%d/%m/%Y")
    title     = f"Agenda prévisionnel — {today_str}"

    # Sauvegarde avant le push pour éviter les doublons si le push est lent (> 60s)
    _save_last_run_date(now.date())

    try:
        page = push_to_notion(
            transcript=transcript,
            source="Agenda",
            meeting_type="Agenda",
            start_time=now,
            title_override=title,
        )
        url = page.get("url", "")
        log.info("calendar_digest: page Notion créée → %s", url)
        return url
    except Exception as e:
        log.error("calendar_digest: échec push Notion (%s)", e)
        return None
