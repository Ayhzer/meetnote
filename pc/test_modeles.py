"""Tests de modeles.py — dossiers temporaires uniquement, jamais le vrai profil."""
import base64
import errno
import hashlib
import os
import pathlib
import shutil
import sys
import tempfile
import threading
import time
import types
import unittest
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import modeles


def creer_modele(dossier, vocabulaire="vocabulary.txt", vides=()):
    """Cree un modele factice ; les fichiers de `vides` sont crees a zero octet."""
    os.makedirs(dossier, exist_ok=True)
    fichiers = list(modeles.FICHIERS_REQUIS) + ([vocabulaire] if vocabulaire else [])
    for nom in fichiers:
        with open(os.path.join(dossier, nom), "wb") as f:
            if nom not in vides:
                f.write(b"x")
    return dossier


class TestDescription(unittest.TestCase):
    def test_modeles_proposes_comme_l_interface(self):
        self.assertEqual(
            modeles.MODELES, ("tiny", "base", "small", "medium", "large-v3"))

    def test_depot(self):
        self.assertEqual(modeles.depot("small"), "Systran/faster-whisper-small")
        self.assertEqual(modeles.depot("large-v3"), "Systran/faster-whisper-large-v3")

    def test_seuil_ram_huit_go(self):
        self.assertEqual(modeles.SEUIL_RAM_GIO, 8.0)


class TestChoixSelonRam(unittest.TestCase):
    def test_small_sous_le_seuil(self):
        self.assertEqual(modeles.modele_par_defaut(4), "small")
        self.assertEqual(modeles.modele_par_defaut(7.9), "small")

    def test_medium_au_seuil_et_au_dela(self):
        self.assertEqual(modeles.modele_par_defaut(8), "medium")
        self.assertEqual(modeles.modele_par_defaut(32), "medium")

    def test_ram_totale_lue_via_psutil(self):
        faux = mock.Mock()
        faux.virtual_memory.return_value.total = 16 * 2 ** 30
        with mock.patch.dict(sys.modules, {"psutil": faux}):
            self.assertEqual(modeles.ram_totale_gio(), 16.0)

    def test_ram_totale_prudente_si_psutil_absent(self):
        with mock.patch.dict(sys.modules, {"psutil": None}):
            self.assertEqual(modeles.ram_totale_gio(), modeles.RAM_PRUDENTE_GIO)

    def test_ram_totale_prudente_si_mesure_en_echec(self):
        faux = mock.Mock()
        faux.virtual_memory.side_effect = OSError("indisponible")
        with mock.patch.dict(sys.modules, {"psutil": faux}):
            self.assertEqual(modeles.ram_totale_gio(), modeles.RAM_PRUDENTE_GIO)

    def test_valeur_prudente_conduit_a_small(self):
        self.assertEqual(modeles.modele_par_defaut(modeles.RAM_PRUDENTE_GIO), "small")


class TestDossiers(unittest.TestCase):
    def test_defaut_sous_localappdata(self):
        chemin = modeles.dossier_modele("small", surcharge="", localappdata="L")
        self.assertEqual(chemin, os.path.join("L", "MeetNote", "modeles", "small"))

    def test_variable_d_environnement_localappdata(self):
        with mock.patch.dict(os.environ, {"LOCALAPPDATA": "E"}):
            racine = modeles.dossier_racine_modeles(surcharge="")
        self.assertEqual(racine, os.path.join("E", "MeetNote", "modeles"))

    def test_repli_sans_localappdata(self):
        with mock.patch.dict(os.environ, clear=False):
            os.environ.pop("LOCALAPPDATA", None)
            racine = modeles.dossier_racine_modeles(surcharge="")
        self.assertTrue(racine.startswith(os.path.expanduser("~")))
        self.assertTrue(racine.endswith(os.path.join("MeetNote", "modeles")))

    def test_surcharge_par_les_reglages(self):
        chemin = modeles.dossier_modele("medium", surcharge="D:\\mes-modeles",
                                        localappdata="L")
        self.assertEqual(chemin, os.path.join("D:\\mes-modeles", "medium"))

    def test_surcharge_lue_dans_user_config(self):
        import user_config
        with mock.patch.object(user_config, "get", return_value="Z") as lire:
            racine = modeles.dossier_racine_modeles()
        lire.assert_called_once_with(modeles.CLE_DOSSIER_MODELES, "")
        self.assertEqual(racine, "Z")

    def test_cle_de_surcharge_dans_les_defauts(self):
        import user_config
        self.assertEqual(user_config._DEFAULTS[modeles.CLE_DOSSIER_MODELES], "")


class TestModeleComplet(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dossier = os.path.join(self._tmp.name, "m")

    def test_complet_avec_vocabulaire_txt(self):
        creer_modele(self.dossier, "vocabulary.txt")
        self.assertTrue(modeles.modele_complet(self.dossier))

    def test_complet_avec_vocabulaire_json(self):
        creer_modele(self.dossier, "vocabulary.json")
        self.assertTrue(modeles.modele_complet(self.dossier))

    def test_sans_vocabulaire(self):
        creer_modele(self.dossier, None)
        self.assertFalse(modeles.modele_complet(self.dossier))

    def test_fichier_manquant(self):
        creer_modele(self.dossier)
        os.remove(os.path.join(self.dossier, "tokenizer.json"))
        self.assertFalse(modeles.modele_complet(self.dossier))

    def test_fichier_vide(self):
        creer_modele(self.dossier, vides=("model.bin",))
        self.assertFalse(modeles.modele_complet(self.dossier))

    def test_vocabulaire_vide(self):
        creer_modele(self.dossier, "vocabulary.txt", vides=("vocabulary.txt",))
        self.assertFalse(modeles.modele_complet(self.dossier))

    def test_dossier_absent(self):
        self.assertFalse(modeles.modele_complet(os.path.join(self.dossier, "absent")))

    def test_liens_symboliques_suivis(self):
        blobs = os.path.join(self._tmp.name, "blobs")
        creer_modele(blobs)
        os.makedirs(self.dossier)
        try:
            for nom in os.listdir(blobs):
                os.symlink(os.path.join(blobs, nom), os.path.join(self.dossier, nom))
        except (OSError, NotImplementedError):
            self.skipTest("liens symboliques non autorises sur ce poste")
        self.assertTrue(modeles.modele_complet(self.dossier))

    def test_lien_casse(self):
        os.makedirs(self.dossier)
        try:
            os.symlink(os.path.join(self._tmp.name, "nulle-part"),
                       os.path.join(self.dossier, "model.bin"))
        except (OSError, NotImplementedError):
            self.skipTest("liens symboliques non autorises sur ce poste")
        self.assertFalse(modeles.modele_complet(self.dossier))


class TestCheminModele(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        racine = self._tmp.name
        self.local = os.path.join(racine, "local")
        self.exe = os.path.join(racine, "exe")
        self.bundle = os.path.join(racine, "bundle")
        self.cache = os.path.join(racine, "hf")
        self.nom = "small"

    def chercher(self, **extra):
        return modeles.chemin_modele(
            self.nom, surcharge="", localappdata=self.local, dossier_exe=self.exe,
            dossier_bundle=self.bundle, cache_hf=self.cache, **extra)

    def emplacements(self):
        return {
            "meetnote": os.path.join(self.local, "MeetNote", "modeles", self.nom),
            "exe": os.path.join(self.exe, "models", self.nom),
            "bundle": os.path.join(self.bundle, "faster_whisper_models", self.nom),
            "cache": os.path.join(
                self.cache, "models--Systran--faster-whisper-small",
                "snapshots", "abc"),
        }

    def test_rien_ne_est_trouve(self):
        self.assertIsNone(self.chercher())

    def test_chaque_emplacement_est_trouve(self):
        for cle, dossier in self.emplacements().items():
            with self.subTest(emplacement=cle):
                creer_modele(dossier)
                self.assertEqual(self.chercher(), dossier)
                for fichier in os.listdir(dossier):
                    os.remove(os.path.join(dossier, fichier))

    def test_ordre_de_recherche(self):
        lieux = self.emplacements()
        for cle in ("cache", "bundle", "exe", "meetnote"):
            creer_modele(lieux[cle])
            self.assertEqual(self.chercher(), lieux[cle])

    def test_dossier_incomplet_ignore_au_profit_du_suivant(self):
        lieux = self.emplacements()
        creer_modele(lieux["meetnote"], vides=("model.bin",))
        creer_modele(lieux["exe"])
        self.assertEqual(self.chercher(), lieux["exe"])

    def test_tous_incomplets(self):
        for dossier in self.emplacements().values():
            creer_modele(dossier, vocabulaire=None)
        self.assertIsNone(self.chercher())

    def test_snapshot_complet_prefere_a_un_snapshot_incomplet(self):
        snapshots = os.path.join(
            self.cache, "models--Systran--faster-whisper-small", "snapshots")
        complet = creer_modele(os.path.join(snapshots, "ancien"))
        incomplet = creer_modele(os.path.join(snapshots, "recent"), vocabulaire=None)
        os.utime(complet, (time.time() - 100, time.time() - 100))
        os.utime(incomplet, None)
        self.assertEqual(self.chercher(), complet)

    def test_autre_modele_non_confondu(self):
        creer_modele(os.path.join(self.local, "MeetNote", "modeles", "medium"))
        self.assertIsNone(self.chercher())

    def test_surcharge_prioritaire(self):
        perso = os.path.join(self._tmp.name, "perso")
        creer_modele(os.path.join(perso, self.nom))
        creer_modele(self.emplacements()["meetnote"])
        trouve = modeles.chemin_modele(
            self.nom, surcharge=perso, localappdata=self.local, dossier_exe=self.exe,
            dossier_bundle=self.bundle, cache_hf=self.cache)
        self.assertEqual(trouve, os.path.join(perso, self.nom))

    def test_hors_executable_fige_emplacements_historiques_ignores(self):
        creer_modele(self.emplacements()["exe"])
        with mock.patch.object(sys, "frozen", False, create=True):
            trouve = modeles.chemin_modele(
                self.nom, surcharge="", localappdata=self.local, cache_hf=self.cache)
        self.assertIsNone(trouve)

    def test_executable_fige_utilise_exe_et_bundle(self):
        creer_modele(self.emplacements()["bundle"])
        with mock.patch.object(sys, "frozen", True, create=True), \
                mock.patch.object(sys, "_MEIPASS", self.bundle, create=True), \
                mock.patch.object(sys, "executable",
                                  os.path.join(self.exe, "MeetNote.exe")):
            trouve = modeles.chemin_modele(
                self.nom, surcharge="", localappdata=self.local, cache_hf=self.cache)
        self.assertEqual(trouve, self.emplacements()["bundle"])


class TestCacheHf(unittest.TestCase):
    def test_hub_cache_prioritaire(self):
        with mock.patch.dict(os.environ, {"HF_HUB_CACHE": "A", "HF_HOME": "B"}):
            self.assertEqual(modeles.dossier_cache_hf(), "A")

    def test_hf_home(self):
        with mock.patch.dict(os.environ, {"HF_HOME": "B"}):
            os.environ.pop("HF_HUB_CACHE", None)
            self.assertEqual(modeles.dossier_cache_hf(), os.path.join("B", "hub"))

    def test_defaut_sous_le_profil(self):
        with mock.patch.dict(os.environ):
            os.environ.pop("HF_HUB_CACHE", None)
            os.environ.pop("HF_HOME", None)
            attendu = os.path.join(
                os.path.expanduser("~"), ".cache", "huggingface", "hub")
            self.assertEqual(modeles.dossier_cache_hf(), attendu)


# --- Telechargement : huggingface_hub entierement simule, aucun reseau ---------------

TAILLE_BLOC = 100
FICHIERS_FAUX = {
    "model.bin": b"m" * 1000,
    "config.json": b"c" * 10,
    "tokenizer.json": b"t" * 20,
    "vocabulary.txt": b"v" * 30,
}
TAILLE_FAUSSE = sum(len(c) for c in FICHIERS_FAUX.values())


# Memes noms de classes que ceux de huggingface_hub / httpx (le code classe par nom).
class TransportError(Exception):
    pass


class ConnectError(TransportError):
    pass


class RepositoryNotFoundError(OSError):
    pass


class LocalEntryNotFoundError(FileNotFoundError):
    pass


class HfHubHTTPError(OSError):
    pass


def chemin_incomplet(dossier, fichier, etag="etag"):
    """Chemin du `.incomplete` de huggingface_hub 1.9.0 (copie independante du code)."""
    empreinte = base64.urlsafe_b64encode(
        hashlib.sha1(f"{fichier}.metadata".encode(),
                     usedforsecurity=False).digest()).decode()
    return os.path.join(dossier, ".cache", "huggingface", "download",
                        f"{empreinte}.{etag}.incomplete")


class FauxHub:
    """Imite `snapshot_download` de huggingface_hub 1.9.0.

    Une barre d'octets partagee (unite « B ») que chaque fichier alimente depuis son
    thread via un objet `_Agregee` (total += taille, update(initial), update(bloc)),
    la barre « fichiers » de `tqdm.contrib.concurrent.thread_map`, et la reprise par un
    fichier `.incomplete` (nom reel de hf) rouvert en ajout. `reprise_interne` imite
    `http_get` : apres une coupure breve il relance dans le meme appel, avec une
    nouvelle barre dont `initial` vaut les octets deja recus. `apres_bloc` est appele
    apres chaque bloc ecrit (synchronisation des tests avec le thread de suivi).
    """

    def __init__(self, coupure=None, erreur=None, avale_les_erreurs=False,
                 metadonnees="ok", reprise_interne=False, apres_bloc=None,
                 journal=None, etiquette=""):
        self.reprise_interne = reprise_interne
        self.apres_bloc = apres_bloc
        self.journal = journal          # liste partagee : ("debut"/"fin", etiquette)
        self.etiquette = etiquette
        self.coupure = coupure          # (fichier, octets) : coupure reseau, une fois
        self.erreur = erreur            # levee d'emblee par snapshot_download
        self.avale_les_erreurs = avale_les_erreurs
        # "ok" : model_info donne les tailles ; "echec" : leve ;
        # "sans_taille" : size=None
        self.metadonnees = metadonnees
        self.constants = SimpleNamespace(HF_HUB_DISABLE_XET=False)
        self.xet_desactive = []         # valeur de la constante a chaque appel
        self.appels = []
        self.lectures_metadonnees = []
        self.reprises = {}
        self.par_fichier = {}
        self._verrou = threading.Lock()

    def HfApi(self):  # meme nom que huggingface_hub
        hub = self

        def model_info(repo_id, **kwargs):
            hub.lectures_metadonnees.append((repo_id, kwargs))
            if hub.metadonnees == "echec":
                raise ConnectError("metadonnees injoignables")
            siblings = [SimpleNamespace(rfilename=nom, size=len(contenu))
                        for nom, contenu in FICHIERS_FAUX.items()]
            siblings.append(SimpleNamespace(rfilename="README.md", size=5000))
            if hub.metadonnees == "sans_taille":
                siblings[0].size = None
            return SimpleNamespace(siblings=siblings)

        return SimpleNamespace(model_info=model_info)

    def snapshot_download(self, **kwargs):
        if self.journal is not None:
            self.journal.append(("debut", self.etiquette))
        try:
            return self._snapshot_download(**kwargs)
        finally:
            if self.journal is not None:
                self.journal.append(("fin", self.etiquette))

    def _snapshot_download(self, **kwargs):
        from tqdm.contrib.concurrent import thread_map
        self.appels.append(kwargs)
        self.xet_desactive.append(self.constants.HF_HUB_DISABLE_XET)
        if self.erreur:
            raise self.erreur
        classe, dossier = kwargs["tqdm_class"], kwargs["local_dir"]
        octets = classe(desc="Downloading (incomplete total...)", disable=None,
                        total=0, initial=0, unit="B", unit_scale=True,
                        name="huggingface_hub.snapshot_download")

        class _Agregee:
            def __init__(self, *args, **kw):
                total = kw.pop("total", None)
                if total is not None:
                    octets.total += total
                initial = kw.pop("initial", 0)
                if initial:
                    octets.update(initial)

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def update(self, n=1):
                octets.update(n)

        def un_fichier(nom):
            try:
                self._un_fichier(nom, dossier, _Agregee)
            except Exception:
                if not self.avale_les_erreurs:
                    raise

        presents = [f for f in FICHIERS_FAUX if f in kwargs["allow_patterns"]]
        thread_map(un_fichier, presents, desc="Fetching", max_workers=2,
                   tqdm_class=classe)
        return dossier

    def _un_fichier(self, nom, dossier, barre):
        contenu = FICHIERS_FAUX[nom]
        final = os.path.join(dossier, nom)
        if os.path.isfile(final):
            return
        incomplet = chemin_incomplet(dossier, nom)
        os.makedirs(os.path.dirname(incomplet), exist_ok=True)
        while True:
            try:
                self._ecrire(nom, contenu, incomplet, barre)
                break
            except ConnectError:
                if not self.reprise_interne:
                    raise
        os.replace(incomplet, final)

    def _ecrire(self, nom, contenu, incomplet, barre):
        position = os.path.getsize(incomplet) if os.path.exists(incomplet) else 0
        if position:
            self.reprises[nom] = position
        with barre(total=len(contenu), initial=position, unit="B") as suivi, \
                open(incomplet, "ab") as f:
            while position < len(contenu):
                if self.coupure and self.coupure[0] == nom \
                        and position >= self.coupure[1]:
                    self.coupure = None
                    raise ConnectError("reseau coupe")
                bloc = contenu[position:position + TAILLE_BLOC]
                f.write(bloc)
                f.flush()
                position += len(bloc)
                with self._verrou:
                    self.par_fichier[nom] = self.par_fichier.get(nom, 0) + len(bloc)
                suivi.update(len(bloc))
                if self.apres_bloc is not None:
                    self.apres_bloc()


INTERVALLE_TEST_S = 0.001


class MesureSynchrone:
    """Mesure disque reelle, observable : `attendre` bloque jusqu'a la mesure suivante.

    Le faux hub l'appelle apres chaque bloc ecrit : chaque etat du disque est ainsi vu
    par le thread de suivi, sans `sleep` et sans test instable.
    """

    def __init__(self):
        self._condition = threading.Condition()
        self._nombre = 0

    def __call__(self, dossier):
        octets = modeles.mesurer_octets_presents(dossier)
        with self._condition:
            self._nombre += 1
            self._condition.notify_all()
        return octets

    def attendre(self):
        with self._condition:
            depart = self._nombre
            self._condition.wait_for(lambda: self._nombre > depart, timeout=5)


class TestTelechargement(unittest.TestCase):
    def setUp(self):
        try:
            import tqdm.contrib.concurrent  # noqa: F401
        except ImportError:
            self.skipTest("tqdm absent : le faux huggingface_hub s'appuie dessus")
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.local = self._tmp.name
        self.nom = "small"
        self.dossier = modeles.dossier_modele(self.nom, surcharge="",
                                              localappdata=self.local)
        self.rappels = []
        self.mesure = MesureSynchrone()

    def noter(self, recus, total):
        self.rappels.append((recus, total))

    def hub(self, **options):
        """Faux hub dont chaque bloc attend une mesure du thread de suivi."""
        return FauxHub(apres_bloc=self.mesure.attendre, **options)

    def threads_suivi(self):
        return [t for t in threading.enumerate() if t.name == "suivi-modele"]

    def telecharger(self, hf, annuler=None, rappel="defaut"):
        if rappel == "defaut":
            rappel = self.noter
        return modeles.telecharger(self.nom, rappel, annuler, surcharge="",
                                   localappdata=self.local, hf=hf,
                                   mesure=self.mesure, intervalle_s=INTERVALLE_TEST_S)

    def test_progression_monotone_jusqu_a_cent_pour_cent(self):
        reentrance = []
        verrou = threading.Lock()

        def rappel(recus, total):
            if not verrou.acquire(blocking=False):
                reentrance.append((recus, total))
                return
            try:
                self.rappels.append((recus, total))
            finally:
                verrou.release()

        hub = self.hub()
        resultat = self.telecharger(hub, rappel=rappel)
        self.assertEqual(resultat, self.dossier)
        self.assertTrue(modeles.modele_complet(self.dossier))
        self.assertEqual(reentrance, [], "rappel appele en meme temps depuis 2 threads")
        self.assertGreater(len(self.rappels), 4)
        self.assertEqual(self.rappels[0], (0, TAILLE_FAUSSE))
        for (r1, t1), (r2, t2) in zip(self.rappels, self.rappels[1:], strict=False):
            self.assertLessEqual(r1, r2)
            self.assertLessEqual(t1, t2)
        for recus, total in self.rappels[:-1]:
            self.assertEqual(total, TAILLE_FAUSSE)
            self.assertLessEqual(recus, TAILLE_FAUSSE * 99 // 100)
        self.assertEqual(self.rappels[-1], (TAILLE_FAUSSE, TAILLE_FAUSSE))
        self.assertEqual(self.threads_suivi(), [])

    def test_arguments_passes_a_huggingface_hub(self):
        hub = FauxHub()
        self.telecharger(hub)
        (appel,) = hub.appels
        self.assertEqual(appel["repo_id"], "Systran/faster-whisper-small")
        self.assertEqual(appel["local_dir"], self.dossier)
        motifs = appel["allow_patterns"]
        for fichier in (*modeles.FICHIERS_REQUIS, *modeles.FICHIERS_VOCABULAIRE,
                        "preprocessor_config.json"):
            self.assertIn(fichier, motifs)
        self.assertTrue(callable(appel["tqdm_class"]))

    def test_sans_rappel(self):
        self.assertEqual(self.telecharger(FauxHub(), rappel=None), self.dossier)

    def test_reprise_apres_coupure_reseau(self):
        hub = self.hub(coupure=("model.bin", 400))
        with self.assertRaises(modeles.ErreurTelechargement) as ctx:
            self.telecharger(hub)
        self.assertIn("Connexion", str(ctx.exception))
        self.assertIsInstance(ctx.exception.__cause__, ConnectError)
        self.assertFalse(modeles.modele_complet(self.dossier))
        self.assertEqual(os.path.getsize(chemin_incomplet(self.dossier, "model.bin")),
                         400)
        self.assertEqual(self.threads_suivi(), [])

        avant = hub.par_fichier["model.bin"]
        deja_la = modeles.mesurer_octets_presents(self.dossier)
        self.assertGreaterEqual(deja_la, 400)
        self.rappels.clear()
        self.telecharger(hub)
        self.assertEqual(hub.reprises, {"model.bin": 400})
        # Seuls les 600 octets manquants sont retelecharges : pas de depart de zero.
        self.assertEqual(hub.par_fichier["model.bin"] - avant, 600)
        self.assertTrue(modeles.modele_complet(self.dossier))
        # La reprise est visible des le premier rappel : les 400 octets sont deja la.
        self.assertEqual(self.rappels[0], (deja_la, TAILLE_FAUSSE))
        self.assertEqual(self.rappels[-1], (TAILLE_FAUSSE, TAILLE_FAUSSE))

    def test_coupure_breve_reprise_dans_le_meme_appel(self):
        """http_get relance avec Range et une nouvelle barre `initial=<deja recu>`."""
        presents = []

        def rappel(recus, total):
            # Octets reellement sur le disque a cet instant : jamais depasses.
            presents.append(modeles.mesurer_octets_presents(self.dossier))
            self.rappels.append((recus, total))

        hub = self.hub(coupure=("model.bin", 500), reprise_interne=True)
        self.telecharger(hub, rappel=rappel)
        self.assertEqual(hub.reprises, {"model.bin": 500})  # la reprise a bien eu lieu
        self.assertEqual(hub.par_fichier["model.bin"], 1000)
        for (recus, _), present in zip(self.rappels, presents, strict=True):
            self.assertLessEqual(recus, present)
        for (r1, _), (r2, _) in zip(self.rappels, self.rappels[1:], strict=False):
            self.assertLessEqual(r1, r2)
        for recus, total in self.rappels[:-1]:
            self.assertLessEqual(recus, total * 99 // 100)
        self.assertEqual(self.rappels[-1], (TAILLE_FAUSSE, TAILLE_FAUSSE))
        self.assertEqual(self.threads_suivi(), [])

    def test_xet_desactive_pendant_snapshot_download_puis_restaure(self):
        hub = FauxHub()
        self.telecharger(hub)
        self.assertEqual(hub.xet_desactive, [True])
        self.assertFalse(hub.constants.HF_HUB_DISABLE_XET)

    def test_xet_restaure_meme_en_cas_d_echec_et_si_deja_desactive(self):
        hub = FauxHub(coupure=("model.bin", 400))
        with self.assertRaises(modeles.ErreurTelechargement):
            self.telecharger(hub)
        self.assertEqual(hub.xet_desactive, [True])
        self.assertFalse(hub.constants.HF_HUB_DISABLE_XET)
        autre = FauxHub()
        autre.constants.HF_HUB_DISABLE_XET = True  # reglage utilisateur conserve
        shutil.rmtree(self.dossier, ignore_errors=True)
        self.telecharger(autre)
        self.assertTrue(autre.constants.HF_HUB_DISABLE_XET)

    def test_xet_desactive_meme_si_constants_n_existe_qu_apres_import_explicite(self):
        """Sur un hub frais, `hub.constants` n'est qu'un attribut paresseux : absent."""
        constantes = SimpleNamespace(HF_HUB_DISABLE_XET=False)
        vus = []

        def snapshot_download(**kwargs):
            vus.append(constantes.HF_HUB_DISABLE_XET)
            creer_modele(kwargs["local_dir"])

        faux = types.ModuleType("faux_hub_t026")
        faux.__path__ = []
        faux.snapshot_download = snapshot_download
        self.assertFalse(hasattr(faux, "constants"))
        with mock.patch.dict(sys.modules, {"faux_hub_t026": faux,
                                           "faux_hub_t026.constants": constantes}):
            self.telecharger(faux)
        self.assertEqual(vus, [True])
        self.assertFalse(constantes.HF_HUB_DISABLE_XET)

    def test_hub_sans_constantes_ni_api_de_metadonnees(self):
        class HubNu:
            def snapshot_download(self, **kwargs):
                creer_modele(kwargs["local_dir"])
                return kwargs["local_dir"]

        self.assertEqual(self.telecharger(HubNu()), self.dossier)
        self.assertEqual(self.rappels[-1][0], self.rappels[-1][1])

    def test_telechargements_concurrents_serialises_et_xet_restaure(self):
        journal = []
        entre, liberer = threading.Event(), threading.Event()

        def bloquer():
            entre.set()
            liberer.wait(10)

        premier = FauxHub(apres_bloc=bloquer, journal=journal, etiquette="premier")
        second = FauxHub(journal=journal, etiquette="second")
        second.constants = premier.constants  # meme module `constants` pour les deux
        erreurs = []

        def lancer(nom, hub):
            try:
                modeles.telecharger(nom, surcharge="", localappdata=self.local, hf=hub)
            except Exception as exc:  # noqa: BLE001 - remontee au thread de test
                erreurs.append(exc)

        t1 = threading.Thread(target=lancer, args=("small", premier))
        t2 = threading.Thread(target=lancer, args=("base", second))
        t1.start()
        self.assertTrue(entre.wait(5))
        t2.start()
        time.sleep(0.5)  # laisse au second l'occasion (ratee) d'entrer en meme temps
        self.assertEqual(journal, [("debut", "premier")])
        liberer.set()
        t1.join(10)
        t2.join(10)
        self.assertEqual(erreurs, [])
        self.assertEqual(journal, [("debut", "premier"), ("fin", "premier"),
                                   ("debut", "second"), ("fin", "second")])
        self.assertEqual(premier.xet_desactive + second.xet_desactive, [True, True])
        self.assertFalse(premier.constants.HF_HUB_DISABLE_XET)

    def test_annulation_pendant_l_attente_du_verrou(self):
        hub = FauxHub()
        reponses = iter([False])  # 1re consultation : pas annule ; ensuite : annule

        def annuler():
            return next(reponses, True)

        with mock.patch.object(modeles, "ATTENTE_VERROU_S", 0.01), \
                modeles._VERROU_TELECHARGEMENT, \
                self.assertRaises(modeles.TelechargementAnnule):
            self.telecharger(hub, annuler=annuler)
        self.assertEqual(hub.appels, [])

    def test_total_connu_avant_le_telechargement(self):
        hub = FauxHub()
        self.telecharger(hub)
        repo, options = hub.lectures_metadonnees[0]
        self.assertEqual(repo, "Systran/faster-whisper-small")
        self.assertTrue(options["files_metadata"])
        self.assertEqual(self.rappels[0], (0, TAILLE_FAUSSE))
        self.assertEqual({total for _, total in self.rappels}, {TAILLE_FAUSSE})

    def test_total_amont_compte_aussi_les_fichiers_deja_complets(self):
        """La mesure disque compte les fichiers finis : le total doit les inclure."""
        os.makedirs(self.dossier)
        with open(os.path.join(self.dossier, "model.bin"), "wb") as f:
            f.write(FICHIERS_FAUX["model.bin"])
        self.telecharger(FauxHub())
        self.assertEqual({total for _, total in self.rappels}, {TAILLE_FAUSSE})
        deja_la = len(FICHIERS_FAUX["model.bin"])
        self.assertEqual(self.rappels[0], (deja_la, TAILLE_FAUSSE))

    def test_total_amont_indisponible_progression_indeterminee(self):
        """Total 0 = inconnu : octets recus seuls, puis (taille, taille) a la fin."""
        for metadonnees in ("echec", "sans_taille"):
            with self.subTest(metadonnees=metadonnees):
                self.rappels.clear()
                shutil.rmtree(self.dossier, ignore_errors=True)
                hub = self.hub(metadonnees=metadonnees)
                self.telecharger(hub)
                self.assertEqual(len(hub.appels), 1)
                self.assertTrue(modeles.modele_complet(self.dossier))
                self.assertGreater(len(self.rappels), 4)
                for _, total in self.rappels[:-1]:
                    self.assertEqual(total, 0)
                for (r1, _), (r2, _) in zip(self.rappels, self.rappels[1:],
                                            strict=False):
                    self.assertLessEqual(r1, r2)
                self.assertEqual(self.rappels[0], (0, 0))
                self.assertEqual(self.rappels[-1], (TAILLE_FAUSSE, TAILLE_FAUSSE))

    def test_cent_pour_cent_jamais_avant_la_fin(self):
        for metadonnees in ("ok", "echec", "sans_taille"):
            with self.subTest(metadonnees=metadonnees):
                self.rappels.clear()
                shutil.rmtree(self.dossier, ignore_errors=True)
                self.telecharger(self.hub(metadonnees=metadonnees))
                for recus, total in self.rappels[:-1]:
                    if total:  # total connu : jamais 100 % avant le dernier appel
                        self.assertLess(recus, total)
                    else:      # total inconnu : seuls les octets recus comptent
                        self.assertLessEqual(recus, TAILLE_FAUSSE)
                self.assertEqual(self.rappels[-1], (TAILLE_FAUSSE, TAILLE_FAUSSE))

    def test_hors_ligne_dossier_partiel_rendu_sans_erreur_par_hf(self):
        class HubHorsLigne:
            def snapshot_download(self, **kwargs):
                return kwargs["local_dir"]  # hf rend un local_dir non vide hors ligne

        creer_modele(self.dossier, vocabulaire=None)
        with self.assertRaises(modeles.ErreurTelechargement) as ctx:
            self.telecharger(HubHorsLigne())
        self.assertIn("incomplet", str(ctx.exception))
        self.assertFalse(modeles.modele_complet(self.dossier))

    def test_annulation_garde_les_fichiers_partiels(self):
        hub = FauxHub()
        etat = {"appels": 0}

        def annuler():
            etat["appels"] += 1
            return etat["appels"] > 6

        with self.assertRaises(modeles.TelechargementAnnule):
            self.telecharger(hub, annuler=annuler)
        self.assertFalse(modeles.modele_complet(self.dossier))
        partiel = chemin_incomplet(self.dossier, "model.bin")
        self.assertGreater(os.path.getsize(partiel), 0)
        self.assertLess(os.path.getsize(partiel), 1000)

        # Une nouvelle demande reprend et aboutit.
        self.telecharger(hub)
        self.assertTrue(modeles.modele_complet(self.dossier))
        self.assertIn("model.bin", hub.reprises)

    def test_annulation_par_evenement_avant_tout_appel_reseau(self):
        hub = FauxHub()
        evenement = threading.Event()
        evenement.set()
        with self.assertRaises(modeles.TelechargementAnnule):
            self.telecharger(hub, annuler=evenement)
        self.assertEqual(hub.appels, [])

    def test_annulation_avalee_par_hf_reste_une_annulation(self):
        hub = FauxHub(avale_les_erreurs=True)
        evenement = threading.Event()
        self.rappels.clear()

        def rappel(recus, total):
            self.rappels.append((recus, total))
            evenement.set()

        with self.assertRaises(modeles.TelechargementAnnule):
            self.telecharger(hub, annuler=evenement, rappel=rappel)
        self.assertFalse(modeles.modele_complet(self.dossier))

    def test_modele_deja_complet_rien_telecharge(self):
        creer_modele(self.dossier)
        contenu = {}
        for fichier in os.listdir(self.dossier):
            with open(os.path.join(self.dossier, fichier), "rb") as f:
                contenu[fichier] = f.read()
        hub = FauxHub()
        self.assertEqual(self.telecharger(hub), self.dossier)
        self.assertEqual(hub.appels, [])
        self.assertEqual(len(self.rappels), 1)
        recus, total = self.rappels[0]
        self.assertGreater(total, 0)
        self.assertEqual(recus, total)
        for fichier, octets in contenu.items():
            with open(os.path.join(self.dossier, fichier), "rb") as f:
                self.assertEqual(f.read(), octets)

    def test_echec_ne_supprime_pas_un_modele_complet(self):
        creer_modele(self.dossier)
        hub = FauxHub(erreur=ConnectError("coupe"))
        self.telecharger(hub)  # complet : hf n'est meme pas appele
        self.assertTrue(modeles.modele_complet(self.dossier))
        self.assertEqual(hub.appels, [])

    def test_nom_inconnu(self):
        with self.assertRaises(ValueError):
            modeles.telecharger("inconnu", hf=FauxHub())

    def test_huggingface_hub_absent(self):
        with mock.patch.dict(sys.modules, {"huggingface_hub": None}), \
                self.assertRaises(modeles.ErreurTelechargement) as ctx:
            modeles.telecharger(self.nom, surcharge="", localappdata=self.local)
        self.assertIn("huggingface_hub", str(ctx.exception))


class TestMessagesDErreur(unittest.TestCase):
    """Chaque cause d'echec donne un message francais distinct, sans modele annonce."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def echec(self, erreur):
        class Hub:
            def snapshot_download(self, **kwargs):
                raise erreur

        with self.assertRaises(modeles.ErreurTelechargement) as ctx:
            modeles.telecharger("medium", surcharge="", localappdata=self._tmp.name,
                                hf=Hub())
        self.assertIs(ctx.exception.__cause__, erreur)
        self.assertFalse(modeles.modele_complet(
            modeles.dossier_modele("medium", "", self._tmp.name)))
        return str(ctx.exception)

    def test_depot_introuvable(self):
        message = self.echec(RepositoryNotFoundError("404"))
        self.assertIn("introuvable", message)
        self.assertIn("Systran/faster-whisper-medium", message)

    def test_reseau_coupe(self):
        self.assertIn("Connexion", self.echec(ConnectError("x")))

    def test_aucun_cache_local_de_secours(self):
        self.assertIn("Connexion", self.echec(LocalEntryNotFoundError("x")))

    def test_mode_hors_ligne(self):
        class OfflineModeIsEnabled(ConnectionError):
            pass

        self.assertIn("Connexion", self.echec(OfflineModeIsEnabled("x")))

    def test_disque_plein_errno(self):
        message = self.echec(OSError(errno.ENOSPC, "No space left on device"))
        self.assertIn("Espace disque insuffisant", message)

    def test_disque_plein_windows(self):
        erreur = OSError(0, "disque plein")
        erreur.winerror = modeles.WINERROR_DISQUE_PLEIN
        self.assertIn("Espace disque insuffisant", self.echec(erreur))

    def test_disque_plein_derriere_une_autre_exception(self):
        try:
            try:
                raise OSError(errno.ENOSPC, "plein")
            except OSError as cause:
                raise RuntimeError("ecriture impossible") from cause
        except RuntimeError as erreur:
            self.assertIn("Espace disque insuffisant", self.echec(erreur))

    def test_erreur_http_du_serveur(self):
        self.assertIn("a répondu par une erreur", self.echec(HfHubHTTPError("500")))

    def test_erreur_inattendue(self):
        message = self.echec(RuntimeError("boum"))
        self.assertIn("a échoué", message)
        self.assertIn("RuntimeError", message)


def ecrire_octets(chemin, taille):
    os.makedirs(os.path.dirname(chemin), exist_ok=True)
    with open(chemin, "wb") as f:
        f.write(b"x" * taille)


class TestMesureDisque(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dossier = self._tmp.name

    def test_dossier_absent_ou_vide(self):
        self.assertEqual(
            modeles.mesurer_octets_presents(os.path.join(self.dossier, "absent")), 0)
        self.assertEqual(modeles.mesurer_octets_presents(self.dossier), 0)

    def test_fichiers_finis_et_incomplets(self):
        ecrire_octets(os.path.join(self.dossier, "config.json"), 10)
        ecrire_octets(os.path.join(self.dossier, "README.md"), 999)  # non attendu
        ecrire_octets(chemin_incomplet(self.dossier, "model.bin", "a"), 300)
        ecrire_octets(chemin_incomplet(self.dossier, "model.bin", "b"), 700)  # + gros
        ecrire_octets(chemin_incomplet(self.dossier, "tokenizer.json"), 5)
        ecrire_octets(os.path.join(self.dossier, "tokenizer.json"), 20)  # fini prime
        # Autres fichiers du dossier de reprise : jamais comptes.
        reprise = os.path.join(self.dossier, ".cache", "huggingface", "download")
        ecrire_octets(os.path.join(reprise, "model.bin.metadata"), 50)
        ecrire_octets(os.path.join(reprise, "vocabulary.txt.lock"), 50)
        ecrire_octets(chemin_incomplet(self.dossier, "autre.bin"), 400)
        self.assertEqual(modeles.mesurer_octets_presents(self.dossier), 10 + 700 + 20)

    def test_nom_incomplet_identique_a_celui_de_huggingface_hub(self):
        try:
            from huggingface_hub._local_folder import get_local_download_paths
        except ImportError:
            self.skipTest("huggingface_hub absent")
        chemin = get_local_download_paths(pathlib.Path(self.dossier), "model.bin")
        reel = chemin.incomplete_path("abc123")
        self.assertEqual(reel.parent, pathlib.Path(self.dossier) / ".cache"
                         / "huggingface" / "download")
        self.assertTrue(reel.name.startswith(modeles._prefixe_incomplet("model.bin")))
        self.assertTrue(reel.name.endswith(".abc123.incomplete"))
        self.assertEqual(str(reel),
                         chemin_incomplet(self.dossier, "model.bin", "abc123"))


class TestSuiviDisque(unittest.TestCase):
    def suivi(self, valeurs, total=0, intervalle_s=60):
        appels = []
        suite = iter(valeurs)
        suivi = modeles._SuiviDisque(lambda r, t: appels.append((r, t)), "d",
                                     lambda dossier: next(suite), intervalle_s, total)
        return suivi, appels

    def test_plafond_et_jamais_de_recul(self):
        suivi, appels = self.suivi([0, 500, 400, 2000, 2000, 1000, 1000], total=1000)
        for _ in range(6):
            suivi.tick()
        # 400 (recul) et le 2e 2000 (inchange) n'emettent rien ; 99 % au plus.
        self.assertEqual(appels, [(0, 1000), (500, 1000), (990, 1000)])
        suivi.terminer()
        self.assertEqual(appels[-1], (1000, 1000))

    def test_total_inconnu_sans_plafond_puis_fin_avec_la_taille_reelle(self):
        suivi, appels = self.suivi([0, 300, 5000, 100, 5200])
        for _ in range(4):
            suivi.tick()
        self.assertEqual(appels, [(0, 0), (300, 0), (5000, 0)])
        suivi.terminer()
        self.assertEqual(appels[-1], (5200, 5200))

    def test_sans_rappel(self):
        suivi = modeles._SuiviDisque(None, "d", lambda dossier: 7, 60)
        suivi.tick()
        suivi.terminer()

    def test_thread_demon_arrete_proprement(self):
        compteur = iter(range(1000))
        trois = threading.Event()
        appels = []

        def rappel(recus, total):
            appels.append(recus)
            if len(appels) >= 3:
                trois.set()

        suivi = modeles._SuiviDisque(rappel, "d", lambda dossier: next(compteur), 0.001)
        suivi.demarrer()
        try:
            self.assertTrue(suivi._thread.daemon)
            self.assertTrue(trois.wait(5), "le thread de suivi ne mesure pas")
        finally:
            suivi.arreter()
        self.assertFalse(suivi._thread.is_alive())
        n = len(appels)
        time.sleep(0.02)
        self.assertEqual(len(appels), n)  # plus rien apres l'arret

    def test_arreter_sans_avoir_demarre(self):
        suivi, _ = self.suivi([])
        suivi.arreter()

    def test_erreur_de_rappel_dans_le_thread_restituee_a_la_fin(self):
        def rappel(recus, total):
            if recus > 0:
                raise ValueError("rappel casse")

        suite = iter(range(100))
        suivi = modeles._SuiviDisque(rappel, "d", lambda dossier: next(suite), 0.001)
        suivi.demarrer()
        suivi._thread.join(5)
        self.assertFalse(suivi._thread.is_alive())
        with self.assertRaises(ValueError):
            suivi.terminer()


class TestAnnulation(unittest.TestCase):
    def test_annulation_collante(self):
        reponses = iter([False, True])
        annulation = modeles._Annulation(lambda: next(reponses))
        annulation.verifier()
        with self.assertRaises(modeles.TelechargementAnnule):
            annulation.verifier()
        with self.assertRaises(modeles.TelechargementAnnule):
            annulation.verifier()  # ne consulte plus `annuler` (l'iterateur est epuise)
        self.assertTrue(annulation.demandee)

    def test_barre_ne_sert_qu_a_annuler(self):
        evenement = threading.Event()
        classe = modeles._fabriquer_classe_barre(modeles._Annulation(evenement))
        fichiers = classe(iter([1, 2, 3]), desc="Fetching", total=3)
        self.assertEqual(list(fichiers), [1, 2, 3])
        octets = classe(desc="x", total=0, initial=0, unit="B")
        octets.total += 40  # hf fait `bytes_progress.total += n`
        octets.update(10)
        evenement.set()
        with self.assertRaises(modeles.TelechargementAnnule):
            octets.update(10)
        verrou = classe.get_lock()
        classe.set_lock(verrou)
        self.assertIs(classe.get_lock(), verrou)


class TestChoixInitial(unittest.TestCase):
    def test_modele_affiche_depuis_la_cle_sinon_la_config(self):
        self.assertEqual(modeles.modele_affiche("small", "medium"), "small")
        self.assertEqual(modeles.modele_affiche("", "medium"), "medium")
        self.assertEqual(modeles.modele_affiche(None, "medium"), "medium")
        self.assertEqual(modeles.modele_affiche("inconnu", "medium"), "medium")

    def test_cle_absente_choix_selon_la_ram(self):
        self.assertEqual(modeles.modele_a_enregistrer("", 4.0), "small")
        self.assertEqual(modeles.modele_a_enregistrer(None, 16.0), "medium")
        self.assertEqual(modeles.modele_a_enregistrer("  ", 8.0), "medium")

    def test_choix_existant_jamais_ecrase(self):
        for ram in (2.0, 64.0):
            self.assertIsNone(modeles.modele_a_enregistrer("tiny", ram))
            self.assertIsNone(modeles.modele_a_enregistrer("large-v3", ram, "small"))

    def test_modele_deja_present_conserve_au_premier_lancement(self):
        self.assertEqual(modeles.modele_a_enregistrer("", 4.0, "medium"), "medium")
        # un nom hors liste n'est pas retenu
        self.assertEqual(modeles.modele_a_enregistrer("", 4.0, "large-v2"), "small")

    def test_cle_de_settings_et_defaut_vide(self):
        import user_config
        self.assertEqual(modeles.CLE_MODELE_CHOISI, "whisper_model")
        self.assertEqual(user_config._DEFAULTS[modeles.CLE_MODELE_CHOISI], "")


class TestLibelleProgression(unittest.TestCase):
    def test_total_connu(self):
        mo = modeles.OCTETS_PAR_MO
        self.assertEqual(
            modeles.libelle_progression("small", 210 * mo, 500 * mo),
            "Téléchargement du modèle small : 42 % — 210 / 500 Mo")

    def test_total_inconnu(self):
        self.assertEqual(
            modeles.libelle_progression("small", 210 * modeles.OCTETS_PAR_MO, 0),
            "Téléchargement du modèle small — 210 Mo reçus")

    def test_zero_octet(self):
        mo = modeles.OCTETS_PAR_MO
        self.assertEqual(
            modeles.libelle_progression("base", 0, 100 * mo),
            "Téléchargement du modèle base : 0 % — 0 / 100 Mo")
        self.assertEqual(
            modeles.libelle_progression("base", 0, 0),
            "Téléchargement du modèle base — 0 Mo reçus")

    def test_cent_pour_cent_et_jamais_au_dela(self):
        mo = modeles.OCTETS_PAR_MO
        self.assertEqual(
            modeles.libelle_progression("small", 480 * mo, 480 * mo),
            "Téléchargement du modèle small : 100 % — 480 / 480 Mo")
        self.assertIn("100 % — 480 / 480 Mo",
                      modeles.libelle_progression("small", 600 * mo, 480 * mo))

    def test_arrondi_vers_le_bas(self):
        self.assertIn(": 99 %", modeles.libelle_progression("small", 999, 1000))

    def test_libelle_de_demarrage(self):
        self.assertEqual(modeles.libelle_demarrage("small"),
                         "Téléchargement du modèle small : en attente…")

    def test_points_d_activite_cycliques(self):
        self.assertEqual([modeles.points_activite(i) for i in range(5)],
                         ["", ".", "..", "...", ""])


class TestDecisionJob(unittest.TestCase):
    def test_transcription_requise(self):
        self.assertTrue(modeles.transcription_requise("queued", "", "a.wav"))
        self.assertTrue(modeles.transcription_requise("error", "", "a.wav"))
        # relance avec transcript deja la mais audio present : on retranscrit
        self.assertTrue(modeles.transcription_requise("done", "texte", "a.wav"))
        # transcript seul : pas de Whisper
        self.assertFalse(modeles.transcription_requise("done", "texte", ""))

    def test_pret_ou_attente(self):
        self.assertEqual(modeles.decision_job(True, True), modeles.PRET)
        self.assertEqual(modeles.decision_job(True, False), modeles.ATTENTE_MODELE)
        self.assertEqual(modeles.decision_job(False, False), modeles.PRET)
        self.assertEqual(modeles.decision_job(False, True), modeles.PRET)

    def test_libelle_d_attente(self):
        self.assertEqual(modeles.LIBELLE_ATTENTE_MODELE, "En attente du modèle…")


class TestNouvelEssai(unittest.TestCase):
    def test_delai_nomme_et_positif(self):
        self.assertGreater(modeles.DELAI_NOUVEL_ESSAI_S, 0)

    def test_duree_lisible(self):
        self.assertEqual(modeles.duree_lisible(45), "45 s")
        self.assertEqual(modeles.duree_lisible(60), "1 min")
        self.assertEqual(modeles.duree_lisible(300), "5 min")
        self.assertEqual(modeles.duree_lisible(301), "6 min")
        self.assertEqual(modeles.duree_lisible(-3), "0 s")

    def test_libelle_echec_donne_cause_et_delai(self):
        texte = modeles.libelle_echec("small", "Connexion coupée.", 300)
        self.assertEqual(
            texte,
            "Téléchargement du modèle small impossible : Connexion coupée. "
            "Nouvel essai dans 5 min.")
        self.assertIn(modeles.duree_lisible(modeles.DELAI_NOUVEL_ESSAI_S),
                      modeles.libelle_echec("small", "x."))


class FauxJob:
    def __init__(self, id_, modele="small", statut="queued"):
        self.id = id_
        self.model_name = modele
        self.status_transcript = statut


class TestAttenteModele(unittest.TestCase):
    """M1 : une seule source de verite entre liste d'attente et file de traitement."""

    def test_sans_job_retire_par_identifiant(self):
        a, b = FauxJob("a"), FauxJob("b")
        self.assertEqual(modeles.sans_job([a, b, FauxJob("a")], "a"), [b])

    def test_relance_pendant_l_attente_un_seul_traitement(self):
        attente, file = [FauxJob("a")], []
        # relance (bouton, « tout transcrire », demarrage) : retire de l'attente puis file
        job = FauxJob("a")
        attente = modeles.sans_job(attente, job.id)
        file.append(job)
        # le worker le reprend, `_job_attend_modele` le remet en attente (modele absent)
        file.clear()
        attente.append(job)
        attente.append(job)  # meme job ajoute deux fois : doublon d'attente
        liberes = modeles.jobs_a_liberer(attente, file, lambda nom: True)
        self.assertEqual([j.id for j in liberes], ["a"])  # un seul traitement

    def test_job_relance_modele_complet_pas_retraite_a_la_liberation(self):
        job = FauxJob("a")
        attente = [job]
        attente = modeles.sans_job(attente, job.id)  # relance : retire
        file = [job]  # transcrit tout de suite
        self.assertEqual(modeles.jobs_a_liberer(attente, file, lambda n: True), [])
        # meme si la relance avait oublie de le retirer, la file l'emporte
        self.assertEqual(modeles.jobs_a_liberer([job], file, lambda n: True), [])

    def test_job_plus_en_attente_est_ecarte(self):
        for statut in ("running", "done", "error"):
            job = FauxJob("a", statut=statut)
            self.assertEqual(modeles.jobs_a_conserver([job], []), [])
            self.assertEqual(modeles.jobs_a_liberer([job], [], lambda n: True), [])

    def test_modele_incomplet_reste_en_attente(self):
        job = FauxJob("a", "large-v3")
        self.assertEqual(modeles.jobs_a_liberer([job], [], lambda n: False), [])
        self.assertEqual(modeles.jobs_a_conserver([job], []), [job])

    def test_liberation_selon_le_modele_de_chaque_job(self):
        a, b = FauxJob("a", "small"), FauxJob("b", "medium")
        liberes = modeles.jobs_a_liberer([a, b], [], lambda n: n == "medium")
        self.assertEqual(liberes, [b])


class TestModelesVoulus(unittest.TestCase):
    """B2 : un modele que plus personne ne veut n'est plus telecharge."""

    def test_garde_choix_courant_et_attendus_dans_l_ordre(self):
        self.assertEqual(
            modeles.modeles_voulus(["tiny", "small", "medium"], "medium", ["small"]),
            ["small", "medium"])

    def test_abandonne_ce_que_personne_ne_veut(self):
        self.assertEqual(modeles.modeles_voulus(["tiny"], "small", []), [])
        self.assertEqual(modeles.modeles_voulus([], "small", ["tiny"]), [])

    def test_est_libelle_echec(self):
        texte = modeles.libelle_echec("small", "Réseau coupé.")
        self.assertTrue(modeles.est_libelle_echec(texte, "small"))
        self.assertFalse(modeles.est_libelle_echec(texte, "medium"))
        self.assertFalse(modeles.est_libelle_echec(
            modeles.libelle_progression("small", 1, 2), "small"))


if __name__ == "__main__":
    unittest.main()
