"""
source_agenda.py — Cache des réunions des 24 prochaines heures.

Module pur, sur le modèle de perf_profile.py : aucune lecture d'horloge, aucun accès à
Outlook ni au réseau. L'heure (`maintenant`) est toujours fournie par l'appelant, ce qui
rend chaque décision et chaque calcul testable avec une horloge fixe.

Le cache contient les réunions déjà filtrées par les sources (journée entière, > 12 h,
objets « off / absent / ooo… »), en cours comprises, au format de
`outlook_cal.get_events_range` : `subject`, `start`, `end` (datetimes naïfs locaux),
`location`, `body`, `organizer`, `required`, `optional`, `is_recurring`.

Usage côté application (un tick d'environ 60 s) :

    motif = cache.doit_recharger(maintenant)
    if motif and cache.essayer_reserver_rechargement():
        # dans un thread : lire les sources, puis
        cache.remplacer(reunions, maintenant)      # ou cache.marquer_echec(maintenant)
        cache.liberer_rechargement()

Le chargement depuis la chaîne de sources (`recharger`) est journalisé dans le logger
`meetnote.agenda`, une ligne par rechargement :

    [Agenda] rechargement motif=bouton source=ews réunions=7 durée=0.42s
"""
import copy
import datetime
import logging
import re
import threading
import time
import unicodedata
from collections.abc import Callable, Sequence
from typing import NamedTuple

import outlook_cal

log = logging.getLogger("meetnote.agenda")

# Âge du cache au-delà duquel on le recharge.
PERIODE_RECHARGEMENT = datetime.timedelta(minutes=15)

# Délai avant de retenter après un rechargement dont toutes les sources ont échoué
# (l'ancien contenu est conservé) : plus court que la période, pour qu'un Outlook ou un
# réseau pas encore prêts au démarrage ne privent pas l'utilisateur de 15 minutes.
DELAI_RETENTATIVE = datetime.timedelta(minutes=5)

# Tant qu'aucun chargement n'a réussi, le cache est vide : on réessaie à chaque tick
# (environ 60 s ; 30 s pour ne pas rater un tick d'un cheveu), sans quoi une réunion
# qui débute dans les minutes suivant le démarrage ne serait jamais auto-démarrée.
DELAI_RETENTATIVE_PREMIER = datetime.timedelta(seconds=30)

# Écart d'heure murale entre deux appels de `doit_recharger` au-delà duquel on conclut
# à une sortie de veille (le tick normal est d'environ 60 s).
SEUIL_SAUT_HORLOGE = datetime.timedelta(minutes=2)

# Horizon couvert par le cache, pour les sources qui le chargent.
HORIZON_CACHE = datetime.timedelta(hours=24)

# Motifs de rechargement, tels que T017 les journalisera.
MOTIF_PREMIER = "premier"
MOTIF_PERIODIQUE = "periodique"
MOTIF_VEILLE = "veille"
MOTIF_FORCE = "force"

# Libellés des motifs dans le journal (un seul mot, pour rester grep-able).
LIBELLES_MOTIFS = {
    MOTIF_PREMIER: "premier_chargement",
    MOTIF_PERIODIQUE: "périodique",
    MOTIF_VEILLE: "veille",
    MOTIF_FORCE: "bouton",
}

# Une source : (nom, fonction (debut, fin) -> liste de réunions, ou None si
# indisponible). Une liste vide est une réponse valable (« aucune réunion »).
Source = tuple[str, Callable[[datetime.datetime, datetime.datetime], list | None]]


class CacheAgenda:
    """Réunions à venir, décision de rechargement et calculs sur le contenu.

    Toutes les méthodes sont sûres entre threads : l'état est protégé par un verrou,
    le contenu n'est jamais exposé qu'en copie et se remplace d'un bloc.
    """

    def __init__(self):
        self._verrou = threading.Lock()
        self._reunions: list[dict] = []
        self._derniere_tentative: datetime.datetime | None = None
        self._dernier_echec = False
        self._jamais_charge = True
        self._dernier_appel: datetime.datetime | None = None
        self._veille_detectee = False
        self._force = False
        self._rechargement_en_cours = False

    # ------------------------------------------------------------------ décision

    def forcer(self) -> None:
        """Demande un rechargement au prochain tick (bouton « actualiser »)."""
        with self._verrou:
            self._force = True

    def doit_recharger(self, maintenant: datetime.datetime) -> str | None:
        """Motif du rechargement à lancer, ou None s'il n'y en a pas.

        À appeler à chaque tick : l'écart avec l'appel précédent sert à repérer une
        sortie de veille (saut de plus de `SEUIL_SAUT_HORLOGE`, dans un sens ou
        dans l'autre).
        La demande forcée et la veille détectée restent affichées jusqu'à ce qu'un
        rechargement soit réellement réservé, pour ne pas se perdre si un autre tourne.
        """
        with self._verrou:
            precedent, self._dernier_appel = self._dernier_appel, maintenant
            saut = None if precedent is None else abs(maintenant - precedent)
            if saut is not None and saut > SEUIL_SAUT_HORLOGE:
                self._veille_detectee = True
            if self._force:
                return MOTIF_FORCE
            if self._veille_detectee:
                return MOTIF_VEILLE
            if self._derniere_tentative is None:
                return MOTIF_PREMIER
            if not self._dernier_echec:
                delai = PERIODE_RECHARGEMENT
            elif self._jamais_charge:
                delai = DELAI_RETENTATIVE_PREMIER
            else:
                delai = DELAI_RETENTATIVE
            ecoule = maintenant - self._derniere_tentative
            # Une horloge revenue en arrière (écoulé négatif) vaut aussi rechargement.
            if ecoule >= delai or ecoule < datetime.timedelta(0):
                return MOTIF_PERIODIQUE
            return None

    # ------------------------------------------------- un seul rechargement à la fois

    def essayer_reserver_rechargement(self) -> bool:
        """Réserve le rechargement ; False si un autre est déjà en cours.

        Une réservation réussie consomme la demande forcée et la veille détectée.
        L'appelant doit appeler `liberer_rechargement` quoi qu'il arrive (try/finally).
        """
        with self._verrou:
            if self._rechargement_en_cours:
                return False
            self._rechargement_en_cours = True
            self._force = False
            self._veille_detectee = False
            return True

    def liberer_rechargement(self) -> None:
        """Libère la réservation prise par `essayer_reserver_rechargement`."""
        with self._verrou:
            self._rechargement_en_cours = False

    @property
    def rechargement_en_cours(self) -> bool:
        """Vrai si un rechargement tient la réservation (à ne pas confondre avec un échec)."""
        with self._verrou:
            return self._rechargement_en_cours

    # ------------------------------------------------------------------- contenu

    def remplacer(self, reunions: list[dict], maintenant: datetime.datetime) -> None:
        """Remplace d'un bloc le contenu du cache après un chargement réussi."""
        nouvelles = sorted(copy.deepcopy(reunions), key=lambda r: r["start"])
        with self._verrou:
            self._reunions = nouvelles
            self._derniere_tentative = maintenant
            self._dernier_echec = False
            self._jamais_charge = False

    def marquer_echec(self, maintenant: datetime.datetime) -> None:
        """Note une tentative dont toutes les sources ont échoué : contenu conservé."""
        with self._verrou:
            self._derniere_tentative = maintenant
            self._dernier_echec = True

    @property
    def jamais_charge(self) -> bool:
        """Vrai tant qu'aucun chargement n'a réussi (cache vide faute de données)."""
        with self._verrou:
            return self._jamais_charge

    def reunions(self) -> list[dict]:
        """Copie du contenu, triée par heure de début."""
        with self._verrou:
            return copy.deepcopy(self._reunions)

    # ------------------------------------------------------------------- calculs

    def reunions_en_cours(self, maintenant: datetime.datetime,
                          fenetre_min: int) -> list[dict]:
        """Réunions en cours ou démarrant dans `fenetre_min` minutes, triées par début.

        Même règle que `outlook_cal.get_all_current_meetings` : début ≤ maintenant +
        fenêtre et fin ≥ maintenant.
        """
        limite = maintenant + datetime.timedelta(minutes=fenetre_min)
        return [r for r in self.reunions()
                if r["start"] <= limite and r["end"] >= maintenant]

    def courante_ou_prochaine(self, maintenant: datetime.datetime,
                              fenetre_min: int) -> dict | None:
        """Première réunion (par début) de `reunions_en_cours`, sinon None."""
        reunions = self.reunions_en_cours(maintenant, fenetre_min)
        return reunions[0] if reunions else None

    def suivante_apres(self, fin: datetime.datetime, ecart_max_min: int,
                       exclure_objet: str = "") -> dict | None:
        """Première réunion démarrant entre 0 et `ecart_max_min` minutes après `fin`,
        dont l'objet diffère de `exclure_objet` (la réunion qui se termine)."""
        for reunion in self.reunions():
            ecart = (reunion["start"] - fin).total_seconds() / 60
            if 0 <= ecart <= ecart_max_min and reunion["subject"] != exclure_objet:
                return reunion
        return None


# ------------------------------------------------------------- chaîne de sources


def source_index(debut: datetime.datetime, fin: datetime.datetime) -> list | None:
    """Index de messagerie : toujours indisponible pour l'instant (branchée en T021)."""
    return None


def sources_par_defaut() -> list[Source]:
    """Chaîne par défaut, dans l'ordre : index, EWS, COM (Outlook en dernier)."""
    return [
        ("index", source_index),
        ("ews", outlook_cal.evenements_ews),
        ("com", outlook_cal.evenements_com),
    ]


def premiere_source(sources: Sequence[Source], debut: datetime.datetime,
                    fin: datetime.datetime) -> tuple[str, list] | None:
    """Première source qui ne rend pas None, sous la forme (nom, réunions).

    Une source qui lève une exception est indisponible, au même titre qu'un None :
    elle est journalisée et la suivante est essayée. Rend None si aucune ne répond.
    Indépendante du cache : l'agenda 15 jours (T020) l'utilise avec d'autres bornes.
    """
    for nom, lire in sources:
        try:
            reunions = lire(debut, fin)
        except Exception as exc:  # noqa: BLE001 — une source en panne ne bloque pas les suivantes
            log.warning("[Agenda] source %s en erreur : %s: %s",
                        nom, type(exc).__name__, exc)
            continue
        if reunions is None:
            log.debug("[Agenda] source %s indisponible", nom)
            continue
        return nom, reunions
    return None


def recharger(cache: CacheAgenda, maintenant: datetime.datetime, motif: str,
              sources: Sequence[Source] | None = None,
              horloge_duree: Callable[[], float] = time.perf_counter) -> str | None:
    """Recharge le cache depuis la première source qui répond, et journalise.

    Lit [maintenant, maintenant + HORIZON_CACHE]. Ne fait rien (rend None) si un autre
    rechargement tient déjà la réservation. Si toutes les sources échouent, l'ancien
    contenu est conservé, la tentative est notée et un WARNING est écrit.
    Rend le nom de la source retenue, ou None (échec ou rechargement déjà en cours).
    """
    if not cache.essayer_reserver_rechargement():
        return None
    try:
        chaine = sources_par_defaut() if sources is None else sources
        libelle = LIBELLES_MOTIFS.get(motif, motif)
        debut_mesure = horloge_duree()
        retenue = premiere_source(chaine, maintenant, maintenant + HORIZON_CACHE)
        duree = horloge_duree() - debut_mesure
        if retenue is None:
            cache.marquer_echec(maintenant)
            log.warning("[Agenda] rechargement motif=%s source=aucune échec "
                        "(ancien cache conservé) durée=%.2fs", libelle, duree)
            return None
        nom, reunions = retenue
        cache.remplacer(reunions, maintenant)
        log.info("[Agenda] rechargement motif=%s source=%s réunions=%d durée=%.2fs",
                 libelle, nom, len(reunions), duree)
        return nom
    finally:
        cache.liberer_rechargement()


# --------------------------------------------------------------- participants

# Préfixe de la ligne placée en tête de la transcription envoyée.
PREFIXE_PARTICIPANTS = "Participants :"


def _cle_nom(nom: str) -> str:
    """Clé de comparaison : sans accents, sans casse, espaces normalisés."""
    decompose = unicodedata.normalize("NFKD", " ".join(nom.split()))
    sans_accents = "".join(c for c in decompose if not unicodedata.combining(c))
    return sans_accents.casefold()


_RE_ADRESSE_ENTRE_CHEVRONS = re.compile(r"<[^>]*>")


def _sans_adresse(nom: str) -> str:
    """Nom aux espaces normalisés, sans adresse e-mail ; chaîne vide s'il n'en reste rien.

    « Nom <a@b.fr> » devient « Nom » ; une entrée qui n'est qu'une adresse (EWS ou COM
    sans nom résolu) devient vide. Une adresse est toute chaîne contenant « @ ».
    """
    sans_chevrons = _RE_ADRESSE_ENTRE_CHEVRONS.sub(" ", nom)
    propre = " ".join(sans_chevrons.split())
    return "" if "@" in propre else propre


def fusionner_participants(*listes: Sequence | None) -> list[str]:
    """Fusionne des listes de noms, dans l'ordre donné, sans doublon.

    La comparaison ignore la casse, les accents et les espaces en trop ; la première
    graphie rencontrée est conservée. Les noms vides, les adresses e-mail (ligne
    « Participants » et panneau historique n'en affichent jamais) et les éléments qui
    ne sont pas des chaînes sont ignorés.
    """
    vus: set[str] = set()
    fusion: list[str] = []
    for liste in listes:
        for nom in liste or ():
            if not isinstance(nom, str):
                continue
            propre = _sans_adresse(nom)
            cle = _cle_nom(propre)
            if cle and cle not in vus:
                vus.add(cle)
                fusion.append(propre)
    return fusion


def invites_de_reunion(reunion: dict) -> list[str]:
    """Invités d'une réunion du cache : requis d'abord, puis facultatifs."""
    return fusionner_participants(reunion.get("required"), reunion.get("optional"))


def invites_pour_nom(objet_retenu: str, invites: Sequence[str],
                     nom_saisi: str) -> list[str]:
    """Invités à attribuer au job, ou [] si l'utilisateur a changé le nom de la réunion.

    Un nom saisi différent de l'objet de la réunion retenue signale que
    l'enregistrement ne correspond plus à cette réunion : on n'invente pas d'invités.
    """
    if _cle_nom(nom_saisi) != _cle_nom(objet_retenu):
        return []
    return list(invites)


def choisir_reunion(reunions: Sequence[dict], nom_saisi: str) -> dict | None:
    """Réunion à retenir parmi celles en cours (triées par début).

    Celle dont l'objet correspond au nom saisi (même comparaison que `invites_pour_nom`) :
    c'est la réunion choisie dans la fenêtre de conflit, qui n'est pas forcément la
    première. À défaut de correspondance, la première ; None s'il n'y en a aucune.
    """
    cle_saisie = _cle_nom(nom_saisi)
    if cle_saisie:
        for reunion in reunions:
            if _cle_nom(reunion["subject"]) == cle_saisie:
                return reunion
    return reunions[0] if reunions else None


class Preremplissage(NamedTuple):
    """Ce qu'il reste à appliquer à un enregistrement en cours ; None = rien à faire."""
    nom: str | None
    fin: datetime.datetime | None
    invites: tuple[str, list] | None


def completer_preremplissage(reunions: Sequence[dict], nom_saisi: str,
                             dernier_nom_auto: str,
                             fin_courante: datetime.datetime | None,
                             invites_retenus: tuple[str, list] | None) -> Preremplissage:
    """Complète, après un rechargement tardif du cache, ce que le démarrage n'avait pas pu
    pré-remplir faute de réunion connue. Ne remplace jamais un choix de l'utilisateur :

    - le nom, seulement s'il est vide ou égal au dernier nom posé automatiquement ;
    - l'heure de fin, seulement si elle est encore inconnue ;
    - les invités, seulement s'ils n'ont pas déjà été retenus.
    """
    retenue = choisir_reunion(reunions, nom_saisi)
    if retenue is None:
        return Preremplissage(None, None, None)
    nom = None
    if not nom_saisi.strip() or nom_saisi == dernier_nom_auto:
        nom = retenue["subject"]
    fin = retenue["end"] if fin_courante is None else None
    invites = None
    if invites_retenus is None:
        invites = (retenue["subject"], invites_de_reunion(retenue))
    return Preremplissage(nom, fin, invites)


def ligne_participants(participants: Sequence[str]) -> str:
    """« Participants : a, b, c », ou chaîne vide s'il n'y en a aucun."""
    noms = fusionner_participants(participants)
    return f"{PREFIXE_PARTICIPANTS} {', '.join(noms)}" if noms else ""


def avec_ligne_participants(texte: str, participants: Sequence[str]) -> str:
    """Place la ligne des participants en tête du texte transcrit.

    Sans effet s'il n'y a aucun participant ou si le texte la porte déjà (un texte
    renvoyé ne reçoit jamais la ligne deux fois).
    """
    ligne = ligne_participants(participants)
    if not ligne or texte.startswith(PREFIXE_PARTICIPANTS):
        return texte
    return f"{ligne}\n\n{texte}"
