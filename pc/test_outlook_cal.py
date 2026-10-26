"""
Tests de la lecture d'agenda (outlook_cal et ews_cal), sans Outlook, sans serveur
Exchange ni coffre Windows : `win32com`, `ews_cal` et `keyring` sont des doublures.

Contrat vérifié : une source rend une liste (éventuellement vide), ou None quand elle
est indisponible — jamais [] pour dire « en panne ».

Lancement : py -3.12 pc\\test_outlook_cal.py
"""
import datetime
import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ews_cal
import outlook_cal


def _le_26(heure: int) -> datetime.datetime:
    """Datetime naïf local du 26/10/2026 à `heure` h (format de l'agenda)."""
    return datetime.datetime.fromisoformat(f"2026-10-26T{heure:02d}:00")


DEBUT = _le_26(9)
FIN = _le_26(18)
IDENTIFIANTS = ("a@exemple.test", "a", "secret", "https://ews.exemple.test/ews")
SANS_IDENTIFIANTS = (None, None, None, None)


def _evt(sujet, heure, duree_min=60, **extra):
    debut = _le_26(heure)
    evt = {"subject": sujet, "start": debut,
           "end": debut + datetime.timedelta(minutes=duree_min),
           "location": "", "body": "", "organizer": "",
           "required": [], "optional": [], "is_recurring": False}
    evt.update(extra)
    return evt


def _item_com(sujet="Point", heure=10, duree_min=60, journee=False, **extra):
    """Doublure d'un AppointmentItem Outlook."""
    debut = _le_26(heure)
    attrs = {
        "Subject": sujet, "AllDayEvent": journee, "Start": debut,
        "End": debut + datetime.timedelta(minutes=duree_min),
        "Body": "  ordre   du jour ", "Location": "Salle 1", "Organizer": "Alice",
        "RequiredAttendees": "Bob; Carol", "OptionalAttendees": "Dan; Eve",
        "IsRecurring": True,
    }
    attrs.update(extra)
    return SimpleNamespace(**attrs)


def _faux_win32com(items=None, dispatch_leve=None):
    """Remplace `outlook_cal.win32com` ; renvoie (module factice, liste des items)."""
    liste = MagicMock()
    liste.Restrict.return_value = list(items or [])
    calendrier = MagicMock()
    calendrier.Items = liste
    espace = MagicMock()
    espace.GetDefaultFolder.return_value = calendrier
    appli = MagicMock()
    appli.GetNamespace.return_value = espace
    win32com = MagicMock()
    if dispatch_leve:
        win32com.client.Dispatch.side_effect = dispatch_leve
    else:
        win32com.client.Dispatch.return_value = appli
    return win32com, liste


_PAR_DEFAUT = object()


class TestEvenementsCom(unittest.TestCase):
    def _lire(self, win32com, disponible=True, pythoncom=_PAR_DEFAUT):
        """`pythoncom` est toujours une doublure : aucun vrai CoInitialize."""
        if pythoncom is _PAR_DEFAUT:
            pythoncom = MagicMock()
        with patch.object(outlook_cal, "_COM_AVAILABLE", disponible), \
                patch.object(outlook_cal, "win32com", win32com, create=True), \
                patch.object(outlook_cal, "pythoncom", pythoncom):
            return outlook_cal.evenements_com(DEBUT, FIN)

    def test_win32com_absent_donne_none(self):
        self.assertIsNone(self._lire(MagicMock(), disponible=False))

    def test_dispatch_qui_leve_donne_none(self):
        win32com, _ = _faux_win32com(dispatch_leve=OSError("classe non valide"))
        self.assertIsNone(self._lire(win32com))

    def test_aucune_reunion_donne_liste_vide(self):
        win32com, _ = _faux_win32com(items=[])
        self.assertEqual(self._lire(win32com), [])

    def test_format_complet_avec_participants(self):
        win32com, liste = _faux_win32com(items=[_item_com()])
        res = self._lire(win32com)
        self.assertEqual(len(res), 1)
        evt = res[0]
        self.assertEqual(evt["subject"], "Point")
        self.assertEqual(evt["start"], _le_26(10))
        self.assertEqual(evt["body"], "ordre du jour")
        self.assertEqual(evt["required"], ["Bob", "Carol"])
        self.assertEqual(evt["optional"], ["Dan", "Eve"])
        self.assertTrue(evt["is_recurring"])
        liste.Restrict.assert_called_once_with(
            "[Start] <= '26/10/2026 18:00' AND [End] >= '26/10/2026 09:00'")

    def test_filtres_communs(self):
        items = [
            _item_com("Journée", journee=True),
            _item_com("OOO"),
            _item_com("  "),
            _item_com("Marathon", duree_min=13 * 60),
            _item_com("Gardée", heure=14),
        ]
        win32com, _ = _faux_win32com(items=items)
        res = self._lire(win32com)
        self.assertEqual([e["subject"] for e in res], ["Gardée"])

    def test_item_illisible_ignore_sans_arreter_la_lecture(self):
        casse = SimpleNamespace(Subject="X", AllDayEvent=False, Start=None, End=None)
        win32com, _ = _faux_win32com(items=[casse, _item_com("Bon")])
        res = self._lire(win32com)
        self.assertEqual([e["subject"] for e in res], ["Bon"])

    def test_participants_avec_virgule_dans_le_nom(self):
        item = _item_com(RequiredAttendees="Martin, Alice; Durand, Bob")
        win32com, _ = _faux_win32com(items=[item])
        res = self._lire(win32com)
        self.assertEqual(res[0]["required"], ["Martin, Alice", "Durand, Bob"])


class TestMotifDateWindows(unittest.TestCase):
    """Conversion du motif de date courte Windows en motif strftime (fonction pure)."""

    def test_motifs_de_locales_courantes(self):
        cas = {
            "dd/MM/yyyy": "%d/%m/%Y",   # fr-FR
            "M/d/yyyy": "%m/%d/%Y",     # en-US
            "dd.MM.yyyy": "%d.%m.%Y",   # de-DE
            "yyyy/MM/dd": "%Y/%m/%d",   # ja-JP
            "d-M-yy": "%d-%m-%Y",
            "yyyy'年'M'月'd'日'": "%Y年%m月%d日",
        }
        for windows, attendu in cas.items():
            with self.subTest(motif=windows):
                self.assertEqual(outlook_cal.motif_strftime_depuis_windows(windows), attendu)

    def test_motifs_non_pris_en_charge_levent(self):
        for motif in ("ddd dd/MM/yyyy", "dd MMM yyyy", "dd/MM/yyyy g", "dd/MM", "", "dd/dd/MM/yyyy"):
            with self.subTest(motif=motif), self.assertRaises(ValueError):
                outlook_cal.motif_strftime_depuis_windows(motif)


class TestFiltreRestrictionCom(unittest.TestCase):
    DU_26 = datetime.datetime.fromisoformat("2026-10-26T10:06")
    AU_10_NOV = datetime.datetime.fromisoformat("2026-11-10T10:06")

    def _filtre(self, **patch_lecture):
        with patch.object(outlook_cal, "_lire_format_date_courte", **patch_lecture):
            return outlook_cal.filtre_restriction_com(self.DU_26, self.AU_10_NOV)

    def test_fr_fr_borne_de_fin_en_jour_puis_mois(self):
        filtre = self._filtre(return_value="dd/MM/yyyy")
        self.assertIn("'10/11/2026 10:06'", filtre)
        self.assertEqual(filtre, "[Start] <= '10/11/2026 10:06' AND [End] >= '26/10/2026 10:06'")

    def test_en_us_borne_de_fin_en_mois_puis_jour(self):
        self.assertIn("'11/10/2026 10:06'", self._filtre(return_value="M/d/yyyy"))

    def test_repli_si_lecture_en_echec_avertit_une_fois(self):
        outlook_cal._repli_signale = False
        with self.assertLogs(outlook_cal.log, level="WARNING") as journal:
            filtre = self._filtre(side_effect=OSError("API en échec"))
            self._filtre(side_effect=OSError("API en échec"))
        self.assertIn("'11/10/2026 10:06'", filtre)
        self.assertEqual(len(journal.records), 1)

    def test_repli_si_motif_inexploitable(self):
        outlook_cal._repli_signale = True  # avertissement déjà émis : pas de journal attendu
        self.assertIn("'11/10/2026 10:06'", self._filtre(return_value="ddd d MMM yyyy"))


class _ItemSensibiliteIllisible(SimpleNamespace):
    """AppointmentItem dont la lecture de Sensitivity lève (comme un com_error)."""

    @property
    def Sensitivity(self):
        raise OSError("com_error")


class TestSensibiliteCom(unittest.TestCase):
    def test_prive_est_vrai(self):
        self.assertTrue(outlook_cal._evenement_com(_item_com(Sensitivity=2))["prive"])

    def test_normal_est_faux(self):
        self.assertFalse(outlook_cal._evenement_com(_item_com(Sensitivity=0))["prive"])

    def test_personnel_et_confidentiel_ne_sont_pas_prives(self):
        for valeur in (1, 3):
            with self.subTest(sensibilite=valeur):
                self.assertFalse(
                    outlook_cal._evenement_com(_item_com(Sensitivity=valeur))["prive"])

    def test_attribut_absent_est_faux(self):
        self.assertFalse(outlook_cal._evenement_com(_item_com())["prive"])

    def test_attribut_qui_leve_ne_fait_pas_tomber_la_lecture(self):
        item = _ItemSensibiliteIllisible(**vars(_item_com()))
        evt = outlook_cal._evenement_com(item)
        self.assertFalse(evt["prive"])
        self.assertEqual(evt["subject"], "Point")


class TestSplitRecipients(unittest.TestCase):
    def test_virgule_fait_partie_du_nom(self):
        self.assertEqual(outlook_cal._split_recipients("Martin, Alice; Durand, Bob"),
                         ["Martin, Alice", "Durand, Bob"])

    def test_espaces_et_entrees_vides_ignores(self):
        self.assertEqual(outlook_cal._split_recipients("A ;; B ; "), ["A", "B"])

    def test_chaine_vide(self):
        self.assertEqual(outlook_cal._split_recipients(""), [])


class TestContexteCom(unittest.TestCase):
    """COM est initialisé pour le thread de lecture et toujours libéré."""

    def _lire(self, win32com, pythoncom):
        with patch.object(outlook_cal, "_COM_AVAILABLE", True), \
                patch.object(outlook_cal, "win32com", win32com, create=True), \
                patch.object(outlook_cal, "pythoncom", pythoncom):
            return outlook_cal.evenements_com(DEBUT, FIN)

    @staticmethod
    def _appels(parent):
        """Noms des appels de premier niveau, dans l'ordre."""
        return [c[0] for c in parent.mock_calls
                if c[0] in ("CoInitialize", "Dispatch", "CoUninitialize")]

    @staticmethod
    def _avec_parent(win32com, pythoncom):
        parent = MagicMock()
        parent.attach_mock(pythoncom.CoInitialize, "CoInitialize")
        parent.attach_mock(pythoncom.CoUninitialize, "CoUninitialize")
        parent.attach_mock(win32com.client.Dispatch, "Dispatch")
        return parent

    def test_coinitialize_avant_dispatch_et_couninitialize_apres(self):
        win32com, _ = _faux_win32com(items=[_item_com()])
        pythoncom = MagicMock()
        parent = self._avec_parent(win32com, pythoncom)
        res = self._lire(win32com, pythoncom)
        self.assertEqual(len(res), 1)
        self.assertEqual(self._appels(parent),
                         ["CoInitialize", "Dispatch", "CoUninitialize"])

    def test_couninitialize_appele_meme_si_la_lecture_leve(self):
        win32com, _ = _faux_win32com(dispatch_leve=OSError("Outlook fermé"))
        pythoncom = MagicMock()
        parent = self._avec_parent(win32com, pythoncom)
        self.assertIsNone(self._lire(win32com, pythoncom))
        self.assertEqual(self._appels(parent),
                         ["CoInitialize", "Dispatch", "CoUninitialize"])

    def test_sans_pythoncom_la_lecture_fonctionne(self):
        win32com, _ = _faux_win32com(items=[_item_com("Seul")])
        res = self._lire(win32com, None)
        self.assertEqual([e["subject"] for e in res], ["Seul"])


class TestEvenementsEws(unittest.TestCase):
    def _lire(self, identifiants, ews_module):
        with patch.object(outlook_cal, "_get_ews_credentials", return_value=identifiants), \
                patch.dict(sys.modules, {"ews_cal": ews_module}):
            return outlook_cal.evenements_ews(DEBUT, FIN)

    def test_sans_identifiants_donne_none(self):
        faux = MagicMock()
        self.assertIsNone(self._lire(SANS_IDENTIFIANTS, faux))
        faux.evenements.assert_not_called()

    def test_delegue_a_ews_cal_avec_la_fenetre(self):
        faux = MagicMock()
        faux.evenements.return_value = [_evt("A", 10)]
        res = self._lire(IDENTIFIANTS, faux)
        self.assertEqual(res, [_evt("A", 10)])
        faux.evenements.assert_called_once_with(*IDENTIFIANTS, DEBUT, FIN)

    def test_liste_vide_est_conservee(self):
        faux = MagicMock()
        faux.evenements.return_value = []
        self.assertEqual(self._lire(IDENTIFIANTS, faux), [])

    def test_ews_injoignable_donne_none(self):
        faux = MagicMock()
        faux.evenements.return_value = None
        self.assertIsNone(self._lire(IDENTIFIANTS, faux))

    def test_exception_donne_none(self):
        faux = MagicMock()
        faux.evenements.side_effect = RuntimeError("boum")
        self.assertIsNone(self._lire(IDENTIFIANTS, faux))


class TestIdentifiantsEws(unittest.TestCase):
    def _lire(self, reglages, mot_de_passe):
        user_config = MagicMock()
        user_config.get.side_effect = lambda cle, defaut="": reglages.get(cle, defaut)
        keyring = MagicMock()
        keyring.get_password.return_value = mot_de_passe
        with patch.dict(sys.modules, {"user_config": user_config, "keyring": keyring}):
            return outlook_cal._get_ews_credentials()

    def test_identifiants_complets(self):
        reglages = {"ews_email": "a@x.test", "ews_username": "a", "ews_url": "u"}
        self.assertEqual(self._lire(reglages, "pw"), ("a@x.test", "a", "pw", "u"))

    def test_mot_de_passe_absent_du_coffre(self):
        reglages = {"ews_email": "a@x.test", "ews_username": "a"}
        self.assertEqual(self._lire(reglages, None), SANS_IDENTIFIANTS)

    def test_email_absent(self):
        self.assertEqual(self._lire({}, "pw"), SANS_IDENTIFIANTS)


class TestChaineEtCompatibilite(unittest.TestCase):
    """EWS puis COM ; les trois fonctions historiques s'appuient sur evenements_*."""

    def _avec(self, ews, com):
        return (patch.object(outlook_cal, "evenements_ews", return_value=ews),
                patch.object(outlook_cal, "evenements_com", return_value=com))

    def test_ews_repond_com_nest_pas_appele(self):
        p_ews, p_com = self._avec([_evt("E", 10)], [_evt("C", 11)])
        with p_ews, p_com as com:
            res = outlook_cal.get_events_range(days=1)
        self.assertEqual([e["subject"] for e in res], ["E"])
        com.assert_not_called()

    def test_ews_vide_ne_declenche_pas_com(self):
        p_ews, p_com = self._avec([], [_evt("C", 11)])
        with p_ews, p_com as com:
            self.assertEqual(outlook_cal.get_events_range(days=1), [])
        com.assert_not_called()

    def test_ews_indisponible_bascule_sur_com(self):
        p_ews, p_com = self._avec(None, [_evt("C", 11)])
        with p_ews, p_com:
            res = outlook_cal.get_events_range(days=1)
        self.assertEqual([e["subject"] for e in res], ["C"])

    def test_aucune_source_donne_liste_vide_ou_none(self):
        p_ews, p_com = self._avec(None, None)
        with p_ews, p_com:
            self.assertEqual(outlook_cal.get_events_range(days=1), [])
            self.assertEqual(outlook_cal.get_all_current_meetings(), [])
            self.assertIsNone(outlook_cal.get_current_or_next_meeting())

    def test_fenetre_de_get_events_range(self):
        with patch.object(outlook_cal, "evenements_ews", return_value=[]) as ews:
            outlook_cal.get_events_range(days=2)
        debut, fin = ews.call_args.args
        self.assertEqual(fin - debut, datetime.timedelta(days=2))

    def test_fenetre_en_minutes_et_projection(self):
        evts = [_evt("B", 11, location="Salle"), _evt("A", 10)]
        with patch.object(outlook_cal, "evenements_ews", return_value=evts) as ews, \
                patch.object(outlook_cal, "evenements_com"):
            res = outlook_cal.get_all_current_meetings(window_minutes=20)
        debut, fin = ews.call_args.args
        self.assertEqual(fin - debut, datetime.timedelta(minutes=20))
        self.assertEqual([e["subject"] for e in res], ["A", "B"])  # trié par début
        self.assertEqual(set(res[0]), {"subject", "start", "end"})

    def test_courante_ou_prochaine_est_la_premiere_par_debut(self):
        evts = [_evt("Plus tard", 11), _evt("En cours", 10)]
        with patch.object(outlook_cal, "evenements_ews", return_value=evts):
            res = outlook_cal.get_current_or_next_meeting(window_minutes=30)
        self.assertEqual(res["subject"], "En cours")
        self.assertEqual(set(res), {"subject", "start", "end"})


class _VueEws:
    """Doublure de account.calendar : garde les bornes reçues par view()."""

    def __init__(self, items):
        self.items = items
        self.bornes = None

    def view(self, start, end):
        self.bornes = (start, end)
        return iter(self.items)


def _participant(nom):
    return SimpleNamespace(mailbox=SimpleNamespace(name=nom, email_address=""))


def _item_ews(sujet="Point", heure=10, duree_min=60, journee=False, **extra):
    debut = _le_26(heure).astimezone()
    return SimpleNamespace(
        **extra,
        subject=sujet, is_all_day=journee,
        start=debut, end=debut + datetime.timedelta(minutes=duree_min),
        text_body=" ordre  du jour ", location="Salle 2",
        organizer=SimpleNamespace(name="Alice"),
        required_attendees=[_participant("Bob")],
        optional_attendees=[_participant("Dan")],
        is_recurring=False,
    )


class TestEwsCalEvenements(unittest.TestCase):
    """ews_cal.evenements, avec exchangelib remplacé par des doublures."""

    def _lire(self, items=None, disponible=True, compte_leve=None,
              identifiants=IDENTIFIANTS):
        vue = _VueEws(items or [])
        faux_ewsdt = SimpleNamespace(from_datetime=lambda dt: dt)
        if compte_leve:
            make = MagicMock(side_effect=compte_leve)
        else:
            make = MagicMock(return_value=SimpleNamespace(calendar=vue))
        with patch.object(ews_cal, "_AVAILABLE", disponible), \
                patch.object(ews_cal, "EWSDateTime", faux_ewsdt, create=True), \
                patch.object(ews_cal, "_make_account", make):
            res = ews_cal.evenements(*identifiants, DEBUT, FIN)
        return res, vue

    def test_exchangelib_absent_donne_none(self):
        self.assertIsNone(self._lire(disponible=False)[0])

    def test_identifiants_manquants_donne_none(self):
        self.assertIsNone(self._lire(identifiants=("a@x.test", "a", "", ""))[0])

    def test_connexion_en_echec_donne_none(self):
        self.assertIsNone(self._lire(compte_leve=OSError("injoignable"))[0])

    def test_aucune_reunion_donne_liste_vide(self):
        self.assertEqual(self._lire(items=[])[0], [])

    def test_bornes_envoyees_en_utc(self):
        _, vue = self._lire(items=[])
        debut, fin = vue.bornes
        self.assertEqual(debut.utcoffset(), datetime.timedelta(0))
        self.assertEqual(debut.timestamp(), DEBUT.timestamp())
        self.assertEqual(fin.timestamp(), FIN.timestamp())

    def test_format_filtres_et_tri(self):
        items = [
            _item_ews("Tard", heure=15),
            _item_ews("Journée", journee=True),
            _item_ews("Off"),
            _item_ews("Marathon", duree_min=13 * 60),
            _item_ews("Tôt", heure=10),
        ]
        res, _ = self._lire(items=items)
        self.assertEqual([e["subject"] for e in res], ["Tôt", "Tard"])
        evt = res[0]
        self.assertEqual(evt["start"], _le_26(10))
        self.assertEqual(evt["body"], "ordre du jour")
        self.assertEqual(evt["organizer"], "Alice")
        self.assertEqual(evt["required"], ["Bob"])
        self.assertEqual(evt["optional"], ["Dan"])
        self.assertFalse(evt["is_recurring"])

    def test_sensibilite_private_donne_prive(self):
        res, _ = self._lire(items=[_item_ews(sensitivity="Private")])
        self.assertTrue(res[0]["prive"])

    def test_sensibilite_non_privee_donne_non_prive(self):
        for valeur in ("Normal", "Confidential", "Personal", None):
            with self.subTest(valeur=valeur):
                res, _ = self._lire(items=[_item_ews(sensitivity=valeur)])
                self.assertFalse(res[0]["prive"])

    def test_sensibilite_absente_donne_non_prive(self):
        res, _ = self._lire(items=[_item_ews()])
        self.assertFalse(res[0]["prive"])

    def test_get_events_range_garde_ses_valeurs_de_repli(self):
        with patch.object(ews_cal, "_AVAILABLE", False):
            self.assertEqual(ews_cal.get_events_range("a@x.test", "a", "pw"), [])
        with patch.object(ews_cal, "_AVAILABLE", True):
            self.assertEqual(ews_cal.get_events_range("", "", ""), [])

    def test_get_events_range_passe_par_evenements(self):
        with patch.object(ews_cal, "_AVAILABLE", True), \
                patch.object(ews_cal, "evenements", return_value=None) as evt:
            res = ews_cal.get_events_range("a@x.test", "a", "pw", "u", days=3)
        self.assertIsNone(res)
        debut, fin = evt.call_args.args[4:]
        self.assertEqual(fin - debut, datetime.timedelta(days=3))


if __name__ == "__main__":
    unittest.main()
