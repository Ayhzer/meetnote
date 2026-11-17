"""Tests de la mise en forme de la confirmation « participants informés »."""
import datetime
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))

import information_participants as ip


class TestHorodatageIso(unittest.TestCase):
    def test_a_la_seconde_sans_microsecondes(self):
        maintenant = datetime.datetime(2026, 11, 17, 14, 32, 5, 987654)  # noqa: DTZ001 — locale naïve
        self.assertEqual(ip.horodatage_iso(maintenant), "2026-11-17T14:32:05")


class TestLigneEnTete(unittest.TestCase):
    def test_oui_avec_date_et_heure(self):
        self.assertEqual(ip.ligne_en_tete("2026-11-17T14:32:05"),
                         "Participants informés : oui (le 17/11/2026 à 14:32)")

    def test_non_confirme_si_vide(self):
        self.assertEqual(ip.ligne_en_tete(""), "Participants informés : non confirmé")

    def test_non_confirme_si_iso_invalide(self):
        self.assertEqual(ip.ligne_en_tete("pas une date"),
                         "Participants informés : non confirmé")

    def test_non_confirme_si_none(self):
        self.assertEqual(ip.ligne_en_tete(None), "Participants informés : non confirmé")

    def test_aller_retour_avec_horodatage(self):
        locale = datetime.datetime(2026, 1, 2, 9, 5, 0)  # noqa: DTZ001 — locale naïve
        iso = ip.horodatage_iso(locale)
        self.assertEqual(ip.ligne_en_tete(iso),
                         "Participants informés : oui (le 02/01/2026 à 09:05)")


class TestLignePourEnregistrement(unittest.TestCase):
    def test_enregistrement_confirme(self):
        ligne = ip.ligne_pour_enregistrement("a.opus", "2026-11-17T14:32:05")
        self.assertIn("oui", ligne)

    def test_enregistrement_non_confirme(self):
        self.assertEqual(ip.ligne_pour_enregistrement("a.opus", ""),
                         "Participants informés : non confirmé")

    def test_sans_audio_aucune_ligne(self):
        self.assertEqual(ip.ligne_pour_enregistrement("", "2026-11-17T14:32:05"), "")


class TestLibellesHistorique(unittest.TestCase):
    def test_enregistrement_confirme_en_vert(self):
        self.assertEqual(
            ip.libelles_historique("a.opus", "2026-11-17T14:32:05", "", ""),
            [("✓ Participants informés le 17/11/2026 à 14:32", ip.COULEUR_OK)])

    def test_enregistrement_non_confirme_en_orange(self):
        self.assertEqual(ip.libelles_historique("a.opus", "", "", ""),
                         [("⚠ Information non confirmée", ip.COULEUR_ALERTE)])

    def test_iso_invalide_vaut_non_confirme(self):
        self.assertEqual(ip.libelles_historique("a.opus", "n'importe quoi", "", ""),
                         [("⚠ Information non confirmée", ip.COULEUR_ALERTE)])

    def test_import_sans_ligne_de_confirmation(self):
        self.assertEqual(ip.libelles_historique("", "2026-11-17T14:32:05", "", ""), [])

    def test_suppressions_date_seule_ou_datetime(self):
        libelles = ip.libelles_historique(
            "", "", "2026-11-24", "2027-10-25T03:00:00")
        self.assertEqual(libelles, [
            ("Audio supprimé le 24/11/2026", ip.COULEUR_INFO),
            ("Transcription purgée le 25/10/2027", ip.COULEUR_INFO)])

    def test_suppression_sans_date_lisible(self):
        self.assertEqual(ip.libelles_historique("", "", "???", ""),
                         [("Audio supprimé", ip.COULEUR_INFO)])

    def test_enregistrement_confirme_et_audio_supprime(self):
        libelles = ip.libelles_historique(
            "a.opus", "2026-11-17T14:32:05", "2026-11-24", "")
        self.assertEqual([c for _, c in libelles], [ip.COULEUR_OK, ip.COULEUR_INFO])


if __name__ == "__main__":
    unittest.main()
