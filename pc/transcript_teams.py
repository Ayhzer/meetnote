"""
transcript_teams.py — Conversion d'un transcript Teams (WebVTT) en texte lisible.

Module pur : aucun accès fichier, Outlook ni réseau. Les répliques consécutives sont
regroupées en paragraphes `[hh:mm:ss] Nom : texte`, séparés par une ligne vide.
"""
import html
import re
from dataclasses import dataclass

from nom_affichable import sans_adresse

# Un nouveau paragraphe commence quand le silence entre deux répliques dépasse ce seuil.
SEUIL_SILENCE_S = 8.0

# Taille maximale d'un paragraphe : un long monologue sans silence est coupé à la
# réplique suivante. Volontairement bien sous la limite de 2000 caractères
# d'un bloc Notion.
TAILLE_MAX_PARAGRAPHE = 600

_SECONDES_PAR_MINUTE = 60
_SECONDES_PAR_HEURE = 3600

_HORODATAGE = r"(?:(\d+):)?(\d{1,2}):(\d{2})[.,](\d{3})"
_MOTIF_LIGNE_TEMPS = re.compile(rf"^\s*{_HORODATAGE}\s*-->\s*{_HORODATAGE}")
_MOTIF_LOCUTEUR = re.compile(r"<v(?:\.[^\s>]*)*\s+([^>]+)>", re.IGNORECASE)
_MOTIF_BALISE = re.compile(r"<[^>]*>")


@dataclass
class _Replique:
    debut: float
    fin: float
    locuteur: str
    texte: str


def _en_secondes(heures, minutes, secondes, millis) -> float:
    return (int(heures or 0) * _SECONDES_PAR_HEURE
            + int(minutes) * _SECONDES_PAR_MINUTE
            + int(secondes) + int(millis) / 1000)


def _formater_horodatage(secondes: float) -> str:
    heures, reste = divmod(int(secondes), _SECONDES_PAR_HEURE)
    minutes, sec = divmod(reste, _SECONDES_PAR_MINUTE)
    return f"[{heures:02d}:{minutes:02d}:{sec:02d}]"


def _nettoyer(lignes: list[str]) -> tuple[str, str]:
    """(locuteur, texte) d'une réplique : locuteur de la première balise `<v Nom>`
    (vide s'il n'y en a pas), balises retirées, lignes jointes par une espace."""
    brut = " ".join(ligne.strip() for ligne in lignes)
    trouve = _MOTIF_LOCUTEUR.search(brut)
    locuteur = trouve.group(1).strip() if trouve else ""
    texte = html.unescape(_MOTIF_BALISE.sub("", brut))
    return locuteur, " ".join(texte.split())


def _lire_repliques(vtt: str) -> list[_Replique]:
    """Extrait les répliques ; en-tête, numéros, notes et lignes vides sont ignorés
    (seul ce qui suit une ligne `début --> fin` jusqu'à la ligne vide est du texte)."""
    repliques: list[_Replique] = []
    lignes_texte: list[str] | None = None
    courante: tuple[float, float] | None = None

    def clore():
        if courante is not None and lignes_texte:
            locuteur, texte = _nettoyer(lignes_texte)
            if texte:
                repliques.append(_Replique(courante[0], courante[1], locuteur, texte))

    for ligne in vtt.splitlines():
        temps = _MOTIF_LIGNE_TEMPS.match(ligne)
        if temps:
            clore()
            g = temps.groups()
            courante = (_en_secondes(*g[0:4]), _en_secondes(*g[4:8]))
            lignes_texte = []
        elif not ligne.strip():
            clore()
            courante, lignes_texte = None, None
        elif lignes_texte is not None:
            lignes_texte.append(ligne)
    clore()
    return repliques


def _commence_paragraphe(precedente: _Replique, suivante: _Replique,
                         locuteur_courant: str, taille_courante: int) -> bool:
    """Vrai si `suivante` ouvre un nouveau paragraphe (locuteur, silence ou taille)."""
    if suivante.locuteur and suivante.locuteur != locuteur_courant:
        return True
    if suivante.debut - precedente.fin > SEUIL_SILENCE_S:
        return True
    return taille_courante + 1 + len(suivante.texte) > TAILLE_MAX_PARAGRAPHE


def vtt_vers_texte(vtt: str | None) -> str:
    """Convertit un transcript WebVTT Teams en paragraphes lisibles ; "" si vide.

    Un paragraphe est préfixé de l'horodatage de sa première réplique, puis de
    « Nom : » si une balise `<v Nom>` l'attribue. Sans balise, aucun locuteur
    n'est inventé. Un locuteur identifié par une adresse e-mail n'est jamais
    affiché (même filtre que l'agenda) : le paragraphe n'a alors pas de nom, mais
    reste séparé de celui des autres locuteurs (le regroupement se fait sur la
    balise brute, qui n'apparaît pas dans le texte).
    """
    repliques = _lire_repliques(vtt or "")
    if not repliques:
        return ""
    paragraphes: list[str] = []
    premiere = repliques[0]
    locuteur = premiere.locuteur
    morceaux = [premiere.texte]
    precedente = premiere

    def clore():
        prefixe = _formater_horodatage(premiere.debut)
        nom = sans_adresse(locuteur)
        if nom:
            prefixe += f" {nom} :"
        paragraphes.append(f"{prefixe} {' '.join(morceaux)}")

    for suivante in repliques[1:]:
        taille = sum(len(m) for m in morceaux) + len(morceaux) - 1
        if _commence_paragraphe(precedente, suivante, locuteur, taille):
            clore()
            premiere, locuteur, morceaux = suivante, suivante.locuteur, []
        morceaux.append(suivante.texte)
        precedente = suivante
    clore()
    return "\n\n".join(paragraphes)
