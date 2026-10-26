"""
assistant_audio.py — Mesure le micro et le son du PC pendant 5 secondes, pour l'assistant
de démarrage.

Deux parties, volontairement séparées :

  - des fonctions PURES (niveau, verdict, génération du son d'essai), testées sans matériel ;
  - des fonctions de CAPTURE (micro, son du PC), qui seules touchent aux périphériques.

Le micro et le son du PC sont mesurés l'un après l'autre, jamais ensemble : le son d'essai
joué pour le loopback rentrerait sinon par le micro et masquerait un micro muet.

Aucune capture ne peut bloquer : chacune tourne dans un thread démon borné par un délai
maximal. Le loopback WASAPI se bloque tant que rien ne joue ; l'assistant joue donc lui-même
un son court pendant la mesure. Les blocs déjà reçus sont conservés en cas de dépassement.

Chemin du loopback imité de `meetnote-tray.py` : pyaudiowpatch d'abord, soundcard en repli.
"""
import logging
import threading
import time
from typing import NamedTuple

import numpy as np

# Ordre d'import volontaire : sounddevice avant soundcard (initialisation PortAudio/COM).
# isort: off
import sounddevice as sd
import soundcard as sc
# isort: on

log = logging.getLogger(__name__)

# ─── Réglages de la mesure ───────────────────────────────────────────────────
DUREE_MESURE_S = 5.0
MARGE_DELAI_S = 5.0                 # délai maximal = durée de mesure + cette marge
BLOC_S = 0.1                        # taille d'un bloc de mesure (comme le callback du tray)

# Même formule que `_push_level` (meetnote-tray.py) : RMS x 100, x 3, plafonné à 100.
FACTEUR_RMS = 100.0
GAIN_NIVEAU = 3.0
NIVEAU_MAX = 100.0
# En deçà, le niveau est considéré nul (silence numérique ou périphérique muet).
SEUIL_NIVEAU_NUL = 0.05

# Son d'essai joué pendant la mesure du son du PC.
FREQUENCE_ESSAI_HZ = 440.0
DUREE_ESSAI_S = 1.0
AMPLITUDE_ESSAI = 0.2
TAUX_ESSAI_DEFAUT = 48000
FONDU_ESSAI_S = 0.02                # évite le « clic » de début et de fin

# ─── Messages affichés tels quels par l'assistant ────────────────────────────
MSG_MICRO_ABSENT = (
    "Le micro est introuvable ou inaccessible. Vérifiez qu'il est branché et autorisé "
    "dans les paramètres de confidentialité de Windows."
)
MSG_MICRO_NUL = (
    "Aucun son n'a été capté par le micro. Parlez pendant la mesure, ou vérifiez qu'il "
    "n'est pas coupé."
)
MSG_PC_ABSENT = (
    "Le son du PC n'a pas pu être écouté. L'enregistrement des réunions en ligne "
    "(Teams, Zoom…) n'entendra que votre micro."
)
MSG_PC_NUL = (
    "Aucun son du PC n'a été détecté. Vérifiez que le volume n'est pas coupé : sinon, les "
    "autres participants ne seront pas enregistrés."
)


class ErreurCapture(RuntimeError):
    """Un périphérique n'a pas pu être ouvert ou lu."""


class VerdictAudio(NamedTuple):
    """Résultat de la mesure : un refus éventuel (micro) et des avertissements (son du PC)."""

    refus: str | None
    avertissements: tuple[str, ...]

    @property
    def accepte(self) -> bool:
        """Vrai si l'assistant peut se terminer : seul le micro nul bloque."""
        return self.refus is None


# ─── Fonctions pures ─────────────────────────────────────────────────────────
def calculer_niveau(mono: np.ndarray) -> float:
    """Niveau 0-100 d'un bloc mono, formule de `_push_level`. Un bloc vide vaut 0."""
    if mono.size == 0:
        return 0.0
    rms = float(np.sqrt(np.mean(np.square(mono, dtype=np.float64)))) * FACTEUR_RMS
    return min(rms * GAIN_NIVEAU, NIVEAU_MAX)


def niveau_pic(mono: np.ndarray, taux: int, bloc_s: float = BLOC_S) -> float:
    """Plus haut niveau sur des blocs de `bloc_s` secondes.

    Le pic, et non la moyenne : une phrase courte dans cinq secondes de calme ne doit pas
    être jugée nulle.
    """
    taille = max(int(taux * bloc_s), 1)
    niveaux = [calculer_niveau(mono[i:i + taille]) for i in range(0, mono.size, taille)]
    return max(niveaux, default=0.0)


def est_nul(niveau: float | None) -> bool:
    """Vrai si la capture a échoué (None) ou si le niveau est sous le seuil."""
    return niveau is None or niveau < SEUIL_NIVEAU_NUL


def rendre_verdict(niveau_micro: float | None, niveau_pc: float | None) -> VerdictAudio:
    """Micro nul → refus ; son du PC nul → avertissement ; `None` = capture impossible."""
    refus = None
    if niveau_micro is None:
        refus = MSG_MICRO_ABSENT
    elif est_nul(niveau_micro):
        refus = MSG_MICRO_NUL

    avertissements = []
    if niveau_pc is None:
        avertissements.append(MSG_PC_ABSENT)
    elif est_nul(niveau_pc):
        avertissements.append(MSG_PC_NUL)
    return VerdictAudio(refus, tuple(avertissements))


def generer_ton(
    taux: int = TAUX_ESSAI_DEFAUT,
    duree_s: float = DUREE_ESSAI_S,
    frequence_hz: float = FREQUENCE_ESSAI_HZ,
    amplitude: float = AMPLITUDE_ESSAI,
) -> np.ndarray:
    """Son d'essai mono float32, avec un court fondu au début et à la fin."""
    n = max(int(taux * duree_s), 1)
    t = np.arange(n) / taux
    ton = amplitude * np.sin(2 * np.pi * frequence_hz * t)
    n_fondu = min(int(taux * FONDU_ESSAI_S), n // 2)
    if n_fondu > 0:
        rampe = np.linspace(0.0, 1.0, n_fondu)
        ton[:n_fondu] *= rampe
        ton[n - n_fondu:] *= rampe[::-1]
    return ton.astype(np.float32)


def vers_mono(donnees: np.ndarray) -> np.ndarray:
    """Ramène un tableau (frames,) ou (frames, canaux) à un signal mono float32."""
    donnees = np.asarray(donnees, dtype=np.float32)
    return donnees if donnees.ndim == 1 else donnees.mean(axis=1)


def assembler(blocs: list[np.ndarray]) -> np.ndarray:
    """Concatène les blocs mono reçus ; aucun bloc donne un signal vide."""
    return np.concatenate(blocs) if blocs else np.zeros(0, dtype=np.float32)


# ─── Délai maximal ───────────────────────────────────────────────────────────
def _executer_avec_delai(cible, delai_s: float) -> bool:
    """Lance `cible` dans un thread démon. Retourne True si elle a fini dans le délai.

    Une exception levée dans le thread est relevée ici en `ErreurCapture`. Un thread
    dépassé n'est pas tué : il est démon et ses blocs déjà reçus restent exploitables.
    """
    erreur: list[Exception] = []

    def _travail():
        try:
            cible()
        except Exception as exc:  # relevée côté appelant, jamais perdue
            log.warning("assistant_audio: capture en échec", exc_info=True)
            erreur.append(exc)

    fil = threading.Thread(target=_travail, daemon=True, name="assistant-audio")
    fil.start()
    fil.join(delai_s)
    if erreur:
        raise ErreurCapture(str(erreur[0])) from erreur[0]
    return not fil.is_alive()


# ─── Capture du micro (sounddevice) ──────────────────────────────────────────
def _ecouter_micro(duree_s: float, blocs: list[np.ndarray], taux_sortie: list[int]) -> None:
    """Lit le micro par défaut en blocs de `BLOC_S` pendant `duree_s` secondes."""
    info = sd.query_devices(sd.default.device[0])
    taux = int(info["default_samplerate"])
    taux_sortie.append(taux)
    taille = max(int(taux * BLOC_S), 1)
    fin = time.monotonic() + duree_s
    with sd.InputStream(samplerate=taux, channels=1, dtype="float32", blocksize=taille) as flux:
        while time.monotonic() < fin:
            donnees, _debordement = flux.read(taille)
            blocs.append(vers_mono(donnees))


def mesurer_micro(duree_s: float = DUREE_MESURE_S) -> float:
    """Niveau de crête du micro par défaut. Lève `ErreurCapture` si impossible."""
    blocs: list[np.ndarray] = []
    taux: list[int] = []
    try:
        _executer_avec_delai(
            lambda: _ecouter_micro(duree_s, blocs, taux), duree_s + MARGE_DELAI_S
        )
    except ErreurCapture:
        if not blocs:
            raise
        # Une erreur en cours de route ne fait pas perdre ce qui a été capté.
    if not blocs:
        raise ErreurCapture("Le micro n'a fourni aucun échantillon.")
    return niveau_pic(assembler(blocs), taux[0])


# ─── Capture du son du PC (loopback WASAPI) ──────────────────────────────────
def _jouer_ton(taux: int) -> None:
    """Joue le son d'essai sur la sortie par défaut ; un échec ne bloque pas la mesure."""
    try:
        sd.play(generer_ton(taux), taux)
    except (sd.PortAudioError, OSError, ValueError):
        log.warning("assistant_audio: son d'essai non joué", exc_info=True)


def _arreter_ton() -> None:
    try:
        sd.stop()
    except (sd.PortAudioError, OSError):
        log.debug("assistant_audio: arrêt du son d'essai impossible", exc_info=True)


def _ecouter_loopback_pyaudiowpatch(
    duree_s: float, blocs: list[np.ndarray], taux_sortie: list[int]
) -> None:
    """Loopback par défaut via pyaudiowpatch, en mode callback (jamais de lecture bloquante)."""
    import pyaudiowpatch as pawp

    pa = pawp.PyAudio()
    try:
        peripherique = pa.get_default_wasapi_loopback()
        taux = int(peripherique["defaultSampleRate"])
        canaux = int(peripherique["maxInputChannels"])

        def _recevoir(donnees, _nb_frames, _temps, _statut):
            brut = np.frombuffer(donnees, dtype=np.float32).reshape(-1, canaux)
            blocs.append(vers_mono(brut))
            return None, pawp.paContinue

        taux_sortie.append(taux)
        flux = pa.open(
            format=pawp.paFloat32, channels=canaux, rate=taux, input=True,
            input_device_index=peripherique["index"],
            frames_per_buffer=max(int(taux * BLOC_S), 1), stream_callback=_recevoir,
        )
        try:
            flux.start_stream()
            _jouer_ton(taux)
            time.sleep(duree_s)
            flux.stop_stream()
        finally:
            flux.close()
    finally:
        pa.terminate()


def _ecouter_loopback_soundcard(
    duree_s: float, blocs: list[np.ndarray], taux_sortie: list[int]
) -> None:
    """Repli : loopback de la sortie par défaut via soundcard (lecture bloquante par blocs)."""
    haut_parleur = sc.default_speaker()
    loopback = sc.get_microphone(haut_parleur.id, include_loopback=True)
    taux = TAUX_ESSAI_DEFAUT
    taille = max(int(taux * BLOC_S), 1)
    taux_sortie.append(taux)
    _jouer_ton(taux)
    fin = time.monotonic() + duree_s
    with loopback.recorder(samplerate=taux, channels=1, blocksize=taille) as enregistreur:
        while time.monotonic() < fin:
            blocs.append(vers_mono(enregistreur.record(numframes=taille)))


def _ecouter_son_pc(duree_s: float, blocs: list[np.ndarray], taux: list[int]) -> None:
    """pyaudiowpatch d'abord ; soundcard si l'ouverture échoue sans rien avoir capté."""
    try:
        _ecouter_loopback_pyaudiowpatch(duree_s, blocs, taux)
    except (ImportError, OSError, RuntimeError, ValueError, KeyError, AttributeError):
        log.warning("assistant_audio: pyaudiowpatch en échec, repli soundcard", exc_info=True)
        if blocs:
            return
        taux.clear()
        _ecouter_loopback_soundcard(duree_s, blocs, taux)


def mesurer_son_pc(duree_s: float = DUREE_MESURE_S) -> float:
    """Niveau de crête du son du PC pendant que l'assistant joue un son d'essai.

    Lève `ErreurCapture` si le loopback est impossible. Un loopback qui ne livre rien
    (rien n'a joué) donne un niveau 0, pas une erreur : c'est le cas « volume coupé ».
    """
    blocs: list[np.ndarray] = []
    taux: list[int] = []
    try:
        _executer_avec_delai(
            lambda: _ecouter_son_pc(duree_s, blocs, taux), duree_s + MARGE_DELAI_S
        )
    except ErreurCapture:
        if not taux:
            raise
        # Périphérique ouvert puis erreur : on juge sur ce qui a été reçu.
    finally:
        _arreter_ton()
    if not taux:
        raise ErreurCapture("Le loopback n'a pas pu être ouvert dans le délai.")
    return niveau_pic(assembler(blocs), taux[0])


# ─── Point d'entrée ──────────────────────────────────────────────────────────
def mesurer_audio(duree_s: float = DUREE_MESURE_S) -> VerdictAudio:
    """Mesure le micro puis le son du PC, et rend le verdict. Ne lève jamais."""
    try:
        niveau_micro = mesurer_micro(duree_s)
    except ErreurCapture:
        log.warning("assistant_audio: micro illisible", exc_info=True)
        niveau_micro = None
    try:
        niveau_pc = mesurer_son_pc(duree_s)
    except ErreurCapture:
        log.warning("assistant_audio: son du PC illisible", exc_info=True)
        niveau_pc = None
    return rendre_verdict(niveau_micro, niveau_pc)
