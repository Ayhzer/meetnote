"""
Tests de la logique de l'assistant de demarrage (assistant_fenetre), sans ecran
ni reseau : `assistant_notion` est mocke.

Lancement : py -3.12 pc\\test_assistant_fenetre.py
"""
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import assistant_audio as aa
import assistant_fenetre as af
import assistant_notion as an
import user_config

ID_PAGE = "01234567-89ab-cdef-0123-456789abcdef"
ID_BASE = "fedcba98-7654-3210-fedc-ba9876543210"
LIEN_PAGE = "https://www.notion.so/Ma-page-0123456789abcdef0123456789abcdef"
JETON = "secret_abc123"


def _patch_notion(**options):
    """Remplace les fonctions reseau d'assistant_notion par des mocks."""
    reglages = {
        "nature_lien": MagicMock(return_value="page"),
        "creer_base": MagicMock(return_value=an.BaseCreee(ID_BASE, "src", None)),
        "lire_schema": MagicMock(return_value={}),
        "comparer_schema": MagicMock(return_value=[]),
        "envoyer_page_test": MagicMock(return_value="https://notion.so/test"),
    }
    reglages.update(options)
    patchs = {nom: patch.object(an, nom, mock) for nom, mock in reglages.items()}
    return patchs, reglages


class _Notion:
    """Gestionnaire de contexte qui active tous les mocks et les expose."""

    def __init__(self, **options):
        self._patchs, self.mocks = _patch_notion(**options)

    def __enter__(self):
        for p in self._patchs.values():
            p.start()
        return self.mocks

    def __exit__(self, *_exc):
        for p in self._patchs.values():
            p.stop()


class TestValiderChamps(unittest.TestCase):
    def test_champs_valides_nettoyes(self):
        token, identifiant = af.valider_champs(f"  {JETON} \n", f" {LIEN_PAGE} ")
        self.assertEqual(token, JETON)
        self.assertEqual(identifiant, ID_PAGE)

    def test_jeton_vide(self):
        for vide in ("", "   ", None):
            with self.assertRaises(ValueError) as ctx:
                af.valider_champs(vide, LIEN_PAGE)
            self.assertEqual(str(ctx.exception), af.MSG_JETON_VIDE)

    def test_lien_vide(self):
        with self.assertRaises(ValueError) as ctx:
            af.valider_champs(JETON, "  ")
        self.assertEqual(str(ctx.exception), af.MSG_LIEN_VIDE)

    def test_lien_non_notion(self):
        with self.assertRaises(ValueError) as ctx:
            af.valider_champs(JETON, "https://exemple.com/page")
        self.assertEqual(str(ctx.exception), af.MSG_LIEN_INVALIDE)


class TestExecuterEtapeNotion(unittest.TestCase):
    def test_page_parente_cree_la_base_puis_envoie_le_test(self):
        with _Notion() as m:
            resultat = af.executer_etape_notion(JETON, LIEN_PAGE, {})
        self.assertTrue(resultat.ok)
        self.assertEqual(resultat.base_id, ID_BASE)
        self.assertEqual(resultat.message, af.message_succes(True))
        m["creer_base"].assert_called_once_with(JETON, ID_PAGE)
        m["envoyer_page_test"].assert_called_once_with(JETON, ID_BASE)
        m["lire_schema"].assert_not_called()

    def test_base_conforme_envoie_le_test_sans_creer(self):
        with _Notion(nature_lien=MagicMock(return_value="base")) as m:
            resultat = af.executer_etape_notion(JETON, LIEN_PAGE, {})
        self.assertTrue(resultat.ok)
        self.assertEqual(resultat.base_id, ID_PAGE)
        self.assertEqual(resultat.message, af.message_succes(False))
        m["creer_base"].assert_not_called()
        m["envoyer_page_test"].assert_called_once_with(JETON, ID_PAGE)

    def test_base_non_conforme_liste_ce_qui_manque_sans_envoyer(self):
        constats = ["Propriété manquante : Durée (min) (Nombre)"]
        with _Notion(
            nature_lien=MagicMock(return_value="base"),
            comparer_schema=MagicMock(return_value=constats),
        ) as m:
            resultat = af.executer_etape_notion(JETON, LIEN_PAGE, {})
        self.assertFalse(resultat.ok)
        self.assertIsNone(resultat.base_id)
        self.assertIn("Durée (min)", resultat.message)
        m["envoyer_page_test"].assert_not_called()

    def test_champ_invalide_n_appelle_pas_notion(self):
        with _Notion() as m:
            resultat = af.executer_etape_notion("", LIEN_PAGE, {})
        self.assertFalse(resultat.ok)
        self.assertEqual(resultat.message, af.MSG_JETON_VIDE)
        m["nature_lien"].assert_not_called()

    def test_erreur_notion_devient_un_message(self):
        erreur = an.ErreurNotion(an.MSG_JETON_REFUSE, statut=401)
        with _Notion(nature_lien=MagicMock(side_effect=erreur)):
            resultat = af.executer_etape_notion(JETON, LIEN_PAGE, {})
        self.assertFalse(resultat.ok)
        self.assertEqual(resultat.message, an.MSG_JETON_REFUSE)
        self.assertIsNone(resultat.base_id)

    def test_echec_du_test_d_envoi_ne_valide_rien(self):
        erreur = an.ErreurNotion(an.MSG_DROIT_INSUFFISANT, statut=403)
        with _Notion(envoyer_page_test=MagicMock(side_effect=erreur)):
            resultat = af.executer_etape_notion(JETON, LIEN_PAGE, {})
        self.assertFalse(resultat.ok)
        self.assertIsNone(resultat.base_id)
        self.assertEqual(resultat.message, an.MSG_DROIT_INSUFFISANT)

    def test_reessai_apres_echec_reutilise_la_base_creee(self):
        bases = {}
        erreur = an.ErreurNotion(an.MSG_RESEAU)
        with _Notion(envoyer_page_test=MagicMock(side_effect=[erreur, "url"])) as m:
            premier = af.executer_etape_notion(JETON, LIEN_PAGE, bases)
            second = af.executer_etape_notion(JETON, LIEN_PAGE, bases)
        self.assertFalse(premier.ok)
        self.assertTrue(second.ok)
        self.assertEqual(second.base_id, ID_BASE)
        self.assertEqual(m["creer_base"].call_count, 1)
        self.assertEqual(bases, {ID_PAGE: ID_BASE})
        # Base reutilisee : le message ne pretend pas l'avoir creee a ce tour.
        self.assertEqual(second.message, af.message_succes(False))

    def test_le_jeton_n_apparait_dans_aucun_message(self):
        erreur = an.ErreurNotion(an.MSG_INTROUVABLE, statut=404)
        with _Notion(nature_lien=MagicMock(side_effect=erreur)):
            resultat = af.executer_etape_notion(JETON, LIEN_PAGE, {})
        self.assertNotIn(JETON, resultat.message)


class TestMessages(unittest.TestCase):
    def test_message_schema_liste_chaque_constat(self):
        texte = af.message_schema_non_conforme(["A manque", "B mauvais type"])
        self.assertIn("• A manque", texte)
        self.assertIn("• B mauvais type", texte)

    def test_message_succes_distingue_creation_et_verification(self):
        self.assertIn("créée", af.message_succes(True))
        self.assertIn("conforme", af.message_succes(False))
        self.assertIn("page de test", af.message_succes(True))


class TestExclusions(unittest.TestCase):
    def test_enregistrees_prevalent(self):
        self.assertEqual(af.exclusions_initiales("alpha\nbeta"), "alpha\nbeta")

    def test_defaut_si_rien_d_enregistre(self):
        for vide in (None, "", "  \n "):
            self.assertEqual(
                af.exclusions_initiales(vide), user_config.EXCLUSIONS_PAR_DEFAUT
            )

    def test_normalisation_retire_lignes_vides_et_espaces(self):
        brut = "  todo \n\n\t perso\r\n   \nne pas enregistrer  \n"
        self.assertEqual(
            af.normaliser_exclusions(brut), "todo\nperso\nne pas enregistrer"
        )

    def test_normalisation_du_vide(self):
        self.assertEqual(af.normaliser_exclusions(None), "")
        self.assertEqual(af.normaliser_exclusions(" \n "), "")


class TestValeursAEnregistrer(unittest.TestCase):
    def test_cles_lues_par_l_application(self):
        valeurs = af.valeurs_a_enregistrer(JETON, ID_BASE, " todo \n\n off ")
        self.assertEqual(
            valeurs,
            {
                "notion_token": JETON,
                "notion_database_id": ID_BASE,
                "auto_start_exclusions": "todo\noff",
                "assistant_termine": True,
            },
        )

    def test_sans_connexion_validee_rien_a_enregistrer(self):
        for token, base in ((None, ID_BASE), (JETON, None), ("", "")):
            with self.assertRaises(ValueError):
                af.valeurs_a_enregistrer(token, base, "todo")


class TestAutorisationTerminer(unittest.TestCase):
    def test_pas_de_test_pas_de_terminer(self):
        self.assertFalse(af.terminer_autorise(None))

    def test_micro_nul_ou_absent_bloque(self):
        self.assertFalse(af.terminer_autorise(aa.rendre_verdict(0.0, 40.0)))
        self.assertFalse(af.terminer_autorise(aa.rendre_verdict(None, 40.0)))

    def test_micro_capte_autorise_meme_sans_son_du_pc(self):
        self.assertTrue(af.terminer_autorise(aa.rendre_verdict(30.0, 40.0)))
        self.assertTrue(af.terminer_autorise(aa.rendre_verdict(30.0, 0.0)))
        self.assertTrue(af.terminer_autorise(aa.rendre_verdict(30.0, None)))

    def test_messages_selon_le_verdict(self):
        texte, couleur = af.messages_audio(aa.rendre_verdict(30.0, 40.0))
        self.assertEqual((texte, couleur), (af.MSG_AUDIO_OK, af.OK))
        texte, couleur = af.messages_audio(aa.rendre_verdict(30.0, 0.0))
        self.assertEqual((texte, couleur), (aa.MSG_PC_NUL, af.AVERT))
        texte, couleur = af.messages_audio(aa.rendre_verdict(0.0, 0.0))
        self.assertEqual(couleur, af.ERR)
        self.assertIn(aa.MSG_MICRO_NUL, texte)
        self.assertIn(aa.MSG_PC_NUL, texte)


class TestExecuterTestAudio(unittest.TestCase):
    def test_niveaux_et_verdict(self):
        recus = []
        with patch.object(aa, "mesurer_micro", return_value=30.0), \
                patch.object(aa, "mesurer_son_pc", return_value=0.0):
            resultat = af.executer_test_audio(recus.append)
        self.assertEqual(recus, [30.0])
        self.assertEqual((resultat.niveau_micro, resultat.niveau_pc), (30.0, 0.0))
        self.assertTrue(resultat.verdict.accepte)
        self.assertEqual(resultat.verdict.avertissements, (aa.MSG_PC_NUL,))

    def test_capture_impossible_devient_none(self):
        erreur = aa.ErreurCapture("absent")
        with patch.object(aa, "mesurer_micro", side_effect=erreur), \
                patch.object(aa, "mesurer_son_pc", return_value=20.0):
            resultat = af.executer_test_audio()
        self.assertIsNone(resultat.niveau_micro)
        self.assertEqual(resultat.verdict.refus, aa.MSG_MICRO_ABSENT)
        self.assertFalse(af.terminer_autorise(resultat.verdict))


class TestRefusTestAudio(unittest.TestCase):
    def test_refuse_pendant_un_enregistrement(self):
        self.assertEqual(
            af.refus_test_audio(lambda: True), af.MSG_TEST_PENDANT_ENREGISTREMENT
        )

    def test_autorise_sans_enregistrement(self):
        self.assertIsNone(af.refus_test_audio(lambda: False))

    def test_autorise_sans_indicateur(self):
        self.assertIsNone(af.refus_test_audio(None))

    def test_etat_relu_a_chaque_appel(self):
        etat = {"en_cours": True}
        self.assertIsNotNone(af.refus_test_audio(lambda: etat["en_cours"]))
        etat["en_cours"] = False
        self.assertIsNone(af.refus_test_audio(lambda: etat["en_cours"]))


class TestLargeurBarre(unittest.TestCase):
    def test_proportionnelle_et_bornee(self):
        self.assertEqual(af.largeur_barre(None, 200), 0)
        self.assertEqual(af.largeur_barre(0.0, 200), 0)
        self.assertEqual(af.largeur_barre(50.0, 200), 100)
        self.assertEqual(af.largeur_barre(250.0, 200), 200)


class TestTerminer(unittest.TestCase):
    """`terminer()` sans ecran : l'objet est construit sans passer par tkinter."""

    def _assistant(self, a_la_fin=None, exclusions=" todo \n\n"):
        assistant = object.__new__(af.AssistantDemarrage)
        assistant.token_notion = JETON
        assistant.base_id = ID_BASE
        assistant.exclusions = exclusions
        assistant.win = MagicMock()
        assistant._a_la_fin = a_la_fin
        return assistant

    def test_enregistre_puis_ferme_puis_appelle_a_la_fin(self):
        a_la_fin = MagicMock()
        assistant = self._assistant(a_la_fin)
        with patch.object(user_config, "save") as sauver:
            erreur = assistant.terminer()
        self.assertIsNone(erreur)
        sauver.assert_called_once_with(
            af.valeurs_a_enregistrer(JETON, ID_BASE, "todo")
        )
        assistant.win.destroy.assert_called_once()
        a_la_fin.assert_called_once()

    def test_echec_d_ecriture_garde_la_fenetre_ouverte(self):
        a_la_fin = MagicMock()
        assistant = self._assistant(a_la_fin)
        with patch.object(user_config, "save", side_effect=PermissionError("x")):
            erreur = assistant.terminer()
        self.assertIn("PermissionError", erreur)
        assistant.win.destroy.assert_not_called()
        a_la_fin.assert_not_called()

    def test_sans_notion_valide_rien_n_est_ecrit(self):
        assistant = self._assistant()
        assistant.token_notion = None
        with patch.object(user_config, "save") as sauver:
            erreur = assistant.terminer()
        self.assertEqual(erreur, af.MSG_RIEN_A_ENREGISTRER)
        sauver.assert_not_called()


class TestImportSansFenetre(unittest.TestCase):
    def test_import_n_ouvre_aucune_fenetre(self):
        """L'import ne cree aucun Tk : la classe existe, aucune instance n'est faite."""
        self.assertTrue(callable(af.ouvrir_assistant))
        self.assertEqual(
            af.AssistantDemarrage.FABRIQUES_ECRANS,
            (af.EcranNotion, af.EcranAgenda, af.EcranExclusions, af.EcranAudio),
        )


if __name__ == "__main__":
    unittest.main()
