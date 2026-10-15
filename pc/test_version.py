"""Vérifie la source unique de version et sa cohérence avec le CHANGELOG.

Lancé par `unittest discover -s pc`, le dossier pc/ est déjà dans sys.path.
"""
import os
import re
import unittest

from version import VERSION

_PC_DIR = os.path.dirname(os.path.abspath(__file__))


class TestVersion(unittest.TestCase):
    def test_format_x_y_z(self):
        self.assertRegex(VERSION, r"^\d+\.\d+\.\d+$")

    def test_entree_changelog_presente(self):
        chemin = os.path.join(_PC_DIR, "CHANGELOG.md")
        with open(chemin, encoding="utf-8") as f:
            contenu = f.read()
        motif = r"^## \[" + re.escape(VERSION) + r"\]"
        self.assertRegex(contenu, re.compile(motif, re.MULTILINE))


if __name__ == "__main__":
    unittest.main()
