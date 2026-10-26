"""
modeles.py — Modeles Whisper : lesquels, ou ils vivent, lequel choisir selon la RAM,
et comment les telecharger (progression, reprise, annulation).

Module pur (aucun import de l'interface) : le branchement dans l'application vient en
T027. Tous les chemins sont injectables pour que les tests n'ecrivent jamais dans le
vrai profil, et `huggingface_hub` n'est importe qu'au telechargement.
"""
import base64
import contextlib
import errno
import glob
import hashlib
import importlib
import os
import sys
import threading

# Modeles proposes dans l'interface, du plus leger au plus precis.
MODELES = ("tiny", "base", "small", "medium", "large-v3")

# Depot Hugging Face d'un modele : DEPOT_PREFIXE + nom.
DEPOT_PREFIXE = "Systran/faster-whisper-"

# Sous ce seuil de RAM totale (Gio), on choisit `small` ; a partir de lui, `medium`.
SEUIL_RAM_GIO = 8.0
MODELE_LEGER = "small"
MODELE_STANDARD = "medium"

# Valeur retenue quand la RAM ne peut pas etre mesuree : volontairement sous le seuil,
# elle conduit au modele leger.
RAM_PRUDENTE_GIO = 4.0

# Cle de settings.json qui surcharge le dossier des modeles (vide = defaut).
CLE_DOSSIER_MODELES = "dossier_modeles"

# Fichiers sans lesquels faster-whisper ne charge pas un modele. Le vocabulaire existe
# en .txt ou en .json selon le modele : l'un des deux suffit.
FICHIERS_REQUIS = ("model.bin", "config.json", "tokenizer.json")
FICHIERS_VOCABULAIRE = ("vocabulary.txt", "vocabulary.json")

# Lu par faster-whisper s'il existe, mais absent de certains depots : jamais exige.
FICHIERS_OPTIONNELS = ("preprocessor_config.json",)

# Seuls fichiers recuperes dans le depot (exclut README, .gitattributes, etc.).
MOTIFS_TELECHARGEMENT = FICHIERS_REQUIS + FICHIERS_VOCABULAIRE + FICHIERS_OPTIONNELS

# Reprise de huggingface_hub avec `local_dir` : les fichiers en cours de telechargement
# vivent dans <dossier>/.cache/huggingface/download/ et se terminent par `.incomplete`.
SOUS_DOSSIER_REPRISE = (".cache", "huggingface", "download")
SUFFIXE_INCOMPLET = ".incomplete"

# Periode de mesure de la progression sur le disque (secondes).
INTERVALLE_SUIVI_S = 0.5

# Pas d'interrogation de l'annulation pendant l'attente du verrou (secondes).
ATTENTE_VERROU_S = 0.2

# Un seul telechargement a la fois : evite aussi que deux appels se marchent dessus en
# basculant `HF_HUB_DISABLE_XET`.
_VERROU_TELECHARGEMENT = threading.Lock()

# Part maximale annoncee tant que le telechargement n'est pas termine : 100 % est reserve
# au dernier appel, une fois `modele_complet` verifie.
PLAFOND_AVANT_FIN_POURCENT = 99

# Code d'erreur Windows « disque plein » (ERROR_DISK_FULL), en plus d'errno.ENOSPC.
WINERROR_DISQUE_PLEIN = 112

# Noms de classes d'exception (toute la hierarchie est examinee), pour classer les
# erreurs sans importer huggingface_hub ni httpx.
_NOMS_DEPOT_INTROUVABLE = frozenset(
    {"RepositoryNotFoundError", "GatedRepoError", "RevisionNotFoundError"})
_NOMS_RESEAU = frozenset(
    {"LocalEntryNotFoundError", "OfflineModeIsEnabled", "TransportError",
     "ConnectionError", "TimeoutError"})
_NOM_ERREUR_HTTP_HF = "HfHubHTTPError"


# Cle de settings.json qui garde le modele choisi (vide = jamais choisi).
CLE_MODELE_CHOISI = "whisper_model"

# Delai avant de reessayer un telechargement qui a echoue (secondes).
DELAI_NOUVEL_ESSAI_S = 300

# Octets par « Mo » affiche (convention Windows : 1 Mo = 1 048 576 octets).
OCTETS_PAR_MO = 1024 * 1024

# Decisions possibles pour un job de la file.
PRET = "pret"
ATTENTE_MODELE = "attente_modele"

LIBELLE_ATTENTE_MODELE = "En attente du modèle…"

# Points d'activite animes a cote de la progression (granularite de 10 Mio de
# huggingface_hub : sans eux, l'affichage paraitrait fige entre deux paliers).
_POINTS_ACTIVITE = ("", ".", "..", "...")


class ErreurTelechargement(Exception):
    """Le modele n'a pas pu etre telecharge ; le message est destine a l'utilisateur."""


class TelechargementAnnule(Exception):
    """Telechargement interrompu a la demande ; les fichiers partiels sont conserves."""


def depot(nom: str) -> str:
    """Depot Hugging Face d'un modele (ex. Systran/faster-whisper-small)."""
    return DEPOT_PREFIXE + nom


def ram_totale_gio() -> float:
    """RAM totale en Gio. Renvoie une valeur prudente si la mesure echoue."""
    try:
        import psutil
        return psutil.virtual_memory().total / 2 ** 30
    except (ImportError, OSError):
        return RAM_PRUDENTE_GIO


def modele_par_defaut(ram_gio: float) -> str:
    """`small` sous le seuil de RAM, `medium` au-dela."""
    return MODELE_LEGER if ram_gio < SEUIL_RAM_GIO else MODELE_STANDARD


def modele_affiche(enregistre, defaut_config: str) -> str:
    """Modele de la liste : le choix enregistre s'il est valide, sinon le defaut."""
    nom = str(enregistre or "").strip()
    return nom if nom in MODELES else defaut_config


def modele_a_enregistrer(enregistre, ram_gio: float, deja_present: str | None = None):
    """Modele a ecrire dans settings.json au demarrage, ou None si rien a ecrire.

    Un choix enregistre valide n'est jamais ecrase. Sinon (premier lancement) : un
    modele deja complet sur le poste (`deja_present`, ex. ancienne installation avec
    `models\\`) est conserve, faute de quoi on retient le choix selon la RAM.
    """
    if str(enregistre or "").strip() in MODELES:
        return None
    if deja_present in MODELES:
        return deja_present
    return modele_par_defaut(ram_gio)


def transcription_requise(statut_transcript: str, transcript: str,
                          wav_path: str) -> bool:
    """Faux pour un job « transcript seul » (texte deja la, pas d'audio)."""
    return not (statut_transcript == "done" and bool(transcript) and not wav_path)


def decision_job(transcription_requise_: bool, modele_disponible: bool) -> str:
    """`PRET` ou `ATTENTE_MODELE` : transcrire sans le modele oblige a attendre."""
    if transcription_requise_ and not modele_disponible:
        return ATTENTE_MODELE
    return PRET


def sans_job(jobs, job_id) -> list:
    """Copie de `jobs` sans le job d'identifiant `job_id` (relance : une seule source
    de verite, le job ne reste pas dans la liste d'attente du modele)."""
    return [j for j in jobs if j.id != job_id]


def jobs_a_conserver(attente, file) -> list:
    """Jobs qui attendent encore reellement le modele : toujours `queued`, pas deja dans
    la file de traitement, une seule fois par identifiant (premier rencontre)."""
    ids_file = {j.id for j in file}
    vus = set()
    gardes = []
    for job in attente:
        if job.status_transcript != "queued" or job.id in ids_file or job.id in vus:
            continue
        vus.add(job.id)
        gardes.append(job)
    return gardes


def jobs_a_liberer(attente, file, modele_complet) -> list:
    """Jobs a remettre en file : ceux de `jobs_a_conserver` dont le modele est complet.
    `modele_complet(nom)` : callable booleen (injectable)."""
    return [j for j in jobs_a_conserver(attente, file) if modele_complet(j.model_name)]


def modeles_voulus(demandes, choix_courant, noms_attente) -> list:
    """Demandes de telechargement encore utiles, dans leur ordre : le modele choisi dans
    la liste ou celui qu'attend un job. Les autres sont abandonnees."""
    voulus = set(noms_attente) | {choix_courant}
    return [nom for nom in demandes if nom in voulus]


def est_libelle_echec(texte: str, nom: str) -> bool:
    """Vrai si `texte` est le libelle d'echec (`libelle_echec`) du modele `nom`."""
    return texte.startswith(f"Téléchargement du modèle {nom} impossible")


def _en_mo(octets: int) -> int:
    return int(octets // OCTETS_PAR_MO)


def libelle_demarrage(nom: str) -> str:
    """Texte tant que le premier rappel n'est pas arrive (verrou, metadonnees)."""
    return f"Téléchargement du modèle {nom} : en attente…"


def libelle_progression(nom: str, recus: int, total: int) -> str:
    """« Téléchargement du modèle small : 42 % — 210 / 480 Mo » ; total inconnu (0) :
    « Téléchargement du modèle small — 210 Mo reçus ». Jamais plus de 100 %."""
    recus = max(0, int(recus))
    if total <= 0:
        return f"Téléchargement du modèle {nom} — {_en_mo(recus)} Mo reçus"
    pourcent = min(100, recus * 100 // total)
    return (f"Téléchargement du modèle {nom} : {pourcent} % — "
            f"{_en_mo(min(recus, total))} / {_en_mo(total)} Mo")


def duree_lisible(secondes: int) -> str:
    """« 45 s », « 5 min » (arrondi a la minute superieure au-dela d'une minute)."""
    secondes = max(0, int(secondes))
    if secondes < 60:
        return f"{secondes} s"
    return f"{-(-secondes // 60)} min"


def libelle_echec(nom: str, cause: str, delai_s: int = DELAI_NOUVEL_ESSAI_S) -> str:
    """Cause en clair et delai avant le nouvel essai automatique."""
    return (f"Téléchargement du modèle {nom} impossible : {cause} "
            f"Nouvel essai dans {duree_lisible(delai_s)}.")


def points_activite(tick: int) -> str:
    """Suite cyclique « », « . », « .. », « ... » pour montrer que ca travaille."""
    return _POINTS_ACTIVITE[tick % len(_POINTS_ACTIVITE)]


def dossier_racine_modeles(surcharge: str | None = None,
                           localappdata: str | None = None) -> str:
    """Dossier qui contient un sous-dossier par modele.

    `surcharge` : valeur de la cle `dossier_modeles` de settings.json ; None = la lire,
    chaine vide = emplacement par defaut. `localappdata` : repli sur
    `~\\AppData\\Local` si la variable d'environnement n'existe pas.
    """
    if surcharge is None:
        import user_config
        surcharge = user_config.get(CLE_DOSSIER_MODELES, "")
    if str(surcharge).strip():
        return str(surcharge).strip()
    base = localappdata or os.environ.get("LOCALAPPDATA") \
        or os.path.join(os.path.expanduser("~"), "AppData", "Local")
    return os.path.join(base, "MeetNote", "modeles")


def dossier_modele(nom: str, surcharge: str | None = None,
                   localappdata: str | None = None) -> str:
    """Dossier dans lequel un modele est (ou sera) installe par MeetNote."""
    return os.path.join(dossier_racine_modeles(surcharge, localappdata), nom)


def _present_non_vide(chemin: str) -> bool:
    """Vrai si le fichier existe (liens suivis) et n'est pas vide."""
    try:
        return os.path.isfile(chemin) and os.path.getsize(chemin) > 0
    except OSError:
        return False


def modele_complet(dossier: str) -> bool:
    """Vrai si tous les fichiers attendus d'un modele sont presents et non vides."""
    if not all(_present_non_vide(os.path.join(dossier, f)) for f in FICHIERS_REQUIS):
        return False
    return any(_present_non_vide(os.path.join(dossier, f))
               for f in FICHIERS_VOCABULAIRE)


def dossier_cache_hf() -> str:
    """Cache Hugging Face du poste (HF_HUB_CACHE, puis HF_HOME, puis ~/.cache)."""
    explicite = os.environ.get("HF_HUB_CACHE")
    if explicite:
        return explicite
    racine = os.environ.get("HF_HOME") or os.path.join(
        os.path.expanduser("~"), ".cache", "huggingface")
    return os.path.join(racine, "hub")


def _snapshots_cache_hf(nom: str, cache: str) -> list[str]:
    """Snapshots du modele dans le cache HF, le plus recent d'abord."""
    repertoire = "models--" + depot(nom).replace("/", "--")
    motif = os.path.join(cache, repertoire, "snapshots", "*")
    return sorted(glob.glob(motif), key=os.path.getmtime, reverse=True)


def _candidats(nom, surcharge, localappdata, dossier_exe, dossier_bundle, cache_hf):
    """Emplacements a essayer, dans l'ordre de preference."""
    yield dossier_modele(nom, surcharge, localappdata)
    if dossier_exe:
        yield os.path.join(dossier_exe, "models", nom)
    if dossier_bundle:
        yield os.path.join(dossier_bundle, "faster_whisper_models", nom)
    yield from _snapshots_cache_hf(nom, cache_hf)


def chemin_modele(nom: str, *, surcharge: str | None = None,
                  localappdata: str | None = None,
                  dossier_exe: str | None = None,
                  dossier_bundle: str | None = None,
                  cache_hf: str | None = None) -> str | None:
    """Premier dossier complet du modele, ou None si aucun n'est utilisable.

    Ordre : dossier MeetNote, ancien `models\\` a cote de l'exe, ancien bundle
    (`faster_whisper_models`), cache Hugging Face existant. Hors executable fige, les
    deux emplacements historiques sont ignores sauf s'ils sont passes explicitement.
    """
    if getattr(sys, "frozen", False):
        if dossier_exe is None:
            dossier_exe = os.path.dirname(sys.executable)
        if dossier_bundle is None:
            dossier_bundle = getattr(sys, "_MEIPASS", None)
    if cache_hf is None:
        cache_hf = dossier_cache_hf()
    candidats = _candidats(nom, surcharge, localappdata, dossier_exe,
                           dossier_bundle, cache_hf)
    for candidat in candidats:
        if modele_complet(candidat):
            return candidat
    return None


# --- Telechargement -------------------------------------------------------------------


class _Annulation:
    """Demande d'annulation (None, Event ou callable) ; l'etat, une fois leve, est collant."""

    def __init__(self, annuler):
        if annuler is None:
            self._sonde = lambda: False
        elif hasattr(annuler, "is_set"):
            self._sonde = annuler.is_set
        else:
            self._sonde = annuler
        self.demandee = False

    def verifier(self):
        """Leve TelechargementAnnule des que l'annulation est demandee (et ensuite)."""
        if self.demandee or self._sonde():
            self.demandee = True
            raise TelechargementAnnule("Téléchargement annulé.")


def _fabriquer_classe_barre(annulation):
    """Classe a passer en `tqdm_class` : elle ne compte rien, elle ne sert qu'a annuler.

    `snapshot_download` appelle `update` depuis ses threads de telechargement a chaque
    bloc recu : lever l'exception d'annulation ici interrompt le fichier en cours. La
    progression, elle, est mesuree sur le disque (`_SuiviDisque`). `get_lock`/`set_lock`
    sont exiges par `tqdm.contrib.concurrent.thread_map`.
    """

    class _Barre:
        _lock = None

        def __init__(self, iterable=None, *args, **kwargs):
            self._iterable = iterable
            self.total = kwargs.get("total")  # hf fait `barre.total += n`

        @classmethod
        def get_lock(cls):
            if cls._lock is None:
                cls._lock = threading.RLock()
            return cls._lock

        @classmethod
        def set_lock(cls, verrou):
            cls._lock = verrou

        def update(self, n=1):
            annulation.verifier()

        def __iter__(self):
            return iter(self._iterable if self._iterable is not None else ())

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def refresh(self, *args, **kwargs):
            """Sans objet : aucun affichage."""

        def set_description(self, *args, **kwargs):
            """Sans objet : aucun affichage."""

        def close(self):
            """Sans objet : rien a fermer."""

    return _Barre


def _chaine_causes(exc):
    """L'exception puis ses causes/contextes, sans boucle."""
    vues = []
    while exc is not None and all(exc is not v for v in vues):
        vues.append(exc)
        exc = exc.__cause__ or exc.__context__
    return vues


def _disque_plein(exc):
    return any(
        isinstance(e, OSError) and (
            e.errno == errno.ENOSPC
            or getattr(e, "winerror", None) == WINERROR_DISQUE_PLEIN)
        for e in _chaine_causes(exc))


def _noms_classes(exc):
    return {c.__name__ for e in _chaine_causes(exc) for c in type(e).__mro__}


def traduire_erreur(exc, nom):
    """Erreur de huggingface_hub / du disque -> `ErreurTelechargement` en francais."""
    noms = _noms_classes(exc)
    if noms & _NOMS_DEPOT_INTROUVABLE:
        message = (f"Le modèle « {nom} » est introuvable sur Hugging Face "
                   f"({depot(nom)}) ou son accès est refusé.")
    elif _disque_plein(exc):
        message = (f"Espace disque insuffisant pour télécharger le modèle « {nom} ». "
                   "Libérez de la place ou choisissez un autre dossier de modèles.")
    elif noms & _NOMS_RESEAU:
        message = ("Connexion à Hugging Face impossible ou coupée. Vérifiez le "
                   "réseau : le téléchargement reprendra là où il s'est arrêté.")
    elif _NOM_ERREUR_HTTP_HF in noms:
        message = ("Hugging Face a répondu par une erreur. Réessayez plus tard : "
                   "le téléchargement reprendra là où il s'est arrêté.")
    else:
        message = (f"Le téléchargement du modèle « {nom} » a échoué "
                   f"({type(exc).__name__}).")
    return ErreurTelechargement(message)


def _taille_fichier(chemin):
    """Taille d'un fichier (liens suivis), 0 s'il est absent ou illisible."""
    try:
        return os.path.getsize(chemin) if os.path.isfile(chemin) else 0
    except OSError:
        return 0


def _prefixe_incomplet(fichier):
    """Debut du nom du `.incomplete` de huggingface_hub 1.9.0 pour `fichier`.

    Le nom est `<sha1 base64 de "<fichier>.metadata">.<etag>.incomplete` (voir
    `_local_folder._short_hash` et `LocalDownloadFilePaths.incomplete_path`).
    """
    empreinte = hashlib.sha1(f"{fichier}.metadata".encode(), usedforsecurity=False).digest()
    return base64.urlsafe_b64encode(empreinte).decode() + "."


def mesurer_octets_presents(dossier):
    """Octets du modele deja sur le disque : fichiers finis + `.incomplete` en cours.

    Pour chaque fichier attendu, la taille du fichier fini s'il existe, sinon celle du
    plus gros `.incomplete` correspondant dans `<dossier>/.cache/huggingface/download/`.
    Independant des compteurs internes de huggingface_hub.
    """
    reprise = os.path.join(dossier, *SOUS_DOSSIER_REPRISE)
    try:
        partiels = os.listdir(reprise)
    except OSError:
        partiels = []
    total = 0
    for fichier in MOTIFS_TELECHARGEMENT:
        prefixe = _prefixe_incomplet(fichier)
        partiel = max((_taille_fichier(os.path.join(reprise, nom)) for nom in partiels
                       if nom.startswith(prefixe) and nom.endswith(SUFFIXE_INCOMPLET)),
                      default=0)
        # Le fichier fini est lu APRES le partiel : s'il est renomme entre les deux, on
        # voit l'un ou l'autre, jamais aucun des deux (la mesure ne chute pas).
        total += _taille_fichier(os.path.join(dossier, fichier)) or partiel
    return total


class _SuiviDisque:
    """Progression mesuree sur le disque, rendue monotone, appelee par un thread demon.

    `tick` mesure et appelle `rappel` si la valeur a change ; le thread de `demarrer` le
    refait toutes les `intervalle_s` secondes jusqu'a `arreter`. Les rappels sont
    serialises par un verrou (thread de suivi, puis appelant pour le dernier). Les octets
    annonces ne reculent jamais (un `.incomplete` renomme ou reparti de zero ne se voit
    pas) et, tant que `terminer` n'a pas ete appele, restent sous
    `PLAFOND_AVANT_FIN_POURCENT` % du total. `total` 0 = inconnu : la mesure est alors
    annoncee telle quelle, sans plafond.
    """

    def __init__(self, rappel, dossier, mesure, intervalle_s, total=0):
        self.total = total
        self._rappel = rappel
        self._dossier = dossier
        self._mesure = mesure
        self._intervalle_s = intervalle_s
        self._verrou = threading.Lock()
        self._arret = threading.Event()
        self._thread = None
        self._emis = None
        self.erreur = None  # exception d'un rappel levee dans le thread de suivi

    def tick(self):
        """Mesure le disque et notifie si la progression a change."""
        with self._verrou:
            octets = self._mesure(self._dossier)
            if self.total > 0:
                octets = min(octets, self.total * PLAFOND_AVANT_FIN_POURCENT // 100)
            self._emettre(max(octets, self._emis[0] if self._emis else 0), self.total)

    def _emettre(self, recus, total):
        if self._rappel is not None and (recus, total) != self._emis:
            self._emis = (recus, total)
            self._rappel(recus, total)

    def demarrer(self):
        """Premier rappel tout de suite (etat de depart, reprise comprise), puis le thread."""
        self.tick()
        self._thread = threading.Thread(target=self._boucle, name="suivi-modele",
                                        daemon=True)
        self._thread.start()

    def _boucle(self):
        while not self._arret.wait(self._intervalle_s):
            try:
                self.tick()
            except Exception as exc:  # noqa: BLE001 - restituee a l'appelant par terminer
                self.erreur = exc
                return

    def arreter(self):
        """Arrete et attend le thread de suivi (sans effet s'il n'a pas demarre)."""
        self._arret.set()
        if self._thread is not None:
            self._thread.join()

    def terminer(self):
        """Dernier rappel, a 100 %, une fois le modele verifie complet."""
        self.arreter()
        if self.erreur is not None:
            raise self.erreur
        with self._verrou:
            fin = max(1, self._mesure(self._dossier), self.total,
                      *(self._emis or (0, 0)))
            self._emettre(fin, fin)


def _importer_hf():
    try:
        import huggingface_hub
    except ImportError as exc:
        raise ErreurTelechargement(
            "Le composant de téléchargement (huggingface_hub) est introuvable."
        ) from exc
    return huggingface_hub


def _constantes_hf(hf):
    """Module `constants` de huggingface_hub, importe explicitement.

    Sur un `huggingface_hub` fraichement importe, ce sous-module n'est charge que
    paresseusement : un simple `getattr` peut rendre None et laisser Xet actif. Repli
    sur l'attribut `constants` pour un faux hub (sans `__name__` ni sous-module).
    """
    nom = getattr(hf, "__name__", None)
    if nom:
        try:
            return importlib.import_module(nom + ".constants")
        except ImportError:
            pass
    return getattr(hf, "constants", None)


@contextlib.contextmanager
def _sans_xet(hf):
    """Force le telechargement HTTP pendant le bloc, en restaurant la valeur d'origine.

    Les depots Systran sont servis par Xet. Avec `hf_xet` present, huggingface_hub 1.9.0
    passe par `xet_get`, qui ignore la reprise (pas d'en-tete Range : un fichier coupe
    repart de zero) et avale dans le code natif les exceptions du rappel de progression
    (l'annulation n'arrete plus le fichier en cours, jusqu'a 1,5 Go). Le chemin HTTP
    n'a aucun de ces defauts. `constants.HF_HUB_DISABLE_XET` est relu a chaque
    telechargement : le positionner suffit. Non reentrant, d'ou le verrou de
    `telecharger` (un seul telechargement a la fois).
    """
    constantes = _constantes_hf(hf)
    if constantes is None:
        yield
        return
    ancienne = getattr(constantes, "HF_HUB_DISABLE_XET", False)
    constantes.HF_HUB_DISABLE_XET = True
    try:
        yield
    finally:
        constantes.HF_HUB_DISABLE_XET = ancienne


def _total_amont(hf, nom):
    """Taille totale des fichiers du modele selon le depot, ou 0 si elle est inconnue.

    Lue avant le telechargement (`HfApi().model_info(files_metadata=True)`) pour que la
    progression ait son vrai total des le premier rappel. Rend 0 si la lecture echoue ou
    si une taille manque : la progression est alors indeterminee.
    """
    try:
        infos = hf.HfApi().model_info(depot(nom), files_metadata=True)
        total = sum(int(f.size) for f in infos.siblings
                    if f.rfilename in MOTIFS_TELECHARGEMENT)
    except Exception:  # noqa: BLE001 - repli voulu : reseau, API ou format inattendus
        return 0
    return max(0, total)


@contextlib.contextmanager
def _un_seul_telechargement(annulation):
    """Verrou de module ; l'attente reste interruptible par l'annulation."""
    while not _VERROU_TELECHARGEMENT.acquire(timeout=ATTENTE_VERROU_S):
        annulation.verifier()
    try:
        yield
    finally:
        _VERROU_TELECHARGEMENT.release()


def _executer_telechargement(hf, nom, dossier, annulation, suivi):
    """Appelle `snapshot_download` avec suivi disque, Xet coupe et annulation."""
    try:
        with _sans_xet(hf):
            hf.snapshot_download(
                repo_id=depot(nom), local_dir=dossier,
                allow_patterns=list(MOTIFS_TELECHARGEMENT),
                tqdm_class=_fabriquer_classe_barre(annulation))
    except TelechargementAnnule:
        raise
    except Exception as exc:
        # Une annulation avalée par un composant natif reste une annulation.
        if annulation.demandee:
            raise TelechargementAnnule("Téléchargement annulé.") from exc
        raise traduire_erreur(exc, nom) from exc
    finally:
        suivi.arreter()


def _preparer_et_telecharger(hf, nom, dossier, annulation, suivi):
    """Corps de `telecharger`, sous verrou : dossier, total, suivi, telechargement."""
    try:
        os.makedirs(dossier, exist_ok=True)
    except OSError as exc:
        raise traduire_erreur(exc, nom) from exc
    suivi.total = _total_amont(hf, nom)
    annulation.verifier()
    suivi.demarrer()
    _executer_telechargement(hf, nom, dossier, annulation, suivi)
    if annulation.demandee:
        raise TelechargementAnnule("Téléchargement annulé.")
    if not modele_complet(dossier):
        # Cas typique : réseau coupé, huggingface_hub rend alors le dossier partiel.
        raise ErreurTelechargement(
            f"Le modèle « {nom} » est incomplet : le téléchargement a été interrompu "
            "(réseau coupé ?). Relancez-le : il reprendra là où il s'est arrêté.")
    suivi.terminer()


def telecharger(nom, rappel=None, annuler=None, *, surcharge=None, localappdata=None,
                hf=None, mesure=None, intervalle_s=INTERVALLE_SUIVI_S):
    """Telecharge le modele `nom` dans son dossier MeetNote et rend ce dossier.

    `rappel(octets_recus, octets_totaux)` : appele au fil de l'eau depuis un thread de
    suivi (serialise ; l'appelant UI doit repasser au thread Tk), d'abord a l'etat de
    depart (reprise comprise), puis toutes les `INTERVALLE_SUIVI_S` secondes. La
    progression est MESUREE SUR LE DISQUE (fichiers finis + `.incomplete`), pas lue dans
    les compteurs de huggingface_hub. Contrat : `octets_recus` ne recule jamais ;
    `octets_totaux` > 0 est la taille du modele selon le depot, et `octets_recus` reste
    a 99 % au plus avant la fin ; `octets_totaux` == 0 signifie **total inconnu**
    (depot injoignable) : seuls les octets recus sont alors significatifs. Le dernier
    appel, toujours, est (taille, taille) une fois `modele_complet` verifie.
    `annuler` : callable ou `threading.Event`, consulte a chaque bloc recu.

    Reprise : `snapshot_download(local_dir=...)` garde les fichiers en cours dans
    `<dossier>/.cache/huggingface/download/*.incomplete` et relance avec un en-tete
    Range ; un nouvel appel reprend donc la ou le precedent s'est arrete. Cela suppose
    le chemin HTTP : Xet est desactive pendant l'appel (voir `_sans_xet`), sinon le
    fichier repartirait de zero et l'annulation serait avalee par le code natif.

    Un seul telechargement a la fois (verrou de module) : les autres appels attendent,
    et peuvent etre annules pendant l'attente.

    Leve `TelechargementAnnule` (fichiers partiels conserves) ou `ErreurTelechargement`
    (jamais de modele annonce pret tant que `modele_complet` est faux ; un modele
    complet deja present n'est jamais touche). `hf` : module exposant
    `snapshot_download` (injectable pour les tests ; par defaut `huggingface_hub`) ;
    `mesure(dossier)` et `intervalle_s` : mesure disque et periode, injectables.
    """
    if nom not in MODELES:
        raise ValueError(f"Modèle inconnu : {nom!r} (attendus : {', '.join(MODELES)}).")
    annulation = _Annulation(annuler)
    annulation.verifier()
    dossier = dossier_modele(nom, surcharge, localappdata)
    suivi = _SuiviDisque(rappel, dossier, mesure or mesurer_octets_presents, intervalle_s)
    if modele_complet(dossier):
        suivi.terminer()
        return dossier
    if hf is None:
        hf = _importer_hf()
    with _un_seul_telechargement(annulation):
        if modele_complet(dossier):  # fini entre-temps par un autre appel
            suivi.terminer()
        else:
            _preparer_et_telecharger(hf, nom, dossier, annulation, suivi)
    return dossier
