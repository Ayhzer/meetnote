"""build_paths.py — Résolution des chemins Python utilisés par les scripts de build.

Module volontairement sans effet de bord à l'import : build_spec.py exécute le
build au niveau module, il ne peut donc pas être importé par un test.
"""

import os
import sys

# Variable d'environnement permettant de désigner une autre installation Python.
ENV_PYTHON_HOME = "MEETNOTE_PYTHON_HOME"


def resolve_python_home() -> str:
    """Retourne la racine de l'installation Python qui porte les dépendances.

    Priorité à la variable d'environnement MEETNOTE_PYTHON_HOME (si définie et
    non vide), sinon `sys.base_prefix` : le build se lance avec
    `py -3.12 build_spec.py`, qui désigne l'installation Python 3.12 en cours.
    """
    override = os.environ.get(ENV_PYTHON_HOME, "").strip()
    return override or sys.base_prefix


def resolve_site_packages(python_home: str | None = None) -> str:
    """Retourne le dossier site-packages de la racine Python donnée (ou résolue)."""
    home = python_home if python_home is not None else resolve_python_home()
    return os.path.join(home, "Lib", "site-packages")
