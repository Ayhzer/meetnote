"""Tests du choix de chemin des transcriptions locales (jamais d'écrasement)."""
import datetime
import os
import tempfile
import unittest

import chemin_transcript
import recuperation_enregistrements

DEBUT = datetime.datetime(2026, 11, 10, 14, 0, 0)  # noqa: DTZ001 — heure locale naïve voulue


def _enregistrer(dossier, debut, texte, chemin_propre=""):
    """Reproduit ce que fait `_save_transcript_local` : choisir, puis écrire."""
    chemin = chemin_transcript.choisir_chemin_transcript(dossier, debut, chemin_propre)
    with open(chemin, "w", encoding="utf-8") as f:
        f.write(texte)
    return chemin


def _lire(chemin):
    with open(chemin, encoding="utf-8") as f:
        return f.read()


class ChoisirCheminTranscriptTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dossier = tmp.name

    def test_premier_job_garde_le_nom_de_base(self):
        chemin = _enregistrer(self.dossier, DEBUT, "A")
        self.assertEqual(os.path.basename(chemin), "transcript_20261110_140000.txt")

    def test_deux_jobs_meme_heure_donnent_deux_fichiers(self):
        premier = _enregistrer(self.dossier, DEBUT, "réunion A")
        second = _enregistrer(self.dossier, DEBUT, "réunion B")
        troisieme = _enregistrer(self.dossier, DEBUT, "réunion C")
        self.assertEqual(len({premier, second, troisieme}), 3)
        self.assertEqual(len(os.listdir(self.dossier)), 3)
        self.assertEqual(_lire(premier), "réunion A")
        self.assertEqual(_lire(second), "réunion B")
        self.assertEqual(_lire(troisieme), "réunion C")

    def test_job_reenregistre_reecrit_son_propre_fichier(self):
        premier = _enregistrer(self.dossier, DEBUT, "A")
        second = _enregistrer(self.dossier, DEBUT, "B")
        # Régénération de chacun : le fichier du job est réécrit, aucun n'est créé.
        _enregistrer(self.dossier, DEBUT, "A v2", chemin_propre=premier)
        _enregistrer(self.dossier, DEBUT, "B v2", chemin_propre=second)
        self.assertEqual(len(os.listdir(self.dossier)), 2)
        self.assertEqual(_lire(premier), "A v2")
        self.assertEqual(_lire(second), "B v2")

    def test_chemin_propre_disparu_retombe_sur_le_nom_libre(self):
        absent = os.path.join(self.dossier, "transcript_20261110_140000_9.txt")
        chemin = _enregistrer(self.dossier, DEBUT, "A", chemin_propre=absent)
        self.assertEqual(os.path.basename(chemin), "transcript_20261110_140000.txt")

    def test_noms_compatibles_avec_la_recuperation(self):
        _enregistrer(self.dossier, DEBUT, "A")
        second = _enregistrer(self.dossier, DEBUT, "B")
        noms = [os.path.basename(second)]
        self.assertTrue(recuperation_enregistrements._porte_horodatage(
            DEBUT, recuperation_enregistrements.PREFIXE_TRANSCRIPT, noms))


if __name__ == "__main__":
    unittest.main()
