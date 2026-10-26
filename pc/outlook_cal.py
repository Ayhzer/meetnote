"""
Lecture du calendrier Outlook/Exchange.
Stratégie :
  1. EWS (exchangelib + NTLM) si des credentials EWS sont configurés
     → nécessaire avec le Nouvel Outlook (Exchange on-premise)
  2. COM/MAPI (win32com) en fallback
     → fonctionne avec l'Outlook classique
Silencieux si aucune méthode n'est disponible.
"""
import contextlib
import datetime
import logging

log = logging.getLogger(__name__)

try:
    import win32com.client
    _COM_AVAILABLE = True
except ImportError:
    _COM_AVAILABLE = False

try:
    import pythoncom
except ImportError:
    pythoncom = None


def _to_naive_local(dt) -> datetime.datetime:
    """Convertit un pywintypes.datetime (heure locale mal étiquetée UTC) en datetime naïf local."""
    return datetime.datetime(dt.year, dt.month, dt.day, dt.hour, dt.minute, dt.second)


def _get_ews_credentials():
    """Retourne (email, username, password, ews_url) depuis user_config + keyring, ou (None,…)."""
    try:
        import keyring
        import user_config
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


_IGNORE = {"off", "absent", "busy", "ooo", "out of office", "hors bureau"}
_FORMAT_RESTRICTION_COM = "%m/%d/%Y %H:%M"
_COM_DOSSIER_CALENDRIER = 9  # olFolderCalendar
_COM_CORPS_MAX = 600
_DUREE_MAX_H = 12
# Sensibilité Outlook (OlSensitivity) : seul « Privé » masque la réunion dans l'agenda envoyé
# à Notion. « Confidentiel » (3) est une simple étiquette, Outlook ne la cache pas aux délégués.
_COM_SENSIBILITE_PRIVE = 2  # olPrivate


def _split_recipients(raw: str) -> list:
    """Découpe une chaîne 'Nom1; Nom2' en liste propre.

    Le séparateur d'Outlook est « ; » : la virgule fait partie d'un nom (« Martin, Alice »).
    """
    if not raw:
        return []
    return [n.strip() for n in raw.split(";") if n.strip()]


@contextlib.contextmanager
def _contexte_com():
    """Initialise COM pour le thread appelant (requis hors du thread principal) et le
    libère toujours à la sortie. Sans pythoncom, ne fait rien."""
    if pythoncom is None:
        yield
        return
    pythoncom.CoInitialize()
    try:
        yield
    finally:
        pythoncom.CoUninitialize()


def _est_prive_com(item) -> bool:
    """Vrai si la réunion est marquée « Privé » ; toute lecture impossible vaut False."""
    try:
        return int(item.Sensitivity) == _COM_SENSIBILITE_PRIVE
    except Exception as e:  # com_error, attribut absent, valeur non numérique
        log.debug("outlook_cal: sensibilité illisible (%s)", e)
        return False


def _evenement_com(item) -> dict | None:
    """Convertit un item COM en dict d'agenda, ou None s'il est à ignorer
    (journée entière, objet vide ou « off/absent/ooo… », plus de 12 h)."""
    subject = str(item.Subject).strip()
    try:
        if item.AllDayEvent:
            return None
    except Exception:
        pass
    if not subject or subject.lower() in _IGNORE:
        return None
    start = _to_naive_local(item.Start)
    end   = _to_naive_local(item.End)
    if (end - start).total_seconds() / 3600 > _DUREE_MAX_H:
        return None

    # Corps — texte brut tronqué
    try:
        raw_body = str(item.Body or "").strip()
    except Exception:
        raw_body = ""
    body = " ".join(raw_body.split())[:_COM_CORPS_MAX]

    return {
        "subject":      subject,
        "start":        start,
        "end":          end,
        "location":     str(getattr(item, "Location", "") or "").strip(),
        "body":         body,
        "organizer":    str(getattr(item, "Organizer", "") or "").strip(),
        "required":     _split_recipients(str(getattr(item, "RequiredAttendees", "") or "")),
        "optional":     _split_recipients(str(getattr(item, "OptionalAttendees", "") or "")),
        "is_recurring": bool(getattr(item, "IsRecurring", False)),
        "prive":        _est_prive_com(item),
    }


def evenements_ews(debut: datetime.datetime, fin: datetime.datetime) -> list | None:
    """
    RDV de la fenêtre [debut, fin] (datetimes naïfs locaux) lus via EWS, au format
    de `get_events_range`. None si EWS est injoignable (pas d'identifiants,
    exchangelib absent, connexion en échec) ; [] = joignable, aucune réunion.
    """
    email, username, password, ews_url = _get_ews_credentials()
    if not email:
        return None
    try:
        import ews_cal
        return ews_cal.evenements(email, username, password, ews_url, debut, fin)
    except Exception as e:
        log.debug("outlook_cal: EWS échoué (%s)", e)
        return None


def evenements_com(debut: datetime.datetime, fin: datetime.datetime) -> list | None:
    """
    RDV de la fenêtre [debut, fin] (datetimes naïfs locaux) lus via COM/MAPI, au
    format de `get_events_range`. None si win32com est absent ou Outlook
    injoignable ; [] = joignable, aucune réunion.
    """
    if not _COM_AVAILABLE:
        return None
    try:
        with _contexte_com():
            return _lire_calendrier_com(debut, fin)
    except Exception as e:
        log.debug("outlook_cal: COM unavailable (%s)", e)
        return None


def _lire_calendrier_com(debut: datetime.datetime, fin: datetime.datetime) -> list:
    """Lecture COM de la fenêtre ; COM doit déjà être initialisé pour ce thread."""
    outlook = win32com.client.Dispatch("Outlook.Application")
    ns  = outlook.GetNamespace("MAPI")
    cal = ns.GetDefaultFolder(_COM_DOSSIER_CALENDRIER)

    items = cal.Items
    items.IncludeRecurrences = True
    items.Sort("[Start]")

    fmt = _FORMAT_RESTRICTION_COM
    restricted = items.Restrict(
        f"[Start] <= '{fin.strftime(fmt)}' AND [End] >= '{debut.strftime(fmt)}'"
    )

    results = []
    for item in restricted:
        try:
            evt = _evenement_com(item)
        except Exception as e:
            log.debug("outlook_cal: item skip (%s)", e)
            continue
        if evt is not None:
            results.append(evt)
    return results


def _evenements_fenetre(debut: datetime.datetime, fin: datetime.datetime) -> list | None:
    """EWS d'abord, COM en repli ; None si aucune des deux sources ne répond."""
    result = evenements_ews(debut, fin)
    if result is None:
        result = evenements_com(debut, fin)
    return result


def _reunions_proches(window_minutes: int) -> list:
    """Réunions en cours ou démarrant dans `window_minutes`, au format
    {"subject", "start", "end"}, triées par début. [] si aucune source ne répond."""
    now = datetime.datetime.now()
    evts = _evenements_fenetre(now, now + datetime.timedelta(minutes=window_minutes))
    return [
        {"subject": e["subject"], "start": e["start"], "end": e["end"]}
        for e in sorted(evts or [], key=lambda e: e["start"])
    ]


def get_current_or_next_meeting(window_minutes: int = 30) -> dict | None:
    """
    Cherche le RDV en cours ou démarrant dans les 'window_minutes' prochaines minutes
    (le premier par heure de début).
    Retourne {"subject": str, "start": datetime, "end": datetime} ou None.
    """
    reunions = _reunions_proches(window_minutes)
    return reunions[0] if reunions else None


def get_all_current_meetings(window_minutes: int = 30) -> list:
    """
    Retourne TOUTES les réunions en cours ou démarrant dans les 'window_minutes' prochaines minutes.
    Chaque item : {"subject": str, "start": datetime, "end": datetime}
    """
    return _reunions_proches(window_minutes)


def get_events_range(days: int = 15) -> list:
    """
    Retourne tous les RDV des 'days' prochains jours (à partir de maintenant).
    Chaque item : {
        "subject", "start", "end",
        "location", "body", "organizer",
        "required": [str], "optional": [str],
        "is_recurring": bool, "prive": bool,
    }
    Essaie EWS en priorité, sinon COM/MAPI ; [] si aucune source ne répond.
    """
    now = datetime.datetime.now()
    return _evenements_fenetre(now, now + datetime.timedelta(days=days)) or []


def is_available() -> bool:
    """Retourne True si win32com est installé ou exchangelib est disponible."""
    try:
        import ews_cal
        if ews_cal.is_available():
            return True
    except ImportError:
        pass
    return _COM_AVAILABLE
