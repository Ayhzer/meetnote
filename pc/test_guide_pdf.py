"""Tests de guide_pdf : conversion Markdown, recherche d'Edge, impression simulée."""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import guide_pdf
from guide_pdf import ErreurGuidePdf


class ConvertirMarkdownTest(unittest.TestCase):
    def test_titres_par_niveau(self):
        h = guide_pdf.convertir_markdown("# Un\n\n## Deux\n\n### Trois")
        self.assertIn("<h1>Un</h1>", h)
        self.assertIn("<h2>Deux</h2>", h)
        self.assertIn("<h3>Trois</h3>", h)

    def test_paragraphe_sur_plusieurs_lignes(self):
        h = guide_pdf.convertir_markdown("Ligne un\nligne deux\n\nAutre")
        self.assertIn("<p>Ligne un ligne deux</p>", h)
        self.assertIn("<p>Autre</p>", h)

    def test_liste_a_puces(self):
        h = guide_pdf.convertir_markdown("- a\n- b")
        self.assertIn("<ul><li>a</li><li>b</li></ul>", h)

    def test_liste_numerotee(self):
        h = guide_pdf.convertir_markdown("1. un\n2. deux")
        self.assertIn("<ol><li>un</li><li>deux</li></ol>", h)

    def test_liste_puis_paragraphe_sont_deux_blocs(self):
        h = guide_pdf.convertir_markdown("- a\n\nTexte")
        self.assertIn("</ul><p>Texte</p>", h)

    def test_gras_et_code(self):
        h = guide_pdf.convertir_markdown("Un **gras** et `code` ici")
        self.assertIn("<strong>gras</strong>", h)
        self.assertIn("<code>code</code>", h)

    def test_code_n_est_pas_interprete(self):
        h = guide_pdf.convertir_markdown("`**pas gras** <b>`")
        self.assertIn("<code>**pas gras** &lt;b&gt;</code>", h)
        self.assertNotIn("<strong>", h)

    def test_image(self):
        h = guide_pdf.convertir_markdown("![Alerte](docs/captures/x.png)")
        self.assertIn('<img src="docs/captures/x.png" alt="Alerte">', h)

    def test_lien(self):
        h = guide_pdf.convertir_markdown("Voir [le site](https://exemple.fr/a)")
        self.assertIn('<a href="https://exemple.fr/a">le site</a>', h)

    def test_echappement_des_chevrons_et_esperluette(self):
        h = guide_pdf.convertir_markdown("MeetNote-<version> & **<b>**")
        self.assertIn("MeetNote-&lt;version&gt; &amp; <strong>&lt;b&gt;</strong>", h)
        self.assertNotIn("<version>", h)

    def test_texte_injecte_dans_le_titre_est_echappe(self):
        h = guide_pdf.convertir_markdown("# <script>x</script>")
        self.assertNotIn("<script>", h)

    def test_base_pour_les_chemins_relatifs(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = guide_pdf.convertir_markdown("x", Path(tmp))
        self.assertIn('<base href="file:///', h)
        self.assertIn('/">', h.split("<base href=")[1])

    def test_sans_racine_pas_de_base(self):
        self.assertNotIn("<base", guide_pdf.convertir_markdown("x"))

    def test_document_autonome_en_a4(self):
        h = guide_pdf.convertir_markdown("x")
        self.assertIn("<style>", h)
        self.assertIn("A4", h)

    def test_guide_reel_converti_sans_balise_brute(self):
        h = guide_pdf.convertir_markdown(
            guide_pdf.GUIDE_MD.read_text(encoding="utf-8"), guide_pdf.RACINE_DEPOT)
        self.assertIn("<h1>Guide d&#x27;installation de MeetNote</h1>", h)
        self.assertIn("<ol>", h)
        self.assertIn("<img ", h)
        self.assertNotIn("<version>", h)
        self.assertNotIn("**", h)


ENVIRONNEMENT = {"ProgramFiles(x86)": "X86", "ProgramFiles": "PF"}


class TrouverEdgeTest(unittest.TestCase):
    ENV = ENVIRONNEMENT

    def _std(self, racine):
        return os.path.join(racine, guide_pdf.SOUS_DOSSIER_EDGE)

    def test_registre_machine_prime(self):
        trouve = guide_pdf.trouver_edge(
            self.ENV, lire_registre=lambda h: f"reg-{h}", existe=lambda c: True)
        self.assertEqual(trouve, "reg-HKEY_LOCAL_MACHINE")

    def test_registre_utilisateur_si_machine_absent(self):
        registre = {"HKEY_CURRENT_USER": "reg-user"}
        trouve = guide_pdf.trouver_edge(
            self.ENV, lire_registre=registre.get, existe=lambda c: c == "reg-user")
        self.assertEqual(trouve, "reg-user")

    def test_registre_absent_chemins_standard(self):
        trouve = guide_pdf.trouver_edge(
            self.ENV, lire_registre=lambda h: None,
            existe=lambda c: c == self._std("PF"))
        self.assertEqual(trouve, self._std("PF"))

    def test_x86_avant_program_files(self):
        trouve = guide_pdf.trouver_edge(
            self.ENV, lire_registre=lambda h: None, existe=lambda c: True)
        self.assertEqual(trouve, self._std("X86"))

    def test_chemin_du_registre_inexistant_ignore(self):
        trouve = guide_pdf.trouver_edge(
            self.ENV, lire_registre=lambda h: "fantome",
            existe=lambda c: c == self._std("PF"))
        self.assertEqual(trouve, self._std("PF"))

    def test_rien_trouve(self):
        self.assertIsNone(guide_pdf.trouver_edge(
            self.ENV, lire_registre=lambda h: None, existe=lambda c: False))

    def test_variables_absentes_sans_chemin_en_dur(self):
        vus = []
        guide_pdf.trouver_edge({}, lire_registre=lambda h: None,
                               existe=lambda c: vus.append(c) or False)
        self.assertEqual(vus, [])

    def test_lecture_reelle_du_registre_ne_leve_pas(self):
        # Edge présent ou non sur le poste : jamais d'exception.
        resultat = guide_pdf._lire_registre("HKEY_LOCAL_MACHINE")
        self.assertTrue(resultat is None or isinstance(resultat, str))


class CommandeEdgeTest(unittest.TestCase):
    def test_options_attendues_sans_shell(self):
        c = guide_pdf.commande_edge("edge.exe", "file:///p.html", "s.pdf", "prof")
        self.assertEqual(c[0], "edge.exe")
        for option in ("--headless", "--disable-gpu", "--no-pdf-header-footer",
                       "--print-to-pdf=s.pdf", "--user-data-dir=prof"):
            self.assertIn(option, c)
        self.assertEqual(c[-1], "file:///p.html")


class ImprimerPdfTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.sortie = Path(self._tmp.name) / "sous" / "guide.pdf"

    def _edge_qui_ecrit(self, contenu=b"%PDF-1.4", code=0, stderr=""):
        def executer(commande, **kw):
            self.assertNotIn("shell", kw)
            self.assertEqual(kw["timeout"], guide_pdf.DELAI_MAX_EDGE_S)
            cible = next(a for a in commande if a.startswith("--print-to-pdf="))
            if contenu:
                Path(cible.split("=", 1)[1]).write_bytes(contenu)
            return SimpleNamespace(returncode=code, stdout="", stderr=stderr)
        return executer

    def test_succes(self):
        chemin = guide_pdf.imprimer_pdf("<p>x</p>", self.sortie, "edge",
                                        self._edge_qui_ecrit())
        self.assertEqual(chemin, self.sortie.resolve())
        self.assertTrue(self.sortie.is_file())

    def test_echec_code_non_nul(self):
        with self.assertRaises(ErreurGuidePdf) as c:
            guide_pdf.imprimer_pdf("x", self.sortie, "edge",
                                   self._edge_qui_ecrit(None, 1, "boum"))
        self.assertIn("code 1", str(c.exception))
        self.assertIn("boum", str(c.exception))

    def test_succes_sans_pdf_est_un_echec(self):
        with self.assertRaises(ErreurGuidePdf):
            guide_pdf.imprimer_pdf("x", self.sortie, "edge",
                                   self._edge_qui_ecrit(None))

    def test_pdf_vide_est_un_echec(self):
        with self.assertRaises(ErreurGuidePdf):
            guide_pdf.imprimer_pdf("x", self.sortie, "edge",
                                   self._edge_qui_ecrit(b""))

    def test_delai_depasse(self):
        def executer(commande, **kw):
            raise subprocess.TimeoutExpired(commande, kw["timeout"])
        with self.assertRaises(ErreurGuidePdf) as c:
            guide_pdf.imprimer_pdf("x", self.sortie, "edge", executer)
        self.assertIn("n'a pas répondu", str(c.exception))

    def test_edge_qui_ne_demarre_pas(self):
        def executer(commande, **kw):
            raise FileNotFoundError("introuvable")
        with self.assertRaises(ErreurGuidePdf):
            guide_pdf.imprimer_pdf("x", self.sortie, "edge", executer)

    def test_pdf_perime_supprime_avant_impression(self):
        self.sortie.parent.mkdir(parents=True)
        self.sortie.write_bytes(b"vieux")
        with self.assertRaises(ErreurGuidePdf):
            guide_pdf.imprimer_pdf("x", self.sortie, "edge",
                                   self._edge_qui_ecrit(None))
        self.assertFalse(self.sortie.exists())


class GenererGuideTest(unittest.TestCase):
    def test_guide_absent(self):
        with self.assertRaises(ErreurGuidePdf):
            guide_pdf.generer_guide(Path("s.pdf"), guide=Path("nexiste-pas.md"))

    def test_edge_absent(self):
        with (mock.patch.object(guide_pdf, "trouver_edge", return_value=None),
              self.assertRaises(ErreurGuidePdf) as c):
            guide_pdf.generer_guide(Path("s.pdf"))
        self.assertIn("Edge", str(c.exception))


class ProduireGuidePdfTest(unittest.TestCase):
    def test_nom_a_cote_de_l_installeur(self):
        self.assertEqual(guide_pdf.nom_guide_pdf("3.7.0"), "MeetNote-3.7.0-guide.pdf")

    def test_succes_journalise_le_chemin(self):
        with tempfile.TemporaryDirectory() as tmp:
            def generer(sortie):
                Path(sortie).write_bytes(b"%PDF")
            journal, alertes = [], []
            chemin = guide_pdf.produire_guide_pdf(
                "3.7.0", tmp, journal=journal.append, alertes=alertes,
                generer=generer)
            self.assertEqual(Path(chemin), Path(tmp) / "MeetNote-3.7.0-guide.pdf")
        self.assertEqual(alertes, [])
        self.assertIn("Guide PDF", journal[0])

    def test_echec_devient_une_alerte_sans_exception(self):
        def generer(sortie):
            raise ErreurGuidePdf("Microsoft Edge est introuvable")
        journal, alertes = [], []
        chemin = guide_pdf.produire_guide_pdf(
            "3.7.0", "dist", journal=journal.append, alertes=alertes,
            generer=generer)
        self.assertIsNone(chemin)
        self.assertEqual(len(alertes), 1)
        self.assertIn("guide PDF non produit", alertes[0])
        self.assertTrue(journal[0].startswith("ATTENTION"))


if __name__ == "__main__":
    unittest.main()
