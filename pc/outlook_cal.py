"""
Lecture du calendrier Outlook/Exchange.
Stratégie :
  1. EWS (exchangelib + NTLM) si des credentials EWS sont configurés
     → nécessaire avec le Nouvel Outlook (Exchange on-premise)
  2. COM/MAPI (win32com) en fallback
     → fonctionne avec l'Outlook classique
Silencieux si aucune méthode n'est disponible.
"""
import datetime
import logging

log = logging.getLogger(__name__)

try:
    import win32com.client
    _COM_AVAILABLE = True
except ImportError:
    _COM_AVAILABLE = False


def _to_naive_local(dt) -> datetime.datetime:
    """Convertit un pywintypes.datetime (heure locale mal étiquetée UTC) en datetime naïf local."""
    return datetime.datetime(dt.year, dt.month, dt.day, dt.hour, dt.minute, dt.second)


def _get_ews_credentials():
    """Retourne (email, username, password, ews_url) depuis user_config + keyring, ou (None,…)."""
    try:
        import user_config
        import keyring
        email    = user_config.get("ews_email", "")
        username = user_config.get("ews_username", "")
        ews_url  = user_config.get("ews_url", "")
        if not email or not username:
            return None, None, None, None
        password = keyring.get_password("MeetNote_EWS", username) or ""
        if not password:
            return None, None, None, None
        return email, username, password, ews_url
    except Exception:
        return None, None, None, None


def get_current_or_next_meeting(window_minutes: int = 30) -> dict | None:
    """
    Cherche le RDV en cours ou démarrant dans les 'window_minutes' prochaines minutes.
    Retourne {"subject": str, "start": datetime, "end": datetime} ou None.
    """
    # ── 1. Tentative EWS ─────────────────────────────────────────────────────
    email, username, password, ews_url = _get_ews_credentials()
    if email:
        try:
            import ews_cal
            result = ews_cal.get_current_or_next_meeting(
                email=email, username=username, password=password,
                ews_url=ews_url, window_minutes=window_minutes,
            )
            return result
        except Exception as e:
            log.debug("outlook_cal: EWS échoué, fallback COM (%s)", e)

    # ── 2. Fallback COM/MAPI ──────────────────────────────────────────────────
    if not _COM_AVAILABLE:
        return None
    try:
        outlook = win32com.client.Dispatch("Outlook.Application")
        ns = outlook.GetNamespace("MAPI")
        cal = ns.GetDefaultFolder(9)  # olFolderCalendar

        now = datetime.datetime.now()
        end_window = now + datetime.timedelta(minutes=window_minutes)

        items = cal.Items
        items.IncludeRecurrences = True
        items.Sort("[Start]")

        fmt = "%m/%d/%Y %H:%M"
        restriction = (
            f"[Start] <= '{end_window.strftime(fmt)}' AND "
            f"[End] >= '{now.strftime(fmt)}'"
        )
        restricted = items.Restrict(restriction)

        _IGNORE = {"off", "absent", "busy", "ooo", "out of office", "hors bureau"}

        for item in restricted:
            try:
                subject = str(item.Subject).strip()

                try:
                    if item.AllDayEvent:
                        continue
                except Exception:
                    pass

                if not subject or subject.lower() in _IGNORE:
                    continue

                start = _to_naive_local(item.Start)
                end   = _to_naive_local(item.End)

                duration_h = (end - start).total_seconds() / 3600
                if duration_h > 12:
                    continue

                return {"subject": subject, "start": start, "end": end}
            except Exception as e:
                log.debug("outlook_cal: item skip (%s)", e)
                continue

    except Exception as e:
        log.debug("outlook_cal: COM unavailable (%s)", e)
    return None


def is_available() -> bool:
    """Retourne True si win32com est installé ou exchangelib est disponible."""
    try:
        import ews_cal
        if ews_cal.is_available():
            return True
    except ImportError:
        pass
    return _COM_AVAILABLE
