"""
assistant_notion.py — Base Notion « Transcriptions » pour l'assistant de démarrage.

Deux parties :

  - une partie pure (aucun reseau) : le schema attendu, calque sur ce que
    `notion_push.push_to_notion` ecrit, l'extraction de l'identifiant d'une page ou
    d'une base depuis un lien colle, et la comparaison d'un schema lu a ce schema ;
  - une partie reseau, en bas de fichier : creer la base, lire son schema, envoyer
    une page de test. Chaque fonction recoit le jeton en parametre (jamais lu dans
    `config` ni dans les reglages) et ne leve que `ErreurNotion`, dont le message
    est destine a l'utilisateur.

Forme des requetes verifiee dans la documentation officielle de l'API Notion
(version 2026-03-11, qui ne change rien a ces appels par rapport a 2025-09-03) :
une base contient des « sources de donnees » qui portent le schema.
"""
import datetime
import re
import uuid
from typing import NamedTuple
from urllib.parse import urlparse

import requests
from notion_api import NOTION_API, NOTION_VERSION

# Nom de la colonne titre attendue (celle que notion_push.py renseigne).
NOM_TITRE = "Titre"

# Propriete -> type Notion, dans l'ordre de creation. Doit rester aligne sur le
# dictionnaire `props` de notion_push.py : « Files & media » n'y est ecrit que
# lorsqu'un audio ou une piece jointe est envoye, mais la colonne doit exister.
PROPRIETES_ATTENDUES = {
    "Titre": "title",
    "Date": "date",
    "Participants": "select",
    "Source": "select",
    "Statut": "select",
    "Type": "select",
    "Durée (min)": "number",
    "Modèle Whisper": "select",
    "Files & media": "files",
}

# Types acceptes EN PLUS du type de creation ci-dessus, pour une propriete donnee.
# « Participants » n'est jamais ecrit par l'application (voir notion_push) : une
# base existante qui l'a en liste a choix multiples fonctionne tout aussi bien.
# La creation d'une nouvelle base garde le type de PROPRIETES_ATTENDUES.
TYPES_ACCEPTES_EN_PLUS = {
    "Participants": ("multi_select",),
}

# Options pre-creees pour les listes de choix a valeurs connues. Les autres listes
# (Participants, Type, Modele Whisper) restent ouvertes : Notion ajoute l'option
# lors de l'ecriture d'une page.
OPTIONS_SELECT = {
    "Source": ("PC", "Mobile", "Agenda"),
    "Statut": ("À traiter", "Traité"),
}

# Format numerique demande a la creation de « Durée (min) ».
FORMAT_NOMBRE = "number"

# Libelles francais des types Notion, pour les messages destines a l'utilisateur.
LIBELLES_TYPES = {
    "title": "titre",
    "rich_text": "texte",
    "number": "nombre",
    "select": "liste de choix",
    "multi_select": "liste à choix multiples",
    "status": "statut",
    "date": "date",
    "people": "personnes",
    "files": "fichiers",
    "checkbox": "case à cocher",
    "url": "lien",
    "email": "e-mail",
    "phone_number": "téléphone",
    "formula": "formule",
    "relation": "relation",
    "rollup": "cumul",
    "created_time": "date de création",
    "created_by": "créé par",
    "last_edited_time": "date de modification",
    "last_edited_by": "modifié par",
}

# Domaines Notion acceptes dans un lien colle (le domaine exact et ses sous-domaines).
DOMAINES_NOTION = ("notion.so", "notion.site", "notion.com")

_HEX32 = r"[0-9a-f]{32}"
_UUID = r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}"
_IDENTIFIANT_BRUT = re.compile(rf"{_UUID}|{_HEX32}", re.IGNORECASE)
# Identifiant en fin de dernier segment du chemin : « Titre-avec-tirets-<id> »
# ou « <id> » seul.
_FIN_DE_SEGMENT = re.compile(rf"(?:^|-)({_UUID}|{_HEX32})$", re.IGNORECASE)


def libelle_type(type_notion: str) -> str:
    """Libelle francais d'un type Notion ; le type brut si on ne le connait pas."""
    return LIBELLES_TYPES.get(type_notion, type_notion)


def _definition_creation(nom: str, type_notion: str) -> dict:
    """Definition d'une propriete au format de creation de l'API Notion."""
    if type_notion == "select":
        options = OPTIONS_SELECT.get(nom)
        if options:
            return {"select": {"options": [{"name": o} for o in options]}}
        return {"select": {}}
    if type_notion == "number":
        return {"number": {"format": FORMAT_NOMBRE}}
    return {type_notion: {}}


def proprietes_creation() -> dict:
    """Dict `properties` pour creer la base « Transcriptions » via l'API Notion."""
    return {
        nom: _definition_creation(nom, type_notion)
        for nom, type_notion in PROPRIETES_ATTENDUES.items()
    }


def _en_uuid(identifiant: str) -> str:
    """Met un identifiant (32 hex ou UUID) au format UUID avec tirets, en minuscules."""
    return str(uuid.UUID(hex=identifiant.replace("-", "")))


def _hote_notion(hote: str) -> bool:
    """Vrai si l'hote est un domaine Notion ou l'un de ses sous-domaines."""
    return any(hote == d or hote.endswith("." + d) for d in DOMAINES_NOTION)


def extraire_identifiant(lien: str) -> str | None:
    """Extrait l'identifiant d'une page ou d'une base depuis un lien Notion collé.

    Accepte une URL notion.so / notion.site (avec ou sans titre, avec `?v=`, `?pvs=`
    ou `#...`), un identifiant brut de 32 hex ou un UUID avec tirets. Pour une base
    vue en tableau, c'est l'identifiant du chemin qui est rendu, jamais celui de
    `?v=` (la vue). Rend un UUID avec tirets, ou None si rien n'est exploitable.
    """
    if not isinstance(lien, str):
        return None
    texte = lien.strip()
    if _IDENTIFIANT_BRUT.fullmatch(texte):
        return _en_uuid(texte)
    if "://" not in texte:
        texte = "https://" + texte
    try:
        url = urlparse(texte)
        hote = (url.hostname or "").lower()
    except ValueError:
        return None
    if not _hote_notion(hote):
        return None
    dernier_segment = url.path.rstrip("/").rsplit("/", 1)[-1]
    trouve = _FIN_DE_SEGMENT.search(dernier_segment)
    return _en_uuid(trouve.group(1)) if trouve else None


def _titre_lu(proprietes_lues: dict) -> str | None:
    """Nom de la colonne titre de la base lue (Notion n'en autorise qu'une)."""
    for nom, definition in proprietes_lues.items():
        if isinstance(definition, dict) and definition.get("type") == "title":
            return nom
    return None


def _message_titre(proprietes_lues: dict) -> list[str]:
    """Constats sur la colonne titre : absente, ou portant un autre nom."""
    nom_lu = _titre_lu(proprietes_lues)
    if nom_lu is None:
        return [f"Propriété manquante : {NOM_TITRE} ({libelle_type('title')})"]
    if nom_lu != NOM_TITRE:
        return [f'La colonne titre s\'appelle "{nom_lu}" au lieu de "{NOM_TITRE}"']
    return []


def _message_propriete(nom: str, type_attendu: str, proprietes_lues: dict) -> list[str]:
    """Constat sur une propriete autre que le titre : manquante ou de mauvais type."""
    definition = proprietes_lues.get(nom)
    if definition is None:
        return [f"Propriété manquante : {nom} ({libelle_type(type_attendu)})"]
    type_lu = definition.get("type") if isinstance(definition, dict) else None
    acceptes = (type_attendu, *TYPES_ACCEPTES_EN_PLUS.get(nom, ()))
    if type_lu not in acceptes:
        lu = libelle_type(str(type_lu))
        attendu = " ou ".join(libelle_type(t) for t in acceptes)
        return [f"Mauvais type pour {nom} : {lu} au lieu de {attendu}"]
    return []


def comparer_schema(proprietes_lues: dict) -> list[str]:
    """Compare les proprietes lues via l'API au schema attendu.

    `proprietes_lues` est le dict `properties` tel que Notion le renvoie (chaque
    entree porte une cle `type`). Rend la liste, en francais clair, de ce qui manque
    ou n'a pas le bon type ; liste vide = base conforme.

    Deux tolerances voulues :
      - les proprietes en trop sont ignorees ;
      - les options de liste de choix manquantes (Source, Statut...) ne sont PAS une
        erreur : Notion cree l'option au moment ou une page l'ecrit.
    """
    constats = _message_titre(proprietes_lues)
    for nom, type_attendu in PROPRIETES_ATTENDUES.items():
        if nom != NOM_TITRE:
            constats += _message_propriete(nom, type_attendu, proprietes_lues)
    return constats


# ── Partie reseau ────────────────────────────────────────────────────────────

# Delai maximal d'une requete (meme valeur que notion_push), en secondes.
DELAI_REQUETE = 30

TITRE_BASE_DEFAUT = "Transcriptions"
TITRE_PAGE_TEST = "Test d'installation MeetNote"
TEXTE_PAGE_TEST = (
    "Cette page a été créée par l'assistant de MeetNote pour vérifier que l'envoi "
    "vers Notion fonctionne. Vous pouvez la supprimer."
)
SOURCE_PAGE_TEST = "PC"
STATUT_PAGE_TEST = "À traiter"

MSG_JETON_REFUSE = (
    "Le jeton d'intégration est refusé par Notion : "
    "vérifiez qu'il a été copié en entier."
)
MSG_INTROUVABLE = (
    "Notion ne trouve pas cette page : ouvrez-la dans Notion, menu ··· → Connexions, "
    "et ajoutez votre intégration."
)
MSG_DROIT_INSUFFISANT = (
    "Votre intégration n'a pas le droit d'insérer du contenu : dans la page de "
    "l'intégration sur notion.so/my-integrations, onglet Capacités, cochez "
    "« Insérer du contenu »."
)
MSG_RESEAU = "Impossible de joindre Notion, vérifiez la connexion Internet."
MSG_REPONSE_ILLISIBLE = (
    "Notion a envoyé une réponse inattendue. Réessayez dans un instant."
)
MSG_SANS_SOURCE = (
    "Cette base Notion ne contient aucune source de données exploitable."
)


class ErreurNotion(Exception):
    """Echec d'un appel Notion, avec un message en francais pour l'utilisateur.

    `statut` est le code HTTP (None si Notion n'a pas repondu) et `code` le code
    d'erreur de Notion (« object_not_found »...). Le message ne contient jamais
    le jeton.
    """

    def __init__(
        self, message: str, statut: int | None = None, code: str | None = None
    ):
        super().__init__(message)
        self.message = message
        self.statut = statut
        self.code = code


class BaseCreee(NamedTuple):
    """Resultat de `creer_base`."""

    base_id: str  # identifiant a mettre dans `parent.database_id` (notion_push)
    source_id: str | None  # premiere source de donnees de la base
    url: str | None


def _entetes(token: str) -> dict:
    """En-tetes d'un appel : construits localement, jamais le HEADERS de notion_push."""
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Notion-Version": NOTION_VERSION,
    }


def _erreur_depuis_reponse(reponse) -> ErreurNotion:
    """Traduit une reponse HTTP en echec en francais, selon le code de statut."""
    statut = reponse.status_code
    try:
        corps = reponse.json()
    except ValueError:
        corps = None
    corps = corps if isinstance(corps, dict) else {}
    code, detail = corps.get("code"), corps.get("message")
    if statut == 401:
        message = MSG_JETON_REFUSE
    elif statut == 404 or code == "object_not_found":
        message = MSG_INTROUVABLE
    elif statut == 403:
        message = MSG_DROIT_INSUFFISANT
    elif statut == 400 and detail:
        message = f"Notion refuse la demande : {detail}"
    elif statut == 400:
        message = "Notion refuse la demande (requête invalide)."
    else:
        message = (
            f"Notion a répondu par une erreur ({statut}). "
            "Réessayez dans un instant."
        )
    return ErreurNotion(message, statut=statut, code=code)


def _appeler(methode, chemin: str, token: str, corps: dict | None = None) -> dict:
    """Appelle l'API (`methode` = requests.get ou requests.post) et rend le JSON.

    Ne leve que `ErreurNotion` : reseau coupe, delai depasse, statut d'erreur ou
    reponse illisible.
    """
    options = {"headers": _entetes(token), "timeout": DELAI_REQUETE}
    if corps is not None:
        options["json"] = corps
    try:
        reponse = methode(f"{NOTION_API}{chemin}", **options)
    except requests.RequestException as exc:
        raise ErreurNotion(MSG_RESEAU) from exc
    if not reponse.ok:
        raise _erreur_depuis_reponse(reponse)
    try:
        resultat = reponse.json()
    except ValueError as exc:
        raise ErreurNotion(MSG_REPONSE_ILLISIBLE, statut=reponse.status_code) from exc
    if not isinstance(resultat, dict):
        raise ErreurNotion(MSG_REPONSE_ILLISIBLE, statut=reponse.status_code)
    return resultat


def _premiere_source(base: dict) -> str | None:
    """Identifiant de la premiere source de donnees d'une base lue ou creee."""
    sources = base.get("data_sources")
    if isinstance(sources, list) and sources and isinstance(sources[0], dict):
        return sources[0].get("id")
    return None


def creer_base(
    token: str, page_parente_id: str, titre: str = TITRE_BASE_DEFAUT
) -> BaseCreee:
    """Cree la base « Transcriptions » sous une page et rend ses identifiants.

    Le schema (`proprietes_creation`) va dans `initial_data_source`, la premiere
    source de donnees creee avec la base (API 2025-09-03 et suivantes).
    """
    corps = {
        "parent": {"type": "page_id", "page_id": page_parente_id},
        "title": [{"type": "text", "text": {"content": titre}}],
        "initial_data_source": {"properties": proprietes_creation()},
    }
    base = _appeler(requests.post, "/databases", token, corps)
    base_id = base.get("id")
    if not base_id:
        raise ErreurNotion(MSG_REPONSE_ILLISIBLE)
    return BaseCreee(base_id, _premiere_source(base), base.get("url"))


def lire_schema(token: str, base_id: str) -> dict:
    """Rend le dict `properties` d'une base, a passer a `comparer_schema`.

    Lecture en deux temps : la base donne la liste de ses sources de donnees, la
    source donne les proprietes. Une base a plusieurs sources de donnees n'est pas
    prevue par MeetNote : on lit la premiere, celle que Notion liste en tete.
    """
    base = _appeler(requests.get, f"/databases/{base_id}", token)
    source_id = _premiere_source(base)
    if source_id is None:
        raise ErreurNotion(MSG_SANS_SOURCE)
    source = _appeler(requests.get, f"/data_sources/{source_id}", token)
    proprietes = source.get("properties")
    if not isinstance(proprietes, dict):
        raise ErreurNotion(MSG_REPONSE_ILLISIBLE)
    return proprietes


def nature_lien(token: str, identifiant: str) -> str:
    """Dit si l'identifiant colle par l'utilisateur est une « base » ou une « page ».

    On essaie d'abord la base ; pour une page Notion repond 400 (validation) ou 404
    selon la version, on essaie alors la page. Si la page n'est pas trouvee non plus,
    l'erreur 404 remonte telle quelle (page non partagee avec l'integration). Tout
    autre echec (jeton refuse, reseau) remonte aussitot.
    """
    try:
        _appeler(requests.get, f"/databases/{identifiant}", token)
    except ErreurNotion as erreur:
        if erreur.statut not in (400, 404):
            raise
    else:
        return "base"
    _appeler(requests.get, f"/pages/{identifiant}", token)
    return "page"


def _date_du_jour() -> str:
    """Date locale du jour au format ISO (AAAA-MM-JJ)."""
    return datetime.datetime.now().astimezone().date().isoformat()


def envoyer_page_test(token: str, base_id: str) -> str:
    """Cree une page de test dans la base et rend son URL.

    Meme forme de parent et de proprietes que `notion_push.push_to_notion`, limitee
    aux colonnes obligatoires de MeetNote.
    """
    corps = {
        "parent": {"database_id": base_id},
        "properties": {
            NOM_TITRE: {"title": [{"text": {"content": TITRE_PAGE_TEST}}]},
            "Date": {"date": {"start": _date_du_jour()}},
            "Source": {"select": {"name": SOURCE_PAGE_TEST}},
            "Statut": {"select": {"name": STATUT_PAGE_TEST}},
        },
        "children": [
            {
                "object": "block",
                "type": "paragraph",
                "paragraph": {
                    "rich_text": [
                        {"type": "text", "text": {"content": TEXTE_PAGE_TEST}}
                    ]
                },
            }
        ],
    }
    page = _appeler(requests.post, "/pages", token, corps)
    url = page.get("url")
    if not url:
        raise ErreurNotion(MSG_REPONSE_ILLISIBLE)
    return url
