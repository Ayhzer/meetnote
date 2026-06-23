"""
calendar_digest.py — Génère et pousse un agenda prévisionnel 15 jours vers Notion.

Format transcript optimisé pour le prompt secrétariat Claude.

Fonctions publiques :
  run_digest(days=15)  → construit le transcript, le pousse vers Notion, retourne l'URL.
  build_transcript(events, days)  → retourne uniquement le texte formaté (pour tests/debug).
"""
import sys
import os
import datetime
import json
import logging

sys.path.insert(0, os.path.dirname(__file__))
import config
import outlook_cal
from notion_push import push_to_notion

log = logging.getLogger(__name__)

# Fichier d'état pour le mécanisme anti-doublon quotidien
_STATE_FILE = os.path.join(os.path.expanduser("~"), "Documents", "MeetNote", "agenda_digest_state.json")

_JOURS_FR   = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]
_MOIS_FR    = ["", "janvier", "février", "mars", "avril", "mai", "juin",
               "juillet", "août", "septembre", "octobre", "novembre", "décembre"]


# ─────────────────────────────────────────────────────────────────────────────
# État (last-run date)
# ─────────────────────────────────────────────────────────────────────────────

def get_last_run_date() -> datetime.date | None:
    """Retourne la date de la dernière exécution, ou None si jamais exécuté."""
    try:
        with open(_STATE_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return datetime.date.fromisoformat(data.get("last_run_date", ""))
    except Exception:
        return None


def _save_last_run_date(d: datetime.date):
    os.makedirs(os.path.dirname(_STATE_FILE), exist_ok=True)
    with open(_STATE_FILE, "w", encoding="utf-8") as f:
        json.dump({"last_run_date": d.isoformat()}, f)


def digest_already_run_today() -> bool:
    last = get_last_run_date()
    return last == datetime.date.today()


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
    Chaque RDV     :
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
                lines.append(
                    f"  {time_range}  {_fmt_duration(dur_min)}  |  {ev['subject']}"
                )

                loc = ev.get("location", "").strip() or "(aucun)"
                lines.append(f"  Lieu        : {loc}")

                org = ev.get("organizer", "").strip() or "(non renseigné)"
                lines.append(f"  Organisateur: {org}")

                req  = [n for n in ev.get("required", []) if n]
                opt  = [n for n in ev.get("optional", []) if n]
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

def run_digest(days: int = 15, force: bool = False) -> str | None:
    """
    Récupère les RDV, formate le transcript, pousse vers Notion.
    Retourne l'URL Notion de la page créée, ou None en cas d'erreur.
    force=True : ignore le check "déjà fait aujourd'hui".
    """
    if not force and digest_already_run_today():
        log.info("calendar_digest: digest déjà effectué aujourd'hui, ignoré.")
        return None

    log.info("calendar_digest: récupération des RDV (%d jours)…", days)
    events = outlook_cal.get_events_range(days=days)
    log.info("calendar_digest: %d RDV récupérés.", len(events))

    now        = datetime.datetime.now()
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
