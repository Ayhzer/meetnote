"""Tests des délais et du nettoyage des conversions ffmpeg (dossier temporaire)."""
import os
import shutil
import tempfile
import unittest
import wave

import conversion_audio as conv

NEUF_HEURES_VINGT_ET_UNE_S = 9 * 3600 + 21 * 60


class _Base(unittest.TestCase):
    def setUp(self):
        self.dossier = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dossier, ignore_errors=True)

    def chemin(self, nom):
        return os.path.join(self.dossier, nom)


class TestDelai(unittest.TestCase):
    def test_court_garde_le_plancher(self):
        self.assertEqual(conv.delai_conversion_s(60), conv.DELAI_MINIMUM_S)
        self.assertEqual(conv.delai_conversion_s(0), conv.DELAI_MINIMUM_S)

    def test_long_suit_la_duree(self):
        # Le cas du 27/10/2026 : 300 s ne suffisaient pas pour 9 h 21 d'audio.
        delai = conv.delai_conversion_s(NEUF_HEURES_VINGT_ET_UNE_S)
        self.assertEqual(delai, NEUF_HEURES_VINGT_ET_UNE_S / conv.VITESSE_MINIMALE)
        self.assertGreater(delai, conv.DELAI_MINIMUM_S)


class TestDureeEstimee(_Base):
    def test_wav_lu_dans_l_entete(self):
        chemin = self.chemin("rec.wav")
        with wave.open(chemin, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(16000)
            wav.writeframes(b"\x00\x00" * 16000 * 3)
        self.assertEqual(conv.duree_estimee_s(chemin), 3.0)

    def test_compresse_estime_par_la_taille(self):
        chemin = self.chemin("archive.opus")
        with open(chemin, "wb") as f:
            f.write(b"\x00" * conv.DEBIT_MINIMAL_OCTETS_S * 120)
        self.assertEqual(conv.duree_estimee_s(chemin), 120.0)

    def test_fichier_absent(self):
        self.assertEqual(conv.duree_estimee_s(self.chemin("absent.wav")), 0.0)


class TestRetirerSortiePartielle(_Base):
    def test_sortie_partielle_retiree(self):
        chemin = self.chemin("partiel.opus")
        with open(chemin, "wb") as f:
            f.write(b"OggS")
        self.assertTrue(conv.retirer_sortie_partielle(chemin))
        self.assertFalse(os.path.exists(chemin))

    def test_rien_a_retirer(self):
        self.assertFalse(conv.retirer_sortie_partielle(self.chemin("absent.opus")))


if __name__ == "__main__":
    unittest.main()
