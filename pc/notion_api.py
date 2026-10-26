"""
notion_api.py — Constantes de l'API Notion partagees.

Module sans dependance (ni `config`, ni reseau) : `notion_push` (jeton de la
configuration) et `assistant_notion` (jeton passe en parametre) y puisent la meme
adresse et la meme version, sans que l'un importe `config`, gitignore, pour l'autre.
"""

NOTION_API = "https://api.notion.com/v1"
NOTION_VERSION = "2026-03-11"
