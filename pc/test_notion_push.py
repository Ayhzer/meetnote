"""Tests de la lecture de durée (sans ffprobe) et de la découpe audio de notion_push."""

import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))
try:
    import config  # noqa: F401  (gitignoré : absent sur un poste neuf)
except ImportError:
    _faux = types.ModuleType("config")
    _faux.NOTION_TOKEN = ""
    _faux.NOTION_DATABASE_ID = ""
    sys.modules["config"] = _faux

import notion_push as np_

SORTIE_FFMPEG = (
    "Input #0, ogg, from 'a.opus':\n"
    "  Duration: 00:47:00.00, start: 0.000000, bitrate: 24 kb/s\n"
    "  Stream #0:0: Audio: opus, 48000 Hz, mono, fltp\n"
)


def _resultat(stderr="", returncode=0):
    r = mock.MagicMock()
    r.returncode = returncode
    r.stderr = stderr.encode()
    r.stdout = b""
    return r


class DureeDepuisSortieFfmpegTest(unittest.TestCase):
    def test_format_nominal(self):
        self.assertAlmostEqual(
            np_.duree_depuis_sortie_ffmpeg("Duration: 01:02:03.45, start: 0.0"), 3723.45)

    def test_sortie_complete_plusieurs_flux(self):
        self.assertEqual(np_.duree_depuis_sortie_ffmpeg(SORTIE_FFMPEG), 2820.0)

    def test_heures_superieures_a_24(self):
        self.assertEqual(np_.duree_depuis_sortie_ffmpeg("Duration: 30:00:00.00,"), 108000.0)

    def test_centiemes_absents(self):
        self.assertEqual(np_.duree_depuis_sortie_ffmpeg("Duration: 00:10:05, start"), 605.0)

    def test_duree_non_disponible(self):
        self.assertIsNone(np_.duree_depuis_sortie_ffmpeg("Duration: N/A, bitrate: N/A"))

    def test_texte_vide_ou_sans_duree(self):
        self.assertIsNone(np_.duree_depuis_sortie_ffmpeg(""))
        self.assertIsNone(np_.duree_depuis_sortie_ffmpeg(None))
        self.assertIsNone(np_.duree_depuis_sortie_ffmpeg("rien ici"))


class LireDureeTest(unittest.TestCase):
    def test_commande_et_delai_et_code_non_nul_ignore(self):
        with mock.patch("subprocess.run", return_value=_resultat(SORTIE_FFMPEG, 1)) as run:
            self.assertEqual(np_._lire_duree_s("ffmpeg", "a.opus"), 2820.0)
        args, kwargs = run.call_args
        self.assertEqual(args[0], ["ffmpeg", "-hide_banner", "-i", "a.opus"])
        self.assertEqual(kwargs["timeout"], np_.DELAI_LECTURE_DUREE_S)

    def test_echec_du_lancement_rend_none(self):
        for erreur in (OSError("introuvable"),
                       np_.subprocess.TimeoutExpired("ffmpeg", 15)):
            with self.subTest(erreur=type(erreur).__name__),                     mock.patch("subprocess.run", side_effect=erreur):
                self.assertIsNone(np_._lire_duree_s("ffmpeg", "a.opus"))


class DecoupeAudioTest(unittest.TestCase):
    """Les segments sont ceux de l'ancien code (commande capturée avant T030)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.opus = os.path.join(self._tmp.name, "meetnote_20261025_100000.opus")

    def _ecrire(self, octets):
        Path(self.opus).write_bytes(b"x" * octets)

    def _decouper(self, sorties, segment_min=10, parts=()):
        appels = []

        def faux_run(cmd, **kwargs):
            appels.append((cmd, kwargs.get("timeout")))
            return sorties(cmd)

        with mock.patch.object(np_, "_find_ffmpeg", return_value="ffmpeg"), \
                mock.patch("subprocess.run", faux_run), \
                mock.patch("glob.glob", return_value=list(parts)):
            resultat = np_._split_audio_for_upload(self.opus, segment_min)
        return resultat, appels

    def test_47_min_en_segments_de_10(self):
        self._ecrire(1024)
        base = os.path.splitext(self.opus)[0]
        parts = [f"{base}_part{i:03d}.opus" for i in range(5)]
        resultat, appels = self._decouper(
            lambda cmd: _resultat(SORTIE_FFMPEG, 1 if "-hide_banner" in cmd else 0),
            parts=parts)
        self.assertEqual(resultat, parts)
        self.assertEqual(len(appels), 2)
        self.assertEqual(appels[0][0], ["ffmpeg", "-hide_banner", "-i", self.opus])
        self.assertEqual(appels[1], (
            ["ffmpeg", "-y", "-i", self.opus, "-f", "segment", "-segment_time", "600",
             "-c", "copy", f"{base}_part%03d.opus"], 600))

    def test_fichier_court_non_decoupe(self):
        self._ecrire(1024)
        court = "Duration: 00:09:59.00, start: 0"
        resultat, appels = self._decouper(lambda cmd: _resultat(court, 1))
        self.assertEqual(resultat, [self.opus])
        self.assertEqual(len(appels), 1)

    def test_echec_de_la_decoupe_rend_le_fichier_entier(self):
        self._ecrire(1024)

        def sorties(cmd):
            return _resultat(SORTIE_FFMPEG, 1) if "-hide_banner" in cmd else _resultat("", 1)

        resultat, _ = self._decouper(sorties)
        self.assertEqual(resultat, [self.opus])

    def test_duree_inconnue_petit_fichier_non_decoupe(self):
        self._ecrire(1024)
        resultat, appels = self._decouper(lambda cmd: _resultat("Duration: N/A", 1))
        self.assertEqual(resultat, [self.opus])
        self.assertEqual(len(appels), 1)

    def test_duree_inconnue_gros_fichier_estime_par_la_taille(self):
        self._ecrire(4 * 1024 * 1024)  # 4 Mo > 10 * 0.3 : estimé à ~13,3 h
        base = os.path.splitext(self.opus)[0]
        resultat, appels = self._decouper(
            lambda cmd: _resultat("", 1 if "-hide_banner" in cmd else 0),
            parts=[f"{base}_part000.opus"])
        self.assertEqual(resultat, [f"{base}_part000.opus"])
        self.assertEqual(appels[1][0][:3], ["ffmpeg", "-y", "-i"])

    def test_ffmpeg_absent(self):
        self._ecrire(1024)
        with mock.patch.object(np_, "_find_ffmpeg", return_value=None), \
                mock.patch("subprocess.run") as run:
            self.assertEqual(np_._split_audio_for_upload(self.opus), [self.opus])
        run.assert_not_called()


class AucunFfprobeTest(unittest.TestCase):
    def test_plus_aucune_reference_a_ffprobe(self):
        source = (Path(__file__).resolve().parent / "notion_push.py").read_text(encoding="utf-8")
        self.assertNotIn("ffprobe", source.lower())
        self.assertFalse(hasattr(np_, "_find_ffprobe"))


if __name__ == "__main__":
    unittest.main()
