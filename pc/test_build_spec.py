"""Tests de la résolution des chemins Python des scripts de build."""

import getpass
import os
import re
import sys
import tempfile
import unittest
from unittest import mock

import build_paths
import build_version
import version

AUTRE_RACINE = r"D:\Python\Custom"


class ResolvePythonHomeTest(unittest.TestCase):
    def test_defaut_est_base_prefix(self):
        with mock.patch.dict(os.environ):
            os.environ.pop(build_paths.ENV_PYTHON_HOME, None)
            self.assertEqual(build_paths.resolve_python_home(), sys.base_prefix)

    def test_variable_d_environnement_prise_en_compte(self):
        env = {build_paths.ENV_PYTHON_HOME: AUTRE_RACINE}
        with mock.patch.dict(os.environ, env):
            self.assertEqual(build_paths.resolve_python_home(), AUTRE_RACINE)

    def test_variable_vide_ignoree(self):
        for valeur in ("", "   "):
            with mock.patch.dict(os.environ, {build_paths.ENV_PYTHON_HOME: valeur}):
                self.assertEqual(build_paths.resolve_python_home(), sys.base_prefix)

    def test_site_packages_derive_de_la_racine(self):
        attendu = os.path.join(AUTRE_RACINE, "Lib", "site-packages")
        resolu = build_paths.resolve_site_packages(AUTRE_RACINE)
        self.assertEqual(resolu, attendu)

    def test_site_packages_sans_argument_suit_la_resolution(self):
        env = {build_paths.ENV_PYTHON_HOME: AUTRE_RACINE}
        with mock.patch.dict(os.environ, env):
            attendu = os.path.join(AUTRE_RACINE, "Lib", "site-packages")
            self.assertEqual(build_paths.resolve_site_packages(), attendu)


class DistDirNameTest(unittest.TestCase):
    def test_dossier_complet_porte_la_version(self):
        self.assertEqual(build_paths.dist_dir_name("3.3.0"), "MeetNote-3.3.0")

    def test_dossier_slim_porte_la_version(self):
        attendu = "MeetNote-slim-3.3.0"
        self.assertEqual(build_paths.dist_dir_name("3.3.0", slim=True), attendu)

    def test_espaces_ignores(self):
        self.assertEqual(build_paths.dist_dir_name(" 1.2.3 "), "MeetNote-1.2.3")


class VersionTupleTest(unittest.TestCase):
    def test_complete_a_quatre_composantes(self):
        self.assertEqual(build_version.version_tuple("3.3.0"), (3, 3, 0, 0))
        self.assertEqual(build_version.version_tuple("1.2"), (1, 2, 0, 0))
        self.assertEqual(build_version.version_tuple("1.2.3.4"), (1, 2, 3, 4))

    def test_format_invalide_refuse(self):
        for invalide in ("", "3.x.0", "3.3.0-beta", "1.2.3.4.5", "3..0"):
            with self.subTest(invalide=invalide), self.assertRaises(ValueError):
                build_version.version_tuple(invalide)


class VersionResourceTest(unittest.TestCase):
    def setUp(self):
        self.texte = build_version.build_version_resource("3.3.0")

    def test_version_en_tuple_et_en_chaine(self):
        self.assertIn("filevers=(3, 3, 0, 0)", self.texte)
        self.assertIn("prodvers=(3, 3, 0, 0)", self.texte)
        self.assertIn("StringStruct('FileVersion', '3.3.0')", self.texte)
        self.assertIn("StringStruct('ProductVersion', '3.3.0')", self.texte)

    def test_suit_version_py(self):
        texte = build_version.build_version_resource(version.VERSION)
        self.assertIn(f"StringStruct('FileVersion', '{version.VERSION}')", texte)

    def test_identite_du_produit(self):
        for champ, valeur in (("ProductName", "MeetNote"),
                              ("FileDescription", "MeetNote"),
                              ("OriginalFilename", "MeetNote.exe")):
            self.assertIn(f"StringStruct('{champ}', '{valeur}')", self.texte)

    def test_aucune_donnee_personnelle_ni_chemin(self):
        self.assertNotIn(getpass.getuser().lower(), self.texte.lower())
        self.assertNotIn(os.path.expanduser("~").lower(), self.texte.lower())
        self.assertIsNone(re.search(r"[A-Za-z]:[/\\]", self.texte))

    def test_texte_relu_par_pyinstaller(self):
        """Le texte est relu par l'outil de PyInstaller (même eval que le build)."""
        try:
            from PyInstaller.utils.win32 import versioninfo
        except ImportError:
            self.skipTest("PyInstaller non installé")
        with tempfile.TemporaryDirectory() as dossier:
            chemin = os.path.join(dossier, "v.txt")
            with open(chemin, "w", encoding="utf-8") as f:
                f.write(self.texte)
            info = versioninfo.load_version_info_from_text_file(chemin)
        # 3.3.0.0 → MS = (3 << 16) | 3, LS = 0
        self.assertEqual(info.ffi.fileVersionMS, (3 << 16) | 3)
        self.assertEqual(info.ffi.fileVersionLS, 0)
        self.assertEqual(info.ffi.productVersionMS, (3 << 16) | 3)
        champs = {c.name: c.val for c in info.kids[0].kids[0].kids}
        self.assertEqual(champs["FileVersion"], "3.3.0")
        self.assertEqual(champs["ProductName"], "MeetNote")


if __name__ == "__main__":
    unittest.main()
