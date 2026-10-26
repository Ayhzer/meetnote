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

    def test_est_meetnote_mode_fige(self):
        self.assertTrue(watchdog.est_meetnote("MeetNote.exe", []))
        self.assertTrue(watchdog.est_meetnote("meetnote-1.2.exe"))

    def test_est_meetnote_mode_source(self):
        cmd = [r"C:\Python312\pythonw.exe", r"C:\DEV\meetnote\pc\MeetNote-Tray.py",
               "--minimized"]
        self.assertTrue(watchdog.est_meetnote("pythonw.exe", cmd))
        self.assertTrue(watchdog.est_meetnote("python.exe", ["python", "meetnote-tray.py"]))

    def test_est_meetnote_refuse_les_autres_python(self):
        self.assertFalse(watchdog.est_meetnote("python.exe", ["python", "boucle-dev.py"]))
        self.assertFalse(watchdog.est_meetnote("pythonw.exe", ["pythonw", "watchdog.py"]))
        self.assertFalse(watchdog.est_meetnote("python.exe", ["python", "x/meetnote-tray.py.bak"]))
        self.assertFalse(watchdog.est_meetnote("python.exe", []))
        self.assertFalse(watchdog.est_meetnote("python.exe", None))

    def test_est_meetnote_refuse_les_autres_programmes(self):
        self.assertFalse(watchdog.est_meetnote("notepad.exe", ["meetnote-tray.py"]))
        self.assertFalse(watchdog.est_meetnote("", ["meetnote-tray.py"]))
        self.assertFalse(watchdog.est_meetnote(None, None))

    def test_watchdog_pid_en_mode_source_n_est_pas_tue(self):
        # `est_processus_meetnote` (watchdog.pid) ignore toujours python.
        self.assertFalse(watchdog.est_processus_meetnote("pythonw.exe"))


class AfficherInstanceExistanteTest(unittest.TestCase):
    """Second lancement : l'instance en cours n'est jamais tuée, on lui demande l'affichage."""

    def setUp(self):
        self.dossier = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dossier, ignore_errors=True)
        self.flag = os.path.join(self.dossier, "afficher.flag")
        self.stop = os.path.join(self.dossier, "clean_exit.flag")
        self.pid = os.path.join(self.dossier, "watchdog.pid")

    def _ecrire_pid(self, contenu):
        with open(self.pid, "w") as f:
            f.write(contenu)

    def _afficher(self, nom_processus, pid_courant=1):
        return watchdog.afficher_instance_existante(
            pid_file=self.pid, afficher_flag=self.flag, pid_courant=pid_courant,
            nom_processus=nom_processus)

    def test_instance_vivante_recoit_la_demande_sans_etre_arretee(self):
        self._ecrire_pid("4242")
        interroges = []

        def nom(pid):
            interroges.append(pid)
            return "MeetNote.exe"

        self.assertTrue(self._afficher(nom))
        self.assertEqual(interroges, [4242])
        self.assertTrue(os.path.isfile(self.flag))
        self.assertFalse(os.path.exists(self.stop))  # aucun arrêt demandé

    def test_process_qui_n_est_pas_meetnote_n_est_pas_une_instance(self):
        self._ecrire_pid("4242")
        self.assertFalse(self._afficher(lambda pid: "pythonw.exe"))
        self.assertFalse(os.path.exists(self.flag))

    def test_pid_perime_process_disparu(self):
        self._ecrire_pid("4242")
        self.assertFalse(self._afficher(lambda pid: ""))
        self.assertFalse(os.path.exists(self.flag))

    def test_aucun_fichier_pid(self):
        self.assertFalse(self._afficher(lambda pid: self.fail("ne doit pas interroger")))
        self.assertFalse(os.path.exists(self.flag))

    def test_pid_illisible(self):
        self._ecrire_pid("pas-un-nombre")
        self.assertFalse(self._afficher(lambda pid: self.fail("ne doit pas interroger")))

    def test_ne_se_reconnait_jamais_soi_meme(self):
        self._ecrire_pid("7")
        self.assertFalse(self._afficher(
            lambda pid: self.fail("ne doit pas interroger"), pid_courant=7))

    def test_psutil_manquant_revient_a_superviser(self):
        self._ecrire_pid("4242")
        import builtins
        reel = builtins.__import__

        def sans_psutil(nom, *args, **kwargs):
            if nom == "psutil":
                raise ImportError(nom)
            return reel(nom, *args, **kwargs)

        builtins.__import__ = sans_psutil
        try:
            self.assertFalse(watchdog.afficher_instance_existante(
                pid_file=self.pid, afficher_flag=self.flag, pid_courant=1))
        finally:
            builtins.__import__ = reel
        self.assertFalse(os.path.exists(self.flag))


class DemandeAffichageTest(unittest.TestCase):
    def setUp(self):
        self.dossier = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dossier, ignore_errors=True)
        self.flag = os.path.join(self.dossier, "sous", "afficher.flag")

    def test_consommation_idempotente(self):
        self.assertFalse(watchdog.consommer_demande_affichage(self.flag))  # rien déposé
        self.assertTrue(watchdog.demander_affichage(self.flag))  # crée le dossier
        self.assertTrue(watchdog.consommer_demande_affichage(self.flag))
        self.assertFalse(watchdog.consommer_demande_affichage(self.flag))

    def test_deux_demandes_valent_une_consommation(self):
        watchdog.demander_affichage(self.flag)
        watchdog.demander_affichage(self.flag)
        self.assertTrue(watchdog.consommer_demande_affichage(self.flag))
        self.assertFalse(watchdog.consommer_demande_affichage(self.flag))


class SuperviserExeTest(unittest.TestCase):
    def setUp(self):
        self.dossier = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dossier, ignore_errors=True)
        self.flag = os.path.join(self.dossier, "afficher.flag")
        self.appels = []

    def _lancer(self, existante):
        watchdog.superviser_exe(
            ["--minimized"], executable="M.exe", existante=existante,
            superviseur=self.appels.append, afficher_flag=self.flag)

    def test_instance_existante_pas_de_supervision(self):
        self._lancer(lambda: True)
        self.assertEqual(self.appels, [])  # ni enfant, ni watchdog.pid réécrit

    def test_aucune_instance_supervise_l_enfant(self):
        self._lancer(lambda: False)
        self.assertEqual(self.appels, [["M.exe", "--enfant", "--minimized"]])

    def test_demande_perimee_effacee_avant_de_superviser(self):
        watchdog.demander_affichage(self.flag)
        self._lancer(lambda: False)
        self.assertFalse(os.path.exists(self.flag))

    def test_demande_conservee_quand_une_instance_existe(self):
        watchdog.demander_affichage(self.flag)
        self._lancer(lambda: True)
        self.assertTrue(os.path.isfile(self.flag))  # l'instance la consommera


if __name__ == "__main__":
    unittest.main()
