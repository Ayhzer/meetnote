"""Tests de notion_push : plus aucun envoi d'audio, pièces jointes conservées."""

import inspect
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

DOSSIER = Path(__file__).resolve().parent


def _reponse(corps):
    r = mock.MagicMock()
    r.ok = True
    r.json.return_value = corps
    return r


def _faux_post(appels):
    """Faux `requests.post` : journalise l'URL, répond selon le point d'accès."""
    def post(url, **kwargs):
        appels.append((url, kwargs))
        if url.endswith("/pages"):
            return _reponse({"id": "page-1"})
        return _reponse({"id": "fichier-1"})
    return post


class SansEnvoiAudioTest(unittest.TestCase):
    def test_plus_de_parametre_audio_path(self):
        self.assertNotIn("audio_path", inspect.signature(np_.push_to_notion).parameters)

    def test_job_audio_ne_produit_aucun_envoi_de_fichier(self):
        appels = []
        with mock.patch.object(np_.requests, "post", _faux_post(appels)):
            page = np_.push_to_notion("Bonjour", meeting_type="Audio", duration_min=12)
        self.assertEqual(page["id"], "page-1")
        urls = [u for u, _ in appels]
        self.assertEqual(len(urls), 1)
        self.assertTrue(urls[0].endswith("/pages"))
        self.assertFalse(any("file_uploads" in u for u in urls))
        props = appels[0][1]["json"]["properties"]
        self.assertNotIn("Files & media", props)
        # Les propriétés de la page restent celles d'avant.
        self.assertEqual(props["Type"], {"select": {"name": "Audio"}})
        self.assertEqual(props["Durée (min)"], {"number": 12.0})

    def test_plus_de_code_audio_dans_le_module(self):
        for nom in ("_compress_audio", "_split_audio_for_upload", "_lire_duree_s",
                    "duree_depuis_sortie_ffmpeg", "AUDIO_BITRATE"):
            self.assertFalse(hasattr(np_, nom), nom)
        source = (DOSSIER / "notion_push.py").read_text(encoding="utf-8")
        self.assertNotIn("ffprobe", source.lower())
        self.assertNotIn("NOTION_UPLOAD_SEGMENT_MIN", source)

    def test_ffmpeg_reste_disponible_pour_l_archivage(self):
        # meetnote-tray.py (_archive_audio) l'importe depuis notion_push.
        self.assertTrue(callable(np_._find_ffmpeg))

    def test_le_tray_n_envoie_plus_audio_path(self):
        source = (DOSSIER / "meetnote-tray.py").read_text(encoding="utf-8")
        self.assertNotIn("audio_path=job.wav_path", source)


class PiecesJointesTest(unittest.TestCase):
    def test_piece_jointe_envoyee_et_attachee(self):
        appels = []
        with tempfile.TemporaryDirectory() as tmp:
            fichier = os.path.join(tmp, "compte-rendu.pdf")
            Path(fichier).write_bytes(b"%PDF")
            with mock.patch.object(np_.requests, "post", _faux_post(appels)):
                np_.push_to_notion("Bonjour", meeting_type="Audio", attachments=[fichier])
        urls = [u for u, _ in appels]
        self.assertEqual(sum(u.endswith("/file_uploads") for u in urls), 1)
        self.assertEqual(sum("/send" in u for u in urls), 1)
        props = appels[-1][1]["json"]["properties"]
        fichiers = props["Files & media"]["files"]
        self.assertEqual(len(fichiers), 1)
        self.assertEqual(fichiers[0]["name"], "compte-rendu.pdf")
        self.assertEqual(fichiers[0]["file_upload"], {"id": "fichier-1"})


if __name__ == "__main__":
    unittest.main()
