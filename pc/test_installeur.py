"""Tests du script Inno Setup : lecture du .iss en texte, sans Inno Setup."""
import os
import re
import unittest

import demarrage_windows

CHEMIN_ISS = os.path.join(os.path.dirname(__file__), "installer", "meetnote.iss")
EXE_INSTALLE = r"C:\Users\moi\AppData\Local\Programs\MeetNote\MeetNote.exe"
DOSSIER_INSTALLE = os.path.dirname(EXE_INSTALLE)


def _lire_iss() -> str:
    # utf-8-sig : Inno Setup exige un BOM pour lire les accents en UTF-8
    with open(CHEMIN_ISS, encoding="utf-8-sig") as f:
        return f.read()


def _est_commentaire(ligne: str) -> bool:
    return ligne.lstrip().startswith((";", "//"))


def _sections(texte: str) -> dict[str, list[str]]:
    """Lignes utiles (sans vides ni commentaires de ligne) de chaque section."""
    sections: dict[str, list[str]] = {}
    courante = ""
    for ligne in texte.splitlines():
        ligne = ligne.strip()
        correspondance = re.fullmatch(r"\[(\w+)\]", ligne)
        if correspondance:
            courante = correspondance.group(1)
            sections[courante] = []
        elif ligne and not _est_commentaire(ligne):
            sections.setdefault(courante, []).append(ligne)
    return sections


def _valeur_run(ligne_registre: str) -> str:
    """Commande de la clé Run : ValueData déséchappé (\"\" -> \")."""
    correspondance = re.search(r'ValueData:\s*"((?:[^"]|"")*)"', ligne_registre)
    assert correspondance, ligne_registre
    return correspondance.group(1).replace('""', '"')


class InstalleurTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.texte = _lire_iss()
        cls.sections = _sections(cls.texte)
        cls.utile = "\n".join(
            ligne for ligne in cls.texte.splitlines() if not _est_commentaire(ligne)
        )

    def _setup(self, cle: str) -> str:
        for ligne in self.sections["Setup"]:
            nom, _, valeur = ligne.partition("=")
            if nom == cle:
                return valeur
        self.fail(f"Directive [Setup] absente : {cle}")

    def _ligne_run(self) -> str:
        lignes = [
            ligne for ligne in self.sections["Registry"]
            if "CurrentVersion\\Run" in ligne and 'ValueName: "MeetNote"' in ligne
        ]
        self.assertEqual(len(lignes), 1)
        return lignes[0]

    def test_installation_par_utilisateur_sans_elevation(self):
        self.assertEqual(self._setup("PrivilegesRequired"), "lowest")
        self.assertEqual(
            self._setup("DefaultDirName"), r"{localappdata}\Programs\MeetNote"
        )

    def test_app_id_fixe_et_litteral(self):
        # `{{` est l'échappement Inno d'une accolade ouvrante littérale
        self.assertRegex(
            self._setup("AppId"),
            r"\{\{[0-9A-F]{8}(-[0-9A-F]{4}){3}-[0-9A-F]{12}\}",
        )

    def test_version_et_source_recues_en_macro(self):
        self.assertRegex(self.texte, r"(?m)^#ifndef AppVersion\s+#error")
        self.assertRegex(self.texte, r"(?m)^#ifndef DossierSource\s+#error")
        self.assertEqual(self._setup("AppVersion"), "{#AppVersion}")
        self.assertEqual(
            self._setup("OutputBaseFilename"), "MeetNote-{#AppVersion}-installation"
        )
        self.assertTrue(
            any(ligne.startswith('Source: "{#DossierSource}')
                for ligne in self.sections["Files"])
        )

    def test_rien_en_dur(self):
        self.assertNotRegex(self.utile, r"(?i)[a-z]:\\")
        self.assertNotIn("apottier", self.utile.lower())
        self.assertNotRegex(self.utile, r"\d+\.\d+\.\d+")

    def test_raccourci_menu_demarrer(self):
        self.assertTrue(
            any(ligne.startswith('Name: "{autoprograms}\\MeetNote"')
                for ligne in self.sections["Icons"])
        )

    def test_francais(self):
        langues = "\n".join(self.sections["Languages"])
        self.assertIn(r"compiler:Languages\French.isl", langues)

    def test_demarrage_avec_windows_coche_par_defaut(self):
        taches = [ligne for ligne in self.sections["Tasks"]
                  if ligne.startswith('Name: "demarrage"')]
        self.assertEqual(len(taches), 1)
        self.assertIn('Description: "Démarrer avec Windows"', taches[0])
        self.assertNotIn("unchecked", taches[0])

    def test_cle_run_retiree_a_la_desinstallation(self):
        ligne = self._ligne_run()
        self.assertIn("HKCU", ligne)
        self.assertIn("uninsdeletevalue", ligne)
        self.assertIn("Tasks: demarrage", ligne)

    def test_commande_run_identique_a_celle_de_l_application(self):
        commande_iss = _valeur_run(self._ligne_run()).replace("{app}", DOSSIER_INSTALLE)
        attendue = demarrage_windows.commande_demarrage(True, EXE_INSTALLE, "")
        self.assertEqual(commande_iss, attendue)
        self.assertEqual(commande_iss, f'"{EXE_INSTALLE}" --minimized')
        self.assertEqual(demarrage_windows.NOM_VALEUR, "MeetNote")

    def test_lancement_en_fin_d_installation(self):
        lignes = [ligne for ligne in self.sections["Run"] if "postinstall" in ligne]
        self.assertEqual(len(lignes), 1)
        self.assertIn("skipifsilent", lignes[0])
        self.assertIn("nowait", lignes[0])
        self.assertIn('Description: "Lancer MeetNote"', lignes[0])
        self.assertNotIn("Parameters", lignes[0])

    def test_arret_sans_relance_avant_installation_et_desinstallation(self):
        code = "\n".join(self.sections["Code"])
        depot = code.index("clean_exit.flag")
        arret = code.index("taskkill.exe")
        self.assertLess(depot, arret, "le drapeau doit précéder l'arrêt des processus")
        self.assertIn("/F /T /IM", code)
        self.assertIn("NomProcessus = 'MeetNote.exe'", code)
        self.assertIn("ewWaitUntilTerminated", code)
        self.assertIn("SW_HIDE", code)
        self.assertRegex(code, r"function PrepareToInstall[\s\S]*?ArreterMeetNote\(\)")
        self.assertRegex(
            code, r"function InitializeUninstall[\s\S]*?ArreterMeetNote\(\)"
        )
        self.assertEqual(self._setup("CloseApplications"), "no")

    def test_desinstallation_annonce_les_dossiers_conserves(self):
        code = "\n".join(self.sections["Code"])
        self.assertIn("usPostUninstall", code)
        self.assertIn("SuppressibleMsgBox", code)
        for dossier in (r"{userappdata}\MeetNote", r"{localappdata}\MeetNote",
                        r"{userdocs}\MeetNote"):
            self.assertIn(dossier, code)

    def test_desinstallation_ne_touche_pas_aux_donnees(self):
        for section in ("UninstallDelete", "Files", "Dirs"):
            contenu = "\n".join(self.sections.get(section, []))
            for interdit in ("{userappdata}", r"{localappdata}\MeetNote", "{userdocs}"):
                self.assertNotIn(interdit, contenu, f"[{section}] touche {interdit}")

    def test_dossier_d_installation_fixe(self):
        # Sans page de choix, impossible d'installer dans Documents\MeetNote (dossier
        # des audios) : une désinstallation ne peut alors pas atteindre les données.
        self.assertEqual(self._setup("DisableDirPage"), "yes")
        self.assertEqual(self._setup("UsePreviousAppDir"), "no")
        self.assertEqual(
            self._setup("DefaultDirName"), r"{localappdata}\Programs\MeetNote"
        )
        self.assertNotIn("AppendDefaultDirName", self.utile)

    def test_aucune_suppression_recursive_du_dossier_programme_ou_des_donnees(self):
        # Inno retire déjà ce qu'il a installé : aucune entrée filesandordirs sur {app}
        # ni sur un dossier de données.
        for ligne in self.sections.get("UninstallDelete", []):
            self.assertNotIn("filesandordirs", ligne)
            self.assertNotIn('Name: "{app}"', ligne)
            for donnees in ("{userappdata}", "{localappdata}", "{userdocs}"):
                self.assertNotIn(donnees, ligne)


if __name__ == "__main__":
    unittest.main()
