"""
Tests de l'etiquetage des locuteurs (diarisation_etiquettes).

Lancement : py -3.12 pc\test_diarisation_etiquettes.py
"""
import os
import sys
import unittest
from typing import ClassVar

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import diarisation_etiquettes as de


class TestOrdreApparition(unittest.TestCase):
    def test_numerotation_dans_l_ordre_d_apparition(self):
        # Les labels DBSCAN n'ont aucun ordre : 5 apparait avant 2.
        self.assertEqual(de.etiqueter_par_ordre_apparition([5, 2, 5, 0]),
                         ["SPKR_1", "SPKR_2", "SPKR_1", "SPKR_3"])

    def test_bruit_sans_locuteur_et_sans_numero_consomme(self):
        self.assertEqual(de.etiqueter_par_ordre_apparition([-1, 3, -1, 4]),
                         ["", "SPKR_1", "", "SPKR_2"])

    def test_aucun_suffixe_de_genre(self):
        for etiquette in de.etiqueter_par_ordre_apparition([0, 1, 2]):
            self.assertRegex(etiquette, r"^SPKR_\d+$")

    def test_liste_vide(self):
        self.assertEqual(de.etiqueter_par_ordre_apparition([]), [])


class TestRenommageTeams(unittest.TestCase):
    ETIQUETTES: ClassVar[dict[int, str]] = {0: "SPKR_1", 1: "SPKR_2", 3: "SPKR_1", 4: "SPKR_3"}

    def test_renomme_dans_l_ordre_d_apparition(self):
        res = de.renommer_par_noms(self.ETIQUETTES, ["Alice", "Bob", "Chloé"])
        self.assertEqual(res, {0: "Alice", 1: "Bob", 3: "Alice", 4: "Chloé"})

    def test_plus_de_locuteurs_que_de_noms(self):
        res = de.renommer_par_noms(self.ETIQUETTES, ["Alice"])
        self.assertEqual(res, {0: "Alice", 1: "SPKR_2", 3: "Alice", 4: "SPKR_3"})

    def test_plus_de_noms_que_de_locuteurs(self):
        res = de.renommer_par_noms({0: "SPKR_1"}, ["Alice", "Bob"])
        self.assertEqual(res, {0: "Alice"})

    def test_sans_noms_rien_ne_change(self):
        self.assertEqual(de.renommer_par_noms(self.ETIQUETTES, []), self.ETIQUETTES)

    def test_l_ordre_suit_les_indices_pas_l_insertion(self):
        res = de.renommer_par_noms({7: "SPKR_2", 2: "SPKR_1"}, ["Alice", "Bob"])
        self.assertEqual(res, {7: "Bob", 2: "Alice"})

    def test_pas_de_suffixe_ajoute(self):
        res = de.renommer_par_noms({0: "SPKR_1"}, ["Alice"])
        self.assertEqual(res[0], "Alice")


if __name__ == "__main__":
    unittest.main()
