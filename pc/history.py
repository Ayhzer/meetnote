"""
Persistance JSON de l'historique des jobs MeetNote.
Stocké dans %APPDATA%/MeetNote/history.json (100 entrées max).
"""
import os
import json
import datetime
import dataclasses

HISTORY_PATH = os.path.join(os.path.expanduser("~"), "AppData", "Roaming",
                             "MeetNote", "history.json")
MAX_ENTRIES = 100


def _job_to_dict(job) -> dict:
    d = dataclasses.asdict(job)
    # Sérialiser les datetime en ISO string
    if isinstance(d.get("start_time"), datetime.datetime):
        d["start_time"] = d["start_time"].isoformat()
    return d


def _dict_to_job_dict(d: dict) -> dict:
    """Normalise un dict chargé depuis JSON (assure les clés manquantes)."""
    defaults = {
        "meeting_name": "",
        "status_audio": "done",
        "status_transcript": "queued",
        "status_notion": "pending",
        "transcript": "",
        "transcript_path": "",
        "notion_url": "",
        "error_msg": "",
    }
    for k, v in defaults.items():
        if k not in d:
            d[k] = v
    return d


def load() -> list:
    """Charge l'historique depuis le fichier JSON. Retourne une liste de dicts."""
    if not os.path.isfile(HISTORY_PATH):
        return []
    try:
        with open(HISTORY_PATH, "r", encoding="utf-8") as f:
            entries = json.load(f)
        if not isinstance(entries, list):
            return []
        return [_dict_to_job_dict(e) for e in entries]
    except Exception:
        return []


def save(entries: list) -> None:
    """Sauvegarde la liste de dicts dans le fichier JSON."""
    try:
        os.makedirs(os.path.dirname(HISTORY_PATH), exist_ok=True)
        with open(HISTORY_PATH, "w", encoding="utf-8") as f:
            json.dump(entries[:MAX_ENTRIES], f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def add(job) -> None:
    """Ajoute un job en tête de l'historique et sauvegarde."""
    entries = load()
    d = _job_to_dict(job)
    # Supprimer entrée existante avec même id si elle existe
    entries = [e for e in entries if e.get("id") != d["id"]]
    entries.insert(0, d)
    save(entries)


def update(job) -> None:
    """Met à jour un job existant dans l'historique et sauvegarde."""
    entries = load()
    d = _job_to_dict(job)
    updated = False
    for i, e in enumerate(entries):
        if e.get("id") == d["id"]:
            entries[i] = d
            updated = True
            break
    if not updated:
        entries.insert(0, d)
    save(entries)
