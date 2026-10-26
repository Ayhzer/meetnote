"""
Tests de history (sauvegarde atomique, fichier corrompu) et du module partage
fichier_json, sur un dossier temporaire.

Lancement : py -3.12 pc\\test_history.py
"""
import datetime
import json
import os
import sys
import tempfile
import unittest
from typing import ClassVar
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fichier_json
import history


class _BaseTemporaire(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dossier = os.path.join(tmp.name, "MeetNote")
        self.chemin = os.path.join(self.dossier, "history.json")
        patcher = patch.object(history, "HISTORY_PATH", self.chemin)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _ecrire_brut(self, contenu: bytes):
        os.makedirs(self.dossier, exist_ok=True)
        with open(self.chemin, "wb") as f:
            f.write(contenu)

    def _octets(self):
        with open(self.chemin, "rb") as f:
            return f.read()

    def _residus(self):
        return [n for n in os.listdir(self.dossier) if n != "history.json"]


class TestHistorySave(_BaseTemporaire):
    def test_save_ecrit_sans_temporaire_residuel(self):
        history.save([{"id": "a", "meeting_name": "Reunion é"}])
        self.assertEqual(json.loads(self._octets())[0]["id"], "a")
        self.assertEqual(self._residus(), [])

    def test_save_borne_a_max_entries(self):
        history.save([{"id": str(i)} for i in range(history.MAX_ENTRIES + 5)])
        self.assertEqual(len(json.loads(self._octets())), history.MAX_ENTRIES)

    def test_echec_dump_laisse_l_ancien_fichier_intact_et_journalise(self):
        history.save([{"id": "ancien"}])
        avant = self._octets()
        panne = patch.object(fichier_json.json, "dump",
                             side_effect=OSError("disque plein"))
        with panne, self.assertLogs("meetnote", level="WARNING") as logs:
            history.save([{"id": "nouveau"}])  # ne leve pas
        self.assertEqual(self._octets(), avant)
        self.assertEqual(self._residus(), [])
        self.assertIn("disque plein", "\n".join(logs.output))

    def test_entree_non_serialisable_journalisee_sans_ecraser(self):
        history.save([{"id": "ancien"}])
        avant = self._octets()
        with self.assertLogs("meetnote", level="WARNING"):
            history.save([{"id": object()}])
        self.assertEqual(self._octets(), avant)
        self.assertEqual(self._residus(), [])


class TestHistoryLoad(_BaseTemporaire):
    def test_absent_rend_liste_vide_sans_renommage(self):
        os.makedirs(self.dossier, exist_ok=True)
        self.assertEqual(history.load(), [])
        self.assertEqual(os.listdir(self.dossier), [])

    def test_corrompu_renomme_et_liste_vide(self):
        self._ecrire_brut(b'[{"id": "a", "meeting_name": "tronq')
        with self.assertLogs("meetnote", level="WARNING") as logs:
            self.assertEqual(history.load(), [])
        self.assertFalse(os.path.exists(self.chemin))
        conserves = [n for n in os.listdir(self.dossier)
                     if n.startswith("history.json.corrompu-")]
        self.assertEqual(len(conserves), 1)
        self.assertNotIn("tronq", "\n".join(logs.output))

    def test_save_apres_corruption_ne_perd_pas_l_original(self):
        self._ecrire_brut(b'[{"id": "a"')
        with self.assertLogs("meetnote", level="WARNING"):
            history.add(_JobFactice("b"))
        conserves = [n for n in os.listdir(self.dossier) if ".corrompu-" in n]
        with open(os.path.join(self.dossier, conserves[0]), "rb") as f:
            self.assertEqual(f.read(), b'[{"id": "a"')

    def test_racine_non_liste_traitee_comme_corrompue(self):
        self._ecrire_brut(b'{"id": "a"}')
        with self.assertLogs("meetnote", level="WARNING"):
            self.assertEqual(history.load(), [])
        self.assertTrue(any(".corrompu-" in n for n in os.listdir(self.dossier)))

    def test_non_utf8_traite_comme_corrompu(self):
        self._ecrire_brut(b"\xff\xfe\x00garbage")
        with self.assertLogs("meetnote", level="WARNING"):
            self.assertEqual(history.load(), [])

    def test_load_complete_les_cles_manquantes(self):
        self._ecrire_brut(b'[{"id": "a"}]')
        entree = history.load()[0]
        self.assertEqual(entree["status_transcript"], "queued")
        self.assertEqual(entree["attachments"], [])

    def test_ancien_historique_sans_participants_invites(self):
        self._ecrire_brut(b'[{"id": "a", "teams_participants": ["Ana"]}]')
        entree = history.load()[0]
        self.assertEqual(entree["participants_invites"], [])
        self.assertEqual(entree["teams_participants"], ["Ana"])

    def test_participants_invites_conserves_au_rechargement(self):
        self._ecrire_brut(b'[{"id": "a", "participants_invites": ["Bob"]}]')
        self.assertEqual(history.load()[0]["participants_invites"], ["Bob"])


def _champs_du_job_tray() -> set:
    """Champs de la dataclass _Job, lus par AST (fichier non importable)."""
    import ast
    dossier = os.path.dirname(os.path.abspath(__file__))
    chemin = os.path.join(dossier, "meetnote-tray.py")
    with open(chemin, encoding="utf-8") as f:
        arbre = ast.parse(f.read())
    for noeud in arbre.body:
        if isinstance(noeud, ast.ClassDef) and noeud.name == "_Job":
            return {n.target.id for n in noeud.body if isinstance(n, ast.AnnAssign)}
    raise AssertionError("classe _Job introuvable")


class TestChampsJob(_BaseTemporaire):
    ENTREE_COMPLETE: ClassVar[dict] = {
        "id": "j1", "wav_path": "a.opus", "start_time": "2026-10-25T09:30:00",
        "duration_min": 42.5, "model_name": "small", "language": "en",
        "meeting_type": "Comité", "output_mode": "fichier", "meeting_name": "Point",
        "teams_participants": ["Ana"], "participants_invites": ["Bob", "Cléa"],
        "status_audio": "done", "status_transcript": "error",
        "status_notion": "pending",
        "transcript": "texte", "whisper_transcript": "brut whisper",
        "teams_transcript": "collé Teams", "notes": "note manuelle é",
        "transcript_path": "t.txt", "notion_url": "https://n", "error_msg": "x",
        "attachments": ["p.pdf"],
    }

    def test_couvre_tous_les_champs_du_job(self):
        self.assertEqual(set(history.champs_job({})), _champs_du_job_tray())

    def test_entree_complete_relue_a_l_identique(self):
        champs = history.champs_job(self.ENTREE_COMPLETE)
        for cle in ("notes", "whisper_transcript", "teams_transcript",
                    "participants_invites", "teams_participants"):
            self.assertEqual(champs[cle], self.ENTREE_COMPLETE[cle], cle)

    def test_entree_ancienne_donne_les_defauts(self):
        champs = history.champs_job({"id": "vieux"})
        self.assertEqual(champs["notes"], "")
        self.assertEqual(champs["whisper_transcript"], "")
        self.assertEqual(champs["teams_transcript"], "")
        self.assertEqual(champs["participants_invites"], [])
        self.assertEqual(champs["teams_participants"], [])
        self.assertEqual(champs["model_name"], "base")
        self.assertEqual(champs["status_transcript"], "done")

    def test_start_time_absent_ou_non_texte_vaut_maintenant(self):
        for entree in ({}, {"start_time": ""}, {"start_time": 12}):
            self.assertIsInstance(history.champs_job(entree)["start_time"],
                                  datetime.datetime)

    def test_start_time_invalide_leve_value_error(self):
        # main() attrape l'erreur, journalise et ignore l'entrée (comme avant).
        with self.assertRaises(ValueError):
            history.champs_job({"id": "x", "start_time": "pas une date"})

    def test_aller_retour_add_load_conserve_les_notes(self):
        import dataclasses

        champs = history.champs_job(self.ENTREE_COMPLETE)
        Job = dataclasses.make_dataclass(
            "Job", [(k, type(v)) for k, v in champs.items()])
        history.add(Job(**champs))
        relu = history.champs_job(history.load()[0])
        self.assertEqual(relu, champs)
        self.assertEqual(
            relu["start_time"],
            datetime.datetime(2026, 10, 25, 9, 30),  # noqa: DTZ001 — locale naïve
        )
        self.assertEqual(relu["notes"], "note manuelle é")


class _JobFactice:
    """Imite un job dataclass pour history.add (asdict exige une dataclass)."""
    def __new__(cls, ident):
        import dataclasses

        @dataclasses.dataclass
        class Job:
            id: str
        return Job(ident)


class TestFichierJson(unittest.TestCase):
    def test_non_dict_rejete_quand_dict_attendu(self):
        with tempfile.TemporaryDirectory() as tmp:
            chemin = os.path.join(tmp, "x.json")
            fichier_json.ecrire_atomique(chemin, [1, 2])
            with self.assertLogs("meetnote", level="WARNING"):
                self.assertIsNone(fichier_json.lire_ou_ecarter(chemin, dict))
            self.assertFalse(os.path.exists(chemin))

    def test_aller_retour(self):
        with tempfile.TemporaryDirectory() as tmp:
            chemin = os.path.join(tmp, "sous", "x.json")
            fichier_json.ecrire_atomique(chemin, {"k": "é"}, ensure_ascii=False)
            self.assertEqual(fichier_json.lire_ou_ecarter(chemin, dict), {"k": "é"})
            self.assertEqual(os.listdir(os.path.dirname(chemin)), ["x.json"])


if __name__ == "__main__":
    unittest.main()
