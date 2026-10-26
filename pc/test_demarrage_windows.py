"""Tests des règles de démarrage avec Windows (winreg mocké, registre réel intact)."""
import unittest
from unittest import mock

import demarrage_windows as dw

EXE = r"C:\Users\moi\AppData\Local\Programs\MeetNote\MeetNote.exe"
ANCIEN_EXE = r"D:\Outils\MeetNote-3.3.0\MeetNote.exe"
CMD_FIGEE = f'"{EXE}" --minimized'
CMD_DEV = r'"C:\Python312\pythonw.exe" "C:\DEV\meetnote\pc\watchdog.py" --minimized'


class CommandeTest(unittest.TestCase):
    def test_figee_est_l_exe_et_rien_d_autre(self):
        cmd = dw.commande_demarrage(True, EXE, r"C:\interne")
        self.assertEqual(cmd, CMD_FIGEE)
        self.assertNotIn("--enfant", cmd)
        self.assertNotIn(".py", cmd)

    def test_source_passe_par_watchdog_et_pythonw(self):
        cmd = dw.commande_demarrage(
            False, r"C:\Python312\python.exe", r"C:\DEV\meetnote\pc",
            existe=lambda _: True)
        self.assertEqual(cmd, CMD_DEV)

    def test_source_sans_pythonw_garde_l_interpreteur(self):
        cmd = dw.commande_demarrage(
            False, r"C:\Python312\python.exe", r"C:\pc", existe=lambda _: False)
        self.assertTrue(cmd.startswith(r'"C:\Python312\python.exe" '))


class NomExecutableTest(unittest.TestCase):
    def test_avec_guillemets(self):
        self.assertEqual(dw.nom_executable(CMD_FIGEE), "meetnote.exe")

    def test_sans_guillemets_et_casse_ignoree(self):
        commande = r"C:\X\MEETNOTE.EXE --minimized"
        self.assertEqual(dw.nom_executable(commande), "meetnote.exe")

    def test_vide(self):
        self.assertEqual(dw.nom_executable(""), "")
        self.assertEqual(dw.nom_executable(None), "")


class DecisionFigeeTest(unittest.TestCase):
    def decider(self, valeur):
        return dw.decision_au_demarrage(True, valeur, CMD_FIGEE)

    def test_cle_absente_n_est_pas_creee(self):
        self.assertEqual(self.decider(None), dw.RIEN)
        self.assertEqual(self.decider(""), dw.RIEN)

    def test_cle_conforme_ne_bouge_pas(self):
        self.assertEqual(self.decider(CMD_FIGEE), dw.RIEN)

    def test_cle_sur_watchdog_n_est_jamais_ecrasee(self):
        self.assertEqual(self.decider(CMD_DEV), dw.RIEN)

    def test_cle_sur_ancien_emplacement_est_recalee(self):
        self.assertEqual(self.decider(f'"{ANCIEN_EXE}" --minimized'), dw.RECALER)

    def test_casse_du_nom_ignoree(self):
        self.assertEqual(self.decider(r'"D:\x\meetnote.EXE" --minimized'), dw.RECALER)

    def test_ancien_format_avec_script_interne_est_recale(self):
        self.assertEqual(
            self.decider(f'"{EXE}" "C:\\_MEI123\\meetnote-tray.py" --minimized'),
            dw.RECALER)

    def test_cle_sur_un_autre_programme_est_laissee(self):
        self.assertEqual(self.decider(r'"C:\Autre\outil.exe" --minimized'), dw.RIEN)


class DecisionSourceTest(unittest.TestCase):
    def test_cle_absente_ou_differente_est_ecrite(self):
        self.assertEqual(dw.decision_au_demarrage(False, None, CMD_DEV), dw.ECRIRE)
        self.assertEqual(
            dw.decision_au_demarrage(False, CMD_FIGEE, CMD_DEV), dw.ECRIRE)

    def test_cle_conforme_ne_bouge_pas(self):
        self.assertEqual(dw.decision_au_demarrage(False, CMD_DEV, CMD_DEV), dw.RIEN)


class RegistreTest(unittest.TestCase):
    def setUp(self):
        self.winreg = mock.MagicMock()
        self.cle = self.winreg.OpenKey.return_value.__enter__.return_value

    def test_lecture_rend_la_valeur(self):
        self.winreg.QueryValueEx.return_value = (CMD_FIGEE, 1)
        self.assertEqual(dw.lire_valeur(self.winreg), CMD_FIGEE)
        self.winreg.OpenKey.assert_called_once_with(
            self.winreg.HKEY_CURRENT_USER, dw.CLE_REGISTRE)

    def test_lecture_cle_absente_rend_none(self):
        self.winreg.QueryValueEx.side_effect = FileNotFoundError
        self.assertIsNone(dw.lire_valeur(self.winreg))

    def test_case_reflete_la_cle(self):
        self.winreg.QueryValueEx.return_value = (CMD_DEV, 1)
        self.assertTrue(dw.est_active(self.winreg))
        self.winreg.QueryValueEx.side_effect = FileNotFoundError
        self.assertFalse(dw.est_active(self.winreg))

    def test_activation_ecrit_la_commande(self):
        dw.definir_active(self.winreg, True, CMD_FIGEE)
        self.winreg.SetValueEx.assert_called_once_with(
            self.cle, dw.NOM_VALEUR, 0, self.winreg.REG_SZ, CMD_FIGEE)

    def test_desactivation_retire_la_valeur(self):
        dw.definir_active(self.winreg, False, CMD_FIGEE)
        self.winreg.DeleteValue.assert_called_once_with(self.cle, dw.NOM_VALEUR)
        self.winreg.SetValueEx.assert_not_called()

    def test_desactivation_sans_cle_ne_leve_pas(self):
        self.winreg.DeleteValue.side_effect = FileNotFoundError
        dw.definir_active(self.winreg, False, CMD_FIGEE)


if __name__ == "__main__":
    unittest.main()
