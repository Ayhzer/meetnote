"""Tests de la boucle de supervision de watchdog.py (Popen factice, aucun vrai process)."""
import os
import shutil
import tempfile
import unittest

import watchdog


class FauxPopen:
    """Process factice : `wait()` rend aussitôt la main."""

    def __init__(self, commande, sur_wait=None):
        self.commande = commande
        self._sur_wait = sur_wait

    def wait(self):
        if self._sur_wait:
            self._sur_wait()
        return 0


class SuperviserTest(unittest.TestCase):
    def setUp(self):
        self.dossier = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dossier, ignore_errors=True)
        self.flag = os.path.join(self.dossier, "clean_exit.flag")
        self.pid = os.path.join(self.dossier, "sous", "watchdog.pid")
        self.lancements = []
        self.attentes = []

    def _poser_flag(self):
        open(self.flag, "w").close()

    def _superviser(self, lanceur, commande=("app", "--x")):
        watchdog.superviser(
            commande, stop_flag=self.flag, pid_file=self.pid,
            lanceur=lanceur, attendre=self.attentes.append, delai=7,
        )

    def _lanceur_flag_au(self, n):
        """Lanceur factice : le n-ième process dépose le drapeau de sortie propre."""
        def lanceur(commande):
            self.lancements.append(commande)
            sur_wait = self._poser_flag if len(self.lancements) == n else None
            return FauxPopen(commande, sur_wait)
        return lanceur

    def test_plantage_puis_relance_puis_arret(self):
        self._superviser(self._lanceur_flag_au(3))
        self.assertEqual(len(self.lancements), 3)
        self.assertEqual(self.attentes, [7, 7])  # une attente par plantage
        self.assertFalse(os.path.exists(self.flag))  # drapeau consommé

    def test_commande_transmise_telle_quelle(self):
        self._superviser(self._lanceur_flag_au(1), commande=("MeetNote.exe", "--enfant"))
        self.assertEqual(self.lancements, [["MeetNote.exe", "--enfant"]])
        self.assertEqual(self.attentes, [])

    def test_drapeau_perime_ignore(self):
        self._poser_flag()  # reste d'un run précédent
        self._superviser(self._lanceur_flag_au(2))
        self.assertEqual(len(self.lancements), 2)  # non consommé, il aurait arrêté dès le 1er

    def test_pid_pose_puis_retire(self):
        vus = []

        def lanceur(commande):
            with open(self.pid) as f:
                vus.append(f.read())
            return FauxPopen(commande, self._poser_flag)

        self._superviser(lanceur)
        self.assertEqual(vus, [str(os.getpid())])
        self.assertFalse(os.path.exists(self.pid))

    def test_pid_retire_si_exception(self):
        def lanceur(commande):
            raise OSError("lancement impossible")

        with self.assertRaises(OSError):
            self._superviser(lanceur)
        self.assertFalse(os.path.exists(self.pid))


class DecisionTest(unittest.TestCase):
    def test_fige_sans_enfant_supervise(self):
        self.assertTrue(watchdog.doit_superviser(True, []))
        self.assertTrue(watchdog.doit_superviser(True, ["--minimized"]))

    def test_fige_avec_enfant_travaille(self):
        self.assertFalse(watchdog.doit_superviser(True, ["--enfant"]))
        self.assertFalse(watchdog.doit_superviser(True, ["--enfant", "--minimized"]))

    def test_mode_source_travaille_toujours(self):
        self.assertFalse(watchdog.doit_superviser(False, []))
        self.assertFalse(watchdog.doit_superviser(False, ["--minimized"]))

    def test_commande_enfant_garde_les_arguments(self):
        self.assertEqual(
            watchdog.commande_enfant("C:/x/MeetNote.exe", ["--minimized"]),
            ["C:/x/MeetNote.exe", "--enfant", "--minimized"])
        self.assertEqual(watchdog.commande_enfant("M.exe", []), ["M.exe", "--enfant"])

    def test_reconnaissance_du_process_meetnote(self):
        self.assertTrue(watchdog.est_processus_meetnote("MeetNote.exe"))
        self.assertFalse(watchdog.est_processus_meetnote("pythonw.exe"))
        self.assertFalse(watchdog.est_processus_meetnote(""))


class FauxProcessus:
    """Process factice qui journalise l'ordre des arrêts dans `journal`."""

    def __init__(self, nom, journal, descendants=()):
        self._nom = nom
        self._journal = journal
        self._descendants = list(descendants)

    def nom(self):
        return self._nom

    def descendants(self):
        return self._descendants

    def terminer(self):
        self._journal.append(("terminer", self._nom))

    def attendre_fin(self, timeout):
        self._journal.append(("attendre", self._nom, timeout))


class RemplacerInstanceTest(unittest.TestCase):
    def setUp(self):
        self.dossier = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dossier, ignore_errors=True)
        self.flag = os.path.join(self.dossier, "clean_exit.flag")
        self.pid = os.path.join(self.dossier, "watchdog.pid")
        self.journal = []

    def _ecrire_pid(self, contenu):
        with open(self.pid, "w") as f:
            f.write(contenu)

    def _remplacer(self, ouvrir, pid_courant=1):
        return watchdog.remplacer_instance(
            pid_file=self.pid, stop_flag=self.flag, pid_courant=pid_courant,
            ouvrir=ouvrir, timeout=2)

    def test_arrete_superviseur_et_enfant_apres_depot_du_drapeau(self):
        self._ecrire_pid("4242")
        enfant = FauxProcessus("MeetNote.exe", self.journal)
        superviseur = FauxProcessus("MeetNote.exe", self.journal, [enfant])
        ouverts = []

        def ouvrir(pid):
            ouverts.append(pid)
            return superviseur

        # Le drapeau doit exister avant le premier arrêt.
        superviseur.terminer = lambda: self.journal.append(
            ("terminer-superviseur", os.path.isfile(self.flag)))
        self.assertTrue(self._remplacer(ouvrir))
        self.assertEqual(ouverts, [4242])
        self.assertTrue(os.path.isfile(self.flag))
        self.assertEqual(self.journal[0], ("terminer-superviseur", True))
        self.assertIn(("terminer", "MeetNote.exe"), self.journal[1:2])  # l'enfant
        self.assertEqual(len([e for e in self.journal if e[0] == "attendre"]), 2)

    def test_process_qui_n_est_pas_meetnote_est_laisse_intact(self):
        self._ecrire_pid("4242")
        autre = FauxProcessus("pythonw.exe", self.journal)
        self.assertFalse(self._remplacer(lambda pid: autre))
        self.assertEqual(self.journal, [])
        self.assertFalse(os.path.exists(self.flag))  # aucun drapeau déposé

    def test_aucun_fichier_pid_rien_a_remplacer(self):
        self.assertFalse(self._remplacer(lambda pid: self.fail("ne doit pas ouvrir")))
        self.assertFalse(os.path.exists(self.flag))

    def test_pid_illisible_ou_process_disparu(self):
        self._ecrire_pid("pas-un-nombre")
        self.assertFalse(self._remplacer(lambda pid: self.fail("ne doit pas ouvrir")))
        self._ecrire_pid("4242")
        self.assertFalse(self._remplacer(lambda pid: None))
        self.assertFalse(os.path.exists(self.flag))

    def test_ne_s_arrete_jamais_soi_meme(self):
        self._ecrire_pid("7")
        self.assertFalse(self._remplacer(
            lambda pid: self.fail("ne doit pas ouvrir"), pid_courant=7))

    def test_superviser_exe_remplace_puis_supervise_l_enfant(self):
        appels = []
        watchdog.superviser_exe(
            ["--minimized"], executable="M.exe",
            remplacer=lambda: appels.append("remplacer"),
            superviseur=lambda commande: appels.append(commande))
        self.assertEqual(appels, ["remplacer", ["M.exe", "--enfant", "--minimized"]])

    def test_import_psutil_manquant_n_arrete_rien(self):
        self._ecrire_pid("4242")
        import builtins
        reel = builtins.__import__

        def sans_psutil(nom, *args, **kwargs):
            if nom == "psutil":
                raise ImportError(nom)
            return reel(nom, *args, **kwargs)

        builtins.__import__ = sans_psutil
        try:
            self.assertFalse(watchdog.remplacer_instance(
                pid_file=self.pid, stop_flag=self.flag, pid_courant=1))
        finally:
            builtins.__import__ = reel
        self.assertFalse(os.path.exists(self.flag))


if __name__ == "__main__":
    unittest.main()
