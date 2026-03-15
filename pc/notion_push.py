"""
Pousse le transcript dans Notion
"""
import sys
import os
import datetime
import requests

sys.path.insert(0, os.path.dirname(__file__))
import config

NOTION_API = "https://api.notion.com/v1"
HEADERS = {
    "Authorization": f"Bearer {config.NOTION_TOKEN}",
    "Content-Type": "application/json",
    "Notion-Version": "2022-06-28",
}


def push_to_notion(
    transcript: str,
    source: str = "PC",
    participants: str = "",
    meeting_type: str = "",
    duration_min: float = 0,
    whisper_model: str = "",
    start_time: datetime.datetime = None,
) -> dict:
    now   = start_time or datetime.datetime.now()
    title = f"Réunion {now.strftime('%Y-%m-%d %H:%M')}"

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

    payload = {
        "parent":     {"database_id": config.NOTION_DATABASE_ID},
        "properties": props,
        "children":   blocks[:100],
    }

    resp = requests.post(f"{NOTION_API}/pages", json=payload, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    return resp.json()


def _paragraph_block(text: str) -> dict:
    return {
        "object": "block",
        "type": "paragraph",
        "paragraph": {
            "rich_text": [{"type": "text", "text": {"content": text}}]
        },
    }
