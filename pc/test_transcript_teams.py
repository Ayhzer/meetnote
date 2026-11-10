"""Tests de transcript_teams.vtt_vers_texte (répliques fictives)."""
import unittest

from transcript_teams import SEUIL_SILENCE_S, TAILLE_MAX_PARAGRAPHE, vtt_vers_texte


class TestVttVersTexte(unittest.TestCase):

    def test_vide(self):
        self.assertEqual(vtt_vers_texte(""), "")
        self.assertEqual(vtt_vers_texte(None), "")
        self.assertEqual(vtt_vers_texte("WEBVTT\r\n\r\n"), "")

    def test_sans_locuteur_ni_entete_regroupe_les_repliques_proches(self):
        vtt = ("00:00:03.575 --> 00:00:03.815\r\nBonjour.\r\n\r\n"
               "00:00:06.855 --> 00:00:07.815\r\nOn commence.\r\n\r\n"
               "00:00:09.655 --> 00:00:28.335\r\nPremier point.\r\n")
        self.assertEqual(vtt_vers_texte(vtt),
                         "[00:00:03] Bonjour. On commence. Premier point.")

    def test_entete_et_numeros_ignores(self):
        vtt = ("WEBVTT\r\n\r\n1\r\n00:00:01.000 --> 00:00:02.000\r\nAlpha\r\n\r\n"
               "2\r\n00:00:03.000 --> 00:00:04.000\r\nBeta\r\n")
        self.assertEqual(vtt_vers_texte(vtt), "[00:00:01] Alpha Beta")

    def test_fins_de_ligne_unix(self):
        vtt = "00:00:01.000 --> 00:00:02.000\nAlpha\n\n00:00:03.000 --> 00:00:04.000\nBeta\n"
        self.assertEqual(vtt_vers_texte(vtt), "[00:00:01] Alpha Beta")

    def test_replique_multiligne_jointe_par_une_espace(self):
        vtt = "00:00:01.000 --> 00:00:02.000\r\nPremière ligne\r\nseconde ligne\r\n"
        self.assertEqual(vtt_vers_texte(vtt), "[00:00:01] Première ligne seconde ligne")

    def test_changement_de_locuteur(self):
        vtt = ("WEBVTT\r\n\r\n"
               "00:00:01.000 --> 00:00:02.000\r\n<v Camille Test>Bonjour</v>\r\n\r\n"
               "00:00:02.500 --> 00:00:03.000\r\n<v Camille Test>à tous</v>\r\n\r\n"
               "00:00:03.500 --> 00:00:04.000\r\n<v Alex Fictif>Salut</v>\r\n")
        self.assertEqual(vtt_vers_texte(vtt),
                         "[00:00:01] Camille Test : Bonjour à tous\n\n"
                         "[00:00:03] Alex Fictif : Salut")

    def test_meme_locuteur_apres_silence_nouveau_paragraphe(self):
        debut = int(2 + SEUIL_SILENCE_S + 1)
        vtt = ("00:00:01.000 --> 00:00:02.000\r\n<v Camille Test>Un</v>\r\n\r\n"
               f"00:00:{debut:02d}.000 --> 00:00:{debut + 1:02d}.000\r\n"
               "<v Camille Test>Deux</v>\r\n")
        texte = vtt_vers_texte(vtt)
        self.assertEqual(texte.count("\n\n"), 1)
        self.assertTrue(texte.endswith("Camille Test : Deux"))

    def test_silence_sous_le_seuil_ne_coupe_pas(self):
        debut = int(2 + SEUIL_SILENCE_S - 1)
        vtt = ("00:00:01.000 --> 00:00:02.000\r\nUn\r\n\r\n"
               f"00:00:{debut:02d}.000 --> 00:00:{debut + 1:02d}.000\r\nDeux\r\n")
        self.assertNotIn("\n\n", vtt_vers_texte(vtt))

    def test_taille_maximale_coupe_un_monologue(self):
        phrase = " ".join(["mot"] * 20)
        cues = [f"00:00:{i:02d}.000 --> 00:00:{i:02d}.900\r\n{phrase}\r\n"
                for i in range(10)]
        paragraphes = vtt_vers_texte("\r\n".join(cues)).split("\n\n")
        self.assertGreater(len(paragraphes), 1)
        for p in paragraphes:
            self.assertLessEqual(len(p.split("] ", 1)[1]), TAILLE_MAX_PARAGRAPHE)

    def test_balises_residuelles_et_entites(self):
        vtt = "00:00:01.000 --> 00:00:02.000\r\nUn <i>mot</i> &amp; un autre\r\n"
        self.assertEqual(vtt_vers_texte(vtt), "[00:00:01] Un mot & un autre")

    def test_locuteur_adresse_seule_jamais_affiche(self):
        vtt = ("00:00:01.000 --> 00:00:02.000\r\n<v a@b.fr>Bonjour</v>\r\n")
        texte = vtt_vers_texte(vtt)
        self.assertNotIn("@", texte)
        self.assertEqual(texte, "[00:00:01] Bonjour")

    def test_repliques_consecutives_d_un_locuteur_adresse_sans_prefixe_vide(self):
        vtt = ("00:00:01.000 --> 00:00:02.000\r\n<v a@b.fr>Un</v>\r\n\r\n"
               "00:00:02.500 --> 00:00:03.000\r\n<v a@b.fr>Deux</v>\r\n")
        texte = vtt_vers_texte(vtt)
        self.assertEqual(texte, "[00:00:01] Un Deux")
        self.assertNotIn(" : ", texte)

    def test_locuteur_adresse_ne_prend_pas_le_nom_du_precedent(self):
        vtt = ("00:00:01.000 --> 00:00:02.000\r\n<v Camille Test>Un</v>\r\n\r\n"
               "00:00:02.500 --> 00:00:03.000\r\n<v a@b.fr>Deux</v>\r\n")
        texte = vtt_vers_texte(vtt)
        self.assertNotIn("@", texte)
        self.assertEqual(texte, "[00:00:01] Camille Test : Un\n\n[00:00:02] Deux")

    def test_horodatage_avec_heures(self):
        vtt = "01:02:03.999 --> 01:02:05.000\r\nTard\r\n"
        self.assertEqual(vtt_vers_texte(vtt), "[01:02:03] Tard")

    def test_horodatage_format_court_sans_heures(self):
        vtt = "10:05.000 --> 10:06.000\r\nCourt\r\n"
        self.assertEqual(vtt_vers_texte(vtt), "[00:10:05] Court")


if __name__ == "__main__":
    unittest.main()
