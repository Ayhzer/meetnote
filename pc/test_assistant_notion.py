"""
Tests du schema Notion de l'assistant (assistant_notion), sans reseau.

Lancement : py -3.12 pc\\test_assistant_notion.py
"""
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import assistant_notion as an
from notion_api import NOTION_VERSION

HEX = "0123456789abcdef0123456789abcdef"
UUID = "01234567-89ab-cdef-0123-456789abcdef"
HEX_VUE = "fedcba9876543210fedcba9876543210"


def _schema_conforme() -> dict:
    """Schema tel que l'API le renverrait pour une base conforme."""
    return {
        nom: {"id": f"id{i}", "type": type_notion, type_notion: {}}
        for i, (nom, type_notion) in enumerate(an.PROPRIETES_ATTENDUES.items())
    }


class TestExtraireIdentifiant(unittest.TestCase):

    def test_identifiant_brut(self):
        self.assertEqual(an.extraire_identifiant(HEX), UUID)

    def test_uuid_avec_tirets(self):
        self.assertEqual(an.extraire_identifiant(UUID), UUID)

    def test_majuscules_rendues_en_minuscules(self):
        self.assertEqual(an.extraire_identifiant(HEX.upper()), UUID)

    def test_espaces_autour_toleres(self):
        self.assertEqual(an.extraire_identifiant(f"  \n{HEX}\t "), UUID)

    def test_url_sans_titre(self):
        lien = f"https://www.notion.so/{HEX}"
        self.assertEqual(an.extraire_identifiant(lien), UUID)

    def test_url_avec_espace_et_titre(self):
        lien = f"https://www.notion.so/monespace/Mes-reunions-{HEX}"
        self.assertEqual(an.extraire_identifiant(lien), UUID)

    def test_base_en_tableau_prend_le_chemin_pas_la_vue(self):
        lien = f"https://www.notion.so/monespace/{HEX}?v={HEX_VUE}"
        self.assertEqual(an.extraire_identifiant(lien), UUID)

    def test_parametre_pvs_et_ancre_ignores(self):
        lien = f"https://www.notion.so/Titre-{HEX}?pvs=4#abcdef0123456789abcdef0123456789"
        self.assertEqual(an.extraire_identifiant(lien), UUID)

    def test_notion_site(self):
        lien = f"https://monespace.notion.site/Transcriptions-{HEX}?v={HEX_VUE}"
        self.assertEqual(an.extraire_identifiant(lien), UUID)

    def test_url_avec_slash_final(self):
        lien = f"https://www.notion.so/Titre-{HEX}/"
        self.assertEqual(an.extraire_identifiant(lien), UUID)

    def test_url_sans_schema(self):
        lien = f"notion.so/Titre-{HEX}?v={HEX_VUE}"
        self.assertEqual(an.extraire_identifiant(lien), UUID)

    def test_uuid_avec_tirets_dans_l_url(self):
        lien = f"https://www.notion.so/monespace/Titre-{UUID}"
        self.assertEqual(an.extraire_identifiant(lien), UUID)

    def test_invalides(self):
        for lien in (
            "",
            "   ",
            "n'importe quoi",
            "https://www.notion.so/monespace/Titre-sans-identifiant",
            "https://www.notion.so/",
            f"https://exemple.com/Titre-{HEX}",
            f"https://notion.so.pirate.fr/{HEX}",
            HEX + "0",
            HEX[:-1],
            "zz" + HEX[2:],
            f"https://www.notion.so/Titre-{HEX}0",
        ):
            with self.subTest(lien=lien):
                self.assertIsNone(an.extraire_identifiant(lien))

    def test_entree_non_texte(self):
        self.assertIsNone(an.extraire_identifiant(None))
        self.assertIsNone(an.extraire_identifiant(42))


class TestProprietesCreation(unittest.TestCase):

    def setUp(self):
        self.props = an.proprietes_creation()

    def test_coherent_avec_la_constante(self):
        self.assertEqual(list(self.props), list(an.PROPRIETES_ATTENDUES))
        for nom, type_notion in an.PROPRIETES_ATTENDUES.items():
            with self.subTest(nom=nom):
                self.assertEqual(list(self.props[nom]), [type_notion])

    def test_couvre_les_proprietes_ecrites_par_notion_push(self):
        # Liste copiee de `props` dans notion_push.push_to_notion.
        ecrites = {
            "Titre", "Date", "Source", "Statut", "Participants", "Type",
            "Durée (min)", "Modèle Whisper", "Files & media",
        }
        self.assertEqual(set(self.props), ecrites)

    def test_formes_de_l_api(self):
        self.assertEqual(self.props["Titre"], {"title": {}})
        self.assertEqual(self.props["Date"], {"date": {}})
        self.assertEqual(self.props["Durée (min)"], {"number": {"format": "number"}})
        self.assertEqual(self.props["Files & media"], {"files": {}})
        self.assertEqual(self.props["Type"], {"select": {}})

    def test_options_des_listes(self):
        def noms(prop):
            return [o["name"] for o in self.props[prop]["select"]["options"]]

        self.assertEqual(noms("Source"), ["PC", "Mobile", "Agenda"])
        self.assertEqual(noms("Statut"), ["À traiter", "Traité"])

    def test_ne_partage_pas_d_etat_entre_appels(self):
        self.props["Titre"]["title"]["x"] = 1
        self.assertEqual(an.proprietes_creation()["Titre"], {"title": {}})


class TestComparerSchema(unittest.TestCase):

    def test_base_conforme(self):
        self.assertEqual(an.comparer_schema(_schema_conforme()), [])

    def test_schema_cree_par_l_assistant_est_conforme(self):
        lu = {
            nom: {"id": "x", "type": next(iter(definition))}
            for nom, definition in an.proprietes_creation().items()
        }
        self.assertEqual(an.comparer_schema(lu), [])

    def test_propriete_manquante(self):
        lu = _schema_conforme()
        del lu["Durée (min)"]
        self.assertEqual(
            an.comparer_schema(lu), ["Propriété manquante : Durée (min) (nombre)"]
        )

    def test_mauvais_type(self):
        lu = _schema_conforme()
        lu["Source"]["type"] = "rich_text"
        self.assertEqual(
            an.comparer_schema(lu),
            ["Mauvais type pour Source : texte au lieu de liste de choix"],
        )

    def test_participants_select_ou_multi_select_acceptes(self):
        for type_notion in ("select", "multi_select"):
            with self.subTest(type=type_notion):
                lu = _schema_conforme()
                lu["Participants"] = {"id": "p", "type": type_notion, type_notion: {}}
                self.assertEqual(an.comparer_schema(lu), [])

    def test_participants_mauvais_type_cite_les_types_acceptes(self):
        lu = _schema_conforme()
        lu["Participants"] = {"id": "p", "type": "rich_text", "rich_text": {}}
        self.assertEqual(
            an.comparer_schema(lu),
            [
                (
                    "Mauvais type pour Participants : texte au lieu de "
                    "liste de choix ou liste à choix multiples"
                )
            ],
        )

    def test_multi_select_refuse_pour_les_autres_listes(self):
        lu = _schema_conforme()
        lu["Source"]["type"] = "multi_select"
        self.assertEqual(
            an.comparer_schema(lu),
            [
                (
                    "Mauvais type pour Source : liste à choix multiples "
                    "au lieu de liste de choix"
                )
            ],
        )

    def test_creation_garde_select_pour_participants(self):
        self.assertEqual(an.proprietes_creation()["Participants"], {"select": {}})

    def test_type_inconnu_affiche_brut(self):
        lu = _schema_conforme()
        lu["Date"]["type"] = "type_futur"
        self.assertEqual(
            an.comparer_schema(lu),
            ["Mauvais type pour Date : type_futur au lieu de date"],
        )

    def test_titre_renomme(self):
        lu = _schema_conforme()
        lu["Name"] = lu.pop("Titre")
        self.assertEqual(
            an.comparer_schema(lu),
            ['La colonne titre s\'appelle "Name" au lieu de "Titre"'],
        )

    def test_titre_renomme_et_colonne_titre_de_mauvais_type(self):
        # Une colonne « Titre » existe mais en texte : le vrai titre est « Name ».
        lu = _schema_conforme()
        lu["Name"] = lu["Titre"]
        lu["Titre"] = {"id": "z", "type": "rich_text", "rich_text": {}}
        self.assertEqual(
            an.comparer_schema(lu),
            ['La colonne titre s\'appelle "Name" au lieu de "Titre"'],
        )

    def test_aucune_colonne_titre(self):
        lu = _schema_conforme()
        del lu["Titre"]
        self.assertEqual(
            an.comparer_schema(lu), ["Propriété manquante : Titre (titre)"]
        )

    def test_proprietes_en_trop_ignorees(self):
        lu = _schema_conforme()
        lu["Priorité"] = {"id": "p", "type": "checkbox", "checkbox": {}}
        lu["Notes"] = {"id": "n", "type": "rich_text", "rich_text": {}}
        self.assertEqual(an.comparer_schema(lu), [])

    def test_options_de_liste_manquantes_ne_sont_pas_une_erreur(self):
        lu = _schema_conforme()
        lu["Source"]["select"] = {"options": []}
        lu["Statut"]["select"] = {"options": [{"name": "Autre"}]}
        self.assertEqual(an.comparer_schema(lu), [])

    def test_plusieurs_constats_dans_l_ordre_du_schema(self):
        lu = _schema_conforme()
        del lu["Date"]
        lu["Statut"]["type"] = "status"
        del lu["Files & media"]
        self.assertEqual(
            an.comparer_schema(lu),
            [
                "Propriété manquante : Date (date)",
                "Mauvais type pour Statut : statut au lieu de liste de choix",
                "Propriété manquante : Files & media (fichiers)",
            ],
        )

    def test_base_vide(self):
        constats = an.comparer_schema({})
        self.assertEqual(len(constats), len(an.PROPRIETES_ATTENDUES))
        self.assertTrue(all(c.startswith("Propriété manquante") for c in constats))


JETON = "jeton_factice_ne_doit_jamais_fuiter"
BASE_ID = "11111111-1111-1111-1111-111111111111"
SOURCE_ID = "22222222-2222-2222-2222-222222222222"
PAGE_ID = "33333333-3333-3333-3333-333333333333"
API = "https://api.notion.com/v1"


def _reponse(statut: int = 200, corps=None) -> MagicMock:
    """Fausse reponse `requests` : statut, ok et json()."""
    reponse = MagicMock()
    reponse.status_code = statut
    reponse.ok = statut < 400
    reponse.json.return_value = {} if corps is None else corps
    return reponse


def _erreur(
    statut: int, code: str = "", message: str = "detail de Notion"
) -> MagicMock:
    return _reponse(statut, {"object": "error", "code": code, "message": message})


def _base_lue(sources=None) -> dict:
    if sources is None:
        sources = [{"id": SOURCE_ID, "name": "Transcriptions"}]
    return {"object": "database", "id": BASE_ID, "data_sources": sources}


def _levee(test, appel, *args) -> an.ErreurNotion:
    """Appelle `appel(*args)`, exige une ErreurNotion et la rend."""
    with test.assertRaises(an.ErreurNotion) as ctx:
        appel(*args)
    return ctx.exception


class TestEntetes(unittest.TestCase):

    def test_bearer_et_version_partagee(self):
        entetes = an._entetes(JETON)
        self.assertEqual(entetes["Authorization"], f"Bearer {JETON}")
        self.assertEqual(entetes["Notion-Version"], NOTION_VERSION)
        self.assertEqual(entetes["Content-Type"], "application/json")

    def test_version_unique_avec_notion_push(self):
        # notion_push lit la meme constante : une seule valeur a maintenir.
        dossier = os.path.dirname(os.path.abspath(__file__))
        chemin = os.path.join(dossier, "notion_push.py")
        with open(chemin, encoding="utf-8") as fichier:
            source = fichier.read()
        self.assertIn("from notion_api import NOTION_API, NOTION_VERSION", source)
        self.assertEqual(an.NOTION_VERSION, "2026-03-11")


class TestCreerBase(unittest.TestCase):

    def _creer(self, **kwargs):
        reponse = _reponse(
            200,
            {
                "object": "database",
                "id": BASE_ID,
                "url": "https://www.notion.so/base",
                "data_sources": [{"id": SOURCE_ID, "name": "Transcriptions"}],
            },
        )
        with patch("assistant_notion.requests.post", return_value=reponse) as post:
            resultat = an.creer_base(JETON, PAGE_ID, **kwargs)
        return resultat, post

    def test_corps_envoye(self):
        _, post = self._creer()
        self.assertEqual(post.call_args.args[0], f"{API}/databases")
        self.assertEqual(
            post.call_args.kwargs["json"],
            {
                "parent": {"type": "page_id", "page_id": PAGE_ID},
                "title": [{"type": "text", "text": {"content": "Transcriptions"}}],
                "initial_data_source": {"properties": an.proprietes_creation()},
            },
        )

    def test_titre_personnalise(self):
        _, post = self._creer(titre="Mes réunions")
        titre = post.call_args.kwargs["json"]["title"]
        self.assertEqual(titre[0]["text"]["content"], "Mes réunions")

    def test_entetes_delai(self):
        _, post = self._creer()
        self.assertEqual(post.call_args.kwargs["headers"], an._entetes(JETON))
        self.assertEqual(post.call_args.kwargs["timeout"], 30)

    def test_identifiants_rendus(self):
        resultat, _ = self._creer()
        self.assertEqual(resultat.base_id, BASE_ID)
        self.assertEqual(resultat.source_id, SOURCE_ID)
        self.assertEqual(resultat.url, "https://www.notion.so/base")

    def test_sans_source_dans_la_reponse(self):
        reponse = _reponse(200, {"object": "database", "id": BASE_ID})
        with patch("assistant_notion.requests.post", return_value=reponse):
            resultat = an.creer_base(JETON, PAGE_ID)
        self.assertEqual(resultat.base_id, BASE_ID)
        self.assertIsNone(resultat.source_id)

    def test_reponse_sans_identifiant(self):
        with patch("assistant_notion.requests.post", return_value=_reponse(200, {})):
            erreur = _levee(self, an.creer_base, JETON, PAGE_ID)
        self.assertEqual(erreur.message, an.MSG_REPONSE_ILLISIBLE)


class TestLireSchema(unittest.TestCase):

    def test_lecture_en_deux_temps(self):
        proprietes = {"Titre": {"id": "t", "type": "title", "title": {}}}
        schema = _reponse(200, {"properties": proprietes})
        reponses = [_reponse(200, _base_lue()), schema]
        with patch("assistant_notion.requests.get", side_effect=reponses) as get:
            lu = an.lire_schema(JETON, BASE_ID)
        self.assertEqual(lu, proprietes)
        urls = [appel.args[0] for appel in get.call_args_list]
        self.assertEqual(
            urls, [f"{API}/databases/{BASE_ID}", f"{API}/data_sources/{SOURCE_ID}"]
        )
        for appel in get.call_args_list:
            self.assertEqual(appel.kwargs["headers"], an._entetes(JETON))
            self.assertEqual(appel.kwargs["timeout"], 30)
            self.assertNotIn("json", appel.kwargs)

    def test_plusieurs_sources_lit_la_premiere(self):
        autre = "44444444-4444-4444-4444-444444444444"
        base = _base_lue([{"id": SOURCE_ID, "name": "A"}, {"id": autre, "name": "B"}])
        reponses = [_reponse(200, base), _reponse(200, {"properties": {}})]
        with patch("assistant_notion.requests.get", side_effect=reponses) as get:
            an.lire_schema(JETON, BASE_ID)
        url_source = get.call_args_list[1].args[0]
        self.assertEqual(url_source, f"{API}/data_sources/{SOURCE_ID}")

    def test_resultat_compatible_avec_comparer_schema(self):
        proprietes = {
            nom: {"id": nom, "type": type_notion}
            for nom, type_notion in an.PROPRIETES_ATTENDUES.items()
        }
        schema = _reponse(200, {"properties": proprietes})
        reponses = [_reponse(200, _base_lue()), schema]
        with patch("assistant_notion.requests.get", side_effect=reponses):
            lu = an.lire_schema(JETON, BASE_ID)
        self.assertEqual(an.comparer_schema(lu), [])

    def test_base_sans_source_de_donnees(self):
        sans_source = _reponse(200, _base_lue([]))
        with patch("assistant_notion.requests.get", return_value=sans_source):
            erreur = _levee(self, an.lire_schema, JETON, BASE_ID)
        self.assertEqual(erreur.message, an.MSG_SANS_SOURCE)

    def test_source_sans_proprietes(self):
        reponses = [_reponse(200, _base_lue()), _reponse(200, {})]
        with patch("assistant_notion.requests.get", side_effect=reponses):
            erreur = _levee(self, an.lire_schema, JETON, BASE_ID)
        self.assertEqual(erreur.message, an.MSG_REPONSE_ILLISIBLE)

    def test_erreur_a_la_premiere_requete(self):
        introuvable = _erreur(404, "object_not_found")
        with patch("assistant_notion.requests.get", return_value=introuvable):
            erreur = _levee(self, an.lire_schema, JETON, BASE_ID)
        self.assertEqual(erreur.message, an.MSG_INTROUVABLE)


class TestNatureLien(unittest.TestCase):

    def test_base(self):
        base = _reponse(200, _base_lue())
        with patch("assistant_notion.requests.get", return_value=base) as get:
            self.assertEqual(an.nature_lien(JETON, BASE_ID), "base")
        self.assertEqual(get.call_count, 1)
        self.assertEqual(get.call_args.args[0], f"{API}/databases/{BASE_ID}")

    def test_page_apres_400_validation(self):
        reponses = [_erreur(400, "validation_error"), _reponse(200, {"object": "page"})]
        with patch("assistant_notion.requests.get", side_effect=reponses) as get:
            self.assertEqual(an.nature_lien(JETON, PAGE_ID), "page")
        self.assertEqual(get.call_args_list[1].args[0], f"{API}/pages/{PAGE_ID}")

    def test_page_apres_404(self):
        reponses = [_erreur(404, "object_not_found"), _reponse(200, {"object": "page"})]
        with patch("assistant_notion.requests.get", side_effect=reponses):
            self.assertEqual(an.nature_lien(JETON, PAGE_ID), "page")

    def test_ni_base_ni_page_accessible(self):
        reponses = [_erreur(404, "object_not_found"), _erreur(404, "object_not_found")]
        with patch("assistant_notion.requests.get", side_effect=reponses):
            erreur = _levee(self, an.nature_lien, JETON, PAGE_ID)
        self.assertEqual(erreur.message, an.MSG_INTROUVABLE)

    def test_jeton_refuse_remonte_sans_second_essai(self):
        refuse = _erreur(401, "unauthorized")
        with patch("assistant_notion.requests.get", return_value=refuse) as get:
            erreur = _levee(self, an.nature_lien, JETON, PAGE_ID)
        self.assertEqual(erreur.message, an.MSG_JETON_REFUSE)
        self.assertEqual(get.call_count, 1)

    def test_reseau_remonte_sans_second_essai(self):
        panne = requests.ConnectionError("x")
        with patch("assistant_notion.requests.get", side_effect=panne) as get:
            erreur = _levee(self, an.nature_lien, JETON, PAGE_ID)
        self.assertEqual(erreur.message, an.MSG_RESEAU)
        self.assertEqual(get.call_count, 1)


class TestEnvoyerPageTest(unittest.TestCase):

    def _envoyer(self):
        reponse = _reponse(200, {"object": "page", "url": "https://www.notion.so/page-test"})
        with patch("assistant_notion._date_du_jour", return_value="2026-10-15"), \
                patch("assistant_notion.requests.post", return_value=reponse) as post:
            url = an.envoyer_page_test(JETON, BASE_ID)
        return url, post

    def test_url_rendue(self):
        url, _ = self._envoyer()
        self.assertEqual(url, "https://www.notion.so/page-test")

    def test_corps_envoye(self):
        _, post = self._envoyer()
        self.assertEqual(post.call_args.args[0], f"{API}/pages")
        corps = post.call_args.kwargs["json"]
        self.assertEqual(corps["parent"], {"database_id": BASE_ID})
        self.assertEqual(
            corps["properties"],
            {
                "Titre": {
                    "title": [{"text": {"content": "Test d'installation MeetNote"}}]
                },
                "Date": {"date": {"start": "2026-10-15"}},
                "Source": {"select": {"name": "PC"}},
                "Statut": {"select": {"name": "À traiter"}},
            },
        )

    def test_paragraphe_explicatif(self):
        _, post = self._envoyer()
        (bloc,) = post.call_args.kwargs["json"]["children"]
        self.assertEqual(bloc["type"], "paragraph")
        texte = bloc["paragraph"]["rich_text"][0]["text"]["content"]
        self.assertIn("assistant", texte)
        self.assertIn("supprimer", texte)

    def test_proprietes_ecrites_existent_dans_le_schema(self):
        _, post = self._envoyer()
        for nom in post.call_args.kwargs["json"]["properties"]:
            self.assertIn(nom, an.PROPRIETES_ATTENDUES)

    def test_entetes(self):
        _, post = self._envoyer()
        self.assertEqual(post.call_args.kwargs["headers"], an._entetes(JETON))

    def test_date_du_jour_au_format_iso(self):
        self.assertRegex(an._date_du_jour(), r"^\d{4}-\d{2}-\d{2}$")

    def test_reponse_sans_url(self):
        with patch("assistant_notion.requests.post", return_value=_reponse(200, {})):
            _levee(self, an.envoyer_page_test, JETON, BASE_ID)


class TestErreurs(unittest.TestCase):
    """Chaque echec d'API devient un message en francais pour l'utilisateur."""

    def _echec(self, reponse=None, exception=None) -> an.ErreurNotion:
        with patch(
            "assistant_notion.requests.post",
            return_value=reponse,
            side_effect=exception,
        ):
            return _levee(self, an.envoyer_page_test, JETON, BASE_ID)

    def test_401(self):
        erreur = self._echec(_erreur(401, "unauthorized", "API token is invalid."))
        self.assertEqual(
            erreur.message,
            "Le jeton d'intégration est refusé par Notion : "
            "vérifiez qu'il a été copié en entier.",
        )
        self.assertEqual(erreur.statut, 401)

    def test_404(self):
        erreur = self._echec(_erreur(404, "object_not_found"))
        self.assertEqual(
            erreur.message,
            "Notion ne trouve pas cette page : ouvrez-la dans Notion, "
            "menu ··· → Connexions, et ajoutez votre intégration.",
        )
        self.assertEqual((erreur.statut, erreur.code), (404, "object_not_found"))

    def test_object_not_found_sous_un_autre_statut(self):
        erreur = self._echec(_erreur(410, "object_not_found"))
        self.assertEqual(erreur.message, an.MSG_INTROUVABLE)

    def test_403_droit_insuffisant(self):
        erreur = self._echec(_erreur(403, "restricted_resource"))
        self.assertEqual(erreur.message, an.MSG_DROIT_INSUFFISANT)
        self.assertIn("insérer du contenu", erreur.message.lower())

    def test_400_reprend_le_message_de_notion(self):
        detail = "Statut is not a property"
        erreur = self._echec(_erreur(400, "validation_error", detail))
        self.assertEqual(
            erreur.message, "Notion refuse la demande : Statut is not a property"
        )
        self.assertEqual(erreur.statut, 400)

    def test_400_sans_detail(self):
        erreur = self._echec(_reponse(400, {}))
        self.assertIn("refuse la demande", erreur.message)

    def test_statut_inattendu(self):
        erreur = self._echec(_erreur(500, "internal_server_error"))
        self.assertIn("(500)", erreur.message)
        self.assertEqual(erreur.statut, 500)

    def test_corps_non_json(self):
        reponse = _reponse(502)
        reponse.json.side_effect = ValueError("pas du json")
        erreur = self._echec(reponse)
        self.assertIn("(502)", erreur.message)

    def test_reponse_ok_non_json(self):
        reponse = _reponse(200)
        reponse.json.side_effect = ValueError("pas du json")
        self.assertEqual(self._echec(reponse).message, an.MSG_REPONSE_ILLISIBLE)

    def test_erreur_reseau(self):
        erreur = self._echec(exception=requests.ConnectionError("dns"))
        self.assertEqual(
            erreur.message,
            "Impossible de joindre Notion, vérifiez la connexion Internet.",
        )
        self.assertIsNone(erreur.statut)

    def test_delai_depasse(self):
        erreur = self._echec(exception=requests.Timeout("lent"))
        self.assertEqual(erreur.message, an.MSG_RESEAU)

    def test_le_jeton_n_apparait_dans_aucun_message(self):
        # Notion pourrait refleter le jeton dans son message : on verifie au moins
        # que rien de notre cote n'en ajoute, quel que soit l'echec.
        echecs = [
            {"reponse": _erreur(401, "unauthorized")},
            {"reponse": _erreur(404, "object_not_found")},
            {"reponse": _erreur(403, "restricted_resource")},
            {"reponse": _erreur(400, "validation_error")},
            {"reponse": _erreur(500)},
            {"reponse": _reponse(200, {})},
            {"exception": requests.ConnectionError(f"{API} {BASE_ID}")},
            {"exception": requests.Timeout()},
        ]
        for echec in echecs:
            with self.subTest(echec=echec):
                erreur = self._echec(**echec)
                self.assertNotIn(JETON, erreur.message)
                self.assertNotIn(JETON, str(erreur))
                self.assertNotIn(JETON, repr(erreur))


if __name__ == "__main__":
    unittest.main()
