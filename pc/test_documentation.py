"""
Tests de la documentation livrée avec MeetNote : le prompt de l'agent Claude
(shared/prompt_agent_claude.md) doit rester anonyme, borné, et cohérent avec ce que
`notion_push.py` écrit réellement dans Notion.

Le code de notion_push.py est lu comme du texte : l'importer tirerait `requests` et la
configuration locale (config.py, gitignoré), inutiles ici.

Convention du prompt : toute propriété Notion citée est écrite entre `backticks` ; les
valeurs de propriété s'écrivent entre guillemets « ». Rien d'autre n'utilise de backticks.

Convention du guide d'installation (GUIDE-INSTALLATION.md) : tout texte entre guillemets
français « » est un libellé de l'application et doit exister dans l'un des fichiers de
LIBELLES_SOURCES, sauf les noms de Windows listés dans LIBELLES_HORS_APPLICATION. Les
valeurs, noms de fichiers et exemples s'écrivent entre `backticks`, jamais entre « ».

Lancement : py -3.12 pc\test_documentation.py
"""
import re
import unittest
from pathlib import Path

DOSSIER_PC = Path(__file__).resolve().parent
RACINE = DOSSIER_PC.parent
PROMPT = RACINE / "shared" / "prompt_agent_claude.md"
NOTION_PUSH = DOSSIER_PC / "notion_push.py"
GUIDE = RACINE / "GUIDE-INSTALLATION.md"

# Motifs qui ne doivent jamais apparaître dans un document livré à un tiers :
# identité de l'auteur, établissement, jetons Notion. Partagé avec les tests du guide.
MOTIFS_PERSONNELS = ("apottier", "for.paris", "Rothschild", "HFAR", "ntn_", "secret_")

PLAFOND_TRANSCRIPTS = 3
STATUT_A_TRAITER = "À traiter"
STATUT_TRAITE = "Traité"


def lire(chemin: Path) -> str:
    return chemin.read_text(encoding="utf-8")


def proprietes_ecrites_par_notion_push() -> set:
    """Noms des propriétés que notion_push.py envoie à Notion (littéraux du dict `props`)."""
    source = lire(NOTION_PUSH)
    litteraux = re.findall(r'^\s*"([^"]+)":\s*\{"(?:title|date|select|number|files)"', source, re.MULTILINE)
    affectations = re.findall(r'props\["([^"]+)"\]\s*=', source)
    return set(litteraux) | set(affectations)


def proprietes_citees(texte: str) -> set:
    """Noms écrits entre backticks dans un document (convention de la documentation)."""
    return set(re.findall(r"`([^`\n]+)`", texte))


def assert_sans_motif_personnel(cas: unittest.TestCase, texte: str, nom: str) -> None:
    """Échoue si `texte` contient un motif de MOTIFS_PERSONNELS (réutilisable par d'autres documents)."""
    bas = texte.lower()
    for motif in MOTIFS_PERSONNELS:
        cas.assertNotIn(motif.lower(), bas, f"{nom} contient le motif personnel « {motif} »")


class TestPromptAgentClaude(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.prompt = lire(PROMPT)

    def test_plafond_de_transcripts_par_execution(self):
        self.assertIn(f"au plus {PLAFOND_TRANSCRIPTS} transcripts par exécution", self.prompt)

    def test_les_deux_statuts_sont_presents(self):
        self.assertIn(f"« {STATUT_A_TRAITER} »", self.prompt)
        self.assertIn(f"« {STATUT_TRAITE} »", self.prompt)

    def test_regle_de_confidentialite_presente(self):
        self.assertIn("donnée de santé ni donnée RH", self.prompt)

    def test_proprietes_citees_ecrites_par_notion_push(self):
        ecrites = proprietes_ecrites_par_notion_push()
        # Garde-fou de l'extraction : si elle se vide, le test ne prouverait plus rien.
        self.assertGreaterEqual(len(ecrites), 7, f"extraction suspecte : {sorted(ecrites)}")
        citees = proprietes_citees(self.prompt)
        self.assertTrue(citees, "le prompt ne cite aucune propriété entre backticks")
        inconnues = citees - ecrites
        self.assertFalse(inconnues, f"propriétés citées mais jamais écrites : {sorted(inconnues)}")

    def test_proprietes_utiles_au_traitement_citees(self):
        citees = proprietes_citees(self.prompt)
        for nom in ("Statut", "Source", "Date", "Type"):
            self.assertIn(nom, citees)

    def test_statut_ecrit_par_notion_push(self):
        self.assertIn(f'"{STATUT_A_TRAITER}"', lire(NOTION_PUSH))

    def test_plus_de_source_mobile(self):
        self.assertNotIn("Mobile", self.prompt)

    def test_aucun_motif_personnel(self):
        assert_sans_motif_personnel(self, self.prompt, PROMPT.name)


# ── Guide d'installation ─────────────────────────────────────────────────────

# Fichiers où chercher les libellés cités par le guide : fenêtre principale et
# réglages, assistant de premier lancement, case « participants informés », installeur.
LIBELLES_SOURCES = (
    DOSSIER_PC / "meetnote-tray.py",
    DOSSIER_PC / "assistant_fenetre.py",
    DOSSIER_PC / "assistant_notion.py",
    DOSSIER_PC / "information_participants.py",
    DOSSIER_PC / "installer" / "meetnote.iss",
)

# Libellés cités par le guide qui ne sont pas ceux de MeetNote : alerte SmartScreen.
LIBELLES_HORS_APPLICATION = ("Informations complémentaires", "Exécuter quand même")

TITRES_GUIDE = (
    "# Guide d'installation de MeetNote",
    "## 1. Télécharger et installer",
    "## 2. Préparer Notion",
    "## 3. Suivre l'assistant de premier lancement",
    "## 4. Régler les exclusions",
    "## 5. Ce que fait MeetNote",
    "## Limites",
)
PHRASE_DPO = (
    "Ne pas utiliser pour des réunions contenant des données de santé "
    "sans avis du DPO."
)
LIGNES_MAX_GUIDE = 120
IMAGES_MIN, IMAGES_MAX = 4, 6
MOTS_MIN_TEXTE_ALTERNATIF = 4


def normaliser(texte: str) -> str:
    """Apostrophes droites et espaces simples, pour comparer guide et code source."""
    return " ".join(texte.replace("’", "'").split())


def libelles_cites(texte: str) -> list:
    """Textes écrits entre guillemets français « » dans un document."""
    return [normaliser(m) for m in re.findall(r"«([^»\n]+)»", texte)]


def images_du_guide(texte: str) -> list:
    """Couples (texte alternatif, chemin) des images Markdown du guide."""
    return re.findall(r"!\[([^\]]*)\]\(([^)]+)\)", texte)


class TestGuideInstallation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.guide = lire(GUIDE)
        cls.code = normaliser(" ".join(lire(f) for f in LIBELLES_SOURCES))

    def test_sections_attendues_dans_l_ordre(self):
        lignes = self.guide.splitlines()
        positions = [
            lignes.index(titre) if titre in lignes else -1 for titre in TITRES_GUIDE
        ]
        self.assertNotIn(-1, positions, f"titre manquant : {positions}")
        self.assertEqual(positions, sorted(positions))

    def test_phrase_dpo_telle_quelle(self):
        self.assertIn(PHRASE_DPO, self.guide)

    def test_aucun_motif_personnel(self):
        assert_sans_motif_personnel(self, self.guide, GUIDE.name)

    def test_tient_en_une_page(self):
        self.assertLessEqual(len(self.guide.splitlines()), LIGNES_MAX_GUIDE)

    def test_libelles_cites_existent_dans_l_application(self):
        cites = libelles_cites(self.guide)
        # Garde-fou de l'extraction : sans libellé trouvé, le test ne prouverait rien.
        self.assertGreaterEqual(len(cites), 8, f"extraction suspecte : {cites}")
        absents = [
            c for c in cites
            if c not in LIBELLES_HORS_APPLICATION and c not in self.code
        ]
        self.assertFalse(absents, f"libellés cités mais absents du code : {absents}")

    def test_exceptions_encore_utilisees(self):
        cites = libelles_cites(self.guide)
        for libelle in LIBELLES_HORS_APPLICATION:
            self.assertIn(libelle, cites)

    def test_images_dans_docs_captures_avec_texte_alternatif(self):
        images = images_du_guide(self.guide)
        self.assertTrue(
            IMAGES_MIN <= len(images) <= IMAGES_MAX, f"{len(images)} images"
        )
        for alternatif, chemin in images:
            self.assertRegex(chemin, r"^docs/captures/[a-z0-9-]+\.png$")
            mots = len(alternatif.split())
            self.assertGreaterEqual(
                mots, MOTS_MIN_TEXTE_ALTERNATIF, f"alt trop court : {chemin}"
            )

    def test_valeurs_par_defaut_citees_sont_celles_du_code(self):
        import retention
        import user_config
        self.assertIn(f"{retention.RETENTION_AUDIO_JOURS_DEFAUT} jours", self.guide)
        # « 6 mois » du guide
        self.assertEqual(retention.RETENTION_TRANSCRIPT_JOURS_DEFAUT, 180)
        for motif in user_config.EXCLUSIONS_PAR_DEFAUT.splitlines():
            self.assertIn(f"`{motif}`", self.guide)


if __name__ == "__main__":
    unittest.main()
