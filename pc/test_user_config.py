"""
Tests de user_config (premier lancement, exclusions par defaut) sur un
settings.json temporaire.

Lancement : py -3.12 pc\test_user_config.py
"""
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fichier_json
import user_config as uc


class _BaseTemporaire(unittest.TestCase):
    """Redirige user_config vers un dossier temporaire."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dossier = os.path.join(tmp.name, "MeetNote")
        self.chemin = os.path.join(self.dossier, "settings.json")
        for nom, valeur in (("_DIR", self.dossier), ("_PATH", self.chemin)):
            patcher = patch.object(uc, nom, valeur)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _ecrire(self, data: dict):
        os.makedirs(self.dossier, exist_ok=True)
        with open(self.chemin, "w", encoding="utf-8") as f:
            json.dump(data, f)


class TestUserConfig(_BaseTemporaire):
    def test_premier_lancement_sans_fichier(self):
        self.assertTrue(uc.est_premier_lancement())

    def test_premier_lancement_token_vide(self):
        self._ecrire({"notion_token": "   "})
        self.assertTrue(uc.est_premier_lancement())

    def test_pas_premier_lancement_avec_token(self):
        self._ecrire({"notion_token": "secret_x"})
        self.assertFalse(uc.est_premier_lancement())

    def test_pas_premier_lancement_assistant_termine(self):
        self._ecrire({"assistant_termine": True})
        self.assertFalse(uc.est_premier_lancement())

    def test_assistant_termine_persiste_via_set(self):
        uc.set("assistant_termine", True)
        self.assertFalse(uc.est_premier_lancement())

    def test_exclusions_par_defaut_une_ligne_par_motif(self):
        motifs = uc.EXCLUSIONS_PAR_DEFAUT.splitlines()
        self.assertEqual(
            motifs,
            ["todo", "off", "perso", "privé", "prive", "déjeuner", "dejeuner",
             "trajet", "ne pas enregistrer"],
        )

    def test_exclusions_relues_comme_le_tray(self):
        uc.set("auto_start_exclusions", uc.EXCLUSIONS_PAR_DEFAUT)
        relues = uc.liste_exclusions(uc.get("auto_start_exclusions", ""))
        self.assertEqual(len(relues), 9)

    def test_liste_exclusions_normalise(self):
        self.assertEqual(uc.liste_exclusions(" ToDo \n\n Privé\n"), ["todo", "privé"])

    def test_liste_exclusions_vide_ou_absente(self):
        self.assertEqual(uc.liste_exclusions(""), [])
        self.assertEqual(uc.liste_exclusions(None), [])


class TestSauvegardeAtomique(_BaseTemporaire):
    def _residus(self):
        return [n for n in os.listdir(self.dossier) if n != "settings.json"]

    def _octets(self):
        with open(self.chemin, "rb") as f:
            return f.read()

    def _lire(self):
        with open(self.chemin, "r", encoding="utf-8") as f:
            return json.load(f)

    def test_save_fusionne_sans_temporaire_residuel(self):
        self._ecrire({"ews_email": "a@b.fr", "notion_token": "t"})
        uc.save({"notion_token": "nouveau", "mode": "fichier"})
        self.assertEqual(
            self._lire(),
            {"ews_email": "a@b.fr", "notion_token": "nouveau",
             "notion_database_id": "", "dossier_modeles": "", "whisper_model": "",
             "graph_client_id": "", "graph_tenant_id": "", "mode": "fichier"},
        )
        self.assertEqual(self._residus(), [])

    def test_echec_ecriture_laisse_l_ancien_fichier_intact(self):
        self._ecrire({"ews_email": "a@b.fr"})
        avant = self._octets()
        panne = patch.object(fichier_json.json, "dump",
                             side_effect=OSError("disque plein"))
        with panne, self.assertRaises(OSError):
            uc.save({"mode": "fichier"})
        self.assertEqual(self._octets(), avant)
        self.assertEqual(self._residus(), [])

    def test_fichier_corrompu_renomme_et_defauts_rendus(self):
        os.makedirs(self.dossier, exist_ok=True)
        with open(self.chemin, "w", encoding="utf-8") as f:
            f.write('{"notion_token": "secret_x", "ews_em')
        with self.assertLogs("meetnote", level="WARNING") as logs:
            data = uc.load()
        self.assertEqual(data, dict(uc._DEFAULTS))
        self.assertFalse(os.path.exists(self.chemin))
        conserves = [n for n in os.listdir(self.dossier) if n.startswith("settings.json.corrompu-")]
        self.assertEqual(len(conserves), 1)
        with open(os.path.join(self.dossier, conserves[0]), encoding="utf-8") as f:
            self.assertIn("secret_x", f.read())
        self.assertNotIn("secret_x", "\n".join(logs.output))

    def test_fichier_non_utf8_traite_comme_corrompu(self):
        os.makedirs(self.dossier, exist_ok=True)
        with open(self.chemin, "wb") as f:
            f.write(b"\xff\xfe\x00garbage")
        self.assertEqual(uc.load()["notion_token"], "")
        self.assertTrue(any(n.startswith("settings.json.corrompu-") for n in os.listdir(self.dossier)))

    def test_fichier_absent_pas_de_renommage(self):
        os.makedirs(self.dossier, exist_ok=True)
        self.assertEqual(uc.load(), dict(uc._DEFAULTS))
        self.assertEqual(os.listdir(self.dossier), [])


if __name__ == "__main__":
    unittest.main()
