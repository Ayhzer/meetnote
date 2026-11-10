"""
user_config.py - Persistance des parametres utilisateur dans %APPDATA%/MeetNote/settings.json
Fonctionne aussi bien en mode developpement qu'en bundle PyInstaller (sys._MEIPASS read-only).
"""
import os

import fichier_json

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
}

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
