"""Tests de graph_teams : `requests.get` remplacé par une doublure (aucun réseau)."""
import datetime
import unittest
from unittest import mock

import graph_teams
import requests

JETON = "jeton-fictif-xyz"
REUNION = "MSo1N2Q4-fictif"
UTC = datetime.timezone.utc
DEBUT = datetime.datetime(2026, 11, 5, 14, 0, tzinfo=UTC)
FIN = datetime.datetime(2026, 11, 5, 15, 0, tzinfo=UTC)
VTT = "WEBVTT\n\n00:00:01.000 --> 00:00:03.000\nBonjour à tous\n"
URL_TRANSCRIPTS = (f"{graph_teams.URL_BASE_GRAPH}/me/onlineMeetings/{REUNION}"
                   "/transcripts")


class FausseReponse:
    """Imite requests.Response : statut, corps JSON ou texte."""

    def __init__(self, statut=200, json=None, texte=""):
        self.status_code = statut
        self.ok = statut < 400
        self._json = json
        self.text = texte
        self.encoding = None

    def json(self):
        if self._json is None:
            raise ValueError("pas de JSON")
        return self._json


def erreur(statut, code, message="Message de Graph à ne pas afficher"):
    return FausseReponse(statut, {"error": {"code": code, "message": message}})


def patch_get(*reponses):
    return mock.patch.object(graph_teams.requests, "get", side_effect=list(reponses))


class TestReunionEnLigne(unittest.TestCase):

    def test_resout_l_url_avec_le_filtre_odata(self):
        reponse = FausseReponse(json={"value": [{"id": REUNION}]})
        with patch_get(reponse) as get:
            self.assertEqual(
                graph_teams.reunion_en_ligne("https://t.example/join", JETON), REUNION)
        args, kwargs = get.call_args
        self.assertEqual(args[0], f"{graph_teams.URL_BASE_GRAPH}/me/onlineMeetings")
        self.assertEqual(kwargs["params"],
                         {"$filter": "JoinWebUrl eq 'https://t.example/join'"})
        self.assertEqual(kwargs["headers"]["Authorization"], f"Bearer {JETON}")
        self.assertEqual(kwargs["timeout"], graph_teams.DELAI_RESEAU_S)

    def test_apostrophes_doublees(self):
        with patch_get(FausseReponse(json={"value": []})) as get:
            graph_teams.reunion_en_ligne("https://t.example/o'brien?x='1'", JETON)
        filtre = get.call_args.kwargs["params"]["$filter"]
        self.assertEqual(filtre, "JoinWebUrl eq 'https://t.example/o''brien?x=''1'''")

    def test_inconnue_rend_none(self):
        with patch_get(FausseReponse(json={"value": []})):
            resultat = graph_teams.reunion_en_ligne("https://t.example/j", JETON)
        self.assertIsNone(resultat)

    def test_reponse_sans_json_est_une_erreur_claire(self):
        with (
            patch_get(FausseReponse(texte="<html>")),
            self.assertRaises(graph_teams.ErreurGraph) as ctx,
        ):
            graph_teams.reunion_en_ligne("https://t.example/j", JETON)
        self.assertIn("illisible", str(ctx.exception))


def transcript(identifiant, cree):
    return {"id": identifiant, "createdDateTime": cree}


class TestTranscripts(unittest.TestCase):

    def lister(self, valeurs, debut=DEBUT, fin=FIN, **corps_en_plus):
        with patch_get(FausseReponse(json={"value": valeurs, **corps_en_plus})) as get:
            resultat = graph_teams.transcripts(REUNION, debut, fin, JETON)
        return resultat, get

    def test_ne_garde_que_la_fenetre_de_l_occurrence(self):
        valeurs = [
            transcript("semaine-passee", "2026-10-29T14:02:11Z"),
            transcript("bonne", "2026-11-05T14:03:00.1234567Z"),
            transcript("semaine-suivante", "2026-11-12T14:01:00Z"),
        ]
        resultat, get = self.lister(valeurs)
        self.assertEqual([t["id"] for t in resultat], ["bonne"])
        self.assertEqual(get.call_args.args[0], URL_TRANSCRIPTS)

    def test_marge_avant_et_apres(self):
        marge = graph_teams.MARGE_FENETRE
        a_la_limite = (DEBUT - marge).strftime("%Y-%m-%dT%H:%M:%SZ")
        trop_tot = (DEBUT - marge - datetime.timedelta(seconds=1)).strftime(
            "%Y-%m-%dT%H:%M:%SZ")
        apres = (FIN + marge).strftime("%Y-%m-%dT%H:%M:%SZ")
        trop_tard = (FIN + marge + datetime.timedelta(seconds=1)).strftime(
            "%Y-%m-%dT%H:%M:%SZ")
        resultat, _ = self.lister([
            transcript("limite-basse", a_la_limite), transcript("trop-tot", trop_tot),
            transcript("limite-haute", apres), transcript("trop-tard", trop_tard)])
        self.assertEqual([t["id"] for t in resultat], ["limite-basse", "limite-haute"])

    def test_tri_chronologique(self):
        resultat, _ = self.lister([transcript("b", "2026-11-05T14:40:00Z"),
                                   transcript("a", "2026-11-05T14:10:00Z")])
        self.assertEqual([t["id"] for t in resultat], ["a", "b"])

    def test_dates_naives_lues_en_heure_locale(self):
        local = datetime.datetime.fromisoformat("2026-11-05T15:00:00")
        debut_utc = local.astimezone(UTC)
        cree = debut_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
        resultat, _ = self.lister([transcript("ok", cree)],
                                  debut=local, fin=local + datetime.timedelta(hours=1))
        self.assertEqual([t["id"] for t in resultat], ["ok"])

    def test_elements_malformes_ignores(self):
        resultat, _ = self.lister([
            {"id": "sans-date"}, {"createdDateTime": "2026-11-05T14:10:00Z"},
            transcript("date-absurde", "pas une date"), "pas un dict",
            transcript("ok", "2026-11-05T14:10:00")])
        self.assertEqual([t["id"] for t in resultat], ["ok"])

    def test_aucun_transcript(self):
        resultat, _ = self.lister([])
        self.assertEqual(resultat, [])

    def test_pagination_suivie_sur_graph_seulement(self):
        suite = f"{graph_teams.URL_BASE_GRAPH}/me/onlineMeetings/x/transcripts?$skip=1"
        page1 = FausseReponse(json={"value": [transcript("p1", "2026-11-05T14:10:00Z")],
                                    "@odata.nextLink": suite})
        page2 = FausseReponse(
            json={"value": [transcript("p2", "2026-11-05T14:20:00Z")]})
        with patch_get(page1, page2) as get:
            resultat = graph_teams.transcripts(REUNION, DEBUT, FIN, JETON)
        self.assertEqual([t["id"] for t in resultat], ["p1", "p2"])
        self.assertEqual(get.call_args.args[0], suite)

    def test_lien_de_page_hors_graph_non_suivi(self):
        page = FausseReponse(
            json={"value": [], "@odata.nextLink": "https://autre.test/x"})
        with patch_get(page) as get:
            graph_teams.transcripts(REUNION, DEBUT, FIN, JETON)
        self.assertEqual(get.call_count, 1)


class TestContenu(unittest.TestCase):

    def test_telecharge_le_vtt(self):
        with patch_get(FausseReponse(texte=VTT)) as get:
            self.assertEqual(graph_teams.contenu(REUNION, "T1", JETON), VTT)
        args, kwargs = get.call_args
        self.assertEqual(args[0], f"{URL_TRANSCRIPTS}/T1/content")
        self.assertEqual(kwargs["params"], {"$format": graph_teams.FORMAT_VTT})
        self.assertEqual(kwargs["headers"]["Accept"], graph_teams.FORMAT_VTT)

    def test_attribution_refusee_redemande_sans_attribution(self):
        refus = erreur(403, graph_teams.CODE_ATTRIBUTION_REFUSEE)
        with patch_get(refus, FausseReponse(texte=VTT)) as get:
            self.assertEqual(graph_teams.contenu(REUNION, "T1", JETON), VTT)
        self.assertEqual(get.call_count, 2)
        premier, second = (appel.kwargs["params"] for appel in get.call_args_list)
        self.assertEqual(premier, {"$format": graph_teams.FORMAT_VTT})
        self.assertEqual(second, {"$format": graph_teams.FORMAT_VTT,
                                  **graph_teams.PARAMETRE_SANS_ATTRIBUTION})

    def test_un_seul_nouvel_essai(self):
        refus = erreur(403, graph_teams.CODE_ATTRIBUTION_REFUSEE)
        with (
            patch_get(refus, refus) as get,
            self.assertRaises(graph_teams.ErreurGraph),
        ):
            graph_teams.contenu(REUNION, "T1", JETON)
        self.assertEqual(get.call_count, 2)

    def test_autre_403_sans_nouvel_essai(self):
        refus = erreur(403, graph_teams.CODE_TRANSCRIPTS_DESACTIVES)
        with patch_get(refus) as get, self.assertRaises(graph_teams.ErreurGraph):
            graph_teams.contenu(REUNION, "T1", JETON)
        self.assertEqual(get.call_count, 1)


class TestErreurs(unittest.TestCase):

    def echec(self, reponse):
        with patch_get(reponse), self.assertRaises(graph_teams.ErreurGraph) as ctx:
            graph_teams.contenu(REUNION, "T1", JETON)
        return ctx.exception

    def test_transcriptions_desactivees(self):
        exc = self.echec(erreur(403, graph_teams.CODE_TRANSCRIPTS_DESACTIVES))
        self.assertEqual(str(exc),
                         "accès aux transcriptions désactivé par l'admin Teams")
        self.assertEqual((exc.statut, exc.code),
                         (403, graph_teams.CODE_TRANSCRIPTS_DESACTIVES))

    def test_401_session_expiree(self):
        exc = self.echec(erreur(401, "InvalidAuthenticationToken"))
        self.assertIn("reconnectez-vous", str(exc))
        self.assertEqual(exc.statut, 401)

    def test_404_introuvable(self):
        exc = self.echec(erreur(404, "NotFound"))
        self.assertIn("introuvable", str(exc))

    def test_403_autre_garde_le_code(self):
        exc = self.echec(erreur(403, "Forbidden"))
        self.assertIn("accès refusé", str(exc))
        self.assertIn("(Forbidden)", str(exc))

    def test_statut_inattendu_corps_illisible(self):
        exc = self.echec(FausseReponse(502, texte="<html>Bad gateway</html>"))
        self.assertEqual(str(exc), "Microsoft Graph a répondu HTTP 502")

    def test_message_graph_et_jeton_jamais_repris(self):
        exc = self.echec(erreur(500, "InternalServerError"))
        self.assertNotIn("à ne pas afficher", str(exc))
        self.assertNotIn(JETON, str(exc))

    def test_corps_json_inattendu(self):
        exc = self.echec(FausseReponse(500, json=["pas", "un", "objet"]))
        self.assertEqual(str(exc), "Microsoft Graph a répondu HTTP 500")

    def test_erreur_reseau(self):
        pannes = (requests.ConnectionError("https://secret.test?jeton=zzz"),
                  requests.Timeout("délai"))
        for panne in pannes:
            with self.subTest(panne=type(panne).__name__):
                with (
                    mock.patch.object(graph_teams.requests, "get", side_effect=panne),
                    self.assertRaises(graph_teams.ErreurGraph) as ctx,
                ):
                    graph_teams.reunion_en_ligne("https://t.example/j", JETON)
                self.assertIn("injoignable", str(ctx.exception))
                self.assertNotIn("zzz", str(ctx.exception))
                self.assertIsNone(ctx.exception.statut)


if __name__ == "__main__":
    unittest.main()
