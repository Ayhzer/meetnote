"""Tests de corbeille.py : `win32com.shell` est toujours remplacé par un faux."""
import os
import tempfile
import types
import unittest
from unittest import mock

import corbeille

FOF_ALLOWUNDO = 0x40
FOF_NOCONFIRMATION = 0x10
FOF_SILENT = 0x4
FOF_NOERRORUI = 0x400
FOF_WANTNUKEWARNING = 0x4000
FO_DELETE = 3


def _faux_shell(retour=(0, False), exception=None):
    """Couple (shell, shellcon) factice ; `shell.SHFileOperation` est un Mock."""
    shell = types.SimpleNamespace(SHFileOperation=mock.Mock(
        return_value=retour, side_effect=exception))
    shellcon = types.SimpleNamespace(
        FO_DELETE=FO_DELETE, FOF_ALLOWUNDO=FOF_ALLOWUNDO,
        FOF_NOCONFIRMATION=FOF_NOCONFIRMATION, FOF_SILENT=FOF_SILENT,
        FOF_NOERRORUI=FOF_NOERRORUI, FOF_WANTNUKEWARNING=FOF_WANTNUKEWARNING)
    return shell, shellcon


class EnvoyerALaCorbeilleTest(unittest.TestCase):
    def setUp(self):
        fd, self.chemin = tempfile.mkstemp(suffix=".opus")
        os.close(fd)
        self.addCleanup(lambda: os.path.exists(self.chemin) and os.remove(self.chemin))

    def _appeler(self, faux):
        with mock.patch.object(corbeille, "_charger_shell", return_value=faux):
            return corbeille.envoyer_a_la_corbeille(self.chemin)

    def test_succes_rend_none_et_appelle_shfileoperation_sans_dialogue(self):
        faux = _faux_shell()
        self.assertIsNone(self._appeler(faux))
        (operation,), _ = faux[0].SHFileOperation.call_args
        _hwnd, fonction, source, destination, drapeaux, *_ = operation
        self.assertEqual(fonction, FO_DELETE)
        self.assertEqual(source, os.path.abspath(self.chemin))
        self.assertIsNone(destination)
        self.assertEqual(drapeaux, FOF_ALLOWUNDO | FOF_NOCONFIRMATION
                         | FOF_SILENT | FOF_NOERRORUI)
        self.assertFalse(drapeaux & FOF_WANTNUKEWARNING)

    def test_code_retour_non_nul_rend_un_motif(self):
        motif = self._appeler(_faux_shell(retour=(0x78, False)))
        self.assertIn("0x78", motif)

    def test_operation_annulee_rend_un_motif(self):
        self.assertEqual(self._appeler(_faux_shell(retour=(0, True))),
                         corbeille.MOTIF_ANNULEE)

    def test_exception_rend_un_motif_sans_se_propager(self):
        motif = self._appeler(_faux_shell(exception=RuntimeError("COM")))
        self.assertIn("RuntimeError", motif)

    def test_chemin_inexistant_ne_touche_pas_au_shell(self):
        faux = _faux_shell()
        absent = self.chemin + ".absent"
        with mock.patch.object(corbeille, "_charger_shell", return_value=faux):
            motif = corbeille.envoyer_a_la_corbeille(absent)
        self.assertEqual(motif, corbeille.MOTIF_INTROUVABLE)
        faux[0].SHFileOperation.assert_not_called()

    def test_pywin32_absent_rend_un_motif(self):
        with mock.patch.object(corbeille, "_charger_shell", side_effect=ImportError):
            motif = corbeille.envoyer_a_la_corbeille(self.chemin)
        self.assertEqual(motif, corbeille.MOTIF_SHELL_ABSENT)
        self.assertTrue(os.path.exists(self.chemin))


if __name__ == "__main__":
    unittest.main()
