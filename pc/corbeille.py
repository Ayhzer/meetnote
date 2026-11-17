"""
Envoi d'un fichier à la Corbeille Windows (suppression récupérable).

Passe par `SHFileOperation` de pywin32 (`win32com.shell`), déjà présent : aucune
dépendance ajoutée. Aucun dialogue n'est jamais affiché.
"""
import os

# Motifs de refus, repris tels quels dans le journal.
MOTIF_INTROUVABLE = "fichier introuvable"
MOTIF_ANNULEE = "opération annulée"
MOTIF_SHELL_ABSENT = "win32com.shell indisponible"


def _charger_shell():
    """Modules `shell` et `shellcon` de pywin32 (import tardif : Windows seulement)."""
    from win32com.shell import shell, shellcon
    return shell, shellcon


def envoyer_a_la_corbeille(chemin: str) -> str | None:
    """Envoie `chemin` à la Corbeille. Rend None si c'est fait, sinon le motif d'échec.

    Drapeaux : `FOF_ALLOWUNDO` (Corbeille, donc annulable par l'utilisateur),
    `FOF_NOCONFIRMATION`, `FOF_SILENT` et `FOF_NOERRORUI` (aucun dialogue, aucune
    barre de progression). `FOF_WANTNUKEWARNING` est volontairement absent : un fichier
    trop gros pour la Corbeille est alors supprimé définitivement par Windows, sans
    question, ce qui est conforme à l'intention de conservation bornée.
    """
    absolu = os.path.abspath(chemin)  # la Corbeille exige un chemin complet à `\`
    if not os.path.exists(absolu):
        return MOTIF_INTROUVABLE
    try:
        shell, shellcon = _charger_shell()
    except ImportError:
        return MOTIF_SHELL_ABSENT
    drapeaux = (shellcon.FOF_ALLOWUNDO | shellcon.FOF_NOCONFIRMATION
                | shellcon.FOF_SILENT | shellcon.FOF_NOERRORUI)
    try:
        code, annulee = shell.SHFileOperation(
            (0, shellcon.FO_DELETE, absolu, None, drapeaux, None, None))
    except Exception as exc:  # noqa: BLE001 - pywintypes.error et COM : rien à propager
        return f"erreur Windows ({type(exc).__name__}: {exc})"
    if annulee:
        return MOTIF_ANNULEE
    if code != 0:
        return f"code retour Windows {code:#x}"
    return None
