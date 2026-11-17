"""Ordre de démarrage du worker et robustesse de la file de jobs.

Régression 3.6.0 : le worker démarrait avant mainloop(). Un job repris au
démarrage appelait _refresh_ui → _root.after et le thread mourait sur
« main thread is not in main loop » : plus rien n'était transcrit.
Le module tray (tiret, tkinter, pystray) n'est pas importable à bas coût :
on vérifie sa structure par l'AST.
"""
import ast
import pathlib
import unittest

CHEMIN_TRAY = pathlib.Path(__file__).parent / "meetnote-tray.py"
ARBRE = ast.parse(CHEMIN_TRAY.read_text(encoding="utf-8"))


def _fonction(nom: str) -> ast.FunctionDef:
    for noeud in ARBRE.body:
        if isinstance(noeud, ast.FunctionDef) and noeud.name == nom:
            return noeud
    raise AssertionError(f"fonction {nom} introuvable")


def _appels(noeud: ast.AST) -> list[ast.Call]:
    return [n for n in ast.walk(noeud) if isinstance(n, ast.Call)]


def _cible_thread(appel: ast.Call) -> str | None:
    """Nom passé en target= d'un threading.Thread(...), sinon None."""
    if ast.unparse(appel.func) != "threading.Thread":
        return None
    for mot in appel.keywords:
        if mot.arg == "target":
            return ast.unparse(mot.value)
    return None


class DemarrageWorkerTest(unittest.TestCase):
    def test_main_ne_lance_pas_le_worker_directement(self):
        cibles = [_cible_thread(a) for a in _appels(_fonction("main"))]
        self.assertNotIn("_worker_loop", cibles)

    def test_main_lance_le_worker_depuis_la_boucle_tk_apres_build_window(self):
        corps = ast.unparse(_fonction("main"))
        position_fenetre = corps.index("_build_window()")
        position_worker = corps.index("root.after(0, _demarrer_worker)")
        position_boucle = corps.index("root.mainloop()")
        self.assertLess(position_fenetre, position_worker)
        self.assertLess(position_worker, position_boucle)

    def test_seul_demarrer_worker_cree_le_thread_du_worker(self):
        lanceurs = [f.name for f in ARBRE.body if isinstance(f, ast.FunctionDef)
                    and "_worker_loop" in [_cible_thread(a) for a in _appels(f)]]
        self.assertEqual(lanceurs, ["_demarrer_worker"])


class RobustesseWorkerTest(unittest.TestCase):
    def test_une_exception_de_job_est_capturee_et_journalisee(self):
        essais = [n for n in ast.walk(_fonction("_worker_loop"))
                  if isinstance(n, ast.Try)
                  and "_process_job" in ast.unparse(n.body[0])]
        self.assertEqual(len(essais), 1)
        textes = [(ast.unparse(g.type), ast.unparse(g)) for g in essais[0].handlers]
        self.assertTrue(any(typ == "Exception" for typ, _ in textes))
        self.assertTrue(any("_logger.exception" in corps for _, corps in textes))
        # Le finally libère toujours le worker et relance la conservation.
        self.assertIn("_worker_busy = False", ast.unparse(essais[0].finalbody))


if __name__ == "__main__":
    unittest.main()
