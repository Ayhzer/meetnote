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
ARRET_INSTANCE_TIMEOUT_S = 5


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


def est_processus_meetnote(nom: str) -> bool:
    """Vrai si le nom de process est celui de MeetNote.exe (jamais python, ni autre)."""
    return (nom or "").lower().startswith(NOM_PROCESSUS_MEETNOTE)


class _ProcessusPsutil:
    """Adaptateur minimal sur psutil.Process : les erreurs psutil n'en sortent pas."""

    def __init__(self, psutil_mod, proc):
        self._psutil = psutil_mod
        self._proc = proc

    def nom(self) -> str:
        try:
            return self._proc.name()
        except self._psutil.Error:
            return ""

    def descendants(self) -> list:
        try:
            enfants = self._proc.children(recursive=True)
        except self._psutil.Error:
            return []
        return [_ProcessusPsutil(self._psutil, e) for e in enfants]

    def terminer(self) -> None:
        try:
            self._proc.terminate()
        except self._psutil.Error:
            pass  # déjà terminé ou accès refusé : rien de plus à faire

    def attendre_fin(self, timeout: float) -> None:
        """Attend la fin du process ; au-delà du délai, le tue."""
        try:
            self._proc.wait(timeout=timeout)
        except self._psutil.TimeoutExpired:
            try:
                self._proc.kill()
            except self._psutil.Error:
                pass
        except self._psutil.Error:
            pass


def _ouvrir_processus_psutil(pid: int):
    """Process de `pid` (adaptateur), ou None s'il n'existe pas ou si psutil manque.

    psutil est importé ici seulement : watchdog.py reste importable en stdlib seule.
    """
    try:
        import psutil
    except ImportError:
        return None
    try:
        return _ProcessusPsutil(psutil, psutil.Process(pid))
    except psutil.Error:
        return None


def _lire_pid(chemin: str):
    try:
        with open(chemin) as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return None


def remplacer_instance(*, pid_file: str = WATCHDOG_PID_FILE,
                       stop_flag: str = STOP_FLAG, pid_courant=None,
                       ouvrir=_ouvrir_processus_psutil,
                       timeout: float = ARRET_INSTANCE_TIMEOUT_S) -> bool:
    """Arrête le superviseur MeetNote déjà en place, avec son enfant.

    Appelé par un second lancement de l'EXE. Le drapeau de sortie propre est
    déposé d'abord : si le superviseur survit un instant à son enfant, il ne le
    relance pas (pas de ping-pong). Rien n'est touché si `watchdog.pid` désigne
    un process qui n'est pas MeetNote (pid recyclé, watchdog.py du mode source).
    Le drapeau déposé est consommé par le nouveau `superviser` avant son 1er
    lancement. Retourne True si une instance a été arrêtée.
    """
    pid = _lire_pid(pid_file)
    if pid is None or pid == (pid_courant or os.getpid()):
        return False
    superviseur = ouvrir(pid)
    if superviseur is None or not est_processus_meetnote(superviseur.nom()):
        return False

    try:
        os.makedirs(os.path.dirname(stop_flag), exist_ok=True)
        open(stop_flag, "w").close()
    except OSError:
        return False  # sans drapeau, tuer l'enfant provoquerait une relance
    descendants = superviseur.descendants()  # à relever avant la mort du père
    superviseur.terminer()  # le superviseur d'abord : plus de relance possible
    for descendant in descendants:
        descendant.terminer()
    for victime in (superviseur, *descendants):
        victime.attendre_fin(timeout)
    return True


def superviser_exe(args, *, executable: str | None = None, remplacer=remplacer_instance,
                   superviseur=superviser) -> None:
    """Point d'entrée de l'EXE figé lancé sans `--enfant` : remplace l'instance
    en cours puis supervise `MeetNote.exe --enfant <args>`."""
    remplacer()
    superviseur(commande_enfant(executable or sys.executable, args))


def main():
    superviser([_pythonw(), SCRIPT, *sys.argv[1:]])


if __name__ == "__main__":
    main()
