"""Tests du calcul des suppressions de conservation (fonctions pures, sans disque)."""
import datetime
import os
import types
import unittest

import retention

# Heure locale naïve voulue : celle des noms de fichiers et de `start_time`.
MAINTENANT = datetime.datetime(2026, 12, 31, 12, 0, 0)  # noqa: DTZ001
RACINE = os.path.abspath(os.sep + "meetnote_test")
ARCHIVES = os.path.join(RACINE, "audio")
TRANSCRIPTS = os.path.join(RACINE, "transcripts")
TMP = os.path.join(RACINE, "tmp")
AUDIO_J = 30
TRANSCRIPT_J = 180


def _debut(jours, heures=0):
    """Début de réunion situé `jours` jours (+ `heures`) avant MAINTENANT."""
    return MAINTENANT - datetime.timedelta(days=jours, hours=heures)


def _job(debut=None, **champs):
    """Job transcrit et archivé, avec toutes les conditions réunies par défaut."""
    debut = debut or _debut(40)
    base = {
        "id": "j1", "start_time": debut, "duration_min": 60.0,
        "wav_path": os.path.join(ARCHIVES, "meetnote_x.opus"),
        "transcript_path": os.path.join(TRANSCRIPTS, "transcript_x.txt"),
        "status_transcript": "done", "transcript": "bonjour",
        "audio_supprime_le": "", "transcript_purge_le": "",
    }
    base.update(champs)
    return types.SimpleNamespace(**base)


def _present(*noms):
    return retention.existence_par_noms(noms)


def _audios(jobs, **kw):
    args = {"maintenant": MAINTENANT, "retention_audio_jours": AUDIO_J,
            "dossier_archives": ARCHIVES,
            "transcript_existe": _present("transcript_x.txt")}
    args.update(kw)
    return retention.audios_a_supprimer(jobs, **args)


def _orphelins(jobs=(), archives=(), transcripts=(), tmp=()):
    return retention.orphelins(
        jobs, MAINTENANT, AUDIO_J, TRANSCRIPT_J,
        ARCHIVES, archives, TRANSCRIPTS, transcripts, TMP, tmp)


def _horodatage(jours, heures=0):
    return _debut(jours, heures).strftime(retention.FORMAT_HORODATAGE)


class TestAudiosASupprimer(unittest.TestCase):
    def test_audio_transcrit_depuis_plus_de_30_jours(self):
        job = _job()
        resultat = _audios([job])
        self.assertEqual(len(resultat), 1)
        self.assertIs(resultat[0].job, job)
        self.assertEqual(resultat[0].chemin, job.wav_path)
        self.assertIn("30", resultat[0].regle)

    def test_limite_exacte_pas_supprime(self):
        # Fin de transcription = début + 60 min : pile 30 jours avant maintenant.
        job = _job(_debut(30, 1))
        self.assertEqual(_audios([job]), [])

    def test_une_seconde_apres_la_limite_supprime(self):
        job = _job(_debut(30, 1) - datetime.timedelta(seconds=1))
        self.assertEqual(len(_audios([job])), 1)

    def test_duree_de_la_reunion_repousse_l_echeance(self):
        # Début il y a 30 j + 30 min, durée 60 min : fin il y a < 30 j.
        job = _job(_debut(30) - datetime.timedelta(minutes=30))
        self.assertEqual(_audios([job]), [])

    def test_duree_reglable(self):
        self.assertEqual(_audios([_job(_debut(5))]), [])
        self.assertEqual(len(_audios([_job(_debut(5))], retention_audio_jours=2)), 1)

    def test_enregistrement_en_cours_suspend_tout(self):
        self.assertEqual(_audios([_job()], enregistrement_en_cours=True), [])

    def test_job_en_file_ou_en_cours_exclu(self):
        self.assertEqual(_audios([_job()], ids_occupes={"j1"}), [])

    def test_autre_job_occupe_sans_effet(self):
        self.assertEqual(len(_audios([_job()], ids_occupes={"autre"})), 1)

    def test_chemin_hors_dossier_archives(self):
        hors = os.path.join(RACINE, "ailleurs", "meetnote_x.opus")
        self.assertEqual(_audios([_job(wav_path=hors)]), [])

    def test_chemin_avec_remontee_refuse(self):
        piege = os.path.join(ARCHIVES, "..", "ailleurs", "meetnote_x.opus")
        self.assertEqual(_audios([_job(wav_path=piege)]), [])
        piege = os.path.join(ARCHIVES, "sous", "..", "meetnote_x.opus")
        self.assertEqual(_audios([_job(wav_path=piege)]), [])

    def test_chemin_vide(self):
        self.assertEqual(_audios([_job(wav_path="")]), [])

    def test_casse_du_chemin_ignoree(self):
        job = _job(wav_path=os.path.join(ARCHIVES.upper(), "MEETNOTE_X.OPUS"))
        self.assertEqual(len(_audios([job])), 1)

    def test_deja_supprime(self):
        self.assertEqual(_audios([_job(audio_supprime_le="2026-12-01T10:00:00")]), [])

    def test_transcription_non_terminee(self):
        for statut in ("queued", "running", "error"):
            self.assertEqual(_audios([_job(status_transcript=statut)]), [], statut)

    def test_transcript_vide(self):
        self.assertEqual(_audios([_job(transcript="")]), [])
        self.assertEqual(_audios([_job(transcript="  \n ")]), [])

    def test_fichier_transcript_absent(self):
        self.assertEqual(_audios([_job()], transcript_existe=_present()), [])
        self.assertEqual(_audios([_job(transcript_path="")]), [])

    def test_fichier_transcript_present_casse_ignoree(self):
        job = _job(transcript_path=os.path.join(TRANSCRIPTS, "Transcript_X.TXT"))
        self.assertEqual(len(_audios([job])), 1)

    def test_predicat_d_existence_injecte(self):
        self.assertEqual(len(_audios([_job()], transcript_existe=lambda _c: True)), 1)
        self.assertEqual(_audios([_job()], transcript_existe=lambda _c: False), [])

    def test_job_sans_date_ignore(self):
        self.assertEqual(_audios([_job(start_time=None)]), [])

    def test_duree_invalide_traitee_comme_nulle(self):
        job = _job(_debut(31), duration_min="n/a")
        self.assertEqual(len(_audios([job])), 1)


class TestTranscriptionsAPurger(unittest.TestCase):
    def _purger(self, jobs, **kw):
        args = {"maintenant": MAINTENANT, "retention_transcript_jours": TRANSCRIPT_J,
                "dossier_transcripts": TRANSCRIPTS}
        args.update(kw)
        return retention.transcriptions_a_purger(jobs, **args)

    def test_reunion_de_plus_de_180_jours(self):
        job = _job(_debut(181))
        resultat = self._purger([job])
        self.assertEqual(len(resultat), 1)
        self.assertIs(resultat[0].job, job)
        self.assertEqual(resultat[0].chemin, job.transcript_path)
        self.assertIn("180", resultat[0].regle)

    def test_limite_exacte_pas_purge(self):
        self.assertEqual(self._purger([_job(_debut(180))]), [])

    def test_une_seconde_apres_la_limite(self):
        debut = _debut(180) - datetime.timedelta(seconds=1)
        self.assertEqual(len(self._purger([_job(debut)])), 1)

    def test_reunion_recente(self):
        self.assertEqual(self._purger([_job(_debut(10))]), [])

    def test_deja_purge(self):
        job = _job(_debut(200), transcript_purge_le="2026-12-01T10:00:00")
        self.assertEqual(self._purger([job]), [])

    def test_job_occupe_exclu(self):
        self.assertEqual(self._purger([_job(_debut(200))], ids_occupes=["j1"]), [])

    def test_chemin_hors_dossier_purge_sans_fichier(self):
        hors = os.path.join(RACINE, "ailleurs", "transcript_x.txt")
        resultat = self._purger([_job(_debut(200), transcript_path=hors)])
        self.assertEqual(len(resultat), 1)
        self.assertEqual(resultat[0].chemin, "")

    def test_chemin_avec_remontee_purge_sans_fichier(self):
        piege = os.path.join(TRANSCRIPTS, "..", "tmp", "transcript_x.txt")
        resultat = self._purger([_job(_debut(200), transcript_path=piege)])
        self.assertEqual(resultat[0].chemin, "")

    def test_sans_fichier_transcript_purge_quand_meme(self):
        resultat = self._purger([_job(_debut(200), transcript_path="")])
        self.assertEqual(len(resultat), 1)
        self.assertEqual(resultat[0].chemin, "")

    def test_job_sans_date_ignore(self):
        self.assertEqual(self._purger([_job(start_time="hier")]), [])

    def test_envoi_notion_non_abouti_jamais_purge(self):
        for statut in ("pending", "running", "error"):
            job = _job(_debut(400), status_notion=statut)
            self.assertEqual(self._purger([job]), [], statut)

    def test_envoi_reussi_ou_mode_fichier_purge(self):
        for statut in ("done", "skipped"):
            job = _job(_debut(400), status_notion=statut)
            self.assertEqual(len(self._purger([job])), 1, statut)

    def test_differees_liste_les_echus_non_transmis_seulement(self):
        attente = _job(_debut(400), id="a", status_notion="error")
        envoye = _job(_debut(400), id="b", status_notion="done")
        recent = _job(_debut(10), id="c", status_notion="error")
        occupe = _job(_debut(400), id="d", status_notion="pending")
        purge = _job(_debut(400), id="e", status_notion="error",
                     transcript_purge_le="2026-12-01")
        resultat = retention.transcriptions_differees(
            [attente, envoye, recent, occupe, purge], MAINTENANT, TRANSCRIPT_J, ["d"])
        self.assertEqual(resultat, [attente])

    def test_champs_a_vider(self):
        self.assertEqual(
            retention.champs_a_vider_purge(),
            ("transcript", "whisper_transcript", "teams_transcript", "notes",
             "teams_participants", "participants_invites"))
        for protege in ("meeting_name", "start_time", "duration_min", "notion_url",
                        "status_notion", "participants_informes_le"):
            self.assertNotIn(protege, retention.champs_a_vider_purge())


class TestOrphelins(unittest.TestCase):
    def _chemins(self, resultat):
        return [os.path.basename(o.chemin) for o in resultat]

    def test_audio_orphelin_avec_transcription_a_30_jours(self):
        h = _horodatage(31)
        res = _orphelins(archives=[f"meetnote_{h}_titre.opus"],
                         transcripts=[f"transcript_{h}.txt"])
        audios = [o for o in res if o.chemin.startswith(ARCHIVES)]
        self.assertEqual(self._chemins(audios), [f"meetnote_{h}_titre.opus"])
        self.assertIn("30", audios[0].regle)
        self.assertEqual(os.path.dirname(audios[0].chemin), ARCHIVES)

    def test_audio_orphelin_avec_transcription_avant_30_jours(self):
        h = _horodatage(29)
        res = _orphelins(archives=[f"meetnote_{h}.opus"],
                         transcripts=[f"transcript_{h}.txt"])
        self.assertEqual(res, [])

    def test_audio_orphelin_sans_transcription_garde_180_jours(self):
        h = _horodatage(100)
        self.assertEqual(_orphelins(archives=[f"meetnote_{h}.opus"]), [])

    def test_audio_orphelin_sans_transcription_apres_180_jours(self):
        h = _horodatage(181)
        res = _orphelins(archives=[f"meetnote_{h}.wav"])
        self.assertEqual(self._chemins(res), [f"meetnote_{h}.wav"])
        self.assertIn("180", res[0].regle)

    def test_transcription_d_un_autre_horodatage_ne_compte_pas(self):
        h = _horodatage(100)
        res = _orphelins(archives=[f"meetnote_{h}.opus"],
                         transcripts=[f"transcript_{_horodatage(101)}.txt"])
        self.assertEqual(res, [])

    def test_transcription_avec_suffixe_compte(self):
        h = _horodatage(40)
        res = _orphelins(archives=[f"meetnote_{h}.opus"],
                         transcripts=[f"transcript_{h}_2.txt"])
        self.assertEqual(len(res), 1)

    def test_transcription_orpheline(self):
        h = _horodatage(181)
        res = _orphelins(transcripts=[f"transcript_{h}.txt"])
        self.assertEqual(self._chemins(res), [f"transcript_{h}.txt"])
        self.assertEqual(os.path.dirname(res[0].chemin), TRANSCRIPTS)
        self.assertIn("180", res[0].regle)

    def test_transcription_orpheline_recente(self):
        self.assertEqual(_orphelins(transcripts=[f"transcript_{_horodatage(179)}.txt"]),
                         [])

    def test_enregistrement_tmp_transcrit_a_30_jours(self):
        h = _horodatage(31)
        res = _orphelins(tmp=[f"rec_{h}.wav", f"rec_{_horodatage(29)}.wav"],
                         transcripts=[f"transcript_{h}.txt",
                                      f"transcript_{_horodatage(29)}.txt"])
        rec = [o for o in res if os.path.dirname(o.chemin) == TMP]
        self.assertEqual(self._chemins(rec), [f"rec_{h}.wav"])
        self.assertIn("30 j", rec[0].regle)
        self.assertIn("transcription présente", rec[0].regle)

    def test_enregistrement_tmp_jamais_transcrit_garde_180_jours(self):
        h = _horodatage(31)
        self.assertEqual(_orphelins(tmp=[f"rec_{h}.wav"]), [])
        h = _horodatage(179)
        self.assertEqual(_orphelins(tmp=[f"rec_{h}.wav"]), [])
        h = _horodatage(181)
        res = _orphelins(tmp=[f"rec_{h}.wav"])
        self.assertEqual(self._chemins(res), [f"rec_{h}.wav"])
        self.assertEqual(os.path.dirname(res[0].chemin), TMP)
        self.assertIn("180 j", res[0].regle)
        self.assertIn("sans transcription", res[0].regle)

    def test_transcription_d_un_autre_horodatage_ne_raccourcit_pas_le_tmp(self):
        h = _horodatage(40)
        res = _orphelins(tmp=[f"rec_{h}.wav"],
                         transcripts=[f"transcript_{_horodatage(41)}.txt"])
        self.assertEqual([o for o in res if os.path.dirname(o.chemin) == TMP], [])

    def test_limite_exacte_orphelin(self):
        h = _horodatage(30)
        self.assertEqual(_orphelins(tmp=[f"rec_{h}.wav"],
                                    transcripts=[f"transcript_{h}.txt"]), [])

    def test_nom_sans_date_jamais_retenu(self):
        res = _orphelins(
            archives=["meetnote_sans_date.opus", "meetnote_20261399_999999.opus",
                      "meetnote_2026.opus"],
            transcripts=["transcript_vide.txt", "notes.txt"],
            tmp=["rec_abc.wav", "whisper_20200101_000000.wav"])
        self.assertEqual(res, [])

    def test_extensions_et_prefixes_etrangers_ignores(self):
        h = _horodatage(400)
        res = _orphelins(archives=[f"meetnote_{h}.mp3", f"autre_{h}.opus"],
                         transcripts=[f"transcript_{h}.md"],
                         tmp=[f"rec_{h}.opus"])
        self.assertEqual(res, [])

    def test_reference_par_wav_path(self):
        h = _horodatage(400)
        nom = f"meetnote_{h}.opus"
        job = _job(_debut(1), wav_path=os.path.join(ARCHIVES, nom))
        self.assertEqual(_orphelins([job], archives=[nom]), [])

    def test_reference_par_transcript_path(self):
        h = _horodatage(400)
        nom = f"transcript_{h}.txt"
        job = _job(_debut(1), transcript_path=os.path.join(TRANSCRIPTS, nom))
        self.assertEqual(_orphelins([job], transcripts=[nom]), [])

    def test_reference_par_horodatage_commun(self):
        h = _horodatage(400)
        job = _job(_debut(400), wav_path="", transcript_path="")
        res = _orphelins([job], archives=[f"meetnote_{h}.opus"],
                         transcripts=[f"transcript_{h}.txt"])
        self.assertEqual(res, [])

    def test_tmp_protege_par_chemin_seulement(self):
        h = _horodatage(400)
        nom = f"rec_{h}.wav"
        # Job au même horodatage (reliquat de tmp) : le fichier est à supprimer...
        meme_debut = _job(_debut(400), wav_path="", transcript_path="")
        self.assertEqual(self._chemins(_orphelins([meme_debut], tmp=[nom])), [nom])
        # ... sauf si le job utilise encore ce fichier comme audio.
        utilise = _job(_debut(400), wav_path=os.path.join(TMP, nom))
        self.assertEqual(_orphelins([utilise], tmp=[nom]), [])

    def test_trois_dossiers_a_la_fois(self):
        h = _horodatage(400)
        res = _orphelins(archives=[f"meetnote_{h}.opus"],
                         transcripts=[f"transcript_{h}.txt"], tmp=[f"rec_{h}.wav"])
        self.assertEqual(len(res), 3)


if __name__ == "__main__":
    unittest.main()
