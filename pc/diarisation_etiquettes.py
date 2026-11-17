"""
Etiquetage des locuteurs issus de la diarisation (fonctions pures, sans audio).

Les locuteurs sont nommes `SPKR_1`, `SPKR_2`... dans leur ordre d'apparition, puis
remplaces par les noms Teams quand ils sont connus. Aucune caracteristique de la voix
(hauteur, sexe) n'est calculee ni affichee : seul le regroupement par timbre sert.
"""

PREFIXE_LOCUTEUR = "SPKR_"
LABEL_BRUIT = -1  # etiquette DBSCAN d'un segment rattache a aucun locuteur


def etiqueter_par_ordre_apparition(labels) -> list[str]:
    """
    Convertit des etiquettes de cluster en `SPKR_N`, numerotes de 1 dans l'ordre
    d'apparition. Un segment de bruit (`-1`) reste sans locuteur : chaine vide.
    """
    numeros: dict = {}
    etiquettes = []
    for label in labels:
        if label == LABEL_BRUIT:
            etiquettes.append("")
            continue
        if label not in numeros:
            numeros[label] = len(numeros) + 1
        etiquettes.append(f"{PREFIXE_LOCUTEUR}{numeros[label]}")
    return etiquettes


def renommer_par_noms(etiquettes: dict[int, str], noms) -> dict[int, str]:
    """
    Remplace les `SPKR_N` par `noms`, dans l'ordre d'apparition des locuteurs
    (segments pris par indice croissant). Quand il y a plus de locuteurs que de
    noms, les derniers gardent leur `SPKR_N`.
    """
    noms = list(noms)
    if not noms:
        return dict(etiquettes)
    correspondance: dict[str, str] = {}
    for idx in sorted(etiquettes):
        etiquette = etiquettes[idx]
        if etiquette and etiquette not in correspondance and len(correspondance) < len(noms):
            correspondance[etiquette] = noms[len(correspondance)]
    return {idx: correspondance.get(etiquette, etiquette)
            for idx, etiquette in etiquettes.items()}
