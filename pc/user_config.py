"""
user_config.py - Persistance des parametres utilisateur dans %APPDATA%/MeetNote/settings.json
Fonctionne aussi bien en mode developpement qu'en bundle PyInstaller (sys._MEIPASS read-only).
"""
import os

import fichier_json
from retention import RETENTION_AUDIO_JOURS_DEFAUT, RETENTION_TRANSCRIPT_JOURS_DEFAUT

_APPDATA  = os.environ.get("APPDATA", os.path.expanduser("~"))
_DIR      = os.path.join(_APPDATA, "MeetNote")
DOSSIER   = _DIR  # public : autres fichiers de MeetNote (cache Graph...)
_PATH     = os.path.join(_DIR, "settings.json")

# Motifs du cahier : une reunion dont le titre en contient un n'est pas enregistree
# automatiquement. Format de la cle "auto_start_exclusions" (un motif par ligne).
_MOTIFS_PAR_DEFAUT = (
    "todo",
    "off",
    "perso",
    "privé",
    "prive",
    "déjeuner",
    "dejeuner",
    "trajet",
    "ne pas enregistrer",
)
EXCLUSIONS_PAR_DEFAUT = "\n".join(_MOTIFS_PAR_DEFAUT)

_DEFAULTS = {
    "notion_token":       "",
    "notion_database_id": "",
    # Dossier des modeles Whisper (vide = defaut, voir modeles.py)
    "dossier_modeles":    "",
    # Modele Whisper choisi (vide = jamais choisi : fixe au premier lancement)
    "whisper_model":      "",
    # Microsoft Graph (transcripts Teams) : identifiants de l'application Entra ID
    "graph_client_id":    "",
    "graph_tenant_id":    "",
    # Durees de conservation (jours) avant suppression automatique vers la Corbeille
    # (source unique des valeurs : retention.py)
    "retention_audio_jours":      RETENTION_AUDIO_JOURS_DEFAUT,  # apres transcription
    "retention_transcript_jours": RETENTION_TRANSCRIPT_JOURS_DEFAUT,  # apres reunion
}

# Cles de conservation reconnues (les seules acceptees par jours_de_conservation)
CLES_CONSERVATION = ("retention_audio_jours", "retention_transcript_jours")

def load() -> dict:
    """Lit settings.json ; absent ou illisible (mis de cote) : valeurs par defaut."""
    data = fichier_json.lire_ou_ecarter(_PATH, dict)
    if data is None:
        return dict(_DEFAULTS)
    # Merge avec les defaults pour les clés manquantes
    return {**_DEFAULTS, **data}


def save(data: dict):
    """Ecrit de facon atomique (voir fichier_json) : jamais de fichier tronque."""
    current = load()
    current.update(data)
    fichier_json.ecrire_atomique(_PATH, current, indent=2, ensure_ascii=False)

def get(key: str, default=None):
    return load().get(key, default)

def set(key: str, value):
    save({key: value})


def est_premier_lancement() -> bool:
    """Vrai si aucun token Notion n'est enregistre et l'assistant jamais termine."""
    data = load()
    sans_token = not str(data.get("notion_token", "")).strip()
    return sans_token and not data.get("assistant_termine")


def liste_exclusions(texte: str | None) -> list[str]:
    """Motifs d'exclusion auto-start : un par ligne, en minuscules, sans lignes vides."""
    return [m.strip().lower() for m in (texte or "").splitlines() if m.strip()]


def valeur_jours(valeur) -> int | None:
    """Entier >= 1 si `valeur` en est un, sinon None.

    Refuse les booleens (True == 1 en Python), les decimaux et tout texte non
    strictement numerique. Un texte est accepte tel quel s'il est saisi dans un champ.
    """
    if isinstance(valeur, bool):
        return None
    if isinstance(valeur, str):
        texte = valeur.strip()
        if not (texte.isascii() and texte.isdigit()):
            return None
        valeur = int(texte)
    if isinstance(valeur, int) and valeur >= 1:
        return valeur
    return None


def jours_de_conservation(cle: str, reglages: dict | None = None) -> int:
    """Duree de conservation en jours pour `cle` (voir CLES_CONSERVATION).

    Rend la valeur par defaut si la valeur est absente, non entiere, booleenne ou < 1.
    `reglages` permet de tester sans toucher au fichier ; None lit settings.json.
    """
    if cle not in CLES_CONSERVATION:
        raise KeyError(cle)
    if reglages is None:
        reglages = load()
    valeur = valeur_jours(reglages.get(cle))
    return valeur if valeur is not None else _DEFAULTS[cle]
