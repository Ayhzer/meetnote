"""
assistant_agenda.py — Détecte quel agenda MeetNote peut lire, pour l'assistant de démarrage.

Trois modes, essayés dans cet ordre :

  1. Outlook classique : l'objet COM `Outlook.Application` répond réellement et donne
     accès au calendrier par défaut. Le Nouvel Outlook n'enregistre pas cet objet : la
     création échoue, ce qui est le signal attendu, pas une erreur.
  2. EWS : une adresse, un identifiant et un mot de passe (coffre Windows) sont présents.
     Les identifiants sont lus par `outlook_cal._get_ews_credentials`, source unique.
  3. Manuel : aucun des deux, l'utilisateur saisira ses réunions à la main.

Module pur côté interface : aucune fenêtre, aucun réseau. `win32com` n'est importé qu'à
l'appel, pour que l'import du module ne dépende ni d'Outlook ni de `config.py`.
"""
import logging
from typing import NamedTuple

import outlook_cal

log = logging.getLogger(__name__)

MODE_OUTLOOK = "outlook"
MODE_EWS = "ews"
MODE_MANUEL = "manuel"

# Identifiant COM d'Outlook classique et dossier « Calendrier » (olFolderCalendar).
PROG_ID_OUTLOOK = "Outlook.Application"
DOSSIER_CALENDRIER = 9

PHRASES = {
    MODE_OUTLOOK: "Outlook classique détecté : vos réunions seront lues directement dans Outlook.",
    MODE_EWS: "Boîte Exchange configurée : vos réunions seront lues via votre compte Exchange.",
    MODE_MANUEL: "Aucun agenda détecté : vous indiquerez vos réunions à la main.",
}


class AgendaDetecte(NamedTuple):
    """Résultat de la détection : le mode retenu et la phrase à afficher."""

    mode: str
    phrase: str


def _outlook_classique_joignable() -> bool:
    """Vrai si Outlook classique répond et expose son calendrier par défaut."""
    try:
        import win32com.client
    except ImportError:
        log.debug("assistant_agenda: pywin32 absent")
        return False
    try:
        outlook = win32com.client.Dispatch(PROG_ID_OUTLOOK)
        outlook.GetNamespace("MAPI").GetDefaultFolder(DOSSIER_CALENDRIER)
    except Exception as e:  # noqa: BLE001 — com_error, AttributeError… : tout échec = injoignable
        log.debug("assistant_agenda: Outlook classique injoignable (%s)", e)
        return False
    return True


def _ews_configure() -> bool:
    """Vrai si les identifiants EWS sont tous présents."""
    email, _username, _password, _url = outlook_cal._get_ews_credentials()
    return bool(email)


def _detecte(mode: str) -> AgendaDetecte:
    return AgendaDetecte(mode, PHRASES[mode])


def detecter_agenda() -> AgendaDetecte:
    """Rend le mode d'agenda disponible : Outlook classique, EWS, sinon manuel."""
    if _outlook_classique_joignable():
        return _detecte(MODE_OUTLOOK)
    if _ews_configure():
        return _detecte(MODE_EWS)
    return _detecte(MODE_MANUEL)
