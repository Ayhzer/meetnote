"""
Pousse le transcript dans Notion (page + blocs), avec les pieces jointes eventuelles.
L'audio n'est plus envoye : il reste archive en local (Documents/MeetNote/audio/).
Pieces jointes : POST /v1/file_uploads -> attach a la page (propriete « Files & media »).
"""
import datetime
import os
import sys

import requests

sys.path.insert(0, os.path.dirname(__file__))
import config
from notion_api import NOTION_API, NOTION_VERSION

HEADERS = {
    "Authorization": f"Bearer {config.NOTION_TOKEN}",
    "Content-Type": "application/json",
    "Notion-Version": NOTION_VERSION,
}


def push_to_notion(
    transcript: str,
    source: str = "PC",
    participants: str = "",
    meeting_type: str = "",
    duration_min: float = 0,
    whisper_model: str = "",
    start_time: datetime.datetime = None,
    title_override: str = "",
    attachments: list = None,
    notes: str = "",
) -> dict:
    now   = start_time or datetime.datetime.now()
    title = title_override.strip() if title_override and title_override.strip() else f"Réunion {now.strftime('%Y-%m-%d %H:%M')}"

    blocks = []

    # ── Notes manuelles en tête de page (avant la transcription) ──────────────
    if notes and notes.strip():
        blocks.append(_callout_block("📝 Notes manuelles prises pendant la réunion"))
        for para in notes.split("\n"):
            para = para.strip()
            if not para:
                continue
            while len(para) > 2000:
                blocks.append(_paragraph_block(para[:2000]))
                para = para[2000:]
            if para:
                blocks.append(_paragraph_block(para))
        blocks.append(_divider_block())

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
        # Jamais passe aujourd'hui par les appelants. L'assistant accepte une base
        # dont Participants est en multi_select : ecrire en `select` y echouerait.
        props["Participants"] = {"select": {"name": participants[:100]}}
    if meeting_type:
        props["Type"] = {"select": {"name": meeting_type}}
    if duration_min > 0:
        props["Durée (min)"] = {"number": round(duration_min, 1)}
    if whisper_model:
        props["Modèle Whisper"] = {"select": {"name": whisper_model}}

    # ── Upload pièces jointes si fournies ─────────────────────────────────────
    file_objects = []
    for att_path in (attachments or []):
        if not os.path.isfile(att_path):
            continue
        try:
            fid, fname = _upload_file(att_path)
            file_objects.append({
                "type": "file_upload",
                "file_upload": {"id": fid},
                "name": fname,
            })
        except Exception as e:
            print(f"[notion_push] Warning: attachment upload failed ({att_path}): {e}", file=sys.stderr)

    if file_objects:
        props["Files & media"] = {"files": file_objects}

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


def _find_ffmpeg() -> str | None:
    """Cherche ffmpeg : dans le bundle PyInstaller, puis dans PATH."""
    import shutil
    # Bundle PyInstaller : on peut embarquer ffmpeg.exe dans _MEIPASS
    if getattr(sys, "frozen", False):
        candidate = os.path.join(sys._MEIPASS, "ffmpeg.exe")
        if os.path.isfile(candidate):
            return candidate
    return shutil.which("ffmpeg")


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
        # Audio
        ".opus": "audio/ogg",
        ".ogg":  "audio/ogg",
        ".mp3":  "audio/mpeg",
        ".wav":  "audio/wav",
        ".webm": "audio/webm",
        ".m4a":  "audio/mp4",
        # Documents
        ".pdf":  "application/pdf",
        ".doc":  "application/msword",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".xls":  "application/vnd.ms-excel",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".ppt":  "application/vnd.ms-powerpoint",
        ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        ".txt":  "text/plain",
        ".csv":  "text/csv",
        # Images
        ".png":  "image/png",
        ".jpg":  "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif":  "image/gif",
        ".webp": "image/webp",
        # Archives
        ".zip":  "application/zip",
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

    # Notion limits file name to 100 characters
    if len(filename) > 100:
        ext = os.path.splitext(filename)[1]
        filename = filename[:100 - len(ext)] + ext
    return file_upload_id, filename


def append_blocks_to_page(page_id: str, text: str, token: str) -> None:
    """Appende une section 'Transcript Teams' à une page Notion existante."""
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Notion-Version": NOTION_VERSION,
    }
    blocks = [
        {
            "object": "block",
            "type": "heading_2",
            "heading_2": {
                "rich_text": [{"type": "text", "text": {"content": "─── Transcript Teams ───"}}]
            },
        }
    ]
    for para in text.split("\n"):
        para = para.strip()
        if not para:
            continue
        while len(para) > 2000:
            blocks.append(_paragraph_block(para[:2000]))
            para = para[2000:]
        if para:
            blocks.append(_paragraph_block(para))

    while blocks:
        batch  = blocks[:100]
        blocks = blocks[100:]
        r = requests.patch(
            f"{NOTION_API}/blocks/{page_id}/children",
            json={"children": batch},
            headers=headers,
            timeout=30,
        )
        if not r.ok:
            try:
                detail = r.json()
            except Exception:
                detail = r.text
            raise requests.HTTPError(
                f"append_blocks_to_page : {r.status_code} {r.reason} — {detail}",
                response=r,
            )


def _paragraph_block(text: str) -> dict:
    return {
        "object": "block",
        "type": "paragraph",
        "paragraph": {
            "rich_text": [{"type": "text", "text": {"content": text}}]
        },
    }


def _callout_block(text: str) -> dict:
    return {
        "object": "block",
        "type": "callout",
        "callout": {
            "rich_text": [{"type": "text", "text": {"content": text}}],
            "icon": {"type": "emoji", "emoji": "📝"},
        },
    }


def _divider_block() -> dict:
    return {"object": "block", "type": "divider", "divider": {}}
