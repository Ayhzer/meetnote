"""
Lecture du calendrier Outlook via win32com (COM local).
Silencieux si Outlook n'est pas disponible ou non ouvert.
"""
import datetime

try:
    import win32com.client
    _AVAILABLE = True
except ImportError:
    _AVAILABLE = False


def get_current_or_next_meeting(window_minutes: int = 30) -> dict | None:
    """
    Cherche dans le calendrier Outlook le RDV qui :
    - est en cours maintenant, OU
    - commence dans les 'window_minutes' prochaines minutes.

    Retourne {"subject": str, "start": datetime, "end": datetime} ou None.
    Silencieux si Outlook non disponible ou Outlook fermé.
    """
    if not _AVAILABLE:
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

        # Format attendu par Outlook COM : MM/DD/YYYY HH:MM
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

                # Ignorer RDV toute la journée
                try:
                    if item.AllDayEvent:
                        continue
                except Exception:
                    pass

                # Ignorer sujets parasites (statuts de présence, blocs OOO)
                if not subject or subject.lower() in _IGNORE:
                    continue

                start = item.Start
                end   = item.End

                # Convertir pywintypes.datetime en datetime standard si nécessaire
                if not isinstance(start, datetime.datetime):
                    start = datetime.datetime(
                        start.year, start.month, start.day,
                        start.hour, start.minute, start.second
                    )
                if not isinstance(end, datetime.datetime):
                    end = datetime.datetime(
                        end.year, end.month, end.day,
                        end.hour, end.minute, end.second
                    )

                # Ignorer les blocs de durée absurde (> 12h = pas une vraie réunion)
                duration_h = (end - start).total_seconds() / 3600
                if duration_h > 12:
                    continue

                return {"subject": subject, "start": start, "end": end}
            except Exception:
                continue

    except Exception:
        pass
    return None


def is_available() -> bool:
    """Retourne True si win32com est installé."""
    return _AVAILABLE
