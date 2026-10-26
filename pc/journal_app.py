"""
Journal de l'application MeetNote : configuration du logging et de faulthandler.

- crash.log : journal texte (exceptions, [Agenda]…), rotation quotidienne à minuit,
  JOURS_CONSERVATION fichiers datés conservés (crash.log.AAAA-MM-JJ), les plus
  anciens supprimés par la rotation elle-même. Les lignes du jour restent dans
  crash.log.
- crash-natif.log : sortie de faulthandler (crashs natifs). Fichier DISTINCT :
  sous Windows, un fichier tenu ouvert par faulthandler ne pourrait pas être renommé
  par la rotation (WinError 32). Il est borné au démarrage : au-delà de
  TAILLE_MAX_NATIF octets, la session précédente est mise de côté (.old, écrasé
  à chaque fois) et un fichier neuf est ouvert.
"""
import dataclasses
import faulthandler
import logging
import logging.handlers
import os
import time

NOM_JOURNAL = "crash.log"
NOM_JOURNAL_NATIF = "crash-natif.log"
JOURS_CONSERVATION = 30
TAILLE_MAX_NATIF = 1024 * 1024  # 1 Mio
DELAI_NOUVEL_ESSAI_ROTATION = 3600  # secondes, si la rotation échoue (fichier verrouillé)
FORMAT_LIGNE = "%(asctime)s [%(levelname)s] %(message)s"
FORMAT_DATE = "%Y-%m-%d %H:%M:%S"

_logger_rotation = logging.getLogger("meetnote.journal")


class RotationTolerante(logging.handlers.TimedRotatingFileHandler):
    """Rotation à minuit qui ne perd jamais la ligne en cours si elle échoue.

    Dans la version standard, une erreur du renommage (fichier ouvert dans un
    éditeur, antivirus…) laisse le flux fermé et la ligne qui a déclenché la
    rotation est perdue. Ici le flux est rouvert en ajout dans le fichier courant.

    Deux échecs se distinguent :
    - le renommage échoue : crash.log n'a pas tourné, nouvel essai après
      DELAI_NOUVEL_ESSAI_ROTATION ;
    - le renommage réussit mais la purge d'un vieux fichier échoue : la rotation
      est faite, l'échéance passe au minuit suivant (sinon la stdlib retrouverait
      le fichier daté existant et ne ferait plus jamais tourner crash.log). La purge
      est retentée à la rotation suivante : elle supprime tout l'excédent.

    Après tout doRollover, l'échéance est dans le futur : aucun chemin ne fige la rotation.
    """

    erreurs_rotation = 0

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._renommage_fait = False

    def rotate(self, source, dest):
        super().rotate(source, dest)
        self._renommage_fait = True

    def doRollover(self):
        self._renommage_fait = False
        try:
            super().doRollover()
        except OSError as erreur:
            RotationTolerante.erreurs_rotation += 1
            if self.stream is None:
                self.stream = self._open()
            maintenant = int(time.time())
            if self._renommage_fait:
                self.rolloverAt = self.computeRollover(maintenant)
                _logger_rotation.warning("Purge du journal impossible, retentée à la prochaine rotation : %s", erreur)
            else:
                self.rolloverAt = maintenant + DELAI_NOUVEL_ESSAI_ROTATION
                _logger_rotation.warning("Rotation du journal impossible, nouvel essai plus tard : %s", erreur)
        self._garantir_echeance_future()

    def _garantir_echeance_future(self):
        """La stdlib sort sans avancer l'échéance si le fichier daté existe déjà."""
        maintenant = int(time.time())
        if self.rolloverAt <= maintenant:
            self.rolloverAt = self.computeRollover(maintenant)


@dataclasses.dataclass
class Journal:
    """Ce que configurer_journal a mis en place, pour pouvoir le défaire (tests)."""

    chemin: str
    chemin_natif: str
    handler: RotationTolerante
    fichier_natif: object
    niveau_racine_initial: int

    def fermer(self):
        """Détache le handler, coupe faulthandler et ferme les fichiers."""
        faulthandler.disable()
        racine = logging.getLogger()
        racine.removeHandler(self.handler)
        racine.setLevel(self.niveau_racine_initial)
        self.handler.close()
        self.fichier_natif.close()


def _borner_journal_natif(chemin):
    """Met de côté (.old) le journal natif s'il dépasse TAILLE_MAX_NATIF."""
    try:
        if os.path.getsize(chemin) > TAILLE_MAX_NATIF:
            os.replace(chemin, chemin + ".old")
    except FileNotFoundError:
        pass
    except OSError as erreur:
        _logger_rotation.warning("Journal natif non borné : %s", erreur)


def configurer_journal(dossier):
    """Configure logging (rotation) et faulthandler dans `dossier`.

    Niveaux inchangés : racine en WARNING (exchangelib, urllib3… se taisent),
    logger « meetnote » en INFO.
    """
    os.makedirs(dossier, exist_ok=True)
    chemin = os.path.join(dossier, NOM_JOURNAL)
    chemin_natif = os.path.join(dossier, NOM_JOURNAL_NATIF)

    handler = RotationTolerante(
        chemin, when="midnight", backupCount=JOURS_CONSERVATION, encoding="utf-8")
    handler.setFormatter(logging.Formatter(FORMAT_LIGNE, FORMAT_DATE))
    racine = logging.getLogger()
    niveau_initial = racine.level
    racine.addHandler(handler)
    racine.setLevel(logging.WARNING)
    logging.getLogger("meetnote").setLevel(logging.INFO)

    _borner_journal_natif(chemin_natif)
    # Fichier gardé ouvert toute la vie du processus (faulthandler en a besoin).
    fichier_natif = open(chemin_natif, "a", encoding="utf-8")  # noqa: SIM115
    faulthandler.enable(file=fichier_natif)
    return Journal(chemin, chemin_natif, handler, fichier_natif, niveau_initial)
