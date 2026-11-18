"""
guide_pdf.py — Version PDF de GUIDE-INSTALLATION.md, produite à chaque build.

Le guide n'utilise qu'un petit sous-ensemble de Markdown (titres, paragraphes,
listes à puces ou numérotées, gras, code inline, images, liens) : on le convertit
ici en HTML autonome, sans dépendance, puis Microsoft Edge (présent sur tout
Windows 10/11) l'imprime en PDF en mode headless.

Usage :  python pc/guide_pdf.py [sortie.pdf]
"""

import html
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from build_paths import _signaler, dist_dir_name
from version import VERSION

RACINE_DEPOT = Path(__file__).resolve().parent.parent
GUIDE_MD = RACINE_DEPOT / "GUIDE-INSTALLATION.md"
DOSSIER_DIST = Path(__file__).resolve().parent / "dist"

DELAI_MAX_EDGE_S = 90
CLE_REGISTRE_EDGE = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\msedge.exe"
SOUS_DOSSIER_EDGE = os.path.join("Microsoft", "Edge", "Application", "msedge.exe")
LONGUEUR_MAX_DIAGNOSTIC = 300

CSS = """
@page { size: A4; margin: 12mm 14mm; }
body { font-family: "Segoe UI", Arial, sans-serif; font-size: 9.5pt;
       line-height: 1.35; color: #222; }
h1 { font-size: 17pt; margin: 0 0 4pt; color: #1f3a5f; }
h2 { font-size: 11.5pt; margin: 10pt 0 3pt; color: #1f3a5f;
     border-bottom: 1px solid #c8d2e0; }
h3 { font-size: 10pt; margin: 8pt 0 2pt; }
p { margin: 3pt 0; }
ul, ol { margin: 3pt 0; padding-left: 18pt; }
li { margin: 1.5pt 0; }
code { font-family: Consolas, monospace; font-size: 9pt; background: #f0f2f5;
       padding: 0 2pt; }
img { max-width: 48%; max-height: 60mm; border: 1px solid #c8d2e0;
      margin: 2pt 2pt 2pt 0; vertical-align: top; }
a { color: #1a5fb4; }
h2, li, p { break-inside: avoid; }
"""

_TITRE = re.compile(r"^(#{1,3})\s+(.*)$")
_PUCE = re.compile(r"^[-*]\s+(.*)$")
_NUMEROTE = re.compile(r"^\d+\.\s+(.*)$")
_CODE = re.compile(r"`([^`]+)`")
_IMAGE = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)\)")
_LIEN = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
_GRAS = re.compile(r"\*\*(.+?)\*\*")


class ErreurGuidePdf(Exception):
    """Le PDF n'a pas pu être produit (message destiné au bilan du build)."""


# ─── Markdown → HTML ──────────────────────────────────────────────────────────
def _inline_hors_code(texte: str) -> str:
    """Échappe puis met en forme images, liens et gras d'un texte sans code."""
    texte = html.escape(texte)  # échappé d'abord : alt et URL le sont donc aussi
    texte = _IMAGE.sub(r'<img src="\2" alt="\1">', texte)
    texte = _LIEN.sub(r'<a href="\2">\1</a>', texte)
    return _GRAS.sub(r"<strong>\1</strong>", texte)


def convertir_inline(texte: str) -> str:
    """Met en forme une ligne : le contenu d'un code inline n'est pas interprété."""
    morceaux = _CODE.split(texte)  # indices impairs = contenu des `code`
    return "".join(
        f"<code>{html.escape(m)}</code>" if i % 2 else _inline_hors_code(m)
        for i, m in enumerate(morceaux)
    )


def _bloc_liste(lignes: list[str], balise: str) -> str:
    items = "".join(f"<li>{convertir_inline(ligne)}</li>" for ligne in lignes)
    return f"<{balise}>{items}</{balise}>"


def _blocs(markdown: str):
    """Découpe en blocs `(type, lignes)` : titre, ul, ol ou p."""
    courant: tuple[str, list[str]] | None = None
    for ligne in markdown.splitlines():
        ligne = ligne.rstrip()
        titre = _TITRE.match(ligne)
        puce = _PUCE.match(ligne)
        num = _NUMEROTE.match(ligne)
        if titre:
            nature, contenu = f"h{len(titre.group(1))}", titre.group(2)
        elif puce:
            nature, contenu = "ul", puce.group(1)
        elif num:
            nature, contenu = "ol", num.group(1)
        elif ligne.strip():
            nature, contenu = "p", ligne.strip()
        else:
            nature, contenu = "", ""
        # Un titre est toujours seul ; liste et paragraphe se prolongent.
        if courant and nature == courant[0] and nature in ("ul", "ol", "p"):
            courant[1].append(contenu)
            continue
        if courant:
            yield courant
        courant = (nature, [contenu]) if nature else None
    if courant:
        yield courant


def convertir_markdown(markdown: str, racine: Path | None = None) -> str:
    """Document HTML complet. `racine` sert de base aux chemins relatifs (images)."""
    corps = []
    for nature, lignes in _blocs(markdown):
        if nature in ("ul", "ol"):
            corps.append(_bloc_liste(lignes, nature))
        else:
            corps.append(f"<{nature}>{convertir_inline(' '.join(lignes))}</{nature}>")
    base = f'<base href="{html.escape(_uri_dossier(racine))}">' if racine else ""
    return ('<!DOCTYPE html><html lang="fr"><head><meta charset="utf-8">'
            f"<title>Guide d'installation de MeetNote</title>{base}"
            f"<style>{CSS}</style></head><body>{''.join(corps)}</body></html>")


def _uri_dossier(racine: Path) -> str:
    """URI `file:///` du dossier, avec la barre finale qu'exige <base>."""
    return racine.resolve().as_uri().rstrip("/") + "/"


# ─── Recherche de Microsoft Edge ──────────────────────────────────────────────
def _lire_registre(hive_nom: str) -> str | None:
    """Valeur par défaut de la clé App Paths d'Edge, ou None si absente."""
    import winreg  # Windows uniquement, comme l'application
    hive = getattr(winreg, hive_nom)
    try:
        with winreg.OpenKey(hive, CLE_REGISTRE_EDGE) as cle:
            return winreg.QueryValue(cle, None) or None
    except OSError:
        return None


def trouver_edge(environ=None, lire_registre=_lire_registre,
                 existe=os.path.isfile) -> str | None:
    """Chemin de msedge.exe : registre (machine, utilisateur), puis chemins standard."""
    environ = os.environ if environ is None else environ
    candidats = [lire_registre(hive)
                 for hive in ("HKEY_LOCAL_MACHINE", "HKEY_CURRENT_USER")]
    for variable in ("ProgramFiles(x86)", "ProgramFiles"):
        racine = environ.get(variable, "").strip()
        if racine:
            candidats.append(os.path.join(racine, SOUS_DOSSIER_EDGE))
    for chemin in candidats:
        if chemin and existe(chemin):
            return chemin
    return None


# ─── Impression ───────────────────────────────────────────────────────────────
def commande_edge(edge: str, html_uri: str, sortie: str, profil: str) -> list[str]:
    """Ligne de commande d'impression (liste d'arguments, sans shell).

    Un profil temporaire dédié évite que l'appel soit absorbé par un Edge déjà
    ouvert : il rendrait la main sans rien imprimer.
    """
    return [edge, "--headless", "--disable-gpu", "--no-pdf-header-footer",
            f"--user-data-dir={profil}", f"--print-to-pdf={sortie}", html_uri]


def imprimer_pdf(html_texte: str, sortie: Path, edge: str,
                 executer=subprocess.run) -> Path:
    """Imprime le HTML en PDF avec Edge ; lève ErreurGuidePdf en cas d'échec."""
    sortie = Path(sortie).resolve()
    sortie.parent.mkdir(parents=True, exist_ok=True)
    sortie.unlink(missing_ok=True)  # un PDF périmé ne doit pas passer pour le nouveau
    with tempfile.TemporaryDirectory(prefix="meetnote_guide_") as tmp:
        page = Path(tmp) / "guide.html"
        page.write_text(html_texte, encoding="utf-8")
        profil = str(Path(tmp) / "profil")
        commande = commande_edge(edge, page.as_uri(), str(sortie), profil)
        try:
            r = executer(commande, capture_output=True, text=True,
                         timeout=DELAI_MAX_EDGE_S)
        except subprocess.TimeoutExpired as erreur:
            raise ErreurGuidePdf(
                f"Edge n'a pas répondu en {DELAI_MAX_EDGE_S} s") from erreur
        except OSError as erreur:
            raise ErreurGuidePdf(f"Edge n'a pas pu démarrer — {erreur}") from erreur
    if r.returncode != 0:
        detail = (r.stderr or r.stdout or "").strip()[-LONGUEUR_MAX_DIAGNOSTIC:]
        raise ErreurGuidePdf(f"Edge a échoué (code {r.returncode}) {detail}".rstrip())
    if not sortie.is_file() or sortie.stat().st_size == 0:
        raise ErreurGuidePdf("Edge n'a produit aucun PDF")
    return sortie


def generer_guide(sortie: Path, guide: Path = GUIDE_MD, racine: Path = RACINE_DEPOT,
                  edge: str | None = None, executer=subprocess.run) -> Path:
    """Convertit le guide et l'imprime ; lève ErreurGuidePdf si impossible."""
    if not guide.is_file():
        raise ErreurGuidePdf(f"guide introuvable : {guide}")
    edge = edge or trouver_edge()
    if not edge:
        raise ErreurGuidePdf("Microsoft Edge est introuvable")
    texte = convertir_markdown(guide.read_text(encoding="utf-8"), racine)
    return imprimer_pdf(texte, sortie, edge, executer)


def nom_guide_pdf(version: str, slim: bool = False) -> str:
    """Nom du PDF : `MeetNote-<version>-guide.pdf`, à côté de l'installeur."""
    return dist_dir_name(version, slim) + "-guide.pdf"


def produire_guide_pdf(version: str, dossier_sortie: str, slim: bool = False,
                       journal=print, alertes: list[str] | None = None,
                       generer=generer_guide) -> str | None:
    """Dépose le PDF du guide dans `dossier_sortie` ; None (et alerte) si impossible.

    Le build n'échoue jamais pour le guide : l'alerte part au bilan final.
    """
    sortie = Path(dossier_sortie) / nom_guide_pdf(version, slim)
    try:
        generer(sortie)
    except ErreurGuidePdf as erreur:
        _signaler(f"guide PDF non produit : {erreur}", journal, alertes)
        return None
    journal(f"Guide PDF : {sortie} ({sortie.stat().st_size / 1024:.0f} Kio)")
    return str(sortie)


def main(argv: list[str]) -> int:
    sortie = Path(argv[1]) if len(argv) > 1 else DOSSIER_DIST / nom_guide_pdf(VERSION)
    try:
        chemin = generer_guide(sortie)
    except ErreurGuidePdf as erreur:
        print(f"[guide_pdf] ATTENTION : {erreur}")
        return 1
    print(f"[guide_pdf] {chemin}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
