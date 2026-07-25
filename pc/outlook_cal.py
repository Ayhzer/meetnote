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
            if result is not None:
                return result
            # None = EWS injoignable → on tente le fallback COM/MAPI ci-dessous
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


def get_all_current_meetings(window_minutes: int = 30) -> list:
    """
    Retourne TOUTES les réunions en cours ou démarrant dans les 'window_minutes' prochaines minutes.
    Chaque item : {"subject": str, "start": datetime, "end": datetime}
    """
    email, username, password, ews_url = _get_ews_credentials()
    if email:
        try:
            import ews_cal
            if hasattr(ews_cal, "get_all_current_meetings"):
                result = ews_cal.get_all_current_meetings(
                    email=email, username=username, password=password,
                    ews_url=ews_url, window_minutes=window_minutes,
                )
                if result is not None:
                    return result
                # None = EWS injoignable → fallback COM/MAPI ci-dessous
        except Exception as e:
            log.debug("outlook_cal: EWS get_all échoué (%s)", e)

    if not _COM_AVAILABLE:
        return []
    try:
        outlook = win32com.client.Dispatch("Outlook.Application")
        ns      = outlook.GetNamespace("MAPI")
        cal     = ns.GetDefaultFolder(9)

        now        = datetime.datetime.now()
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

        results = []
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
                if (end - start).total_seconds() / 3600 > 12:
                    continue
                results.append({"subject": subject, "start": start, "end": end})
            except Exception as e:
                log.debug("outlook_cal: item skip (%s)", e)
        return results
    except Exception as e:
        log.debug("outlook_cal: COM get_all unavailable (%s)", e)
    return []


def get_events_range(days: int = 15) -> list:
    """
    Retourne tous les RDV des 'days' prochains jours (à partir de maintenant).
    Chaque item : {
        "subject", "start", "end",
        "location", "body", "organizer",
        "required": [str], "optional": [str],
        "is_recurring": bool,
    }
    Essaie EWS en priorité, sinon COM/MAPI.
    """
    # ── 1. Tentative EWS ─────────────────────────────────────────────────────
    email, username, password, ews_url = _get_ews_credentials()
    if email:
        try:
            import ews_cal
            if hasattr(ews_cal, "get_events_range"):
                result = ews_cal.get_events_range(
                    email=email, username=username, password=password,
                    ews_url=ews_url, days=days,
                )
                if result is not None:
                    return result
        except Exception as e:
            log.debug("outlook_cal: EWS get_events_range échoué, fallback COM (%s)", e)

    # ── 2. Fallback COM/MAPI ──────────────────────────────────────────────────
    if not _COM_AVAILABLE:
        return []
    try:
        outlook = win32com.client.Dispatch("Outlook.Application")
        ns  = outlook.GetNamespace("MAPI")
        cal = ns.GetDefaultFolder(9)  # olFolderCalendar

        now        = datetime.datetime.now()
        end_window = now + datetime.timedelta(days=days)

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

        def _split_recipients(raw: str) -> list:
            """Découpe une chaîne 'Nom1; Nom2' en liste propre."""
            if not raw:
                return []
            return [n.strip() for n in raw.replace(",", ";").split(";") if n.strip()]

        results = []
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
                if (end - start).total_seconds() / 3600 > 12:
                    continue

                # Corps — texte brut tronqué
                try:
                    raw_body = str(item.Body or "").strip()
                except Exception:
                    raw_body = ""
                body = " ".join(raw_body.split())[:600]

                results.append({
                    "subject":      subject,
                    "start":        start,
                    "end":          end,
                    "location":     str(getattr(item, "Location", "") or "").strip(),
                    "body":         body,
                    "organizer":    str(getattr(item, "Organizer", "") or "").strip(),
                    "required":     _split_recipients(str(getattr(item, "RequiredAttendees", "") or "")),
                    "optional":     _split_recipients(str(getattr(item, "OptionalAttendees", "") or "")),
                    "is_recurring": bool(getattr(item, "IsRecurring", False)),
                })
            except Exception as e:
                log.debug("outlook_cal: get_events_range item skip (%s)", e)

        return results

    except Exception as e:
        log.debug("outlook_cal: get_events_range COM unavailable (%s)", e)
    return []


def is_available() -> bool:
    """Retourne True si win32com est installé ou exchangelib est disponible."""
    try:
        import ews_cal
        if ews_cal.is_available():
            return True
    except ImportError:
        pass
    return _COM_AVAILABLE
