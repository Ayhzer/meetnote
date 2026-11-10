"""
graph_teams.py — Lecture des transcripts d'une réunion Teams par Microsoft Graph (v1.0).

- reunion_en_ligne(join_url, jeton) : identifiant de la réunion en ligne qui porte l'URL
  de participation (None si Graph ne la connaît pas).
- transcripts(reunion_id, debut, fin, jeton) : transcripts créés dans la fenêtre d'une
  occurrence (une réunion récurrente partage le même identifiant pour toutes ses dates).
- contenu(reunion_id, transcript_id, jeton) : texte WebVTT du transcript.

Toute erreur (réponse Graph, réseau) devient une `ErreurGraph` dont le message, en
français, s'affiche tel quel à l'utilisateur. Aucun jeton dans les messages ni les
journaux.
"""
import datetime
import logging

import requests

_log = logging.getLogger("meetnote")

URL_BASE_GRAPH = "https://graph.microsoft.com/v1.0"
DELAI_RESEAU_S = 30

# Une transcription démarre pendant la réunion, parfois avant l'heure prévue ou bien
# après sa fin planifiée (réunion qui déborde) : la fenêtre de l'occurrence est élargie.
MARGE_FENETRE = datetime.timedelta(minutes=30)

# Garde-fou contre une pagination sans fin sur la liste des transcripts.
PAGES_MAX = 20

FORMAT_VTT = "text/vtt"
# Paramètre de requête qui demande le VTT sans attribution des locuteurs. Valeur
# retenue sur plausibilité (non vérifiée sur un tenant qui l'interdit) : à isoler ici.
PARAMETRE_SANS_ATTRIBUTION = {"includeSpeakerAttribution": "false"}

CODE_TRANSCRIPTS_DESACTIVES = "GraphAccessToTranscriptsDisabled"
CODE_ATTRIBUTION_REFUSEE = "SpeakerAttributionNotAllowed"

_STATUT_NON_AUTORISE = 401
_STATUT_INTERDIT = 403
_STATUT_INTROUVABLE = 404

_MESSAGES_PAR_CODE = {
    CODE_TRANSCRIPTS_DESACTIVES:
        "accès aux transcriptions désactivé par l'admin Teams",
}
_MESSAGES_PAR_STATUT = {
    _STATUT_NON_AUTORISE: "session Microsoft expirée ou refusée : reconnectez-vous",
    _STATUT_INTERDIT: "accès refusé par Microsoft Graph",
    _STATUT_INTROUVABLE: "réunion ou transcript introuvable dans Microsoft Graph",
}


class ErreurGraph(Exception):
    """Échec d'un appel Graph. `statut` et `code` valent None pour une panne réseau."""

    def __init__(self, message: str, statut: int | None = None, code: str = ""):
        super().__init__(message)
        self.statut = statut
        self.code = code


def _code_erreur(reponse) -> str:
    """Code `error.code` du corps JSON de Graph ; "" si le format est autre."""
    try:
        corps = reponse.json()
    except ValueError:
        return ""
    erreur = corps.get("error") if isinstance(corps, dict) else None
    code = erreur.get("code") if isinstance(erreur, dict) else None
    return code if isinstance(code, str) else ""


def _erreur_reponse(reponse) -> ErreurGraph:
    statut = reponse.status_code
    code = _code_erreur(reponse)
    message = _MESSAGES_PAR_CODE.get(code) or _MESSAGES_PAR_STATUT.get(statut)
    if message is None:
        message = f"Microsoft Graph a répondu HTTP {statut}"
    if code and code not in _MESSAGES_PAR_CODE:
        message += f" ({code})"
    _log.info("Graph : HTTP %s %s", statut, code or "-")
    return ErreurGraph(message, statut, code)


def _get(url: str, jeton: str, params: dict | None = None,
         accept: str = "application/json"):
    """GET authentifié ; rend la réponse réussie, sinon lève ErreurGraph."""
    entetes = {"Authorization": f"Bearer {jeton}", "Accept": accept}
    try:
        reponse = requests.get(url, params=params, headers=entetes,
                               timeout=DELAI_RESEAU_S)
    except requests.RequestException as exc:  # son message peut citer l'URL : omis
        _log.warning("Graph : échec réseau (%s)", type(exc).__name__)
        raise ErreurGraph("Microsoft Graph est injoignable : "
                          "vérifiez la connexion réseau") from exc
    if not reponse.ok:
        raise _erreur_reponse(reponse)
    return reponse


def _json(reponse) -> dict:
    try:
        corps = reponse.json()
    except ValueError as exc:
        raise ErreurGraph("Réponse illisible de Microsoft Graph") from exc
    if not isinstance(corps, dict):
        raise ErreurGraph("Réponse inattendue de Microsoft Graph")
    return corps


def reunion_en_ligne(join_url: str, jeton: str) -> str | None:
    """Identifiant de la réunion en ligne d'URL `join_url`, None si Graph l'ignore."""
    url_echappee = join_url.replace("'", "''")  # OData : apostrophe doublée
    reponse = _get(f"{URL_BASE_GRAPH}/me/onlineMeetings", jeton,
                   params={"$filter": f"JoinWebUrl eq '{url_echappee}'"})
    for reunion in _json(reponse).get("value") or []:
        if isinstance(reunion, dict) and reunion.get("id"):
            return reunion["id"]
    return None


def _en_utc(date: datetime.datetime) -> datetime.datetime:
    """Date aware en UTC ; une date naïve est lue comme l'heure locale du poste."""
    return date.astimezone(datetime.timezone.utc)


def _date_creation(valeur) -> datetime.datetime | None:
    """`createdDateTime` de Graph (ISO UTC, fraction et `Z` possibles) ; None sinon."""
    if not isinstance(valeur, str):
        return None
    try:
        date = datetime.datetime.fromisoformat(valeur.replace("Z", "+00:00"))
    except ValueError:
        return None
    if date.tzinfo is None:  # Graph donne de l'UTC même sans suffixe
        date = date.replace(tzinfo=datetime.timezone.utc)
    return date


def _pages_transcripts(reunion_id: str, jeton: str):
    """Éléments `value` de chaque page de la liste des transcripts."""
    url = f"{URL_BASE_GRAPH}/me/onlineMeetings/{reunion_id}/transcripts"
    for _ in range(PAGES_MAX):
        corps = _json(_get(url, jeton))
        yield from corps.get("value") or []
        url = corps.get("@odata.nextLink") or ""
        # Le jeton ne part jamais vers un autre hôte que Graph
        if not url.startswith(URL_BASE_GRAPH + "/"):
            return


def transcripts(reunion_id: str, debut: datetime.datetime, fin: datetime.datetime,
                jeton: str) -> list[dict]:
    """Transcripts créés de `debut - marge` à `fin + marge`, du plus ancien au dernier.

    Chaque élément : {"id": str, "cree": datetime (UTC)}.
    """
    plancher = _en_utc(debut) - MARGE_FENETRE
    plafond = _en_utc(fin) + MARGE_FENETRE
    retenus = []
    for element in _pages_transcripts(reunion_id, jeton):
        if not isinstance(element, dict) or not element.get("id"):
            continue
        cree = _date_creation(element.get("createdDateTime"))
        if cree is not None and plancher <= cree <= plafond:
            retenus.append({"id": element["id"], "cree": cree})
    return sorted(retenus, key=lambda t: t["cree"])


def _telecharger_vtt(url: str, jeton: str, params: dict) -> str:
    reponse = _get(url, jeton, params=params, accept=FORMAT_VTT)
    reponse.encoding = "utf-8"  # le jeu de caractères du VTT n'est pas toujours déclaré
    return reponse.text


def contenu(reunion_id: str, transcript_id: str, jeton: str) -> str:
    """Texte WebVTT du transcript ; sans attribution si le tenant refuse les noms."""
    url = (f"{URL_BASE_GRAPH}/me/onlineMeetings/{reunion_id}"
           f"/transcripts/{transcript_id}/content")
    params = {"$format": FORMAT_VTT}
    try:
        return _telecharger_vtt(url, jeton, params)
    except ErreurGraph as exc:
        if exc.statut != _STATUT_INTERDIT or exc.code != CODE_ATTRIBUTION_REFUSEE:
            raise
    return _telecharger_vtt(url, jeton, {**params, **PARAMETRE_SANS_ATTRIBUTION})
