"""Tests de lien_teams.url_participation (données fictives)."""
import unittest

import lien_teams

ANCIEN = ("https://teams.microsoft.com/l/meetup-join/19%3ameeting_ABC123%40thread.v2/0"
          "?context=%7b%22Tid%22%3a%22t-0000%22%2c%22Oid%22%3a%22o-1111%22%7d")
NOUVEAU = "https://teams.microsoft.com/meet/123456789012?p=CodeFictif"


class TestUrlParticipation(unittest.TestCase):

    def test_texte_ancien_format(self):
        corps = f"Rejoindre la réunion\r\n{ANCIEN}\r\nID : 1"
        self.assertEqual(lien_teams.url_participation(corps), ANCIEN)

    def test_texte_nouveau_format(self):
        corps = f"Rejoindre : {NOUVEAU} merci"
        self.assertEqual(lien_teams.url_participation(corps), NOUVEAU)

    def test_texte_entre_chevrons(self):
        self.assertEqual(lien_teams.url_participation(f"Lien <{NOUVEAU}>"), NOUVEAU)

    def test_html_entites_decodees_et_guillemets(self):
        html_url = ANCIEN.replace("%2c", "&amp;x=")
        corps = f'<p><a href="{html_url}" target="_blank">Rejoindre</a></p>'
        attendu = ANCIEN.replace("%2c", "&x=")
        self.assertEqual(lien_teams.url_participation(corps), attendu)

    def test_html_apostrophes(self):
        self.assertEqual(
            lien_teams.url_participation(f"<a href='{NOUVEAU}'>lien</a>"), NOUVEAU)

    def test_ponctuation_finale_retiree(self):
        self.assertEqual(lien_teams.url_participation(f"Voir {NOUVEAU}."), NOUVEAU)

    def test_premiere_url_retenue(self):
        corps = f"{NOUVEAU} puis {ANCIEN}"
        self.assertEqual(lien_teams.url_participation(corps), NOUVEAU)

    def test_absence(self):
        for corps in ("", None, "Ordre du jour", "https://exemple.test/meet/1",
                      "https://teams.microsoft.com/l/message/1"):
            with self.subTest(corps=corps):
                self.assertEqual(lien_teams.url_participation(corps), "")


if __name__ == "__main__":
    unittest.main()
