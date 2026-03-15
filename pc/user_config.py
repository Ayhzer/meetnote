"""
user_config.py - Persistance des parametres utilisateur dans %APPDATA%/MeetNote/settings.json
Fonctionne aussi bien en mode developpement qu'en bundle PyInstaller (sys._MEIPASS read-only).
"""
import os
import json

_APPDATA  = os.environ.get("APPDATA", os.path.expanduser("~"))
_DIR      = os.path.join(_APPDATA, "MeetNote")
_PATH     = os.path.join(_DIR, "settings.json")

_DEFAULTS = {
    "notion_token":       "",
    "notion_database_id": "",
}

def load() -> dict:
    try:
        with open(_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        # Merge avec les defaults pour les clés manquantes
        return {**_DEFAULTS, **data}
    except (FileNotFoundError, json.JSONDecodeError):
        return dict(_DEFAULTS)

def save(data: dict):
    os.makedirs(_DIR, exist_ok=True)
    current = load()
    current.update(data)
    with open(_PATH, "w", encoding="utf-8") as f:
        json.dump(current, f, indent=2, ensure_ascii=False)

def get(key: str, default=None):
    return load().get(key, default)

def set(key: str, value):
    save({key: value})
