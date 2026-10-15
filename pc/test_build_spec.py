"""Tests de la résolution des chemins Python des scripts de build."""

import os
import sys
import unittest
from unittest import mock

import build_paths

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


if __name__ == "__main__":
    unittest.main()
