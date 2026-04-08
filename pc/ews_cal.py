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
        Credentials, Account, Configuration, DELEGATE, NTLM,
        EWSDateTime,
    )
    _AVAILABLE = True
except ImportError:
    _AVAILABLE = False

_IGNORE = {"off", "absent", "busy", "ooo", "out of office", "hors bureau"}


def _to_naive_local(ews_dt) -> datetime.datetime:
    """Convertit un EWSDateTime (UTC aware) en datetime naïf heure locale."""
    return datetime.datetime.fromtimestamp(ews_dt.timestamp())


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
        creds = Credentials(username=username, password=password)

        if ews_url:
            cfg = Configuration(
                service_endpoint=ews_url,
                credentials=creds,
                auth_type=NTLM,
            )
            account = Account(
                primary_smtp_address=email,
                config=cfg,
                autodiscover=False,
                access_type=DELEGATE,
            )
        else:
            account = Account(
                primary_smtp_address=email,
                credentials=creds,
                autodiscover=True,
                access_type=DELEGATE,
            )

        now = datetime.datetime.now()
        end_window = now + datetime.timedelta(minutes=window_minutes)

        tz = account.default_timezone
        now_ews = EWSDateTime.from_datetime(
            now.replace(microsecond=0)
        ).replace(tzinfo=tz)
        end_ews = EWSDateTime.from_datetime(
            end_window.replace(microsecond=0)
        ).replace(tzinfo=tz)

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


def is_available() -> bool:
    """Retourne True si exchangelib est installé."""
    return _AVAILABLE
