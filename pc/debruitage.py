"""
debruitage.py — Débruitage du micro (noisereduce, porte spectrale stationnaire).

Module pur, sans interface. `noisereduce` n'a besoin ni de `torch` ni de `numba` : il
n'importe `torch` que dans un `try/except ImportError` pour une variante GPU que
l'application n'utilise jamais. Le paquet livré exclut donc `torch` (366 Mo)
et `llvmlite`.

Si `noisereduce` est indisponible ou échoue, le signal est rendu intact et un message en
clair est écrit une seule fois dans le journal : l'enregistrement n'est jamais perdu.
"""
import logging
import threading

import numpy as np

_logger = logging.getLogger("meetnote")

# Part du bruit retirée : équilibre entre qualité et artefacts (réglage historique).
PROP_DECREASE = 0.75

_verrou = threading.Lock()
_profil_bruit: np.ndarray | None = None  # premier bloc reçu, pris comme profil de bruit
_module = None                           # module noisereduce, une fois importé
_indisponible = False                    # True après un échec : on ne réessaie plus
_message_emis = False


def reinitialiser() -> None:
    """Remet le module à zéro (nouvelle calibration, nouvel essai d'import)."""
    global _profil_bruit, _module, _indisponible, _message_emis
    with _verrou:
        _profil_bruit = None
        _module = None
        _indisponible = False
        _message_emis = False


def _signaler_indisponible(cause: Exception) -> None:
    """Désactive le débruitage et le dit une seule fois, en clair. Verrou déjà pris."""
    global _indisponible, _message_emis
    _indisponible = True
    if not _message_emis:
        _message_emis = True
        _logger.warning(
            "Débruitage désactivé : %s (%s). "
            "L'enregistrement continue sans débruitage.",
            cause, type(cause).__name__,
        )


def _charger():
    """Importe noisereduce au premier usage. Rend None s'il est indisponible."""
    global _module
    if _indisponible:
        return None
    if _module is None:
        try:
            import noisereduce
            _module = noisereduce
        except ImportError as exc:
            _signaler_indisponible(exc)
    return _module


def debruiter(samples: np.ndarray, sample_rate: int) -> np.ndarray:
    """Rend `samples` débruité (float32), ou `samples` inchangé si le débruitage est
    indisponible. Le premier bloc reçu sert de profil de bruit pour toute la session."""
    global _profil_bruit
    with _verrou:
        nr = _charger()
        if nr is None:
            return samples
        if _profil_bruit is None:
            _profil_bruit = samples.copy()
        try:
            reduit = nr.reduce_noise(
                y=samples,
                sr=sample_rate,
                y_noise=_profil_bruit,
                prop_decrease=PROP_DECREASE,
                stationary=True,
            )
        except Exception as exc:  # noqa: BLE001 (le signal brut vaut mieux qu'une coupure)
            _signaler_indisponible(exc)
            return samples
    return reduit.astype(np.float32)
