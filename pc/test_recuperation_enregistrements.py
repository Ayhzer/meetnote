"""Tests de la détection des enregistrements interrompus (dossier temporaire)."""
import datetime
import os
import shutil
import tempfile
import unittest
import wave

import recuperation_enregistrements as recup

MAINTENANT = 1_800_000_000.0   # horloge injectée, en secondes epoch
DEBUT = datetime.datetime(2026, 10, 26, 14, 5, 9)  # noqa: DTZ001 — heure locale naïve
NOM = "rec_20261026_140509.wav"


class _Base(unittest.TestCase):
    def setUp(self):
        self.dossier = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dossier, ignore_errors=True)

    def ecrire_wav(self, nom=NOM, secondes=2, age_s=3600):
        chemin = os.path.join(self.dossier, nom)
        with wave.open(chemin, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(16000)
            wav.writeframes(b"\x01\x00" * 16000 * secondes)
        self.dater(chemin, age_s)
        return chemin

    def dater(self, chemin, age_s):
        instant = MAINTENANT - age_s
        os.utime(chemin, (instant, instant))

    def chercher(self, **kw):
        return recup.chercher(self.dossier, maintenant=lambda: MAINTENANT, **kw)


class TestExtraireDate(unittest.TestCase):
    def test_date_tiree_du_nom(self):
        self.assertEqual(recup.extraire_date(NOM), DEBUT)

    def test_noms_sans_date_valide(self):
        for nom in ("rec_abc.wav", "whisper_rec_20261026_140509.wav",
                    "rec_20269999_999999.wav", "rec_20261026_140509.opus"):
            with self.subTest(nom=nom):
                self.assertIsNone(recup.extraire_date(nom))

    def test_titre_au_format_francais(self):
        self.assertEqual(recup.titre_recupere(DEBUT),
                         "Enregistrement récupéré du 26/10/2026 14:05")


class TestLireDuree(_Base):
    def test_duree_depuis_l_entete(self):
        duree, raison = recup.lire_duree(self.ecrire_wav(secondes=3))
        self.assertEqual((duree, raison), (3.0, ""))

    def test_zero_frame(self):
        chemin = self.ecrire_wav(secondes=0)
        duree, raison = recup.lire_duree(chemin)
        self.assertIsNone(duree)
        self.assertIn("aucun échantillon", raison)

    def test_contenu_non_wav(self):
        chemin = os.path.join(self.dossier, NOM)
        with open(chemin, "wb") as f:
            f.write(b"x" * 500)
        duree, raison = recup.lire_duree(chemin)
        self.assertIsNone(duree)
        self.assertIn("illisible", raison)


class TestChercher(_Base):
    def test_orphelin_ancien_est_candidat(self):
        chemin = self.ecrire_wav(secondes=2)
        candidats, ecartes = self.chercher()
        self.assertEqual(ecartes, [])
        self.assertEqual([(c.chemin, c.debut, c.duree_s) for c in candidats],
                         [(chemin, DEBUT, 2.0)])

    def test_candidats_du_plus_ancien_au_plus_recent(self):
        self.ecrire_wav("rec_20261026_160000.wav")
        self.ecrire_wav("rec_20261025_090000.wav")
        candidats, _ = self.chercher()
        self.assertEqual([c.chemin[-19:] for c in candidats],
                         ["20261025_090000.wav", "20261026_160000.wav"])

    def test_fichier_reference_par_chemin_n_est_pas_candidat(self):
        chemin = self.ecrire_wav()
        # Casse et séparateurs différents : la comparaison est normalisée.
        reference = chemin.upper().replace("\\", "/")
        self.assertEqual(self.chercher(chemins_references=[reference]), ([], []))

    def test_idempotence_apres_mise_en_historique(self):
        chemin = self.ecrire_wav()
        premier, _ = self.chercher()
        self.assertEqual(len(premier), 1)
        # Au démarrage suivant le job est dans l'historique avec ce wav_path.
        self.assertEqual(self.chercher(chemins_references=[chemin]), ([], []))

    def test_job_dont_le_wav_pointe_vers_l_archive(self):
        # Arrêt « sans transcription » : le job référence l'archive, tmp reste.
        self.ecrire_wav()
        self.assertEqual(
            self.chercher(debuts_references=[DEBUT.replace(microsecond=5)]),
            ([], []))

    def test_archive_existante_pour_le_meme_horodatage(self):
        self.ecrire_wav()
        self.assertEqual(
            self.chercher(noms_archives=["meetnote_20261026_140509_Point.opus"]),
            ([], []))

    def test_autres_archives_ne_masquent_pas(self):
        self.ecrire_wav()
        candidats, _ = self.chercher(noms_archives=["meetnote_20261026_140510.opus"])
        self.assertEqual(len(candidats), 1)

    def test_transcription_existante_pour_le_meme_horodatage(self):
        # Archive supprimée et job sorti de l'historique : la transcription suffit.
        self.ecrire_wav()
        self.assertEqual(
            self.chercher(noms_transcripts=["transcript_20261026_140509.txt"]),
            ([], []))

    def test_reliquat_plus_ancien_que_la_borne_ignore_sans_bruit(self):
        chemin = self.ecrire_wav(age_s=recup.AGE_MAXIMUM_S + 1)
        vide = os.path.join(self.dossier, "rec_20260404_185706.wav")
        with open(vide, "wb") as f:
            f.write(b"\x00" * recup.TAILLE_ENTETE_WAV)
        self.dater(vide, recup.AGE_MAXIMUM_S + 1)
        self.assertEqual(self.chercher(), ([], []))
        self.assertTrue(os.path.exists(chemin))  # laissé en place

    def test_juste_sous_la_borne_reste_candidat(self):
        self.ecrire_wav(age_s=recup.AGE_MAXIMUM_S - 60)
        candidats, _ = self.chercher()
        self.assertEqual(len(candidats), 1)

    def test_intermediaires_whisper_et_autres_fichiers_ignores(self):
        self.ecrire_wav("whisper_rec_20261026_140509.wav")
        self.ecrire_wav("autre.wav")
        self.ecrire_wav("rec_20261026_140509.txt")
        self.assertEqual(self.chercher(), ([], []))

    def test_fichier_trop_recent_est_ecarte(self):
        chemin = self.ecrire_wav(age_s=3)
        candidats, ecartes = self.chercher()
        self.assertEqual(candidats, [])
        self.assertEqual([e.chemin for e in ecartes], [chemin])
        self.assertIn("moins de", ecartes[0].raison)

    def test_fichier_vide_ou_entete_seul_ecarte_et_conserve(self):
        vide = os.path.join(self.dossier, "rec_20261026_100000.wav")
        entete = os.path.join(self.dossier, "rec_20261026_110000.wav")
        open(vide, "wb").close()
        with open(entete, "wb") as f:
            f.write(b"\0" * recup.TAILLE_ENTETE_WAV)
        for chemin in (vide, entete):
            self.dater(chemin, 3600)
        candidats, ecartes = self.chercher()
        self.assertEqual(candidats, [])
        self.assertEqual({e.chemin for e in ecartes}, {vide, entete})
        self.assertTrue(all("vide" in e.raison for e in ecartes))
        self.assertTrue(os.path.isfile(vide) and os.path.isfile(entete))

    def test_fichier_illisible_ecarte_et_conserve(self):
        chemin = os.path.join(self.dossier, NOM)
        with open(chemin, "wb") as f:
            f.write(b"pas un wav" * 20)
        self.dater(chemin, 3600)
        candidats, ecartes = self.chercher()
        self.assertEqual(candidats, [])
        self.assertIn("illisible", ecartes[0].raison)
        self.assertTrue(os.path.isfile(chemin))

    def test_en_tete_a_zero_frame_ecarte(self):
        chemin = self.ecrire_wav(secondes=0)
        with open(chemin, "ab") as f:
            f.write(b"\0" * 100)  # > en-tête, 0 échantillon
        self.dater(chemin, 3600)
        candidats, ecartes = self.chercher()
        self.assertEqual(candidats, [])
        self.assertEqual(len(ecartes), 1)

    def test_nom_sans_date_ecarte(self):
        chemin = self.ecrire_wav("rec_hier.wav")
        candidats, ecartes = self.chercher()
        self.assertEqual(candidats, [])
        self.assertEqual([e.chemin for e in ecartes], [chemin])

    def test_aucune_suppression_ni_deplacement(self):
        self.ecrire_wav()
        self.ecrire_wav("rec_20261026_100000.wav", age_s=1)
        avant = sorted(os.listdir(self.dossier))
        self.chercher()
        self.assertEqual(sorted(os.listdir(self.dossier)), avant)

    def test_dossier_absent(self):
        resultat = recup.chercher(os.path.join(self.dossier, "inexistant"))
        self.assertEqual(resultat, ([], []))

    def test_listage_injecte(self):
        candidats, ecartes = recup.chercher(
            self.dossier, maintenant=lambda: MAINTENANT, lister=lambda _d: [])
        self.assertEqual((candidats, ecartes), ([], []))


if __name__ == "__main__":
    unittest.main()
