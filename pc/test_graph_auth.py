"""Tests de graph_auth : msal et DPAPI remplaces par des doublures (aucun reseau)."""
import os
import sys
import tempfile
import types
import unittest
from typing import ClassVar
from unittest import mock

import graph_auth

JETON = "jeton-fictif-xyz"
COMPTE = {"username": "agnes@exemple.test"}
PREFIXE_DPAPI = b"DPAPI:"


class FauxCache:
    """Imite msal.SerializableTokenCache : contenu texte + indicateur de changement."""

    def __init__(self):
        self.contenu = ""
        self.has_state_changed = False

    def deserialize(self, texte):
        if not texte.startswith("{"):
            raise ValueError("cache corrompu")
        self.contenu = texte
        self.has_state_changed = False

    def serialize(self):
        return self.contenu


class FausseApp:
    """Imite PublicClientApplication ; scenario regle par les attributs de classe."""
    comptes: ClassVar[list] = [COMPTE]
    reponse_silencieuse: ClassVar[dict | None] = None
    reponse_interactive: ClassVar[dict | None] = None
    appels: ClassVar[list] = []

    def __init__(self, client_id, authority=None, token_cache=None):
        FausseApp.appels.append(("init", client_id, authority))
        self.cache = token_cache

    def get_accounts(self):
        return FausseApp.comptes

    def acquire_token_silent(self, scopes, account=None):
        FausseApp.appels.append(("silent", tuple(scopes), account))
        return FausseApp.reponse_silencieuse

    def acquire_token_interactive(self, scopes, login_hint=None, prompt=None):
        FausseApp.appels.append(("interactif", tuple(scopes), login_hint, prompt))
        self.cache.contenu = '{"refresh": "secret-fictif"}'
        self.cache.has_state_changed = True
        return FausseApp.reponse_interactive


class FauxDpapi:
    """Chiffrement reversible factice (le texte clair n'apparait pas tel quel)."""

    @staticmethod
    def CryptProtectData(donnees, description, a, b, c, drapeaux):
        return PREFIXE_DPAPI + donnees[::-1]

    @staticmethod
    def CryptUnprotectData(chiffre, a, b, c, drapeaux):
        if not chiffre.startswith(PREFIXE_DPAPI):
            raise OSError("illisible")
        return ("MeetNote Graph", chiffre[len(PREFIXE_DPAPI):][::-1])


def _faux_msal():
    return types.SimpleNamespace(PublicClientApplication=FausseApp,
                                 SerializableTokenCache=FauxCache)


class BaseGraph(unittest.TestCase):

    def setUp(self):
        self.dossier = tempfile.TemporaryDirectory()
        self.addCleanup(self.dossier.cleanup)
        self.chemin = os.path.join(self.dossier.name, "graph_cache.bin")
        self.reglages = {"graph_client_id": "client-fictif",
                         "graph_tenant_id": "tenant-fictif",
                         "ews_email": "agnes@exemple.test"}
        patches = (
            mock.patch.object(graph_auth, "CHEMIN_CACHE", self.chemin),
            mock.patch.object(
                graph_auth.user_config, "get",
                side_effect=lambda cle, defaut=None: self.reglages.get(cle, defaut)),
            mock.patch.dict(sys.modules,
                            {"msal": _faux_msal(), "win32crypt": FauxDpapi}),
        )
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        FausseApp.comptes = [COMPTE]
        FausseApp.reponse_silencieuse = None
        FausseApp.reponse_interactive = None
        FausseApp.appels = []

    def appels(self, nom):
        return [a for a in FausseApp.appels if a[0] == nom]


class TestObtenirJeton(BaseGraph):

    def test_silencieux_reussi_sans_navigateur(self):
        claims = {"preferred_username": "a@b.test"}
        FausseApp.reponse_silencieuse = {"access_token": JETON,
                                         "id_token_claims": claims}
        res = graph_auth.obtenir_jeton(interactif=True)
        self.assertEqual((res.jeton, res.compte, res.motif), (JETON, "a@b.test", ""))
        self.assertEqual(self.appels("interactif"), [])
        self.assertEqual(self.appels("init")[0][2],
                         "https://login.microsoftonline.com/tenant-fictif")
        self.assertEqual(self.appels("silent")[0][1], tuple(graph_auth.PORTEES))

    def test_portees_attendues(self):
        self.assertEqual(graph_auth.PORTEES,
                         ["OnlineMeetings.Read", "OnlineMeetingTranscript.Read.All"])

    def test_silencieux_rate_sans_interactif(self):
        res = graph_auth.obtenir_jeton(interactif=False)
        self.assertIsNone(res.jeton)
        self.assertIn("Connexion Microsoft requise", res.motif)
        self.assertEqual(self.appels("interactif"), [])

    def test_sans_compte_en_cache_pas_de_silencieux(self):
        FausseApp.comptes = []
        res = graph_auth.obtenir_jeton(interactif=False)
        self.assertIsNone(res.jeton)
        self.assertEqual(self.appels("silent"), [])

    def test_interactif_apres_silencieux_rate(self):
        FausseApp.reponse_interactive = {"access_token": JETON}
        res = graph_auth.obtenir_jeton(interactif=True)
        self.assertEqual(res.jeton, JETON)
        self.assertEqual(res.compte, "agnes@exemple.test")
        self.assertEqual(self.appels("interactif"),
                         [("interactif", tuple(graph_auth.PORTEES),
                           "agnes@exemple.test", "select_account")])

    def test_interactif_refuse(self):
        FausseApp.reponse_interactive = {"error": "access_denied",
                                         "error_description": "ne doit pas fuiter"}
        res = graph_auth.obtenir_jeton(interactif=True)
        self.assertIsNone(res.jeton)
        self.assertIn("access_denied", res.motif)
        self.assertNotIn("fuiter", res.motif)

    def test_exception_msal_devient_un_motif(self):
        with mock.patch.object(FausseApp, "get_accounts",
                               side_effect=RuntimeError("boom")):
            res = graph_auth.obtenir_jeton(interactif=True)
        self.assertIsNone(res.jeton)
        self.assertIn("RuntimeError", res.motif)

    def test_configuration_absente_n_appelle_pas_msal(self):
        for cle in ("graph_client_id", "graph_tenant_id"):
            with self.subTest(cle=cle):
                valeur = self.reglages[cle]
                self.reglages[cle] = ""
                FausseApp.appels = []
                res = graph_auth.obtenir_jeton(interactif=True)
                self.assertIsNone(res.jeton)
                self.assertIn("non configure", res.motif)
                self.assertEqual(FausseApp.appels, [])
                self.reglages[cle] = valeur

    def test_msal_absent(self):
        with mock.patch.dict(sys.modules, {"msal": None}):
            res = graph_auth.obtenir_jeton(interactif=True)
        self.assertIsNone(res.jeton)
        self.assertIn("msal", res.motif)

    def test_aucun_jeton_dans_les_journaux(self):
        FausseApp.reponse_interactive = {"access_token": JETON}
        with self.assertLogs("meetnote", level="DEBUG") as logs:
            graph_auth.obtenir_jeton(interactif=True)
            graph_auth._log.info("sentinelle")  # assertLogs exige au moins un message
        self.assertNotIn(JETON, "\n".join(logs.output))


class TestCache(BaseGraph):

    def test_aller_retour_chiffre(self):
        FausseApp.reponse_interactive = {"access_token": JETON}
        graph_auth.obtenir_jeton(interactif=True)
        with open(self.chemin, "rb") as f:
            brut = f.read()
        self.assertTrue(brut.startswith(PREFIXE_DPAPI))
        self.assertNotIn(b"secret-fictif", brut)
        cache = graph_auth._charger_cache(_faux_msal(), FauxDpapi)
        self.assertEqual(cache.contenu, '{"refresh": "secret-fictif"}')
        self.assertFalse(cache.has_state_changed)

    def test_cache_inchange_non_reecrit(self):
        FausseApp.reponse_silencieuse = {"access_token": JETON}
        graph_auth.obtenir_jeton(interactif=False)
        self.assertFalse(os.path.exists(self.chemin))

    def test_cache_absent_donne_cache_vide(self):
        cache = graph_auth._charger_cache(_faux_msal(), FauxDpapi)
        self.assertEqual(cache.contenu, "")

    def test_cache_corrompu_mis_de_cote(self):
        with open(self.chemin, "wb") as f:
            f.write(b"n'importe quoi")
        FausseApp.reponse_interactive = {"access_token": JETON}
        with self.assertLogs("meetnote", level="WARNING"):
            res = graph_auth.obtenir_jeton(interactif=True)
        self.assertEqual(res.jeton, JETON)
        voisins = os.listdir(self.dossier.name)
        self.assertTrue(any(".corrompu-" in n for n in voisins), voisins)
        with open(self.chemin, "rb") as f:  # le nouveau cache valide remplace l'ancien
            self.assertTrue(f.read().startswith(PREFIXE_DPAPI))

    def test_cache_dechiffre_mais_json_invalide_mis_de_cote(self):
        with open(self.chemin, "wb") as f:
            f.write(FauxDpapi.CryptProtectData(b"pas du json", "", None, None, None, 0))
        with self.assertLogs("meetnote", level="WARNING"):
            cache = graph_auth._charger_cache(_faux_msal(), FauxDpapi)
        self.assertEqual(cache.contenu, "")
        self.assertFalse(os.path.exists(self.chemin))

    def test_sans_dpapi_rien_n_est_ecrit_en_clair(self):
        FausseApp.reponse_interactive = {"access_token": JETON}
        with (mock.patch.dict(sys.modules, {"win32crypt": None}),
              self.assertLogs("meetnote", level="WARNING")):
            res = graph_auth.obtenir_jeton(interactif=True)
        self.assertEqual(res.jeton, JETON)
        self.assertFalse(os.path.exists(self.chemin))


class TestIdentifiantsEtMessage(unittest.TestCase):
    def test_identifiants_saisis_exige_les_deux(self):
        self.assertTrue(graph_auth.identifiants_saisis("abc", "def"))
        self.assertFalse(graph_auth.identifiants_saisis("", "def"))
        self.assertFalse(graph_auth.identifiants_saisis("abc", "   "))
        self.assertFalse(graph_auth.identifiants_saisis(None, None))

    def test_message_succes_nomme_le_compte(self):
        res = graph_auth.ResultatJeton(JETON, "agnes@exemple.test", "")
        msg = graph_auth.message_connexion(res)
        self.assertIn("agnes@exemple.test", msg)
        self.assertNotIn(JETON, msg)

    def test_message_succes_sans_compte(self):
        msg = graph_auth.message_connexion(graph_auth.ResultatJeton(JETON))
        self.assertIn("Microsoft 365", msg)

    def test_message_echec_rend_le_motif(self):
        res = graph_auth.ResultatJeton(None, None, "Connexion annulee")
        self.assertEqual("Connexion annulee", graph_auth.message_connexion(res))

    def test_message_echec_sans_motif(self):
        msg = graph_auth.message_connexion(graph_auth.ResultatJeton(None))
        self.assertTrue(msg)


if __name__ == "__main__":
    unittest.main()
