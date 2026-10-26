"""
Lecture du calendrier Exchange on-premise via EWS (exchangelib + NTLM).
Utilisé comme alternative au COM/MAPI pour le Nouvel Outlook.
Silencieux si exchangelib non installé ou serveur inaccessible.
"""
import datetime
import logging

log = logging.getLogger(__name__)

try:
    from exchangelib import (
        DELEGATE,
        NTLM,
        Account,
        Configuration,
        Credentials,
        EWSDateTime,
    )
    _AVAILABLE = True
except ImportError:
    _AVAILABLE = False

_IGNORE = {"off", "absent", "busy", "ooo", "out of office", "hors bureau"}
# Valeur exchangelib de `item.sensitivity` pour « Privé » (choix : Normal, Personal, Private,
# Confidential). « Confidential » n'est pas masqué : simple étiquette, cf. outlook_cal.
_SENSIBILITE_PRIVE = "private"


def _to_naive_local(ews_dt) -> datetime.datetime:
    """Convertit un EWSDateTime (UTC aware) en datetime naïf heure locale."""
    return datetime.datetime.fromtimestamp(ews_dt.timestamp())


def _make_account(email: str, username: str, password: str, ews_url: str) -> "Account":
    """Crée un objet Account exchangelib (EWS ou autodiscover)."""
    creds = Credentials(username=username, password=password)
    if ews_url:
        cfg = Configuration(
            service_endpoint=ews_url,
            credentials=creds,
            auth_type=NTLM,
        )
        return Account(
            primary_smtp_address=email,
            config=cfg,
            autodiscover=False,
            access_type=DELEGATE,
        )
    return Account(
        primary_smtp_address=email,
        credentials=creds,
        autodiscover=True,
        access_type=DELEGATE,
    )


def _make_ews_window(window_minutes: int):
    """Retourne (now_ews, end_ews) en UTC, compatible exchangelib ≥ 3."""
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    end_utc = now_utc + datetime.timedelta(minutes=window_minutes)
    now_ews = EWSDateTime.from_datetime(now_utc)
    end_ews = EWSDateTime.from_datetime(end_utc)
    return now_ews, end_ews


def get_current_or_next_meeting(
    email: str,
    username: str,
    password: str,
    ews_url: str = "",
    window_minutes: int = 30,
) -> dict | None:
    """
    Cherche via EWS le RDV en cours ou démarrant dans les 'window_minutes' prochaines minutes.
    Retourne {"subject": str, "start": datetime, "end": datetime} ou None.
    """
    if not _AVAILABLE:
        log.debug("ews_cal: exchangelib non installé")
        return None

    if not email or not username or not password:
        return None

    try:
        account = _make_account(email, username, password, ews_url)
        now_ews, end_ews = _make_ews_window(window_minutes)

        for item in account.calendar.view(start=now_ews, end=end_ews):
            try:
                subject = str(item.subject or "").strip()

                if getattr(item, "is_all_day", False):
                    continue
                if not subject or subject.lower() in _IGNORE:
                    continue

                start = _to_naive_local(item.start)
                end   = _to_naive_local(item.end)

                duration_h = (end - start).total_seconds() / 3600
                if duration_h > 12:
                    continue

                return {"subject": subject, "start": start, "end": end}
            except Exception as e:
                log.debug("ews_cal: item skip (%s)", e)
                continue

    except Exception as e:
        log.debug("ews_cal: unavailable (%s)", e)

    return None


def get_all_current_meetings(
    email: str,
    username: str,
    password: str,
    ews_url: str = "",
    window_minutes: int = 30,
) -> list:
    """
    Retourne TOUTES les réunions en cours ou démarrant dans les 'window_minutes' prochaines minutes.
    Chaque item : {"subject": str, "start": datetime, "end": datetime}
    """
    if not _AVAILABLE:
        log.debug("ews_cal: exchangelib non installé")
        return []

    if not email or not username or not password:
        return []

    try:
        account = _make_account(email, username, password, ews_url)
        now_ews, end_ews = _make_ews_window(window_minutes)

        results = []
        for item in account.calendar.view(start=now_ews, end=end_ews):
            try:
                subject = str(item.subject or "").strip()

                if getattr(item, "is_all_day", False):
                    continue
                if not subject or subject.lower() in _IGNORE:
                    continue

                start = _to_naive_local(item.start)
                end   = _to_naive_local(item.end)

                if (end - start).total_seconds() / 3600 > 12:
                    continue

                results.append({"subject": subject, "start": start, "end": end})
            except Exception as e:
                log.debug("ews_cal: item skip (%s)", e)
        return results

    except Exception as e:
        log.debug("ews_cal: unavailable (%s)", e)
        return None   # None = connexion EWS impossible → le caller tente COM/MAPI

    return []


def _names(coll) -> list:
    """Noms (ou adresses à défaut) des participants d'une collection EWS."""
    if not coll:
        return []
    try:
        return [str(a.mailbox.name or a.mailbox.email_address or "") for a in coll]
    except Exception:
        return []


def _est_prive(item) -> bool:
    """Vrai si la réunion est marquée « Privé » ; toute lecture impossible vaut False."""
    try:
        return str(getattr(item, "sensitivity", "") or "").strip().lower() == _SENSIBILITE_PRIVE
    except Exception as e:
        log.debug("ews_cal: sensibilité illisible (%s)", e)
        return False


def _evenement_depuis_item(item) -> dict | None:
    """Convertit un item EWS en dict d'agenda, ou None s'il est à ignorer
    (journée entière, objet vide ou « off/absent/ooo… », plus de 12 h)."""
    subject = str(item.subject or "").strip()
    if getattr(item, "is_all_day", False):
        return None
    if not subject or subject.lower() in _IGNORE:
        return None
    start = _to_naive_local(item.start)
    end   = _to_naive_local(item.end)
    if (end - start).total_seconds() / 3600 > 12:
        return None

    # Corps du message — nettoyage basique
    raw_body = str(getattr(item, "text_body", "") or
                   getattr(item, "body", "") or "").strip()
    body = " ".join(raw_body.split())[:600]

    return {
        "subject":      subject,
        "start":        start,
        "end":          end,
        "location":     str(getattr(item, "location", "") or "").strip(),
        "body":         body,
        "organizer":    str(getattr(item.organizer, "name", "") or "").strip()
                        if getattr(item, "organizer", None) else "",
        "required":     _names(getattr(item, "required_attendees", None)),
        "optional":     _names(getattr(item, "optional_attendees", None)),
        "is_recurring": bool(getattr(item, "is_recurring", False)),
        "prive":        _est_prive(item),
    }


def _vers_ews_datetime(naif_local: datetime.datetime):
    """Datetime naïf local → EWSDateTime UTC (un naïf est lu en heure locale)."""
    return EWSDateTime.from_datetime(naif_local.astimezone(datetime.timezone.utc))


def evenements(
    email: str,
    username: str,
    password: str,
    ews_url: str,
    debut: datetime.datetime,
    fin: datetime.datetime,
) -> list | None:
    """
    Retourne les RDV de la fenêtre [debut, fin] (datetimes naïfs locaux), au format
    de `get_events_range`, triés par début.
    Retourne None si EWS est injoignable (exchangelib absent, identifiants
    manquants, connexion en échec) : None n'est jamais « aucune réunion », qui est [].
    """
    if not _AVAILABLE:
        log.debug("ews_cal: exchangelib non installé")
        return None
    if not email or not username or not password:
        return None

    try:
        account = _make_account(email, username, password, ews_url)
        vue = account.calendar.view(
            start=_vers_ews_datetime(debut), end=_vers_ews_datetime(fin),
        )

        results = []
        for item in vue:
            try:
                evt = _evenement_depuis_item(item)
            except Exception as e:
                log.debug("ews_cal: evenements item skip (%s)", e)
                continue
            if evt is not None:
                results.append(evt)
        results.sort(key=lambda e: e["start"])
        return results

    except Exception as e:
        log.debug("ews_cal: evenements unavailable (%s)", e)
        return None


def get_events_range(
    email: str,
    username: str,
    password: str,
    ews_url: str = "",
    days: int = 15,
) -> list | None:
    """
    Retourne tous les RDV des 'days' prochains jours (à partir de maintenant).
    Chaque item : {
        "subject", "start", "end",
        "location", "body", "organizer",
        "required": [str], "optional": [str],
        "is_recurring": bool, "prive": bool,
    }
    [] si exchangelib ou les identifiants manquent ; None si la connexion EWS
    échoue (le caller tente alors COM/MAPI).
    """
    if not _AVAILABLE:
        return []
    if not email or not username or not password:
        return []
    # Heure locale naïve, sans datetime.now() nu (la fenêtre est lue en local).
    maintenant = datetime.datetime.now(datetime.timezone.utc).astimezone().replace(tzinfo=None)
    return evenements(email, username, password, ews_url,
                      maintenant, maintenant + datetime.timedelta(days=days))


def is_available() -> bool:
    """Retourne True si exchangelib est installé."""
    return _AVAILABLE
