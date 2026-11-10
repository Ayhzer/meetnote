"""
graph_auth.py - Authentification Microsoft Graph (transcripts Teams) via MSAL.

- obtenir_jeton(interactif) : jeton silencieux sur le compte en cache, puis, seulement
  si `interactif`, connexion dans le navigateur (login_hint = adresse EWS).
- Le cache MSAL est chiffre par DPAPI (lie a la session Windows) dans
  %APPDATA%/MeetNote/graph_cache.bin, ecrit de facon atomique ; un cache illisible est
  renomme en .corrompu-... (voir fichier_json), jamais supprime ni cause d'echec.
- `msal` et `win32crypt` sont importes a l'usage : le module se charge sans eux.
- Aucun jeton ni contenu de cache dans les journaux (seuls les codes d'erreur).
"""
import importlib
import logging
import os
from typing import NamedTuple

import fichier_json
import user_config

try:
    from pywintypes import error as _ERREUR_WIN32
except ImportError:  # pywin32 absent : DPAPI l'est aussi, traite plus bas
    _ERREUR_WIN32 = OSError

_log = logging.getLogger("meetnote")

URL_AUTORITE = "https://login.microsoftonline.com/"
PORTEE_REUNIONS = "OnlineMeetings.Read"
PORTEE_TRANSCRIPTS = "OnlineMeetingTranscript.Read.All"
PORTEES = [PORTEE_REUNIONS, PORTEE_TRANSCRIPTS]
# Echecs attendus d'un cache illisible (DPAPI, JSON), d'un disque ou d'un appel msal
_ERREURS_CACHE = (_ERREUR_WIN32, OSError, ValueError, TypeError, KeyError, RuntimeError)
_ERREURS_MSAL = (OSError, ValueError, TypeError, RuntimeError)
CHEMIN_CACHE = os.path.join(user_config.DOSSIER, "graph_cache.bin")
DESCRIPTION_DPAPI = "MeetNote Graph"
INVITE_CHOIX_COMPTE = "select_account"


class ResultatJeton(NamedTuple):
    """`jeton` est None en cas d'echec, et `motif` l'explique en francais."""
    jeton: str | None
    compte: str | None = None
    motif: str = ""


def identifiants_saisis(client_id: str, tenant_id: str) -> bool:
    """Vrai si l'ID client et l'ID de locataire sont tous deux renseignes."""
    return bool((client_id or "").strip() and (tenant_id or "").strip())


def message_connexion(resultat: ResultatJeton) -> str:
    """Phrase en clair pour l'IHM : compte connecte ou motif d'echec."""
    if resultat.jeton:
        return f"Connecté : {resultat.compte or 'compte Microsoft 365'}"
    return resultat.motif or "Connexion Microsoft impossible"


def _echec(motif: str) -> ResultatJeton:
    return ResultatJeton(None, None, motif)


def _importer(nom: str):
    """Rend le module `nom`, ou None s'il n'est pas installe."""
    try:
        return importlib.import_module(nom)
    except ImportError:
        return None


def _charger_cache(msal, win32crypt):
    """Cache MSAL relu depuis le disque ; vide si absent, indechiffrable ou corrompu."""
    cache = msal.SerializableTokenCache()
    try:
        with open(CHEMIN_CACHE, "rb") as f:
            chiffre = f.read()
    except FileNotFoundError:
        return cache
    except OSError as exc:
        _log.warning("Cache Graph illisible (%s) : connexion a refaire",
                     type(exc).__name__)
        return cache
    try:
        if win32crypt is None:
            raise RuntimeError("DPAPI indisponible")
        clair = win32crypt.CryptUnprotectData(chiffre, None, None, None, 0)[1]
        cache.deserialize(clair.decode("utf-8"))
    except _ERREURS_CACHE as exc:  # contenu du cache jamais journalise
        _log.warning("Cache Graph indechiffrable (%s) : mis de cote",
                     type(exc).__name__)
        fichier_json.ecarter(CHEMIN_CACHE)
        return msal.SerializableTokenCache()
    return cache


def _sauver_cache(cache, win32crypt) -> None:
    """Chiffre (DPAPI) et ecrit le cache si MSAL l'a modifie. Jamais en clair."""
    if not cache.has_state_changed:
        return
    if win32crypt is None:
        _log.warning("DPAPI indisponible : cache Graph non conserve")
        return
    try:
        chiffre = win32crypt.CryptProtectData(
            cache.serialize().encode("utf-8"), DESCRIPTION_DPAPI, None, None, None, 0)
        fichier_json.ecrire_octets_atomique(CHEMIN_CACHE, chiffre)
    except _ERREURS_CACHE as exc:  # ne pas perdre le jeton deja obtenu
        _log.warning("Cache Graph non sauvegarde (%s)", type(exc).__name__)


def _choisir_compte(comptes: list, email: str):
    """Compte dont l'identifiant est `email`, sinon le premier ; None si aucun."""
    for compte in comptes:
        if email and compte.get("username", "").lower() == email.lower():
            return compte
    return comptes[0] if comptes else None


def _jeton_silencieux(app, email: str) -> dict | None:
    compte = _choisir_compte(app.get_accounts(), email)
    if compte is None:
        return None
    return app.acquire_token_silent(PORTEES, account=compte)


def _jeton_interactif(app, email: str) -> dict | None:
    return app.acquire_token_interactive(
        PORTEES, login_hint=email or None, prompt=INVITE_CHOIX_COMPTE)


def _a_un_jeton(reponse: dict | None) -> bool:
    return bool(reponse and reponse.get("access_token"))


def _vers_resultat(reponse: dict | None, email: str) -> ResultatJeton:
    if _a_un_jeton(reponse):
        claims = reponse.get("id_token_claims") or {}
        compte = claims.get("preferred_username") or email or None
        return ResultatJeton(reponse["access_token"], compte, "")
    code = (reponse or {}).get("error", "")
    _log.info("Jeton Graph refuse (%s)", code or "aucune reponse")
    return _echec("Connexion Microsoft refusee ou annulee"
                  + (f" ({code})" if code else ""))


def obtenir_jeton(interactif: bool = False) -> ResultatJeton:
    """Jeton d'acces Graph : silencieux d'abord, navigateur si `interactif`."""
    client_id = (user_config.get("graph_client_id", "") or "").strip()
    tenant_id = (user_config.get("graph_tenant_id", "") or "").strip()
    if not client_id or not tenant_id:
        return _echec("Microsoft Graph non configure : identifiant d'application "
                      "ou de locataire manquant")
    msal = _importer("msal")
    if msal is None:
        return _echec("Bibliotheque msal absente : Microsoft Graph indisponible")
    win32crypt = _importer("win32crypt")
    email = (user_config.get("ews_email", "") or "").strip()
    cache = _charger_cache(msal, win32crypt)
    try:
        app = msal.PublicClientApplication(
            client_id, authority=URL_AUTORITE + tenant_id, token_cache=cache)
        reponse = _jeton_silencieux(app, email)
        if not _a_un_jeton(reponse) and interactif:
            reponse = _jeton_interactif(app, email)
    except _ERREURS_MSAL as exc:  # OSError couvre les erreurs reseau (requests)
        _log.warning("Authentification Graph en echec (%s)", type(exc).__name__)
        return _echec(f"Authentification Microsoft impossible ({type(exc).__name__})")
    _sauver_cache(cache, win32crypt)
    if not _a_un_jeton(reponse) and not interactif:
        return _echec("Connexion Microsoft requise : aucune session en cache")
    return _vers_resultat(reponse, email)
