"""
Tests de journal_app (rotation du journal, faulthandler dans un fichier distinct),
sur un dossier temporaire. Le logging est global : chaque test détache ses handlers.

Lancement : py -3.12 pc\test_journal_app.py
"""
import datetime
import faulthandler
import logging
import os
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import journal_app


class _BaseJournal(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dossier = os.path.join(tmp.name, "MeetNote")
        niveau_meetnote = logging.getLogger("meetnote").level
        self.addCleanup(logging.getLogger("meetnote").setLevel, niveau_meetnote)
        self.journal = journal_app.configurer_journal(self.dossier)
        # Ordre des nettoyages : fermer le journal AVANT de supprimer le dossier.
        self.addCleanup(self._fermer)

    def _fermer(self):
        self.journal.fermer()

    def _lire(self, nom=journal_app.NOM_JOURNAL):
        self.journal.handler.flush()
        with open(os.path.join(self.dossier, nom), encoding="utf-8") as f:
            return f.read()

    def _fichiers_dates(self):
        prefixe = journal_app.NOM_JOURNAL + "."
        return sorted(n for n in os.listdir(self.dossier) if n.startswith(prefixe))


class TestConfiguration(_BaseJournal):
    def test_logger_agenda_ecrit_dans_crash_log(self):
        logging.getLogger("meetnote.agenda").info("[Agenda] rechargement (premier)")
        contenu = self._lire()
        self.assertIn("[INFO] [Agenda] rechargement (premier)", contenu)
        # Format : asctime [niveau] message
        self.assertRegex(contenu, r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} \[INFO\] ")

    def test_niveaux_inchanges(self):
        self.assertEqual(logging.getLogger().level, logging.WARNING)
        self.assertEqual(logging.getLogger("meetnote").level, logging.INFO)
        logging.getLogger("urllib3.test").info("bruit de bibliothèque")
        self.assertNotIn("bruit de bibliothèque", self._lire())

    def test_faulthandler_actif_dans_un_fichier_distinct(self):
        self.assertTrue(faulthandler.is_enabled())
        self.assertNotEqual(self.journal.chemin, self.journal.chemin_natif)
        self.assertTrue(os.path.isfile(self.journal.chemin_natif))

    def test_fermer_detache_le_handler_et_coupe_faulthandler(self):
        self.journal.fermer()
        self.assertNotIn(self.journal.handler, logging.getLogger().handlers)
        self.assertFalse(faulthandler.is_enabled())
        # Un second fermer() (nettoyage du test) reste sans effet.
        self.journal.fermer()


class TestRotation(_BaseJournal):
    def test_rotation_cree_un_fichier_date_et_un_crash_log_neuf(self):
        logging.getLogger("meetnote").info("ligne d'hier")
        self.journal.handler.doRollover()
        logging.getLogger("meetnote").info("ligne du jour")
        self.assertEqual(len(self._fichiers_dates()), 1)
        self.assertNotIn("ligne d'hier", self._lire())
        self.assertIn("ligne du jour", self._lire())
        nom_date = os.path.join(self.dossier, self._fichiers_dates()[0])
        with open(nom_date, encoding="utf-8") as f:
            self.assertIn("ligne d'hier", f.read())

    def test_rotation_reussit_pendant_que_faulthandler_est_actif(self):
        # Piège Windows : un second handle ouvert sur crash.log bloquerait le renommage.
        self.assertTrue(faulthandler.is_enabled())
        avant = journal_app.RotationTolerante.erreurs_rotation
        self.journal.handler.doRollover()
        self.assertEqual(journal_app.RotationTolerante.erreurs_rotation, avant)
        self.assertEqual(len(self._fichiers_dates()), 1)

    def test_les_fichiers_les_plus_anciens_sont_supprimes(self):
        debut = datetime.date(2020, 1, 1)
        anciens = []
        for i in range(journal_app.JOURS_CONSERVATION + 10):
            nom = f"{journal_app.NOM_JOURNAL}.{debut + datetime.timedelta(days=i)}"
            anciens.append(nom)
            with open(os.path.join(self.dossier, nom), "w", encoding="utf-8") as f:
                f.write("x")
        self.journal.handler.doRollover()
        restants = self._fichiers_dates()
        self.assertEqual(len(restants), journal_app.JOURS_CONSERVATION)
        self.assertNotIn(anciens[0], restants)
        self.assertIn(anciens[-1], restants)
        self.assertTrue(os.path.isfile(self.journal.chemin))

    def test_rotation_en_echec_ne_perd_pas_la_ligne_ni_ne_leve(self):
        avant = journal_app.RotationTolerante.erreurs_rotation
        with patch("os.rename", side_effect=PermissionError("verrouillé")):
            self.journal.handler.doRollover()
            logging.getLogger("meetnote").info("ligne après échec")
        self.assertEqual(journal_app.RotationTolerante.erreurs_rotation, avant + 1)
        self.assertIn("ligne après échec", self._lire())
        self.assertEqual(self._fichiers_dates(), [])
        # Le prochain essai n'est pas immédiat.
        self.assertFalse(self.journal.handler.shouldRollover(
            logging.LogRecord("meetnote", logging.INFO, "", 0, "m", None, None)))


# Midi, un jour d'hiver : pas de changement d'heure dans les jours simulés.
_MIDI = time.mktime((2026, 1, 10, 12, 0, 0, 0, 0, -1))
_JOUR = 86400


class TestRotationMinuitEtPurge(_BaseJournal):
    def _ecrire_jour(self, instant, texte):
        """Écrit `texte` dans le journal comme si l'horloge valait `instant`."""
        with patch("time.time", return_value=instant):
            logging.getLogger("meetnote").info(texte)
        self.journal.handler.flush()

    def _poser_vieux_fichiers(self, nombre):
        debut = datetime.date(2020, 1, 1)
        noms = []
        for i in range(nombre):
            nom = f"{journal_app.NOM_JOURNAL}.{debut + datetime.timedelta(days=i)}"
            noms.append(nom)
            with open(os.path.join(self.dossier, nom), "w", encoding="utf-8") as f:
                f.write("x")
        return noms

    def test_echeance_tombe_a_minuit_heure_locale(self):
        handler = self.journal.handler
        self.assertEqual(handler.when, "MIDNIGHT")
        echeance = time.localtime(handler.computeRollover(_MIDI))
        self.assertEqual(echeance[:6], (2026, 1, 11, 0, 0, 0))

    def test_purge_en_echec_ne_fige_pas_la_rotation(self):
        handler = self.journal.handler
        anciens = self._poser_vieux_fichiers(journal_app.JOURS_CONSERVATION)
        handler.rolloverAt = handler.computeRollover(_MIDI)
        vrai_remove = os.remove

        def remove_verrouille(chemin, *args, **kwargs):
            if os.path.basename(chemin) == anciens[0]:
                raise PermissionError("verrouillé par un éditeur")
            return vrai_remove(chemin, *args, **kwargs)

        avant = journal_app.RotationTolerante.erreurs_rotation
        with patch("os.remove", side_effect=remove_verrouille):
            for jour in (1, 2, 3):
                instant = _MIDI + jour * _JOUR
                self._ecrire_jour(instant, f"ligne du jour {jour}")
                contenu = self._lire()
                # Seules les lignes du jour restent dans crash.log, chaque jour.
                self.assertIn(f"ligne du jour {jour}", contenu)
                self.assertNotIn(f"ligne du jour {jour - 1}", contenu)
                self.assertGreater(handler.rolloverAt, instant)
                self.assertEqual(len(self._fichiers_dates()),
                                 journal_app.JOURS_CONSERVATION + jour)
        self.assertEqual(journal_app.RotationTolerante.erreurs_rotation, avant + 3)
        self.assertIn(anciens[0], self._fichiers_dates())

        # Verrou levé : la rotation suivante purge tout l'excédent.
        self._ecrire_jour(_MIDI + 4 * _JOUR, "ligne du jour 4")
        restants = self._fichiers_dates()
        self.assertEqual(len(restants), journal_app.JOURS_CONSERVATION)
        self.assertNotIn(anciens[0], restants)
        self.assertNotIn("ligne du jour 3", self._lire())

    def test_echeance_toujours_dans_le_futur_si_le_fichier_date_existe(self):
        handler = self.journal.handler
        handler.rolloverAt = handler.computeRollover(_MIDI)
        veille = time.localtime(handler.rolloverAt - handler.interval)
        nom = f"{journal_app.NOM_JOURNAL}.{time.strftime(handler.suffix, veille)}"
        with open(os.path.join(self.dossier, nom), "w", encoding="utf-8") as f:
            f.write("déjà là")
        instant = _MIDI + _JOUR
        with patch("time.time", return_value=instant):
            handler.doRollover()
        self.assertGreater(handler.rolloverAt, instant)

    def test_renommage_en_echec_retente_une_heure_plus_tard(self):
        handler = self.journal.handler
        instant = int(_MIDI) + _JOUR
        with patch("os.rename", side_effect=PermissionError("verrouillé")),                 patch("time.time", return_value=instant):
            handler.doRollover()
        self.assertEqual(handler.rolloverAt, instant + journal_app.DELAI_NOUVEL_ESSAI_ROTATION)


class TestJournalNatif(unittest.TestCase):
    def test_journal_natif_trop_gros_est_mis_de_cote(self):
        with tempfile.TemporaryDirectory() as dossier:
            chemin = os.path.join(dossier, journal_app.NOM_JOURNAL_NATIF)
            with open(chemin, "wb") as f:
                f.write(b"x" * (journal_app.TAILLE_MAX_NATIF + 1))
            journal_app._borner_journal_natif(chemin)
            self.assertFalse(os.path.exists(chemin))
            self.assertTrue(os.path.isfile(chemin + ".old"))

    def test_journal_natif_petit_ou_absent_est_laisse(self):
        with tempfile.TemporaryDirectory() as dossier:
            chemin = os.path.join(dossier, journal_app.NOM_JOURNAL_NATIF)
            journal_app._borner_journal_natif(chemin)  # absent : sans effet
            with open(chemin, "wb") as f:
                f.write(b"x")
            journal_app._borner_journal_natif(chemin)
            self.assertTrue(os.path.isfile(chemin))
            self.assertFalse(os.path.exists(chemin + ".old"))


if __name__ == "__main__":
    unittest.main()
