"""
Persistance JSON de l'historique des jobs MeetNote.
Stocké dans %APPDATA%/MeetNote/history.json (100 entrées max).
"""
import dataclasses
import datetime
import logging
import os
import threading

import fichier_json

_log = logging.getLogger("meetnote")

HISTORY_PATH = os.path.join(os.path.expanduser("~"), "AppData", "Roaming",
                             "MeetNote", "history.json")
MAX_ENTRIES = 100

# Lecture-modification-écriture de `add`/`update` : sans verrou, deux fils (worker,
# interface, passe de conservation) s'écraseraient mutuellement leurs mises à jour.
_verrou_ecriture = threading.Lock()


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
        "teams_participants": [],
        "participants_invites": [],
        "status_audio": "done",
        "status_transcript": "queued",
        "status_notion": "pending",
        "transcript": "",
        "whisper_transcript": "",
        "teams_transcript": "",
        "notes": "",
        "transcript_path": "",
        "notion_url": "",
        "error_msg": "",
        "attachments": [],
        "participants_informes_le": "",
        "audio_supprime_le": "",
        "transcript_purge_le": "",
    }
    for k, v in defaults.items():
        if k not in d:
            d[k] = v
    return d


def champs_job(entree: dict) -> dict:
    """Entrée d'historique -> arguments de `_Job(**...)`.

    Tous les champs du `_Job` sont persistés par `_job_to_dict` ; ils doivent
    donc tous être relus ici, sinon la reprise au démarrage les réécrit vides
    dans history.json (notes manuelles, transcriptions et dates de traçabilité
    perdues). Tout nouveau champ de `_Job` doit donc être ajouté ici.
    `start_time` absent ou non textuel : maintenant. Texte invalide : ValueError.
    """
    st = entree.get("start_time", "")
    if isinstance(st, str) and st:
        start = datetime.datetime.fromisoformat(st)
    else:
        start = datetime.datetime.now()  # noqa: DTZ005 — heure locale naïve voulue
    return {
        "id": entree.get("id", ""),
        "wav_path": entree.get("wav_path", ""),
        "start_time": start,
        "duration_min": entree.get("duration_min", 0),
        "model_name": entree.get("model_name", "base"),
        "language": entree.get("language", "fr"),
        "meeting_type": entree.get("meeting_type", ""),
        "output_mode": entree.get("output_mode", "notion"),
        "meeting_name": entree.get("meeting_name", ""),
        "teams_participants": entree.get("teams_participants", []),
        "participants_invites": entree.get("participants_invites", []),
        "status_audio": entree.get("status_audio", "done"),
        "status_transcript": entree.get("status_transcript", "done"),
        "status_notion": entree.get("status_notion", "done"),
        "transcript": entree.get("transcript", ""),
        "whisper_transcript": entree.get("whisper_transcript", ""),
        "teams_transcript": entree.get("teams_transcript", ""),
        "notes": entree.get("notes", ""),
        "transcript_path": entree.get("transcript_path", ""),
        "notion_url": entree.get("notion_url", ""),
        "error_msg": entree.get("error_msg", ""),
        "attachments": entree.get("attachments", []),
        "participants_informes_le": entree.get("participants_informes_le", ""),
        "audio_supprime_le": entree.get("audio_supprime_le", ""),
        "transcript_purge_le": entree.get("transcript_purge_le", ""),
    }


def load() -> list:
    """Charge l'historique. Absent : []. Illisible : mis de cote + WARNING + []."""
    entries = fichier_json.lire_ou_ecarter(HISTORY_PATH, list)
    if entries is None:
        return []
    return [_dict_to_job_dict(e) for e in entries if isinstance(e, dict)]


def save(entries: list) -> None:
    """Sauvegarde de facon atomique. Un echec est journalise, jamais propage :
    les appelants (worker de transcription, UI) ne l'attendent pas, et l'ancien
    fichier reste intact."""
    try:
        fichier_json.ecrire_atomique(HISTORY_PATH, entries[:MAX_ENTRIES],
                                     ensure_ascii=False, indent=2)
    except (OSError, TypeError, ValueError) as exc:
        _log.warning("history.json non sauvegarde (%s) : ancien fichier conserve", exc)


def add(job) -> None:
    """Ajoute un job en tête de l'historique et sauvegarde."""
    with _verrou_ecriture:
        entries = load()
        d = _job_to_dict(job)
        # Supprimer entrée existante avec même id si elle existe
        entries = [e for e in entries if e.get("id") != d["id"]]
        entries.insert(0, d)
        save(entries)


def update(job) -> None:
    """Met à jour un job existant dans l'historique et sauvegarde."""
    with _verrou_ecriture:
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
