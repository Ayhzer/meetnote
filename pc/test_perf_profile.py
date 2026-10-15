"""
Tests du calibrage de la transcription (perf_profile).

Lancement : py -3.12 pc\\test_perf_profile.py
Pas de dependance a pytest : le projet n'en embarque pas.
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import perf_profile


class TestPlafondMateriel(unittest.TestCase):
    """_fils_max_possibles ne doit jamais promettre plus que le poste ne peut tenir."""

    def test_petit_poste_reste_a_un_flux(self):
        # 4 coeurs : comportement historique preserve, aucun parallelisme.
        self.assertEqual(perf_profile._fils_max_possibles(4, 16.0), 1)

    def test_huit_coeurs_montent_a_deux_flux(self):
        self.assertEqual(perf_profile._fils_max_possibles(8, 16.0), 2)

    def test_plafonne_a_trois_flux(self):
        # Au-dela, le gain mesure est nul et la RAM grimpe pour rien.
        self.assertEqual(perf_profile._fils_max_possibles(64, 128.0), 3)

    def test_ram_insuffisante_ramene_a_un_flux(self):
        # 0,9 Gio libre : pas la place pour deux modeles en vol.
        self.assertEqual(perf_profile._fils_max_possibles(16, 0.9), 1)

    def test_jamais_zero_flux(self):
        self.assertEqual(perf_profile._fils_max_possibles(1, 0.0), 1)


class TestChoixProfil(unittest.TestCase):
    """choisir() arbitre entre vitesse et disponibilite du poste."""

    def setUp(self):
        # Poste de reference : 8 coeurs, 16 Gio libres, poste au repos.
        self._patchs = [
            mock.patch.object(perf_profile, "coeurs_physiques", return_value=8),
            mock.patch.object(perf_profile, "ram_libre_gio", return_value=16.0),
            mock.patch.object(perf_profile, "charge_cpu", return_value=5.0),
        ]
        for p in self._patchs:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in self._patchs])

    def test_poste_au_repos_prend_le_maximum(self):
        p = perf_profile.choisir("auto")
        self.assertEqual((p.fils, p.threads), (2, 4))

    def test_poste_sollicite_lache_un_flux(self):
        with mock.patch.object(perf_profile, "charge_cpu", return_value=80.0):
            p = perf_profile.choisir("auto")
        self.assertEqual(p.fils, 1)

    def test_enregistrement_en_cours_prime_sur_le_mode_rapide(self):
        # La capture audio ne doit pas se faire voler le CPU, meme reglage "rapide".
        p = perf_profile.choisir("rapide", enregistrement_en_cours=True)
        self.assertEqual(p.fils, 1)
        self.assertEqual(p.motif, "enregistrement en cours")

    def test_batterie_ramene_a_un_flux(self):
        p = perf_profile.choisir("rapide", sur_batterie=True)
        self.assertEqual(p.fils, 1)

    def test_mode_econome_ramene_a_un_flux(self):
        p = perf_profile.choisir("econome")
        self.assertEqual(p.fils, 1)

    def test_profil_inconnu_traite_comme_auto(self):
        self.assertEqual(perf_profile.choisir("n_importe_quoi"),
                         perf_profile.choisir("auto"))

    def test_toujours_au_moins_deux_threads_par_flux(self):
        with mock.patch.object(perf_profile, "coeurs_physiques", return_value=3):
            p = perf_profile.choisir("rapide")
        self.assertGreaterEqual(p.threads, 2)


class TestReplisSansPsutil(unittest.TestCase):
    """Les mesures materielles ne doivent jamais faire echouer une transcription."""

    def test_choisir_survit_a_psutil_absent(self):
        with mock.patch.dict(sys.modules, {"psutil": None}):
            p = perf_profile.choisir("auto")
        self.assertGreaterEqual(p.fils, 1)
        self.assertGreaterEqual(p.threads, 2)

    def test_description_poste_est_lisible(self):
        self.assertIn("coeurs", perf_profile.description_poste())


if __name__ == "__main__":
    unittest.main(verbosity=2)
