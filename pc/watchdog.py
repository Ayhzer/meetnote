"""Supervise meetnote-tray.py et le relance automatiquement s'il se termine
de façon inattendue (ex: access violation native WASAPI, que Python ne peut
pas rattraper depuis l'intérieur du process qui crashe).

Ne dépend que de la stdlib : lancé tôt (avant l'installation des dépendances
par start.bat) et doit rester robuste même si l'environnement Python de
l'appli n'est pas encore prêt.

Sortie propre (Quitter dans le tray, ou stop.bat) : meetnote-tray.py / stop.bat
déposent %APPDATA%/MeetNote/clean_exit.flag avant que le process ne se
termine → le watchdog voit le flag et s'arrête sans relancer.
Sortie sans le flag == crash → relance après un court délai.

Mode figé : MeetNote.exe lancé sans `--enfant` se supervise lui-même
(`superviser_exe`) et relance `MeetNote.exe --enfant`, sur le même protocole.
Si une instance tourne déjà (menu Démarrer pendant une réunion), le second
lancement ne la tue jamais : il dépose `afficher.flag` et se termine ; l'instance
en cours le consomme et montre sa fenêtre. Seul l'installeur force l'arrêt
(`clean_exit.flag` puis `taskkill`).
"""
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "meetnote-tray.py")

APPDATA_DIR = os.path.join(os.path.expanduser("~"), "AppData", "Roaming", "MeetNote")
STOP_FLAG = os.path.join(APPDATA_DIR, "clean_exit.flag")
WATCHDOG_PID_FILE = os.path.join(APPDATA_DIR, "watchdog.pid")
# Demande d'affichage : déposée par un second lancement, consommée par l'instance en cours.
AFFICHER_FLAG = os.path.join(APPDATA_DIR, "afficher.flag")

RESTART_DELAY_S = 3


def _pythonw() -> str:
    exe = sys.executable
    pyw = exe.replace("python.exe", "pythonw.exe")
    return pyw if os.path.isfile(pyw) else exe


def _consume_stop_flag(stop_flag: str = STOP_FLAG) -> bool:
    if os.path.isfile(stop_flag):
        try:
            os.remove(stop_flag)
        except OSError:
            pass
        return True
    return False


def superviser(commande, *, stop_flag: str = STOP_FLAG,
               pid_file: str = WATCHDOG_PID_FILE,
               lanceur=subprocess.Popen, attendre=time.sleep,
               delai: float = RESTART_DELAY_S) -> None:
    """Lance `commande` (liste argv) et la relance tant qu'elle ne se termine
    pas en déposant `stop_flag`.

    Chemins, lanceur et attente sont injectables pour les tests. Aucune
    écriture disque avant l'appel : le module reste importable sans effet de bord.
    """
    os.makedirs(os.path.dirname(pid_file), exist_ok=True)
    with open(pid_file, "w") as f:
        f.write(str(os.getpid()))

    try:
        while True:
            _consume_stop_flag(stop_flag)  # ignorer un flag périmé d'un run précédent
            proc = lanceur(list(commande))
            proc.wait()
            if _consume_stop_flag(stop_flag):
                break  # sortie volontaire : ne pas relancer
            attendre(delai)
    finally:
        try:
            os.remove(pid_file)
        except OSError:
            pass


ARG_ENFANT = "--enfant"
NOM_PROCESSUS_MEETNOTE = "meetnote"
NOM_PROCESSUS_PYTHON = "python"
SCRIPT_TRAY = "meetnote-tray.py"


def doit_superviser(fige: bool, args) -> bool:
    """Décision « superviser ou travailler » (fonction pure).

    Seul l'EXE figé lancé sans `--enfant` devient superviseur ; l'enfant, comme
    le mode source (où start.bat / la clé Run passent déjà par watchdog.py),
    travaille directement.
    """
    return bool(fige) and ARG_ENFANT not in args


def commande_enfant(executable: str, args) -> list:
    """Commande de l'enfant : même EXE, `--enfant` en tête, autres args conservés."""
    return [executable, ARG_ENFANT, *args]


def est_meetnote(nom: str, ligne_de_commande=()) -> bool:
    """Vrai si le process est MeetNote (fonction pure, insensible à la casse).

    - mode figé : nom `meetnote*` ;
    - mode source : nom `python*` ET un élément de la ligne de commande est
      `meetnote-tray.py`. Un autre python (boucle du kit, superviseur) ou un
      pid recyclé n'est jamais reconnu.
    """
    nom = (nom or "").lower()
    if nom.startswith(NOM_PROCESSUS_MEETNOTE):
        return True
    if not nom.startswith(NOM_PROCESSUS_PYTHON):
        return False
    for element in ligne_de_commande or ():
        fichier = str(element).lower().replace("\\", "/").rsplit("/", 1)[-1]
        if fichier == SCRIPT_TRAY:
            return True
    return False


def est_processus_meetnote(nom: str) -> bool:
    """Vrai si le nom est celui de MeetNote.exe, sans ligne de commande : jamais python.

    Sert au remplacement d'instance via `watchdog.pid`, où le mode source
    (`pythonw watchdog.py`) ne doit pas être reconnu.
    """
    return est_meetnote(nom)


def _nom_processus_psutil(pid: int) -> str:
    """Nom du process `pid`, ou "" s'il n'existe pas, est inaccessible ou si psutil manque.

    psutil est importé ici seulement : watchdog.py reste importable en stdlib seule.
    """
    try:
        import psutil
    except ImportError:
        return ""
    try:
        return psutil.Process(pid).name()
    except psutil.Error:
        return ""


def _lire_pid(chemin: str):
    try:
        with open(chemin) as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return None


def demander_affichage(afficher_flag: str = AFFICHER_FLAG) -> bool:
    """Dépose la demande d'affichage. Retourne False si le dépôt est impossible."""
    try:
        os.makedirs(os.path.dirname(afficher_flag), exist_ok=True)
        open(afficher_flag, "w").close()
    except OSError:
        return False
    return True


def consommer_demande_affichage(afficher_flag: str = AFFICHER_FLAG) -> bool:
    """Retire la demande d'affichage ; True une seule fois par demande déposée."""
    try:
        os.remove(afficher_flag)
    except OSError:
        return False
    return True


def afficher_instance_existante(*, pid_file: str = WATCHDOG_PID_FILE,
                                afficher_flag: str = AFFICHER_FLAG, pid_courant=None,
                                nom_processus=_nom_processus_psutil) -> bool:
    """Si un superviseur MeetNote vivant existe, lui demande d'afficher sa fenêtre.

    Ne tue rien : couper l'instance en cours interromprait un enregistrement.
    Retourne True si une instance vit (la demande est déposée) ; False si le
    fichier PID est absent, périmé, illisible, ou désigne un autre programme
    (pid recyclé, watchdog.py du mode source) — l'appelant doit alors superviser.
    """
    pid = _lire_pid(pid_file)
    if pid is None or pid == (pid_courant or os.getpid()):
        return False
    if not est_processus_meetnote(nom_processus(pid)):
        return False
    demander_affichage(afficher_flag)
    return True


def superviser_exe(args, *, executable: str | None = None,
                   existante=afficher_instance_existante, superviseur=superviser,
                   afficher_flag: str = AFFICHER_FLAG) -> None:
    """Point d'entrée de l'EXE figé lancé sans `--enfant`.

    Une instance déjà en cours reçoit une demande d'affichage et ce lancement
    se termine ; sinon il supervise `MeetNote.exe --enfant <args>`.
    """
    if existante():
        return
    # Aucune instance : une demande d'affichage restée là est périmée, elle ne
    # doit pas faire surgir la fenêtre d'un démarrage voulu silencieux.
    consommer_demande_affichage(afficher_flag)
    superviseur(commande_enfant(executable or sys.executable, args))


def main():
    superviser([_pythonw(), SCRIPT, *sys.argv[1:]])


if __name__ == "__main__":
    main()
