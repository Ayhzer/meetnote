"""Tests de la résolution des chemins Python des scripts de build."""

import ast
import getpass
import os
import re
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

import build_paths
import build_version
import version

AUTRE_RACINE = r"D:\Python\Custom"


class ResolvePythonHomeTest(unittest.TestCase):
    def test_defaut_est_base_prefix(self):
        with mock.patch.dict(os.environ):
            os.environ.pop(build_paths.ENV_PYTHON_HOME, None)
            self.assertEqual(build_paths.resolve_python_home(), sys.base_prefix)

    def test_variable_d_environnement_prise_en_compte(self):
        env = {build_paths.ENV_PYTHON_HOME: AUTRE_RACINE}
        with mock.patch.dict(os.environ, env):
            self.assertEqual(build_paths.resolve_python_home(), AUTRE_RACINE)

    def test_variable_vide_ignoree(self):
        for valeur in ("", "   "):
            with mock.patch.dict(os.environ, {build_paths.ENV_PYTHON_HOME: valeur}):
                self.assertEqual(build_paths.resolve_python_home(), sys.base_prefix)

    def test_site_packages_derive_de_la_racine(self):
        attendu = os.path.join(AUTRE_RACINE, "Lib", "site-packages")
        resolu = build_paths.resolve_site_packages(AUTRE_RACINE)
        self.assertEqual(resolu, attendu)

    def test_site_packages_sans_argument_suit_la_resolution(self):
        env = {build_paths.ENV_PYTHON_HOME: AUTRE_RACINE}
        with mock.patch.dict(os.environ, env):
            attendu = os.path.join(AUTRE_RACINE, "Lib", "site-packages")
            self.assertEqual(build_paths.resolve_site_packages(), attendu)


class DistDirNameTest(unittest.TestCase):
    def test_dossier_complet_porte_la_version(self):
        self.assertEqual(build_paths.dist_dir_name("3.3.0"), "MeetNote-3.3.0")

    def test_dossier_slim_porte_la_version(self):
        attendu = "MeetNote-slim-3.3.0"
        self.assertEqual(build_paths.dist_dir_name("3.3.0", slim=True), attendu)

    def test_espaces_ignores(self):
        self.assertEqual(build_paths.dist_dir_name(" 1.2.3 "), "MeetNote-1.2.3")


class VersionTupleTest(unittest.TestCase):
    def test_complete_a_quatre_composantes(self):
        self.assertEqual(build_version.version_tuple("3.3.0"), (3, 3, 0, 0))
        self.assertEqual(build_version.version_tuple("1.2"), (1, 2, 0, 0))
        self.assertEqual(build_version.version_tuple("1.2.3.4"), (1, 2, 3, 4))

    def test_format_invalide_refuse(self):
        for invalide in ("", "3.x.0", "3.3.0-beta", "1.2.3.4.5", "3..0"):
            with self.subTest(invalide=invalide), self.assertRaises(ValueError):
                build_version.version_tuple(invalide)


class VersionResourceTest(unittest.TestCase):
    def setUp(self):
        self.texte = build_version.build_version_resource("3.3.0")

    def test_version_en_tuple_et_en_chaine(self):
        self.assertIn("filevers=(3, 3, 0, 0)", self.texte)
        self.assertIn("prodvers=(3, 3, 0, 0)", self.texte)
        self.assertIn("StringStruct('FileVersion', '3.3.0')", self.texte)
        self.assertIn("StringStruct('ProductVersion', '3.3.0')", self.texte)

    def test_suit_version_py(self):
        texte = build_version.build_version_resource(version.VERSION)
        self.assertIn(f"StringStruct('FileVersion', '{version.VERSION}')", texte)

    def test_identite_du_produit(self):
        for champ, valeur in (("ProductName", "MeetNote"),
                              ("FileDescription", "MeetNote"),
                              ("OriginalFilename", "MeetNote.exe")):
            self.assertIn(f"StringStruct('{champ}', '{valeur}')", self.texte)

    def test_aucune_donnee_personnelle_ni_chemin(self):
        self.assertNotIn(getpass.getuser().lower(), self.texte.lower())
        self.assertNotIn(os.path.expanduser("~").lower(), self.texte.lower())
        self.assertIsNone(re.search(r"[A-Za-z]:[/\\]", self.texte))

    def test_texte_relu_par_pyinstaller(self):
        """Le texte est relu par l'outil de PyInstaller (même eval que le build)."""
        try:
            from PyInstaller.utils.win32 import versioninfo
        except ImportError:
            self.skipTest("PyInstaller non installé")
        with tempfile.TemporaryDirectory() as dossier:
            chemin = os.path.join(dossier, "v.txt")
            with open(chemin, "w", encoding="utf-8") as f:
                f.write(self.texte)
            info = versioninfo.load_version_info_from_text_file(chemin)
        # 3.3.0.0 → MS = (3 << 16) | 3, LS = 0
        self.assertEqual(info.ffi.fileVersionMS, (3 << 16) | 3)
        self.assertEqual(info.ffi.fileVersionLS, 0)
        self.assertEqual(info.ffi.productVersionMS, (3 << 16) | 3)
        champs = {c.name: c.val for c in info.kids[0].kids[0].kids}
        self.assertEqual(champs["FileVersion"], "3.3.0")
        self.assertEqual(champs["ProductName"], "MeetNote")


class DatasEmbarqueesTest(unittest.TestCase):
    """Le spec n'embarque aucun modèle Whisper (retiré du paquet, T028)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.site = os.path.join(self._tmp.name, "site-packages")
        for sous_dossier, _dest in build_paths.PAQUETS_EMBARQUES:
            os.makedirs(os.path.join(self.site, sous_dossier))
        self.messages = []

    def _collecter(self, trouver=lambda nom: None):
        return build_paths.collecter_datas(
            self.site, trouver, self.messages.append, environ={})

    def test_paquets_presents_embarques(self):
        destinations = {dest for _src, dest in self._collecter()}
        self.assertEqual(destinations,
                         {dest for _s, dest in build_paths.PAQUETS_EMBARQUES})

    def test_paquet_absent_ignore(self):
        os.rmdir(os.path.join(self.site, "soundcard"))
        destinations = {dest for _src, dest in self._collecter()}
        self.assertNotIn("soundcard", destinations)

    def test_aucun_dossier_de_modele_dans_le_spec(self):
        # Même avec un cache Hugging Face présent sur le poste de build.
        cache = os.path.join(self._tmp.name, "hf", "models--Systran--faster-whisper-x")
        os.makedirs(cache)
        bloc = build_paths.formater_datas(self._collecter(lambda nom: None))
        for interdit in ("faster_whisper_models", "models--Systran", "faster-whisper-",
                         "huggingface", "snapshots", "r'models'", "/models"):
            self.assertNotIn(interdit, bloc)
        self.assertNotIn("model.bin", bloc)

    def test_cache_vide_ne_fait_pas_echouer(self):
        """Aucun modèle trouvé n'est plus une erreur : la liste est rendue."""
        self.assertIsInstance(self._collecter(), list)

    def test_binaires_trouves_copies_a_la_racine(self):
        faux = {"ffmpeg": "C:/outils/ffmpeg.exe", "ffprobe": "C:/outils/ffprobe.exe"}
        datas = self._collecter(faux.get)
        self.assertIn(("C:/outils/ffmpeg.exe", "."), datas)
        self.assertNotIn(("C:/outils/ffprobe.exe", "."), datas)

    def test_ffprobe_n_est_plus_embarque(self):
        self.assertNotIn("ffprobe.exe", build_paths.BINAIRES_EMBARQUES)
        appels = []
        self._collecter(lambda nom: appels.append(nom))
        self.assertFalse(any("ffprobe" in nom for nom in appels))

    def test_binaire_absent_signale(self):
        self._collecter()
        self.assertTrue(any("ffmpeg.exe" in m for m in self.messages))

    def test_format_du_bloc(self):
        bloc = build_paths.formater_datas([("C:/a/b", "dest"), ("C:/c.exe", ".")])
        self.assertEqual(bloc, "    (r'C:/a/b', r'dest'),\n    (r'C:/c.exe', r'.'),")


class ResoudreFfmpegTest(unittest.TestCase):
    """ffmpeg vient de MEETNOTE_FFMPEG, à défaut du PATH (T031)."""

    def setUp(self):
        self.messages = []

    def _resoudre(self, environ, trouve=None, existe=lambda chemin: True):
        return build_paths.resoudre_ffmpeg(
            environ, lambda nom: trouve, self.messages.append, existe)

    def test_variable_valide_prime_sur_le_path(self):
        env = {build_paths.ENV_FFMPEG: r"D:\essentials\ffmpeg.exe"}
        self.assertEqual(self._resoudre(env, r"C:\path\ffmpeg.exe"),
                         r"D:\essentials\ffmpeg.exe")
        self.assertEqual(self.messages, [])

    def test_sans_variable_le_path_est_utilise(self):
        self.assertEqual(self._resoudre({}, r"C:\path\ffmpeg.exe"),
                         r"C:\path\ffmpeg.exe")

    def test_variable_vide_ignoree(self):
        env = {build_paths.ENV_FFMPEG: "  "}
        self.assertEqual(self._resoudre(env, "X"), "X")
        self.assertEqual(self.messages, [])

    def test_variable_invalide_signalee_et_repli_sur_le_path(self):
        env = {build_paths.ENV_FFMPEG: r"D:\absent\ffmpeg.exe"}
        resolu = self._resoudre(env, r"C:\path\ffmpeg.exe",
                                existe=lambda chemin: False)
        self.assertEqual(resolu, r"C:\path\ffmpeg.exe")
        self.assertTrue(any(build_paths.ENV_FFMPEG in m and "absent" in m
                            for m in self.messages))

    def test_rien_nulle_part_rend_none(self):
        self.assertIsNone(self._resoudre({}))

    def test_collecter_datas_embarque_le_ffmpeg_de_la_variable(self):
        with tempfile.TemporaryDirectory() as tmp:
            ffmpeg = os.path.join(tmp, "ffmpeg.exe")
            Path(ffmpeg).write_bytes(b"x" * 10)
            datas = build_paths.collecter_datas(
                tmp, lambda nom: None, lambda m: None,
                environ={build_paths.ENV_FFMPEG: ffmpeg})
        self.assertIn((ffmpeg.replace("\\", "/"), "."), datas)


class PoidsTest(unittest.TestCase):
    """Contrôles de poids : fonctions pures, seuils nommés en constante."""

    MO = build_paths.OCTETS_PAR_MO

    def test_unite_coherente_avec_modeles(self):
        import modeles
        self.assertEqual(build_paths.OCTETS_PAR_MO, modeles.OCTETS_PAR_MO)

    def test_ffmpeg_sous_le_seuil_sans_alerte(self):
        limite = build_paths.POIDS_MAX_FFMPEG_MO * self.MO
        self.assertIsNone(build_paths.avertissement_poids_ffmpeg(limite))

    def test_ffmpeg_complet_signale_en_clair(self):
        message = build_paths.avertissement_poids_ffmpeg(232 * self.MO)
        self.assertIn("ffmpeg complet détecté (232.0 Mo", message)
        self.assertIn("essentials", message)
        self.assertIn(build_paths.ENV_FFMPEG, message)

    def test_archive_sous_le_seuil_sans_alerte(self):
        limite = build_paths.POIDS_MAX_ARCHIVE_MO * self.MO
        self.assertIsNone(build_paths.avertissement_poids_archive(limite))

    def test_archive_trop_lourde_signalee(self):
        octets = (build_paths.POIDS_MAX_ARCHIVE_MO + 1) * self.MO
        message = build_paths.avertissement_poids_archive(octets)
        self.assertIn("301.0 Mo", message)
        self.assertIn("300 Mo", message)

    def test_un_octet_au_dela_du_seuil_n_est_pas_affiche_egal_au_seuil(self):
        octets = build_paths.POIDS_MAX_ARCHIVE_MO * self.MO + 1
        message = build_paths.avertissement_poids_archive(octets)
        self.assertIn("archive de 300.1 Mo, au-delà de 300 Mo", message)
        octets = build_paths.POIDS_MAX_FFMPEG_MO * self.MO + 1
        message = build_paths.avertissement_poids_ffmpeg(octets)
        self.assertIn("120.1 Mo, au-delà de 120 Mo", message)

    def test_arrondi_par_exces_a_une_decimale(self):
        self.assertEqual(build_paths.mo_arrondi_au_dessus(301 * self.MO), 301.0)
        self.assertEqual(build_paths.mo_arrondi_au_dessus(301 * self.MO + 1), 301.1)

    def test_seuils_attendus(self):
        self.assertEqual(build_paths.POIDS_MAX_ARCHIVE_MO, 300)
        self.assertEqual(build_paths.POIDS_MAX_FFMPEG_MO, 120)

    def test_collecter_datas_avertit_d_un_ffmpeg_trop_lourd(self):
        messages = []
        build_paths.collecter_datas(
            "absent", lambda nom: "C:/outils/ffmpeg.exe", messages.append,
            environ={}, taille=lambda chemin: 232 * self.MO)
        self.assertTrue(any("ffmpeg complet détecté (232.0 Mo" in m for m in messages))

    def test_collecter_datas_ffmpeg_leger_sans_alerte(self):
        messages = []
        build_paths.collecter_datas(
            "absent", lambda nom: "C:/outils/ffmpeg.exe", messages.append,
            environ={}, taille=lambda chemin: 90 * self.MO)
        self.assertFalse(any("ATTENTION" in m for m in messages))
        self.assertTrue(any("90 Mo" in m for m in messages))

    def test_alerte_ffmpeg_retenue_pour_le_bilan(self):
        alertes = []
        build_paths.collecter_datas(
            "absent", lambda nom: "C:/outils/ffmpeg.exe", lambda m: None,
            environ={}, taille=lambda chemin: 232 * self.MO, alertes=alertes)
        self.assertEqual(len(alertes), 1)
        self.assertIn("ffmpeg complet détecté", alertes[0])

    def test_ffmpeg_absent_retenu_pour_le_bilan(self):
        alertes = []
        build_paths.collecter_datas(
            "absent", lambda nom: None, lambda m: None, environ={},
            alertes=alertes)
        self.assertTrue(any("ffmpeg.exe" in a for a in alertes))

    def test_variable_invalide_retenue_pour_le_bilan(self):
        alertes = []
        build_paths.collecter_datas(
            "absent", lambda nom: None, lambda m: None,
            environ={build_paths.ENV_FFMPEG: r"D:\absent\ffmpeg.exe"},
            alertes=alertes)
        self.assertTrue(any(build_paths.ENV_FFMPEG in a for a in alertes))

    def test_ffmpeg_leger_ne_laisse_aucune_alerte(self):
        alertes = []
        build_paths.collecter_datas(
            "absent", lambda nom: "C:/outils/ffmpeg.exe", lambda m: None,
            environ={}, taille=lambda chemin: 90 * self.MO, alertes=alertes)
        self.assertEqual(alertes, [])


class BilanTest(unittest.TestCase):
    """Le bilan final répète les alertes, que le journal PyInstaller noie."""

    def test_sans_alerte_aucune_ligne(self):
        self.assertEqual(build_paths.composer_bilan([]), [])

    def test_alertes_listees_apres_un_titre_compte(self):
        lignes = build_paths.composer_bilan(["ffmpeg trop lourd", "archive lourde"])
        self.assertIn("2 alerte(s)", lignes[0])
        self.assertEqual(lignes[1:], ["  - ffmpeg trop lourd", "  - archive lourde"])

    def test_alertes_d_archive_et_de_ffmpeg_dans_le_meme_bilan(self):
        alertes = []
        build_paths.collecter_datas(
            "absent", lambda nom: "C:/o/ffmpeg.exe", lambda m: None, environ={},
            taille=lambda chemin: 232 * build_paths.OCTETS_PAR_MO, alertes=alertes)
        with tempfile.TemporaryDirectory() as tmp:
            source = os.path.join(tmp, "MeetNote")
            os.makedirs(source)
            Path(source, "MeetNote.exe").write_bytes(b"x")
            with mock.patch.object(build_paths, "POIDS_MAX_ARCHIVE_MO", 0):
                build_paths.produire_archive(
                    source, "9.9.9", tmp, journal=lambda m: None, alertes=alertes)
        texte = "\n".join(build_paths.composer_bilan(alertes))
        self.assertIn("ffmpeg complet détecté", texte)
        self.assertIn("archive de", texte)


class FfmpegRenommeTest(unittest.TestCase):
    """Un ffmpeg d'un autre nom est embarqué sous « ffmpeg.exe » (A2, T031)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.source = os.path.join(self._tmp.name, "ffmpeg-essentials-7.exe")
        Path(self.source).write_bytes(b"binaire")
        self.etape = os.path.join(self._tmp.name, "etape")
        self.messages = []

    def _collecter(self, **options):
        return build_paths.collecter_datas(
            "absent", lambda nom: None, self.messages.append,
            environ={build_paths.ENV_FFMPEG: self.source}, **options)

    def test_copie_nommee_ffmpeg_exe_embarquee(self):
        datas = self._collecter(etape=self.etape)
        copie = os.path.join(self.etape, "ffmpeg.exe")
        self.assertEqual(datas, [(copie.replace("\\", "/"), ".")])
        self.assertEqual(Path(copie).read_bytes(), b"binaire")
        # L'original n'est jamais touché.
        self.assertTrue(os.path.isfile(self.source))

    def test_nom_conforme_embarque_tel_quel_sans_copie(self):
        conforme = os.path.join(self._tmp.name, "FFMPEG.EXE")
        Path(conforme).write_bytes(b"x")
        copies = []
        datas = build_paths.collecter_datas(
            "absent", lambda nom: None, self.messages.append,
            environ={build_paths.ENV_FFMPEG: conforme}, etape=self.etape,
            copier=lambda *args: copies.append(args))
        self.assertEqual(datas, [(conforme.replace("\\", "/"), ".")])
        self.assertEqual(copies, [])

    def test_sans_dossier_d_etape_le_decalage_est_signale(self):
        alertes = []
        datas = self._collecter(alertes=alertes)
        self.assertEqual(datas, [(self.source.replace("\\", "/"), ".")])
        self.assertTrue(any("ffmpeg-essentials-7.exe" in a and "ffmpeg.exe" in a
                            for a in alertes))


class NomArchiveTest(unittest.TestCase):
    def test_archive_complete(self):
        self.assertEqual(build_paths.nom_archive("3.3.0"), "MeetNote-3.3.0.zip")

    def test_archive_slim_coherente_avec_le_dossier(self):
        self.assertEqual(build_paths.nom_archive("3.3.0", slim=True),
                         "MeetNote-slim-3.3.0.zip")
        for slim in (False, True):
            self.assertEqual(build_paths.nom_archive("3.3.0", slim),
                             build_paths.dist_dir_name("3.3.0", slim) + ".zip")

    def test_suit_version_py(self):
        self.assertEqual(build_paths.nom_archive(version.VERSION),
                         f"MeetNote-{version.VERSION}.zip")


class ResteTest(unittest.TestCase):
    """`lister_restes` : fonction pure, chemins relatifs à barres obliques."""

    def test_aucun_reste_si_tout_est_dans_la_sortie_fraiche(self):
        frais = ["MeetNote.exe", "_internal/a.dll"]
        self.assertEqual(build_paths.lister_restes(frais, frais), [])

    def test_dossier_absent_nomme_une_seule_fois(self):
        livres = ["MeetNote.exe", "_internal/torch/x.dll",
                  "_internal/torch/lib/y.dll"]
        restes = build_paths.lister_restes(livres, ["MeetNote.exe"])
        self.assertEqual(restes, ["_internal"])

    def test_fichier_perdu_dans_un_dossier_encore_present_nomme_seul(self):
        livres = ["_internal/ffprobe.EXE", "_internal/a.dll", "_internal/torch/x.dll"]
        frais = ["_internal/a.dll"]
        self.assertEqual(build_paths.lister_restes(livres, frais),
                         ["_internal/ffprobe.EXE", "_internal/torch"])

    def test_comparaison_insensible_a_la_casse(self):
        self.assertEqual(
            build_paths.lister_restes(["_internal/FFMPEG.exe"],
                                      ["_internal/ffmpeg.exe"]), [])

    def test_modele_herite_est_un_reste_comme_un_autre(self):
        livres = ["MeetNote.exe", "models/small/model.bin",
                  "_internal/faster_whisper_models/m.bin"]
        restes = build_paths.lister_restes(livres, ["MeetNote.exe"])
        self.assertEqual(restes, ["_internal", "models"])


class ProduireArchiveTest(unittest.TestCase):
    """Archive produite depuis une fausse sortie fraîche, jamais par PyInstaller."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dist = os.path.join(self._tmp.name, "dist")
        self.frais = os.path.join(self._tmp.name, "build", "MeetNote")
        self.livre = os.path.join(self.dist, "MeetNote-9.9.9")
        for racine in (self.frais, self.livre):
            self._ecrire(racine, "MeetNote.exe", b"exe")
            self._ecrire(racine, os.path.join("_internal", "ffmpeg.exe"), b"ff")
            self._ecrire(racine, os.path.join("_internal", "ctranslate2", "models",
                                              "x.bin"), b"ok")
        self.messages = []
        self.alertes = []

    @staticmethod
    def _ecrire(racine, relatif, contenu=b"x"):
        chemin = os.path.join(racine, relatif)
        os.makedirs(os.path.dirname(chemin), exist_ok=True)
        Path(chemin).write_bytes(contenu)

    def _produire(self, **options):
        return build_paths.produire_archive(
            self.frais, "9.9.9", self.dist, journal=self.messages.append,
            alertes=self.alertes, **options)

    def _noms(self, chemin):
        with zipfile.ZipFile(chemin) as z:
            return {n.replace("\\", "/") for n in z.namelist()}

    def test_archive_dans_le_dossier_de_sortie_sous_la_racine_versionnee(self):
        chemin = self._produire()
        self.assertEqual(chemin, os.path.join(self.dist, "MeetNote-9.9.9.zip"))
        self.assertEqual(self._noms(chemin), {
            "MeetNote-9.9.9/MeetNote.exe",
            "MeetNote-9.9.9/_internal/ffmpeg.exe",
            "MeetNote-9.9.9/_internal/ctranslate2/models/x.bin",
        })

    def test_archive_slim_nommee_comme_son_dossier(self):
        chemin = self._produire(slim=True)
        self.assertEqual(os.path.basename(chemin), "MeetNote-slim-9.9.9.zip")
        self.assertTrue(all(n.startswith("MeetNote-slim-9.9.9/")
                            for n in self._noms(chemin)))

    def test_poids_affiche(self):
        self._produire()
        self.assertTrue(any("Archive prête" in m and "Mo)" in m
                            for m in self.messages))

    def test_archive_existante_remplacee(self):
        ancienne = os.path.join(self.dist, "MeetNote-9.9.9.zip")
        Path(ancienne).write_bytes(b"ancien contenu non zip")
        chemin = self._produire()
        self.assertTrue(zipfile.is_zipfile(chemin))

    def test_restes_d_anciens_builds_absents_du_zip_presents_sur_le_disque(self):
        self._ecrire(self.livre, os.path.join("_internal", "torch", "x.dll"))
        self._ecrire(self.livre, os.path.join("_internal", "ffprobe.EXE"))
        self._ecrire(self.livre, os.path.join("models", "small", "model.bin"))
        chemin = self._produire(dossier_livre=self.livre)
        noms = self._noms(chemin)
        self.assertFalse(any("torch" in n or "ffprobe" in n or "/models/small" in n
                             for n in noms))
        # Le models d'un paquet, lui, est dans la sortie fraîche : il reste.
        self.assertIn("MeetNote-9.9.9/_internal/ctranslate2/models/x.bin", noms)
        # Rien n'est supprimé du dossier livré.
        for relatif in (os.path.join("_internal", "torch", "x.dll"),
                        os.path.join("_internal", "ffprobe.EXE"),
                        os.path.join("models", "small", "model.bin")):
            self.assertTrue(os.path.isfile(os.path.join(self.livre, relatif)))
        # Les restes sont nommés dans l'avertissement, repris dans le bilan.
        restes = [a for a in self.alertes if "reste(s)" in a]
        self.assertEqual(len(restes), 1)
        for nom in ("_internal/torch", "_internal/ffprobe.EXE", "models"):
            self.assertIn(nom, restes[0])
        self.assertTrue(any("ATTENTION" in m and "reste(s)" in m
                            for m in self.messages))

    def test_dossier_livre_propre_sans_avertissement(self):
        self._produire(dossier_livre=self.livre)
        self.assertFalse(any("reste(s)" in a for a in self.alertes))

    def test_dossier_livre_absent_ignore(self):
        self._produire(dossier_livre=os.path.join(self.dist, "absent"))
        self.assertEqual(self.alertes, [])

    def test_restes_nombreux_resumes(self):
        for i in range(build_paths.MAX_RESTES_NOMMES + 2):
            self._ecrire(self.livre, f"reste{i:02d}.bin")
        self._produire(dossier_livre=self.livre)
        message = next(a for a in self.alertes if "reste(s)" in a)
        self.assertIn("…", message)
        self.assertNotIn(f"reste{build_paths.MAX_RESTES_NOMMES + 1:02d}.bin", message)

    def test_avertissement_si_archive_trop_lourde(self):
        with mock.patch.object(build_paths, "POIDS_MAX_ARCHIVE_MO", 0):
            self._produire()
        self.assertTrue(any("ATTENTION" in m and "au-delà" in m
                            for m in self.messages))
        self.assertTrue(any("au-delà" in a for a in self.alertes))

    def test_pas_d_avertissement_si_archive_legere(self):
        self._produire()
        self.assertFalse(any("ATTENTION" in m for m in self.messages))
        self.assertEqual(self.alertes, [])


class ScriptsDeBuildSansModeleTest(unittest.TestCase):
    """Les deux scripts de build n'embarquent ni ne copient plus de modèle."""

    SCRIPTS = ("build_spec.py", "build_spec_slim.py")
    DOSSIER = Path(__file__).resolve().parent

    def _source(self, nom):
        return (self.DOSSIER / nom).read_text(encoding="utf-8")

    def _hidden_imports(self, nom):
        for noeud in ast.walk(ast.parse(self._source(nom))):
            if not isinstance(noeud, ast.Assign):
                continue
            if any(getattr(c, "id", "") == "hidden_imports" for c in noeud.targets):
                return ast.literal_eval(noeud.value)
        self.fail(f"hidden_imports introuvable dans {nom}")

    def test_archive_produite_par_la_fonction_commune(self):
        for nom in self.SCRIPTS:
            with self.subTest(script=nom):
                self.assertIn("produire_archive(", self._source(nom))
                self.assertNotIn("make_archive", self._source(nom))

    def test_archive_faite_depuis_la_sortie_fraiche_et_bilan_final(self):
        for nom in self.SCRIPTS:
            source = self._source(nom)
            with self.subTest(script=nom):
                self.assertIn("composer_bilan(ALERTES)", source)
                self.assertIn("alertes=ALERTES", source)
        complet = self._source("build_spec.py")
        self.assertIn("produire_archive(sortie_fraiche", complet)
        # La signature se fait dans la sortie fraîche, avant la copie livrée.
        self.assertLess(complet.index("signer_fichier(os.path.join(sortie_fraiche"),
                        complet.index("copytree(sortie_fraiche"))

    def test_installeur_produit_apres_le_zip_dans_les_deux_scripts(self):
        for nom in self.SCRIPTS:
            source = self._source(nom)
            with self.subTest(script=nom):
                self.assertIn("produire_installeur(", source)
                self.assertLess(source.index("produire_archive("),
                                source.index("produire_installeur("))
                self.assertLess(source.index("produire_installeur("),
                                source.index("composer_bilan(ALERTES)"))
        appel_slim = self._source("build_spec_slim.py").split("produire_installeur(")[1]
        self.assertIn("slim=True", appel_slim)

    def test_aucune_copie_de_modele(self):
        for nom in self.SCRIPTS:
            source = self._source(nom)
            for interdit in ("faster_whisper_models", "EXTRA_MODELS",
                             "find_model_snapshot", "MODEL_NAMES", "HF_CACHE",
                             "snapshots"):
                with self.subTest(script=nom, interdit=interdit):
                    self.assertNotIn(interdit, source)

    def test_ffprobe_non_embarque_par_les_scripts(self):
        for nom in self.SCRIPTS:
            with self.subTest(script=nom):
                self.assertNotIn("ffprobe", self._source(nom).lower())

    def test_huggingface_hub_reste_embarque_pour_le_telechargement(self):
        for nom in self.SCRIPTS:
            with self.subTest(script=nom):
                self.assertIn("huggingface_hub", self._hidden_imports(nom))

    def test_modeles_embarque_comme_module(self):
        for nom in self.SCRIPTS:
            with self.subTest(script=nom):
                self.assertIn("modeles", self._hidden_imports(nom))

    def test_debruitage_embarque_comme_module(self):
        for nom in self.SCRIPTS:
            with self.subTest(script=nom):
                self.assertIn("debruitage", self._hidden_imports(nom))

    def test_watchdog_embarque_comme_module(self):
        # L'EXE se supervise lui-même : sans ce module dans le bundle, il ne
        # pourrait ni se relancer après un plantage ni remplacer une instance.
        for nom in self.SCRIPTS:
            with self.subTest(script=nom):
                self.assertIn("watchdog", self._hidden_imports(nom))

    def test_demarrage_windows_embarque_comme_module(self):
        # Le tray importe ce module au démarrage : absent du bundle, pas d'EXE.
        for nom in self.SCRIPTS:
            with self.subTest(script=nom):
                self.assertIn("demarrage_windows", self._hidden_imports(nom))

    def _liste_litterale(self, nom, variable):
        for noeud in ast.walk(ast.parse(self._source(nom))):
            if not isinstance(noeud, ast.Assign):
                continue
            if any(getattr(c, "id", "") == variable for c in noeud.targets):
                return ast.literal_eval(noeud.value)
        self.fail(f"{variable} introuvable dans {nom}")

    def test_torch_numba_llvmlite_exclus_du_paquet(self):
        for nom in self.SCRIPTS:
            exclusions = self._liste_litterale(nom, "excludes")
            for module in ("torch", "numba", "llvmlite"):
                with self.subTest(script=nom, module=module):
                    self.assertIn(module, exclusions)

    def test_excludes_est_bien_injecte_dans_le_spec(self):
        for nom in self.SCRIPTS:
            with self.subTest(script=nom):
                self.assertIn("excludes=[{excludes_str}]", self._source(nom))

    def test_aucun_module_exclu_n_est_demande_en_hidden_import(self):
        for nom in self.SCRIPTS:
            communs = set(self._liste_litterale(nom, "excludes")) & set(
                self._hidden_imports(nom))
            with self.subTest(script=nom):
                self.assertEqual(communs, set())


ENV_POSTE = {
    "LOCALAPPDATA": r"U:\Profil\AppData\Local",
    "ProgramFiles(x86)": r"P:\Programmes86",
    "ProgramFiles": r"P:\Programmes",
}
ISCC_UTILISATEUR = os.path.join(ENV_POSTE["LOCALAPPDATA"], "Programs",
                                "Inno Setup 6", "ISCC.exe")
ISCC_MACHINE_X86 = os.path.join(ENV_POSTE["ProgramFiles(x86)"],
                                "Inno Setup 6", "ISCC.exe")
ISCC_MACHINE = os.path.join(ENV_POSTE["ProgramFiles"], "Inno Setup 6", "ISCC.exe")
ISCC_VOULU = r"Z:\outils\ISCC.exe"


def _existent(*chemins):
    return lambda chemin: chemin in chemins


class TrouverIsccTest(unittest.TestCase):
    def test_emplacements_dans_l_ordre_utilisateur_puis_machine(self):
        self.assertEqual(build_paths.emplacements_iscc(ENV_POSTE),
                         [ISCC_UTILISATEUR, ISCC_MACHINE_X86, ISCC_MACHINE])

    def test_variables_absentes_ne_donnent_aucun_emplacement(self):
        self.assertEqual(build_paths.emplacements_iscc({}), [])

    def test_variable_prime_si_le_fichier_existe(self):
        env = {**ENV_POSTE, build_paths.ENV_ISCC: ISCC_VOULU}
        trouve = build_paths.trouver_iscc(
            env, _existent(ISCC_VOULU, ISCC_UTILISATEUR))
        self.assertEqual(trouve, ISCC_VOULU)

    def test_installation_par_utilisateur_avant_par_machine(self):
        trouve = build_paths.trouver_iscc(
            ENV_POSTE, _existent(ISCC_MACHINE, ISCC_UTILISATEUR))
        self.assertEqual(trouve, ISCC_UTILISATEUR)

    def test_repli_sur_la_machine(self):
        trouve = build_paths.trouver_iscc(ENV_POSTE, _existent(ISCC_MACHINE))
        self.assertEqual(trouve, ISCC_MACHINE)

    def test_variable_invalide_signalee_puis_repli(self):
        messages = []
        env = {**ENV_POSTE, build_paths.ENV_ISCC: ISCC_VOULU}
        trouve = build_paths.trouver_iscc(env, _existent(ISCC_MACHINE_X86),
                                          messages.append)
        self.assertEqual(trouve, ISCC_MACHINE_X86)
        self.assertEqual(len(messages), 1)
        self.assertIn(ISCC_VOULU, messages[0])

    def test_introuvable(self):
        self.assertIsNone(build_paths.trouver_iscc(ENV_POSTE, _existent()))


class CommandeIsccTest(unittest.TestCase):
    def test_macros_sortie_et_nom_de_l_installeur(self):
        commande = build_paths.commande_iscc(
            "ISCC.exe", "meetnote.iss", "3.4.0", r"C:\tmp\sortie", r"C:\dist")
        self.assertEqual(commande, [
            "ISCC.exe", "/DAppVersion=3.4.0", r"/DDossierSource=C:\tmp\sortie",
            r"/OC:\dist", "/FMeetNote-3.4.0-installation", "meetnote.iss"])

    def test_variante_slim_ne_reprend_pas_le_nom_du_paquet_complet(self):
        commande = build_paths.commande_iscc(
            "ISCC.exe", "m.iss", "3.4.0", "src", "dist", slim=True)
        self.assertIn("/FMeetNote-slim-3.4.0-installation", commande)

    def test_nom_de_l_installeur(self):
        self.assertEqual(build_paths.nom_installeur(" 3.4.0 "),
                         "MeetNote-3.4.0-installation.exe")

    def test_le_nom_produit_est_celui_que_declare_le_script_iss(self):
        iss = Path(build_paths.SCRIPT_ISS).read_text(encoding="utf-8-sig")
        self.assertIn("OutputBaseFilename=MeetNote-{#AppVersion}-installation", iss)
        self.assertEqual(build_paths.nom_installeur("{#AppVersion}"),
                         "MeetNote-{#AppVersion}-installation.exe")


class ProduireInstalleurTest(unittest.TestCase):
    ISS = r"S:\installer\meetnote.iss"
    SORTIE = r"S:\dist"
    INSTALLEUR = os.path.join(SORTIE, "MeetNote-3.4.0-installation.exe")
    REUSSI = mock.Mock(returncode=0, stdout="", stderr="")

    def _produire(self, resultat=None, existants=(), environ=None, executer=None,
                  **kwargs):
        alertes, journal, signes = [], [], []
        executer = executer or mock.Mock(return_value=resultat)
        chemin = build_paths.produire_installeur(
            r"S:\sortie", "3.4.0", self.SORTIE, journal=journal.append,
            alertes=alertes, environ=ENV_POSTE if environ is None else environ,
            iss=self.ISS, executer=executer, existe=_existent(*existants),
            signer=lambda c, journal=None: signes.append(c), **kwargs)
        return chemin, alertes, executer, signes

    def test_compile_puis_signe(self):
        chemin, alertes, executer, signes = self._produire(
            self.REUSSI, existants=(self.ISS, ISCC_UTILISATEUR, self.INSTALLEUR))
        self.assertEqual(chemin, self.INSTALLEUR)
        self.assertEqual(alertes, [])
        self.assertEqual(signes, [self.INSTALLEUR])
        self.assertEqual(executer.call_args.args[0], build_paths.commande_iscc(
            ISCC_UTILISATEUR, self.ISS, "3.4.0", r"S:\sortie", self.SORTIE))

    def test_sans_inno_setup_alerte_claire_sans_echec(self):
        chemin, alertes, executer, signes = self._produire(existants=(self.ISS,))
        self.assertIsNone(chemin)
        self.assertEqual(len(alertes), 1)
        self.assertIn("Inno Setup 6 introuvable", alertes[0])
        self.assertIn("zip portable", alertes[0])
        executer.assert_not_called()
        self.assertEqual(signes, [])

    def test_variable_invalide_et_rien_ailleurs_est_dit_dans_le_bilan(self):
        env = {**ENV_POSTE, build_paths.ENV_ISCC: ISCC_VOULU}
        _chemin, alertes, _executer, _signes = self._produire(
            existants=(self.ISS,), environ=env)
        self.assertEqual(len(alertes), 2)
        self.assertIn(ISCC_VOULU, alertes[0])

    def test_compilation_en_echec_devient_une_alerte(self):
        chemin, alertes, _executer, signes = self._produire(
            mock.Mock(returncode=2, stdout="", stderr="Erreur ligne 12"),
            existants=(self.ISS, ISCC_UTILISATEUR))
        self.assertIsNone(chemin)
        self.assertEqual(len(alertes), 1)
        self.assertIn("code 2", alertes[0])
        self.assertIn("Erreur ligne 12", alertes[0])
        self.assertEqual(signes, [])

    def test_succes_sans_fichier_produit_est_une_alerte(self):
        chemin, alertes, _executer, signes = self._produire(
            self.REUSSI, existants=(self.ISS, ISCC_UTILISATEUR))
        self.assertIsNone(chemin)
        self.assertEqual(len(alertes), 1)
        self.assertEqual(signes, [])

    def test_iscc_qui_ne_demarre_pas_est_une_alerte(self):
        chemin, alertes, _executer, _signes = self._produire(
            existants=(self.ISS, ISCC_UTILISATEUR),
            executer=mock.Mock(side_effect=OSError("accès refusé")))
        self.assertIsNone(chemin)
        self.assertIn("accès refusé", alertes[0])

    def test_script_iss_absent_est_une_alerte(self):
        chemin, alertes, executer, _signes = self._produire(
            existants=(ISCC_UTILISATEUR,))
        self.assertIsNone(chemin)
        self.assertIn(self.ISS, alertes[0])
        executer.assert_not_called()

    def test_variante_slim_nomme_son_installeur(self):
        slim = os.path.join(self.SORTIE, "MeetNote-slim-3.4.0-installation.exe")
        chemin, _alertes, _executer, signes = self._produire(
            self.REUSSI, existants=(self.ISS, ISCC_UTILISATEUR, slim), slim=True)
        self.assertEqual(chemin, slim)
        self.assertEqual(signes, [slim])


class SignerFichierTest(unittest.TestCase):
    def test_signature_reussie(self):
        executer = mock.Mock(return_value=mock.Mock(
            returncode=0, stdout="Signe: Valid", stderr=""))
        self.assertTrue(build_paths.signer_fichier(
            r"S:\a.exe", executer, lambda _m: None))
        self.assertIn(r"S:\a.exe", executer.call_args.args[0][-1])

    def test_certificat_absent_previent_sans_echouer(self):
        journal = []
        executer = mock.Mock(return_value=mock.Mock(
            returncode=1, stdout="", stderr="Certificat MeetNote introuvable"))
        self.assertFalse(build_paths.signer_fichier(
            r"S:\a.exe", executer, journal.append))
        self.assertIn("introuvable", journal[0])

    def test_powershell_indisponible_ne_fait_pas_echouer(self):
        executer = mock.Mock(side_effect=OSError("introuvable"))
        self.assertFalse(build_paths.signer_fichier(
            r"S:\a.exe", executer, lambda _m: None))

    def test_apostrophe_du_chemin_doublee(self):
        commande = build_paths.commande_signature(r"S:\l'an\a.exe")
        self.assertIn("l''an", commande[-1])


if __name__ == "__main__":
    unittest.main()
