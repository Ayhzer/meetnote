"""
Tests de la mesure audio de l'assistant (assistant_audio), sans aucun périphérique réel :
`sounddevice`, `soundcard` et `pyaudiowpatch` sont remplacés par des doublures.

Lancement : py -3.12 pc\\test_assistant_audio.py
"""
import os
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import assistant_audio as aa

TAUX = 16000


def _bloc(amplitude: float, n: int = TAUX // 10) -> np.ndarray:
    return np.full(n, amplitude, dtype=np.float32)


class TestNiveau(unittest.TestCase):
    def test_meme_formule_que_push_level(self):
        mono = np.full(1000, 0.1, dtype=np.float32)
        attendu = min(float(np.sqrt(np.mean(mono ** 2))) * 100 * 3, 100)
        self.assertAlmostEqual(aa.calculer_niveau(mono), attendu, places=4)

    def test_silence_et_bloc_vide_valent_zero(self):
        self.assertEqual(aa.calculer_niveau(np.zeros(100, dtype=np.float32)), 0.0)
        self.assertEqual(aa.calculer_niveau(np.zeros(0, dtype=np.float32)), 0.0)

    def test_plafonne_a_100(self):
        self.assertEqual(aa.calculer_niveau(np.ones(100, dtype=np.float32)), 100.0)

    def test_pic_retient_le_bloc_le_plus_fort(self):
        signal = np.concatenate([_bloc(0.0), _bloc(0.2), _bloc(0.0)])
        self.assertAlmostEqual(
            aa.niveau_pic(signal, TAUX), aa.calculer_niveau(_bloc(0.2)), places=4
        )

    def test_pic_d_un_signal_vide_vaut_zero(self):
        self.assertEqual(aa.niveau_pic(np.zeros(0, dtype=np.float32), TAUX), 0.0)

    def test_est_nul(self):
        self.assertTrue(aa.est_nul(None))
        self.assertTrue(aa.est_nul(0.0))
        self.assertTrue(aa.est_nul(aa.SEUIL_NIVEAU_NUL / 2))
        self.assertFalse(aa.est_nul(aa.SEUIL_NIVEAU_NUL))
        self.assertFalse(aa.est_nul(30.0))


class TestVerdict(unittest.TestCase):
    def test_tout_va_bien(self):
        v = aa.rendre_verdict(20.0, 15.0)
        self.assertTrue(v.accepte)
        self.assertIsNone(v.refus)
        self.assertEqual(v.avertissements, ())

    def test_micro_nul_refuse(self):
        v = aa.rendre_verdict(0.0, 15.0)
        self.assertFalse(v.accepte)
        self.assertEqual(v.refus, aa.MSG_MICRO_NUL)

    def test_micro_absent_refuse(self):
        v = aa.rendre_verdict(None, 15.0)
        self.assertFalse(v.accepte)
        self.assertEqual(v.refus, aa.MSG_MICRO_ABSENT)

    def test_son_pc_nul_avertit_sans_refuser(self):
        v = aa.rendre_verdict(20.0, 0.0)
        self.assertTrue(v.accepte)
        self.assertEqual(v.avertissements, (aa.MSG_PC_NUL,))

    def test_son_pc_absent_avertit_sans_refuser(self):
        v = aa.rendre_verdict(20.0, None)
        self.assertTrue(v.accepte)
        self.assertEqual(v.avertissements, (aa.MSG_PC_ABSENT,))

    def test_micro_nul_et_pc_nul_cumulent_refus_et_avertissement(self):
        v = aa.rendre_verdict(0.0, 0.0)
        self.assertFalse(v.accepte)
        self.assertEqual(v.avertissements, (aa.MSG_PC_NUL,))


class TestSignaux(unittest.TestCase):
    def test_ton_a_la_bonne_longueur_et_audible(self):
        ton = aa.generer_ton(TAUX, 0.5)
        self.assertEqual(len(ton), TAUX // 2)
        self.assertEqual(ton.dtype, np.float32)
        self.assertFalse(aa.est_nul(aa.niveau_pic(ton, TAUX)))
        self.assertLessEqual(float(np.max(np.abs(ton))), aa.AMPLITUDE_ESSAI + 1e-6)

    def test_ton_commence_et_finit_en_douceur(self):
        ton = aa.generer_ton(TAUX, 0.5)
        self.assertAlmostEqual(float(ton[0]), 0.0, places=6)
        self.assertAlmostEqual(float(ton[-1]), 0.0, places=6)

    def test_vers_mono_moyenne_les_canaux(self):
        stereo = np.array([[0.2, 0.4], [0.0, 1.0]], dtype=np.float32)
        np.testing.assert_allclose(aa.vers_mono(stereo), [0.3, 0.5], rtol=1e-6)
        mono = np.array([0.1, 0.2], dtype=np.float32)
        np.testing.assert_allclose(aa.vers_mono(mono), mono)

    def test_assembler_sans_bloc(self):
        self.assertEqual(aa.assembler([]).size, 0)
        self.assertEqual(aa.assembler([_bloc(0.1), _bloc(0.1)]).size, 2 * (TAUX // 10))


class TestDelai(unittest.TestCase):
    def test_fonction_qui_se_termine(self):
        self.assertTrue(aa._executer_avec_delai(lambda: None, 1.0))

    def test_fonction_bloquee_rend_la_main_au_delai(self):
        arret = threading.Event()
        debut = time.monotonic()
        try:
            termine = aa._executer_avec_delai(lambda: arret.wait(10), 0.2)
        finally:
            arret.set()
        self.assertFalse(termine)
        self.assertLess(time.monotonic() - debut, 2.0)

    def test_exception_devient_erreur_capture(self):
        def _echec():
            raise OSError("périphérique absent")

        with self.assertRaises(aa.ErreurCapture):
            aa._executer_avec_delai(_echec, 1.0)


class TestCaptureMicro(unittest.TestCase):
    def _faux_sd(self, amplitude: float) -> MagicMock:
        sd = MagicMock()
        sd.default.device = (0, 1)
        sd.query_devices.return_value = {"default_samplerate": TAUX}
        flux = MagicMock()
        flux.read.side_effect = lambda n: (np.full((n, 1), amplitude, np.float32), False)
        sd.InputStream.return_value.__enter__.return_value = flux
        return sd

    def test_micro_qui_capte(self):
        with patch.object(aa, "sd", self._faux_sd(0.1)):
            niveau = aa.mesurer_micro(0.3)
        self.assertFalse(aa.est_nul(niveau))

    def test_micro_muet_donne_niveau_nul(self):
        with patch.object(aa, "sd", self._faux_sd(0.0)):
            niveau = aa.mesurer_micro(0.3)
        self.assertTrue(aa.est_nul(niveau))

    def test_micro_inaccessible_leve_erreur_capture(self):
        faux = self._faux_sd(0.1)
        faux.InputStream.side_effect = OSError("accès refusé")
        with patch.object(aa, "sd", faux), self.assertRaises(aa.ErreurCapture):
            aa.mesurer_micro(0.3)

    def test_micro_qui_bloque_ne_bloque_pas_la_mesure(self):
        arret = threading.Event()
        faux = self._faux_sd(0.1)
        faux.InputStream.return_value.__enter__.return_value.read.side_effect = (
            lambda n: arret.wait(10)
        )
        debut = time.monotonic()
        try:
            with (
                patch.object(aa, "sd", faux),
                patch.object(aa, "MARGE_DELAI_S", 0.2),
                self.assertRaises(aa.ErreurCapture),
            ):
                aa.mesurer_micro(0.1)
        finally:
            arret.set()
        self.assertLess(time.monotonic() - debut, 3.0)


class TestCaptureSonPc(unittest.TestCase):
    def _faux_pawp(self, amplitude: float, canaux: int = 2) -> MagicMock:
        """Doublure de pyaudiowpatch : le callback reçoit des blocs d'amplitude donnée."""
        pawp = MagicMock()
        pawp.paFloat32 = 1
        pawp.paContinue = 0
        pa = pawp.PyAudio.return_value
        pa.get_default_wasapi_loopback.return_value = {
            "defaultSampleRate": TAUX, "maxInputChannels": canaux, "index": 7,
        }

        def _ouvrir(**kwargs):
            rappel = kwargs["stream_callback"]
            flux = MagicMock()

            def _demarrer():
                brut = np.full((TAUX // 10) * canaux, amplitude, np.float32).tobytes()
                for _ in range(3):
                    rappel(brut, TAUX // 10, None, 0)

            flux.start_stream.side_effect = _demarrer
            return flux

        pa.open.side_effect = _ouvrir
        return pawp

    def _mesurer(self, pawp, sd=None, sc=None):
        modules = {"pyaudiowpatch": pawp}
        with (
            patch.dict(sys.modules, modules),
            patch.object(aa, "sd", sd or MagicMock()),
            patch.object(aa, "sc", sc or MagicMock()),
        ):
            return aa.mesurer_son_pc(0.1)

    def test_loopback_avec_son(self):
        self.assertFalse(aa.est_nul(self._mesurer(self._faux_pawp(0.1))))

    def test_loopback_silencieux_donne_niveau_nul(self):
        self.assertTrue(aa.est_nul(self._mesurer(self._faux_pawp(0.0))))

    def test_l_assistant_joue_un_son_pendant_la_mesure(self):
        sd = MagicMock()
        self._mesurer(self._faux_pawp(0.1), sd=sd)
        sd.play.assert_called_once()
        sd.stop.assert_called()

    def test_son_d_essai_en_echec_ne_fait_pas_echouer_la_mesure(self):
        sd = MagicMock()
        sd.PortAudioError = OSError
        sd.play.side_effect = OSError("pas de sortie")
        niveau = self._mesurer(self._faux_pawp(0.1), sd=sd)
        self.assertFalse(aa.est_nul(niveau))

    def test_repli_soundcard_si_pyaudiowpatch_absent(self):
        sc = MagicMock()
        enregistreur = sc.get_microphone.return_value.recorder.return_value
        enregistreur.__enter__.return_value.record.side_effect = (
            lambda numframes: np.full((numframes, 1), 0.1, np.float32)
        )
        # Une valeur None dans sys.modules fait lever ImportError à l'import.
        niveau = self._mesurer(None, sc=sc)
        sc.get_microphone.assert_called_once()
        self.assertFalse(aa.est_nul(niveau))

    def test_aucun_loopback_leve_erreur_capture(self):
        sc = MagicMock()
        sc.default_speaker.side_effect = RuntimeError("pas de haut-parleur")
        with self.assertRaises(aa.ErreurCapture):
            self._mesurer(None, sc=sc)


class TestMesurerAudio(unittest.TestCase):
    def test_micro_nul_refuse_et_pc_nul_avertit(self):
        with (
            patch.object(aa, "mesurer_micro", return_value=0.0),
            patch.object(aa, "mesurer_son_pc", return_value=0.0),
        ):
            v = aa.mesurer_audio(0.1)
        self.assertFalse(v.accepte)
        self.assertEqual(v.avertissements, (aa.MSG_PC_NUL,))

    def test_les_erreurs_de_capture_ne_remontent_pas(self):
        with (
            patch.object(aa, "mesurer_micro", side_effect=aa.ErreurCapture("x")),
            patch.object(aa, "mesurer_son_pc", side_effect=aa.ErreurCapture("y")),
        ):
            v = aa.mesurer_audio(0.1)
        self.assertEqual(v.refus, aa.MSG_MICRO_ABSENT)
        self.assertEqual(v.avertissements, (aa.MSG_PC_ABSENT,))

    def test_micro_ok_pc_ok(self):
        with (
            patch.object(aa, "mesurer_micro", return_value=25.0),
            patch.object(aa, "mesurer_son_pc", return_value=18.0),
        ):
            v = aa.mesurer_audio(0.1)
        self.assertTrue(v.accepte)
        self.assertEqual(v.avertissements, ())

    def test_ordre_d_import_sounddevice_avant_soundcard(self):
        source = Path(aa.__file__).read_text(encoding="utf-8")
        self.assertLess(
            source.index("import sounddevice as sd"), source.index("import soundcard as sc")
        )


if __name__ == "__main__":
    unittest.main()
