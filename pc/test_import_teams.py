"""Tests de import_teams : filtrage, état, identifiant stable, chargement."""
import datetime
import hashlib
import unittest
from unittest import mock

import graph_auth
import graph_teams
import import_teams as it

# Dates avec fuseau : les comparaisons sont les mêmes qu'en naïf local.
UTC = datetime.timezone.utc
MAINTENANT = datetime.datetime(2026, 11, 10, 12, 0, tzinfo=UTC)
LIEN = "https://teams.microsoft.com/meet/123456789012?p=CodeFictif"
LIEN_B = "https://teams.microsoft.com/meet/999999999999?p=AutreCode"
CONFIG = {"graph_client_id": "client-fictif", "graph_tenant_id": "tenant-fictif"}


def evenement(debut, duree_min=60, lien=LIEN, objet="Point projet", orga="A. Martin",
              requis=None, optionnels=None):
    return {"subject": objet, "start": debut,
            "end": debut + datetime.timedelta(minutes=duree_min),
            "organizer": orga, "join_url": lien,
            "required": requis or [], "optional": optionnels or []}


def transcript(identifiant, cree=None):
    return {"id": identifiant, "cree": cree or MAINTENANT}


class FauxGraphe:
    """Remplace graph_teams : réunions et transcripts fournis par des dictionnaires."""

    def __init__(self, reunions=None, transcripts=None, erreurs=None, vtt=None):
        self.reunions = reunions or {}
        self.vtt = vtt or {}
        self.transcripts_par_reunion = transcripts or {}
        self.erreurs = erreurs or {}
        self.appels_reunion = []

    def reunion_en_ligne(self, join_url, jeton):
        self.appels_reunion.append(join_url)
        return self.reunions.get(join_url)

    def transcripts(self, reunion_id, debut, fin, jeton):
        if reunion_id in self.erreurs:
            raise self.erreurs[reunion_id]
        return self.transcripts_par_reunion.get(reunion_id, [])

    def contenu(self, reunion_id, transcript_id, jeton):
        cle = (reunion_id, transcript_id)
        if cle in self.erreurs:
            raise self.erreurs[cle]
        return self.vtt.get(cle, "")


def jeton_ok():
    return graph_auth.ResultatJeton("jeton-fictif", "a@exemple.fr")


class TestPeriode(unittest.TestCase):

    def test_quinze_jours_en_arriere(self):
        debut, fin = it.periode(MAINTENANT)
        self.assertEqual(fin, MAINTENANT)
        self.assertEqual(debut, MAINTENANT - datetime.timedelta(days=15))


class TestReunionsAInterroger(unittest.TestCase):

    def test_sans_lien_ou_lien_vide_ecarte(self):
        debut = MAINTENANT - datetime.timedelta(days=1)
        evts = [evenement(debut, lien=""), evenement(debut, lien="   "),
                {"subject": "x", "start": debut, "end": debut}]
        self.assertEqual(it.reunions_a_interroger(evts, MAINTENANT), [])

    def test_non_terminee_ecartee_et_fin_egale_a_maintenant_retenue(self):
        en_cours = evenement(MAINTENANT - datetime.timedelta(minutes=10))
        finie_pile = evenement(MAINTENANT - datetime.timedelta(hours=1))
        retenus = it.reunions_a_interroger([en_cours, finie_pile], MAINTENANT)
        self.assertEqual(retenus, [finie_pile])

    def test_tri_recent_d_abord(self):
        ancienne = evenement(MAINTENANT - datetime.timedelta(days=3))
        recente = evenement(MAINTENANT - datetime.timedelta(days=1))
        retenus = it.reunions_a_interroger([ancienne, recente], MAINTENANT)
        self.assertEqual(retenus, [recente, ancienne])


class TestIdentifiant(unittest.TestCase):

    def test_format_et_valeur(self):
        debut = datetime.datetime(2026, 11, 9, 14, 5, 7, tzinfo=UTC)
        attendu = hashlib.sha1(b"transcript-1", usedforsecurity=False).hexdigest()[:8]
        self.assertEqual(it.identifiant_import(debut, "transcript-1"),
                         f"20261109_140507_teams_{attendu}")

    def test_stable_et_distinct_selon_le_transcript(self):
        debut = datetime.datetime(2026, 11, 9, 14, 5, 7, tzinfo=UTC)
        self.assertEqual(it.identifiant_import(debut, "a"),
                         it.identifiant_import(debut, "a"))
        self.assertNotEqual(it.identifiant_import(debut, "a"),
                            it.identifiant_import(debut, "b"))

    def test_identifiants_importes_ignore_les_entrees_sans_id(self):
        entrees = [{"id": "x"}, {"id": 5}, {}, {"id": "y"}]
        self.assertEqual(it.identifiants_importes(entrees), {"x", "y"})


class TestChoisirTranscript(unittest.TestCase):
    DEBUT = datetime.datetime(2026, 11, 9, 14, 0, tzinfo=UTC)

    def test_aucun_transcript(self):
        self.assertEqual(it.choisir_transcript(self.DEBUT, [], set()),
                         (it.ETAT_ABSENT, "", ""))

    def test_disponible_prend_le_dernier(self):
        etat, tid, jid = it.choisir_transcript(
            self.DEBUT, [transcript("t1"), transcript("t2")], set())
        self.assertEqual((etat, tid), (it.ETAT_DISPONIBLE, "t2"))
        self.assertEqual(jid, it.identifiant_import(self.DEBUT, "t2"))

    def test_deja_importe(self):
        jid = it.identifiant_import(self.DEBUT, "t1")
        etat, tid, jid_rendu = it.choisir_transcript(
            self.DEBUT, [transcript("t1"), transcript("t2")], {jid})
        self.assertEqual((etat, tid, jid_rendu), (it.ETAT_IMPORTE, "t1", jid))

    def test_cochable_seulement_si_disponible(self):
        coche = {etat: it.LigneReunion(MAINTENANT, MAINTENANT, "o", "", "u", etat)
                 for etat in it.LIBELLES_ETATS}
        self.assertTrue(coche[it.ETAT_DISPONIBLE].cochable)
        self.assertFalse(coche[it.ETAT_ABSENT].cochable)
        self.assertFalse(coche[it.ETAT_IMPORTE].cochable)
        self.assertEqual(coche[it.ETAT_ABSENT].libelle_etat, "pas de transcript")

    def test_libelle_creneau(self):
        debut = datetime.datetime(2026, 11, 9, 9, 5, tzinfo=UTC)
        self.assertEqual(it.libelle_creneau(debut, debut.replace(hour=10)),
                         "09/11/2026 09:05–10:05")


class TestMotifSensible(unittest.TestCase):

    def test_privee_prime_puis_exclue_puis_banale(self):
        debut = MAINTENANT
        self.assertEqual(it.motif_sensible(
            dict(evenement(debut, objet="Perso"), prive=True), ["perso"]),
            it.MOTIF_PRIVEE)
        self.assertEqual(it.motif_sensible(evenement(debut, objet="Rdv Perso"),
                                           ["perso"]), it.MOTIF_EXCLUE)
        self.assertEqual(it.motif_sensible(evenement(debut), ["perso"]), "")
        self.assertEqual(it.motif_sensible({"subject": None}, ["perso"]), "")

    def test_une_ligne_non_disponible_garde_son_libelle(self):
        ligne = it.LigneReunion(MAINTENANT, MAINTENANT, "o", "", "u", it.ETAT_ABSENT,
                                motif_sensible=it.MOTIF_PRIVEE)
        self.assertEqual(ligne.libelle_etat, "pas de transcript")
        self.assertFalse(ligne.pre_cochee)


class TestCharger(unittest.TestCase):

    def setUp(self):
        patch = mock.patch.object(it.user_config, "get",
                                  side_effect=lambda cle, defaut="": CONFIG.get(
                                      cle, defaut))
        patch.start()
        self.addCleanup(patch.stop)
        self.hier = MAINTENANT - datetime.timedelta(days=1)

    def charger(self, evenements, graphe, importes=None, jeton=jeton_ok,
                progression=None):
        sources = [("faux", lambda debut, fin: evenements)]
        return it.charger(MAINTENANT, importes or set(), sources=sources,
                          obtenir_jeton=jeton, graphe=graphe,
                          progression=progression)

    def test_trois_etats(self):
        d1, d2, d3 = (self.hier, self.hier - datetime.timedelta(days=1),
                      self.hier - datetime.timedelta(days=2))
        evts = [evenement(d1, lien=LIEN), evenement(d2, lien=LIEN_B),
                evenement(d3, lien="https://teams.microsoft.com/meet/3?p=c")]
        graphe = FauxGraphe(
            reunions={LIEN: "r1", LIEN_B: "r2"},
            transcripts={"r1": [transcript("t1")], "r2": [transcript("t2")]})
        importes = {it.identifiant_import(d2, "t2")}
        resultat = self.charger(evts, graphe, importes)
        self.assertEqual(resultat.message, "")
        self.assertEqual([ligne.etat for ligne in resultat.lignes],
                         [it.ETAT_DISPONIBLE, it.ETAT_IMPORTE, it.ETAT_ABSENT])
        premiere = resultat.lignes[0]
        self.assertEqual((premiere.objet, premiere.organisateur, premiere.reunion_id,
                          premiere.transcript_id),
                         ("Point projet", "A. Martin", "r1", "t1"))
        self.assertEqual(premiere.job_id, it.identifiant_import(d1, "t1"))

    def test_les_invites_de_la_ligne_n_ont_pas_d_adresse_email(self):
        evts = [evenement(self.hier, requis=["Alice Durand", "bob@exemple.fr"],
                          optionnels=["Carole <carole@exemple.fr>"])]
        resultat = self.charger(evts, FauxGraphe(reunions={LIEN: "r1"}))
        invites = resultat.lignes[0].invites
        self.assertIn("Alice Durand", invites)
        self.assertFalse([i for i in invites if "@" in i])

    def test_reunion_privee_ou_exclue_listee_mais_jamais_precochee(self):
        privee = evenement(self.hier, lien=LIEN, objet="Point RH")
        privee["prive"] = True
        perso = evenement(self.hier - datetime.timedelta(days=1), lien=LIEN_B,
                          objet="Rdv PERSO médecin")
        banale = evenement(self.hier - datetime.timedelta(days=2),
                           lien="https://teams.microsoft.com/meet/3?p=c")
        graphe = FauxGraphe(
            reunions={LIEN: "r1", LIEN_B: "r2",
                      "https://teams.microsoft.com/meet/3?p=c": "r3"},
            transcripts={"r1": [transcript("t1")], "r2": [transcript("t2")],
                         "r3": [transcript("t3")]})
        sources = [("faux", lambda debut, fin: [privee, perso, banale])]
        resultat = it.charger(MAINTENANT, set(), sources=sources,
                              obtenir_jeton=jeton_ok, graphe=graphe,
                              exclusions=["perso"])
        l_privee, l_perso, l_banale = resultat.lignes
        for ligne in (l_privee, l_perso):
            self.assertEqual(ligne.etat, it.ETAT_DISPONIBLE)
            self.assertTrue(ligne.cochable)  # cochable à la main
            self.assertFalse(ligne.pre_cochee)
        self.assertEqual(l_privee.libelle_etat,
                         "transcript disponible · privée — non cochée")
        self.assertEqual(l_perso.libelle_etat,
                         "transcript disponible · exclue — non cochée")
        self.assertTrue(l_banale.pre_cochee)
        self.assertEqual(l_banale.libelle_etat, "transcript disponible")

    def test_exclusions_par_defaut_lues_dans_les_reglages(self):
        reglages = dict(CONFIG, auto_start_exclusions="Perso\n\nOff")
        evts = [evenement(self.hier, objet="Café OFF")]
        with mock.patch.object(it.user_config, "get",
                               side_effect=lambda cle, defaut="": reglages.get(
                                   cle, defaut)):
            resultat = self.charger(evts, FauxGraphe(
                reunions={LIEN: "r1"}, transcripts={"r1": [transcript("t1")]}))
        self.assertEqual(resultat.lignes[0].motif_sensible, it.MOTIF_EXCLUE)
        self.assertFalse(resultat.lignes[0].pre_cochee)

    def test_serie_recurrente_une_seule_requete_de_reunion(self):
        evts = [evenement(self.hier), evenement(self.hier - datetime.timedelta(days=7))]
        graphe = FauxGraphe(reunions={LIEN: "r1"})
        self.charger(evts, graphe)
        self.assertEqual(graphe.appels_reunion, [LIEN])

    def test_reunion_introuvable_dans_graph_est_sans_transcript(self):
        erreur = graph_teams.ErreurGraph("introuvable", 404)
        graphe = FauxGraphe(reunions={LIEN: "r1"}, erreurs={"r1": erreur})
        resultat = self.charger([evenement(self.hier)], graphe)
        self.assertEqual(resultat.message, "")
        self.assertEqual(resultat.lignes[0].etat, it.ETAT_ABSENT)

    def test_acces_desactive_par_l_admin_affiche_le_message(self):
        erreur = graph_teams.ErreurGraph(
            "accès aux transcriptions désactivé par l'admin Teams", 403,
            graph_teams.CODE_TRANSCRIPTS_DESACTIVES)
        graphe = FauxGraphe(reunions={LIEN: "r1", LIEN_B: "r2"},
                            erreurs={"r2": erreur},
                            transcripts={"r1": [transcript("t1")]})
        evts = [evenement(self.hier, lien=LIEN),
                evenement(self.hier - datetime.timedelta(days=1), lien=LIEN_B)]
        resultat = self.charger(evts, graphe)
        self.assertIn("désactivé par l'admin Teams", resultat.message)
        self.assertEqual(len(resultat.lignes), 1)  # lignes déjà établies conservées

    def test_application_non_configuree(self):
        with mock.patch.object(it.user_config, "get", return_value=""):
            resultat = self.charger([evenement(self.hier)], FauxGraphe())
        self.assertEqual(resultat, it.ResultatListe([], it.MSG_NON_CONFIGURE))
        self.assertIn("Paramètres", resultat.message)

    def test_connexion_requise_renvoie_vers_parametres(self):
        def sans_session():
            return graph_auth.ResultatJeton(
                None, None, "Connexion Microsoft requise : aucune session en cache")

        resultat = self.charger([evenement(self.hier)], FauxGraphe(),
                                jeton=sans_session)
        self.assertEqual(resultat.lignes, [])
        self.assertIn("aucune session en cache", resultat.message)
        self.assertIn("Paramètres", resultat.message)

    def test_agenda_injoignable(self):
        sources = [("faux", lambda debut, fin: None)]
        resultat = it.charger(MAINTENANT, set(), sources=sources,
                              obtenir_jeton=jeton_ok, graphe=FauxGraphe())
        self.assertEqual(resultat, it.ResultatListe([], it.MSG_AGENDA_INJOIGNABLE))

    def test_aucune_reunion_a_interroger(self):
        resultat = self.charger([evenement(self.hier, lien="")], FauxGraphe())
        self.assertEqual(resultat, it.ResultatListe([], it.MSG_AUCUNE_REUNION))

    def test_progression_appelee_a_chaque_reunion(self):
        evts = [evenement(self.hier), evenement(self.hier - datetime.timedelta(days=1))]
        suivi = []
        self.charger(evts, FauxGraphe(), progression=lambda f, t: suivi.append((f, t)))
        self.assertEqual(suivi, [(1, 2), (2, 2)])

    def test_la_fenetre_lue_est_celle_des_quinze_jours(self):
        fenetres = []

        def source(debut, fin):
            fenetres.append((debut, fin))
            return []

        it.charger(MAINTENANT, set(), sources=[("faux", source)],
                   obtenir_jeton=jeton_ok, graphe=FauxGraphe())
        self.assertEqual(fenetres, [it.periode(MAINTENANT)])


VTT = ("WEBVTT\n\n00:00:01.000 --> 00:00:04.000\n<v Alice Durand>Bonjour à tous."
       "</v>\n")
DEBUT = datetime.datetime(2026, 11, 9, 14, 0)  # noqa: DTZ001 — heure locale naïve


def ligne_a_importer(invites=("Alice Durand", "Bob Martin"), duree_min=45):
    return it.LigneReunion(DEBUT, DEBUT + datetime.timedelta(minutes=duree_min),
                           "Point projet", "A. Martin", LIEN, it.ETAT_DISPONIBLE,
                           "r1", "t1", it.identifiant_import(DEBUT, "t1"),
                           tuple(invites))


class TestParametresJob(unittest.TestCase):

    def test_champs_d_un_job_transcript_seul(self):
        params = it.parametres_job(ligne_a_importer(), "Texte du transcript",
                                   "notion")
        self.assertEqual(params["id"], it.identifiant_import(DEBUT, "t1"))
        self.assertEqual(params["wav_path"], "")
        self.assertEqual(params["status_audio"], "done")
        self.assertEqual(params["status_transcript"], "done")
        self.assertEqual(params["status_notion"], "pending")
        self.assertEqual(params["meeting_type"], "Teams")
        self.assertEqual(params["model_name"], "Teams")
        self.assertEqual(params["meeting_name"], "Point projet")
        self.assertEqual(params["start_time"], DEBUT)
        self.assertEqual(params["duration_min"], 45)
        self.assertEqual(params["transcript"],
                         "Participants : Alice Durand, Bob Martin\n\n"
                         "Texte du transcript")

    def test_mode_fichier_saute_notion(self):
        params = it.parametres_job(ligne_a_importer(), "x", "fichier")
        self.assertEqual(params["status_notion"], "skipped")
        self.assertEqual(params["output_mode"], "fichier")

    def test_sans_invite_pas_de_ligne_participants(self):
        params = it.parametres_job(ligne_a_importer(invites=()), "Texte", "notion")
        self.assertEqual(params["transcript"], "Texte")

    def test_jamais_d_adresse_email_dans_le_transcript(self):
        ligne = ligne_a_importer(invites=("a@exemple.fr", "Bob <b@exemple.fr>"))
        params = it.parametres_job(ligne, "Texte", "notion")
        self.assertNotIn("@", params["transcript"])

    def test_duree_en_minutes(self):
        self.assertEqual(it.duree_minutes(DEBUT, DEBUT.replace(hour=15, minute=30)),
                         90)
        self.assertEqual(it.duree_minutes(DEBUT, DEBUT), 0)
        self.assertEqual(it.duree_minutes(DEBUT, DEBUT - datetime.timedelta(hours=1)),
                         0)

    def test_les_parametres_construisent_un_job_a_l_id_stable(self):
        a = it.parametres_job(ligne_a_importer(), "x", "notion")
        b = it.parametres_job(ligne_a_importer(), "x", "notion")
        self.assertEqual(a["id"], b["id"])


class TestPreparerImports(unittest.TestCase):

    def test_lit_le_vtt_et_construit_le_job(self):
        graphe = FauxGraphe(vtt={("r1", "t1"): VTT})
        resultat = it.preparer_imports([ligne_a_importer()], "jeton", "notion", graphe)
        self.assertEqual(resultat.erreurs, [])
        self.assertEqual(len(resultat.jobs), 1)
        transcript_job = resultat.jobs[0]["transcript"]
        self.assertTrue(transcript_job.startswith("Participants : Alice Durand"))
        self.assertIn("Alice Durand : Bonjour à tous.", transcript_job)

    def test_une_erreur_graph_n_arrete_pas_les_autres_reunions(self):
        mauvaise = ligne_a_importer()._replace(
            objet="Réunion KO", reunion_id="r2", transcript_id="t2")
        erreur = graph_teams.ErreurGraph("accès refusé", 403)
        graphe = FauxGraphe(vtt={("r1", "t1"): VTT}, erreurs={("r2", "t2"): erreur})
        resultat = it.preparer_imports([mauvaise, ligne_a_importer()], "jeton",
                                       "notion", graphe)
        self.assertEqual(len(resultat.jobs), 1)
        self.assertEqual(resultat.erreurs, ["Réunion KO : accès refusé"])

    def test_une_ligne_de_journal_par_transcript_lu_sans_contenu(self):
        autre = ligne_a_importer()._replace(
            objet="Comité secret", reunion_id="r2", transcript_id="t2",
            job_id=it.identifiant_import(DEBUT, "t2"))
        graphe = FauxGraphe(vtt={("r1", "t1"): VTT, ("r2", "t2"): VTT})
        with self.assertLogs("meetnote", level="INFO") as capture:
            resultat = it.preparer_imports([ligne_a_importer(), autre], "jeton-secret",
                                           "notion", graphe)
        self.assertEqual(len(resultat.jobs), 2)
        for job in resultat.jobs:
            lignes = [m for m in capture.output if job["id"] in m]
            self.assertEqual(len(lignes), 1)
        interdits = ("Point projet", "Comité secret", "A. Martin", "Alice", "Bonjour",
                     "jeton-secret", LIEN, "r1", "r2")
        for message in capture.output:
            for mot in interdits:
                self.assertNotIn(mot, message)

    def test_echec_de_lecture_journalise_l_id_et_le_code_sans_message_brut(self):
        erreur = graph_teams.ErreurGraph("détail brut Graph", 403, "Forbidden")
        graphe = FauxGraphe(erreurs={("r1", "t1"): erreur})
        with self.assertLogs("meetnote", level="INFO") as capture:
            it.preparer_imports([ligne_a_importer()], "jeton", "notion", graphe)
        self.assertEqual(len(capture.output), 1)
        self.assertIn(it.identifiant_import(DEBUT, "t1"), capture.output[0])
        self.assertIn("Forbidden", capture.output[0])
        self.assertNotIn("détail brut", capture.output[0])
        self.assertNotIn("Point projet", capture.output[0])

    def test_transcript_vide_signale(self):
        graphe = FauxGraphe(vtt={("r1", "t1"): "WEBVTT\n"})
        resultat = it.preparer_imports([ligne_a_importer()], "jeton", "notion", graphe)
        self.assertEqual(resultat.jobs, [])
        self.assertEqual(resultat.erreurs, ["Point projet : le transcript est vide"])


if __name__ == "__main__":
    unittest.main()
