"""
Tests du cache d'agenda (source_agenda), avec une horloge fixe.

Lancement : py -3.12 pc\\test_source_agenda.py
"""
import datetime
import os
import sys
import threading
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import source_agenda
from source_agenda import CacheAgenda

MIN = datetime.timedelta(minutes=1)
SEC = datetime.timedelta(seconds=1)
T0 = datetime.datetime(2026, 10, 26, 9, 0)  # noqa: DTZ001 — heure locale naïve voulue


def reunion(objet: str, debut_min: int, duree_min: int = 30) -> dict:
    """Réunion au format de get_events_range, décalée de `debut_min` depuis T0."""
    debut = T0 + debut_min * MIN
    return {
        "subject": objet, "start": debut, "end": debut + duree_min * MIN,
        "location": "", "body": "", "organizer": "",
        "required": ["a@x.fr"], "optional": [], "is_recurring": False,
    }


def cache_charge(*reunions: dict) -> CacheAgenda:
    cache = CacheAgenda()
    cache.remplacer(list(reunions), T0)
    return cache


class TestDecisionRechargement(unittest.TestCase):

    def test_jamais_charge_premier(self):
        cache = CacheAgenda()
        self.assertTrue(cache.jamais_charge)
        self.assertEqual(cache.doit_recharger(T0), source_agenda.MOTIF_PREMIER)

    def test_apres_chargement_rien_a_faire(self):
        cache = cache_charge()
        self.assertFalse(cache.jamais_charge)
        self.assertIsNone(cache.doit_recharger(T0 + 1 * MIN))
        self.assertIsNone(cache.doit_recharger(T0 + 2 * MIN))

    def test_periodique_a_quinze_minutes(self):
        cache = cache_charge()
        # Un appel par minute, comme le tick de l'application : pas de saut d'horloge.
        motifs = [cache.doit_recharger(T0 + n * MIN) for n in range(1, 16)]
        self.assertEqual(motifs[:13], [None] * 13)
        self.assertIsNone(motifs[13])  # 14 min
        self.assertEqual(motifs[14], source_agenda.MOTIF_PERIODIQUE)  # 15 min

    def test_sortie_de_veille(self):
        cache = cache_charge()
        self.assertIsNone(cache.doit_recharger(T0 + 1 * MIN))
        # Sept minutes d'un coup : le poste dormait, sans que le cache soit périmé.
        self.assertEqual(cache.doit_recharger(T0 + 8 * MIN), source_agenda.MOTIF_VEILLE)

    def test_ecart_de_deux_minutes_pile_n_est_pas_une_veille(self):
        cache = cache_charge()
        cache.doit_recharger(T0 + 1 * MIN)
        self.assertIsNone(cache.doit_recharger(T0 + 3 * MIN))

    def test_veille_persiste_tant_que_non_reservee(self):
        cache = cache_charge()
        cache.doit_recharger(T0 + 1 * MIN)
        cache.doit_recharger(T0 + 8 * MIN)
        # Le tick suivant est normal, mais la veille n'a pas été traitée.
        self.assertEqual(cache.doit_recharger(T0 + 9 * MIN), source_agenda.MOTIF_VEILLE)
        self.assertTrue(cache.essayer_reserver_rechargement())
        self.assertIsNone(cache.doit_recharger(T0 + 10 * MIN))

    def test_horloge_revenue_en_arriere(self):
        cache = cache_charge()
        cache.doit_recharger(T0 + 1 * MIN)
        self.assertEqual(
            cache.doit_recharger(T0 - 30 * MIN), source_agenda.MOTIF_VEILLE)

    def test_demande_forcee(self):
        cache = cache_charge()
        self.assertIsNone(cache.doit_recharger(T0 + 1 * MIN))
        cache.forcer()
        self.assertEqual(cache.doit_recharger(T0 + 2 * MIN), source_agenda.MOTIF_FORCE)
        self.assertTrue(cache.essayer_reserver_rechargement())
        self.assertIsNone(cache.doit_recharger(T0 + 3 * MIN))

    def test_force_prime_sur_les_autres_motifs(self):
        cache = CacheAgenda()
        cache.forcer()
        self.assertEqual(cache.doit_recharger(T0), source_agenda.MOTIF_FORCE)

    def test_apres_echec_nouvelle_tentative_plus_tot_et_cache_conserve(self):
        cache = cache_charge(reunion("Point", 10))
        cache.marquer_echec(T0 + 15 * MIN)
        self.assertEqual(len(cache.reunions()), 1)
        self.assertIsNone(cache.doit_recharger(T0 + 16 * MIN))
        for n in range(17, 20):
            self.assertIsNone(cache.doit_recharger(T0 + n * MIN))
        self.assertEqual(
            cache.doit_recharger(T0 + 20 * MIN), source_agenda.MOTIF_PERIODIQUE)

    def test_echec_au_premier_chargement_reste_jamais_charge(self):
        cache = CacheAgenda()
        cache.marquer_echec(T0)
        self.assertTrue(cache.jamais_charge)
        self.assertIsNone(cache.doit_recharger(T0 + 10 * SEC))

    def test_echec_au_premier_chargement_reessaye_a_chaque_tick(self):
        # Poste démarré sans réseau : une réunion à +2 min ne doit pas attendre 5 min.
        cache = CacheAgenda()
        cache.marquer_echec(T0)
        self.assertEqual(cache.doit_recharger(T0 + 1 * MIN), source_agenda.MOTIF_PERIODIQUE)
        self.assertTrue(cache.essayer_reserver_rechargement())
        cache.liberer_rechargement()
        cache.marquer_echec(T0 + 1 * MIN)
        self.assertEqual(cache.doit_recharger(T0 + 2 * MIN), source_agenda.MOTIF_PERIODIQUE)

    def test_echec_avec_cache_existant_garde_cinq_minutes(self):
        cache = cache_charge(reunion("Point", 10))
        cache.marquer_echec(T0 + 15 * MIN)
        for n in range(16, 20):
            self.assertIsNone(cache.doit_recharger(T0 + n * MIN))
        self.assertEqual(cache.doit_recharger(T0 + 20 * MIN), source_agenda.MOTIF_PERIODIQUE)

    def test_succes_apres_echecs_revient_a_la_periode(self):
        cache = CacheAgenda()
        cache.marquer_echec(T0)
        cache.remplacer([], T0 + 1 * MIN)
        self.assertIsNone(cache.doit_recharger(T0 + 2 * MIN))


class TestUnSeulRechargement(unittest.TestCase):

    def test_deux_reservations_simultanees_refusees(self):
        cache = CacheAgenda()
        self.assertTrue(cache.essayer_reserver_rechargement())
        self.assertFalse(cache.essayer_reserver_rechargement())
        cache.liberer_rechargement()
        self.assertTrue(cache.essayer_reserver_rechargement())

    def test_force_demande_pendant_un_rechargement_n_est_pas_perdu(self):
        cache = cache_charge()
        self.assertTrue(cache.essayer_reserver_rechargement())
        cache.forcer()
        cache.remplacer([], T0 + 1 * MIN)
        cache.liberer_rechargement()
        self.assertEqual(cache.doit_recharger(T0 + 2 * MIN), source_agenda.MOTIF_FORCE)

    def test_rechargement_en_cours_distingue_de_l_echec(self):
        cache = CacheAgenda()
        self.assertFalse(cache.rechargement_en_cours)
        self.assertTrue(cache.essayer_reserver_rechargement())
        self.assertTrue(cache.rechargement_en_cours)
        # Un second appel de `recharger` rend None sans rien noter : ce n'est pas un échec.
        self.assertIsNone(source_agenda.recharger(
            cache, T0, source_agenda.MOTIF_PREMIER, [("ews", source_fixe([]))]))
        self.assertTrue(cache.jamais_charge)
        self.assertTrue(cache.rechargement_en_cours)
        self.assertEqual(cache.doit_recharger(T0 + 1 * MIN), source_agenda.MOTIF_PREMIER)
        cache.liberer_rechargement()
        self.assertFalse(cache.rechargement_en_cours)

    def test_un_seul_gagnant_entre_threads(self):
        cache = CacheAgenda()
        gagnants = []
        depart = threading.Barrier(8)

        def tenter():
            depart.wait()
            gagnants.append(cache.essayer_reserver_rechargement())

        fils = [threading.Thread(target=tenter) for _ in range(8)]
        for fil in fils:
            fil.start()
        for fil in fils:
            fil.join()
        self.assertEqual(gagnants.count(True), 1)


class TestContenu(unittest.TestCase):

    def test_remplacer_trie_et_copie(self):
        source = [reunion("B", 60), reunion("A", 10)]
        cache = cache_charge(*source)
        self.assertEqual([r["subject"] for r in cache.reunions()], ["A", "B"])
        source[0]["subject"] = "modifié après coup"
        self.assertEqual([r["subject"] for r in cache.reunions()], ["A", "B"])

    def test_la_lecture_rend_une_copie(self):
        cache = cache_charge(reunion("A", 10))
        cache.reunions()[0]["subject"] = "X"
        cache.reunions()[0]["required"].append("b@x.fr")
        lue = cache.reunions()[0]
        self.assertEqual(lue["subject"], "A")
        self.assertEqual(lue["required"], ["a@x.fr"])

    def test_remplacer_ecrase_l_ancien_contenu(self):
        cache = cache_charge(reunion("A", 10))
        cache.remplacer([], T0 + 15 * MIN)
        self.assertEqual(cache.reunions(), [])


class TestCalculs(unittest.TestCase):

    def test_reunions_en_cours_regle_de_get_all_current_meetings(self):
        cache = cache_charge(
            reunion("passee", -120, 30),        # finie depuis 90 min
            reunion("juste_finie", -30, 30),    # fin == maintenant : gardée
            reunion("en_cours", -10, 30),
            reunion("bientot", 30, 30),         # début == maintenant + 30 : gardée
            reunion("trop_tard", 31, 30),
        )
        noms = [r["subject"] for r in cache.reunions_en_cours(T0, 30)]
        self.assertEqual(noms, ["juste_finie", "en_cours", "bientot"])

    def test_reunions_en_cours_triees_par_debut_et_au_format_complet(self):
        cache = cache_charge(reunion("tard", 20), reunion("tot", -5))
        resultat = cache.reunions_en_cours(T0, 30)
        self.assertEqual([r["subject"] for r in resultat], ["tot", "tard"])
        self.assertEqual(resultat[0]["required"], ["a@x.fr"])

    def test_courante_ou_prochaine(self):
        cache = cache_charge(reunion("prochaine", 20), reunion("en_cours", -10))
        self.assertEqual(cache.courante_ou_prochaine(T0, 30)["subject"], "en_cours")

    def test_courante_ou_prochaine_aucune(self):
        cache = cache_charge(reunion("lointaine", 120))
        self.assertIsNone(cache.courante_ou_prochaine(T0, 30))
        self.assertIsNone(CacheAgenda().courante_ou_prochaine(T0, 30))

    def test_reunion_qui_commence_entre_deux_rechargements(self):
        """Chargée à 9 h00, la réunion de 9 h25 est vue à 9 h00 comme à 9 h10, sans
        nouveau chargement : la fenêtre de 30 min se déplace, pas le cache."""
        cache = cache_charge(reunion("Comité", 25))
        self.assertIsNone(cache.courante_ou_prochaine(T0 - 10 * MIN, 30))
        self.assertEqual(cache.courante_ou_prochaine(T0, 30)["subject"], "Comité")
        self.assertEqual(
            cache.courante_ou_prochaine(T0 + 10 * MIN, 30)["subject"], "Comité")
        self.assertIsNone(cache.doit_recharger(T0 + 10 * MIN))

    def test_suivante_apres(self):
        fin = T0 + 30 * MIN
        cache = cache_charge(
            reunion("courante", 0, 30),
            reunion("meme_objet", 35),
            reunion("suivante", 40),
            reunion("trop_loin", 50),
        )
        self.assertEqual(cache.suivante_apres(fin, 15, "courante")["subject"],
                         "meme_objet")
        self.assertEqual(
            cache.suivante_apres(fin, 15, "meme_objet")["subject"], "suivante")

    def test_suivante_apres_bornes(self):
        fin = T0 + 30 * MIN
        cache = cache_charge(
            reunion("avant_la_fin", 20),        # début 10 min avant la fin : écartée
            reunion("pile_a_la_fin", 30),       # écart 0 : gardée
            reunion("pile_15", 45),
        )
        self.assertEqual(cache.suivante_apres(fin, 15, "x")["subject"], "pile_a_la_fin")
        self.assertEqual(cache.suivante_apres(fin, 15, "pile_a_la_fin")["subject"],
                         "pile_15")
        self.assertIsNone(cache.suivante_apres(fin + 1 * MIN, 13, "x"))

    def test_suivante_apres_aucune(self):
        self.assertIsNone(CacheAgenda().suivante_apres(T0, 15, ""))


def source_fixe(reunions):
    """Source factice : rend toujours `reunions` (None = indisponible)."""
    appels = []

    def lire(debut, fin):
        appels.append((debut, fin))
        return reunions

    lire.appels = appels
    return lire


def source_en_erreur(exception):
    def lire(debut, fin):
        raise exception
    return lire


def horloge_fixe(*valeurs):
    """Horloge monotone factice : rend les valeurs données, dans l'ordre."""
    it = iter(valeurs)
    return lambda: next(it)


class TestPremiereSource(unittest.TestCase):

    def test_prend_la_premiere_qui_repond(self):
        ews = source_fixe([reunion("E", 10)])
        com = source_fixe([reunion("C", 10)])
        resultat = source_agenda.premiere_source(
            [("index", source_fixe(None)), ("ews", ews), ("com", com)], T0, T0 + MIN)
        self.assertEqual(resultat[0], "ews")
        self.assertEqual(resultat[1][0]["subject"], "E")
        self.assertEqual(com.appels, [])

    def test_liste_vide_est_une_reponse(self):
        resultat = source_agenda.premiere_source(
            [("ews", source_fixe([])), ("com", source_fixe([reunion("C", 1)]))], T0, T0)
        self.assertEqual(resultat, ("ews", []))

    def test_une_exception_vaut_indisponible_et_est_journalisee(self):
        sources = [("ews", source_en_erreur(RuntimeError("boum"))),
                   ("com", source_fixe([reunion("C", 1)]))]
        with self.assertLogs("meetnote.agenda", level="WARNING") as capture:
            resultat = source_agenda.premiere_source(sources, T0, T0)
        self.assertEqual(resultat[0], "com")
        self.assertEqual(len(capture.output), 1)
        self.assertIn("ews", capture.output[0])
        self.assertIn("boum", capture.output[0])

    def test_toutes_en_panne(self):
        sources = [("a", source_fixe(None)), ("b", source_en_erreur(OSError("x")))]
        with self.assertLogs("meetnote.agenda", level="WARNING"):
            self.assertIsNone(source_agenda.premiere_source(sources, T0, T0))

    def test_bornes_transmises_aux_sources(self):
        source = source_fixe([])
        source_agenda.premiere_source([("a", source)], T0, T0 + 5 * MIN)
        self.assertEqual(source.appels, [(T0, T0 + 5 * MIN)])


class TestChaineParDefaut(unittest.TestCase):

    def test_ordre_index_ews_com(self):
        sources = source_agenda.sources_par_defaut()
        self.assertEqual([nom for nom, _ in sources], ["index", "ews", "com"])

    def test_index_repond_none_jusqu_a_t021(self):
        nom, lire = source_agenda.sources_par_defaut()[0]
        self.assertEqual(nom, "index")
        self.assertIsNone(lire(T0, T0 + MIN))

    def test_ews_et_com_branches_sur_outlook_cal(self):
        with mock.patch.object(source_agenda.outlook_cal, "evenements_ews",
                               return_value=None) as ews, \
             mock.patch.object(source_agenda.outlook_cal, "evenements_com",
                               return_value=[reunion("C", 5)]) as com:
            nom, reunions = source_agenda.premiere_source(
                source_agenda.sources_par_defaut(), T0, T0 + MIN)
        self.assertEqual(nom, "com")
        self.assertEqual(len(reunions), 1)
        ews.assert_called_once_with(T0, T0 + MIN)
        com.assert_called_once_with(T0, T0 + MIN)


class TestRecharger(unittest.TestCase):

    def test_succes_remplace_le_cache_et_journalise(self):
        cache = cache_charge(reunion("ancienne", 5))
        ews = source_fixe([reunion("B", 60), reunion("A", 10)])
        sources = [("index", source_fixe(None)), ("ews", ews)]
        with self.assertLogs("meetnote.agenda", level="INFO") as capture:
            nom = source_agenda.recharger(
                cache, T0 + 15 * MIN, source_agenda.MOTIF_FORCE, sources,
                horloge_duree=horloge_fixe(10.0, 10.42))
        self.assertEqual(nom, "ews")
        self.assertEqual([r["subject"] for r in cache.reunions()], ["A", "B"])
        self.assertEqual(capture.output, [
            ("INFO:meetnote.agenda:[Agenda] rechargement motif=bouton source=ews "
             "réunions=2 durée=0.42s")])
        # Fenêtre lue : les 24 h à partir de maintenant.
        self.assertEqual(ews.appels, [(T0 + 15 * MIN,
                                       T0 + 15 * MIN + source_agenda.HORIZON_CACHE)])
        self.assertIsNone(cache.doit_recharger(T0 + 16 * MIN))

    def test_bascule_sur_com_quand_ews_panne(self):
        cache = CacheAgenda()
        sources = [("index", source_fixe(None)),
                   ("ews", source_en_erreur(RuntimeError("ews"))),
                   ("com", source_fixe([reunion("C", 10)]))]
        with self.assertLogs("meetnote.agenda", level="INFO") as capture:
            nom = source_agenda.recharger(
                cache, T0, source_agenda.MOTIF_PREMIER, sources)
        self.assertEqual(nom, "com")
        self.assertFalse(cache.jamais_charge)
        self.assertEqual(len(cache.reunions()), 1)
        lignes = [m for m in capture.output if "rechargement motif=" in m]
        self.assertEqual(len(lignes), 1)
        self.assertIn("motif=premier_chargement source=com réunions=1", lignes[0])

    def test_toutes_en_panne_garde_l_ancien_cache(self):
        cache = cache_charge(reunion("ancienne", 5))
        sources = [("index", source_fixe(None)), ("ews", source_fixe(None)),
                   ("com", source_en_erreur(OSError("outlook fermé")))]
        with self.assertLogs("meetnote.agenda", level="WARNING") as capture:
            nom = source_agenda.recharger(
                cache, T0 + 15 * MIN, source_agenda.MOTIF_PERIODIQUE, sources)
        self.assertIsNone(nom)
        self.assertEqual([r["subject"] for r in cache.reunions()], ["ancienne"])
        lignes = [m for m in capture.output if "rechargement motif=" in m]
        self.assertEqual(len(lignes), 1)
        self.assertTrue(lignes[0].startswith("WARNING:"))
        self.assertIn("motif=périodique source=aucune", lignes[0])
        # L'échec est noté : nouvelle tentative après 5 min, pas 15.
        for n in range(16, 20):
            self.assertIsNone(cache.doit_recharger(T0 + n * MIN))
        self.assertEqual(cache.doit_recharger(T0 + 20 * MIN),
                         source_agenda.MOTIF_PERIODIQUE)

    def test_premier_chargement_en_echec_reste_jamais_charge(self):
        cache = CacheAgenda()
        with self.assertLogs("meetnote.agenda", level="WARNING"):
            source_agenda.recharger(cache, T0, source_agenda.MOTIF_PREMIER,
                                    [("ews", source_fixe(None))])
        self.assertTrue(cache.jamais_charge)
        self.assertEqual(cache.reunions(), [])

    def test_libelles_des_motifs(self):
        attendus = {source_agenda.MOTIF_PERIODIQUE: "motif=périodique",
                    source_agenda.MOTIF_VEILLE: "motif=veille",
                    source_agenda.MOTIF_FORCE: "motif=bouton",
                    source_agenda.MOTIF_PREMIER: "motif=premier_chargement"}
        for motif, texte in attendus.items():
            with self.subTest(motif=motif), \
                 self.assertLogs("meetnote.agenda", level="INFO") as capture:
                source_agenda.recharger(CacheAgenda(), T0, motif,
                                        [("ews", source_fixe([]))])
            self.assertIn(texte, capture.output[0])

    def test_reservation_liberee_apres_succes_et_apres_exception(self):
        cache = CacheAgenda()
        with self.assertLogs("meetnote.agenda", level="INFO"):
            source_agenda.recharger(cache, T0, source_agenda.MOTIF_PREMIER,
                                    [("ews", source_fixe([]))])
        self.assertTrue(cache.essayer_reserver_rechargement())
        cache.liberer_rechargement()

        def horloge_cassee():
            raise RuntimeError("horloge")

        with self.assertRaises(RuntimeError):
            source_agenda.recharger(cache, T0, source_agenda.MOTIF_PREMIER,
                                    [("ews", source_fixe([]))],
                                    horloge_duree=horloge_cassee)
        self.assertTrue(cache.essayer_reserver_rechargement())

    def test_ne_fait_rien_si_un_rechargement_est_deja_en_cours(self):
        cache = cache_charge(reunion("ancienne", 5))
        source = source_fixe([reunion("nouvelle", 5)])
        self.assertTrue(cache.essayer_reserver_rechargement())
        with self.assertNoLogs("meetnote.agenda", level="DEBUG"):
            nom = source_agenda.recharger(cache, T0 + 15 * MIN,
                                          source_agenda.MOTIF_PERIODIQUE,
                                          [("ews", source)])
        self.assertIsNone(nom)
        self.assertEqual(source.appels, [])
        self.assertEqual([r["subject"] for r in cache.reunions()], ["ancienne"])
        # La réservation de l'autre rechargement n'a pas été libérée par erreur.
        self.assertFalse(cache.essayer_reserver_rechargement())

    def test_chaine_par_defaut_utilisee_sans_sources(self):
        cache = CacheAgenda()
        with mock.patch.object(source_agenda.outlook_cal, "evenements_ews",
                               return_value=[reunion("E", 5)]), \
             self.assertLogs("meetnote.agenda", level="INFO"):
            nom = source_agenda.recharger(cache, T0, source_agenda.MOTIF_PREMIER)
        self.assertEqual(nom, "ews")


class TestParticipants(unittest.TestCase):

    def test_fusion_ordre_et_doublons(self):
        fusion = source_agenda.fusionner_participants(
            ["Alice Martin", "bob durand"], ["Chloé  Petit"],
            ["BOB DURAND", "chloe petit", "Dan Roux"])
        self.assertEqual(fusion, ["Alice Martin", "bob durand", "Chloé Petit",
                                  "Dan Roux"])

    def test_fusion_insensible_aux_accents_et_garde_premiere_graphie(self):
        self.assertEqual(
            source_agenda.fusionner_participants(["Éloïse"], ["eloise", "ELOISE"]),
            ["Éloïse"])

    def test_fusion_ignore_vides_non_chaines_et_none(self):
        self.assertEqual(
            source_agenda.fusionner_participants(
                ["", "   ", None, 3, {"name": "x"}, "Zoé"], None, []),
            ["Zoé"])

    def test_invites_de_reunion_requis_puis_facultatifs(self):
        r = reunion("R", 0)
        r["required"] = ["Req 1", "Req 2"]
        r["optional"] = ["Opt 1", "req 1"]
        self.assertEqual(source_agenda.invites_de_reunion(r),
                         ["Req 1", "Req 2", "Opt 1"])

    def test_invites_de_reunion_sans_listes(self):
        self.assertEqual(source_agenda.invites_de_reunion({"subject": "R"}), [])

    def test_invites_pour_nom_identique_ou_renomme(self):
        invites = ["Alice"]
        self.assertEqual(source_agenda.invites_pour_nom("Point  Équipe", invites,
                                                        "point equipe"), invites)
        self.assertEqual(source_agenda.invites_pour_nom("Point équipe", invites,
                                                        "Autre titre"), [])
        self.assertEqual(
            source_agenda.invites_pour_nom("Point équipe", invites, ""), [])

    def test_choisir_reunion_prend_celle_dont_l_objet_correspond(self):
        # Réunion courte chevauchée par une longue : la première par début n'est pas
        # celle que l'utilisateur a choisie dans la fenêtre de conflit.
        longue, courte = reunion("Séminaire", 0, 120), reunion("Point  flash", 10, 15)
        self.assertIs(source_agenda.choisir_reunion([longue, courte], "point FLASH"), courte)

    def test_choisir_reunion_repli_sur_la_premiere(self):
        a, b = reunion("A", 0), reunion("B", 5)
        self.assertIs(source_agenda.choisir_reunion([a, b], ""), a)
        self.assertIs(source_agenda.choisir_reunion([a, b], "Titre libre"), a)
        self.assertIsNone(source_agenda.choisir_reunion([], "A"))

    def test_completer_preremplissage_demarrage_sans_cache(self):
        r = reunion("Point", 0)
        r["required"], r["optional"] = ["Alice"], ["Bob"]
        res = source_agenda.completer_preremplissage([r], "", "", None, None)
        self.assertEqual(res, ("Point", r["end"], ("Point", ["Alice", "Bob"])))

    def test_completer_preremplissage_ne_remplace_pas_un_choix_utilisateur(self):
        r = reunion("Point", 0)
        fin = T0 + 99 * MIN
        res = source_agenda.completer_preremplissage(
            [r], "Mon titre", "Ancien auto", fin, ("Autre", ["Zoé"]))
        self.assertEqual(res, (None, None, None))

    def test_completer_preremplissage_nom_egal_au_dernier_auto(self):
        res = source_agenda.completer_preremplissage(
            [reunion("Point", 0), reunion("Autre", 5)], "Autre", "Autre", None, None)
        self.assertEqual(res.nom, "Autre")
        self.assertEqual(res.invites[0], "Autre")

    def test_completer_preremplissage_sans_reunion(self):
        self.assertEqual(
            source_agenda.completer_preremplissage([], "", "", None, None),
            (None, None, None))

    def test_fusion_ecarte_les_adresses_et_garde_les_noms(self):
        self.assertEqual(
            source_agenda.fusionner_participants(
                ["jean.dupont@externe.fr", "Dupont, Jean"], ["<a@b.fr>", " x@y.z "]),
            ["Dupont, Jean"])

    def test_fusion_nom_avec_adresse_entre_chevrons_garde_le_nom(self):
        self.assertEqual(
            source_agenda.fusionner_participants(
                ["Jean Dupont <jean.dupont@externe.fr>"], ["jean dupont"]),
            ["Jean Dupont"])

    def test_invites_de_reunion_ecarte_les_adresses(self):
        reunion = {"required": ["jean.dupont@externe.fr", "Dupont, Jean"],
                   "optional": ["marie@externe.fr", "Martin, Marie"]}
        self.assertEqual(source_agenda.invites_de_reunion(reunion),
                         ["Dupont, Jean", "Martin, Marie"])

    def test_ligne_participants_sans_adresse_ni_noms_teams_en_adresse(self):
        fusion = source_agenda.fusionner_participants(
            ["jean.dupont@externe.fr", "Dupont, Jean"], ["expediteur@externe.fr"])
        self.assertEqual(source_agenda.ligne_participants(fusion),
                         "Participants : Dupont, Jean")
        self.assertEqual(
            source_agenda.avec_ligne_participants("Bonjour", ["a@b.fr"]), "Bonjour")

    def test_ligne_participants(self):
        self.assertEqual(source_agenda.ligne_participants(["A", "a", "B"]),
                         "Participants : A, B")
        self.assertEqual(source_agenda.ligne_participants([]), "")

    def test_avec_ligne_participants_en_tete(self):
        self.assertEqual(source_agenda.avec_ligne_participants("Bonjour", ["A", "B"]),
                         "Participants : A, B\n\nBonjour")

    def test_avec_ligne_participants_sans_participant(self):
        self.assertEqual(source_agenda.avec_ligne_participants("Bonjour", []),
                         "Bonjour")

    def test_avec_ligne_participants_jamais_deux_fois(self):
        une_fois = source_agenda.avec_ligne_participants("Bonjour", ["A"])
        self.assertEqual(source_agenda.avec_ligne_participants(une_fois, ["A"]),
                         une_fois)


if __name__ == "__main__":
    unittest.main()
