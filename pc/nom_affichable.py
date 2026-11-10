"""
nom_affichable.py — Nettoyage d'un nom de personne avant affichage.

Module pur, sans dépendance : partagé par l'agenda (`source_agenda`) et les
transcripts Teams (`transcript_teams`) pour qu'aucune adresse e-mail n'apparaisse
dans un texte produit.
"""
import re

_RE_ADRESSE_ENTRE_CHEVRONS = re.compile(r"<[^>]*>")


def sans_adresse(nom: str) -> str:
    """Nom aux espaces normalisés, sans adresse e-mail ; "" s'il n'en reste rien.

    « Nom <a@b.fr> » devient « Nom » ; une entrée qui n'est qu'une adresse (EWS ou COM
    sans nom résolu) devient vide. Une adresse est toute chaîne contenant « @ ».
    """
    sans_chevrons = _RE_ADRESSE_ENTRE_CHEVRONS.sub(" ", nom)
    propre = " ".join(sans_chevrons.split())
    return "" if "@" in propre else propre
