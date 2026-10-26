"""Démarrage de MeetNote avec Windows (clé HKCU\\...\\Run).

Les règles (quelle commande, que faire de la clé au lancement) sont des
fonctions pures ; les accès au registre reçoivent le module `winreg` en
paramètre pour être testés sans toucher au registre réel.
"""
import ntpath
import os

CLE_REGISTRE = r"Software\Microsoft\Windows\CurrentVersion\Run"
NOM_VALEUR = "MeetNote"

# Décisions possibles au lancement de l'application.
RIEN = "rien"
ECRIRE = "ecrire"
RECALER = "recaler"

_SCRIPT_WATCHDOG = "watchdog.py"


def commande_demarrage(fige: bool, executable: str, dossier_pc: str,
                       existe=os.path.isfile) -> str:
    """Commande de démarrage attendue.

    Figé : l'EXE seul, qui se supervise lui-même (voir watchdog.doit_superviser).
    Source : pythonw sur watchdog.py, pour survivre à un plantage natif WASAPI.
    """
    if fige:
        return f'"{executable}" --minimized'
    # pythonw évite la console ; repli sur l'interpréteur s'il est absent
    pythonw = executable.replace("python.exe", "pythonw.exe")
    if not existe(pythonw):
        pythonw = executable
    watchdog = os.path.join(dossier_pc, _SCRIPT_WATCHDOG)
    return f'"{pythonw}" "{watchdog}" --minimized'


def nom_executable(commande: str) -> str:
    """Nom de fichier (minuscules) de l'exécutable en tête d'une commande."""
    commande = (commande or "").strip()
    if commande.startswith('"'):
        fin = commande.find('"', 1)
        tete = commande[1:] if fin < 0 else commande[1:fin]
    else:
        tete = commande.split(" ", 1)[0]
    return ntpath.basename(tete).lower()


def pointe_sur_watchdog(valeur: str) -> bool:
    """Vrai si la clé lance watchdog.py : c'est celle du poste de développement."""
    return _SCRIPT_WATCHDOG in (valeur or "").lower()


def decision_au_demarrage(fige: bool, valeur: str | None, attendue: str) -> str:
    """Que faire de la clé quand l'application démarre : RIEN, ECRIRE ou RECALER.

    Mode source : la clé est réenregistrée dès qu'elle diffère.
    Mode figé : le choix appartient à l'installeur et aux paramètres, donc
    jamais de création ; la clé du développement (watchdog.py) n'est jamais
    écrasée ; seule une clé sur un autre MeetNote.exe est recalée.
    """
    if not fige:
        return RIEN if valeur == attendue else ECRIRE
    if not valeur or valeur == attendue or pointe_sur_watchdog(valeur):
        return RIEN
    if nom_executable(valeur) == nom_executable(attendue):
        return RECALER
    return RIEN


def lire_valeur(winreg) -> str | None:
    """Commande enregistrée dans la clé Run, ou None si absente ou illisible."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, CLE_REGISTRE) as cle:
            valeur, _ = winreg.QueryValueEx(cle, NOM_VALEUR)
            return valeur
    except OSError:
        return None


def est_active(winreg) -> bool:
    """La case des paramètres reflète la clé : cochée dès qu'une valeur existe."""
    return bool(lire_valeur(winreg))


def definir_active(winreg, active: bool, commande: str) -> None:
    """Écrit la clé Run avec `commande`, ou la retire."""
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, CLE_REGISTRE,
                        0, winreg.KEY_SET_VALUE) as cle:
        if active:
            winreg.SetValueEx(cle, NOM_VALEUR, 0, winreg.REG_SZ, commande)
            return
        try:
            winreg.DeleteValue(cle, NOM_VALEUR)
        except FileNotFoundError:
            pass  # déjà absente : rien à retirer
