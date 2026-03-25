"""
Pousse le transcript + l'enregistrement audio dans Notion.
Upload audio : wav -> opus (via ffmpeg) -> POST /v1/file_uploads -> attach a la page.
"""
import sys
import os
import datetime
import subprocess
import tempfile
import requests

sys.path.insert(0, os.path.dirname(__file__))
import config

NOTION_API = "https://api.notion.com/v1"
NOTION_VERSION = "2026-03-11"

HEADERS = {
    "Authorization": f"Bearer {config.NOTION_TOKEN}",
    "Content-Type": "application/json",
    "Notion-Version": NOTION_VERSION,
}

# Bitrate opus cible : 24 kbps mono → ~10.8 Mo/heure → 2h = ~21.6 Mo
# Sur plan payant Notion (5 GiB) c'est OK.
# Sur plan gratuit (5 MiB) : max ~27 min — on l'indique dans le nom du fichier.
AUDIO_BITRATE = "24k"


def push_to_notion(
    transcript: str,
    source: str = "PC",
    participants: str = "",
    meeting_type: str = "",
    duration_min: float = 0,
    whisper_model: str = "",
    start_time: datetime.datetime = None,
    audio_path: str = None,
    title_override: str = "",
) -> dict:
    now   = start_time or datetime.datetime.now()
    title = title_override.strip() if title_override and title_override.strip() else f"Réunion {now.strftime('%Y-%m-%d %H:%M')}"

    blocks = []
    for para in transcript.split("\n"):
        para = para.strip()
        if not para:
            continue
        while len(para) > 2000:
            blocks.append(_paragraph_block(para[:2000]))
            para = para[2000:]
        if para:
            blocks.append(_paragraph_block(para))

    props = {
        "Titre":  {"title": [{"text": {"content": title}}]},
        "Date":   {"date": {"start": now.isoformat()}},
        "Source": {"select": {"name": source}},
        "Statut": {"select": {"name": "À traiter"}},
    }

    if participants:
        props["Participants"] = {"select": {"name": participants[:100]}}
    if meeting_type:
        props["Type"] = {"select": {"name": meeting_type}}
    if duration_min > 0:
        props["Durée (min)"] = {"number": round(duration_min, 1)}
    if whisper_model:
        props["Modèle Whisper"] = {"select": {"name": whisper_model}}

    # ── Upload audio si fourni ────────────────────────────────────────────────
    if audio_path and os.path.isfile(audio_path):
        try:
            compressed = _compress_audio(audio_path, now)
            segments   = _split_audio_for_upload(compressed, segment_min=config.NOTION_UPLOAD_SEGMENT_MIN)

            file_objects = []
            for seg_path in segments:
                fid, fname = _upload_file(seg_path)
                file_objects.append({
                    "type": "file_upload",
                    "file_upload": {"id": fid},
                    "name": fname,
                })
                if seg_path != compressed and seg_path != audio_path:
                    try: os.remove(seg_path)
                    except OSError: pass

            if compressed != audio_path:
                try: os.remove(compressed)
                except OSError: pass

            if file_objects:
                props["Files & media"] = {"files": file_objects}

        except Exception as e:
            # L'upload audio est non-bloquant : on continue sans fichier
            print(f"[notion_push] Warning: audio upload failed: {e}", file=sys.stderr)

    # Découpage en batches de 100 blocs (limite API Notion)
    first_batch  = blocks[:100]
    extra_blocks = blocks[100:]

    payload = {
        "parent":     {"database_id": config.NOTION_DATABASE_ID},
        "properties": props,
        "children":   first_batch,
    }

    resp = requests.post(f"{NOTION_API}/pages", json=payload, headers=HEADERS, timeout=30)
    if not resp.ok:
        try:
            detail = resp.json()
        except Exception:
            detail = resp.text
        raise requests.HTTPError(
            f"{resp.status_code} {resp.reason} — {detail}", response=resp
        )
    page = resp.json()
    page_id = page["id"]

    # Ajouter les blocs supplémentaires via PATCH /blocks/{id}/children
    while extra_blocks:
        batch = extra_blocks[:100]
        extra_blocks = extra_blocks[100:]
        r = requests.patch(
            f"{NOTION_API}/blocks/{page_id}/children",
            json={"children": batch},
            headers=HEADERS,
            timeout=30,
        )
        if not r.ok:
            try:
                detail = r.json()
            except Exception:
                detail = r.text
            raise requests.HTTPError(
                f"Ajout blocs supplémentaires : {r.status_code} {r.reason} — {detail}",
                response=r,
            )

    return page


def _compress_audio(wav_path: str, dt: datetime.datetime) -> str:
    """Compresse wav -> opus via ffmpeg. Retourne le chemin du fichier compressé."""
    ffmpeg = _find_ffmpeg()
    if not ffmpeg:
        return wav_path  # pas de ffmpeg → wav brut

    fname = f"meetnote_{dt.strftime('%Y%m%d_%H%M%S')}.opus"
    out   = os.path.join(tempfile.gettempdir(), fname)

    cmd = [
        ffmpeg, "-y", "-i", wav_path,
        "-c:a", "libopus",
        "-b:a", AUDIO_BITRATE,
        "-ac", "1",       # mono
        "-ar", "16000",   # 16 kHz (déjà le cas, mais on force)
        out,
    ]
    result = subprocess.run(cmd, capture_output=True, timeout=120,
                            creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg error: {result.stderr.decode(errors='replace')[-300:]}")
    return out


def _find_ffmpeg() -> str | None:
    """Cherche ffmpeg : dans le bundle PyInstaller, puis dans PATH."""
    import shutil
    # Bundle PyInstaller : on peut embarquer ffmpeg.exe dans _MEIPASS
    if getattr(sys, "frozen", False):
        candidate = os.path.join(sys._MEIPASS, "ffmpeg.exe")
        if os.path.isfile(candidate):
            return candidate
    return shutil.which("ffmpeg")


def _find_ffprobe() -> str | None:
    """Cherche ffprobe : déduit du chemin ffmpeg, puis dans PATH."""
    import shutil
    ffmpeg = _find_ffmpeg()
    if ffmpeg:
        # Cherche ffprobe dans le même dossier que ffmpeg
        ffprobe = os.path.join(os.path.dirname(ffmpeg),
                               "ffprobe.exe" if sys.platform == "win32" else "ffprobe")
        if os.path.isfile(ffprobe):
            return ffprobe
    return shutil.which("ffprobe")


def _split_audio_for_upload(opus_path: str, segment_min: int = 10) -> list:
    """
    Découpe un fichier opus en segments de N minutes via ffmpeg.
    Retourne la liste des chemins de segments.
    Si le fichier est assez court, retourne [opus_path] sans modification.
    """
    import json as _json
    import glob as _glob

    ffmpeg  = _find_ffmpeg()
    ffprobe = _find_ffprobe()

    if not ffmpeg:
        return [opus_path]

    # Déterminer la durée
    duration_s = None
    if ffprobe:
        try:
            r = subprocess.run(
                [ffprobe, "-v", "quiet", "-print_format", "json",
                 "-show_format", opus_path],
                capture_output=True, timeout=15,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            if r.returncode == 0:
                duration_s = float(_json.loads(r.stdout)["format"]["duration"])
        except Exception:
            pass

    # Si on n'a pas pu mesurer la durée, on découpe si le fichier > 50 Mo
    if duration_s is None:
        size_mb = os.path.getsize(opus_path) / (1024 * 1024)
        if size_mb <= segment_min * 0.3:   # ~0.3 Mo/min à 24 kbps
            return [opus_path]
        # durée estimée
        duration_s = size_mb / 0.3 * 60

    if duration_s <= segment_min * 60:
        return [opus_path]

    base    = os.path.splitext(opus_path)[0]
    pattern = f"{base}_part%03d.opus"
    cmd = [
        ffmpeg, "-y", "-i", opus_path,
        "-f", "segment",
        "-segment_time", str(segment_min * 60),
        "-c", "copy",
        pattern,
    ]
    result = subprocess.run(cmd, capture_output=True, timeout=600,
                            creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode != 0:
        return [opus_path]   # échec silencieux → envoyer le fichier entier

    parts = sorted(_glob.glob(f"{base}_part*.opus"))
    return parts if parts else [opus_path]


def _upload_file(path: str) -> tuple[str, str]:
    """
    Upload un fichier vers Notion en 2 étapes :
    1. POST /v1/file_uploads  → obtient file_upload_id
    2. POST /v1/file_uploads/{id}/send  → envoie le binaire
    Retourne (file_upload_id, filename).
    """
    filename = os.path.basename(path)
    ext      = os.path.splitext(filename)[1].lower()
    mime     = {
        ".opus": "audio/ogg",
        ".ogg":  "audio/ogg",
        ".mp3":  "audio/mpeg",
        ".wav":  "audio/wav",
        ".webm": "audio/webm",
    }.get(ext, "application/octet-stream")

    auth_headers = {
        "Authorization": f"Bearer {config.NOTION_TOKEN}",
        "Notion-Version": NOTION_VERSION,
    }

    # Étape 1 — créer l'objet file_upload
    r1 = requests.post(
        f"{NOTION_API}/file_uploads",
        json={"filename": filename, "content_type": mime},
        headers={**auth_headers, "Content-Type": "application/json"},
        timeout=30,
    )
    r1.raise_for_status()
    file_upload_id = r1.json()["id"]

    # Étape 2 — envoyer le binaire
    with open(path, "rb") as f:
        r2 = requests.post(
            f"{NOTION_API}/file_uploads/{file_upload_id}/send",
            headers=auth_headers,
            files={"file": (filename, f, mime)},
            timeout=300,
        )
    r2.raise_for_status()

    return file_upload_id, filename


def _paragraph_block(text: str) -> dict:
    return {
        "object": "block",
        "type": "paragraph",
        "paragraph": {
            "rich_text": [{"type": "text", "text": {"content": text}}]
        },
    }
