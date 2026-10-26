"""
Tests de la détection d'agenda de l'assistant (assistant_agenda), sans Outlook réel
ni coffre Windows : `win32com` et `keyring` sont remplacés par des doublures.

Lancement : py -3.12 pc\test_assistant_agenda.py
"""
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import assistant_agenda as aa

REGLAGES_EWS = {"ews_email": "a@exemple.test", "ews_username": "a", "ews_url": ""}


def _faux_win32com(dispatch) -> dict:
    """Modules à injecter dans sys.modules pour que `import win32com.client` réussisse."""
    com = MagicMock()
    com.client.Dispatch = dispatch
    return {"win32com": com, "win32com.client": com.client}


def _faux_keyring(mot_de_passe) -> dict:
    kr = MagicMock()
    kr.get_password.return_value = mot_de_passe
    return {"keyring": kr}


def _com_injoignable():
    """Dispatch qui échoue comme le fait le Nouvel Outlook (classe non enregistrée)."""
    return MagicMock(side_effect=OSError("Chaîne de classe non valide"))


class TestOutlookClassique(unittest.TestCase):
    def test_outlook_joignable(self):
        dispatch = MagicMock()
        with patch.dict(sys.modules, _faux_win32com(dispatch)):
            res = aa.detecter_agenda()
        self.assertEqual(res.mode, aa.MODE_OUTLOOK)
        self.assertEqual(res.phrase, aa.PHRASES[aa.MODE_OUTLOOK])
        dispatch.assert_called_once_with("Outlook.Application")
        dispatch.return_value.GetNamespace.assert_called_once_with("MAPI")

    def test_outlook_prioritaire_sur_ews(self):
        with patch.dict(sys.modules, _faux_win32com(MagicMock())), \
                patch("user_config.get", side_effect=lambda cle, defaut="": REGLAGES_EWS.get(cle, defaut)), \
                patch.dict(sys.modules, _faux_keyring("secret")):
            self.assertEqual(aa.detecter_agenda().mode, aa.MODE_OUTLOOK)

    def test_calendrier_inaccessible_n_est_pas_joignable(self):
        dispatch = MagicMock()
        dispatch.return_value.GetNamespace.return_value.GetDefaultFolder.side_effect = OSError("MAPI")
        with patch.dict(sys.modules, _faux_win32com(dispatch)), \
                patch("outlook_cal._get_ews_credentials", return_value=(None,) * 4):
            self.assertEqual(aa.detecter_agenda().mode, aa.MODE_MANUEL)


class TestEws(unittest.TestCase):
    def test_nouvel_outlook_et_identifiants_donnent_ews(self):
        with patch.dict(sys.modules, _faux_win32com(_com_injoignable())), \
                patch("user_config.get", side_effect=lambda cle, defaut="": REGLAGES_EWS.get(cle, defaut)), \
                patch.dict(sys.modules, _faux_keyring("secret")):
            res = aa.detecter_agenda()
        self.assertEqual(res.mode, aa.MODE_EWS)
        self.assertEqual(res.phrase, aa.PHRASES[aa.MODE_EWS])

    def test_mot_de_passe_absent_du_coffre_donne_manuel(self):
        with patch.dict(sys.modules, _faux_win32com(_com_injoignable())), \
                patch("user_config.get", side_effect=lambda cle, defaut="": REGLAGES_EWS.get(cle, defaut)), \
                patch.dict(sys.modules, _faux_keyring(None)):
            self.assertEqual(aa.detecter_agenda().mode, aa.MODE_MANUEL)


class TestManuel(unittest.TestCase):
    def test_ni_outlook_ni_ews(self):
        with patch.dict(sys.modules, _faux_win32com(_com_injoignable())), \
                patch("outlook_cal._get_ews_credentials", return_value=(None,) * 4):
            res = aa.detecter_agenda()
        self.assertEqual(res.mode, aa.MODE_MANUEL)
        self.assertEqual(res.phrase, aa.PHRASES[aa.MODE_MANUEL])

    def test_pywin32_absent(self):
        # `None` dans sys.modules fait lever ImportError à l'import.
        with patch.dict(sys.modules, {"win32com": None, "win32com.client": None}), \
                patch("outlook_cal._get_ews_credentials", return_value=(None,) * 4):
            self.assertEqual(aa.detecter_agenda().mode, aa.MODE_MANUEL)

    def test_phrases_distinctes_et_non_vides(self):
        phrases = set(aa.PHRASES.values())
        self.assertEqual(len(phrases), 3)
        self.assertTrue(all(p.strip() for p in phrases))


if __name__ == "__main__":
    unittest.main()
