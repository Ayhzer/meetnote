"""
teams_roster.py — Capture de la liste des participants d'une réunion Teams active.

Stratégie : lecture du log Teams (MSTeams.log ou logs/MSTeams_*.log) qui
contient des entrées JSON avec les noms des participants entrant/sortant.
Aucune dépendance externe, aucune API Graph nécessaire.

Fonctionne avec Microsoft Teams Classic et Teams New (MSAL-based).
"""

import os
import re
import glob
import json
import datetime
import threading
import time


# ─── Chemins connus des logs Teams ────────────────────────────────────────────
_TEAMS_LOG_PATTERNS = [
    # Teams Classic
    os.path.join(os.environ.get("APPDATA", ""), "Microsoft", "Teams", "logs.txt"),
    # Teams New (version 2.x)
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "Packages",
                 "MSTeams_8wekyb3d8bbwe", "LocalCache", "Microsoft", "MSTeams", "Logs",
                 "MSTeams_*.log"),
    # Teams entreprise (EXE)
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "Teams", "logs.txt"),
]

# Regex pour extraire un participant depuis les logs Teams Classic
# Exemple : "name":"Jean Dupont"  ou  "displayName":"Jean Dupont"
_RE_NAME = re.compile(
    r'"(?:displayName|name|userDisplayName)"\s*:\s*"([^"]{2,80})"',
    re.IGNORECASE,
)

# Mots-clés indiquant un événement participant dans les logs
_PARTICIPANT_KEYWORDS = [
    "participantAdded", "participantRemoved", "rosterUpdate",
    "callParticipant", "meetingParticipant",
    "addedParticipant", "removedParticipant",
    "nameChanged",
]


def _find_teams_log() -> str | None:
    """Retourne le chemin du fichier de log Teams le plus récent, ou None."""
    candidates = []
    for pattern in _TEAMS_LOG_PATTERNS:
        if "*" in pattern:
            matches = glob.glob(pattern)
            candidates.extend(matches)
        elif os.path.isfile(pattern):
            candidates.append(pattern)

    if not candidates:
        return None

    # Prendre le plus récemment modifié
    candidates.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return candidates[0]


def _extract_names_from_log(log_path: str, since: datetime.datetime) -> list[str]:
    """
    Parcourt le log Teams depuis `since` et extrait tous les noms uniques
    trouvés dans les lignes liées aux événements participants.
    """
    names: set[str] = set()
    since_ts = since.timestamp()

    try:
        # Lire les dernières lignes (log peut être volumineux)
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            # Chercher depuis la fin pour les logs récents
            f.seek(0, 2)
            size = f.tell()
            # Lire les 2 Mo les plus récents maximum
            start = max(0, size - 2 * 1024 * 1024)
            f.seek(start)
            lines = f.readlines()
    except OSError:
        return []

    for line in lines:
        # Filtre rapide : la ligne doit contenir un mot-clé participant
        if not any(kw in line for kw in _PARTICIPANT_KEYWORDS):
            continue

        # Extraire tous les noms de la ligne
        for m in _RE_NAME.finditer(line):
            raw = m.group(1).strip()
            # Filtrer les valeurs qui ressemblent à des IDs ou emails techniques
            if _is_valid_name(raw):
                names.add(raw)

    return sorted(names)


def _is_valid_name(s: str) -> bool:
    """Vérifie qu'une chaîne ressemble à un vrai nom de personne."""
    if len(s) < 2 or len(s) > 80:
        return False
    # Exclure les UUIDs, emails, URLs
    if re.search(r"[0-9a-f]{8}-[0-9a-f]{4}", s, re.IGNORECASE):
        return False
    if "@" in s or s.startswith("http"):
        return False
    # Doit contenir au moins une lettre
    if not re.search(r"[a-zA-ZÀ-ÿ]", s):
        return False
    # Pas que des chiffres
    if s.replace(" ", "").isdigit():
        return False
    return True


def is_teams_running() -> bool:
    """Retourne True si un processus Teams est en cours d'exécution."""
    try:
        import subprocess
        result = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq ms-teams.exe", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=5,
            creationflags=0x08000000,  # CREATE_NO_WINDOW
        )
        if "ms-teams.exe" in result.stdout.lower():
            return True
        # Teams New
        result2 = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq MSTeams.exe", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=5,
            creationflags=0x08000000,
        )
        return "msteams.exe" in result2.stdout.lower()
    except Exception:
        return False


def get_participants(since: datetime.datetime | None = None) -> list[str]:
    """
    Retourne la liste des participants Teams détectés depuis `since`.
    Si `since` est None, cherche sur les 4 dernières heures.
    Retourne [] si Teams n'est pas détecté ou si aucun participant trouvé.
    """
    if since is None:
        since = datetime.datetime.now() - datetime.timedelta(hours=4)

    log_path = _find_teams_log()
    if not log_path:
        return []

    return _extract_names_from_log(log_path, since)


# ─── Surveillance en temps réel pendant l'enregistrement ──────────────────────

class TeamsRosterWatcher:
    """
    Surveille les participants Teams en arrière-plan pendant l'enregistrement.
    Collecte : nom → liste de timestamps (quand détecté dans les logs).
    """

    def __init__(self):
        self._participants: dict[str, list[datetime.datetime]] = {}
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._start_time: datetime.datetime | None = None
        self._log_path: str | None = None

    def start(self, recording_start: datetime.datetime):
        """Démarre la surveillance."""
        self._start_time = recording_start
        self._stop.clear()
        self._participants.clear()
        self._log_path = _find_teams_log()
        self._thread = threading.Thread(target=self._watch_loop, daemon=True)
        self._thread.start()

    def stop(self):
        """Arrête la surveillance et retourne les participants collectés."""
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)

    def _watch_loop(self):
        while not self._stop.wait(timeout=15):
            if not self._log_path:
                self._log_path = _find_teams_log()
                continue
            since = self._start_time or (datetime.datetime.now() - datetime.timedelta(hours=1))
            names = _extract_names_from_log(self._log_path, since)
            now = datetime.datetime.now()
            with self._lock:
                for name in names:
                    if name not in self._participants:
                        self._participants[name] = []
                    self._participants[name].append(now)

    def get_participants(self) -> list[str]:
        """Liste des noms uniques détectés (triée)."""
        with self._lock:
            return sorted(self._participants.keys())

    def get_participants_with_times(self) -> dict[str, list[datetime.datetime]]:
        with self._lock:
            return dict(self._participants)
