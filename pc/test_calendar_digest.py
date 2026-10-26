"""
Tests de l'agenda 15 jours (calendar_digest) : chaîne de sources factice, envoi Notion
mocké, date du jour dans un dossier temporaire. Ni Outlook, ni réseau, ni config.py.

Lancement : py -3.12 pc\test_calendar_digest.py
"""
import datetime
import json
import os
import sys
import tempfile
import types
import unittest
from typing import ClassVar
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import calendar_digest

T0 = datetime.datetime(2026, 10, 26, 9, 0)  # noqa: DTZ001 — heure locale naïve voulue


def reunion(objet: str = "Point", **extra) -> dict:
    debut = T0 + datetime.timedelta(hours=2)
    base = {"subject": objet, "start": debut, "end": debut + datetime.timedelta(minutes=30)}
    base.update(extra)
    return base


class TestRunDigest(unittest.TestCase):

    def setUp(self):
        dossier = tempfile.TemporaryDirectory()
        self.addCleanup(dossier.cleanup)
        self.etat = os.path.join(dossier.name, "etat.json")
        patcher = mock.patch.object(calendar_digest, "_STATE_FILE", self.etat)
        patcher.start()
        self.addCleanup(patcher.stop)
        # notion_push exige config.py dès son import : module factice, aucun réseau.
        self.push = mock.Mock(return_value={"url": "https://notion.test/p"})
        faux = types.ModuleType("notion_push")
        faux.push_to_notion = self.push
        modules = mock.patch.dict(sys.modules, {"notion_push": faux})
        modules.start()
        self.addCleanup(modules.stop)

    def digest(self, sources, **kw):
        return calendar_digest.run_digest(days=15, sources=sources, horloge=lambda: T0, **kw)

    def test_bascule_index_ews_com_et_journal(self):
        fenetres = []

        def com(debut, fin):
            fenetres.append((debut, fin))
            return [reunion("Réunion COM")]

        def ews(debut, fin):
            raise RuntimeError("EWS en panne")

        sources = [("index", lambda d, f: None), ("ews", ews), ("com", com)]
        with self.assertLogs("meetnote.agenda", level="INFO") as journal:
            url = self.digest(sources)
        self.assertEqual(url, "https://notion.test/p")
        self.assertEqual(fenetres, [(T0, T0 + datetime.timedelta(days=15))])
        self.assertTrue(any("[Agenda] digest source=com réunions=1 jours=15" in ligne
                            for ligne in journal.output), journal.output)
        transcript = self.push.call_args.kwargs["transcript"]
        self.assertIn("Réunion COM", transcript)
        self.assertEqual(self.push.call_args.kwargs["title_override"],
                         "Agenda prévisionnel — 26/10/2026")
        self.assertEqual(calendar_digest.get_last_run_date(), T0.date())

    def test_toutes_indisponibles_rien_pousse_ni_date_enregistree(self):
        sources = [("index", lambda d, f: None), ("com", lambda d, f: None)]
        with self.assertLogs("meetnote.agenda", level="WARNING") as journal, \
                self.assertRaises(calendar_digest.SourcesIndisponibles) as ctx:
            self.digest(sources)
        self.push.assert_not_called()
        self.assertIsNone(calendar_digest.get_last_run_date())
        self.assertEqual(ctx.exception.prochain_essai, T0 + calendar_digest.DELAI_NOUVEL_ESSAI)
        self.assertTrue(any("source=aucune" in ligne for ligne in journal.output))

    def test_source_sans_reunion_pousse_un_agenda_vide(self):
        url = self.digest([("ews", lambda d, f: [])])
        self.assertEqual(url, "https://notion.test/p")
        self.assertIn("(aucun rendez-vous)", self.push.call_args.kwargs["transcript"])
        self.assertEqual(calendar_digest.get_last_run_date(), T0.date())

    def test_deja_fait_aujourdhui_rien_n_est_appele(self):
        calendar_digest._save_last_run_date(T0.date())
        appelee = mock.Mock(return_value=[])
        self.assertIsNone(self.digest([("x", appelee)]))
        appelee.assert_not_called()
        self.push.assert_not_called()

    def test_force_ignore_le_deja_fait(self):
        calendar_digest._save_last_run_date(T0.date())
        url = self.digest([("ews", lambda d, f: [])], force=True)
        self.assertEqual(url, "https://notion.test/p")

    def test_echec_notion_rend_none(self):
        self.push.side_effect = RuntimeError("réseau")
        with self.assertLogs("calendar_digest", level="ERROR"):
            self.assertIsNone(self.digest([("ews", lambda d, f: [])]))

    def test_chaine_par_defaut_et_aucun_acces_au_cache(self):
        chaine = [("ews", lambda d, f: [])]
        source = calendar_digest.source_agenda
        with mock.patch.object(source, "sources_par_defaut", return_value=chaine) as defaut, \
                mock.patch.object(source, "recharger", side_effect=AssertionError("cache")), \
                mock.patch.object(source, "CacheAgenda", side_effect=AssertionError("cache")):
            self.assertEqual(calendar_digest.run_digest(horloge=lambda: T0),
                             "https://notion.test/p")
        defaut.assert_called_once()

    def test_horloge_injectee_pour_le_controle_du_jour(self):
        """« Aujourd'hui » suit l'horloge injectée, pas la vraie date."""
        calendar_digest._save_last_run_date(T0.date())
        self.assertTrue(calendar_digest.digest_already_run_today(T0.date()))
        lendemain = T0.date() + datetime.timedelta(days=1)
        self.assertFalse(calendar_digest.digest_already_run_today(lendemain))
        self.assertIsNone(self.digest([("x", mock.Mock(return_value=[]))]))
        self.push.assert_not_called()


class TestDelaiNouvelEssai(unittest.TestCase):

    def setUp(self):
        dossier = tempfile.TemporaryDirectory()
        self.addCleanup(dossier.cleanup)
        self.etat = os.path.join(dossier.name, "etat.json")
        patcher = mock.patch.object(calendar_digest, "_STATE_FILE", self.etat)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.push = mock.Mock(return_value={"url": "https://notion.test/p"})
        faux = types.ModuleType("notion_push")
        faux.push_to_notion = self.push
        modules = mock.patch.dict(sys.modules, {"notion_push": faux})
        modules.start()
        self.addCleanup(modules.stop)
        self.t_echec = T0.replace(hour=10, minute=0)

    def echouer(self, **kw):
        with self.assertLogs("meetnote.agenda", level="WARNING"), \
                self.assertRaises(calendar_digest.SourcesIndisponibles):
            calendar_digest.run_digest(sources=[("x", lambda d, f: None)],
                                       horloge=lambda: self.t_echec, **kw)

    def apres(self, minutes: int) -> datetime.datetime:
        return self.t_echec + datetime.timedelta(minutes=minutes)

    def test_delai_apres_echec(self):
        self.echouer()
        self.assertFalse(calendar_digest.digest_a_tenter(self.apres(20)))
        self.assertTrue(calendar_digest.digest_a_tenter(self.apres(31)))
        self.assertTrue(calendar_digest.digest_a_tenter(self.t_echec + calendar_digest.DELAI_NOUVEL_ESSAI))
        self.assertEqual(calendar_digest.prochain_essai(self.apres(20)),
                         self.t_echec + calendar_digest.DELAI_NOUVEL_ESSAI)
        self.assertIsNone(calendar_digest.get_last_run_date())

    def test_pendant_le_delai_aucune_source_lue(self):
        self.echouer()
        lue = mock.Mock(return_value=[])
        self.assertIsNone(calendar_digest.run_digest(sources=[("x", lue)],
                                                     horloge=lambda: self.apres(5)))
        lue.assert_not_called()
        self.push.assert_not_called()

    def test_nouvel_essai_reussi_efface_l_echec(self):
        self.echouer()
        url = calendar_digest.run_digest(sources=[("x", lambda d, f: [])],
                                         horloge=lambda: self.apres(31))
        self.assertEqual(url, "https://notion.test/p")
        self.assertIsNone(calendar_digest.dernier_echec_sources())
        self.assertEqual(calendar_digest.get_last_run_date(), self.apres(31).date())

    def test_force_ignore_le_delai(self):
        self.echouer()
        url = calendar_digest.run_digest(force=True, sources=[("x", lambda d, f: [])],
                                         horloge=lambda: self.apres(5))
        self.assertEqual(url, "https://notion.test/p")
        self.assertIsNone(calendar_digest.dernier_echec_sources())

    def test_echec_force_leve_aussi_la_cause(self):
        self.echouer(force=True)
        self.assertEqual(calendar_digest.dernier_echec_sources(), self.t_echec)

    def test_echec_conserve_la_date_de_succes(self):
        veille = self.t_echec.date() - datetime.timedelta(days=1)
        calendar_digest._save_last_run_date(veille)
        self.echouer()
        self.assertEqual(calendar_digest.get_last_run_date(), veille)

    def test_ancien_fichier_sans_cle_d_echec(self):
        with open(self.etat, "w", encoding="utf-8") as f:
            json.dump({"last_run_date": "2026-10-25"}, f)
        self.assertIsNone(calendar_digest.dernier_echec_sources())
        self.assertIsNone(calendar_digest.prochain_essai(self.t_echec))
        self.assertTrue(calendar_digest.digest_a_tenter(self.t_echec))
        self.assertFalse(calendar_digest.digest_a_tenter(self.t_echec.replace(day=25)))

    def test_echec_notion_distinct_de_sources_indisponibles(self):
        """Un échec de push rend None, sans délai de nouvel essai ; la date est enregistrée."""
        self.push.side_effect = RuntimeError("réseau")
        with self.assertLogs("calendar_digest", level="ERROR"):
            resultat = calendar_digest.run_digest(sources=[("x", lambda d, f: [])],
                                                  horloge=lambda: self.t_echec)
        self.assertIsNone(resultat)
        self.assertIsNone(calendar_digest.dernier_echec_sources())
        self.assertEqual(calendar_digest.get_last_run_date(), self.t_echec.date())


class TestBuildTranscriptTolerant(unittest.TestCase):

    def test_cles_absentes_ou_nulles(self):
        """Une source (l'index) peut ne pas fournir body, location, participants…"""
        texte = calendar_digest.build_transcript(
            [reunion("Minimal"), reunion("Nulles", location=None, organizer=None,
                                         required=None, optional=None, body=None)],
            days=1, generated_at=T0)
        self.assertEqual(texte.count("Lieu        : (aucun)"), 2)
        self.assertIn("Minimal", texte)
        self.assertIn("Nulles", texte)


class TestReunionPrivee(unittest.TestCase):

    COMPLETE: ClassVar[dict] = {"location": "Salle Secrète", "body": "Licenciement de Martin",
                "organizer": "Dr Organisateur", "required": ["Alice Requise"],
                "optional": ["Bob Optionnel"], "is_recurring": True}

    def _texte(self, *reunions):
        return calendar_digest.build_transcript(list(reunions), days=1, generated_at=T0)

    def test_reunion_privee_garde_le_creneau_et_masque_le_reste(self):
        texte = self._texte(reunion("Entretien confidentiel", prive=True, **self.COMPLETE))
        self.assertIn("[11:00 → 11:30]  30min  |  Privé", texte)
        for secret in ("Entretien confidentiel", "Salle Secrète", "Licenciement",
                       "Dr Organisateur", "Alice Requise", "Bob Optionnel"):
            self.assertNotIn(secret, texte)
        for rubrique in ("Lieu", "Organisateur", "Participants", "Récurrent", "Description"):
            self.assertNotIn(rubrique, texte)

    def test_reunion_normale_inchangee(self):
        texte = self._texte(reunion("Comité", prive=False, **self.COMPLETE))
        for attendu in ("|  Comité", "Lieu        : Salle Secrète",
                        "Organisateur: Dr Organisateur",
                        "Participants requis   : Alice Requise", "Récurrent   : oui",
                        "Description : Licenciement de Martin"):
            self.assertIn(attendu, texte)
        self.assertNotIn("Privé", texte)

    def test_cle_absente_vaut_reunion_normale(self):
        texte = self._texte(reunion("Sans clé", **self.COMPLETE))
        self.assertIn("|  Sans clé", texte)
        self.assertNotIn("Privé", texte)

    def test_privee_et_normale_le_meme_jour(self):
        texte = self._texte(reunion("Visible"), reunion("Cachée", prive=True))
        self.assertIn("Visible", texte)
        self.assertNotIn("Cachée", texte)


if __name__ == "__main__":
    unittest.main()
