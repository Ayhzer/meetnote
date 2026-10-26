"""
assistant_fenetre.py — Fenetre de l'assistant de demarrage (ecrans successifs).

Deux parties :

  - une partie pure (aucun tkinter, aucun reseau direct) : validation des champs
    de l'ecran Notion et enchainement « creer ou verifier la base » puis « page de
    test », avec les messages affiches a l'utilisateur. Toute la logique Notion vient
    de `assistant_notion` ; elle est testee sans ecran (`test_assistant_fenetre.py`) ;
  - une partie graphique : `AssistantDemarrage`, un `Toplevel` qui montre un ecran a
    la fois dans un conteneur unique, avec « Suivant ». Les appels reseau tournent
    dans un thread, le retour a l'interface passe par `after(0, ...)`.

Le jeton et l'identifiant de base valides a l'ecran 1, puis les exclusions de
l'ecran 3, restent en memoire dans l'objet assistant : rien n'est ecrit sur disque
tant que l'utilisateur n'a pas clique « Terminer » (ecran 4, apres un test audio dont
le micro n'est pas nul). `a_la_fin` est alors appele, `a_la_fermeture` jamais.

Importer ce module n'ouvre aucune fenetre.
"""
import logging
import threading
import tkinter as tk
import webbrowser
from collections.abc import Callable
from typing import NamedTuple

import assistant_agenda as aag
import assistant_audio as aa
import assistant_notion as an
import user_config

log = logging.getLogger(__name__)

# ── Couleurs et polices : reprises de la fenetre des parametres (meetnote-tray.py) ──
# meetnote-tray.py n'est pas importable (nom avec tiret) : valeurs recopiees ici.
S_MAIN = "#16213e"
S_CARD = "#1e1e32"
S_HIGH = "#28283d"
FG = "#e2e0fc"
FG2 = "#e4beba"
FG3 = "#ab8985"
RED = "#dc3232"
RED_DIM = "#b81c1c"
ERR = "#ff4444"
OK = "#4caf50"  # absent de la fenetre des parametres : vert de reussite
AVERT = "#ffb300"  # avertissement non bloquant (son du PC muet)
POLICE = "Segoe UI"

# ── Textes de l'ecran Notion ─────────────────────────────────────────────────
URL_INTEGRATIONS = "https://www.notion.so/my-integrations"
TITRE_FENETRE = "Assistant de démarrage MeetNote"
TITRE_ECRAN_NOTION = "1. Connexion à Notion"
LIBELLE_LIEN_INTEGRATION = "Créer mon intégration Notion (ouvre le navigateur)"
EXPLICATIONS_NOTION = (
    "• Créez une intégration sur la page ci-dessus et copiez son jeton secret.",
    (
        "• Dans Notion, ouvrez la page qui accueillera vos transcriptions (ou une "
        "base MeetNote existante) puis, menu ··· → Connexions, partagez la page "
        "avec l'intégration."
    ),
    (
        "• Collez ci-dessous le jeton et le lien de cette page : MeetNote crée la "
        "base « Transcriptions » (ou vérifie la vôtre) et envoie une page de test."
    ),
)
LIBELLE_JETON = "Jeton de l'intégration"
LIBELLE_LIEN = "Lien de la page parente ou de la base existante"
LIBELLE_SUIVANT = "Suivant"
MSG_EN_COURS = "Vérification en cours, un instant…"

MSG_JETON_VIDE ="Collez le jeton secret de votre intégration Notion."
MSG_LIEN_VIDE = "Collez le lien de la page Notion (ou de la base) à utiliser."
MSG_LIEN_INVALIDE = (
    "Ce lien n'est pas un lien Notion reconnu. Dans Notion, ouvrez la page puis "
    "copiez son lien (menu ··· → Copier le lien)."
)
MSG_ERREUR_INATTENDUE = "Une erreur inattendue est survenue : {detail}"

# ── Textes des ecrans agenda, exclusions et audio ────────────────────────────
TITRE_ECRAN_AGENDA = "2. Votre agenda"
MSG_DETECTION_AGENDA = "Recherche de votre agenda, un instant…"
TITRE_ECRAN_EXCLUSIONS = "3. Réunions à ne pas enregistrer"
EXPLICATIONS_EXCLUSIONS = (
    "Une réunion dont le titre contient l'un de ces mots n'est pas enregistrée "
    "automatiquement. Un mot par ligne ; vous pourrez les modifier plus tard dans "
    "les paramètres."
)
TITRE_ECRAN_AUDIO = "4. Test du son"
EXPLICATIONS_AUDIO = (
    "Le test dure environ 10 secondes : 5 s pour le micro (parlez normalement), puis "
    "5 s pour le son du PC (un court son d'essai est joué)."
)
LIBELLE_TEST_AUDIO = "Lancer le test"
LIBELLE_TERMINER = "Terminer"
LIBELLE_MICRO = "Micro"
LIBELLE_SON_PC = "Son du PC"
MSG_TEST_EN_COURS = (
    "Test en cours : parlez maintenant, puis laissez jouer le son d'essai…"
)
MSG_AUDIO_OK = "Le micro et le son du PC fonctionnent. Vous pouvez terminer."
MSG_TEST_REQUIS = (
    "Lancez le test du son : le micro doit capter votre voix pour terminer."
)
MSG_TEST_PENDANT_ENREGISTREMENT = (
    "Un enregistrement est en cours : le test du son aurait besoin du micro et "
    "du son du PC. Attendez la fin de l'enregistrement, puis relancez le test."
)
MSG_ENREGISTREMENT_IMPOSSIBLE = (
    "Les réglages n'ont pas pu être enregistrés : {detail}"
)
MSG_RIEN_A_ENREGISTRER = (
    "La connexion Notion n'est pas validée : revenez à l'écran 1."
)

# Cles de `settings.json` : celles que lit l'application (meetnote-tray.py).
CLE_JETON = "notion_token"
CLE_BASE = "notion_database_id"
CLE_EXCLUSIONS = "auto_start_exclusions"
CLE_TERMINE = "assistant_termine"

LARGEUR_VUMETRE = 360
HAUTEUR_VUMETRE = 14


# ── Partie pure ──────────────────────────────────────────────────────────────

class ResultatNotion(NamedTuple):
    """Issue de l'ecran Notion : `base_id` n'est renseigne que si le test a reussi."""

    ok: bool
    message: str
    base_id: str | None = None


def valider_champs(token: str, lien: str) -> tuple[str, str]:
    """Verifie les deux champs saisis ; rend (jeton nettoye, identifiant).

    Leve `ValueError` avec un message pret a afficher si un champ est vide ou si le
    lien n'est pas un lien Notion exploitable.
    """
    token_propre = (token or "").strip()
    lien_propre = (lien or "").strip()
    if not token_propre:
        raise ValueError(MSG_JETON_VIDE)
    if not lien_propre:
        raise ValueError(MSG_LIEN_VIDE)
    identifiant = an.extraire_identifiant(lien_propre)
    if identifiant is None:
        raise ValueError(MSG_LIEN_INVALIDE)
    return token_propre, identifiant


def message_schema_non_conforme(constats: list[str]) -> str:
    """Message en clair listant ce qui manque dans la base existante."""
    lignes = "\n".join(f"• {constat}" for constat in constats)
    return (
        "Cette base ne correspond pas à ce que MeetNote attend. Corrigez-la dans "
        "Notion, ou donnez plutôt le lien d'une page pour en créer une neuve :\n"
        f"{lignes}"
    )


def message_succes(base_creee: bool) -> str:
    """Message de reussite : base creee ou verifiee, puis page de test envoyee."""
    base = (
        "La base « Transcriptions » a été créée"
        if base_creee
        else "Votre base Notion est conforme"
    )
    return f"{base} et une page de test y a été envoyée. Notion est prêt."


def _base_a_utiliser(
    token: str, identifiant: str, bases_creees: dict[str, str]
) -> tuple[str | None, bool, list[str]]:
    """Rend (base_id, creee_maintenant, constats) pour l'identifiant donne.

    `bases_creees` memorise les bases deja creees sous une page parente : si le test
    d'envoi echoue et que l'utilisateur reessaie, on reutilise la base au lieu d'en
    creer une seconde. Seule `ErreurNotion` peut etre levee.
    """
    if identifiant in bases_creees:
        return bases_creees[identifiant], False, []
    if an.nature_lien(token, identifiant) == "page":
        base = an.creer_base(token, identifiant)
        bases_creees[identifiant] = base.base_id
        return base.base_id, True, []
    constats = an.comparer_schema(an.lire_schema(token, identifiant))
    return (None if constats else identifiant), False, constats


def executer_etape_notion(
    token: str, lien: str, bases_creees: dict[str, str]
) -> ResultatNotion:
    """Enchaine validation, creation ou verification de la base, puis page de test.

    Ne leve jamais : tout echec devient un `ResultatNotion(ok=False, message)` en
    francais. Le jeton n'apparait jamais dans le message.
    """
    try:
        token_propre, identifiant = valider_champs(token, lien)
    except ValueError as erreur:
        return ResultatNotion(False, str(erreur))
    try:
        base_id, creee, constats = _base_a_utiliser(
            token_propre, identifiant, bases_creees
        )
        if base_id is None:
            return ResultatNotion(False, message_schema_non_conforme(constats))
        an.envoyer_page_test(token_propre, base_id)
    except an.ErreurNotion as erreur:
        return ResultatNotion(False, erreur.message)
    return ResultatNotion(True, message_succes(creee), base_id)


def exclusions_initiales(enregistrees: str | None) -> str:
    """Texte prerempli de l'ecran 3 : exclusions deja enregistrees, sinon le defaut."""
    if enregistrees and enregistrees.strip():
        return enregistrees
    return user_config.EXCLUSIONS_PAR_DEFAUT


def normaliser_exclusions(texte: str | None) -> str:
    """Un motif par ligne : espaces de bord et lignes vides retires."""
    lignes = ((ligne or "").strip() for ligne in (texte or "").splitlines())
    return "\n".join(ligne for ligne in lignes if ligne)


def valeurs_a_enregistrer(
    token: str | None, base_id: str | None, exclusions: str | None
) -> dict:
    """Reglages ecrits par « Terminer », sous les cles lues par l'application.

    Leve `ValueError` si la connexion Notion n'a pas ete validee a l'ecran 1.
    """
    if not token or not base_id:
        raise ValueError(MSG_RIEN_A_ENREGISTRER)
    return {
        CLE_JETON: token,
        CLE_BASE: base_id,
        CLE_EXCLUSIONS: normaliser_exclusions(exclusions),
        CLE_TERMINE: True,
    }


def terminer_autorise(verdict: aa.VerdictAudio | None) -> bool:
    """« Terminer » n'est permis qu'apres un test dont le micro n'est pas nul."""
    return verdict is not None and verdict.accepte


def messages_audio(verdict: aa.VerdictAudio) -> tuple[str, str]:
    """Rend (texte, couleur) d'un verdict : refus, avertissement ou succes."""
    if verdict.refus:
        return "\n".join((verdict.refus, *verdict.avertissements)), ERR
    if verdict.avertissements:
        return "\n".join(verdict.avertissements), AVERT
    return MSG_AUDIO_OK, OK


class ResultatAudio(NamedTuple):
    """Niveaux mesures (None = capture impossible) et verdict qui en decoule."""

    niveau_micro: float | None
    niveau_pc: float | None
    verdict: aa.VerdictAudio


def _mesurer_ou_none(mesure: Callable[[], float]) -> float | None:
    try:
        return mesure()
    except aa.ErreurCapture:
        log.warning("Assistant : capture audio impossible", exc_info=True)
        return None


def refus_test_audio(
    enregistrement_en_cours: Callable[[], bool] | None,
) -> str | None:
    """Message en clair si le test audio doit etre refuse, sinon `None`.

    Le test ouvre le micro et la boucle audio : le lancer pendant un enregistrement
    risquerait de perturber la capture en cours.
    """
    if enregistrement_en_cours is not None and enregistrement_en_cours():
        return MSG_TEST_PENDANT_ENREGISTREMENT
    return None


def executer_test_audio(
    apres_micro: Callable[[float | None], None] | None = None,
) -> ResultatAudio:
    """Mesure le micro puis le son du PC ; `apres_micro` recoit le niveau du micro des
    qu'il est connu, pour que le vu-metre bouge avant la fin du test complet.

    Ne leve jamais `ErreurCapture` : une capture impossible vaut `None`.
    """
    niveau_micro = _mesurer_ou_none(aa.mesurer_micro)
    if apres_micro is not None:
        apres_micro(niveau_micro)
    niveau_pc = _mesurer_ou_none(aa.mesurer_son_pc)
    return ResultatAudio(
        niveau_micro, niveau_pc, aa.rendre_verdict(niveau_micro, niveau_pc)
    )


def largeur_barre(niveau: float | None, largeur_max: int) -> int:
    """Largeur en pixels de la barre d'un vu-metre pour un niveau 0-100."""
    if niveau is None or niveau <= 0:
        return 0
    return round(min(niveau, aa.NIVEAU_MAX) / aa.NIVEAU_MAX * largeur_max)


# ── Partie graphique ─────────────────────────────────────────────────────────

class _Ecran:
    """Base des ecrans : un cadre dans le conteneur, des etiquettes, `suivant()`."""

    titre = ""

    def __init__(self, assistant: "AssistantDemarrage", parent: tk.Frame):
        self.assistant = assistant
        self.frame = tk.Frame(parent, bg=S_CARD)
        self._construire()

    def _construire(self) -> None:
        raise NotImplementedError

    def suivant(self) -> None:
        """Valide l'ecran puis passe au suivant (par defaut : rien a valider)."""
        self.assistant.aller_suivant()

    def _etiquette(self, texte: str, **options) -> tk.Label:
        reglages = {"bg": S_CARD, "fg": FG2, "font": (POLICE, 9), "anchor": "w"}
        reglages.update(options)
        etiquette = tk.Label(self.frame, text=texte, **reglages)
        etiquette.pack(fill="x", pady=(0, 4))
        return etiquette

    def _existe(self) -> bool:
        """Faux si l'ecran est remplace ou la fenetre fermee (thread en retard)."""
        try:
            return bool(self.frame.winfo_exists())
        except tk.TclError:
            return False


class EcranNotion(_Ecran):
    """Ecran 1 : lien d'integration, jeton, lien de page ou de base."""

    titre = TITRE_ECRAN_NOTION

    def _champ(self, libelle: str, masque: bool) -> tk.Entry:
        self._etiquette(libelle, fg=FG3, pady=0)
        champ = tk.Entry(
            self.frame, bg=S_HIGH, fg=FG, insertbackground=FG, relief="flat",
            font=(POLICE, 10), width=60, show="•" if masque else "",
        )
        champ.pack(fill="x", ipady=5, pady=(0, 10))
        return champ

    def _construire(self) -> None:
        lien = tk.Label(
            self.frame, text=LIBELLE_LIEN_INTEGRATION, bg=S_CARD, fg=RED,
            font=(POLICE, 10, "underline"), cursor="hand2", anchor="w",
        )
        lien.pack(fill="x", pady=(0, 8))
        lien.bind("<Button-1>", lambda _evt: webbrowser.open(URL_INTEGRATIONS))
        for ligne in EXPLICATIONS_NOTION:
            self._etiquette(ligne, justify="left", wraplength=520)
        self.champ_token = self._champ(LIBELLE_JETON, masque=True)
        self.champ_lien = self._champ(LIBELLE_LIEN, masque=False)
        self.resultat = self._etiquette(
            "", justify="left", wraplength=520, font=(POLICE, 9, "bold")
        )

    def afficher_message(self, texte: str, couleur: str) -> None:
        self.resultat.config(text=texte, fg=couleur)

    def verrouiller(self, verrouille: bool) -> None:
        etat = "disabled" if verrouille else "normal"
        self.champ_token.config(state=etat)
        self.champ_lien.config(state=etat)

    def suivant(self) -> None:
        """Lance la verification dans un thread ; rien n'est enregistre ici."""
        token, lien = self.champ_token.get(), self.champ_lien.get()
        self.afficher_message(MSG_EN_COURS, FG3)
        self.verrouiller(True)
        self.assistant.bloquer_suivant(True)
        threading.Thread(
            target=self._travailler, args=(token, lien), daemon=True
        ).start()

    def _travailler(self, token: str, lien: str) -> None:
        """Corps du thread : aucun acces a tkinter hors `after`."""
        try:
            resultat = executer_etape_notion(
                token, lien, self.assistant.bases_creees
            )
        except Exception as erreur:  # le thread ne doit jamais mourir en silence
            log.exception("Assistant : echec inattendu de l'ecran Notion")
            resultat = ResultatNotion(
                False, MSG_ERREUR_INATTENDUE.format(detail=type(erreur).__name__)
            )
        self.assistant.dans_l_interface(
            lambda: self._fin_verification(token.strip(), resultat)
        )

    def _fin_verification(self, token: str, resultat: ResultatNotion) -> None:
        self.verrouiller(False)
        self.assistant.bloquer_suivant(False)
        self.afficher_message(resultat.message, OK if resultat.ok else ERR)
        if resultat.ok:
            self.assistant.valider_notion(token, resultat.base_id)


class EcranAgenda(_Ecran):
    """Ecran 2 : affiche le mode d'agenda detecte (information, rien a saisir)."""

    titre = TITRE_ECRAN_AGENDA

    def _construire(self) -> None:
        self.resultat = self._etiquette(
            MSG_DETECTION_AGENDA, justify="left", wraplength=520,
            font=(POLICE, 10, "bold"), fg=FG,
        )
        # La detection peut toucher COM (Outlook) : jamais dans le thread tkinter.
        threading.Thread(target=self._detecter, daemon=True).start()

    def _detecter(self) -> None:
        try:
            phrase = aag.detecter_agenda().phrase
        except Exception:  # le thread ne doit jamais mourir en silence
            log.exception("Assistant : echec de la detection de l'agenda")
            phrase = aag.PHRASES[aag.MODE_MANUEL]
        self.assistant.dans_l_interface(lambda: self._afficher(phrase))

    def _afficher(self, phrase: str) -> None:
        if self._existe():
            self.resultat.config(text=phrase)


class EcranExclusions(_Ecran):
    """Ecran 3 : zone multi-ligne des motifs de reunions a ne pas enregistrer."""

    titre = TITRE_ECRAN_EXCLUSIONS

    def _construire(self) -> None:
        self._etiquette(EXPLICATIONS_EXCLUSIONS, justify="left", wraplength=520)
        self.zone = tk.Text(
            self.frame, bg=S_HIGH, fg=FG, insertbackground=FG, relief="flat",
            font=(POLICE, 10), width=60, height=10,
        )
        self.zone.pack(fill="both", expand=True, pady=(4, 0))
        self.zone.insert(
            "1.0", exclusions_initiales(user_config.get(CLE_EXCLUSIONS, ""))
        )

    def suivant(self) -> None:
        self.assistant.exclusions = normaliser_exclusions(self.zone.get("1.0", "end"))
        self.assistant.aller_suivant()


class VuMetre:
    """Barre horizontale de niveau 0-100 avec son libelle."""

    def __init__(self, parent: tk.Frame, libelle: str):
        ligne = tk.Frame(parent, bg=S_CARD)
        ligne.pack(fill="x", pady=4)
        tk.Label(
            ligne, text=libelle, width=10, anchor="w", bg=S_CARD, fg=FG2,
            font=(POLICE, 9),
        ).pack(side="left")
        self.canevas = tk.Canvas(
            ligne, width=LARGEUR_VUMETRE, height=HAUTEUR_VUMETRE, bg=S_HIGH,
            highlightthickness=0,
        )
        self.canevas.pack(side="left")
        self.barre = self.canevas.create_rectangle(
            0, 0, 0, HAUTEUR_VUMETRE, fill=OK, width=0
        )

    def afficher(self, niveau: float | None) -> None:
        largeur = largeur_barre(niveau, LARGEUR_VUMETRE)
        self.canevas.coords(self.barre, 0, 0, largeur, HAUTEUR_VUMETRE)


class EcranAudio(_Ecran):
    """Ecran 4 : test de 5 s du micro puis du son du PC ; bloque « Terminer »."""

    titre = TITRE_ECRAN_AUDIO

    def _construire(self) -> None:
        self.verdict: aa.VerdictAudio | None = None
        self._etiquette(EXPLICATIONS_AUDIO, justify="left", wraplength=520)
        self.vu_micro = VuMetre(self.frame, LIBELLE_MICRO)
        self.vu_pc = VuMetre(self.frame, LIBELLE_SON_PC)
        self.bouton_test = tk.Button(
            self.frame, text=LIBELLE_TEST_AUDIO, font=(POLICE, 10), bg=S_HIGH, fg=FG,
            activebackground=S_MAIN, activeforeground=FG, relief="flat", bd=0,
            padx=14, pady=5, cursor="hand2", command=self.lancer_test,
        )
        self.bouton_test.pack(anchor="w", pady=(8, 8))
        self.resultat = self._etiquette(
            MSG_TEST_REQUIS, justify="left", wraplength=520,
            font=(POLICE, 9, "bold"), fg=FG3,
        )
        self.assistant.bloquer_suivant(True)

    def lancer_test(self) -> None:
        """Lance la mesure dans un thread ; « Terminer » attend le verdict."""
        refus = refus_test_audio(self.assistant.enregistrement_en_cours)
        if refus is not None:
            self.resultat.config(text=refus, fg=AVERT)
            return
        self.verdict = None
        self.bouton_test.config(state="disabled")
        self.assistant.bloquer_suivant(True)
        self.vu_micro.afficher(None)
        self.vu_pc.afficher(None)
        self.resultat.config(text=MSG_TEST_EN_COURS, fg=FG3)
        threading.Thread(target=self._travailler, daemon=True).start()

    def _travailler(self) -> None:
        """Corps du thread : aucun acces a tkinter hors `after`."""
        try:
            resultat = executer_test_audio(
                lambda niveau: self.assistant.dans_l_interface(
                    lambda: self._montrer_micro(niveau)
                )
            )
        except Exception:  # le thread ne doit jamais mourir en silence
            log.exception("Assistant : echec inattendu du test audio")
            resultat = ResultatAudio(None, None, aa.rendre_verdict(None, None))
        self.assistant.dans_l_interface(lambda: self._fin_test(resultat))

    def _montrer_micro(self, niveau: float | None) -> None:
        if self._existe():
            self.vu_micro.afficher(niveau)

    def _fin_test(self, resultat: ResultatAudio) -> None:
        if not self._existe():
            return
        self.verdict = resultat.verdict
        self.vu_micro.afficher(resultat.niveau_micro)
        self.vu_pc.afficher(resultat.niveau_pc)
        texte, couleur = messages_audio(resultat.verdict)
        self.resultat.config(text=texte, fg=couleur)
        self.bouton_test.config(state="normal")
        self.assistant.bloquer_suivant(not terminer_autorise(resultat.verdict))

    def suivant(self) -> None:
        """« Terminer » : enregistre, sauf si le micro n'a pas ete valide."""
        if not terminer_autorise(self.verdict):
            self.resultat.config(text=MSG_TEST_REQUIS, fg=ERR)
            return
        erreur = self.assistant.terminer()
        if erreur is not None:
            self.resultat.config(text=erreur, fg=ERR)


class AssistantDemarrage:
    """Fenetre de l'assistant : un conteneur, un ecran a la fois, bouton « Suivant ».

    Le dernier ecran de `FABRIQUES_ECRANS` fait de « Suivant » un « Terminer ».
    `token_notion` et `base_id` ne sont renseignes qu'apres un test d'envoi reussi,
    et seulement en memoire ; tout est ecrit d'un coup par `terminer()`.
    `a_la_fin` est appele apres l'enregistrement reussi (la fenetre est alors
    fermee), `a_la_fermeture` uniquement si l'utilisateur abandonne.
    `enregistrement_en_cours` (optionnel) est consulte avant chaque test audio,
    qui est refuse tant qu'un enregistrement est en cours.
    """

    FABRIQUES_ECRANS: tuple[Callable, ...] = (
        EcranNotion, EcranAgenda, EcranExclusions, EcranAudio,
    )

    def __init__(
        self,
        parent: tk.Misc,
        a_la_fermeture: Callable[[], None] | None = None,
        a_la_fin: Callable[[], None] | None = None,
        enregistrement_en_cours: Callable[[], bool] | None = None,
    ):
        self.token_notion: str | None = None
        self.base_id: str | None = None
        self.exclusions: str = ""
        self.bases_creees: dict[str, str] = {}
        self._a_la_fermeture = a_la_fermeture
        self._a_la_fin = a_la_fin
        self.enregistrement_en_cours = enregistrement_en_cours
        self._index = -1
        self._ecran = None

        self.win = tk.Toplevel(parent)
        self.win.title(TITRE_FENETRE)
        self.win.configure(bg=S_CARD)
        self.win.resizable(False, False)
        self.win.protocol("WM_DELETE_WINDOW", self.fermer)
        self._construire_cadre()
        self._aller_a(0)

    def _construire_cadre(self) -> None:
        entete = tk.Frame(self.win, bg=S_MAIN)
        entete.pack(fill="x")
        self.titre_ecran = tk.Label(
            entete, text="", font=(POLICE, 11, "bold"), bg=S_MAIN, fg=FG
        )
        self.titre_ecran.pack(side="left", padx=16, pady=12)
        self.conteneur = tk.Frame(self.win, bg=S_CARD, padx=20, pady=14)
        self.conteneur.pack(fill="both", expand=True)
        pied = tk.Frame(self.win, bg=S_CARD, padx=20, pady=12)
        pied.pack(fill="x")
        self.bouton_suivant = tk.Button(
            pied, text=LIBELLE_SUIVANT, font=(POLICE, 10, "bold"), bg=RED, fg="white",
            activebackground=RED_DIM, activeforeground="white", relief="flat",
            bd=0, padx=18, pady=6, cursor="hand2", command=self.suivant,
        )
        self.bouton_suivant.pack(side="right")

    def _aller_a(self, index: int) -> None:
        """Remplace l'ecran affiche par celui d'indice `index`."""
        if self._ecran is not None:
            self._ecran.frame.destroy()
        self._index = index
        dernier = index == len(self.FABRIQUES_ECRANS) - 1
        libelle = LIBELLE_TERMINER if dernier else LIBELLE_SUIVANT
        self.bouton_suivant.config(text=libelle)
        self.bloquer_suivant(False)
        self._ecran = self.FABRIQUES_ECRANS[index](self, self.conteneur)
        self._ecran.frame.pack(fill="both", expand=True)
        self.titre_ecran.config(text=self._ecran.titre)

    def suivant(self) -> None:
        """« Suivant » : l'ecran courant verifie ses champs avant toute navigation."""
        self._ecran.suivant()

    def bloquer_suivant(self, bloque: bool) -> None:
        self.bouton_suivant.config(state="disabled" if bloque else "normal")

    def dans_l_interface(self, action: Callable[[], None]) -> None:
        """Execute `action` dans le thread de l'interface (appel depuis un thread)."""

        def _si_ouverte() -> None:
            if self.win.winfo_exists():
                action()

        try:
            self.win.after(0, _si_ouverte)
        except (tk.TclError, RuntimeError):
            # Fenetre fermee pendant la verification : plus personne a informer.
            log.debug("Assistant : fenetre fermee avant la fin de la verification")

    def valider_notion(self, token: str, base_id: str) -> None:
        """Garde en memoire le couple valide, puis passe a l'ecran suivant."""
        self.token_notion = token
        self.base_id = base_id
        self.aller_suivant()

    def aller_suivant(self) -> None:
        """Affiche l'ecran suivant (sans effet sur le dernier : voir `terminer`)."""
        if self._index + 1 < len(self.FABRIQUES_ECRANS):
            self._aller_a(self._index + 1)

    def terminer(self) -> str | None:
        """Enregistre les reglages en une fois, ferme, puis appelle `a_la_fin`.

        Rend un message d'erreur en francais si l'enregistrement echoue (la fenetre
        reste ouverte), sinon `None`.
        """
        try:
            user_config.save(
                valeurs_a_enregistrer(self.token_notion, self.base_id, self.exclusions)
            )
        except ValueError as erreur:
            return str(erreur)
        except OSError as erreur:
            log.exception("Assistant : enregistrement impossible")
            return MSG_ENREGISTREMENT_IMPOSSIBLE.format(detail=type(erreur).__name__)
        self.win.destroy()
        if self._a_la_fin is not None:
            self._a_la_fin()
        return None

    def fermer(self) -> None:
        """Ferme la fenetre sans rien enregistrer."""
        self.win.destroy()
        if self._a_la_fermeture is not None:
            self._a_la_fermeture()


def ouvrir_assistant(
    parent: tk.Misc,
    a_la_fermeture: Callable[[], None] | None = None,
    a_la_fin: Callable[[], None] | None = None,
    enregistrement_en_cours: Callable[[], bool] | None = None,
) -> AssistantDemarrage:
    """Ouvre l'assistant devant `parent` (la racine tkinter de l'application)."""
    return AssistantDemarrage(
        parent, a_la_fermeture, a_la_fin, enregistrement_en_cours
    )
