"""Tests de passe_conservation.py : jobs factices, suppression et journal injectés."""
import datetime
import os
import types
import unittest
from unittest import mock

import passe_conservation as pc

ARCHIVES = r"C:\MeetNote\audio"
TRANSCRIPTS = r"C:\MeetNote\transcripts"
TMP = r"C:\MeetNote\tmp"
# Heure locale naïve voulue : celle des noms de fichiers et de `start_time`.
MAINTENANT = datetime.datetime(2026, 11, 17, 12, 0, 0)  # noqa: DTZ001
AUJOURDHUI = "2026-11-17"
VIEUX = MAINTENANT - datetime.timedelta(days=320)     # plus de 180 j
ANCIEN = MAINTENANT - datetime.timedelta(days=47)     # plus de 30 j, moins de 180 j
RECENT = MAINTENANT - datetime.timedelta(days=7)


def _horodatage(debut):
    return debut.strftime("%Y%m%d_%H%M%S")


def _job(identifiant, debut, **champs):
    """Job transcrit dont l'audio et le .txt sont dans les dossiers de MeetNote."""
    h = _horodatage(debut)
    valeurs = {
        "id": identifiant, "start_time": debut, "duration_min": 10,
        "wav_path": os.path.join(ARCHIVES, f"meetnote_{h}.opus"),
        "transcript_path": os.path.join(TRANSCRIPTS, f"transcript_{h}.txt"),
        "status_transcript": "done", "transcript": "texte confidentiel",
        "whisper_transcript": "brut", "teams_transcript": "teams", "notes": "notes",
        "teams_participants": ["A"], "participants_invites": ["B"],
        "audio_supprime_le": "", "transcript_purge_le": "", "titre": "reste"}
    valeurs.update(champs)
    return types.SimpleNamespace(**valeurs)


class _Env:
    """Disque factice : noms par dossier, fichiers présents, suppressions demandées."""

    def __init__(self, noms=None, motifs=None):
        self.noms = noms or {}
        self.motifs = motifs or {}      # chemin -> motif d'échec (ou exception)
        self.supprimes = []
        self.mises_a_jour = []
        self.journal = mock.Mock()

    def lister(self, dossier):
        return list(self.noms.get(dossier, []))

    def existe(self, chemin):
        return os.path.basename(chemin) in self.noms.get(os.path.dirname(chemin), [])

    def supprimer(self, chemin):
        motif = self.motifs.get(chemin)
        if isinstance(motif, Exception):
            raise motif
        if motif is None:
            self.supprimes.append(chemin)
        return motif

    def passe(self, jobs, **extra):
        return pc.executer_passe(
            jobs, MAINTENANT, 30, 180, ARCHIVES, TRANSCRIPTS, TMP, self.lister,
            self.supprimer, self.mises_a_jour.append, self.journal,
            existe=self.existe, **extra)

    def messages(self):
        return " | ".join(str(c) for c in self.journal.mock_calls)


def _env_pour(*jobs, **extra):
    noms = {ARCHIVES: [], TRANSCRIPTS: [], TMP: []}
    for job in jobs:
        noms[ARCHIVES].append(os.path.basename(job.wav_path))
        noms[TRANSCRIPTS].append(os.path.basename(job.transcript_path))
    return _Env(noms, **extra)


class AudioTest(unittest.TestCase):
    def test_audio_ancien_part_a_la_corbeille_et_le_job_est_marque(self):
        job = _job("a", ANCIEN)
        env = _env_pour(job)
        bilan = env.passe([job])
        self.assertEqual(env.supprimes, [job.wav_path])
        self.assertEqual(job.audio_supprime_le, AUJOURDHUI)
        self.assertEqual(job.transcript, "texte confidentiel")  # transcription intacte
        self.assertEqual((bilan.audios, bilan.echecs), (1, 0))
        self.assertEqual(env.mises_a_jour, [job])

    def test_audio_recent_reste(self):
        job = _job("a", RECENT)
        env = _env_pour(job)
        bilan = env.passe([job])
        self.assertEqual(env.supprimes, [])
        self.assertFalse(bilan.a_change)

    def test_job_occupe_ou_enregistrement_en_cours_rien_ne_part(self):
        job = _job("a", ANCIEN)
        env = _env_pour(job)
        env.passe([job], ids_occupes=["a"])
        self.assertEqual(env.supprimes, [])
        env.passe([job], enregistrement_en_cours=True)
        self.assertEqual(env.supprimes, [])

    def test_audio_deja_absent_est_marque_sans_echec(self):
        job = _job("a", ANCIEN)
        env = _env_pour(job)
        env.noms[ARCHIVES] = []  # supprimé à la main entre-temps
        bilan = env.passe([job])
        self.assertEqual(env.supprimes, [])
        self.assertEqual(job.audio_supprime_le, AUJOURDHUI)
        self.assertEqual((bilan.audios, bilan.echecs), (0, 0))

    def test_echec_laisse_le_job_intact_et_la_passe_continue(self):
        premier = _job("a", ANCIEN)
        second = _job("b", ANCIEN - datetime.timedelta(days=1))
        env = _env_pour(premier, second,
                        motifs={premier.wav_path: "code retour 0x78"})
        bilan = env.passe([premier, second])
        self.assertEqual(premier.audio_supprime_le, "")
        self.assertEqual(second.audio_supprime_le, AUJOURDHUI)
        self.assertEqual((bilan.audios, bilan.echecs), (1, 1))

    def test_exception_de_suppression_est_comptee_sans_se_propager(self):
        job = _job("a", ANCIEN)
        env = _env_pour(job, motifs={job.wav_path: RuntimeError("COM")})
        bilan = env.passe([job])
        self.assertEqual(bilan.echecs, 1)
        self.assertEqual(job.audio_supprime_le, "")

    def test_echec_d_ecriture_de_l_historique_est_compte(self):
        job = _job("a", ANCIEN)
        env = _env_pour(job)
        bilan = pc.executer_passe(
            [job], MAINTENANT, 30, 180, ARCHIVES, TRANSCRIPTS, TMP, env.lister,
            env.supprimer, mock.Mock(side_effect=OSError("disque")), env.journal,
            existe=env.existe)
        self.assertEqual((bilan.audios, bilan.echecs), (1, 1))


class DefenseEnProfondeurTest(unittest.TestCase):
    def test_chemin_hors_des_dossiers_n_est_jamais_supprime(self):
        job = _job("a", ANCIEN)
        env = _env_pour(job)
        env.noms[r"C:\Windows\system32"] = ["config.sys"]  # il existe bel et bien
        faux = types.SimpleNamespace(
            job=job, chemin=r"C:\Windows\system32\config.sys", regle="test")
        passe = pc._Passe((ARCHIVES, TRANSCRIPTS, TMP), env.supprimer,
                          env.mises_a_jour.append, env.journal, env.existe, AUJOURDHUI)
        passe.audios([faux])
        self.assertEqual(env.supprimes, [])
        self.assertEqual(passe.bilan.echecs, 1)
        self.assertIn(pc.MOTIF_HORS_DOSSIERS, env.messages())
        self.assertEqual(job.audio_supprime_le, "")

    def test_chemin_avec_remontee_est_refuse(self):
        env = _env_pour()
        passe = pc._Passe((ARCHIVES, TRANSCRIPTS, TMP), env.supprimer,
                          env.mises_a_jour.append, env.journal, env.existe, AUJOURDHUI)
        self.assertFalse(passe.envoyer(ARCHIVES + r"\..\..\Windows\x.opus", "test"))
        self.assertEqual(env.supprimes, [])


class TranscriptionTest(unittest.TestCase):
    def test_purge_vide_les_champs_et_envoie_le_txt_a_la_corbeille(self):
        job = _job("a", VIEUX)
        env = _env_pour(job)
        bilan = env.passe([job])
        self.assertIn(job.transcript_path, env.supprimes)
        self.assertEqual(job.transcript_purge_le, AUJOURDHUI)
        for champ in ("transcript", "whisper_transcript", "teams_transcript", "notes"):
            self.assertEqual(getattr(job, champ), "")
        self.assertEqual(job.teams_participants, [])
        self.assertEqual(job.participants_invites, [])
        self.assertEqual(job.titre, "reste")
        self.assertEqual(bilan.transcriptions, 1)

    def test_txt_non_supprime_ne_vide_pas_l_historique(self):
        job = _job("a", VIEUX, wav_path="")
        env = _env_pour(job, motifs={job.transcript_path: "annulée"})
        env.noms[ARCHIVES] = []
        bilan = env.passe([job])
        self.assertEqual(job.transcript, "texte confidentiel")
        self.assertEqual(job.transcript_purge_le, "")
        self.assertEqual((bilan.transcriptions, bilan.echecs), (0, 1))

    def test_purge_sans_fichier_vide_quand_meme_l_historique(self):
        job = _job("a", VIEUX)
        env = _env_pour(job)
        env.noms[TRANSCRIPTS] = []
        env.passe([job])
        self.assertEqual(job.transcript, "")
        self.assertEqual(job.transcript_purge_le, AUJOURDHUI)
        self.assertNotIn(job.transcript_path, env.supprimes)

    def test_job_deja_purge_n_est_pas_repris(self):
        job = _job("a", VIEUX, transcript_purge_le="2026-11-01", transcript="")
        env = _env_pour(job)
        self.assertFalse(env.passe([job]).a_change)


class PurgeDiffereeTest(unittest.TestCase):
    def test_envoi_notion_en_echec_ni_purge_ni_corbeille_et_journal(self):
        job = _job("a", VIEUX, status_notion="error")
        env = _env_pour(job)
        bilan = env.passe([job])
        self.assertNotIn(job.transcript_path, env.supprimes)
        self.assertEqual(job.transcript, "texte confidentiel")
        self.assertEqual(job.transcript_purge_le, "")
        self.assertEqual(bilan.transcriptions, 0)
        messages = env.messages()
        self.assertIn("purge différée : transcription non transmise", messages)
        self.assertIn("'a'", messages)
        self.assertNotIn("texte confidentiel", messages)

    def test_purge_a_la_passe_suivante_une_fois_l_envoi_reussi(self):
        job = _job("a", VIEUX, status_notion="pending")
        env = _env_pour(job)
        env.passe([job])
        self.assertEqual(job.transcript_purge_le, "")
        job.status_notion = "done"
        env.passe([job])
        self.assertIn(job.transcript_path, env.supprimes)
        self.assertEqual(job.transcript_purge_le, AUJOURDHUI)
        self.assertEqual(job.transcript, "")

    def test_mode_fichier_reste_purge(self):
        job = _job("a", VIEUX, status_notion="skipped")
        env = _env_pour(job)
        env.passe([job])
        self.assertIn(job.transcript_path, env.supprimes)
        self.assertEqual(job.transcript_purge_le, AUJOURDHUI)
        self.assertNotIn("purge différée", env.messages())


class OrphelinsEtJournalTest(unittest.TestCase):
    def test_orphelins_des_trois_dossiers_sont_supprimes(self):
        h = _horodatage(VIEUX)
        env = _Env({
            ARCHIVES: [f"meetnote_{h}.opus", "sans_date.opus"],
            TRANSCRIPTS: [f"transcript_{h}.txt"],
            TMP: [f"rec_{h}.wav"],
        })
        bilan = env.passe([])
        self.assertEqual(bilan.orphelins, 3)
        self.assertEqual(len(env.supprimes), 3)
        self.assertFalse(any("sans_date" in c for c in env.supprimes))

    def test_pas_d_orphelin_pendant_un_enregistrement(self):
        h = _horodatage(VIEUX)
        env = _Env({TMP: [f"rec_{h}.wav"]})
        env.passe([], enregistrement_en_cours=True)
        self.assertEqual(env.supprimes, [])

    def test_journal_porte_nom_et_regle_jamais_le_contenu(self):
        job = _job("a", VIEUX)
        env = _env_pour(job)
        env.passe([job])
        messages = env.messages()
        self.assertIn(os.path.basename(job.wav_path), messages)
        self.assertIn("de plus de 30 j", messages)
        self.assertNotIn("texte confidentiel", messages)

    def test_ligne_corbeille_porte_la_taille_du_fichier(self):
        job = _job("a", ANCIEN)
        env = _env_pour(job)
        env.passe([job], taille=lambda _chemin: 3 * 1024 * 1024 + 200 * 1024)
        self.assertIn("3.2 Mo", env.messages())

    def test_taille_inconnue_si_le_fichier_est_illisible(self):
        job = _job("a", ANCIEN)
        env = _env_pour(job)

        def illisible(_chemin):
            raise OSError("verrouillé")

        bilan = env.passe([job], taille=illisible)
        self.assertIn(pc.TAILLE_INCONNUE, env.messages())
        self.assertEqual(env.supprimes, [job.wav_path])  # la suppression a bien lieu
        self.assertEqual(bilan.echecs, 0)

    def test_taille_lisible(self):
        self.assertEqual(pc.taille_lisible(0), "0 Ko")
        self.assertEqual(pc.taille_lisible(10), "1 Ko")
        self.assertEqual(pc.taille_lisible(512 * 1024), "512 Ko")
        self.assertEqual(pc.taille_lisible(5 * 1024 * 1024), "5.0 Mo")

    def test_tmp_jamais_transcrit_survit_a_47_jours_et_part_apres_180(self):
        recent = f"rec_{_horodatage(ANCIEN)}.wav"
        vieux = f"rec_{_horodatage(VIEUX)}.wav"
        env = _Env({TMP: [recent, vieux]})
        env.passe([])
        self.assertEqual(env.supprimes, [os.path.join(TMP, vieux)])
        self.assertIn("sans transcription", env.messages())

    def test_un_resume_par_passe(self):
        env = _Env()
        bilan = env.passe([])
        env.journal.info.assert_called_once_with(bilan.resume())
        self.assertIn("0 audio(s)", bilan.resume())


if __name__ == "__main__":
    unittest.main()
