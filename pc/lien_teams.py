"""
lien_teams.py — Extraction de l'URL de participation Teams d'une invitation d'agenda.

Module pur : aucun accès à Outlook ni au réseau. L'URL sert à identifier la réunion
(clé `join_url` des dicts d'agenda) ; elle n'est jamais affichée dans l'agenda envoyé
à Notion.
"""
import html
import re

# Deux formats rencontrés : l'ancien « /l/meetup-join/… » et le nouveau « /meet/… ».
# L'URL s'arrête au premier espace, guillemet ou chevron (corps texte ou HTML).
_MOTIF_URL_TEAMS = re.compile(
    r"https://teams\.microsoft\.com/(?:l/meetup-join/|meet/)[^\s\"'<>]+",
    re.IGNORECASE,
)

# Ponctuation de phrase collée à la fin d'une URL dans un corps texte.
_PONCTUATION_FINALE = ".,;"


def url_participation(corps: str | None) -> str:
    """Première URL de participation Teams du corps (texte ou HTML), entités décodées ;
    chaîne vide si le corps n'en contient pas."""
    if not corps:
        return ""
    trouve = _MOTIF_URL_TEAMS.search(str(corps))
    if not trouve:
        return ""
    return html.unescape(trouve.group(0)).rstrip(_PONCTUATION_FINALE)
