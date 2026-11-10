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
import re

from lien_teams import url_participation

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
# Outlook lit les dates d'un filtre Restrict selon le format de date courte de Windows
# (dd/MM/yyyy en fr-FR) : un format américain fixe fait lire « 11/10/2026 » comme le
# 11 octobre. L'heure, elle, est acceptée en %H:%M quel que soit le poste.
_FORMAT_HEURE_COM = "%H:%M"
# Dernier recours si le format du poste est illisible. Mesuré sur le poste fr-FR : Outlook
# n'accepte pas l'ISO (yyyy-mm-dd, yyyy/mm/dd → 0 résultat), seul le format de date courte
# du poste fonctionne ; le format américain n'est donc qu'un pari, d'où l'avertissement.
_MOTIF_DATE_REPLI_COM = "%m/%d/%Y"
_LOCALE_SSHORTDATE = 0x1F  # LOCALE_SSHORTDATE : motif de date courte de l'utilisateur
_LOCALE_TAILLE_TAMPON = 80
# Jetons d'un motif de date Windows : littéral entre apostrophes, d/M/y répétés, ou un caractère.
_JETON_DATE_WINDOWS = re.compile(r"'[^']*'|d+|M+|y+|g+|.", re.DOTALL)
_repli_signale = False
_COM_DOSSIER_CALENDRIER = 9  # olFolderCalendar
_COM_CORPS_MAX = 600
_DUREE_MAX_H = 12
# Sensibilité Outlook (OlSensitivity) : seul « Privé » masque la réunion dans l'agenda envoyé
# à Notion. « Confidentiel » (3) est une simple étiquette, Outlook ne la cache pas aux délégués.
_COM_SENSIBILITE_PRIVE = 2  # olPrivate


def motif_strftime_depuis_windows(motif: str) -> str:
    """Convertit un motif de date courte Windows (« dd/MM/yyyy », « M/d/yyyy »,
    « yyyy/MM/dd »…) en motif strftime ; les séparateurs sont conservés.

    Lève ValueError si le motif ne contient pas exactement jour, mois et année, ou s'il
    utilise des noms (ddd, MMM) ou une ère (g), que strftime n'écrirait pas comme Outlook.
    """
    sortie, vus = [], []
    for jeton in _JETON_DATE_WINDOWS.findall(motif):
        if jeton[0] == "'":
            sortie.append(jeton.strip("'").replace("%", "%%"))
        elif jeton[0] in "dMy" and len(jeton) <= (2 if jeton[0] != "y" else 4):
            code = {"d": "%d", "M": "%m", "y": "%Y"}[jeton[0]]
            vus.append(code)
            sortie.append(code)
        elif jeton[0] in "dMyg":
            raise ValueError(f"jeton de date non pris en charge : {jeton!r}")
        else:
            sortie.append(jeton.replace("%", "%%"))
    if sorted(vus) != ["%Y", "%d", "%m"]:
        raise ValueError(f"motif de date incomplet : {motif!r}")
    return "".join(sortie)


def _lire_format_date_courte() -> str:
    """Motif de date courte de l'utilisateur Windows (ex. « dd/MM/yyyy »).
    Lève OSError si l'API est absente (hors Windows) ou échoue."""
    import ctypes
    tampon = ctypes.create_unicode_buffer(_LOCALE_TAILLE_TAMPON)
    try:
        lire = ctypes.windll.kernel32.GetLocaleInfoEx
    except AttributeError as e:
        raise OSError("GetLocaleInfoEx indisponible") from e
    if not lire(None, _LOCALE_SSHORTDATE, tampon, _LOCALE_TAILLE_TAMPON):
        raise OSError("GetLocaleInfoEx a échoué")
    return tampon.value


def format_restriction_com() -> str:
    """Motif strftime des bornes d'un filtre Restrict, aligné sur le poste (date courte
    Windows + %H:%M). Repli sur le format américain, avec un avertissement unique."""
    global _repli_signale
    try:
        date = motif_strftime_depuis_windows(_lire_format_date_courte())
    except (OSError, ValueError) as e:
        if not _repli_signale:
            _repli_signale = True
            log.warning("outlook_cal: format de date du poste illisible (%s), repli sur %s : "
                        "les filtres COM peuvent être faux", e, _MOTIF_DATE_REPLI_COM)
        date = _MOTIF_DATE_REPLI_COM
    return f"{date} {_FORMAT_HEURE_COM}"


def filtre_restriction_com(debut: datetime.datetime, fin: datetime.datetime) -> str:
    """Filtre Items.Restrict des réunions qui chevauchent [debut, fin]."""
    fmt = format_restriction_com()
    return f"[Start] <= '{fin.strftime(fmt)}' AND [End] >= '{debut.strftime(fmt)}'"


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
    # Lien Teams lu sur le corps complet : il peut dépasser la troncature.
    join_url = url_participation(raw_body)
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
        "join_url":     join_url,
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

    restricted = items.Restrict(filtre_restriction_com(debut, fin))

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
        "join_url": str (URL de participation Teams, "" si absente),
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
