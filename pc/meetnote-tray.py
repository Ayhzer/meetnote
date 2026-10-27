"""
MeetNote Tray — icône systray Windows + fenêtre tkinter
- Panneau principal : enregistrement (activité critique)
- Panel latéral coulissant : historique des enregistrements avec statuts par étape
- Étapes séparées et relançables : audio → transcription → notion
- Intégration Outlook via win32com (lecture du calendrier)
"""
import sys

# Aiguillage superviseur / travailleur — AVANT tout import lourd (tkinter, numpy,
# sounddevice…). Le superviseur est un process léger : il ne charge que la stdlib,
# ouvre ni fenêtre ni icône, et sort (sys.exit) quand MeetNote est quitté pour de bon.
import watchdog as _watchdog

if _watchdog.doit_superviser(getattr(sys, "frozen", False), sys.argv[1:]):
    _watchdog.superviser_exe(sys.argv[1:])
    sys.exit(0)

import atexit
import collections
import ctypes
import dataclasses
import datetime
import os
import queue
import shutil
import struct
import subprocess
import threading
import tkinter as tk
import wave
from tkinter import ttk

# Ordre d'import volontaire : sounddevice avant soundcard (initialisation PortAudio/COM), ne pas trier.
# isort: off
import pystray
from PIL import Image, ImageDraw
import sounddevice as sd
import soundcard as sc
import numpy as np
# isort: on

sys.path.insert(0, os.path.dirname(__file__))

# ─── Logging de crash persistant ──────────────────────────────────────────────
# L'app ne loguait rien dans un fichier : un plantage (exception non catchée,
# crash natif audio/whisper) ne laissait aucune trace. On capture désormais :
#   - les exceptions Python du thread principal (sys.excepthook)
#   - les exceptions des threads (threading.excepthook, Python 3.8+)
#   - les crashs natifs (faulthandler : segfault, access violation…)
# Tout est écrit dans %APPDATA%/MeetNote/crash.log (crashs natifs : crash-natif.log)
import logging
import traceback

import assistant_fenetre
import calendar_digest
import config
import conversion_audio
import debruitage
import demarrage_windows
import history as hist_mod
import journal_app
import modeles
import perf_profile
import recuperation_enregistrements as recup_mod
import source_agenda
import teams_roster
import user_config
from notion_push import push_to_notion
from version import VERSION

_LOG_DIR = os.path.join(os.path.expanduser("~"), "AppData", "Roaming", "MeetNote")

try:
    # Root en WARNING pour ne pas polluer avec les logs INFO des libs
    # (exchangelib autodiscover, urllib3…) ; notre logger reste en INFO.
    # Rotation quotidienne + faulthandler dans un fichier distinct : voir journal_app.
    _journal = journal_app.configurer_journal(_LOG_DIR)
    _logger = logging.getLogger("meetnote")
    _logger.info("=" * 60)
    _logger.info("Démarrage MeetNote")
except Exception:
    _logger = logging.getLogger("meetnote")


def _excepthook(exc_type, exc_value, exc_tb):
    """Loggue toute exception non catchée du thread principal avant de sortir."""
    _logger.critical(
        "Exception non catchée :\n%s",
        "".join(traceback.format_exception(exc_type, exc_value, exc_tb)),
    )
    sys.__excepthook__(exc_type, exc_value, exc_tb)


def _thread_excepthook(args):
    """Loggue les exceptions non catchées des threads (worker, audio, watchers)."""
    _logger.critical(
        "Exception non catchée dans le thread %s :\n%s",
        getattr(args.thread, "name", "?"),
        "".join(traceback.format_exception(
            args.exc_type, args.exc_value, args.exc_traceback)),
    )


sys.excepthook = _excepthook
try:
    threading.excepthook = _thread_excepthook  # Python 3.8+
except Exception:
    pass

RELEASE_NOTES = """\
## [3.1] — 2026-05-04

### Correctifs
- Crash après "Stop sans transcription" : l'archivage audio démarre maintenant avant
  l'ouverture du dialog "coller le transcript Teams", évitant la perte de l'audio.
- Garde de None sur _source_combo et _status_var dans _update_window_state().
- Moniteur de périphérique audio : ne plus interrompre quand un dialog modal est ouvert.

### Agenda Outlook
- Liste d'exclusion : les réunions dont l'intitulé contient un motif configuré
  (Settings → Outlook → Exclusions) ne déclenchent plus l'auto-start.
  (un motif par ligne, insensible à la casse)
- Popup "Fin de réunion imminente" étendue aux enregistrements manuels :
  si un RDV Outlook est en cours au démarrage de l'enregistrement, la popup
  se déclenche à l'approche de la fin du RDV.
- Bouton 🔄 dans la section MEETING pour forcer immédiatement le polling Outlook
  (sans attendre le cycle de 60 s).

---

## [3.0] — 2026-03-21

### UI — Refonte "Digital Control Room" (Stitch design)
- Layout 2 colonnes avec sidebar de navigation gauche fixe
- Nouvelle palette de couleurs Material Design (surface hierarchy)
- Boutons d'action avec états visuels distincts (rouge/vert/violet/gris)
- Indicateur de statut (dot coloré) : gris/vert/jaune selon l'état
- System Console avec timestamps et tags colorés (erreurs en rouge)
- Fenêtre historique redesignée : cartes avec badges statut colorés

### Nouvelles fonctionnalités
- Étapes indépendantes : audio, transcription, Notion — chacune relançable depuis l'historique
- "Stop without transcribing" : archive l'audio, diffère la transcription
- Import audio externe : bouton dans l'historique, supporte wav/mp3/opus/ogg/m4a/flac/webm/mp4
- Retranscription avec modèle différent : combo modèle sur chaque carte de l'historique
- Suppression d'entrées depuis l'historique
- Timestamps [HH:MM:SS] dans les transcripts (par segment Whisper)
- Agenda Outlook : pré-remplissage automatique du nom de réunion (polling 60s)
- Slider REC GAIN (0.5x–4x) : gain numérique appliqué aux samples, toutes sources
- Slider WIN VOLUME : contrôle du volume master Windows via pycaw
- Instance unique : au démarrage, l'instance précédente est automatiquement terminée

### Qualité audio & transcription
- Resampling scipy resample_poly (remplace np.interp)
- Débruitage noisereduce (prop_decrease=0.75, stationary)
- Mix Mixte : 60% mic / 40% loopback (voix moins atténuée)
- Whisper : beam_size=5, no_speech_threshold=0.6, vad min_silence=500ms
- Détection RIFF magic bytes pour fichiers opus renommés en .wav

### Corrections
- Status "Processing N job(s)" ne se remettait pas à "Idle" après la fin des jobs
- Fichiers opus archivés avec extension .wav causaient une erreur à la retranscription
- Nom de réunion Outlook retournait "OFF" (bloc hors-bureau) au lieu du vrai RDV
- S_TOP non défini dans _open_settings causait une NameError

---

## [2.0] — 2026-03-17

### Traitement en arrière-plan (job queue)
- Enregistrement et transcription entièrement découplés
- Démarrer un nouvel enregistrement pendant qu'un job est en cours de traitement
- Worker daemon unique (traitement séquentiel pour éviter la surcharge CPU)

### Nouvelles fonctionnalités
- Prévention de veille étendue à toute la durée du traitement (transcription + upload Notion)
- Archive audio locale : Documents\\MeetNote\\audio\\ (Opus 24 kbps si ffmpeg, sinon WAV)
- Découpage audio en segments de 10 min pour les uploads Notion longs
- condition_on_previous_text=False pour éliminer les boucles de répétition Whisper

### Corrections
- La veille PC ne s'activait plus après l'arrêt de l'enregistrement
- Le fichier WAV temporaire était supprimé avant d'être archivé localement
"""

import notion_push as _np_mod

_auto_start_exclusions = []


def _recaler_reglages():
    """Recale les copies en mémoire sur settings.json : credentials Notion, HEADERS
    de notion_push et exclusions auto-start.

    Appelée au chargement du module, puis après chaque enregistrement des réglages
    (paramètres, assistant). user_config est chargé APRÈS l'import de notion_push :
    sans ce recalage, HEADERS garderait l'ancien token.
    """
    global _auto_start_exclusions
    reglages = user_config.load()
    if reglages.get("notion_token"):
        config.NOTION_TOKEN = reglages["notion_token"]
    if reglages.get("notion_database_id"):
        config.NOTION_DATABASE_ID = reglages["notion_database_id"]
    _np_mod.HEADERS["Authorization"] = f"Bearer {config.NOTION_TOKEN}"
    _auto_start_exclusions = user_config.liste_exclusions(
        reglages.get("auto_start_exclusions", "")
    )


# Surcharge les credentials avec les valeurs utilisateur persistées
_recaler_reglages()

# ─── Win32 sleep prevention ──────────────────────────────────────────────────
ES_CONTINUOUS        = 0x80000000
ES_SYSTEM_REQUIRED   = 0x00000001
ES_AWAYMODE_REQUIRED = 0x00000040

def _prevent_sleep():
    ctypes.windll.kernel32.SetThreadExecutionState(
        ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_AWAYMODE_REQUIRED
    )

def _allow_sleep():
    ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS)

# ─── Battery check ────────────────────────────────────────────────────────────
def _is_on_battery() -> bool:
    """Retourne True si le PC tourne sur batterie (non branché secteur)."""
    try:
        SYSTEM_POWER_STATUS = ctypes.c_buffer(12)
        if ctypes.windll.kernel32.GetSystemPowerStatus(SYSTEM_POWER_STATUS):
            ac_line = SYSTEM_POWER_STATUS[0]
            # ac_line : 0 = batterie, 1 = secteur, 255 = inconnu
            return ac_line == b'\x00'
    except Exception:
        pass
    return False

def _warn_if_on_battery() -> bool:
    """Affiche une alerte si sur batterie. Retourne True si l'utilisateur confirme quand même."""
    if not _is_on_battery():
        return True
    import tkinter.messagebox as mb
    return mb.askyesno(
        "⚠ Batterie",
        "Le PC n'est pas branché sur secteur.\n\n"
        "La transcription est très gourmande en CPU et va décharger la batterie rapidement.\n\n"
        "Continuer quand même ?",
        icon="warning",
        default="no",
    )

# ─── State ───────────────────────────────────────────────────────────────────
_rec_gain        = 4.0   # gain numérique appliqué aux samples capturés
_recording       = False
_teams_watcher   = teams_roster.TeamsRosterWatcher()
_audio_chunks    = []
_wav_stream      = None
_wav_stream_path = None
_flush_frame_count = 0
_lock            = threading.Lock()
_stream_mic      = None
_loop_thread     = None
_stop_loop       = threading.Event()
# Armé par le moniteur audio quand la sortie Windows change PENDANT un enregistrement.
# Le reader loopback cesse alors de lire l'endpoint WASAPI invalidé — sinon PortAudio
# déréférence la mémoire morte dans AUDIOSES.DLL → access violation (0xC0000005) qui
# tue tout le process (crash natif non rattrapable en Python).
_audio_device_changed = threading.Event()
_icon            = None
_root            = None
_level_var       = None
_progress_var    = None
_status_var      = None
_source_var      = None
_speaker_var     = None
_model_var       = None
_lang_var        = None
_type_var        = None
_output_var      = None
_meeting_name_var = None   # champ nom réunion (pré-rempli depuis Outlook)
_outlook_poll_fn  = None   # handle pour forcer le polling Outlook depuis le bouton
_cache_agenda     = source_agenda.CacheAgenda()  # réunions des 24 h, lues sans solliciter Outlook
_log_text        = None
_btn_start       = None
_btn_stop        = None
_btn_cancel      = None
_btn_stop_only   = None
_source_combo    = None
_speaker_frame   = None
_speaker_combo   = None
_rec_start_time  = None
_audio_device_monitor_started = False  # un seul thread de surveillance
_modal_dialog_open = False             # True pendant wait_window() d'un dialog modal

# Fenêtre historique (Toplevel indépendante)
_history_win:       tk.Toplevel | None = None
_conflict_win:      tk.Toplevel | None = None
_end_warn_win:      tk.Toplevel | None = None
_history_list_frame: tk.Frame | None   = None

# Notes manuelles en cours d'enregistrement (mini-fenêtre flottante)
_notes_win:         tk.Toplevel | None = None
_notes_text:        tk.Text | None     = None
_notes_timer_lbl:   tk.Label | None    = None
_notes_pinned:      bool               = True

# Fin de réunion imminente
_current_meeting_end:  datetime.datetime | None = None
_end_warned_key:       str | None = None  # ISO[:16] de la fin déjà signalée

# Invités de la réunion enregistrée (issus du cache d'agenda, jamais d'Outlook en direct)
_reunion_imposee:      dict | None = None  # réunion choisie par l'auto-start / « Arrêter + Relancer »
_invites_retenus:      tuple[str, list] | None = None  # (objet, invités) de l'enregistrement en cours
_preremplissage_en_attente = False  # réunion inconnue au démarrage : à compléter au prochain rechargement réussi

# Auto-start : sujets exclus (motifs en minuscules, chargés depuis user_config l.122)

# Indicateur de statut (dot coloré)
_status_dot:        tk.Label | None   = None
_nav_btns:          dict              = {}   # nom → tk.Button

_FLUSH_FRAMES = config.CHUNK_SECONDS * config.SAMPLE_RATE

# ─── Job queue ───────────────────────────────────────────────────────────────
@dataclasses.dataclass
class _Job:
    id:              str
    wav_path:        str            # chemin audio archivé (opus ou wav)
    start_time:      datetime.datetime
    duration_min:    float
    model_name:      str
    language:        str
    meeting_type:    str
    output_mode:     str
    meeting_name:        str = ""
    teams_participants:  list = dataclasses.field(default_factory=list)
    participants_invites: list = dataclasses.field(default_factory=list)  # invités de l'agenda (requis puis facultatifs)
    # Statuts par étape
    status_audio:      str = "done"    # done
    status_transcript: str = "queued"  # queued / running / done / error
    status_notion:     str = "pending" # pending / running / done / error / skipped
    # Résultats
    transcript:         str = ""
    whisper_transcript: str = ""   # Transcription Whisper brute (avant concat Teams)
    teams_transcript:   str = ""   # Transcript Teams collé manuellement
    notes:           str = ""   # Notes manuelles prises pendant l'enregistrement
    transcript_path: str = ""
    notion_url:      str = ""
    error_msg:       str = ""
    attachments:     list = dataclasses.field(default_factory=list)  # chemins fichiers joints (mails)

_job_queue            = collections.deque()
_queue_lock           = threading.Lock()
_work_event           = threading.Event()
_worker_busy          = False
_worker_cancel_event  = threading.Event()   # set() pour annuler la transcription en cours
_current_job: "_Job | None" = None          # job actuellement traité par le worker
# Jobs à transcrire dont le modèle n'est pas encore là : sortis de la file (le worker
# reste libre pour les autres), remis en tête dès que le modèle est complet.
# Protégé par _queue_lock.
_jobs_attente_modele  = []

# Tous les jobs chargés (en mémoire, pour l'historique)
_all_jobs: list[_Job] = []
_all_jobs_lock = threading.Lock()

# ─── Lecture audio historique ────────────────────────────────────────────────
_playback_job_id: str | None = None   # ID du job en cours de lecture
_playback_stop_event = threading.Event()


def _play_audio(job_id: str, wav_path: str):
    """Lance la lecture audio dans un thread de fond. Arrête toute lecture en cours."""
    global _playback_job_id
    _stop_audio()                         # arrête la lecture précédente si active
    _playback_stop_event.clear()
    _playback_job_id = job_id

    def _run():
        global _playback_job_id
        try:
            import soundfile as sf
            data, samplerate = sf.read(wav_path, dtype="float32")
            import sounddevice as sd_pb
            chunk = 1024
            idx = 0
            with sd_pb.OutputStream(samplerate=samplerate,
                                    channels=max(1, data.ndim)) as stream:
                while idx < len(data) and not _playback_stop_event.is_set():
                    block = data[idx: idx + chunk]
                    stream.write(block)
                    idx += chunk
        except Exception:
            pass
        finally:
            _playback_job_id = None
            if _history_win and _history_win.winfo_exists():
                _history_win.after(0, _refresh_history_panel)

    threading.Thread(target=_run, daemon=True).start()


def _stop_audio():
    """Arrête la lecture en cours."""
    global _playback_job_id
    _playback_stop_event.set()
    _playback_job_id = None


CHUNK_FRAMES = 1024

# ─── Whisper ─────────────────────────────────────────────────────────────────
_whisper_model      = None
_whisper_model_name = None


def _is_real_wav(path: str) -> bool:
    """Vérifie que le fichier commence bien par le magic RIFF (vrai WAV)."""
    try:
        with open(path, "rb") as f:
            return f.read(4) == b"RIFF"
    except Exception:
        return False


def _split_wav_for_transcription(wav_path: str, segment_min: int = 10) -> list:
    with wave.open(wav_path, "r") as wf:
        rate       = wf.getframerate()
        n_frames   = wf.getnframes()
        n_channels = wf.getnchannels()
        sampwidth  = wf.getsampwidth()
        duration_s = n_frames / rate

    if duration_s <= segment_min * 60:
        return [wav_path]

    chunk_frames = segment_min * 60 * rate
    base = os.path.splitext(wav_path)[0]
    paths = []
    with wave.open(wav_path, "r") as wf:
        part = 0
        while True:
            frames_data = wf.readframes(chunk_frames)
            if not frames_data:
                break
            seg_path = f"{base}_seg{part:03d}.wav"
            with wave.open(seg_path, "w") as out:
                out.setnchannels(n_channels)
                out.setsampwidth(sampwidth)
                out.setframerate(rate)
                out.writeframes(frames_data)
            paths.append(seg_path)
            part += 1
    return paths if paths else [wav_path]


# ─── Tray icon ───────────────────────────────────────────────────────────────
def _make_icon(recording: bool) -> Image.Image:
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    color = (220, 50, 50) if recording else (80, 80, 80)
    d.ellipse([8, 8, 56, 56], fill=color)
    if recording:
        d.ellipse([24, 24, 40, 40], fill=(255, 255, 255))
    return img


# ─── Resample ────────────────────────────────────────────────────────────────
def _resample(data: np.ndarray, src: int, dst: int) -> np.ndarray:
    if src == dst:
        return data
    try:
        from math import gcd

        from scipy.signal import resample_poly
        g = gcd(src, dst)
        return resample_poly(data, dst // g, src // g).astype(np.float32)
    except Exception:
        new_len = int(len(data) * dst / src)
        return np.interp(
            np.linspace(0, len(data) - 1, new_len),
            np.arange(len(data)),
            data,
        ).astype(np.float32)


# ─── Débruitage (module debruitage, sans torch) ───────────────────────────────
def _denoise(samples: np.ndarray) -> np.ndarray:
    """Débruite un bloc du micro ; rend le bloc intact si le débruitage est indisponible."""
    return debruitage.debruiter(samples, config.SAMPLE_RATE)


# ─── Level update ────────────────────────────────────────────────────────────
def _push_level(mono: np.ndarray):
    rms = float(np.sqrt(np.mean(mono ** 2))) * 100
    if _level_var and _root:
        _root.after(0, lambda v=min(rms * 3, 100): _level_var.set(v))


# ─── Audio buffer ────────────────────────────────────────────────────────────
_loopback_active = False  # True quand le thread loopback est actif

def _append_audio(samples: np.ndarray):
    global _flush_frame_count
    if _rec_gain != 1.0:
        samples = np.clip(samples * _rec_gain, -1.0, 1.0)
    if not _loopback_active:
        samples = _denoise(samples)
    _audio_chunks.append(samples.copy())
    _flush_frame_count += len(samples)
    if _flush_frame_count >= _FLUSH_FRAMES and _wav_stream is not None:
        _flush_audio_to_wav()


def _flush_audio_to_wav():
    global _audio_chunks, _flush_frame_count
    if not _audio_chunks or _wav_stream is None:
        return
    data = np.concatenate(_audio_chunks, axis=0)
    data = np.clip(data, -1.0, 1.0)
    _wav_stream.writeframes((data * 32767).astype(np.int16).tobytes())
    _audio_chunks = []
    _flush_frame_count = 0
    # Patch le header WAV avec les tailles réelles pour que le fichier soit
    # valide en cas de crash (normalement écrit seulement à wave.close()).
    try:
        f = _wav_stream._file
        pos = f.tell()
        data_size = pos - 44  # octets de données audio (header WAV = 44 octets)
        f.seek(4)
        f.write(struct.pack('<I', data_size + 36))  # RIFF chunk size
        f.seek(40)
        f.write(struct.pack('<I', data_size))        # data chunk size
        f.seek(pos)
        f.flush()
    except Exception:
        pass


def _emergency_flush():
    """Appelé par atexit : flush les chunks restants et ferme proprement le WAV."""
    try:
        _flush_audio_to_wav()
        if _wav_stream is not None:
            _wav_stream.close()
    except Exception:
        pass

atexit.register(_emergency_flush)


# ─── sounddevice callback ────────────────────────────────────────────────────
def _mic_callback(indata, frames, time, status):
    mono = indata[:, 0]
    src_rate = int(sd.query_devices(sd.default.device[0])["default_samplerate"])
    resampled = _resample(mono, src_rate, config.SAMPLE_RATE)
    gained = np.clip(resampled * _rec_gain, -1.0, 1.0) if _rec_gain != 1.0 else resampled
    with _lock:
        if _recording:
            _append_audio(resampled)  # gain déjà appliqué dans _append_audio
    _push_level(gained)


# ─── sounddevice loopback thread ─────────────────────────────────────────────
def _find_wasapi_output_device(spk_name: str | None) -> tuple[int, int] | None:
    """Cherche le device de sortie WASAPI correspondant au haut-parleur sélectionné.
    Retourne (device_index, samplerate) pour utilisation avec WasapiSettings(loopback=True).
    """
    try:
        hostapis = sd.query_hostapis()
        wasapi_idx = next((i for i, h in enumerate(hostapis) if 'WASAPI' in h['name']), None)
        if wasapi_idx is None:
            print("[audio] WASAPI host API non trouvé", file=sys.stderr)
            return None
        devs = sd.query_devices()
        needle = spk_name.lower()[:20] if spk_name else None
        # Cherche l'output WASAPI correspondant au haut-parleur sélectionné
        for i, d in enumerate(devs):
            if d.get('hostapi') != wasapi_idx:
                continue
            if d['max_output_channels'] == 0:
                continue
            if needle and needle not in d['name'].lower():
                continue
            rate = int(d['default_samplerate']) or 48000
            print(f"[audio] loopback WASAPI output #{i} '{d['name']}' {rate} Hz", file=sys.stderr)
            return i, rate
        # Fallback : default output WASAPI
        def_out = int(sd.default.device[1])
        if sd.query_devices(def_out).get('hostapi') == wasapi_idx:
            rate = int(sd.query_devices(def_out)['default_samplerate']) or 48000
            print(f"[audio] loopback WASAPI default output #{def_out} {rate} Hz", file=sys.stderr)
            return def_out, rate
        print("[audio] aucun device WASAPI output trouvé", file=sys.stderr)
        return None
    except Exception as e:
        print(f"[audio] erreur recherche WASAPI: {e}", file=sys.stderr)
        return None


def _loopback_thread_fn(mic_also: bool):
    """Capture loopback (+ micro en mode mixte), avec réouverture automatique.

    La capture est enveloppée dans une boucle : si le périphérique audio par
    défaut change PENDANT l'enregistrement (casque branché/débranché, écran
    HDMI, Teams qui bascule la sortie…), la session en cours se termine
    proprement, puis on rouvre des flux WASAPI NEUFS sur le nouveau
    périphérique. On reconstruit entièrement l'instance PyAudio à chaque
    réouverture car PortAudio fige la liste des périphériques (et le device
    par défaut) à sa création : sans ça on continuerait à lire un endpoint
    mort → access violation fatale dans AUDIOSES.DLL, et surtout on
    n'enregistrait plus que du silence jusqu'à la fin de la réunion.
    """
    global _loopback_active
    _loopback_active = True
    ctypes.windll.ole32.CoInitialize(None)
    try:
        import time as _time
        while not _stop_loop.is_set():
            reopen_needed = _loopback_session(mic_also)
            if not reopen_needed or _stop_loop.is_set():
                break
            _logger.info("Périphérique audio changé pendant l'enregistrement — "
                         "réouverture des flux de capture (micro + PC).")
            _time.sleep(0.4)   # laisser Windows stabiliser le nouveau périphérique par défaut
    finally:
        _loopback_active = False


def _loopback_session(mic_also: bool) -> bool:
    """Une session de capture WASAPI. Retourne True si elle s'est terminée à
    cause d'un changement de périphérique (⇒ il faut rouvrir), False si arrêt
    normal (_stop_loop) ou échec définitif (fallback soundcard déjà tenté)."""
    _audio_device_changed.clear()   # repartir propre pour cette session
    spk_name = _speaker_var.get() if _speaker_var else None

    try:
        import pyaudiowpatch as pawp

        pa = pawp.PyAudio()
        try:
            wasapi_info = pa.get_host_api_info_by_type(pawp.paWASAPI)

            all_loopbacks = list(pa.get_loopback_device_info_generator())
            _AUTO = "— Sortie par défaut Windows —"
            auto_mode = (not spk_name) or spk_name == _AUTO

            def _best_loopback(target_name: str):
                """Retourne le loopback dont le nom correspond le mieux à target_name.
                Priorité : containment exact > score par mots-clés."""
                target_low = target_name.lower()
                keywords = [w for w in target_low.split() if len(w) > 3]
                best, best_score = None, -1
                for lb in all_loopbacks:
                    lb_low = lb['name'].lower()
                    # Priorité max : le nom du loopback contient le nom du device de sortie
                    if target_low in lb_low:
                        score = 1000
                    else:
                        score = sum(1 for kw in keywords if kw in lb_low)
                    if score > best_score:
                        best_score, best = score, lb
                return best

            loopback_dev = None
            if auto_mode:
                # Utiliser le loopback du périphérique de sortie Windows par défaut
                default_out = pa.get_device_info_by_index(wasapi_info["defaultOutputDevice"])
                loopback_dev = _best_loopback(default_out['name'])
                print(f"[audio] auto loopback → '{default_out['name']}' → '{loopback_dev and loopback_dev['name']}'", file=sys.stderr)
            else:
                loopback_dev = _best_loopback(spk_name)
                print(f"[audio] spk loopback '{spk_name}' → '{loopback_dev and loopback_dev['name']}'", file=sys.stderr)

            if loopback_dev is None and all_loopbacks:
                loopback_dev = all_loopbacks[0]
                print(f"[audio] fallback premier loopback: '{loopback_dev['name']}'", file=sys.stderr)
            if loopback_dev is None:
                raise RuntimeError("Aucun device loopback WASAPI trouvé")

            loop_rate   = int(loopback_dev['defaultSampleRate'])
            n_ch        = int(loopback_dev['maxInputChannels'])
            # En mode mixte : blocs de 100 ms pour ne pas bloquer sur silence loopback
            # En mode loopback seul : 1 s pour minimiser artefacts de resampling
            loop_frames = loop_rate // 10 if mic_also else loop_rate
            print(f"[audio] pyaudiowpatch loopback: '{loopback_dev['name']}' {loop_rate}Hz {n_ch}ch",
                  file=sys.stderr)

            loop_stream = pa.open(
                format=pawp.paFloat32, channels=n_ch, rate=loop_rate,
                input=True, input_device_index=loopback_dev['index'],
                frames_per_buffer=loop_frames,
            )
            loop_stream.start_stream()

            # ── Mode mixte : deux threads readers indépendants ─────────────────
            # WASAPI loopback bloque quand rien ne joue → on ne peut pas l'utiliser
            # comme horloge principale. On met mic ET loopback dans des queues
            # et on mélange périodiquement (100 ms).
            mic_q:      queue.Queue = queue.Queue()
            lb_q:       queue.Queue = queue.Queue()
            stop_readers = threading.Event()
            mic_stream   = None
            mic_reader   = None
            mic_rate     = loop_rate

            # ── Thread loopback reader ─────────────────────────────────────────
            def _lb_reader():
                while not stop_readers.is_set():
                    # Le périphérique audio par défaut a changé : l'endpoint de CE
                    # flux est invalidé. On arrête de lire AVANT que PortAudio ne
                    # touche la mémoire morte (AUDIOSES.DLL → access violation
                    # fatale). La session sera rouverte sur le nouveau périphérique.
                    if _audio_device_changed.is_set():
                        print("[audio] périphérique changé — arrêt du reader loopback "
                              "(réouverture imminente)", file=sys.stderr)
                        break
                    try:
                        data = loop_stream.read(loop_frames, exception_on_overflow=False)
                        arr  = np.frombuffer(data, dtype=np.float32).copy()
                        mono = arr.reshape(-1, n_ch).mean(axis=1) if n_ch > 1 else arr
                        lb_q.put(mono)
                    except Exception:
                        break
            lb_reader = threading.Thread(target=_lb_reader, daemon=True)
            lb_reader.start()

            # ── Thread mic reader (mixte seulement) ────────────────────────────
            if mic_also:
                try:
                    mic_info  = pa.get_default_input_device_info()
                    mic_rate  = int(mic_info['defaultSampleRate'])
                    mic_block = max(mic_rate // 10, 512)   # ~100 ms
                    mic_stream = pa.open(
                        format=pawp.paFloat32, channels=1, rate=mic_rate,
                        input=True, input_device_index=int(mic_info['index']),
                        frames_per_buffer=mic_block,
                    )
                    mic_stream.start_stream()
                    print(f"[audio] mic stream (mixte): '{mic_info['name']}' {mic_rate}Hz", file=sys.stderr, flush=True)
                    def _mic_reader():
                        while not stop_readers.is_set():
                            # Périphérique par défaut changé (mic et sortie sont
                            # souvent le même device combiné : casque BT/USB). On
                            # arrête AVANT que PortAudio ne touche la mémoire morte
                            # (access violation fatale). La session sera rouverte sur
                            # le nouveau micro par défaut → capture reprise en <1 s.
                            if _audio_device_changed.is_set():
                                print("[audio] périphérique changé — arrêt du reader mic "
                                      "(réouverture imminente)", file=sys.stderr)
                                break
                            try:
                                raw = mic_stream.read(mic_block, exception_on_overflow=False)
                                arr = np.frombuffer(raw, dtype=np.float32).copy()
                                mic_q.put(arr)
                                if _recording:
                                    _push_level(arr)
                            except Exception:
                                break
                    mic_reader = threading.Thread(target=_mic_reader, daemon=True)
                    mic_reader.start()
                except Exception as e:
                    print(f"[audio] mic setup error: {e}", file=sys.stderr, flush=True)
                    mic_stream = None

            # ── Boucle de mixage (100 ms) ──────────────────────────────────────
            MIX_S   = 0.1          # période de mixage
            mix_len = int(config.SAMPLE_RATE * MIX_S)
            try:
                while not _stop_loop.is_set() and not _audio_device_changed.is_set():
                    import time as _time
                    _time.sleep(MIX_S)

                    # Drainer loopback
                    lb_parts = []
                    while True:
                        try:  lb_parts.append(lb_q.get_nowait())
                        except queue.Empty: break
                    lb_rs = _resample(np.concatenate(lb_parts), loop_rate, config.SAMPLE_RATE) \
                            if lb_parts else np.zeros(mix_len, dtype=np.float32)

                    if mic_also and mic_stream:
                        # Drainer mic
                        mic_parts = []
                        while True:
                            try:  mic_parts.append(mic_q.get_nowait())
                            except queue.Empty: break
                        mic_rs = _resample(np.concatenate(mic_parts), mic_rate, config.SAMPLE_RATE) \
                                 if mic_parts else np.zeros(mix_len, dtype=np.float32)

                        # Aligner les longueurs
                        tgt = max(len(mic_rs), len(lb_rs), mix_len)
                        if len(mic_rs) < tgt: mic_rs = np.pad(mic_rs, (0, tgt - len(mic_rs)))
                        if len(lb_rs)  < tgt: lb_rs  = np.pad(lb_rs,  (0, tgt - len(lb_rs)))
                        out = np.clip(mic_rs * 0.6 + lb_rs * 0.4, -1.0, 1.0)
                    else:
                        out = lb_rs
                        if len(out): _push_level(out)

                    if len(out) == 0:
                        continue
                    with _lock:
                        if _recording:
                            _append_audio(out)
            finally:
                stop_readers.set()
                try:  loop_stream.stop_stream()
                except Exception: pass
                if mic_stream:
                    try:  mic_stream.stop_stream()
                    except Exception: pass
                # Attendre la fin des threads reader avant de fermer les flux :
                # fermer un stream WASAPI pendant qu'un thread le lit encore
                # provoque une access violation Windows fatale (non rattrapable).
                lb_reader.join(timeout=2)
                if mic_reader:
                    mic_reader.join(timeout=2)
                if mic_stream:
                    try:  mic_stream.close()
                    except Exception: pass

            loop_stream.close()
        finally:
            pa.terminate()

    except Exception as e:
        print(f"[audio] erreur pyaudiowpatch: {e} — fallback soundcard", file=sys.stderr)
        _loopback_thread_fn_soundcard(mic_also)
        return False   # le fallback soundcard a géré la session : ne pas rouvrir

    # Session terminée normalement : rouvrir uniquement si un changement de
    # périphérique en est la cause (et qu'on ne s'arrête pas).
    return _audio_device_changed.is_set() and not _stop_loop.is_set()


class _NullCtx:
    """Context manager no-op."""
    def __enter__(self): return self
    def __exit__(self, *_): pass


def _loopback_thread_fn_soundcard(mic_also: bool):
    """Fallback : loopback via soundcard (MediaFoundation)."""
    spk_name = _speaker_var.get() if _speaker_var else None
    _AUTO = "— Sortie par défaut Windows —"
    if spk_name and spk_name != _AUTO:
        spk = next((s for s in sc.all_speakers() if s.name == spk_name), sc.default_speaker())
    else:
        spk = sc.default_speaker()
    loopback = sc.get_microphone(spk.id, include_loopback=True)
    mic_dev  = sc.default_microphone() if mic_also else None
    native_rate = getattr(config, "LOOPBACK_NATIVE_RATE", 0) or 48000
    loop_frames = max(native_rate // 4, 2048)

    with loopback.recorder(samplerate=native_rate, channels=1, blocksize=loop_frames) as loop_rec:
        if mic_also and mic_dev:
            with mic_dev.recorder(samplerate=native_rate, channels=1, blocksize=loop_frames) as mic_rec:
                while not _stop_loop.is_set():
                    loop_chunk = loop_rec.record(numframes=loop_frames)[:, 0]
                    mic_chunk  = mic_rec.record(numframes=loop_frames)[:, 0]
                    if len(mic_chunk) != len(loop_chunk):
                        mic_chunk = np.resize(mic_chunk, len(loop_chunk))
                    mixed     = np.clip(mic_chunk * 0.6 + loop_chunk * 0.4, -1.0, 1.0)
                    resampled = _resample(mixed, native_rate, config.SAMPLE_RATE)
                    with _lock:
                        if _recording:
                            _append_audio(resampled)
                    _push_level(resampled)
        else:
            while not _stop_loop.is_set():
                chunk     = loop_rec.record(numframes=loop_frames)[:, 0]
                resampled = _resample(chunk, native_rate, config.SAMPLE_RATE)
                with _lock:
                    if _recording:
                        _append_audio(resampled)
                _push_level(resampled)


# ─── Save audio to temp ───────────────────────────────────────────────────────
def _save_audio_to_tempfile() -> str | None:
    global _wav_stream, _wav_stream_path
    with _lock:
        _flush_audio_to_wav()
        path = _wav_stream_path
        if _wav_stream is not None:
            try:
                _wav_stream.close()
            except Exception:
                pass
            _wav_stream = None
            _wav_stream_path = None
    if not path or not os.path.isfile(path):
        return None
    return path


# ─── Archive audio locale ────────────────────────────────────────────────────
def _encoder_opus(ffmpeg: str, source: str, dest: str) -> bool:
    """Encode `source` en Opus 24 kb/s ; True si `dest` est complet.

    En cas d'échec ou de délai dépassé, l'Opus partiel est retiré : l'appelant
    se replie sur une copie WAV intègre.
    """
    delai = conversion_audio.delai_conversion_s(conversion_audio.duree_estimee_s(source))
    cmd = [ffmpeg, "-y", "-i", source,
           "-c:a", "libopus", "-b:a", "24k", "-ac", "1", "-ar", "16000", dest]
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=delai,
                                creationflags=subprocess.CREATE_NO_WINDOW)
    except subprocess.TimeoutExpired:
        _logger.warning("Archive Opus abandonnée après %.0f s, repli sur une copie "
                        "WAV : %s", delai, os.path.basename(source))
        conversion_audio.retirer_sortie_partielle(dest)
        return False
    if result.returncode != 0:
        _logger.warning("Archive Opus en échec (code %s), repli sur une copie WAV : %s",
                        result.returncode, os.path.basename(source))
        conversion_audio.retirer_sortie_partielle(dest)
        return False
    return True


def _archive_audio(wav_path: str, start_time: datetime.datetime,
                   meeting_name: str = "") -> str | None:
    import re

    from notion_push import _find_ffmpeg
    try:
        os.makedirs(config.AUDIO_ARCHIVE_DIR, exist_ok=True)
        ts = start_time.strftime("%Y%m%d_%H%M%S")
        if meeting_name:
            safe = re.sub(r'[\\/:*?"<>|\s]+', '_', meeting_name).strip("_")[:60]
            basename = f"meetnote_{ts}_{safe}"
        else:
            basename = f"meetnote_{ts}"
        # Déjà archivé (relance depuis l'historique, reprise après crash) : la
        # source EST la destination. ffmpeg refuse d'éditer un fichier en place,
        # et le repli recopierait l'opus sous une extension .wav trompeuse.
        deja = os.path.abspath(wav_path)
        for ext in (".opus", ".wav"):
            if deja == os.path.abspath(os.path.join(config.AUDIO_ARCHIVE_DIR,
                                                    basename + ext)):
                return wav_path
        ffmpeg = _find_ffmpeg()
        if ffmpeg:
            dest = os.path.join(config.AUDIO_ARCHIVE_DIR, f"{basename}.opus")
            if _encoder_opus(ffmpeg, wav_path, dest):
                return dest
        dest = os.path.join(config.AUDIO_ARCHIVE_DIR, f"{basename}.wav")
        shutil.copy2(wav_path, dest)
        return dest
    except Exception as e:
        _log_error(f"Archive audio : {e}")
        return None


# ─── Transcript Teams ────────────────────────────────────────────────────────

def _combine_transcripts(whisper: str, teams: str) -> str:
    """Concatène le transcript Whisper et le transcript Teams avec séparateur."""
    parts = []
    if whisper.strip():
        parts.append("─── Transcription automatique (Whisper) ───\n\n" + whisper.strip())
    if teams.strip():
        parts.append("─── Transcript Teams ───\n\n" + teams.strip())
    return "\n\n".join(parts)


def _open_teams_transcript_dialog() -> str:
    """Ouvre un dialog modal pour coller le transcript Teams.
    Bloquant (wait_window). Retourne le texte collé ou '' si ignoré."""
    global _modal_dialog_open
    if not _root:
        return ""
    result = {"text": ""}
    win = tk.Toplevel(_root)
    win.title("Transcript Teams")
    win.configure(bg="#111125")
    win.attributes("-topmost", True)
    win.resizable(True, True)

    tk.Label(win,
             text="Colle le transcript Teams ci-dessous (optionnel) :",
             bg="#111125", fg="#e2e0fc",
             font=("Segoe UI", 9, "bold")).pack(padx=16, pady=(14, 6), anchor="w")

    frame = tk.Frame(win, bg="#111125")
    frame.pack(fill="both", expand=True, padx=16, pady=(0, 8))

    txt = tk.Text(frame, bg="#1a1a30", fg="#e2e0fc",
                  font=("Consolas", 8), insertbackground="#e2e0fc",
                  relief="flat", wrap="word", height=12)
    sb = ttk.Scrollbar(frame, orient="vertical", command=txt.yview)
    txt.configure(yscrollcommand=sb.set)
    sb.pack(side="right", fill="y")
    txt.pack(side="left", fill="both", expand=True)
    txt.focus_set()

    btn_row = tk.Frame(win, bg="#111125")
    btn_row.pack(fill="x", padx=16, pady=(0, 14))

    def _close():
        win.destroy()

    def _confirm():
        result["text"] = txt.get("1.0", "end-1c").strip()
        win.destroy()

    tk.Button(btn_row, text="Ignorer", command=_close,
              bg="#2a2a4a", fg="#9ab4e8", font=("Segoe UI", 9),
              relief="flat", padx=10, pady=4).pack(side="left")
    tk.Button(btn_row, text="✓ Valider", command=_confirm,
              bg="#5b5be8", fg="white", font=("Segoe UI", 9, "bold"),
              relief="flat", padx=10, pady=4).pack(side="right")

    win.protocol("WM_DELETE_WINDOW", _close)
    win.update_idletasks()
    # Centrer sur l'écran si la fenêtre principale est masquée (tray), sinon sur la fenêtre
    root_visible = _root.state() not in ("withdrawn", "iconic")
    if root_visible and _root.winfo_width() > 1:
        x = _root.winfo_x() + (_root.winfo_width()  - 420) // 2
        y = _root.winfo_y() + (_root.winfo_height() - 320) // 2
    else:
        sw = win.winfo_screenwidth()
        sh = win.winfo_screenheight()
        x = (sw - 420) // 2
        y = (sh - 320) // 2
    win.geometry(f"420x320+{x}+{y}")
    try:
        win.grab_set()
    except Exception:
        pass  # grab échoue si _root est withdrawn (tray) — sans conséquence
    _modal_dialog_open = True
    try:
        win.wait_window()
    except Exception:
        try:
            win.destroy()
        except Exception:
            pass
    finally:
        _modal_dialog_open = False
    return result["text"]


def _append_teams_to_notion(job: "_Job"):
    """Appende le transcript Teams à la page Notion existante du job."""
    if not job.notion_url or not job.teams_transcript.strip():
        return
    try:
        import notion_push as _np
        # Extraire le page_id depuis l'URL Notion (format: https://notion.so/TITLE-PAGEID32)
        raw = job.notion_url.rstrip("/").split("/")[-1]
        page_id_raw = raw.split("-")[-1] if "-" in raw else raw
        if len(page_id_raw) == 32:
            page_id = (f"{page_id_raw[:8]}-{page_id_raw[8:12]}-"
                       f"{page_id_raw[12:16]}-{page_id_raw[16:20]}-{page_id_raw[20:]}")
        else:
            page_id = page_id_raw
        _np.append_blocks_to_page(page_id, job.teams_transcript, config.NOTION_TOKEN)
    except Exception as e:
        _log_error(f"[{job.id}] Append Teams→Notion : {e}")


# ─── Participants affichés et envoyés ────────────────────────────────────────
def _participants_job(job: _Job) -> list[str]:
    """Invités de l'agenda puis participants Teams, sans doublon (affichage, envoi).

    Le renommage des locuteurs n'utilise pas cette liste : seuls les noms Teams y servent.
    """
    return source_agenda.fusionner_participants(job.participants_invites,
                                                job.teams_participants)


def _texte_transcript(job: _Job) -> str:
    """Texte transcrit tel qu'il part (fichier et Notion) : ligne « Participants : … » en tête.

    `job.transcript` reste intact (historique, re-traitement) : la ligne est ajoutée à
    l'envoi seulement, donc jamais deux fois.
    """
    return source_agenda.avec_ligne_participants(job.transcript, _participants_job(job))


def _consommer_invites(nom_saisi: str) -> list[str]:
    """Invités retenus au démarrage pour le job qu'on crée ; remet la mémoire à zéro.

    Un nom de réunion saisi à la main, différent de l'objet de la réunion retenue,
    n'hérite d'aucun invité.
    """
    global _invites_retenus
    retenus, _invites_retenus = _invites_retenus, None
    if retenus is None:
        return []
    return source_agenda.invites_pour_nom(retenus[0], retenus[1], nom_saisi)


# ─── Sauvegarde transcript local ─────────────────────────────────────────────
def _save_transcript_local(job: _Job) -> str | None:
    """Sauvegarde le transcript dans Documents/MeetNote/transcripts/."""
    try:
        os.makedirs(config.TRANSCRIPT_DIR, exist_ok=True)
        ts = job.start_time.strftime("%Y%m%d_%H%M%S")
        fname = f"transcript_{ts}.txt"
        path = os.path.join(config.TRANSCRIPT_DIR, fname)
        title = job.meeting_name or f"Réunion {job.start_time.strftime('%Y-%m-%d %H:%M')}"
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"Titre    : {title}\n")
            f.write(f"Date     : {job.start_time.strftime('%Y-%m-%d %H:%M')}\n")
            f.write(f"Durée    : {job.duration_min:.1f} min\n")
            f.write(f"Modèle   : {job.model_name}\n")
            if job.meeting_type:
                f.write(f"Type     : {job.meeting_type}\n")
            f.write("-" * 60 + "\n\n")
            if job.notes and job.notes.strip():
                f.write("=== NOTES MANUELLES ===\n")
                f.write(job.notes.strip() + "\n\n")
                f.write("-" * 60 + "\n\n")
            f.write(_texte_transcript(job))
        return path
    except Exception as e:
        _log_error(f"Sauvegarde transcript : {e}")
        return None


# ─── Diarisation (MFCC + DBSCAN, sans dépendance externe) ───────────────────
def _diarize(audio_path: str, segments: list) -> dict:
    """
    Identifie les locuteurs sur chaque segment Whisper via embeddings MFCC + DBSCAN.
    Distinction H/F via pitch moyen (F0 autocorrélation).
    Retourne {segment_idx: "SPKR_X (H|F)"} ou {} en cas d'échec.
    Dépendances : numpy, scipy, sklearn, soundfile — toutes déjà présentes.
    """
    if len(segments) < 2:
        return {}
    try:
        import warnings
        warnings.filterwarnings("ignore")
        from math import gcd

        import numpy as np_d
        import soundfile as sf
        from scipy.fft import dct
        from scipy.signal import resample_poly
        from sklearn.cluster import DBSCAN
        from sklearn.preprocessing import normalize

        SR = 16000

        # ── Charge audio ──────────────────────────────────────────────────────
        wav, sr = sf.read(audio_path, dtype="float32", always_2d=False)
        if wav.ndim > 1:
            wav = wav.mean(axis=1)
        if sr != SR:
            g = gcd(SR, sr)
            wav = resample_poly(wav, SR // g, sr // g).astype(np_d.float32)
        wav = np_d.clip(wav, -1.0, 1.0)

        # ── MFCC léger (13 coefficients, fenêtres de 25ms / pas 10ms) ────────
        def _mfcc_mean(chunk, n_mfcc=13, n_filt=26):
            N = len(chunk)
            if N < 400:
                return None
            frame_len = 400          # 25ms @ 16kHz
            hop       = 160          # 10ms
            n_fft     = 512
            # Découpe en frames
            frames = np_d.lib.stride_tricks.sliding_window_view(
                np_d.pad(chunk, (0, frame_len)), frame_len
            )[::hop]
            # Fenêtre de Hamming
            win = np_d.hamming(frame_len)
            frames = frames * win
            # Spectre de puissance
            mag   = np_d.abs(np_d.fft.rfft(frames, n=n_fft)) ** 2
            # Banc de filtres mel
            f_min, f_max = 80.0, 7600.0
            mel_min  = 2595 * np_d.log10(1 + f_min / 700)
            mel_max  = 2595 * np_d.log10(1 + f_max / 700)
            mel_pts  = np_d.linspace(mel_min, mel_max, n_filt + 2)
            hz_pts   = 700 * (10 ** (mel_pts / 2595) - 1)
            bin_pts  = np_d.floor((n_fft + 1) * hz_pts / SR).astype(int)
            fbank    = np_d.zeros((n_filt, n_fft // 2 + 1))
            for m in range(1, n_filt + 1):
                lo, ctr, hi = bin_pts[m-1], bin_pts[m], bin_pts[m+1]
                fbank[m-1, lo:ctr] = (np_d.arange(lo, ctr) - lo) / (ctr - lo + 1e-9)
                fbank[m-1, ctr:hi] = (hi - np_d.arange(ctr, hi)) / (hi - ctr + 1e-9)
            filter_banks = np_d.dot(mag, fbank.T)
            filter_banks = np_d.log(filter_banks + 1e-9)
            mfcc         = dct(filter_banks, type=2, axis=1, norm="ortho")[:, 1:n_mfcc+1]
            return mfcc.mean(axis=0)

        # ── Pitch F0 par autocorrélation ──────────────────────────────────────
        def _mean_pitch(chunk):
            if len(chunk) < 512:
                return 0.0
            frame = chunk[:2048]
            corr  = np_d.correlate(frame, frame, mode="full")[len(frame)-1:]
            corr[0] = 0
            lo, hi = int(SR / 300), int(SR / 80)
            if hi >= len(corr):
                return 0.0
            peak = np_d.argmax(corr[lo:hi]) + lo
            return SR / peak if peak > 0 else 0.0

        # ── Embeddings par segment ────────────────────────────────────────────
        embeds, valid_idx, pitches = [], [], []
        for i, (start, end, _) in enumerate(segments):
            s, e = int(start * SR), int(end * SR)
            chunk = wav[s:e]
            feat  = _mfcc_mean(chunk)
            if feat is None:
                continue
            embeds.append(feat)
            valid_idx.append(i)
            pitches.append(_mean_pitch(chunk))

        if len(embeds) < 2:
            return {}

        X = normalize(np_d.array(embeds))

        # ── DBSCAN clustering ─────────────────────────────────────────────────
        db     = DBSCAN(eps=0.35, min_samples=1, metric="cosine").fit(X)
        labels = db.labels_

        # ── Pitch moyen par locuteur → genre ──────────────────────────────────
        pitch_by_lbl = {}
        for ei, lbl in enumerate(labels):
            pitch_by_lbl.setdefault(lbl, []).append(pitches[ei])
        pitch_mean = {lbl: np_d.mean([v for v in ps if v > 0] or [0])
                      for lbl, ps in pitch_by_lbl.items()}

        def _genre(lbl):
            f0 = pitch_mean.get(lbl, 0)
            if f0 < 10:
                return ""
            return " (F)" if f0 >= 165 else " (H)"

        # ── Numérotation par ordre d'apparition ───────────────────────────────
        seen, counter = {}, [0]
        def _name(lbl):
            if lbl == -1:
                return ""
            if lbl not in seen:
                counter[0] += 1
                seen[lbl] = counter[0]
            return f"SPKR_{seen[lbl]}{_genre(lbl)}"

        result = {}
        for ei, seg_i in enumerate(valid_idx):
            n = _name(labels[ei])
            if n:
                result[seg_i] = n
        return result

    except ModuleNotFoundError as e:
        _log_error(f"Diarisation ignorée : {e} — installez le module manquant (pip install soundfile scikit-learn scipy)")
        return {}
    except Exception as e:
        _log_error(f"Diarisation ignorée : {e}")
        return {}


# ─── Étapes de traitement ─────────────────────────────────────────────────────
def _do_step_transcribe(job: _Job) -> bool:
    """Transcrit le fichier audio. Met à jour job.transcript + job.status_transcript."""
    global _whisper_model, _whisper_model_name
    from faster_whisper import WhisperModel

    job.status_transcript = "running"
    hist_mod.update(job)
    _refresh_ui()
    _set_status(f"Transcription [{job.id}]…")
    _set_progress(0)

    tmp_wav_for_whisper: str | None = None
    try:
        path = job.wav_path
        if not os.path.isfile(path):
            raise FileNotFoundError(f"Fichier audio introuvable : {path}")

        # Décoder opus/mp3/… en WAV avant de passer à Whisper (évite NoneType.write
        # causé par faster-whisper qui ne gère pas opus nativement sous PyInstaller)
        if not _is_real_wav(path):
            from notion_push import _find_ffmpeg
            ffmpeg = _find_ffmpeg()
            if ffmpeg:
                os.makedirs(config.TEMP_DIR, exist_ok=True)
                tmp_wav_for_whisper = os.path.join(
                    config.TEMP_DIR, f"whisper_{os.path.splitext(os.path.basename(path))[0]}.wav"
                )
                result = subprocess.run(
                    [ffmpeg, "-y", "-i", path,
                     "-ar", "16000", "-ac", "1", "-f", "wav", tmp_wav_for_whisper],
                    capture_output=True,
                    timeout=conversion_audio.delai_conversion_s(
                        conversion_audio.duree_estimee_s(path)),
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
                if result.returncode == 0 and os.path.isfile(tmp_wav_for_whisper):
                    path = tmp_wav_for_whisper
                else:
                    err = result.stderr.decode(errors='replace')[:200]
                    raise RuntimeError(f"ffmpeg decode failed: {err}")
            else:
                raise RuntimeError("ffmpeg introuvable — impossible de décoder l'audio opus/mp3")

        language = None if job.language == "auto" else job.language

        # Calibrage sur les capacités du poste : le gain vient du nombre de segments
        # traités de front, pas du nombre de threads d'un segment isolé (cf. perf_profile).
        profil = perf_profile.choisir(
            user_config.get("perf_profile", "auto"),
            enregistrement_en_cours=_recording,
            sur_batterie=_is_on_battery(),
        )

        # Le profil fait partie de la clé de cache : un modèle chargé en mode économe
        # resterait bridé pour tous les jobs suivants s'il était réutilisé tel quel.
        cle_modele = (job.model_name, profil.fils, profil.threads)
        if _whisper_model is None or _whisper_model_name != cle_modele:
            _set_progress(5)
            _set_status(f"Chargement modèle {job.model_name}…")
            # Jamais de nom symbolique : faster-whisper téléchargerait dans le cache
            # HF sans progression. Le worker ne laisse passer un job que si le
            # modèle est complet (_job_attend_modele) ; ce cas ne reste possible
            # que si le dossier a disparu entre-temps.
            model_path = modeles.chemin_modele(job.model_name)
            if model_path is None:
                raise RuntimeError(
                    f"Modèle {job.model_name} introuvable : relancez la transcription "
                    "pour le télécharger.")
            _whisper_model      = WhisperModel(
                model_path, device="cpu", compute_type="int8",
                cpu_threads=profil.threads, num_workers=profil.fils,
            )
            _whisper_model_name = cle_modele
            _log_error(f"[{job.id}] Transcription calibrée : {profil.resume}")

        # Référence figée : un changement de réglage pendant la transcription remet
        # _whisper_model à None, les fils en vol ne doivent pas le voir disparaitre.
        modele = _whisper_model

        _set_progress(20)

        if language is None:
            _, detect_info = modele.transcribe(path, language=None, beam_size=1,
                                                        vad_filter=True, temperature=0,
                                                        max_new_tokens=1)
            detected = detect_info.language
        else:
            detected = language

        task       = "transcribe"  # toujours transcrire dans la langue source
        lang_label = detected.upper()
        _set_status(f"Transcription [{lang_label}{'→EN' if task == 'translate' else ''}]…")

        if _is_real_wav(path):
            seg_paths = _split_wav_for_transcription(path, config.TRANSCRIPTION_SEGMENT_MIN)
        else:
            seg_paths = [path]  # opus/mp3/etc → Whisper lit directement via ffmpeg
        n_segs    = len(seg_paths)
        # ── Collecte des segments Whisper avec timestamps absolus ──────────────
        raw_segments = []   # list of (start_abs, end_abs, text)
        all_lines    = []

        # Les segments sont transcrits de front quand le profil l'autorise, mais leurs
        # résultats sont réassemblés dans l'ordre du fichier : les timestamps absolus
        # et la diarisation qui suit en dépendent.
        n_fils      = max(1, min(profil.fils, n_segs))
        par_segment = [[] for _ in seg_paths]   # résultats indexés, jamais concaténés
        avancement  = [0.0] * n_segs            # fraction traitée de chaque segment
        a_traiter   = collections.deque(range(n_segs))
        verrou      = threading.Lock()
        echecs      = []

        def _transcrire_segment(i: int):
            """Transcrit le segment i et range son résultat à l'indice i."""
            seg_segments, seg_info = modele.transcribe(
                seg_paths[i],
                language=detected,
                task=task,
                beam_size=5,
                vad_filter=True,
                vad_parameters={"min_silence_duration_ms": 500},
                condition_on_previous_text=False,
                no_speech_threshold=0.6,
                temperature=0,
            )
            seg_total   = max(seg_info.duration, 1)
            time_offset = i * config.TRANSCRIPTION_SEGMENT_MIN * 60
            trouves     = []
            for seg in seg_segments:
                if _worker_cancel_event.is_set():
                    return
                text = seg.text.strip()
                if not text:
                    continue
                trouves.append((seg.start + time_offset, seg.end + time_offset, text))
                with verrou:
                    avancement[i] = min(seg.end / seg_total, 1.0)
                    total = sum(avancement) / n_segs
                _set_progress(min(20 + int(total * 55), 75))
            par_segment[i] = trouves
            with verrou:
                avancement[i] = 1.0
                finis = sum(1 for a in avancement if a >= 1.0)
            if n_segs > 1:
                _set_status(f"Transcription segment {finis}/{n_segs} [{lang_label}]…")

        def _ouvrier():
            """Prend les segments restants un à un jusqu'à épuisement ou annulation."""
            while not _worker_cancel_event.is_set():
                with verrou:
                    if not a_traiter:
                        return
                    i = a_traiter.popleft()
                try:
                    _transcrire_segment(i)
                except Exception as exc:      # remontée au fil principal après jonction
                    with verrou:
                        echecs.append(exc)
                    return
                if seg_paths[i] != path:
                    try: os.remove(seg_paths[i])
                    except OSError: pass

        if n_segs > 1:
            _set_status(f"Transcription de {n_segs} segments [{lang_label}]…")
        fils = [threading.Thread(target=_ouvrier, daemon=True) for _ in range(n_fils)]
        for f in fils:
            f.start()
        for f in fils:
            f.join()

        # Tous les fils sont joints : ce qui subsiste est un segment jamais traité
        # (annulation) ou abandonné en vol (échec d'un fil).
        for remaining in seg_paths:
            if remaining != path and os.path.isfile(remaining):
                try: os.remove(remaining)
                except OSError: pass

        if echecs:
            raise echecs[0]

        if _worker_cancel_event.is_set():
            job.status_transcript = "queued"
            job.error_msg = ""
            hist_mod.update(job)
            _set_status("Transcription annulée.")
            _set_progress(0)
            return False

        for trouves in par_segment:
            raw_segments.extend(trouves)

        # ── Diarisation (resemblyzer + DBSCAN) ────────────────────────────────
        speaker_map = _diarize(path, raw_segments)  # {idx: "SPKR_X (H/F)"} ou {}

        # ── Substitution des labels SPKR_X par les vrais noms Teams ──────────
        if speaker_map and job.teams_participants:
            # Collecter les speakers uniques dans leur ordre d'apparition
            seen_spkr: list[str] = []
            for idx_s in range(len(raw_segments)):
                raw_lbl = speaker_map.get(idx_s, "")
                if raw_lbl:
                    # Extraire "SPKR_X" sans le suffixe genre
                    base = raw_lbl.split(" ")[0]  # ex: "SPKR_0"
                    if base not in seen_spkr:
                        seen_spkr.append(base)
            # Mapper chaque SPKR_X → nom Teams (dans l'ordre)
            teams_names = list(job.teams_participants)
            spkr_name_map: dict[str, str] = {}
            for i, base in enumerate(seen_spkr):
                if i < len(teams_names):
                    spkr_name_map[base] = teams_names[i]
            # Reconstruire speaker_map avec vrais noms
            new_speaker_map: dict[int, str] = {}
            for idx_s, raw_lbl in speaker_map.items():
                base = raw_lbl.split(" ")[0]
                gender_suffix = " ".join(raw_lbl.split(" ")[1:])  # ex: "(H)"
                if base in spkr_name_map:
                    new_speaker_map[idx_s] = f"{spkr_name_map[base]} {gender_suffix}".strip()
                else:
                    new_speaker_map[idx_s] = raw_lbl
            speaker_map = new_speaker_map

        for idx, (start_abs, end_abs, text) in enumerate(raw_segments):
            t  = int(start_abs)
            ts = f"[{t // 3600:02d}:{(t % 3600) // 60:02d}:{t % 60:02d}]"
            spk = speaker_map.get(idx, "")
            prefix = f"{spk}: " if spk else ""
            all_lines.append(f"{ts} {prefix}{text}")

        _set_progress(85)
        job.transcript = "\n".join(all_lines)
        job.whisper_transcript = job.transcript   # sauvegarde le Whisper pur
        if job.teams_transcript.strip():
            job.transcript = _combine_transcripts(job.transcript, job.teams_transcript)
        job.status_transcript = "done"
        hist_mod.update(job)
        _set_progress(90)
        return True

    except Exception as e:
        job.status_transcript = "error"
        job.error_msg = str(e)
        hist_mod.update(job)
        _log_error(f"[{job.id}] Transcription : {e}")
        _set_status("Erreur transcription — voir journal.")
        _set_progress(0)
        return False

    finally:
        if tmp_wav_for_whisper and os.path.isfile(tmp_wav_for_whisper):
            try: os.remove(tmp_wav_for_whisper)
            except OSError: pass


def _do_step_notion(job: _Job) -> bool:
    """Pousse vers Notion. Met à jour job.notion_url + job.status_notion."""
    job.status_notion = "running"
    hist_mod.update(job)
    _refresh_ui()
    _set_status("Envoi vers Notion…")

    try:
        title = job.meeting_name or f"Réunion {job.start_time.strftime('%Y-%m-%d %H:%M')}"
        notion_type = "Audio" if job.wav_path else job.meeting_type
        page = push_to_notion(
            _texte_transcript(job),
            source="PC",
            duration_min=job.duration_min,
            whisper_model=job.model_name,
            meeting_type=notion_type,
            start_time=job.start_time,
            audio_path=job.wav_path,
            title_override=title,
            attachments=job.attachments if job.attachments else None,
            notes=job.notes,
        )
        job.notion_url = f"https://notion.so/{page['id'].replace('-', '')}"
        job.status_notion = "done"
        hist_mod.update(job)
        _set_progress(100)
        _set_status("✓ Envoyé dans Notion !")
        return True

    except Exception as e:
        job.status_notion = "error"
        job.error_msg = str(e)
        hist_mod.update(job)
        _log_error(f"[{job.id}] Notion : {e}")
        _set_status("Erreur Notion — voir journal.")
        _set_progress(0)
        return False


# ─── Traitement complet d'un job ─────────────────────────────────────────────
def _process_job(job: _Job):
    """Séquence : archive audio → transcription → sauvegarde txt → push Notion."""

    # ── Cas transcript-only : pas d'audio à traiter, aller directement à Notion ──
    if job.status_transcript == "done" and job.transcript and not job.wav_path:
        txt_path = _save_transcript_local(job)
        if txt_path:
            job.transcript_path = txt_path
        hist_mod.update(job)
        if job.output_mode == "fichier":
            job.status_notion = "skipped"
            hist_mod.update(job)
            if txt_path:
                subprocess.Popen(f'explorer /select,"{txt_path}"')
            _set_progress(100)
            _set_status("✓ Fichier sauvegardé dans Documents/MeetNote/transcripts/")
        else:
            _do_step_notion(job)
        _refresh_ui()
        if _root:
            _root.after(3000, lambda: _set_progress(0))
        return

    wav_tmp = job.wav_path  # chemin WAV temporaire avant archivage

    try:
        # 1. Archive audio locale
        _set_status("Archivage audio…")
        archived = _archive_audio(wav_tmp, job.start_time, job.meeting_name)
        if archived:
            job.wav_path = archived  # pointer vers l'archive définitive
        hist_mod.update(job)

        # 2. Transcription
        ok = _do_step_transcribe(job)

        if ok:
            # 3. Sauvegarde transcript local (systématique, avant Notion)
            txt_path = _save_transcript_local(job)
            if txt_path:
                job.transcript_path = txt_path
            hist_mod.update(job)

            # 4. Push selon mode de sortie
            if job.output_mode == "fichier":
                job.status_notion = "skipped"
                hist_mod.update(job)
                if txt_path:
                    subprocess.Popen(f'explorer /select,"{txt_path}"')
                _set_progress(100)
                _set_status("✓ Fichier sauvegardé dans Documents/MeetNote/transcripts/")
            else:
                _do_step_notion(job)

    finally:
        # Supprimer le WAV temporaire (sauf si c'est déjà l'archive)
        if wav_tmp != job.wav_path and os.path.isfile(wav_tmp):
            try: os.remove(wav_tmp)
            except OSError: pass
        elif job.status_transcript == "error" and os.path.isfile(wav_tmp):
            # En cas d'erreur : conserver le WAV pour diagnostic
            _log_error(f"[{job.id}] Fichier audio conservé : {wav_tmp}")

    _refresh_ui()
    if _root:
        _root.after(3000, lambda: _set_progress(0))


# ─── Modèles Whisper : récupération en arrière-plan ──────────────────────────
# Un seul fil de téléchargement, qui sert les demandes une à une (le verrou de
# modeles.telecharger garantit en plus qu'il n'y en a jamais deux de front). Une
# nouvelle demande passe devant les autres : c'est le dernier choix de l'utilisateur.
_dl_lock       = threading.Lock()
_dl_demandes   = collections.deque()   # noms de modèles à récupérer, le prochain à gauche
_dl_nom        = None                  # modèle en cours de téléchargement
_choix_modele_courant = ""             # choix du combo, copié ici : tkinter interdit hors thread Tk
_dl_thread     = None
_dl_annuler    = threading.Event()     # fermeture de l'application
_dl_reveil     = threading.Event()     # nouvelle demande : écourte l'attente avant un essai
_dl_texte      = ""                    # libellé affiché ("" = rien à montrer)
_dl_anime      = False                 # points d'activité tant que ce n'est pas fini
_dl_tick       = 0
_dl_label      = None                  # Label Tk du libellé (créé dans _build_window)
_DL_PRET_AFFICHE_MS = 4000            # durée d'affichage de « modèle prêt »
_DL_ANIMATION_MS    = 600             # pas de l'animation des points


def _rafraichir_telechargement():
    """Écrit le libellé dans la fenêtre. Thread Tk uniquement."""
    if _dl_label is None:
        return
    if not _dl_texte:
        _dl_label.pack_forget()
        return
    points = modeles.points_activite(_dl_tick) if _dl_anime else ""
    _dl_label.config(text=_dl_texte + points)
    if not _dl_label.winfo_ismapped():
        _dl_label.pack(fill="x", padx=10, pady=(0, 8))


def _animer_telechargement():
    """Fait avancer les points tant qu'un téléchargement est affiché. Thread Tk."""
    global _dl_tick
    _dl_tick += 1
    if _dl_texte and _dl_anime:
        _rafraichir_telechargement()
    if _root:
        _root.after(_DL_ANIMATION_MS, _animer_telechargement)


def _afficher_telechargement(texte: str, anime: bool = False):
    """Fixe le libellé depuis n'importe quel thread ; l'affichage passe par Tk."""
    global _dl_texte, _dl_anime
    _dl_texte, _dl_anime = texte, anime
    if _root is None:
        return
    try:
        _root.after(0, _rafraichir_telechargement)
    except (RuntimeError, tk.TclError):
        pass  # fenêtre déjà détruite : l'application se ferme


def _effacer_telechargement_si(texte: str):
    """Efface le libellé s'il n'a pas changé entre-temps (autre téléchargement lancé)."""
    if _dl_texte == texte:
        _afficher_telechargement("")


def _rappel_progression(nom: str, recus: int, total: int):
    """Rappel de modeles.telecharger (thread de suivi). Ne lève jamais : une exception
    ici ferait échouer un téléchargement pourtant réussi."""
    try:
        fini = total > 0 and recus >= total
        _afficher_telechargement(
            modeles.libelle_progression(nom, recus, total), anime=not fini)
    except Exception as exc:
        _logger.warning("Progression du modèle %s non affichée (%s)",
                        nom, type(exc).__name__)


def _liberer_jobs_attente():
    """Remet en tête de file les jobs dont le modèle est maintenant complet. Ne remet
    que les jobs encore `queued` et absents de la file ; écarte les autres de l'attente."""
    with _queue_lock:
        prets = modeles.jobs_a_liberer(
            _jobs_attente_modele, _job_queue,
            lambda nom: modeles.chemin_modele(nom) is not None)
        ids_prets = {j.id for j in prets}
        _jobs_attente_modele[:] = [
            j for j in modeles.jobs_a_conserver(_jobs_attente_modele, _job_queue)
            if j.id not in ids_prets]
        for job in reversed(prets):
            _job_queue.appendleft(job)
    if prets:
        _work_event.set()
        _refresh_ui()


def _remettre_en_file(job: "_Job", tete: bool = False):
    """Met le job en file après l'avoir retiré de l'attente du modèle (une seule source
    de vérité : sinon il serait traité deux fois). Sans effet s'il est déjà en file."""
    with _queue_lock:
        _jobs_attente_modele[:] = modeles.sans_job(_jobs_attente_modele, job.id)
        if any(j.id == job.id for j in _job_queue):
            return
        if tete:
            _job_queue.appendleft(job)
        else:
            _job_queue.append(job)


def _noms_attendus_par_les_jobs() -> list:
    with _queue_lock:
        return [j.model_name for j in _jobs_attente_modele]


def _modele_encore_voulu(nom: str) -> bool:
    """Vrai si `nom` est le choix courant du combo ou attendu par un job en attente."""
    return bool(modeles.modeles_voulus(
        [nom], _choix_modele_courant, _noms_attendus_par_les_jobs()))


def _modeles_voulus_maintenant() -> list:
    """Demandes encore utiles : purge `_dl_demandes` des modèles abandonnés (ni choisis
    dans la liste, ni attendus par un job) et efface leur libellé d'échec périmé."""
    attendus = _noms_attendus_par_les_jobs()
    with _dl_lock:
        demandes = list(_dl_demandes)
        voulus = modeles.modeles_voulus(demandes, _choix_modele_courant, attendus)
        _dl_demandes.clear()
        _dl_demandes.extend(voulus)
    for nom in demandes:
        if nom not in voulus and modeles.est_libelle_echec(_dl_texte, nom):
            _afficher_telechargement("")
    return voulus


def _echec_telechargement(nom: str, cause: str):
    """Affiche la cause, remet le modèle en file et attend le délai (ou une nouvelle
    demande) avant le prochain essai. Les jobs concernés restent en attente. Un modèle
    qui n'est plus voulu est abandonné sans libellé ni nouvel essai."""
    global _dl_nom
    if not _modele_encore_voulu(nom):
        _afficher_telechargement("")
        return
    texte = modeles.libelle_echec(nom, cause, modeles.DELAI_NOUVEL_ESSAI_S)
    _afficher_telechargement(texte)
    _log_error(texte)
    with _dl_lock:
        if nom not in _dl_demandes:
            _dl_demandes.append(nom)
        # Pendant l'attente, plus rien n'est « en cours » : redemander le même modèle
        # doit réveiller le fil au lieu d'être ignoré.
        _dl_nom = None
        attendre = not _dl_reveil.is_set()
        _dl_reveil.clear()
    if attendre:
        _dl_reveil.wait(modeles.DELAI_NOUVEL_ESSAI_S)


def _telecharger_un_modele(nom: str):
    """Télécharge `nom` avec progression ; libère les jobs en attente au succès."""
    _afficher_telechargement(modeles.libelle_demarrage(nom), anime=True)
    try:
        modeles.telecharger(
            nom,
            rappel=lambda recus, total: _rappel_progression(nom, recus, total),
            annuler=_dl_annuler)
    except modeles.TelechargementAnnule:
        _afficher_telechargement("")
    except modeles.ErreurTelechargement as exc:
        _echec_telechargement(nom, str(exc))
    except Exception as exc:
        _logger.exception("Téléchargement du modèle %s", nom)
        _echec_telechargement(
            nom, f"erreur inattendue ({type(exc).__name__}).")
    else:
        texte = f"Modèle {nom} prêt."
        _afficher_telechargement(texte)
        _log_error(texte)
        if _root:
            _root.after(_DL_PRET_AFFICHE_MS, lambda: _effacer_telechargement_si(texte))
        _liberer_jobs_attente()


def _boucle_telechargement():
    """Sert les demandes une à une ; s'arrête quand il n'y en a plus (ou à la fermeture).
    Une exception inattendue ne doit pas laisser l'état figé : le `finally` remet
    `_dl_thread` et `_dl_nom` à None pour qu'une demande suivante relance un fil."""
    global _dl_nom, _dl_thread
    try:
        while True:
            _modeles_voulus_maintenant()  # abandonne les modèles que plus personne ne veut
            with _dl_lock:
                if _dl_annuler.is_set() or not _dl_demandes:
                    _dl_nom = None
                    _dl_thread = None
                    return
                nom = _dl_demandes.popleft()
                _dl_nom = nom
                _dl_reveil.clear()
            if modeles.chemin_modele(nom) is not None:
                _liberer_jobs_attente()
                continue
            _telecharger_un_modele(nom)
    except Exception:
        _logger.warning("Fil de téléchargement des modèles interrompu", exc_info=True)
    finally:
        with _dl_lock:
            if _dl_thread is threading.current_thread():
                _dl_nom = None
                _dl_thread = None


def _demander_modele(nom: str):
    """Demande la présence du modèle : sans effet s'il est complet ou déjà en cours,
    sinon il est téléchargé (relancé tout de suite s'il attendait un nouvel essai)."""
    global _dl_thread
    if nom not in modeles.MODELES or _dl_annuler.is_set():
        return
    if modeles.chemin_modele(nom) is not None:
        _liberer_jobs_attente()
        return
    with _dl_lock:
        if nom == _dl_nom:
            return
        if nom in _dl_demandes:
            _dl_demandes.remove(nom)
        _dl_demandes.appendleft(nom)
        _dl_reveil.set()
        if _dl_thread is None:
            _dl_thread = threading.Thread(
                target=_boucle_telechargement, name="telechargement-modele", daemon=True)
            _dl_thread.start()


def _arreter_telechargement():
    """Fermeture : annule le téléchargement en cours (fichiers partiels conservés pour
    la reprise) sans attendre le fil, qui est un démon."""
    _dl_annuler.set()
    _dl_reveil.set()


def _job_attend_modele(job: "_Job") -> bool:
    """Vrai (et job mis de côté) si le job doit transcrire avec un modèle absent."""
    requise = modeles.transcription_requise(
        job.status_transcript, job.transcript, job.wav_path)
    # Un nom hors liste n'est pas téléchargeable : on laisse la transcription échouer
    # avec son message plutôt que de faire attendre le job indéfiniment.
    disponible = (job.model_name not in modeles.MODELES
                  or modeles.chemin_modele(job.model_name) is not None)
    if modeles.decision_job(requise, disponible) == modeles.PRET:
        return False
    with _queue_lock:
        if all(j.id != job.id for j in _jobs_attente_modele):
            _jobs_attente_modele.append(job)
    _log_error(f"[{job.id}] {modeles.LIBELLE_ATTENTE_MODELE} ({job.model_name})")
    _set_status(modeles.LIBELLE_ATTENTE_MODELE)
    _demander_modele(job.model_name)
    _refresh_ui()
    return True


def _demarrer_modele_initial():
    """Fixe le modèle au premier lancement (jamais d'écrasement d'un choix existant),
    puis s'assure qu'il est présent. Thread Tk."""
    enregistre = user_config.get(modeles.CLE_MODELE_CHOISI, "")
    present = modeles.chemin_modele(config.WHISPER_MODEL)
    a_ecrire = modeles.modele_a_enregistrer(
        enregistre, modeles.ram_totale_gio(),
        config.WHISPER_MODEL if present is not None else None)
    if a_ecrire:
        user_config.set(modeles.CLE_MODELE_CHOISI, a_ecrire)
        _logger.info("Modèle Whisper choisi au premier lancement : %s", a_ecrire)
        if _model_var is not None:
            _model_var.set(a_ecrire)  # déclenche aussi _sur_choix_modele
    global _choix_modele_courant
    choix = _model_var.get() if _model_var is not None else a_ecrire
    if choix:
        _choix_modele_courant = choix
        _demander_modele(choix)


def _sur_choix_modele(*_):
    """Choix dans la liste : enregistré, et téléchargé s'il est absent. Thread Tk."""
    global _choix_modele_courant
    nom = _model_var.get() if _model_var is not None else ""
    if nom not in modeles.MODELES:
        return
    _choix_modele_courant = nom
    if user_config.get(modeles.CLE_MODELE_CHOISI, "") != nom:
        user_config.set(modeles.CLE_MODELE_CHOISI, nom)
    _demander_modele(nom)
    _dl_reveil.set()  # le fil purge sans attendre ce que ce choix rend inutile


# ─── Worker ──────────────────────────────────────────────────────────────────
def _worker_loop():
    global _worker_busy, _current_job
    while True:
        _work_event.wait()
        _work_event.clear()
        while True:
            with _queue_lock:
                if not _job_queue:
                    break
                job = _job_queue.popleft()
            if _job_attend_modele(job):
                continue  # mis de côté : le worker reste libre pour les autres jobs
            _worker_busy = True
            _current_job = job
            _worker_cancel_event.clear()
            _prevent_sleep()
            try:
                _process_job(job)
            finally:
                _allow_sleep()
                _worker_busy = False
                _current_job = None
        # Queue vide — forcer le retour à Idle
        _refresh_ui()


# ─── Relance d'étapes depuis l'historique ────────────────────────────────────
def _requeue_transcribe(job: _Job):
    """Relance la transcription (+ notion si pertinent) depuis le panel historique."""
    if not _warn_if_on_battery():
        return
    job.status_transcript = "queued"
    job.transcript = ""
    job.transcript_path = ""
    if job.output_mode != "fichier":
        job.status_notion = "pending"
    hist_mod.update(job)
    _remettre_en_file(job, tete=True)  # priorité haute
    _work_event.set()
    _refresh_ui()


def _requeue_notion(job: _Job):
    """Relance uniquement le push Notion (transcript déjà disponible)."""
    if not job.transcript:
        _log_error(f"[{job.id}] Pas de transcript disponible pour push Notion.")
        return
    job.status_notion = "pending"
    hist_mod.update(job)

    def _push_thread():
        _prevent_sleep()
        try:
            _do_step_notion(job)
        finally:
            _allow_sleep()
            _refresh_ui()

    threading.Thread(target=_push_thread, daemon=True).start()


def _delete_all_done():
    """Supprime de l'historique tous les jobs terminés (transcript=done ET notion=done/skipped)."""
    import tkinter.messagebox as mb
    def _is_done(j: _Job) -> bool:
        return (j.status_transcript == "done" and
                j.status_notion in ("done", "skipped"))
    with _all_jobs_lock:
        to_delete = [j for j in _all_jobs if _is_done(j)]
    if not to_delete:
        mb.showinfo("Supprimer terminés", "Aucun enregistrement terminé à supprimer.")
        return
    if not mb.askyesno("Supprimer terminés",
                        f"Supprimer {len(to_delete)} enregistrement(s) terminé(s) ?\n\n"
                        "(Les fichiers audio/transcript locaux ne sont pas supprimés.)",
                        default="no"):
        return
    ids = {j.id for j in to_delete}
    with _all_jobs_lock:
        _all_jobs[:] = [j for j in _all_jobs if j.id not in ids]
    entries = hist_mod.load()
    entries = [e for e in entries if e.get("id") not in ids]
    hist_mod.save(entries)
    _refresh_history_panel()


def _delete_all():
    """Supprime des jobs de l'historique via un choix : Complets / Tous / Annuler."""
    import tkinter as tk_dlg
    with _all_jobs_lock:
        total = len(_all_jobs)
        done_count = sum(
            1 for j in _all_jobs
            if j.status_transcript == "done" and j.status_notion in ("done", "skipped")
        )
    if total == 0:
        import tkinter.messagebox as mb
        mb.showinfo("Supprimer tout", "L'historique est déjà vide.")
        return

    choice = [None]

    parent = _history_win if (_history_win and _history_win.winfo_exists()) else None
    dlg = tk_dlg.Toplevel(parent)
    dlg.title("Supprimer l'historique")
    dlg.resizable(False, False)
    dlg.grab_set()
    if parent:
        dlg.transient(parent)
    _BG = "#16213e"
    _FG = "#e2e0fc"
    dlg.configure(bg=_BG)

    tk_dlg.Label(dlg, text="Que souhaitez-vous supprimer ?",
                 font=("Segoe UI", 10, "bold"), bg=_BG, fg=_FG).pack(padx=20, pady=(14, 4))
    tk_dlg.Label(dlg,
                 text=f"{done_count} complet(s)  ·  {total - done_count} en cours  ·  {total} total",
                 font=("Segoe UI", 8), bg=_BG, fg="#888888").pack(padx=20, pady=(0, 12))

    def _pick(val):
        choice[0] = val
        dlg.destroy()

    btn_frame = tk_dlg.Frame(dlg, bg=_BG)
    btn_frame.pack(padx=16, pady=(0, 14), fill="x")

    tk_dlg.Button(btn_frame, text=f"✓ Complets ({done_count})",
                  font=("Segoe UI", 9), bg="#1a3a10", fg="#88ee88",
                  activebackground="#225522", activeforeground="white",
                  relief="flat", bd=0, cursor="hand2",
                  command=lambda: _pick("done")).pack(fill="x", pady=2)
    tk_dlg.Button(btn_frame, text=f"✕ Tout ({total})",
                  font=("Segoe UI", 9), bg="#3a1010", fg="#ffb4ab",
                  activebackground="#551515", activeforeground="white",
                  relief="flat", bd=0, cursor="hand2",
                  command=lambda: _pick("all")).pack(fill="x", pady=2)
    tk_dlg.Button(btn_frame, text="Annuler",
                  font=("Segoe UI", 9), bg="#2a2a3a", fg="#aaaaaa",
                  activebackground="#333344", activeforeground="white",
                  relief="flat", bd=0, cursor="hand2",
                  command=lambda: _pick(None)).pack(fill="x", pady=2)

    dlg.wait_window()

    if choice[0] == "done":
        _delete_all_done()
    elif choice[0] == "all":
        import tkinter.messagebox as mb
        unfinished_count = total - done_count
        if unfinished_count:
            msg = (f"{unfinished_count} enregistrement(s) sur {total} ne sont pas encore\n"
                   f"transcrits ou envoyés vers Notion.\n\n"
                   f"Supprimer quand même TOUT l'historique ?")
            if not mb.askyesno("Supprimer tout", msg, default="no", icon="warning"):
                return
        else:
            if not mb.askyesno("Supprimer tout",
                               f"Supprimer les {total} enregistrement(s) de l'historique ?\n\n"
                               "(Les fichiers audio/transcript locaux ne sont pas supprimés.)",
                               default="no"):
                return
        with _all_jobs_lock:
            _all_jobs.clear()
        hist_mod.save([])
        _refresh_history_panel()


def _bulk_transcribe_all():
    """Lance la transcription de tous les jobs en attente/erreur (AC uniquement)."""
    import tkinter.messagebox as mb
    if _is_on_battery():
        mb.showwarning("Sur batterie",
                       "La transcription en masse n'est disponible que sur secteur.\n\n"
                       "Branchez le PC et réessayez.")
        return
    with _all_jobs_lock:
        candidates = [
            j for j in _all_jobs
            if j.status_transcript in ("queued", "error")
            and os.path.isfile(j.wav_path)
        ]
    if not candidates:
        mb.showinfo("Transcrire tout", "Aucun enregistrement à transcrire.")
        return
    if not mb.askyesno("Transcrire tout",
                        f"Lancer la transcription + push Notion de "
                        f"{len(candidates)} enregistrement(s) ?",
                        default="yes"):
        return
    for job in candidates:
        job.status_transcript = "queued"
        job.transcript = ""
        job.transcript_path = ""
        if job.output_mode != "fichier":
            job.status_notion = "pending"
        hist_mod.update(job)
        _remettre_en_file(job)
    _work_event.set()
    _refresh_ui()
    if _history_win and _history_win.winfo_exists():
        _refresh_history_panel()


def _check_pending_at_startup():
    """Au démarrage, relance automatiquement et silencieusement les transcriptions
    laissées en attente ou interrompues (ex: PC éteint en pleine transcription)."""
    if _is_on_battery():
        return
    with _all_jobs_lock:
        candidates = [
            j for j in _all_jobs
            if j.status_transcript in ("queued", "running", "error")
            and j.meeting_type != "Agenda"
            and os.path.isfile(j.wav_path)
        ]
    if not candidates:
        return
    for job in candidates:
        job.status_transcript = "queued"
        job.transcript = ""
        job.transcript_path = ""
        if job.output_mode != "fichier":
            job.status_notion = "pending"
        hist_mod.update(job)
        _remettre_en_file(job)
    _work_event.set()
    _refresh_ui()
    if _history_win and _history_win.winfo_exists():
        _refresh_history_panel()


# ─── Notes manuelles (mini-fenêtre flottante) ─────────────────────────────────
def _rec_elapsed_str() -> str:
    """Temps écoulé depuis le début de l'enregistrement, format mm:ss."""
    if not _rec_start_time:
        return "00:00"
    secs = max(0, int((datetime.datetime.now() - _rec_start_time).total_seconds()))
    return f"{secs // 60:02d}:{secs % 60:02d}"


def _collect_notes() -> str:
    """Lit le contenu de la zone de notes (chaîne nettoyée)."""
    if _notes_text is None:
        return ""
    try:
        return _notes_text.get("1.0", "end").strip()
    except Exception:
        return ""


def _notes_insert_timestamp():
    """Insère [mm:ss] au curseur dans la zone de notes."""
    if _notes_text is None:
        return
    _notes_text.insert("insert", f"[{_rec_elapsed_str()}] ")
    _notes_text.focus_set()


def _show_notes_window():
    """Affiche (ou crée) la mini-fenêtre de prise de notes."""
    global _notes_win, _notes_text, _notes_timer_lbl, _notes_pinned
    if not _root:
        return

    if _notes_win is not None and _notes_win.winfo_exists():
        _notes_win.deiconify()
        _notes_win.lift()
        return

    S_BASE = "#111125"
    S_CARD = "#1e1e32"
    S_HIGH = "#28283d"
    FG     = "#e2e0fc"
    FG3    = "#ab8985"
    RED    = "#dc3232"

    _notes_pinned = True
    win = tk.Toplevel(_root)
    _notes_win = win
    win.title("MeetNote — Notes")
    win.configure(bg=S_BASE)
    win.attributes("-topmost", True)
    win.geometry("340x300")
    # Coin bas-droit de l'écran
    try:
        sw = win.winfo_screenwidth()
        sh = win.winfo_screenheight()
        win.geometry(f"340x300+{sw - 360}+{sh - 380}")
    except Exception:
        pass
    win.protocol("WM_DELETE_WINDOW", lambda: win.withdraw())

    # En-tête : titre + compteur REC
    hdr = tk.Frame(win, bg=S_CARD)
    hdr.pack(fill="x")
    tk.Label(hdr, text="📝 Notes", font=("Segoe UI", 10, "bold"),
             bg=S_CARD, fg=FG).pack(side="left", padx=8, pady=6)
    _notes_timer_lbl = tk.Label(hdr, text=f"● REC {_rec_elapsed_str()}",
                                font=("Segoe UI", 9), bg=S_CARD, fg=RED)
    _notes_timer_lbl.pack(side="right", padx=8)

    # Zone de saisie
    txt_frame = tk.Frame(win, bg=S_BASE)
    txt_frame.pack(fill="both", expand=True, padx=6, pady=6)
    _notes_text = tk.Text(txt_frame, bg=S_HIGH, fg=FG, font=("Segoe UI", 10),
                          relief="flat", bd=0, wrap="word", insertbackground=FG,
                          undo=True)
    sb = tk.Scrollbar(txt_frame, command=_notes_text.yview,
                      bg=S_BASE, troughcolor=S_BASE)
    _notes_text.config(yscrollcommand=sb.set)
    sb.pack(side="right", fill="y")
    _notes_text.pack(side="left", fill="both", expand=True)
    _notes_text.focus_set()

    # Horodatage auto en début de nouvelle ligne
    def _on_return(event):
        _notes_text.insert("insert", f"\n[{_rec_elapsed_str()}] ")
        return "break"
    _notes_text.bind("<Return>", _on_return)

    # Barre de boutons
    bar = tk.Frame(win, bg=S_CARD)
    bar.pack(fill="x")
    tk.Button(bar, text="⏱ Timestamp", font=("Segoe UI", 9),
              bg=S_CARD, fg=FG3, activebackground=S_HIGH, activeforeground=FG,
              relief="flat", bd=0, padx=8, pady=5, cursor="hand2",
              command=_notes_insert_timestamp).pack(side="left", padx=4, pady=4)

    pin_btn = tk.Button(bar, text="📌 Épinglé", font=("Segoe UI", 9),
                        bg=S_CARD, fg=FG3, activebackground=S_HIGH,
                        activeforeground=FG, relief="flat", bd=0, padx=8, pady=5,
                        cursor="hand2")

    def _toggle_pin():
        global _notes_pinned
        _notes_pinned = not _notes_pinned
        win.attributes("-topmost", _notes_pinned)
        pin_btn.config(text="📌 Épinglé" if _notes_pinned else "📌 Libre")
    pin_btn.config(command=_toggle_pin)
    pin_btn.pack(side="right", padx=4, pady=4)

    # Préfixe la première ligne avec un timestamp pour amorcer
    _notes_text.insert("1.0", f"[{_rec_elapsed_str()}] ")

    _update_notes_timer()


def _update_notes_timer():
    """Rafraîchit le compteur REC de la fenêtre de notes chaque seconde."""
    if (_notes_win is None or not _notes_win.winfo_exists()
            or _notes_timer_lbl is None):
        return
    if _recording:
        try:
            _notes_timer_lbl.config(text=f"● REC {_rec_elapsed_str()}")
        except Exception:
            return
        if _root:
            _root.after(1000, _update_notes_timer)
    else:
        try:
            _notes_timer_lbl.config(text="⏹ Terminé")
        except Exception:
            pass


def _start_notes_session():
    """Ouvre la fenêtre de notes pour un nouvel enregistrement (vide + timestamp)."""
    if _notes_win is not None and _notes_win.winfo_exists():
        _reset_notes_window()
        try:
            _notes_text.insert("1.0", f"[{_rec_elapsed_str()}] ")
            _notes_win.deiconify()
            _notes_win.lift()
        except Exception:
            pass
        _update_notes_timer()
    else:
        _show_notes_window()


def _hide_notes_window():
    """Masque la fenêtre de notes (sans détruire, pour conserver le texte)."""
    if _notes_win is not None and _notes_win.winfo_exists():
        try:
            _notes_win.withdraw()
        except Exception:
            pass


def _reset_notes_window():
    """Vide la zone de notes (nouvel enregistrement)."""
    if _notes_text is not None and _notes_win is not None and _notes_win.winfo_exists():
        try:
            _notes_text.delete("1.0", "end")
        except Exception:
            pass


# ─── Recording actions ────────────────────────────────────────────────────────
_SOURCE_MAP = {
    "🎤  Microphone uniquement": "micro",
    "🔊  Loopback (son du PC)":  "loopback",
    "🎤🔊  Mixte (micro + PC)":  "mixte",
}


def _forcer_agenda():
    """Demande un rechargement immédiat du cache d'agenda (le tick est déclenché tout de suite)."""
    _cache_agenda.forcer()
    if _outlook_poll_fn:
        _outlook_poll_fn()


def _reappliquer_preremplissage():
    """Après un rechargement réussi pendant un enregistrement démarré sans réunion connue,
    complète nom, heure de fin et invités (une seule fois, sans écraser un choix de
    l'utilisateur : la règle est dans `source_agenda.completer_preremplissage`)."""
    global _current_meeting_end, _invites_retenus, _preremplissage_en_attente
    if not (_recording and _preremplissage_en_attente):
        return
    _preremplissage_en_attente = False
    nom_saisi = _meeting_name_var.get() if _meeting_name_var else ""
    dernier_nom_auto = getattr(_meeting_name_var, "_last_outlook", "")
    a_faire = source_agenda.completer_preremplissage(
        _cache_agenda.reunions_en_cours(datetime.datetime.now(), 30),
        nom_saisi, dernier_nom_auto, _current_meeting_end, _invites_retenus)
    if a_faire.nom is not None and _meeting_name_var:
        _meeting_name_var.set(a_faire.nom)
        _meeting_name_var._last_outlook = a_faire.nom
    if a_faire.fin is not None:
        _current_meeting_end = a_faire.fin
    if a_faire.invites is not None:
        _invites_retenus = a_faire.invites


def _do_start():
    global _recording, _audio_chunks, _stream_mic, _loop_thread, _rec_start_time
    global _wav_stream, _wav_stream_path, _flush_frame_count, _end_warned_key
    global _reunion_imposee, _invites_retenus, _preremplissage_en_attente

    # La réunion imposée ne vaut que pour CE démarrage : consommée sur toutes les sorties
    imposee, _reunion_imposee = _reunion_imposee, None

    if _recording:
        return

    _end_warned_key = None

    source = _SOURCE_MAP.get(_source_var.get(), "micro")
    _recording       = True
    _audio_chunks    = []
    _flush_frame_count = 0
    _rec_start_time  = datetime.datetime.now()
    _stop_loop.clear()
    _prevent_sleep()

    # Démarrer la surveillance des participants Teams
    if teams_roster.is_teams_running():
        _teams_watcher.start(_rec_start_time)

    # Pré-remplir le nom de la réunion et définir l'heure de fin depuis Outlook
    # (lecture du cache d'agenda : aucun accès à Outlook, donc ni thread ni attente)
    # La réunion est celle dont l'objet correspond au nom saisi (choix du conflit), sinon la première
    mtg = source_agenda.choisir_reunion(
        _cache_agenda.reunions_en_cours(datetime.datetime.now(), 30),
        _meeting_name_var.get() if _meeting_name_var else "")
    # Invités : réunion imposée par l'auto-start, sinon celle que propose le cache
    retenue = imposee or mtg
    _invites_retenus = ((retenue["subject"], source_agenda.invites_de_reunion(retenue))
                        if retenue else None)
    # Réunion absente du cache (jamais chargé, ou trop ancien) : on le recharge, et le
    # résultat complétera l'enregistrement en cours
    _preremplissage_en_attente = retenue is None
    if _preremplissage_en_attente and _root:
        _root.after(0, _forcer_agenda)
    if mtg and _root:
        def _apply(m=mtg):
            global _current_meeting_end
            if _meeting_name_var and not _meeting_name_var.get().strip():
                _meeting_name_var.set(m["subject"])
                _meeting_name_var._last_outlook = m["subject"]
            # Définir l'heure de fin seulement si pas déjà définie (auto-start l'a peut-être fixée)
            if _current_meeting_end is None and _recording:
                _current_meeting_end = m["end"]
        _root.after(0, _apply)

    os.makedirs(config.TEMP_DIR, exist_ok=True)
    ts = _rec_start_time.strftime("%Y%m%d_%H%M%S")
    _wav_stream_path = os.path.join(config.TEMP_DIR, f"rec_{ts}.wav")
    _wav_stream = wave.open(_wav_stream_path, "w")
    _wav_stream.setnchannels(1)
    _wav_stream.setsampwidth(2)
    _wav_stream.setframerate(config.SAMPLE_RATE)

    try:
        if source == "micro":
            mic_info = sd.query_devices(sd.default.device[0])
            _stream_mic = sd.InputStream(
                samplerate=int(mic_info["default_samplerate"]),
                channels=1, dtype="float32",
                callback=_mic_callback,
            )
            _stream_mic.start()

        elif source == "loopback":
            _loop_thread = threading.Thread(
                target=_loopback_thread_fn, args=(False,), daemon=True
            )
            _loop_thread.start()

        elif source == "mixte":
            _loop_thread = threading.Thread(
                target=_loopback_thread_fn, args=(True,), daemon=True
            )
            _loop_thread.start()

    except Exception as e:
        _recording = False
        _stop_loop.set()
        _allow_sleep()
        _log_error(f"Erreur ouverture audio : {e}")
        _refresh_ui()
        return

    _start_notes_session()
    _refresh_ui()


def _do_stop_transcribe():
    global _recording, _stream_mic, _current_meeting_end

    if not _recording:
        return

    if not _warn_if_on_battery():
        return

    _current_meeting_end = None
    _recording = False
    _stop_loop.set()

    if _stream_mic:
        try: _stream_mic.stop(); _stream_mic.close()
        except Exception: pass
        _stream_mic = None

    if _level_var and _root:
        _root.after(0, lambda: _level_var.set(0))

    stop_time    = datetime.datetime.now()
    start_time   = _rec_start_time or stop_time
    duration_min = (stop_time - start_time).total_seconds() / 60
    path = _save_audio_to_tempfile()

    # Récupérer les participants Teams avant de libérer
    _teams_watcher.stop()
    teams_participants = _teams_watcher.get_participants()

    if not path:
        _allow_sleep()
        _set_status("Aucun audio enregistré.")
        _refresh_ui()
        return

    meeting_name = (_meeting_name_var.get().strip() if _meeting_name_var else "") or ""
    participants_invites = _consommer_invites(meeting_name)
    _ts_str = start_time.strftime("%Y-%m-%d %H:%M")
    if _ts_str not in meeting_name:
        meeting_name = f"{meeting_name} {_ts_str}".strip() if meeting_name else _ts_str

    job = _Job(
        id=f"{stop_time.strftime('%Y%m%d_%H%M%S')}",
        wav_path=path,
        start_time=start_time,
        duration_min=duration_min,
        model_name=_model_var.get() if _model_var else config.WHISPER_MODEL,
        language=_lang_var.get() if _lang_var else config.WHISPER_LANGUAGE,
        meeting_type=(_type_var.get() if _type_var else "A classifier") or "A classifier",
        output_mode=_output_var.get() if _output_var else "notion",
        meeting_name=meeting_name,
        teams_participants=teams_participants,
        participants_invites=participants_invites,
        notes=_collect_notes(),
    )
    _hide_notes_window()

    with _all_jobs_lock:
        _all_jobs.insert(0, job)
    hist_mod.add(job)

    with _queue_lock:
        _job_queue.append(job)
        queue_len = len(_job_queue)
    _work_event.set()

    if queue_len > 1:
        _set_status(f"En file d'attente ({queue_len} jobs)…")
    else:
        _set_status("En attente de transcription…")
    _refresh_ui()


def _do_stop_archive_only():
    """Arrête l'enregistrement, archive l'audio, mais ne transcrit pas maintenant."""
    global _recording, _stream_mic, _current_meeting_end

    if not _recording:
        return

    _current_meeting_end = None
    _recording = False
    _stop_loop.set()

    if _stream_mic:
        try: _stream_mic.stop(); _stream_mic.close()
        except Exception: pass
        _stream_mic = None

    if _level_var and _root:
        _root.after(0, lambda: _level_var.set(0))

    stop_time    = datetime.datetime.now()
    start_time   = _rec_start_time or stop_time
    duration_min = (stop_time - start_time).total_seconds() / 60
    path = _save_audio_to_tempfile()

    # Récupérer les participants Teams avant de libérer
    _teams_watcher.stop()
    teams_participants = _teams_watcher.get_participants()

    _allow_sleep()

    if not path:
        _set_status("Aucun audio enregistré.")
        _refresh_ui()
        return

    meeting_name = (_meeting_name_var.get().strip() if _meeting_name_var else "") or ""
    participants_invites = _consommer_invites(meeting_name)
    _ts_str = start_time.strftime("%Y-%m-%d %H:%M")
    if _ts_str not in meeting_name:
        meeting_name = f"{meeting_name} {_ts_str}".strip() if meeting_name else _ts_str

    # Créer et sauvegarder le job AVANT d'ouvrir le dialog : si l'app crashe
    # pendant la fenêtre Teams, le fichier audio reste référencé dans l'historique.
    job = _Job(
        id=f"{stop_time.strftime('%Y%m%d_%H%M%S')}",
        wav_path=path,
        start_time=start_time,
        duration_min=duration_min,
        model_name=_model_var.get() if _model_var else config.WHISPER_MODEL,
        language=_lang_var.get() if _lang_var else config.WHISPER_LANGUAGE,
        meeting_type=(_type_var.get() if _type_var else "A classifier") or "A classifier",
        output_mode=_output_var.get() if _output_var else "notion",
        meeting_name=meeting_name,
        teams_participants=teams_participants,
        participants_invites=participants_invites,
        notes=_collect_notes(),
        status_transcript="queued",
        status_notion="pending",
    )
    _hide_notes_window()

    with _all_jobs_lock:
        _all_jobs.insert(0, job)
    hist_mod.add(job)

    # Archiver l'audio immédiatement, avant d'ouvrir le dialog.
    # Si le dialog crashe ou est fermé brutalement, l'audio est déjà en cours
    # d'archivage dans un thread daemon séparé.
    def _archive_only():
        _prevent_sleep()
        try:
            _set_status("Archivage audio…")
            archived = _archive_audio(path, start_time, job.meeting_name)
            if archived:
                job.wav_path = archived
            hist_mod.update(job)
            _set_status("✓ Audio archivé. Transcription disponible depuis l'historique.")
        except Exception as e:
            _log_error(f"Archive : {e}")
        finally:
            _allow_sleep()
            _refresh_ui()

    threading.Thread(target=_archive_only, daemon=True).start()
    _set_status("Arrêt sans transcription…")
    _refresh_ui()


def _do_cancel():
    global _recording, _stream_mic, _audio_chunks, _wav_stream, _wav_stream_path

    if not _recording:
        return

    _recording = False
    _stop_loop.set()

    if _stream_mic:
        try: _stream_mic.stop(); _stream_mic.close()
        except Exception: pass
        _stream_mic = None

    _allow_sleep()
    _audio_chunks = []

    if _wav_stream is not None:
        try: _wav_stream.close()
        except Exception: pass
        _wav_stream = None
    if _wav_stream_path and os.path.isfile(_wav_stream_path):
        try: os.remove(_wav_stream_path)
        except OSError: pass
    _wav_stream_path = None

    if _level_var and _root:
        _root.after(0, lambda: _level_var.set(0))

    _hide_notes_window()
    _set_status("Annulé.")
    _refresh_ui()


# ─── Outlook auto-start helpers ──────────────────────────────────────────────
def _is_auto_start_excluded(subject: str) -> bool:
    """Retourne True si l'intitulé contient un motif de la liste d'exclusion."""
    s = subject.lower()
    return any(excl in s for excl in _auto_start_exclusions)


# ─── UI helpers ──────────────────────────────────────────────────────────────
def _set_status(msg: str):
    if _root and _status_var:
        _root.after(0, lambda: _status_var.set(msg))

def _set_progress(val: float):
    if _root and _progress_var:
        _root.after(0, lambda: _progress_var.set(val))

def _log_error(msg: str):
    ts   = datetime.datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}\n"
    try:
        _logger.info(msg)
    except Exception:
        pass
    if _root and _log_text:
        def _insert():
            _log_text.config(state="normal")
            _log_text.insert("end", line)
            _log_text.see("end")
            _log_text.config(state="disabled")
        _root.after(0, _insert)
    else:
        print(line, file=sys.stderr)

def _refresh_ui():
    if _root:
        _root.after(0, _update_window_state)
    if _icon:
        _icon.icon  = _make_icon(_recording)
        _icon.title = "MeetNote — Enregistrement…" if _recording else "MeetNote — Prêt"
        _icon.menu  = _build_menu()

def _update_window_state():
    if _recording:
        if _btn_start:
            _btn_start.config(text="⏹  STOP RECORDING",
                              bg="#1a6b1a", activebackground="#2e7d32")
        if _source_combo: _source_combo.config(state="disabled")
        if _speaker_combo: _speaker_combo.config(state="disabled")
        if _status_var: _status_var.set("Recording in progress…")
        if _status_dot:
            _status_dot.config(bg="#88d982")
        if _root and hasattr(_root, "_toggle_whisper"):
            _root._toggle_whisper(False)
    else:
        if _btn_start:
            _btn_start.config(text="⏺  START RECORDING",
                              bg="#b81c1c", activebackground="#dc3232")
        if _source_combo: _source_combo.config(state="readonly")
        if _speaker_combo: _speaker_combo.config(state="readonly")
        if _root and hasattr(_root, "_toggle_whisper"):
            _root._toggle_whisper(True)
        with _queue_lock:
            pending = len(_job_queue) + (1 if _worker_busy else 0)
            en_attente = len(_jobs_attente_modele)
        if pending > 0:
            if _status_var: _status_var.set(f"Processing {pending} job(s)…")
            if _status_dot: _status_dot.config(bg="#ffcc00")
        elif en_attente > 0:
            if _status_var:
                _status_var.set(f"{modeles.LIBELLE_ATTENTE_MODELE} ({en_attente} job(s))")
            if _status_dot: _status_dot.config(bg="#ffcc00")
        else:
            if _status_var: _status_var.set("Idle: Start to record")
            if _status_dot: _status_dot.config(bg="#555566")
    # Rafraîchir la fenêtre historique si ouverte
    if _history_win and _history_win.winfo_exists():
        _root.after(0, _refresh_history_panel)


# ─── Window show / hide ───────────────────────────────────────────────────────
def _show_window():
    if _root:
        def _do():
            _root.deiconify()
            _root.lift()
            _root.focus_force()
            if _history_win and _history_win.winfo_exists():
                _history_win.deiconify()
                _history_win.lift()
                _history_win.focus_force()
        _root.after(0, _do)

def _hide_window():
    if _root:
        _root.withdraw()
        if _history_win and _history_win.winfo_exists():
            _history_win.withdraw()

def _close_history_window():
    """Ferme (cache) la fenêtre history sans la détruire."""
    global _history_win
    if _history_win and _history_win.winfo_exists():
        _history_win.withdraw()


# ─── Tray menu ────────────────────────────────────────────────────────────────
def _build_menu():
    # Statut dynamique
    if _recording:
        status_label = "● Enregistrement en cours…"
    elif _job_queue or _worker_busy or _jobs_attente_modele:
        n = len(_job_queue) + len(_jobs_attente_modele) + (1 if _worker_busy else 0)
        status_label = f"⏳ Traitement ({n} job(s))"
    else:
        status_label = "○ Prêt"

    return pystray.Menu(
        pystray.MenuItem(status_label, lambda i, it: None, enabled=False),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Ouvrir",    lambda i, it: _show_window(), default=True),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("▶  Démarrer l'enregistrement", lambda i, it: _root.after(0, _do_start)             if _root else None, enabled=not _recording),
        pystray.MenuItem("⏹  Arrêter l'enregistrement",  lambda i, it: _root.after(0, _do_stop_archive_only) if _root else None, enabled=_recording),
        pystray.MenuItem("📝  Notes de réunion",         lambda i, it: _root.after(0, _show_notes_window)     if _root else None, enabled=_recording),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("📅  Agenda Notion (15 j)", lambda i, it: _root.after(0, _trigger_agenda_digest_manual) if _root else None),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quitter", _quit_app),
    )

def _open_audio_dir_tray():
    os.makedirs(config.AUDIO_ARCHIVE_DIR, exist_ok=True)
    subprocess.Popen(f'explorer "{config.AUDIO_ARCHIVE_DIR}"')

def _open_transcript_dir_tray():
    os.makedirs(config.TRANSCRIPT_DIR, exist_ok=True)
    subprocess.Popen(f'explorer "{config.TRANSCRIPT_DIR}"')

def _open_version_window():
    if not _root:
        return
    win = tk.Toplevel(_root)
    win.title(f"MeetNote v{VERSION} — À propos")
    win.configure(bg="#111125")
    win.resizable(True, True)
    win.geometry("580x500")
    win.attributes("-topmost", True)

    tk.Label(win, text=f"MeetNote  v{VERSION}",
             font=("Segoe UI", 13, "bold"), bg="#111125", fg="#e2e0fc").pack(
        anchor="w", padx=20, pady=(16, 4))
    tk.Frame(win, bg="#333348", height=1).pack(fill="x", padx=16)

    frame = tk.Frame(win, bg="#1e1e32")
    frame.pack(fill="both", expand=True, padx=16, pady=(8, 0))

    sb = tk.Scrollbar(frame)
    sb.pack(side="right", fill="y")
    txt = tk.Text(frame, font=("Consolas", 9), bg="#1e1e32", fg="#e2e0fc",
                  insertbackground="#e2e0fc", relief="flat", bd=0,
                  yscrollcommand=sb.set, wrap="word")
    txt.pack(side="left", fill="both", expand=True, padx=8, pady=8)
    sb.config(command=txt.yview)
    txt.insert("1.0", RELEASE_NOTES)
    txt.config(state="disabled")

    tk.Button(win, text="Fermer", command=win.destroy,
              bg="#28283d", fg="#e2e0fc", font=("Segoe UI", 9),
              relief="flat", bd=0, pady=4, padx=16, cursor="hand2").pack(pady=10)

# ─────────────────────────────────────────────────────────────────────────────
# Agenda digest quotidien
# ─────────────────────────────────────────────────────────────────────────────

_DIGEST_HOUR = 10  # déclenchement à 10h00
_digest_en_cours = threading.Lock()  # un seul digest à la fois (sources parfois lentes)
_MSG_NOTION_A_CONFIGURER = "Configurez Notion d'abord (jeton et identifiant de base, dans les réglages)."


def _notion_configure() -> bool:
    """Jeton Notion et identifiant de base présents en mémoire (recalés par _recaler_reglages)."""
    return calendar_digest.notion_configure(config.NOTION_TOKEN, config.NOTION_DATABASE_ID)


def _run_agenda_digest_bg(force: bool = False):
    """Lance le digest en thread de fond, crée un Job visible dans l'historique."""
    def _task():
        try:
            _executer_digest(force)
        finally:
            _digest_en_cours.release()

    def _executer_digest(force: bool):
        now = datetime.datetime.now()
        # Vérification silencieuse avant de créer le job : ni job ni lecture des sources
        # si c'est déjà fait aujourd'hui ou pendant le délai de nouvel essai.
        if not force and not calendar_digest.digest_a_tenter(now):
            return

        job = _Job(
            id=now.strftime("%Y%m%d%H%M%S") + "_agenda",
            wav_path="",
            start_time=now,
            duration_min=0,
            model_name="",
            language="",
            meeting_type="Agenda",
            output_mode="notion",
            meeting_name=f"Agenda 15 j — {now.strftime('%d/%m/%Y')}",
            status_audio="done",
            status_transcript="done",
            status_notion="running",
        )
        with _all_jobs_lock:
            _all_jobs.insert(0, job)
        hist_mod.add(job)
        if _root:
            _root.after(0, _refresh_ui)

        try:
            url = calendar_digest.run_digest(days=15, force=force)
            if url:
                job.status_notion = "done"
                job.notion_url    = url
                _log_error("📅 Agenda 15 j envoyé dans Notion.")
            else:
                job.status_notion = "error"
                job.error_msg     = "Échec de l'envoi vers Notion (voir logs)"
                _log_error("📅 Agenda : échec de l'envoi vers Notion.")
        except calendar_digest.SourcesIndisponibles as e:
            job.status_notion = "error"
            job.error_msg     = ("Agenda injoignable (Outlook/EWS) — "
                                 f"nouvel essai à {e.prochain_essai:%H:%M}")
            _log_error(f"📅 {job.error_msg}")
        except Exception as e:
            job.status_notion = "error"
            job.error_msg     = str(e)
            _log_error(f"📅 Agenda digest erreur : {e}")

        hist_mod.update(job)
        if _root:
            _root.after(0, _refresh_ui)
            if _history_win and _history_win.winfo_exists():
                _root.after(0, _refresh_history_panel)

    # Notion non configuré (premier lancement, assistant ouvert) : ni job ni lecture des
    # sources, et rien n'est mémorisé — le digest partira dès que les réglages seront saisis.
    if not _notion_configure():
        return
    if not _digest_en_cours.acquire(blocking=False):
        if force:
            _log_error("📅 Agenda déjà en cours de génération.")
        return
    threading.Thread(target=_task, daemon=True).start()


def _trigger_agenda_digest_manual():
    """Déclenchement manuel depuis le menu tray (force=True)."""
    if not _notion_configure():
        import tkinter.messagebox as mb
        _log_error(f"📅 {_MSG_NOTION_A_CONFIGURER}")
        mb.showinfo("Agenda Notion", _MSG_NOTION_A_CONFIGURER)
        return
    _log_error("📅 Génération de l'agenda en cours…")
    _run_agenda_digest_bg(force=True)


def _schedule_daily_digest():
    """
    Planifie le digest quotidien à _DIGEST_HOUR:00.
    Logique de rattrapage :
      - Si pas encore fait aujourd'hui ET heure >= _DIGEST_HOUR → exécution immédiate.
      - Si pas encore fait aujourd'hui ET heure < _DIGEST_HOUR  → planifie pour _DIGEST_HOUR:00.
      - Si déjà fait aujourd'hui                                → planifie pour demain _DIGEST_HOUR:00.
      - Si les sources étaient injoignables il y a moins de DELAI_NOUVEL_ESSAI
        → rien n'est lancé, nouveau contrôle à la fin du délai.
    Se replanifie automatiquement après chaque exécution.
    """
    if not _root:
        return

    now   = datetime.datetime.now()
    today = now.date()

    reessai = calendar_digest.prochain_essai(now)
    if reessai and not calendar_digest.digest_already_run_today(today):
        delay_ms = max(1000, int((reessai - now).total_seconds() * 1000))
        _root.after(delay_ms, _schedule_daily_digest)
        return

    if calendar_digest.digest_a_tenter(now):
        if now.hour >= _DIGEST_HOUR:
            # Rattrapage : on est passé 10h sans avoir tourné
            _run_agenda_digest_bg(force=False)
            # Replanifier pour demain
            _root.after(60_000, _schedule_daily_digest)
            return
        else:
            # Planifier pour 10h aujourd'hui
            target    = now.replace(hour=_DIGEST_HOUR, minute=0, second=0, microsecond=0)
            delay_ms  = max(1000, int((target - now).total_seconds() * 1000))
            _root.after(delay_ms, _fire_digest_then_reschedule)
            return

    # Déjà fait aujourd'hui → prochain : demain à _DIGEST_HOUR:00
    tomorrow  = datetime.datetime.combine(today + datetime.timedelta(days=1),
                                          datetime.time(_DIGEST_HOUR, 0))
    delay_ms  = max(1000, int((tomorrow - now).total_seconds() * 1000))
    _root.after(delay_ms, _fire_digest_then_reschedule)


def _fire_digest_then_reschedule():
    """Déclenche le digest puis replanifie pour le lendemain."""
    _run_agenda_digest_bg(force=False)
    # Replanifier via _schedule_daily_digest (qui calculera le prochain délai)
    if _root:
        _root.after(60_000, _schedule_daily_digest)  # 1 min après pour laisser le temps à _save_last_run_date


def _shutdown_app(quit_for_good: bool):
    """Arrête proprement l'appli. Si quit_for_good, dépose un marqueur pour que
    le watchdog externe (watchdog.py) ne relance pas MeetNote après coup —
    sinon (ex: redémarrage suite à changement de périphérique audio) le
    process se termine normalement et le watchdog le relance tout seul."""
    _arreter_telechargement()
    if _recording:
        _do_cancel()
    else:
        _stop_loop.set()
    if quit_for_good:
        try:
            os.makedirs(os.path.dirname(_STOP_FLAG), exist_ok=True)
            open(_STOP_FLAG, "w").close()
        except Exception:
            pass
    def _shutdown():
        if _root:
            _root.destroy()
    if _root:
        _root.after(0, _shutdown)
    def _stop_icon():
        if _icon:
            _icon.stop()
    threading.Thread(target=_stop_icon, daemon=True).start()


def _quit_app(icon=None, item=None):
    _shutdown_app(quit_for_good=True)


# ─── Fenêtre historique (Toplevel indépendante) ───────────────────────────────
MAIN_W    = 420

_STATUS_ICON = {
    "done":    ("✓", "#00cc66"),
    "running": ("⏳", "#ffcc00"),
    "queued":  ("◷", "#aaaaaa"),
    "error":   ("✗", "#ff4444"),
    "pending": ("○", "#888888"),
    "skipped": ("—", "#888888"),
}

def _status_badge(status: str) -> tuple[str, str]:
    return _STATUS_ICON.get(status, ("?", "#888888"))


def _refresh_history_panel():
    """Reconstruit le contenu du panel historique (design Stitch)."""
    global _history_list_frame

    if not _history_list_frame:
        return
    if not (_history_win and _history_win.winfo_exists()):
        return

    # Palette design Stitch
    S_BASE  = "#111125"
    S_CARD  = "#1e1e32"
    S_HIGH  = "#28283d"
    S_TOP   = "#333348"
    FG      = "#e2e0fc"
    FG2     = "#e4beba"
    FG3     = "#ab8985"

    # Badge couleurs (pilules)
    BADGE_OK    = ("#003909", "#88d982")   # (bg, fg)
    BADGE_ERR   = ("#93000a", "#ffb4ab")
    BADGE_PROC  = ("#1a1a00", "#ffcc00")
    BADGE_PEND  = (S_HIGH,    FG3)
    BADGE_SKIP  = (S_HIGH,    FG3)

    def _badge_colors(status):
        return {
            "done":    BADGE_OK,
            "running": BADGE_PROC,
            "queued":  BADGE_PEND,
            "error":   BADGE_ERR,
            "pending": BADGE_PEND,
            "skipped": BADGE_SKIP,
        }.get(status, BADGE_PEND)

    def _badge_text(status):
        return {
            "done":    "✓",
            "running": "⏳",
            "queued":  "◷",
            "error":   "✗",
            "pending": "○",
            "skipped": "—",
        }.get(status, "?")

    # Vider le contenu actuel
    for w in _history_list_frame.winfo_children():
        w.destroy()

    with _all_jobs_lock:
        jobs = list(_all_jobs)

    if not jobs:
        tk.Label(_history_list_frame, text="No recordings yet",
                 font=("Segoe UI", 9), bg=S_BASE, fg=FG3).pack(pady=30)
        return

    for job in jobs:
        # Carte
        card = tk.Frame(_history_list_frame, bg=S_CARD, bd=0)
        card.pack(fill="x", padx=10, pady=(0, 8))

        # Hover effect
        def _on_enter(e, c=card): c.config(bg=S_HIGH)
        def _on_leave(e, c=card): c.config(bg=S_CARD)
        card.bind("<Enter>", _on_enter)
        card.bind("<Leave>", _on_leave)

        # ── Ligne haut : date + titre + durée
        top_row = tk.Frame(card, bg=S_CARD)
        top_row.pack(fill="x", padx=10, pady=(8, 4))

        dt_str = job.start_time.strftime("%Y.%b.%d | %H:%M").upper()
        dur    = f"{job.duration_min:.0f} min"

        tk.Label(top_row, text=dt_str, font=("Consolas", 7),
                 bg=S_CARD, fg=FG3, anchor="w").pack(side="left")

        # Badge durée (droite)
        dur_badge = tk.Label(top_row, text=dur, font=("Segoe UI", 7),
                             bg=S_HIGH, fg=FG3, padx=6, pady=1)
        dur_badge.pack(side="right")

        # Titre réunion (éditable inline)
        title_row = tk.Frame(card, bg=S_CARD)
        title_row.pack(fill="x", padx=10, pady=(0, 2))

        name_var = tk.StringVar(value=job.meeting_name or "")
        name_entry = tk.Entry(
            title_row, textvariable=name_var,
            font=("Segoe UI", 9, "bold"),
            bg=S_CARD, fg=FG, insertbackground=FG,
            relief="flat", bd=0, highlightthickness=1,
            highlightbackground=S_CARD, highlightcolor=S_TOP,
        )
        name_entry.pack(fill="x", expand=True)

        def _save_name(_j=job, _var=name_var):
            new_name = _var.get().strip()
            if new_name != _j.meeting_name:
                _j.meeting_name = new_name
                hist_mod.update(_j)

        name_entry.bind("<FocusOut>", lambda e, _j=job, _v=name_var: _save_name(_j, _v))
        name_entry.bind("<Return>",   lambda e, _j=job, _v=name_var, _entry=name_entry: (_save_name(_j, _v), _entry.master.focus_set()))

        # ── Ligne badges statuts
        badges_row = tk.Frame(card, bg=S_CARD)
        badges_row.pack(fill="x", padx=10, pady=(0, 6))

        for label, status in [("AUDIO", job.status_audio),
                               ("TRANSCRIPT", job.status_transcript),
                               ("NOTION", job.status_notion)]:
            b_bg, b_fg = _badge_colors(status)
            icon = _badge_text(status)
            pill = tk.Label(badges_row, text=f"{icon} {label}",
                            font=("Segoe UI", 7, "bold"),
                            bg=b_bg, fg=b_fg, padx=6, pady=2, relief="flat")
            pill.pack(side="left", padx=(0, 4))

        # ── Participants : invités de l'agenda + Teams (si disponibles)
        participants = _participants_job(job)
        if participants:
            teams_row = tk.Frame(card, bg=S_CARD)
            teams_row.pack(fill="x", padx=10, pady=(0, 4))
            names_str = "  ·  ".join(participants)
            tk.Label(teams_row, text=f"👥 {names_str}",
                     font=("Segoe UI", 7), bg=S_CARD, fg="#9ab4e8",
                     anchor="w", wraplength=420, justify="left").pack(fill="x")

        # ── Boutons d'action contextuels
        j = job
        can_transcribe    = os.path.isfile(j.wav_path)
        can_play_audio    = can_transcribe
        can_push_notion   = j.status_transcript == "done" and bool(j.transcript)
        has_btn = (
            can_transcribe or can_push_notion or
            (j.transcript_path and os.path.isfile(j.transcript_path)) or
            bool(j.notion_url)
        )

        if has_btn:
            btn_row = tk.Frame(card, bg=S_CARD)
            btn_row.pack(fill="x", padx=10, pady=(0, 8))

            def _ghost_btn(parent, text, fg_c, cmd):
                b = tk.Button(parent, text=text, font=("Segoe UI", 7, "bold"),
                              bg=S_HIGH, fg=fg_c,
                              activebackground=S_TOP, activeforeground=fg_c,
                              relief="flat", bd=0, padx=8, pady=3,
                              cursor="hand2", command=cmd)
                b.pack(side="left", padx=(0, 4))

            # Lecture audio
            if can_play_audio:
                is_playing = (_playback_job_id == j.id)
                if is_playing:
                    _ghost_btn(btn_row, "⏹ Stop", "#ffb4ab",
                               lambda: _stop_audio() or _refresh_history_panel())
                else:
                    _ghost_btn(btn_row, "▶ Play", "#a8d8a8",
                               lambda _j=j: (_play_audio(_j.id, _j.wav_path), _refresh_history_panel()))

            # Transcription : Stop si en cours, sinon combo modèle + bouton
            if can_transcribe:
                is_transcribing = (
                    j.status_transcript == "running"
                    and _current_job is not None
                    and _current_job.id == j.id
                )
                if is_transcribing:
                    def _do_stop_transcription():
                        _worker_cancel_event.set()
                    _ghost_btn(btn_row, "⏹ Stop transcript", "#ffb4ab", _do_stop_transcription)
                else:
                    model_var_h = tk.StringVar(value=j.model_name)
                    model_combo_h = ttk.Combobox(btn_row, textvariable=model_var_h,
                                                 state="readonly", width=9,
                                                 values=list(modeles.MODELES),
                                                 font=("Segoe UI", 7))
                    model_combo_h.pack(side="left", padx=(0, 4))

                    def _do_retranscribe(_j=j, _mv=model_var_h):
                        _j.model_name = _mv.get()
                        _requeue_transcribe(_j)
                        _refresh_history_panel()

                    lbl = "📝 Transcribe" if j.status_transcript in ("queued", "error") else "🔄 Re-transcribe"
                    _ghost_btn(btn_row, lbl, "#ffb3ac", _do_retranscribe)

            # Notion : toujours proposer si transcript disponible
            if can_push_notion:
                notion_lbl = "→ Notion" if j.status_notion in ("pending", "error") else "↺ Re-push Notion"
                notion_fg  = "#88d982" if j.status_notion in ("pending", "error") else "#9ab4e8"
                def _do_force_notion(_j=j):
                    _j.status_notion = "pending"
                    _j.output_mode   = "notion"
                    hist_mod.update(_j)
                    _requeue_notion(_j)
                    _refresh_history_panel()
                _ghost_btn(btn_row, notion_lbl, notion_fg,
                           lambda _j=j: _do_force_notion(_j))

            if j.transcript_path and os.path.isfile(j.transcript_path):
                _ghost_btn(btn_row, "📄 View txt", FG2,
                           lambda p=j.transcript_path: subprocess.Popen(f'notepad "{p}"'))

            if j.notion_url:
                _ghost_btn(btn_row, "🌐 Open Notion", "#e4b5ff",
                           lambda u=j.notion_url: os.startfile(u))

            # Bouton Teams transcript (toujours disponible si transcription faite)
            def _do_add_teams(_j=j):
                txt = _open_teams_transcript_dialog()
                if not txt:
                    return
                _j.teams_transcript = txt
                base = _j.whisper_transcript or _j.transcript
                _j.transcript = _combine_transcripts(base, txt)
                if not _j.whisper_transcript:
                    _j.whisper_transcript = base
                _save_transcript_local(_j)
                hist_mod.update(_j)
                if _j.notion_url:
                    threading.Thread(
                        target=_append_teams_to_notion, args=(_j,), daemon=True
                    ).start()
                _refresh_history_panel()
            teams_lbl = "📋 Teams ✓" if j.teams_transcript else "📋 Teams"
            teams_fg  = "#88d982" if j.teams_transcript else "#ffcc66"
            _ghost_btn(btn_row, teams_lbl, teams_fg, _do_add_teams)
        else:
            tk.Frame(card, bg=S_CARD, height=2).pack()

        # Message d'erreur
        if j.error_msg:
            err_row = tk.Frame(card, bg=S_CARD)
            err_row.pack(fill="x", padx=10, pady=(0, 6))
            tk.Label(err_row, text=f"⚠  {j.error_msg[:90]}",
                     font=("Consolas", 7), bg=S_CARD, fg="#ffb4ab",
                     anchor="w", wraplength=400, justify="left").pack(fill="x")

        # Bouton supprimer (coin bas-droit)
        def _delete_job(_j=j):
            with _all_jobs_lock:
                _all_jobs[:] = [x for x in _all_jobs if x.id != _j.id]
            entries = hist_mod.load()
            entries = [e for e in entries if e.get("id") != _j.id]
            hist_mod.save(entries)
            _refresh_history_panel()

        del_row = tk.Frame(card, bg=S_CARD)
        del_row.pack(fill="x", padx=10, pady=(0, 6))
        tk.Button(del_row, text="🗑 Delete", font=("Segoe UI", 7),
                  bg=S_CARD, fg=FG3,
                  activebackground="#3a1010", activeforeground="#ff6666",
                  relief="flat", bd=0, padx=6, pady=2, cursor="hand2",
                  command=_delete_job).pack(side="right")

    # Footer
    with _all_jobs_lock:
        count = len(_all_jobs)
    footer = tk.Frame(_history_list_frame, bg=S_BASE)
    footer.pack(fill="x", pady=(4, 8))
    tk.Label(footer, text=f"{count} session(s) total",
             font=("Segoe UI", 7), bg=S_BASE, fg=FG3).pack(pady=4)


def _toggle_history_window():
    """Ouvre ou ferme la fenêtre historique (design Stitch)."""
    global _history_win, _history_list_frame

    # Si déjà ouverte et visible → cacher
    if _history_win and _history_win.winfo_exists():
        if _history_win.state() == "withdrawn":
            _history_win.deiconify()
            _history_win.lift()
            _history_win.focus_force()
        else:
            _history_win.withdraw()
        return

    if not _root:
        return

    S_BASE  = "#111125"
    S_MAIN  = "#16213e"
    S_CARD  = "#1e1e32"
    FG      = "#e2e0fc"
    FG3     = "#ab8985"
    RED     = "#dc3232"

    _history_win = tk.Toplevel(_root)
    _history_win.title("MeetNote — Recording History")
    _history_win.configure(bg=S_BASE)
    _history_win.resizable(True, True)
    _history_win.transient(_root)   # lien parent-enfant : pas d'entrée séparée dans la barre des tâches
    # Fermer = cacher (pas détruire), synchronisé avec la fenêtre principale
    _history_win.protocol("WM_DELETE_WINDOW", _close_history_window)

    # Réduction de history → iconifier la principale (les deux se réduisent ensemble)
    def _on_history_unmap(e):
        if e.widget is not _history_win:
            return
        def _check():
            if _history_win.winfo_exists() and _history_win.state() == "iconic":
                _history_win.withdraw()
                _root.iconify()
        _history_win.after(50, _check)
    _history_win.bind("<Unmap>", _on_history_unmap)

    _root.update_idletasks()
    rx = _root.winfo_x() + _root.winfo_width() + 8
    ry = _root.winfo_y()
    _history_win.geometry(f"480x650+{rx}+{ry}")

    # ── Header
    hdr = tk.Frame(_history_win, bg=S_MAIN)
    hdr.pack(fill="x")

    tk.Label(hdr, text="MeetNote", font=("Segoe UI", 9, "bold"),
             bg=S_MAIN, fg=RED).pack(side="left", padx=14, pady=(10, 2))

    hdr2 = tk.Frame(_history_win, bg=S_MAIN)
    hdr2.pack(fill="x")
    tk.Label(hdr2, text="Recording History", font=("Segoe UI", 12, "bold"),
             bg=S_MAIN, fg=FG).pack(side="left", padx=14, pady=(0, 4))

    # ── Ligne 1 de boutons : actions import
    btn_row1 = tk.Frame(_history_win, bg=S_MAIN)
    btn_row1.pack(fill="x", padx=10, pady=(0, 2))
    tk.Button(btn_row1, text="🗑 Supprimer tout", font=("Segoe UI", 8),
              bg="#3a1010", fg="#ffb4ab", activebackground="#551515",
              activeforeground="white", relief="flat", bd=0, cursor="hand2",
              command=_delete_all).pack(side="left", padx=(0, 4))
    tk.Button(btn_row1, text="📝 Transcrire tout", font=("Segoe UI", 8),
              bg="#1a3a5c", fg="#88ccff", activebackground="#22456e",
              activeforeground="white", relief="flat", bd=0, cursor="hand2",
              command=_bulk_transcribe_all).pack(side="left", padx=(0, 4))
    tk.Button(btn_row1, text="📋 Import transcript", font=("Segoe UI", 8),
              bg="#1a4a1a", fg="#88ee88", activebackground="#225522",
              activeforeground="white", relief="flat", bd=0, cursor="hand2",
              command=_import_transcript).pack(side="left", padx=(0, 4))

    # ── Ligne 2 de boutons : import fichiers + mails + refresh
    btn_row2 = tk.Frame(_history_win, bg=S_MAIN)
    btn_row2.pack(fill="x", padx=10, pady=(0, 8))
    tk.Button(btn_row2, text="⊕ Import audio", font=("Segoe UI", 8),
              bg=RED, fg="white", activebackground="#ff4444",
              activeforeground="white", relief="flat", bd=0, cursor="hand2",
              command=_import_audio_file).pack(side="left", padx=(0, 4))
    tk.Button(btn_row2, text="📧 Import mails", font=("Segoe UI", 8),
              bg="#1a3a5c", fg="#88ccff", activebackground="#22456e",
              activeforeground="white", relief="flat", bd=0, cursor="hand2",
              command=_import_outlook_mails).pack(side="left", padx=(0, 4))
    tk.Button(btn_row2, text="↺ Refresh", font=("Segoe UI", 8),
              bg=S_MAIN, fg=FG3, activebackground=S_CARD,
              activeforeground=FG, relief="flat", bd=0, cursor="hand2",
              command=_refresh_history_panel).pack(side="left", padx=(0, 4))

    # ── Zone scrollable
    canvas = tk.Canvas(_history_win, bg=S_BASE, highlightthickness=0)
    scrollbar = tk.Scrollbar(_history_win, orient="vertical", command=canvas.yview,
                              bg=S_BASE, troughcolor=S_BASE)
    canvas.configure(yscrollcommand=scrollbar.set)
    scrollbar.pack(side="right", fill="y")
    canvas.pack(side="left", fill="both", expand=True)

    _history_list_frame = tk.Frame(canvas, bg=S_BASE)
    cw = canvas.create_window((0, 0), window=_history_list_frame, anchor="nw")

    def _on_frame_configure(e):
        canvas.configure(scrollregion=canvas.bbox("all"))
    _history_list_frame.bind("<Configure>", _on_frame_configure)

    def _on_canvas_configure(e):
        canvas.itemconfig(cw, width=e.width)
    canvas.bind("<Configure>", _on_canvas_configure)

    def _on_mousewheel(e):
        canvas.yview_scroll(int(-1 * (e.delta / 120)), "units")
    canvas.bind("<MouseWheel>", _on_mousewheel)

    _refresh_history_panel()


# ─── Tkinter window ───────────────────────────────────────────────────────────
def _build_window():
    global _root, _status_var, _progress_var, _level_var, _source_var, _speaker_var
    global _btn_start, _btn_stop, _btn_stop_only, _btn_cancel, _source_combo, _log_text
    global _speaker_frame, _speaker_combo, _model_var, _lang_var, _output_var
    global _choix_modele_courant
    global _meeting_name_var, _history_list_frame, _status_dot, _dl_label

    _root = tk.Tk()

    # Capture les exceptions levées dans les callbacks Tkinter (clics de boutons,
    # .after(), etc.). Par défaut Tkinter les imprime sur stderr — donc invisibles
    # quand l'app est lancée sans console. On les loggue et on les affiche.
    def _tk_report_exception(exc, val, tb):
        msg = "".join(traceback.format_exception(exc, val, tb))
        try:
            _logger.critical("Exception dans un callback Tkinter :\n%s", msg)
        except Exception:
            pass
        try:
            _log_error(f"⚠ Erreur interne : {val}")
        except Exception:
            pass
    _root.report_callback_exception = _tk_report_exception

    _root.title("MeetNote")
    _root.resizable(True, True)
    _root.minsize(720, 560)
    _root.configure(bg="#111125")
    _root.protocol("WM_DELETE_WINDOW", _hide_window)

    _history_was_visible = [False]  # list pour mutation dans closures

    def _on_root_unmap(e):
        if e.widget is not _root:
            return
        def _check_iconic():
            if _root.state() == "iconic":
                if _history_win and _history_win.winfo_exists():
                    _history_was_visible[0] = _history_win.state() != "withdrawn"
                    if _history_was_visible[0]:
                        _history_win.withdraw()
        _root.after(50, _check_iconic)

    def _on_root_map(e):
        if e.widget is not _root:
            return
        if _root.state() == "normal" and _history_was_visible[0]:
            if _history_win and _history_win.winfo_exists():
                _history_win.deiconify()
                _history_win.lift()
            _history_was_visible[0] = False

    _root.bind("<Unmap>", _on_root_unmap)
    _root.bind("<Map>",   _on_root_map)

    # Déplacement solidaire : history suit la principale
    def _on_root_configure(e):
        if e.widget is not _root:
            return
        if _history_win and _history_win.winfo_exists() and _history_win.state() != "withdrawn":
            new_x = _root.winfo_x() + _root.winfo_width() + 8
            new_y = _root.winfo_y()
            _history_win.geometry(f"+{new_x}+{new_y}")
    _root.bind("<Configure>", _on_root_configure)

    _output_var       = tk.StringVar(value="notion")
    _meeting_name_var = tk.StringVar(value="")

    # ── Palette design Stitch "Digital Control Room"
    S_BASE  = "#111125"   # fond général
    S_MAIN  = "#16213e"   # nav + panels
    S_CARD  = "#1e1e32"   # cartes / sections
    S_HIGH  = "#28283d"   # éléments actifs / hover
    S_TOP   = "#333348"   # focus
    FG      = "#e2e0fc"   # texte principal
    FG2     = "#e4beba"   # texte secondaire
    FG3     = "#ab8985"   # hints / labels
    RED     = "#dc3232"   # action critique
    RED_DIM = "#b81c1c"   # rouge foncé (bouton start)

    # ════════════════════════════════════════════════════════════
    # LAYOUT RACINE : nav gauche | contenu (header + body 2-col)
    # ════════════════════════════════════════════════════════════
    NAV_W    = 160
    LEFT_W   = 300
    RIGHT_W  = 260
    TOTAL_W  = NAV_W + LEFT_W + RIGHT_W

    _root.configure(bg=S_BASE)

    # ── ttk styles
    style = ttk.Style()
    style.theme_use("default")
    style.configure("VU.Horizontal.TProgressbar",
                    troughcolor=S_HIGH, background="#00cc66", thickness=8, borderwidth=0)
    style.configure("Prog.Horizontal.TProgressbar",
                    troughcolor=S_HIGH, background=RED, thickness=8, borderwidth=0)
    style.configure("TCombobox",
                    fieldbackground=S_HIGH, background=S_HIGH,
                    foreground=FG, selectbackground=S_TOP, selectforeground=FG,
                    arrowcolor=FG3)
    style.map("TCombobox",
              fieldbackground=[("readonly", S_HIGH)],
              foreground=[("readonly", FG)])

    # ─────────────────────────────────────────
    # NAVIGATION GAUCHE
    # ─────────────────────────────────────────
    nav_frame = tk.Frame(_root, bg=S_MAIN, width=NAV_W)
    nav_frame.pack(side="left", fill="y")
    nav_frame.pack_propagate(False)

    # Logo
    tk.Label(nav_frame, text="MeetNote", font=("Segoe UI", 11, "bold"),
             bg=S_MAIN, fg=RED).pack(anchor="w", padx=16, pady=(18, 0))
    tk.Label(nav_frame, text="DIGITAL CONTROL ROOM", font=("Segoe UI", 6),
             bg=S_MAIN, fg=FG3).pack(anchor="w", padx=16, pady=(0, 14))
    tk.Frame(nav_frame, bg=S_HIGH, height=1).pack(fill="x", padx=0)

    def _nav_item(label, icon, cmd, active=False):
        color = RED if active else FG3
        bg_c  = S_CARD if active else S_MAIN
        row = tk.Frame(nav_frame, bg=bg_c, cursor="hand2")
        row.pack(fill="x")
        if active:
            tk.Frame(row, bg=RED, width=3).pack(side="left", fill="y")
        else:
            tk.Frame(row, bg=S_MAIN, width=3).pack(side="left", fill="y")
        tk.Label(row, text=f"{icon}  {label}", font=("Segoe UI", 9),
                 bg=bg_c, fg=color, anchor="w", pady=10, padx=12).pack(
            side="left", fill="x", expand=True)
        row.bind("<Button-1>", lambda e: cmd())
        for w in row.winfo_children():
            w.bind("<Button-1>", lambda e: cmd())
        return row

    def _open_audio_dir():
        os.makedirs(config.AUDIO_ARCHIVE_DIR, exist_ok=True)
        subprocess.Popen(f'explorer "{config.AUDIO_ARCHIVE_DIR}"')

    def _open_transcript_dir():
        os.makedirs(config.TRANSCRIPT_DIR, exist_ok=True)
        subprocess.Popen(f'explorer "{config.TRANSCRIPT_DIR}"')

    tk.Frame(nav_frame, bg=S_MAIN, height=6).pack()
    _nav_item("Settings",    "⚙", _open_settings)
    _nav_item("Audio",       "🎙", _open_audio_dir,        active=True)
    _nav_item("Transcripts", "📄", _open_transcript_dir)
    _nav_item("History",     "📋", _toggle_history_window)

    # Version + Quit en bas
    tk.Frame(nav_frame, bg=S_MAIN).pack(fill="both", expand=True)
    _nav_item(f"v{VERSION}", "ℹ", _open_version_window)
    tk.Frame(nav_frame, bg=S_HIGH, height=1).pack(fill="x")
    quit_row = tk.Frame(nav_frame, bg=S_MAIN, cursor="hand2")
    quit_row.pack(fill="x")
    tk.Label(quit_row, text="✕  Quit", font=("Segoe UI", 9),
             bg=S_MAIN, fg="#cc4444", anchor="w", pady=10, padx=15).pack(fill="x")
    quit_row.bind("<Button-1>", lambda e: _quit_app())
    for w in quit_row.winfo_children():
        w.bind("<Button-1>", lambda e: _quit_app())

    # ─────────────────────────────────────────
    # ZONE CONTENU (droite de la nav)
    # ─────────────────────────────────────────
    content_frame = tk.Frame(_root, bg=S_BASE)
    content_frame.pack(side="left", fill="both", expand=True)

    # ── Header
    header = tk.Frame(content_frame, bg=S_MAIN)
    header.pack(fill="x")

    tk.Label(header, text="MeetNote", font=("Segoe UI", 13, "bold"),
             bg=S_MAIN, fg=FG).pack(side="left", padx=16, pady=(10, 2))

    hdr_btns = tk.Frame(header, bg=S_MAIN)
    hdr_btns.pack(side="right", padx=12, pady=8)
    tk.Button(hdr_btns, text="⚙", font=("Segoe UI", 10),
              bg=S_MAIN, fg=FG3, activebackground=S_CARD, activeforeground=FG,
              relief="flat", bd=0, cursor="hand2",
              command=_open_settings).pack(side="left", padx=4)
    tk.Button(hdr_btns, text="✕", font=("Segoe UI", 10),
              bg=S_MAIN, fg="#cc4444", activebackground=S_CARD, activeforeground="#ff6666",
              relief="flat", bd=0, cursor="hand2",
              command=_quit_app).pack(side="left", padx=4)

    hdr2 = tk.Frame(content_frame, bg=S_MAIN)
    hdr2.pack(fill="x")
    tk.Label(hdr2, text="Recording  →  Whisper  →  Notion",
             font=("Segoe UI", 8), bg=S_MAIN, fg=FG3).pack(
        side="left", padx=16, pady=(0, 10))

    # ── Body (2 colonnes)
    body = tk.Frame(content_frame, bg=S_BASE)
    body.pack(fill="both", expand=True)

    left_col  = tk.Frame(body, bg=S_BASE, width=LEFT_W)
    left_col.pack(side="left", fill="both", padx=(10, 4), pady=10)
    left_col.pack_propagate(False)

    right_col = tk.Frame(body, bg=S_BASE)
    right_col.pack(side="left", fill="both", expand=True, padx=(4, 10), pady=10)

    # ─────────────────────────────────────────
    # HELPERS SECTIONS (colonne gauche)
    # ─────────────────────────────────────────
    def _section(icon_title):
        outer = tk.Frame(left_col, bg=S_BASE)
        outer.pack(fill="x", pady=(0, 8))
        tk.Label(outer, text=icon_title, font=("Segoe UI", 7, "bold"),
                 bg=S_BASE, fg=FG3).pack(anchor="w", pady=(0, 4))
        inner = tk.Frame(outer, bg=S_CARD)
        inner.pack(fill="x")
        return inner

    def _row(parent, cols=1):
        f = tk.Frame(parent, bg=S_CARD)
        f.pack(fill="x", padx=10, pady=(8, 0))
        if cols == 1:
            return f
        frames = []
        for i in range(cols):
            cf = tk.Frame(f, bg=S_CARD)
            cf.pack(side="left", fill="x", expand=True,
                    padx=(0, 8) if i < cols - 1 else 0)
            frames.append(cf)
        return frames

    def _lbl(parent, text):
        tk.Label(parent, text=text, font=("Segoe UI", 7),
                 bg=S_CARD, fg=FG3, anchor="w").pack(fill="x")

    def _hint(parent, var):
        tk.Label(parent, textvariable=var, font=("Segoe UI", 7),
                 bg=S_CARD, fg=FG3, anchor="w",
                 wraplength=LEFT_W - 30, justify="left").pack(fill="x", pady=(1, 0))

    def _combo(parent, var, values):
        c = ttk.Combobox(parent, textvariable=var, state="readonly",
                         font=("Segoe UI", 9), values=values)
        c.pack(fill="x", pady=(2, 0))
        return c

    def _spacer(parent, h=8):
        tk.Frame(parent, bg=S_CARD, height=h).pack()

    # ════════════════════════
    # SECTION : SOURCE AUDIO
    # ════════════════════════
    sec_src = _section("🎙  AUDIO SOURCE")

    r = _row(sec_src)
    _lbl(r, "Select the primary input device for the recording stream.")
    _source_var = tk.StringVar(value="🎤🔊  Mixte (micro + PC)")
    _source_combo = _combo(r, _source_var, list(_SOURCE_MAP.keys()))

    _SOURCE_HINTS = {
        "🎤  Microphone uniquement": "Mic only — remote participants not captured.",
        "🔊  Loopback (son du PC)":  "PC audio only — your voice not captured.",
        "🎤🔊  Mixte (micro + PC)":  "Mic + PC audio — all participants captured.",
    }
    _hint_var = tk.StringVar(value=_SOURCE_HINTS["🎤🔊  Mixte (micro + PC)"])
    _hint(r, _hint_var)
    _source_var.trace_add("write", lambda *_: _hint_var.set(_SOURCE_HINTS.get(_source_var.get(), "")))

    _speaker_frame = tk.Frame(sec_src, bg=S_CARD)
    rs = tk.Frame(_speaker_frame, bg=S_CARD)
    rs.pack(fill="x", padx=10, pady=(6, 0))
    _lbl(rs, "Speaker Output (Optional)")
    _AUTO_SPEAKER = "— Sortie par défaut Windows —"
    speakers     = [_AUTO_SPEAKER] + [s.name for s in sc.all_speakers()]
    _speaker_var = tk.StringVar(value=_AUTO_SPEAKER)
    _speaker_combo = _combo(rs, _speaker_var, speakers)
    _speaker_frame.pack_forget()

    def _on_source_change(*_):
        src = _SOURCE_MAP.get(_source_var.get(), "micro")
        if src in ("loopback", "mixte"):
            _speaker_frame.pack(fill="x")
        else:
            _speaker_frame.pack_forget()
        _root.update_idletasks()
    _source_var.trace_add("write", _on_source_change)
    _spacer(sec_src)

    # ════════════════════════
    # SECTION : MEETING
    # ════════════════════════
    sec_mtg = _section("🗓  MEETING")

    rm = _row(sec_mtg)
    mtg_hdr = tk.Frame(rm, bg=S_MAIN)
    mtg_hdr.pack(fill="x")
    _lbl(mtg_hdr, "Meeting name (auto-filled from Outlook calendar)")
    tk.Button(mtg_hdr, text="🔄", font=("Segoe UI", 9),
              bg=S_MAIN, fg=FG3, activebackground=S_HIGH, activeforeground=FG,
              relief="flat", bd=0, cursor="hand2",
              command=_forcer_agenda).pack(side="right", pady=(2, 0))
    mtg_entry = tk.Entry(rm, textvariable=_meeting_name_var,
                         font=("Segoe UI", 9), bg=S_HIGH, fg=FG,
                         insertbackground=FG, relief="flat", bd=0,
                         highlightthickness=1, highlightbackground=S_TOP,
                         highlightcolor=RED)
    mtg_entry.pack(fill="x", pady=(2, 0), ipady=5)

    rt = _row(sec_mtg)
    _lbl(rt, "Meeting type")
    _type_var = tk.StringVar(value="A classifier")
    type_combo = _combo(rt, _type_var,
                        ["A classifier", "Gouvernance", "Technique", "Projet", "RH", "Fournisseur", "Autre"])
    type_combo.current(0)
    _spacer(sec_mtg)

    # ════════════════════════
    # SECTION : TRANSCRIPTION
    # ════════════════════════
    sec_w = _section("📝  TRANSCRIPTION")

    mc, lc = _row(sec_w, cols=2)

    _lbl(mc, "Whisper Model")
    modele_initial = modeles.modele_affiche(
        user_config.get(modeles.CLE_MODELE_CHOISI, ""), config.WHISPER_MODEL)
    _model_var = tk.StringVar(value=modele_initial)
    _choix_modele_courant = modele_initial
    model_combo = _combo(mc, _model_var, list(modeles.MODELES))
    _MODEL_HINTS = {
        "tiny":    "Fastest — lower accuracy",
        "base":    "Fast — decent accuracy",
        "small":   "Balanced",
        "medium":  "High quality — slower",
        "large-v3":"Best quality — needs GPU",
    }
    _model_hint_var = tk.StringVar(value=_MODEL_HINTS.get(modele_initial, ""))
    _hint(mc, _model_hint_var)
    _model_var.trace_add("write", lambda *_: _model_hint_var.set(_MODEL_HINTS.get(_model_var.get(), "")))
    _model_var.trace_add("write", _sur_choix_modele)

    _lbl(lc, "Language")
    _lang_var = tk.StringVar(value="auto")
    lang_combo = _combo(lc, _lang_var, ["auto", "fr", "en", "es", "de", "it", "pt", "nl", "pl", "ja", "zh"])
    _LANG_HINTS = {"auto": "Auto-detect Language", "fr": "Transcribed in French", "en": "Translated to English"}
    _lang_hint_var = tk.StringVar(value="Auto-detect Language")
    _hint(lc, _lang_hint_var)
    _lang_var.trace_add("write", lambda *_: _lang_hint_var.set(
        _LANG_HINTS.get(_lang_var.get(), "Translated to English if needed")))
    _spacer(sec_w)

    def _toggle_whisper_controls(enabled: bool):
        state = "readonly" if enabled else "disabled"
        for c in (model_combo, lang_combo, type_combo):
            c.config(state=state)
        mtg_entry.config(state="normal" if enabled else "disabled")
    _root._toggle_whisper = _toggle_whisper_controls

    # ════════════════════════
    # SECTION : LEVELS
    # ════════════════════════
    sec_lev = _section("📊  LEVELS")

    rl = _row(sec_lev)
    tk.Label(rl, text="MIC LEVEL", font=("Segoe UI", 7),
             bg=S_CARD, fg=FG3, width=12, anchor="w").pack(side="left")
    _level_var = tk.DoubleVar(value=0)
    ttk.Progressbar(rl, variable=_level_var, maximum=100,
                    style="VU.Horizontal.TProgressbar").pack(side="left", fill="x", expand=True)
    _level_label = tk.Label(rl, text="-∞dB", font=("Consolas", 7),
                             bg=S_CARD, fg=FG3, width=7, anchor="e")
    _level_label.pack(side="left")

    def _vu_decay():
        if not _recording and _level_var.get() > 0:
            v = max(0, _level_var.get() - 3)
            _level_var.set(v)
        if _level_var.get() > 0:
            db = max(-60, 20 * __import__("math").log10(_level_var.get() / 100 + 1e-9))
            _level_label.config(text=f"{db:.0f}dB")
        else:
            _level_label.config(text="-∞dB")
        _root.after(50, _vu_decay)
    _root.after(100, _vu_decay)

    rp = _row(sec_lev)
    tk.Label(rp, text="TRANSCRIPTION", font=("Segoe UI", 7),
             bg=S_CARD, fg=FG3, width=12, anchor="w").pack(side="left")
    _progress_var = tk.DoubleVar(value=0)
    ttk.Progressbar(rp, variable=_progress_var, maximum=100,
                    style="Prog.Horizontal.TProgressbar").pack(side="left", fill="x", expand=True)
    _prog_label = tk.Label(rp, text="0%", font=("Consolas", 7),
                            bg=S_CARD, fg=FG3, width=4, anchor="e")
    _prog_label.pack(side="left")
    _progress_var.trace_add("write", lambda *_: _prog_label.config(
        text=f"{int(_progress_var.get())}%"))

    # ── Gain enregistrement (numérique, toutes sources)
    rg = _row(sec_lev)
    tk.Label(rg, text="REC GAIN", font=("Segoe UI", 7),
             bg=S_CARD, fg=FG3, width=12, anchor="w").pack(side="left")

    _gain_label = tk.Label(rg, text="4.0x", font=("Consolas", 7),
                           bg=S_CARD, fg=FG3, width=5, anchor="e")
    _gain_label.pack(side="right")

    def _on_gain_change(val):
        global _rec_gain
        _rec_gain = round(float(val), 2)
        _gain_label.config(text=f"{_rec_gain:.1f}x")

    gain_slider = tk.Scale(rg, from_=0.5, to=4.0, resolution=0.1, orient="horizontal",
                           command=_on_gain_change,
                           bg="#dc3232", fg=FG, troughcolor=S_HIGH,
                           activebackground="#ff4444", highlightthickness=0,
                           sliderrelief="raised", bd=1, showvalue=False)
    gain_slider.set(4.0)
    gain_slider.pack(side="left", fill="x", expand=True, padx=(4, 4))

    # ── Volume Windows (pycaw)
    rv = _row(sec_lev)
    tk.Label(rv, text="WIN VOLUME", font=("Segoe UI", 7),
             bg=S_CARD, fg=FG3, width=12, anchor="w").pack(side="left")

    _vol_label = tk.Label(rv, text="---%", font=("Consolas", 7),
                          bg=S_CARD, fg=FG3, width=5, anchor="e")
    _vol_label.pack(side="right")

    _vol_var = tk.IntVar(value=100)

    def _get_pycaw_volume():
        try:
            from pycaw.pycaw import AudioUtilities
            device = AudioUtilities.GetSpeakers()
            return device.EndpointVolume
        except Exception:
            return None

    _pycaw_vol = _get_pycaw_volume()

    def _init_vol_slider():
        if _pycaw_vol:
            try:
                current = int(_pycaw_vol.GetMasterVolumeLevelScalar() * 100)
                _vol_var.set(current)
                _vol_label.config(text=f"{current}%")
            except Exception:
                pass

    def _on_vol_change(val):
        pct = int(float(val))
        _vol_label.config(text=f"{pct}%")
        if _pycaw_vol:
            try:
                _pycaw_vol.SetMasterVolumeLevelScalar(pct / 100.0, None)
            except Exception:
                pass

    vol_slider = tk.Scale(rv, from_=0, to=100, orient="horizontal",
                          variable=_vol_var, command=_on_vol_change,
                          bg="#dc3232", fg=FG, troughcolor=S_HIGH,
                          activebackground="#ff4444", highlightthickness=0,
                          sliderrelief="raised", bd=1, showvalue=False,
                          length=140)
    vol_slider.pack(side="left", fill="x", expand=True, padx=(4, 4))

    if _pycaw_vol is None:
        vol_slider.config(state="disabled")
        _vol_label.config(text="N/A")
    else:
        _root.after(200, _init_vol_slider)

    _spacer(sec_lev)

    # ─────────────────────────────────────────
    # COLONNE DROITE — actions + status + console
    # ─────────────────────────────────────────

    # ── Boutons d'action
    btns_frame = tk.Frame(right_col, bg=S_BASE)
    btns_frame.pack(fill="x", pady=(0, 8))

    def _toggle_recording():
        if _recording:
            _do_stop_archive_only()
        else:
            _do_start()

    _btn_start = tk.Button(
        btns_frame, text="⏺  START RECORDING",
        font=("Segoe UI", 11, "bold"),
        bg=RED_DIM, fg="white",
        activebackground=RED, activeforeground="white",
        relief="flat", bd=0, pady=12, cursor="hand2",
        command=_toggle_recording,
    )
    _btn_start.pack(fill="x", pady=(0, 4))

    _btn_stop        = None
    _btn_stop_only   = None
    _btn_cancel      = None

    # ── Indicateur de statut
    status_frame = tk.Frame(right_col, bg=S_CARD)
    status_frame.pack(fill="x", pady=(0, 8))

    dot_row = tk.Frame(status_frame, bg=S_CARD)
    dot_row.pack(fill="x", padx=10, pady=8)

    _status_dot = tk.Label(dot_row, text="  ", bg="#555566",
                            width=2, relief="flat")
    _status_dot.pack(side="left", padx=(0, 8), ipadx=3, ipady=3)

    _status_var = tk.StringVar(value="Idle: Start to record")
    tk.Label(dot_row, textvariable=_status_var, font=("Segoe UI", 8),
             bg=S_CARD, fg=FG2, anchor="w",
             wraplength=RIGHT_W - 50, justify="left").pack(side="left", fill="x", expand=True)

    # ── Téléchargement du modèle Whisper (masqué tant qu'il n'y en a pas)
    _dl_label = tk.Label(status_frame, text="", font=("Segoe UI", 8),
                         bg=S_CARD, fg="#ffcc00", anchor="w",
                         wraplength=RIGHT_W - 30, justify="left")
    _root.after(_DL_ANIMATION_MS, _animer_telechargement)

    # ── System Console (log)
    console_hdr = tk.Frame(right_col, bg=S_BASE)
    console_hdr.pack(fill="x", pady=(0, 2))
    tk.Label(console_hdr, text="▶  SYSTEM CONSOLE", font=("Segoe UI", 7, "bold"),
             bg=S_BASE, fg=FG3).pack(side="left")

    def _clear_log():
        if _log_text:
            _log_text.config(state="normal")
            _log_text.delete("1.0", "end")
            _log_text.config(state="disabled")

    tk.Button(console_hdr, text="🗑 Clear", font=("Segoe UI", 7),
              bg=S_BASE, fg=FG3,
              activebackground=S_CARD, activeforeground="#ff6666",
              relief="flat", bd=0, padx=6, pady=1, cursor="hand2",
              command=_clear_log).pack(side="right", padx=4)

    log_frame = tk.Frame(right_col, bg=S_BASE)
    log_frame.pack(fill="both", expand=True)

    _log_text = tk.Text(
        log_frame, bg=S_BASE, fg=FG2,
        font=("Consolas", 8), relief="flat", bd=0,
        state="disabled", wrap="word",
        insertbackground=FG,
    )
    log_sb = tk.Scrollbar(log_frame, command=_log_text.yview,
                           bg=S_BASE, troughcolor=S_BASE)
    _log_text.config(yscrollcommand=log_sb.set)
    log_sb.pack(side="right", fill="y")
    _log_text.pack(side="left", fill="both", expand=True, padx=6, pady=4)

    # Tag pour timestamps vs messages
    _log_text.tag_configure("ts", foreground=FG3)
    _log_text.tag_configure("err", foreground="#ffb4ab")
    _log_text.tag_configure("msg", foreground=FG2)

    # Surcharge _log_error pour utiliser les tags
    def _log_with_tags(msg: str):
        ts   = datetime.datetime.now().strftime("%H:%M:%S")
        def _insert():
            _log_text.config(state="normal")
            _log_text.insert("end", f"[{ts}] ", "ts")
            tag = "err" if any(w in msg.lower() for w in ("error", "erreur", "failed", "warning")) else "msg"
            _log_text.insert("end", f"{msg}\n", tag)
            _log_text.see("end")
            _log_text.config(state="disabled")
        if _root:
            _root.after(0, _insert)
        else:
            print(f"[{ts}] {msg}", file=sys.stderr)

    # Monkey-patch _log_error pour cette session
    _root._log_fn = _log_with_tags

    # Message d'initialisation
    _log_with_tags("MeetNote Engine initialized.")
    _log_with_tags("Audio backend ready (WASAPI).")

    # ── Polling Outlook : pré-remplir, détecter conflits, auto-démarrer ─────────
    _auto_started_keys: set = set()  # évite de relancer pour la même réunion
    _resolved_conflict_key: list = [None]  # frozenset des sujets du dernier conflit résolu

    _poll_after_id: list = [None]  # tick en attente : un seul à la fois, même après un clic sur 🔄

    def _recharger_agenda(motif: str):
        """Recharge le cache d'agenda (thread démon) puis relance l'évaluation.

        Tourne aussi pendant un enregistrement : la fin de réunion lit ce cache.
        `recharger` ne fait rien si un rechargement tient déjà la réservation.
        """
        try:
            source = source_agenda.recharger(_cache_agenda, datetime.datetime.now(), motif)
        except Exception as e:
            source = None
            _log_with_tags(f"[Outlook] Erreur: {e}")
        # Un rechargement déjà en cours rend aussi None : ce n'est pas un échec
        if (source is None and _cache_agenda.jamais_charge
                and not _cache_agenda.rechargement_en_cours):
            _log_with_tags("[Outlook] Agenda indisponible, nouvel essai au prochain tick.")
        if _root:
            if source is not None:
                _root.after(0, _reappliquer_preremplissage)
            _root.after(0, _evaluer_agenda)

    def _evaluer_agenda():
        """Auto-start, pré-remplissage et conflits, d'après le cache (aucun accès Outlook)."""
        if _recording or _cache_agenda.jamais_charge:
            return

        def _fetch():
            meetings = _cache_agenda.reunions_en_cours(datetime.datetime.now(), 30)
            if not meetings:
                _log_with_tags("[Outlook] Aucune réunion dans les 30 prochaines minutes.")
                return

            now = datetime.datetime.now()

            # ── Auto-start mixte si réunion imminente (dans 2 min ou déjà en cours) ──
            for mtg in meetings:
                if _is_auto_start_excluded(mtg["subject"]):
                    _log_with_tags(f"[Outlook] Ignoré (exclusion) : {mtg['subject']}")
                    continue
                key = mtg["subject"] + mtg["start"].isoformat()[:16]
                delta = (mtg["start"] - now).total_seconds()
                if -60 <= delta <= 120 and key not in _auto_started_keys:
                    _auto_started_keys.add(key)
                    _log_with_tags(f"[Outlook] Auto-start mixte : {mtg['subject']}")
                    def _do_auto(m=mtg):
                        global _current_meeting_end, _end_warned_key, _reunion_imposee
                        if _recording or _modal_dialog_open:
                            return
                        if _source_var:
                            _source_var.set("🎤🔊  Mixte (micro + PC)")
                        if _meeting_name_var:
                            _meeting_name_var.set(m["subject"])
                            _meeting_name_var._last_outlook = m["subject"]
                        _current_meeting_end = m["end"]
                        _end_warned_key = None
                        _reunion_imposee = m
                        _do_start()
                    _root.after(0, _do_auto)
                    return  # un seul auto-start à la fois

            # ── Pré-remplir le nom / popup si conflits ───────────────────────────
            if _meeting_name_var and _root:
                if len(meetings) == 1:
                    subject = meetings[0]["subject"]
                    _log_with_tags(f"[Outlook] Réunion détectée : {subject}")
                    def _set_single(s=subject):
                        current = _meeting_name_var.get().strip()
                        if not current or current == _meeting_name_var._last_outlook:
                            _meeting_name_var.set(s)
                            _meeting_name_var._last_outlook = s
                    _root.after(0, _set_single)
                else:
                    subjects = [m["subject"] for m in meetings]
                    conflict_key = frozenset(subjects)
                    if conflict_key != _resolved_conflict_key[0]:
                        _log_with_tags(f"[Outlook] Conflit de réunions : {subjects}")
                    def _show_conflict(ms=meetings, ck=conflict_key):
                        global _conflict_win
                        # Ne pas rouvrir si ce conflit a déjà été résolu
                        if _resolved_conflict_key[0] == ck:
                            return
                        # Ne pas rouvrir si un popup est déjà visible
                        if _conflict_win is not None:
                            try:
                                if _conflict_win.winfo_exists():
                                    return
                            except Exception:
                                pass
                        win = tk.Toplevel(_root)
                        _conflict_win = win
                        win.title("Conflit de réunions")
                        win.configure(bg="#111125")
                        win.attributes("-topmost", True)
                        win.resizable(False, False)
                        tk.Label(win, text="Plusieurs réunions simultanées.\nLaquelle enregistrer ?",
                                 bg="#111125", fg="#e2e0fc",
                                 font=("Segoe UI", 10, "bold")).pack(padx=20, pady=(15, 8))
                        chosen = tk.StringVar(value=ms[0]["subject"])
                        for m in ms:
                            lbl = f"{m['subject']}  ({m['start'].strftime('%H:%M')}–{m['end'].strftime('%H:%M')})"
                            tk.Radiobutton(win, text=lbl, variable=chosen, value=m["subject"],
                                           bg="#111125", fg="#e2e0fc", selectcolor="#2a2a4a",
                                           activebackground="#111125", activeforeground="#e2e0fc",
                                           font=("Segoe UI", 9)).pack(anchor="w", padx=20, pady=2)
                        def _close():
                            global _conflict_win
                            _conflict_win = None
                            win.destroy()
                        def _confirm(ck=conflict_key):
                            s = chosen.get()
                            _meeting_name_var.set(s)
                            _meeting_name_var._last_outlook = s
                            _resolved_conflict_key[0] = ck  # ne plus poser la question pour ce conflit
                            _close()
                        win.protocol("WM_DELETE_WINDOW", _close)
                        tk.Button(win, text="Confirmer", command=_confirm,
                                  bg="#5b5be8", fg="white", font=("Segoe UI", 9, "bold"),
                                  relief="flat", padx=12, pady=4).pack(pady=(10, 15))
                        win.update_idletasks()
                        x = _root.winfo_x() + (_root.winfo_width() - win.winfo_reqwidth()) // 2
                        y = _root.winfo_y() + (_root.winfo_height() - win.winfo_reqheight()) // 2
                        win.geometry(f"+{x}+{y}")
                    _root.after(0, _show_conflict)

        _fetch()

    def _poll_outlook():
        """Tick de 60 s : décide d'un rechargement du cache, puis évalue les réunions."""
        if _root is None:
            return
        if _poll_after_id[0] is not None:
            try:
                _root.after_cancel(_poll_after_id[0])
            except tk.TclError:
                pass  # tick déjà échu (appel par le tick lui-même) : rien à annuler
        _poll_after_id[0] = _root.after(60_000, _poll_outlook)

        motif = _cache_agenda.doit_recharger(datetime.datetime.now())
        if motif and not _cache_agenda.rechargement_en_cours:
            threading.Thread(target=_recharger_agenda, args=(motif,), daemon=True).start()
        else:
            _evaluer_agenda()

    _meeting_name_var._last_outlook = ""
    global _outlook_poll_fn
    _outlook_poll_fn = _poll_outlook
    _root.after(1_000, _poll_outlook)  # premier appel 1s après démarrage

    # ── Polling fin de réunion : popup d'alerte 5 min avant la fin ───────────
    def _show_end_warn_popup(minutes_left: float, next_mtg: dict | None):
        global _end_warn_win
        if _end_warn_win is not None:
            try:
                if _end_warn_win.winfo_exists():
                    return
            except Exception:
                pass

        win = tk.Toplevel(_root)
        _end_warn_win = win
        win.title("Fin de réunion imminente")
        win.configure(bg="#111125")
        win.attributes("-topmost", True)
        win.resizable(False, False)

        mins = max(0, int(minutes_left))
        if mins == 0:
            time_txt = "La réunion se termine maintenant."
        else:
            time_txt = f"La réunion se termine dans {mins} min."

        tk.Label(win, text=time_txt,
                 bg="#111125", fg="#e2e0fc",
                 font=("Segoe UI", 10, "bold")).pack(padx=24, pady=(18, 4))

        if next_mtg:
            next_label = f"{next_mtg['subject']}  ({next_mtg['start'].strftime('%H:%M')})"
            tk.Label(win, text=f"Réunion suivante : {next_label}",
                     bg="#111125", fg="#aaaacc",
                     font=("Segoe UI", 9)).pack(padx=24, pady=(0, 12))
        else:
            tk.Label(win, text="Arrêter l'enregistrement ?",
                     bg="#111125", fg="#aaaacc",
                     font=("Segoe UI", 9)).pack(padx=24, pady=(0, 12))

        def _close():
            global _end_warn_win
            _end_warn_win = None
            win.destroy()

        def _stop():
            _close()
            _do_stop_transcribe()

        def _stop_and_restart(m=next_mtg):
            global _current_meeting_end, _end_warned_key
            _close()
            _do_stop_transcribe()
            if m and _root:
                def _restart():
                    global _current_meeting_end, _end_warned_key, _reunion_imposee
                    if _recording:  # arrêt annulé (alerte batterie) : l'enregistrement continue tel quel
                        return
                    if _source_var:
                        _source_var.set("🎤🔊  Mixte (micro + PC)")
                    if _meeting_name_var:
                        _meeting_name_var.set(m["subject"])
                        _meeting_name_var._last_outlook = m["subject"]
                    _current_meeting_end = m["end"]
                    _end_warned_key = None
                    _reunion_imposee = m
                    _do_start()
                _root.after(500, _restart)

        btn_frame = tk.Frame(win, bg="#111125")
        btn_frame.pack(pady=(0, 16), padx=16)

        tk.Button(btn_frame, text="Arrêter", command=_stop,
                  bg="#dc3232", fg="white", font=("Segoe UI", 9, "bold"),
                  relief="flat", padx=12, pady=5).pack(side="left", padx=4)

        if next_mtg:
            tk.Button(btn_frame,
                      text=f"Arrêter + Relancer → {next_mtg['subject'][:28]}",
                      command=_stop_and_restart,
                      bg="#5b5be8", fg="white", font=("Segoe UI", 9, "bold"),
                      relief="flat", padx=12, pady=5).pack(side="left", padx=4)

        tk.Button(btn_frame, text="Ignorer", command=_close,
                  bg="#333355", fg="#aaaacc", font=("Segoe UI", 9),
                  relief="flat", padx=12, pady=5).pack(side="left", padx=4)

        win.protocol("WM_DELETE_WINDOW", _close)
        win.update_idletasks()
        x = _root.winfo_x() + (_root.winfo_width() - win.winfo_reqwidth()) // 2
        y = _root.winfo_y() + (_root.winfo_height() - win.winfo_reqheight()) // 2
        win.geometry(f"+{x}+{y}")

    def _poll_meeting_end():
        global _end_warned_key

        if not _root:
            return

        if _recording and _current_meeting_end is not None:
            now = datetime.datetime.now()
            minutes_left = (_current_meeting_end - now).total_seconds() / 60
            warn_key = _current_meeting_end.isoformat()[:16]

            if -2 <= minutes_left <= 5 and warn_key != _end_warned_key:
                _end_warned_key = warn_key

                # Réunion démarrant dans les 15 min après la fin actuelle, lue dans le
                # cache d'agenda (aucun accès Outlook : pas de thread nécessaire).
                nom_courant = _meeting_name_var.get() if _meeting_name_var else ""
                next_mtg = _cache_agenda.suivante_apres(
                    _current_meeting_end, 15, exclure_objet=nom_courant)
                _show_end_warn_popup(minutes_left, next_mtg)

        _root.after(30_000, _poll_meeting_end)

    _root.after(30_000, _poll_meeting_end)  # démarre 30s après l'ouverture

    # ── Centrer la fenêtre
    _root.update_idletasks()
    w = TOTAL_W
    h = max(_root.winfo_reqheight(), 580)
    sw, sh = _root.winfo_screenwidth(), _root.winfo_screenheight()
    _root.geometry(f"{w}x{h}+{(sw-w)//2}+{(sh-h)//2}")

    return _root


def _import_audio_file():
    """Ouvre un sélecteur de fichier audio et crée un job de transcription."""
    from tkinter import filedialog
    path = filedialog.askopenfilename(
        parent=_root,
        title="Importer un fichier audio",
        filetypes=[
            ("Fichiers audio", "*.wav *.mp3 *.opus *.ogg *.m4a *.flac *.webm *.mp4"),
            ("Tous les fichiers", "*.*"),
        ],
    )
    if not path:
        return

    now = datetime.datetime.now()
    model  = _model_var.get() if _model_var else config.WHISPER_MODEL
    lang   = _lang_var.get()  if _lang_var  else config.WHISPER_LANGUAGE
    mtype  = _type_var.get()  if _type_var  else ""
    output = _output_var.get() if _output_var else "notion"
    name   = os.path.splitext(os.path.basename(path))[0]

    job = _Job(
        id=now.strftime("%Y%m%d_%H%M%S") + "_imp",
        wav_path=path,
        start_time=now,
        duration_min=0,
        model_name=model,
        language=lang,
        meeting_type=mtype if mtype not in ("—",) else "A classifier",
        output_mode=output,
        meeting_name=name,
        status_audio="done",
        status_transcript="queued",
        status_notion="pending" if output == "notion" else "skipped",
    )

    with _all_jobs_lock:
        _all_jobs.insert(0, job)
    hist_mod.add(job)

    with _queue_lock:
        _job_queue.appendleft(job)
    _work_event.set()
    _refresh_ui()
    if _history_win and _history_win.winfo_exists():
        _refresh_history_panel()
    _log_error(f"Import : {os.path.basename(path)} → transcription en cours…")


def _import_outlook_mails():
    """
    Dialogue listant les mails Outlook catégorie "meetnote".
    L'utilisateur sélectionne les mails à pousser vers Notion.
    Après succès, chaque mail est déplacé dans le dossier "meetnote OK".
    """
    import tkinter.messagebox as mb

    import outlook_mail

    if not outlook_mail.is_available():
        mb.showwarning("Non disponible",
                       "win32com n'est pas installé — impossible de lire Outlook.",
                       parent=_root)
        return

    # ── Récupération des mails (dans un thread pour ne pas bloquer l'UI)
    dlg = tk.Toplevel(_root)
    dlg.title("Importer des mails Outlook")
    dlg.configure(bg="#1a1a2e")
    dlg.resizable(True, True)
    dlg.grab_set()

    _root.update_idletasks()
    x = _root.winfo_x() + (_root.winfo_width()  - 580) // 2
    y = _root.winfo_y() + (_root.winfo_height() - 520) // 2
    dlg.geometry(f"580x520+{x}+{y}")

    DLG_BG   = "#1a1a2e"
    DLG_BG2  = "#16213e"
    DLG_BG3  = "#0f3460"
    DLG_FG   = "#e0e0e0"
    DLG_FG2  = "#888888"

    tk.Label(dlg, text="📧  Mails catégorie « meetnote »",
             font=("Segoe UI", 11, "bold"), bg=DLG_BG, fg=DLG_FG).pack(pady=(14, 2))
    tk.Label(dlg, text="Cochez les mails à envoyer dans Notion → ils seront déplacés vers « meetnote OK »",
             font=("Segoe UI", 8), bg=DLG_BG, fg=DLG_FG2, wraplength=520).pack(pady=(0, 8))

    # ── Zone scrollable pour la liste
    list_frame_outer = tk.Frame(dlg, bg=DLG_BG2, bd=0)
    list_frame_outer.pack(fill="both", expand=True, padx=14, pady=(0, 8))

    canvas = tk.Canvas(list_frame_outer, bg=DLG_BG2, highlightthickness=0)
    scrollbar = tk.Scrollbar(list_frame_outer, orient="vertical", command=canvas.yview)
    canvas.configure(yscrollcommand=scrollbar.set)
    scrollbar.pack(side="right", fill="y")
    canvas.pack(side="left", fill="both", expand=True)

    list_frame = tk.Frame(canvas, bg=DLG_BG2)
    cw = canvas.create_window((0, 0), window=list_frame, anchor="nw")

    def _on_frame_conf(e):
        canvas.configure(scrollregion=canvas.bbox("all"))
    list_frame.bind("<Configure>", _on_frame_conf)

    def _on_canvas_conf(e):
        canvas.itemconfig(cw, width=e.width)
    canvas.bind("<Configure>", _on_canvas_conf)

    canvas.bind("<MouseWheel>", lambda e: canvas.yview_scroll(int(-1*(e.delta/120)), "units"))

    status_lbl = tk.Label(dlg, text="Chargement des mails…",
                          font=("Segoe UI", 8, "italic"), bg=DLG_BG, fg=DLG_FG2)
    status_lbl.pack(pady=(0, 4))

    # ── Boutons bas
    btn_frame = tk.Frame(dlg, bg=DLG_BG)
    btn_frame.pack(fill="x", padx=14, pady=(0, 14))

    check_vars: list[tuple[tk.BooleanVar, dict]] = []   # (var, mail_dict)

    def _do_import():
        selected = [(v, m) for v, m in check_vars if v.get()]
        if not selected:
            mb.showwarning("Aucun mail sélectionné",
                           "Cochez au moins un mail.", parent=dlg)
            return

        output = _output_var.get() if _output_var else "notion"
        imported = 0
        for _, mail in selected:
            now   = mail["date"]
            mname = mail["subject"] or f"Mail {now.strftime('%Y-%m-%d %H:%M')}"
            body  = mail["body"]
            if not body.strip():
                body = "(corps vide)"

            job = _Job(
                id=now.strftime("%Y%m%d_%H%M%S") + f"_mail{imported}",
                wav_path="",
                start_time=now,
                duration_min=0,
                model_name="",
                language="",
                meeting_type="Email",
                output_mode=output,
                meeting_name=mname,
                teams_participants=[mail["sender"]] if mail["sender"] else [],
                status_audio="done",
                status_transcript="done",
                status_notion="pending" if output == "notion" else "skipped",
                transcript=f"De : {mail['sender']}\nObjet : {mail['subject']}\n"
                           f"Date : {now.strftime('%d/%m/%Y %H:%M')}\n\n{body}",
                attachments=mail.get("attachments", []),
            )

            with _all_jobs_lock:
                _all_jobs.insert(0, job)
            hist_mod.add(job)

            if output == "notion":
                with _queue_lock:
                    _job_queue.appendleft(job)

            # Changement de catégorie → "meetnote OK" en arrière-plan (non bloquant)
            entry_id = mail["entry_id"]
            threading.Thread(
                target=outlook_mail.mark_as_meetnote_ok,
                args=(entry_id,), daemon=True
            ).start()

            imported += 1

        if imported:
            _work_event.set()
            _refresh_ui()
            if _history_win and _history_win.winfo_exists():
                _refresh_history_panel()
            _log_error(f"{imported} mail(s) importé(s) → Notion en cours…")

        dlg.destroy()

    tk.Button(btn_frame, text="✓ Importer et envoyer vers Notion",
              font=("Segoe UI", 9, "bold"), bg="#2e7d32", fg="white",
              relief="flat", bd=0, padx=12, pady=8, cursor="hand2",
              command=_do_import).pack(side="left", fill="x", expand=True)
    tk.Button(btn_frame, text="Annuler",
              font=("Segoe UI", 9), bg="#333355", fg="#aaaacc",
              relief="flat", bd=0, padx=12, pady=8, cursor="hand2",
              command=dlg.destroy).pack(side="left", padx=(8, 0))

    # ── Chargement des mails en arrière-plan pour ne pas geler l'UI
    def _load_mails():
        try:
            import pythoncom
            pythoncom.CoInitialize()
        except Exception:
            pass
        mails = outlook_mail.get_meetnote_emails()

        def _populate():
            status_lbl.configure(text=f"{len(mails)} mail(s) trouvé(s) avec la catégorie « meetnote »")

            if not mails:
                tk.Label(list_frame, text="Aucun mail trouvé avec la catégorie « meetnote ».",
                         font=("Segoe UI", 9, "italic"), bg=DLG_BG2, fg=DLG_FG2
                         ).pack(padx=12, pady=20)
                return

            for mail in mails:
                var = tk.BooleanVar(value=True)
                check_vars.append((var, mail))

                row = tk.Frame(list_frame, bg=DLG_BG3, bd=0)
                row.pack(fill="x", padx=6, pady=3)

                # ── Checkbox custom (tk.Checkbutton natif mal visible sur fond sombre)
                chk_frame = tk.Frame(row, bg=DLG_BG3, cursor="hand2")
                chk_frame.pack(side="left", padx=(8, 4), pady=6)
                chk_box = tk.Label(chk_frame, text="✓", font=("Segoe UI", 10, "bold"),
                                   bg="#2e7d32", fg="white", width=2,
                                   relief="flat", cursor="hand2")
                chk_box.pack()

                def _toggle(v=var, lbl=chk_box):
                    v.set(not v.get())
                    lbl.configure(bg="#2e7d32" if v.get() else "#333355",
                                  fg="white" if v.get() else "#666688",
                                  text="✓" if v.get() else " ")

                chk_box.bind("<Button-1>", lambda e, fn=_toggle: fn())

                info = tk.Frame(row, bg=DLG_BG3, cursor="hand2")
                info.pack(side="left", fill="x", expand=True, padx=(4, 8), pady=6)
                info.bind("<Button-1>", lambda e, fn=_toggle: fn())

                subject = mail["subject"] or "(sans objet)"
                subj_lbl = tk.Label(info, text=subject, font=("Segoe UI", 9, "bold"),
                                    bg=DLG_BG3, fg=DLG_FG, anchor="w",
                                    wraplength=430, cursor="hand2")
                subj_lbl.pack(fill="x")
                subj_lbl.bind("<Button-1>", lambda e, fn=_toggle: fn())

                meta = f"{mail['sender']}  ·  {mail['date'].strftime('%d/%m/%Y %H:%M')}"
                meta_lbl = tk.Label(info, text=meta, font=("Segoe UI", 7),
                                    bg=DLG_BG3, fg=DLG_FG2, anchor="w", cursor="hand2")
                meta_lbl.pack(fill="x")
                meta_lbl.bind("<Button-1>", lambda e, fn=_toggle: fn())

                # Pièces jointes
                atts = mail.get("attachments", [])
                if atts:
                    att_names = "  📎 " + "  ·  ".join(
                        os.path.basename(p) for p in atts
                    )
                    tk.Label(info, text=att_names, font=("Segoe UI", 7),
                             bg=DLG_BG3, fg="#ffcc66", anchor="w",
                             wraplength=430).pack(fill="x")

                preview = mail["body"][:120].replace("\n", " ").strip()
                if len(mail["body"]) > 120:
                    preview += "…"
                if preview:
                    tk.Label(info, text=preview, font=("Segoe UI", 7, "italic"),
                             bg=DLG_BG3, fg=DLG_FG2, anchor="w",
                             wraplength=430).pack(fill="x")

        dlg.after(0, _populate)

    threading.Thread(target=_load_mails, daemon=True).start()


def _import_transcript():
    """Dialogue pour importer un transcript : fichier texte ou copier-coller."""
    import tkinter.messagebox as mb
    from tkinter import filedialog

    dlg = tk.Toplevel(_root)
    dlg.title("Importer un transcript")
    dlg.configure(bg="#1a1a2e")
    dlg.resizable(False, False)
    dlg.grab_set()

    # ── Centrage sur la fenêtre principale
    _root.update_idletasks()
    x = _root.winfo_x() + (_root.winfo_width()  - 540) // 2
    y = _root.winfo_y() + (_root.winfo_height() - 420) // 2
    dlg.geometry(f"540x420+{x}+{y}")

    DLG_BG   = "#1a1a2e"
    DLG_BG2  = "#16213e"
    DLG_BG3  = "#0f3460"
    DLG_FG   = "#e0e0e0"
    DLG_FG2  = "#888888"
    DLG_GRN  = "#2e7d32"
    DLG_LGRN = "#88ee88"

    tk.Label(dlg, text="Importer un transcript", font=("Segoe UI", 11, "bold"),
             bg=DLG_BG, fg=DLG_FG).pack(pady=(14, 2))
    tk.Label(dlg, text="Choisissez la source du transcript",
             font=("Segoe UI", 8), bg=DLG_BG, fg=DLG_FG2).pack(pady=(0, 10))

    # ── Onglets
    tab_frame = tk.Frame(dlg, bg=DLG_BG)
    tab_frame.pack(fill="x", padx=14)

    tab_var = tk.StringVar(value="fichier")

    panel_fichier = tk.Frame(dlg, bg=DLG_BG2)
    panel_coller  = tk.Frame(dlg, bg=DLG_BG2)

    def _show_tab(name):
        tab_var.set(name)
        if name == "fichier":
            panel_coller.pack_forget()
            panel_fichier.pack(fill="both", expand=True, padx=14, pady=(0, 8))
            btn_tab_f.configure(bg="#2e5e8e", fg="white")
            btn_tab_c.configure(bg=DLG_BG3, fg=DLG_FG2)
        else:
            panel_fichier.pack_forget()
            panel_coller.pack(fill="both", expand=True, padx=14, pady=(0, 8))
            btn_tab_c.configure(bg="#2e5e8e", fg="white")
            btn_tab_f.configure(bg=DLG_BG3, fg=DLG_FG2)

    btn_tab_f = tk.Button(tab_frame, text="📄 Fichier", font=("Segoe UI", 9),
                          bg="#2e5e8e", fg="white", relief="flat", bd=0,
                          padx=12, pady=5, cursor="hand2",
                          command=lambda: _show_tab("fichier"))
    btn_tab_f.pack(side="left", padx=(0, 4))
    btn_tab_c = tk.Button(tab_frame, text="📋 Copier-coller", font=("Segoe UI", 9),
                          bg=DLG_BG3, fg=DLG_FG2, relief="flat", bd=0,
                          padx=12, pady=5, cursor="hand2",
                          command=lambda: _show_tab("coller"))
    btn_tab_c.pack(side="left")

    # ── Panneau Fichier
    file_path_var = tk.StringVar()
    file_lbl = tk.Label(panel_fichier, text="Aucun fichier sélectionné",
                        font=("Segoe UI", 8), bg=DLG_BG2, fg=DLG_FG2,
                        wraplength=460, anchor="w")
    file_lbl.pack(fill="x", padx=10, pady=(10, 4))

    def _choose_file():
        p = filedialog.askopenfilename(
            parent=dlg,
            title="Choisir un fichier transcript",
            filetypes=[
                ("Fichiers texte", "*.txt *.md *.srt *.vtt"),
                ("Tous les fichiers", "*.*"),
            ],
        )
        if p:
            file_path_var.set(p)
            file_lbl.configure(text=f"✓ {os.path.basename(p)}", fg=DLG_LGRN)

    tk.Button(panel_fichier, text="Choisir un fichier…", font=("Segoe UI", 9),
              bg=DLG_BG3, fg=DLG_FG, relief="flat", bd=0, padx=10, pady=6,
              cursor="hand2", command=_choose_file).pack(fill="x", padx=10, pady=(0, 10))

    # ── Panneau Copier-coller
    tk.Label(panel_coller, text="Collez votre transcript :",
             font=("Segoe UI", 8), bg=DLG_BG2, fg=DLG_FG2,
             anchor="w").pack(fill="x", padx=10, pady=(8, 2))
    paste_text = tk.Text(panel_coller, height=9, wrap="word",
                         bg=DLG_BG3, fg=DLG_FG, insertbackground=DLG_FG,
                         font=("Segoe UI", 9), relief="flat",
                         padx=6, pady=4)
    paste_text.pack(fill="both", expand=True, padx=10, pady=(0, 8))

    _show_tab("fichier")

    # ── Infos réunion
    info_frame = tk.Frame(dlg, bg=DLG_BG)
    info_frame.pack(fill="x", padx=14, pady=(0, 6))
    tk.Label(info_frame, text="Nom de la réunion (optionnel) :",
             font=("Segoe UI", 8), bg=DLG_BG, fg=DLG_FG2).pack(anchor="w")
    name_var = tk.StringVar()
    tk.Entry(info_frame, textvariable=name_var, bg=DLG_BG3, fg=DLG_FG,
             insertbackground=DLG_FG, relief="flat", font=("Segoe UI", 9),
             ).pack(fill="x", pady=(2, 0))

    # ── Boutons bas
    btn_frame = tk.Frame(dlg, bg=DLG_BG)
    btn_frame.pack(fill="x", padx=14, pady=(0, 14))

    def _do_import():
        mode = tab_var.get()
        if mode == "fichier":
            p = file_path_var.get()
            if not p or not os.path.isfile(p):
                mb.showwarning("Fichier manquant", "Veuillez d'abord sélectionner un fichier.", parent=dlg)
                return
            with open(p, "r", encoding="utf-8", errors="replace") as f:
                text = f.read().strip()
            suggested_name = os.path.splitext(os.path.basename(p))[0]
        else:
            text = paste_text.get("1.0", "end").strip()
            suggested_name = ""

        if not text:
            mb.showwarning("Transcript vide", "Le transcript est vide.", parent=dlg)
            return

        now   = datetime.datetime.now()
        mname = name_var.get().strip() or suggested_name or f"Réunion {now.strftime('%Y-%m-%d %H:%M')}"
        mtype = _type_var.get() if _type_var else ""
        output = _output_var.get() if _output_var else "notion"

        job = _Job(
            id=now.strftime("%Y%m%d_%H%M%S") + "_txt",
            wav_path="",
            start_time=now,
            duration_min=0,
            model_name="",
            language="",
            meeting_type=mtype if mtype not in ("—",) else "",
            output_mode=output,
            meeting_name=mname,
            status_audio="done",
            status_transcript="done",
            status_notion="pending" if output == "notion" else "skipped",
            transcript=text,
        )

        with _all_jobs_lock:
            _all_jobs.insert(0, job)
        hist_mod.add(job)

        if output == "notion":
            with _queue_lock:
                _job_queue.appendleft(job)
            _work_event.set()

        _refresh_ui()
        if _history_win and _history_win.winfo_exists():
            _refresh_history_panel()
        wc = len(text.split())
        _log_error(f"Transcript importé : {mname} ({wc} mots) → Notion en cours…")
        dlg.destroy()

    tk.Button(btn_frame, text="Importer et envoyer vers Notion",
              font=("Segoe UI", 9, "bold"), bg=DLG_GRN, fg="white",
              relief="flat", bd=0, padx=12, pady=8, cursor="hand2",
              command=_do_import).pack(side="left", fill="x", expand=True)
    tk.Button(btn_frame, text="Annuler",
              font=("Segoe UI", 9), bg="#333355", fg="#aaaacc",
              relief="flat", bd=0, padx=12, pady=8, cursor="hand2",
              command=dlg.destroy).pack(side="left", padx=(8, 0))


def _get_startup_cmd() -> str:
    """Retourne la commande de démarrage Windows attendue pour MeetNote."""
    return demarrage_windows.commande_demarrage(
        getattr(sys, "frozen", False), sys.executable,
        os.path.dirname(os.path.abspath(__file__)))

def _startup_value() -> str | None:
    """Retourne la commande de démarrage auto enregistrée, ou None si absente."""
    import winreg
    return demarrage_windows.lire_valeur(winreg)


def _startup_is_enabled() -> bool:
    """Retourne True si la clé de démarrage automatique existe."""
    import winreg
    return demarrage_windows.est_active(winreg)

def _set_startup_enabled(enabled: bool):
    """Active ou désactive le démarrage automatique de MeetNote avec Windows."""
    import winreg
    demarrage_windows.definir_active(winreg, enabled, _get_startup_cmd())


def _fin_assistant():
    """À la fermeture de l'assistant : réglages recalés, agenda rechargé (EWS peut être
    configuré), puis modèle Whisper fixé et récupéré s'il manque."""
    _recaler_reglages()
    _forcer_agenda()
    _demarrer_modele_initial()


def _ouvrir_assistant():
    """Ouvre l'assistant de démarrage ; à son terme, les réglages enregistrés sont
    recalés en mémoire. Le test audio y est refusé pendant un enregistrement."""
    if _root is None:
        return
    assistant_fenetre.ouvrir_assistant(
        _root,
        # Fermé sans terminer : le modèle est quand même récupéré, sans attendre le
        # lancement suivant (l'assistant se rouvrirait alors sans rien télécharger).
        a_la_fermeture=_demarrer_modele_initial,
        a_la_fin=_fin_assistant,
        enregistrement_en_cours=lambda: _recording,
    )


def _open_settings():
    """Modal Settings — design Stitch 'Control Center'."""
    if _root is None:
        return

    S_BASE  = "#111125"
    S_MAIN  = "#16213e"
    S_CARD  = "#1e1e32"
    S_HIGH  = "#28283d"
    S_TOP   = "#333348"
    FG      = "#e2e0fc"
    FG2     = "#e4beba"
    FG3     = "#ab8985"
    RED     = "#dc3232"
    RED_DIM = "#b81c1c"
    ERR     = "#ff4444"

    win = tk.Toplevel(_root)
    win.title("Settings")
    win.configure(bg=S_CARD)
    win.resizable(False, False)
    win.grab_set()

    # ── Header modal
    hdr = tk.Frame(win, bg=S_MAIN)
    hdr.pack(fill="x")
    tk.Label(hdr, text="⚙  Control Center Settings",
             font=("Segoe UI", 11, "bold"), bg=S_MAIN, fg=FG).pack(
        side="left", padx=16, pady=12)
    tk.Button(hdr, text="✕", font=("Segoe UI", 10),
              bg=S_MAIN, fg=FG3, activebackground=S_CARD, activeforeground=ERR,
              relief="flat", bd=0, cursor="hand2",
              command=win.destroy).pack(side="right", padx=12, pady=8)

    # ── Tab bar (underline style)
    tab_bar = tk.Frame(win, bg=S_CARD)
    tab_bar.pack(fill="x")

    tab_bodies = {}
    _active_tab = tk.StringVar(value="notion")

    def _make_tab(name, label):
        f = tk.Frame(tab_bar, bg=S_CARD, cursor="hand2")
        f.pack(side="left")
        lbl = tk.Label(f, text=label, font=("Segoe UI", 9),
                       bg=S_CARD, fg=FG3, padx=16, pady=8)
        lbl.pack()
        underline = tk.Frame(f, bg=S_CARD, height=2)
        underline.pack(fill="x")
        body = tk.Frame(win, bg=S_CARD, padx=20, pady=14)
        tab_bodies[name] = (body, lbl, underline)

        def _activate():
            _active_tab.set(name)
            for n, (b, l, u) in tab_bodies.items():
                if n == name:
                    b.pack(fill="both", expand=True)
                    l.config(fg=FG)
                    u.config(bg=RED)
                else:
                    b.pack_forget()
                    l.config(fg=FG3)
                    u.config(bg=S_CARD)

        f.bind("<Button-1>", lambda e: _activate())
        lbl.bind("<Button-1>", lambda e: _activate())
        return _activate

    act_notion  = _make_tab("notion",  "  Notion  ")
    _make_tab("general", "  Général  ")
    _make_tab("outlook", "  Outlook  ")
    tk.Frame(win, bg=S_HIGH, height=1).pack(fill="x")

    # ── Tab : Notion
    tab_n = tab_bodies["notion"][0]

    INSTRUCTIONS = (
        "1. Créer une intégration Notion\n"
        "   → notion.so/my-integrations → New integration\n"
        "   → Copier le Secret (ntn_ ou secret_)\n\n"
        "2. Obtenir le Database ID\n"
        "   → URL notion : .../xxxxxxxxxxxxxxxxxxxxxxxx?v=...\n"
        "   → Copier les 32 caractères avant le « ?v= »\n\n"
        "3. Partager la database avec l'intégration\n"
        "   → ••• → Connections → Ajouter l'intégration"
    )
    instr_frame = tk.Frame(tab_n, bg=S_BASE)
    instr_frame.pack(fill="x", pady=(0, 10))
    instr_txt = tk.Text(instr_frame, height=8, bg=S_BASE, fg=FG2,
                        font=("Consolas", 8), relief="flat", bd=0,
                        wrap="word", state="normal")
    instr_txt.insert("1.0", INSTRUCTIONS)
    instr_txt.config(state="disabled")
    instr_txt.pack(fill="x", padx=8, pady=6)

    def _field_with_eye(parent, label, value, show_char="•"):
        tk.Label(parent, text=label, font=("Segoe UI", 8),
                 bg=S_CARD, fg=FG3, anchor="w").pack(fill="x", pady=(8, 1))
        row = tk.Frame(parent, bg=S_HIGH)
        row.pack(fill="x")
        var = tk.StringVar(value=value)
        _show = tk.BooleanVar(value=False)
        entry = tk.Entry(row, textvariable=var, font=("Segoe UI", 9),
                         bg=S_HIGH, fg=FG, insertbackground=FG,
                         relief="flat", bd=0, show=show_char)
        entry.pack(side="left", fill="x", expand=True, ipady=5, padx=(8, 0))

        def _toggle_eye():
            if _show.get():
                entry.config(show="")
                eye_btn.config(text="🙈")
            else:
                entry.config(show=show_char)
                eye_btn.config(text="👁")
            _show.set(not _show.get())

        eye_btn = tk.Button(row, text="👁", font=("Segoe UI", 9),
                            bg=S_HIGH, fg=FG3, activebackground=S_TOP, activeforeground=FG,
                            relief="flat", bd=0, cursor="hand2",
                            command=_toggle_eye)
        eye_btn.pack(side="right", padx=4)
        return var

    def _field(parent, label, value):
        tk.Label(parent, text=label, font=("Segoe UI", 8),
                 bg=S_CARD, fg=FG3, anchor="w").pack(fill="x", pady=(8, 1))
        var = tk.StringVar(value=value)
        tk.Entry(parent, textvariable=var, font=("Segoe UI", 9),
                 bg=S_HIGH, fg=FG, insertbackground=FG,
                 relief="flat", bd=0).pack(fill="x", ipady=5, padx=0)
        return var

    token_var = _field_with_eye(tab_n, "Notion Token", config.NOTION_TOKEN)
    db_var    = _field(tab_n, "Database ID", config.NOTION_DATABASE_ID)

    status_lbl = tk.Label(tab_n, text="", font=("Segoe UI", 8),
                          bg=S_CARD, fg=FG3, wraplength=360)
    status_lbl.pack(pady=(6, 0))

    def _save_notion():
        new_token = token_var.get().strip()
        new_db    = db_var.get().strip()
        if not new_token or not new_db:
            status_lbl.config(text="Token et Database ID requis.", fg=ERR)
            return
        try:
            user_config.save({"notion_token": new_token, "notion_database_id": new_db})
            _recaler_reglages()
            status_lbl.config(text="✓ Configuration sauvegardée.", fg="#00cc66")
        except Exception as e:
            status_lbl.config(text=f"Erreur : {e}", fg=ERR)

    # ── Tab : Outlook (EWS — Nouvel Outlook on-premise)
    tab_o = tab_bodies["outlook"][0]

    tk.Label(tab_o,
             text="Connexion Exchange (EWS)\nNécessaire avec le Nouvel Outlook on-premise",
             font=("Segoe UI", 9, "bold"), bg=S_CARD, fg=FG, justify="left").pack(
        anchor="w", pady=(0, 6))

    _ews_instr = (
        "1. Email      : votre adresse Exchange (prenom.nom@hopital.fr)\n"
        "2. Utilisateur : DOMAINE\\prenom.nom  (barre oblique inversée)\n"
        "3. Mot de passe : stocké dans le Gestionnaire d'identifiants Windows\n"
        "4. URL EWS    : laisser vide → Autodiscover (recommandé)\n"
        "   Sinon : https://mail.hopital.fr/EWS/Exchange.asmx"
    )
    oi_frame = tk.Frame(tab_o, bg=S_BASE)
    oi_frame.pack(fill="x", pady=(0, 10))
    oi_txt = tk.Text(oi_frame, height=6, bg=S_BASE, fg=FG2,
                     font=("Consolas", 8), relief="flat", bd=0,
                     wrap="word", state="normal")
    oi_txt.insert("1.0", _ews_instr)
    oi_txt.config(state="disabled")
    oi_txt.pack(fill="x", padx=8, pady=6)

    ews_email_var = _field(tab_o, "Email Exchange",  user_config.get("ews_email", ""))
    ews_user_var  = _field(tab_o, "Utilisateur (DOMAINE\\login)", user_config.get("ews_username", ""))
    ews_pass_var  = _field_with_eye(tab_o, "Mot de passe", "")
    ews_url_var   = _field(tab_o, "URL EWS (vide = Autodiscover)", user_config.get("ews_url", ""))

    try:
        import keyring as _kr
        _saved_user = user_config.get("ews_username", "")
        if _saved_user:
            _saved_pw = _kr.get_password("MeetNote_EWS", _saved_user) or ""
            ews_pass_var.set(_saved_pw)
    except Exception:
        pass

    ews_status_lbl = tk.Label(tab_o, text="", font=("Segoe UI", 8),
                              bg=S_CARD, fg=FG3, wraplength=360)
    ews_status_lbl.pack(pady=(6, 0))

    def _test_ews():
        import threading
        ews_status_lbl.config(text="Connexion en cours…", fg=FG3)
        win.update_idletasks()

        def _run():
            try:
                import ews_cal
                results = ews_cal.get_all_current_meetings(
                    email=ews_email_var.get().strip(),
                    username=ews_user_var.get().strip(),
                    password=ews_pass_var.get(),
                    ews_url=ews_url_var.get().strip(),
                    window_minutes=1440,
                )
                if results is None:
                    msg = "✗ Erreur lecture calendrier (voir logs)"
                    color = ERR
                elif results:
                    msg = f"✓ Connexion OK — {len(results)} RDV dans les 24h : {results[0]['subject']}"
                    color = "#00cc66"
                else:
                    msg = "✓ Connexion OK — aucun RDV dans les 24h"
                    color = "#00cc66"
            except Exception as e:
                msg = f"✗ Erreur : {e}"
                color = ERR
            win.after(0, lambda: ews_status_lbl.config(text=msg, fg=color))

        threading.Thread(target=_run, daemon=True).start()

    tk.Button(tab_o, text="Tester la connexion",
              font=("Segoe UI", 8), bg=S_HIGH, fg=FG,
              activebackground=S_TOP, activeforeground=FG,
              relief="flat", bd=0, pady=5, cursor="hand2",
              command=_test_ews).pack(anchor="w", pady=(4, 0))

    # ── Exclusions auto-start
    tk.Frame(tab_o, bg=S_HIGH, height=1).pack(fill="x", pady=(14, 0))
    tk.Label(tab_o, text="Exclusions auto-start",
             font=("Segoe UI", 10, "bold"), bg=S_CARD, fg=FG).pack(anchor="w", pady=(10, 2))
    tk.Label(tab_o,
             text="Ne pas démarrer automatiquement si l'intitulé contient\n"
                  "un de ces motifs (un par ligne, insensible à la casse) :",
             font=("Segoe UI", 8), bg=S_CARD, fg=FG3, justify="left").pack(anchor="w")

    excl_frame = tk.Frame(tab_o, bg=S_HIGH)
    excl_frame.pack(fill="x", pady=(4, 0))
    excl_text = tk.Text(excl_frame, height=4,
                        font=("Segoe UI", 9), bg=S_HIGH, fg=FG,
                        insertbackground=FG, relief="flat", bd=0,
                        highlightthickness=1, highlightbackground=S_TOP,
                        highlightcolor=RED, wrap="none")
    excl_sb = tk.Scrollbar(excl_frame, orient="vertical", command=excl_text.yview)
    excl_text.configure(yscrollcommand=excl_sb.set)
    excl_sb.pack(side="right", fill="y")
    excl_text.pack(side="left", fill="both", expand=True, padx=6, pady=4)
    _excl_init = user_config.get("auto_start_exclusions", "")
    if _excl_init:
        excl_text.insert("1.0", _excl_init)

    def _save_ews():
        new_email = ews_email_var.get().strip()
        new_user  = ews_user_var.get().strip()
        new_pw    = ews_pass_var.get()
        new_url   = ews_url_var.get().strip()
        excl_raw  = excl_text.get("1.0", "end-1c").strip()
        try:
            import keyring as _kr
            if new_user and new_pw:
                _kr.set_password("MeetNote_EWS", new_user, new_pw)
            user_config.save({
                "ews_email":             new_email,
                "ews_username":          new_user,
                "ews_url":               new_url,
                "auto_start_exclusions": excl_raw,
            })
            _recaler_reglages()
            _forcer_agenda()  # nouveaux identifiants EWS : l'agenda se recharge sans attendre
            ews_status_lbl.config(text="✓ Configuration EWS sauvegardée.", fg="#00cc66")
        except Exception as e:
            ews_status_lbl.config(text=f"Erreur : {e}", fg=ERR)

    # ── Tab : Général
    tab_g = tab_bodies["general"][0]

    tk.Label(tab_g, text="Destination du transcript",
             font=("Segoe UI", 10, "bold"), bg=S_CARD, fg=FG).pack(anchor="w", pady=(0, 8))

    out_frame = tk.Frame(tab_g, bg=S_HIGH)
    out_frame.pack(fill="x", pady=(4, 0))

    rb_notion = tk.Radiobutton(
        out_frame, text="  Envoyer vers Notion",
        variable=_output_var, value="notion",
        font=("Segoe UI", 9), bg=S_HIGH, fg=FG, selectcolor=S_HIGH,
        activebackground=S_HIGH, activeforeground=FG, relief="flat", cursor="hand2",
    )
    rb_notion.pack(anchor="w", padx=10, pady=(8, 2))
    tk.Label(out_frame, text="     Transcript + audio envoyés dans Notion",
             font=("Segoe UI", 8), bg=S_HIGH, fg=FG2, justify="left").pack(anchor="w", padx=10)

    tk.Frame(out_frame, bg="#334", height=1).pack(fill="x", padx=10, pady=8)

    rb_file = tk.Radiobutton(
        out_frame, text="  Fichier texte local uniquement",
        variable=_output_var, value="fichier",
        font=("Segoe UI", 9), bg=S_HIGH, fg=FG, selectcolor=S_HIGH,
        activebackground=S_HIGH, activeforeground=FG, relief="flat", cursor="hand2",
    )
    rb_file.pack(anchor="w", padx=10, pady=(0, 2))
    tk.Label(out_frame,
             text="     Sauvegardé en local uniquement\n"
                  "     (Documents\\MeetNote\\transcripts\\)",
             font=("Segoe UI", 8), bg=S_HIGH, fg=FG2, justify="left").pack(anchor="w", padx=10, pady=(0, 8))

    tk.Label(tab_g,
             text="Note : le transcript est TOUJOURS sauvegardé en local,\nmême en mode Notion.",
             font=("Segoe UI", 8), bg=S_CARD, fg="#00cc66").pack(anchor="w", pady=(8, 0))

    # ── Assistant de démarrage
    tk.Frame(tab_g, bg=S_HIGH, height=1).pack(fill="x", pady=(14, 0))
    tk.Label(tab_g, text="Assistant de démarrage",
             font=("Segoe UI", 10, "bold"), bg=S_CARD, fg=FG).pack(anchor="w", pady=(10, 4))

    def _relancer_assistant():
        """Ferme les paramètres (modaux) puis ouvre l'assistant."""
        win.destroy()
        _ouvrir_assistant()

    tk.Button(tab_g, text="Relancer l'assistant",
              font=("Segoe UI", 8), bg=S_HIGH, fg=FG,
              activebackground=S_TOP, activeforeground=FG,
              relief="flat", bd=0, pady=5, cursor="hand2",
              command=_relancer_assistant).pack(anchor="w")

    # ── Puissance allouée à la transcription
    tk.Frame(tab_g, bg=S_HIGH, height=1).pack(fill="x", pady=(14, 0))
    tk.Label(tab_g, text="Puissance de transcription",
             font=("Segoe UI", 10, "bold"), bg=S_CARD, fg=FG).pack(anchor="w", pady=(10, 4))

    perf_frame = tk.Frame(tab_g, bg=S_HIGH)
    perf_frame.pack(fill="x")

    perf_var = tk.StringVar(value=user_config.get("perf_profile", "auto"))

    def _sauver_perf():
        """Enregistre le profil ; il sera pris au chargement du prochain modèle."""
        global _whisper_model, _whisper_model_name
        user_config.save({"perf_profile": perf_var.get()})
        # Le modèle en cache porte les threads de l'ancien profil : le relâcher force
        # un rechargement calibré au prochain job, sans quoi le réglage resterait lettre morte.
        _whisper_model      = None
        _whisper_model_name = None
        perf_status_lbl.config(
            text=f"✓ {perf_profile.choisir(perf_var.get()).resume}", fg="#00cc66")

    for nom in perf_profile.PROFILS:
        tk.Radiobutton(
            perf_frame, text=f"  {perf_profile.LIBELLES[nom]}",
            variable=perf_var, value=nom, command=_sauver_perf,
            font=("Segoe UI", 9), bg=S_HIGH, fg=FG, selectcolor=S_HIGH,
            activebackground=S_HIGH, activeforeground=FG, relief="flat", cursor="hand2",
        ).pack(anchor="w", padx=10, pady=(6, 0))
    tk.Frame(perf_frame, bg=S_HIGH, height=6).pack(fill="x")

    tk.Label(tab_g, text=perf_profile.description_poste(),
             font=("Segoe UI", 8), bg=S_CARD, fg=FG2, wraplength=360,
             justify="left").pack(anchor="w", pady=(4, 0))
    perf_status_lbl = tk.Label(tab_g, text="", font=("Segoe UI", 8),
                               bg=S_CARD, fg=FG3, wraplength=360)
    perf_status_lbl.pack(anchor="w")

    # ── Démarrage avec Windows
    tk.Frame(tab_g, bg=S_HIGH, height=1).pack(fill="x", pady=(14, 0))
    tk.Label(tab_g, text="Démarrage avec Windows",
             font=("Segoe UI", 10, "bold"), bg=S_CARD, fg=FG).pack(anchor="w", pady=(10, 4))

    startup_frame = tk.Frame(tab_g, bg=S_HIGH)
    startup_frame.pack(fill="x")

    startup_var = tk.BooleanVar(value=_startup_is_enabled())
    startup_status_lbl = tk.Label(tab_g, text="", font=("Segoe UI", 8),
                                  bg=S_CARD, fg=FG3, wraplength=360)
    startup_status_lbl.pack(anchor="w", pady=(4, 0))

    def _toggle_startup():
        try:
            _set_startup_enabled(startup_var.get())
            if startup_var.get():
                startup_status_lbl.config(text="✓ MeetNote démarrera au démarrage de Windows (tray uniquement).", fg="#00cc66")
            else:
                startup_status_lbl.config(text="✓ Démarrage automatique désactivé.", fg=FG3)
        except Exception as e:
            startup_status_lbl.config(text=f"Erreur : {e}", fg=ERR)
            startup_var.set(not startup_var.get())

    tk.Checkbutton(
        startup_frame,
        text="  Lancer MeetNote au démarrage de Windows (tray, sans fenêtre principale)",
        variable=startup_var,
        font=("Segoe UI", 9), bg=S_HIGH, fg=FG, selectcolor=S_HIGH,
        activebackground=S_HIGH, activeforeground=FG,
        relief="flat", cursor="hand2",
        command=_toggle_startup,
    ).pack(anchor="w", padx=10, pady=8)

    # ── Footer boutons
    footer = tk.Frame(win, bg=S_MAIN)
    footer.pack(fill="x", side="bottom")
    tk.Button(footer, text="Discard", font=("Segoe UI", 9),
              bg=S_MAIN, fg=ERR, activebackground=S_CARD, activeforeground=ERR,
              relief="flat", bd=0, pady=9, cursor="hand2",
              command=win.destroy).pack(side="left", padx=16, pady=10)
    def _save_active():
        tab = _active_tab.get()
        if tab == "notion":
            _save_notion()
        elif tab == "outlook":
            _save_ews()

    tk.Button(footer, text="SAVE CONFIGURATION", font=("Segoe UI", 9, "bold"),
              bg=RED_DIM, fg="white", activebackground=RED, activeforeground="white",
              relief="flat", bd=0, pady=9, cursor="hand2",
              command=_save_active).pack(side="right", padx=16, pady=10, ipadx=12)

    # Activer onglet Notion par défaut
    act_notion()

    win.update_idletasks()
    wx = _root.winfo_x() + (_root.winfo_width()  - win.winfo_reqwidth())  // 2
    wy = _root.winfo_y() + (_root.winfo_height() - win.winfo_reqheight()) // 2
    win.geometry(f"+{wx}+{wy}")


# ─── Surveillance changement de périphérique audio ──────────────────────────
def _start_audio_device_monitor():
    """Lance un thread qui détecte les changements de sortie audio Windows
    et propose de redémarrer MeetNote pour une détection correcte."""
    global _audio_device_monitor_started
    if _audio_device_monitor_started:
        return
    _audio_device_monitor_started = True

    def _get_default_speaker_name() -> str:
        try:
            import soundcard as _sc
            spk = _sc.default_speaker()
            return spk.name if spk else ""
        except Exception:
            return ""

    def _get_default_mic_name() -> str:
        try:
            import soundcard as _sc
            mic = _sc.default_microphone()
            return mic.name if mic else ""
        except Exception:
            return ""

    _initial_name = _get_default_speaker_name()
    _initial_mic  = _get_default_mic_name()

    _restart_prompt_shown = [False]  # évite les doublons

    def _monitor():
        import time
        last_name = _initial_name
        last_mic  = _initial_mic
        while True:
            time.sleep(0.5)   # fenêtre courte : réagir vite au changement de périphérique
            current_name = _get_default_speaker_name()
            current_mic  = _get_default_mic_name()
            spk_changed = bool(current_name) and current_name != last_name
            mic_changed = bool(current_mic)  and current_mic  != last_mic

            # ── Pendant un enregistrement ────────────────────────────────────
            # Changement de sortie OU de micro (souvent liés : casque combiné
            # Bluetooth/USB) → armer la réouverture des flux de capture. Aucun
            # popup. _loopback_thread_fn ferme proprement la session en cours et
            # rouvre micro + loopback sur le nouveau périphérique par défaut.
            if _recording:
                if spk_changed or mic_changed:
                    prev_spk = last_name
                    if spk_changed: last_name = current_name
                    if mic_changed: last_mic  = current_mic
                    _audio_device_changed.set()
                    _logger.warning(
                        "Périphérique audio changé pendant l'enregistrement "
                        "(sortie: %s → %s | micro: %s → %s) : réouverture des flux de capture.",
                        prev_spk or "(inconnu)", current_name or "(inconnu)",
                        "≠" if mic_changed else "=", current_mic or "(inconnu)")
                continue

            # ── Hors enregistrement : proposer un redémarrage si la SORTIE change ─
            if mic_changed:
                last_mic = current_mic   # suivre le micro sans popup
            if spk_changed and not _restart_prompt_shown[0]:
                prev, last_name = last_name, current_name
                _restart_prompt_shown[0] = True

                def _ask(old=prev, new=current_name):
                    import tkinter.messagebox as mb
                    if _recording:
                        # reporter la proposition de redémarrage après la fin d'enregistrement
                        _restart_prompt_shown[0] = False
                        return
                    # Ne pas interrompre si un dialog modal est actif (ex: transcript Teams)
                    if _modal_dialog_open:
                        _restart_prompt_shown[0] = False
                        return
                    try:
                        if _root and _root.grab_current():
                            _restart_prompt_shown[0] = False
                            return
                    except Exception:
                        pass
                    msg = (f"La sortie audio par défaut a changé :\n"
                           f"  {old or '(inconnu)'}  →  {new}\n\n"
                           f"Redémarrer MeetNote pour une détection correcte ?")
                    if mb.askyesno("Changement audio détecté", msg, default="yes"):
                        # Pas de flag "quit_for_good" : le watchdog externe
                        # relance MeetNote automatiquement après la sortie.
                        _shutdown_app(quit_for_good=False)
                    else:
                        _restart_prompt_shown[0] = False  # permettre une prochaine alerte

                if _root:
                    _root.after(0, _ask)
            elif not spk_changed:
                _restart_prompt_shown[0] = False  # sortie stable

    threading.Thread(target=_monitor, daemon=True).start()


# ─── Main ─────────────────────────────────────────────────────────────────────
_LOCK_FILE = os.path.join(os.path.expanduser("~"), "AppData", "Roaming",
                          "MeetNote", "meetnote.pid")
# Déposé juste avant une sortie volontaire (Quitter / stop.bat) pour indiquer
# au watchdog externe (watchdog.py) qu'il ne doit PAS relancer MeetNote.
# Absence de ce fichier après la sortie du process == crash → relance auto.
_STOP_FLAG = os.path.join(os.path.expanduser("~"), "AppData", "Roaming",
                          "MeetNote", "clean_exit.flag")

def _terminer_ancienne_instance(psutil, old_pid):
    """Termine le process `old_pid` seulement s'il est bien MeetNote.

    Un PID recyclé par Windows pour un autre programme (autre python.exe,
    boucle du kit…) n'est jamais touché ; en cas de doute (accès refusé,
    process disparu), on ne tue rien.
    """
    try:
        proc = psutil.Process(old_pid)
        nom, ligne = proc.name(), proc.cmdline()
        if not _watchdog.est_meetnote(nom, ligne):
            _logger.info("PID %s (%s) n'est pas MeetNote : conservé", old_pid, nom)
            return
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except psutil.TimeoutExpired:
            proc.kill()
    except psutil.NoSuchProcess:
        pass  # déjà terminé : rien à faire
    except psutil.AccessDenied:
        _logger.warning("Accès refusé au PID %s : ancienne instance non arrêtée", old_pid)
    except psutil.Error as exc:
        _logger.warning("Arrêt de l'ancienne instance (PID %s) impossible : %s", old_pid, exc)


# Un second lancement de l'EXE (menu Démarrer) ne tue pas l'instance en cours :
# il dépose afficher.flag, que l'on consomme ici pour montrer la fenêtre.
_AFFICHAGE_SONDAGE_MS = 1_000


def _sonder_demande_affichage():
    """Montre la fenêtre si un second lancement l'a demandé, puis se reprogramme."""
    if _watchdog.consommer_demande_affichage():
        _show_window()
    if _root:
        _root.after(_AFFICHAGE_SONDAGE_MS, _sonder_demande_affichage)


def _acquire_single_instance():
    """Tue l'instance MeetNote précédente et écrit notre PID. Retourne True si OK."""
    import psutil
    os.makedirs(os.path.dirname(_LOCK_FILE), exist_ok=True)
    if os.path.isfile(_LOCK_FILE):
        try:
            with open(_LOCK_FILE) as f:
                old_pid = int(f.read().strip())
        except (OSError, ValueError) as exc:
            _logger.warning("Fichier PID illisible (%s) : ignoré", exc)
        else:
            # Jamais notre propre superviseur (EXE figé) : le tuer priverait
            # l'enfant de sa relance, et il porte lui aussi le nom MeetNote.exe.
            if old_pid != os.getppid() and old_pid != os.getpid():
                _terminer_ancienne_instance(psutil, old_pid)
    with open(_LOCK_FILE, "w") as f:
        f.write(str(os.getpid()))
    return True


def _release_single_instance():
    try:
        if os.path.isfile(_LOCK_FILE):
            os.remove(_LOCK_FILE)
    except Exception:
        pass


def _lister_ou_vide(dossier: str) -> list[str]:
    """Noms des fichiers de `dossier`, ou liste vide s'il est absent ou illisible."""
    try:
        return os.listdir(dossier)
    except OSError:
        return []


def _recuperer_enregistrements_interrompus():
    """Remet en file les `rec_*.wav` de tmp laissés par un arrêt brutal.

    Un plantage natif ou le `taskkill` de l'installeur interrompt l'enregistrement
    avant la création du job : l'audio (en-tête recalé toutes les 5 s) reste dans
    tmp, invisible de l'historique. Chaque fichier récupéré devient un job
    ordinaire, donc archivé puis transcrit comme après un arrêt normal (le worker
    retire le WAV de tmp une fois archivé). Un fichier écarté reste en place.
    """
    with _all_jobs_lock:
        chemins = [j.wav_path for j in _all_jobs]
        debuts = [j.start_time for j in _all_jobs]
    if _wav_stream_path:
        chemins.append(_wav_stream_path)  # enregistrement en cours : jamais repris
    archives = _lister_ou_vide(config.AUDIO_ARCHIVE_DIR)
    transcripts = _lister_ou_vide(config.TRANSCRIPT_DIR)
    candidats, ecartes = recup_mod.chercher(config.TEMP_DIR, chemins, debuts, archives,
                                            noms_transcripts=transcripts)
    for ecarte in ecartes:
        _logger.warning("Enregistrement non récupéré, laissé en place : %s (%s)",
                        os.path.basename(ecarte.chemin), ecarte.raison)
    jobs = []
    for cand in candidats:
        job = _Job(
            id=f"{cand.debut.strftime(recup_mod.FORMAT_HORODATAGE)}_recup",
            wav_path=cand.chemin,
            start_time=cand.debut,
            duration_min=cand.duree_s / 60,
            model_name=modeles.modele_affiche(
                user_config.get(modeles.CLE_MODELE_CHOISI, ""), config.WHISPER_MODEL),
            language=config.WHISPER_LANGUAGE,
            meeting_type="A classifier",
            output_mode="notion",
            meeting_name=recup_mod.titre_recupere(cand.debut),
        )
        with _all_jobs_lock:
            _all_jobs.insert(0, job)
        hist_mod.add(job)
        jobs.append(job)
        _logger.info("Enregistrement récupéré : %s (%.1f min)",
                     os.path.basename(cand.chemin), job.duration_min)
    if jobs:
        with _queue_lock:
            _job_queue.extend(jobs)  # du plus ancien au plus récent
        _work_event.set()


def main():
    global _icon, _all_jobs

    _acquire_single_instance()

    # Charger l'historique existant
    entries = hist_mod.load()
    with _all_jobs_lock:
        for rang, e in enumerate(entries):
            try:
                _all_jobs.append(_Job(**hist_mod.champs_job(e)))
            except (ValueError, TypeError) as exc:
                # Ni contenu ni message de l'exception : l'entrée porte des
                # notes et des transcriptions.
                _logger.warning("Historique : entrée n°%d ignorée (%s)",
                                rang, type(exc).__name__)

    # ── Reprise des jobs laissés en plan ────────────────────────────────────
    # La file de travail vit en mémoire : un job « queued » ou « running » au
    # moment d'un crash natif (access violation WASAPI) n'y est plus au
    # redémarrage, alors que son audio est intact sur le disque. Sans cette
    # reprise il faudrait le relancer à la main depuis l'historique.
    with _all_jobs_lock:
        orphelins = [j for j in _all_jobs
                     if j.status_transcript in ("queued", "running")
                     and j.wav_path and os.path.isfile(j.wav_path)]
    if orphelins:
        for job in orphelins:
            job.status_transcript = "queued"
            hist_mod.update(job)
        with _queue_lock:
            # L'historique est du plus récent au plus ancien : on remet dans
            # l'ordre chronologique pour traiter le plus ancien d'abord.
            _job_queue.extend(reversed(orphelins))
        _work_event.set()
        _logger.info("Reprise de %d job(s) interrompu(s) : %s", len(orphelins),
                     ", ".join(j.id for j in orphelins))

    _recuperer_enregistrements_interrompus()

    import sys
    tray_only = "--minimized" in sys.argv

    _icon = pystray.Icon(
        "meetnote", _make_icon(False), "MeetNote — Prêt", menu=_build_menu(),
    )
    _icon.default_action = lambda i, it: _show_window()
    threading.Thread(target=_icon.run, daemon=True).start()
    threading.Thread(target=_worker_loop, daemon=True).start()
    _start_audio_device_monitor()

    # ── Démarrage automatique avec Windows (registre HKCU\Run) ──────────────
    # Mode source : la clé est réenregistrée si elle diffère. EXE figé : le choix
    # appartient à l'installeur et aux paramètres, donc jamais de création ; la
    # clé du poste de développement (watchdog.py) n'est jamais écrasée ; une clé
    # sur un autre MeetNote.exe (ancien emplacement) est recalée sur celui-ci.
    try:
        decision = demarrage_windows.decision_au_demarrage(
            getattr(sys, "frozen", False), _startup_value(), _get_startup_cmd())
        if decision != demarrage_windows.RIEN:
            _logger.info("Démarrage avec Windows : clé %s", decision)
            _set_startup_enabled(True)
    except OSError as e:
        # droit refusé ou registre verrouillé : l'application démarre quand même
        _logger.warning("Démarrage avec Windows : clé non mise à jour (%s)", e)

    root = _build_window()
    if tray_only:
        root.withdraw()   # démarrage silencieux en tray uniquement

    # L'assistant s'ouvre au premier lancement, y compris en --minimized :
    # sans réglages, l'application ne ferait rien d'utile en tray seul.
    if user_config.est_premier_lancement():
        # Le modèle est fixé et téléchargé à la fin de l'assistant (_fin_assistant).
        root.after(500, _ouvrir_assistant)
    else:
        root.after(1_000, _demarrer_modele_initial)

    # ── Planification du digest quotidien ────────────────────────────────────
    # Démarre 3 s après la fenêtre pour laisser tkinter s'initialiser
    root.after(3_000, _schedule_daily_digest)
    root.after(5_000, _check_pending_at_startup)
    root.after(_AFFICHAGE_SONDAGE_MS, _sonder_demande_affichage)

    root.mainloop()
    _release_single_instance()
    os._exit(0)


if __name__ == "__main__":
    main()
