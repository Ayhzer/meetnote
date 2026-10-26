"""
Tests du débruitage (debruitage) : il doit fonctionner sans `torch`, et se désactiver
proprement, avec un message en clair une seule fois, si `noisereduce` manque ou échoue.

Lancement : py -3.12 pc\\test_debruitage.py
"""
import importlib
import os
import sys
import unittest
from unittest import mock

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import debruitage

# scipy lit sys.modules['torch'] à l'import : on le charge avant tout blocage de torch.
for _nom in ("scipy.signal", "scipy.stats"):
    importlib.import_module(_nom)

FREQ_ECH = 16000
FREQ_SINUS = 440
LARGEUR_SINUS_HZ = 50       # bande autour du sinus, exclue de la mesure du bruit
AMPLITUDE_SINUS = 0.3
ECART_TYPE_BRUIT = 0.05
SECONDES = 2


def _signal():
    """Sinus + bruit blanc (graine fixe), et un bloc de bruit seul (calibration)."""
    rng = np.random.default_rng(12345)
    t = np.arange(FREQ_ECH * SECONDES) / FREQ_ECH
    sinus = (AMPLITUDE_SINUS * np.sin(2 * np.pi * FREQ_SINUS * t)).astype(np.float32)
    bruit = (ECART_TYPE_BRUIT * rng.standard_normal(len(t))).astype(np.float32)
    calibration = (ECART_TYPE_BRUIT * rng.standard_normal(FREQ_ECH)).astype(np.float32)
    return sinus, bruit, calibration


def _puissance_hors_sinus(x):
    """Puissance du signal en dehors de la bande du sinus (donc : le bruit restant)."""
    spectre = np.fft.rfft(x)
    freqs = np.fft.rfftfreq(len(x), 1 / FREQ_ECH)
    spectre[np.abs(freqs - FREQ_SINUS) < LARGEUR_SINUS_HZ] = 0
    return float(np.sum(np.abs(spectre) ** 2)) / len(x) ** 2


def _sans_torch():
    """torch absent (None dans sys.modules) et noisereduce à réimporter depuis zéro.

    `mock.patch.dict` restaure sys.modules à la sortie, noisereduce compris.
    """
    ctx = mock.patch.dict(sys.modules, {"torch": None})
    ctx.start()
    for nom in [n for n in sys.modules if n.split(".")[0] == "noisereduce"]:
        del sys.modules[nom]
    return ctx


class TestDebruitageSansTorch(unittest.TestCase):

    def setUp(self):
        debruitage.reinitialiser()
        self.addCleanup(debruitage.reinitialiser)
        ctx = _sans_torch()
        self.addCleanup(ctx.stop)

    def test_noisereduce_s_importe_sans_torch(self):
        import noisereduce.noisereduce as nr_interne
        self.assertFalse(nr_interne.TORCH_AVAILABLE)

    def test_le_bruit_baisse_et_la_forme_est_conservee(self):
        sinus, bruit, calibration = _signal()
        bruite = sinus + bruit
        debruitage.debruiter(calibration, FREQ_ECH)

        sortie = debruitage.debruiter(bruite, FREQ_ECH)

        self.assertEqual(sortie.dtype, np.float32)
        self.assertEqual(sortie.shape, bruite.shape)
        rapport = _puissance_hors_sinus(sortie) / _puissance_hors_sinus(bruite)
        self.assertLess(rapport, 0.25)
        # Le sinus reste reconnaissable, et mieux que dans le signal bruité.
        corr_sortie = np.corrcoef(sortie, sinus)[0, 1]
        corr_entree = np.corrcoef(bruite, sinus)[0, 1]
        self.assertGreater(corr_sortie, 0.95)
        self.assertGreater(corr_sortie, corr_entree)

    def test_le_premier_bloc_sert_de_profil_de_bruit_pour_la_suite(self):
        sinus, bruit, calibration = _signal()
        debruitage.debruiter(calibration, FREQ_ECH)
        np.testing.assert_array_equal(debruitage._profil_bruit, calibration)
        debruitage.debruiter(sinus + bruit, FREQ_ECH)
        np.testing.assert_array_equal(debruitage._profil_bruit, calibration)


class TestReglageConserve(unittest.TestCase):
    """Même réglage qu'avant l'extraction : stationnaire, 75 %, profil du 1er bloc."""

    def setUp(self):
        debruitage.reinitialiser()
        self.addCleanup(debruitage.reinitialiser)

    def test_parametres_passes_a_reduce_noise(self):
        faux = mock.Mock()
        faux.reduce_noise.side_effect = lambda **kw: kw["y"] * 0.5
        bloc = np.ones(100, dtype=np.float64)
        with mock.patch.dict(sys.modules, {"noisereduce": faux}):
            sortie = debruitage.debruiter(bloc, FREQ_ECH)
        args = faux.reduce_noise.call_args.kwargs
        self.assertEqual(args["sr"], FREQ_ECH)
        self.assertEqual(args["prop_decrease"], 0.75)
        self.assertTrue(args["stationary"])
        np.testing.assert_array_equal(args["y_noise"], bloc)
        self.assertEqual(sortie.dtype, np.float32)


class TestDebruitageIndisponible(unittest.TestCase):

    def setUp(self):
        debruitage.reinitialiser()
        self.addCleanup(debruitage.reinitialiser)

    def test_noisereduce_absent_rend_le_signal_intact_et_le_dit_une_fois(self):
        bloc = np.linspace(-1, 1, 500, dtype=np.float32)
        with mock.patch.dict(sys.modules, {"noisereduce": None}),                 self.assertLogs("meetnote", level="WARNING") as journal:
            premier = debruitage.debruiter(bloc, FREQ_ECH)
            second = debruitage.debruiter(bloc, FREQ_ECH)
        self.assertIs(premier, bloc)
        self.assertIs(second, bloc)
        self.assertEqual(len(journal.records), 1)
        self.assertIn("Débruitage désactivé", journal.output[0])

    def test_echec_de_reduce_noise_rend_le_signal_intact_et_le_dit_une_fois(self):
        faux = mock.Mock()
        faux.reduce_noise.side_effect = ValueError("bloc trop court")
        bloc = np.zeros(10, dtype=np.float32)
        with mock.patch.dict(sys.modules, {"noisereduce": faux}),                 self.assertLogs("meetnote", level="WARNING") as journal:
            premier = debruitage.debruiter(bloc, FREQ_ECH)
            second = debruitage.debruiter(bloc, FREQ_ECH)
        self.assertIs(premier, bloc)
        self.assertIs(second, bloc)
        self.assertEqual(len(journal.records), 1)
        self.assertIn("bloc trop court", journal.output[0])
        self.assertEqual(faux.reduce_noise.call_count, 1)  # désactivé après l'échec


if __name__ == "__main__":
    unittest.main()
