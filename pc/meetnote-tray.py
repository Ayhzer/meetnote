"""
MeetNote Tray — icône systray Windows + fenêtre tkinter
- Panneau principal : enregistrement (activité critique)
- Panel latéral coulissant : historique des enregistrements avec statuts par étape
- Étapes séparées et relançables : audio → transcription → notion
- Intégration Outlook via win32com (lecture du calendrier)
"""
import sys
import os
import threading
import collections
import ctypes
import subprocess
import tkinter as tk
from tkinter import ttk
import pystray
from PIL import Image, ImageDraw
import sounddevice as sd
import soundcard as sc
import numpy as np
import wave
import datetime
import shutil
import dataclasses

sys.path.insert(0, os.path.dirname(__file__))
import config
import user_config
import history as hist_mod
import outlook_cal
from notion_push import push_to_notion

# Surcharge les credentials avec les valeurs utilisateur persistées
_uc = user_config.load()
if _uc.get("notion_token"):
    config.NOTION_TOKEN = _uc["notion_token"]
if _uc.get("notion_database_id"):
    config.NOTION_DATABASE_ID = _uc["notion_database_id"]

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

# ─── State ───────────────────────────────────────────────────────────────────
_rec_gain        = 1.0   # gain numérique appliqué aux samples capturés
_recording       = False
_audio_chunks    = []
_wav_stream      = None
_wav_stream_path = None
_flush_frame_count = 0
_lock            = threading.Lock()
_stream_mic      = None
_loop_thread     = None
_stop_loop       = threading.Event()
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
_log_text        = None
_btn_start       = None
_btn_stop        = None
_btn_cancel      = None
_btn_stop_only   = None
_source_combo    = None
_speaker_frame   = None
_speaker_combo   = None
_rec_start_time  = None

# Fenêtre historique (Toplevel indépendante)
_history_win:       tk.Toplevel | None = None
_history_list_frame: tk.Frame | None   = None

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
    meeting_name:    str = ""
    # Statuts par étape
    status_audio:      str = "done"    # done
    status_transcript: str = "queued"  # queued / running / done / error
    status_notion:     str = "pending" # pending / running / done / error / skipped
    # Résultats
    transcript:      str = ""
    transcript_path: str = ""
    notion_url:      str = ""
    error_msg:       str = ""

_job_queue   = collections.deque()
_queue_lock  = threading.Lock()
_work_event  = threading.Event()
_worker_busy = False

# Tous les jobs chargés (en mémoire, pour l'historique)
_all_jobs: list[_Job] = []
_all_jobs_lock = threading.Lock()

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
        from scipy.signal import resample_poly
        from math import gcd
        g = gcd(src, dst)
        return resample_poly(data, dst // g, src // g).astype(np.float32)
    except Exception:
        new_len = int(len(data) * dst / src)
        return np.interp(
            np.linspace(0, len(data) - 1, new_len),
            np.arange(len(data)),
            data,
        ).astype(np.float32)


# ─── Débruitage (noisereduce) ─────────────────────────────────────────────────
_nr_noise_profile: np.ndarray | None = None  # profil de bruit estimé au démarrage
_nr_calibrated    = False
_nr_lock          = threading.Lock()

def _denoise(samples: np.ndarray) -> np.ndarray:
    """Applique noisereduce si disponible. Silencieux en cas d'erreur."""
    global _nr_noise_profile, _nr_calibrated
    try:
        import noisereduce as nr
        with _nr_lock:
            if not _nr_calibrated:
                # Première passe : utiliser les samples courants comme profil de bruit
                _nr_noise_profile = samples.copy()
                _nr_calibrated = True
            reduced = nr.reduce_noise(
                y=samples,
                sr=config.SAMPLE_RATE,
                y_noise=_nr_noise_profile,
                prop_decrease=0.75,   # 75% de réduction — équilibre qualité/artefacts
                stationary=True,
            )
        return reduced.astype(np.float32)
    except Exception:
        return samples


# ─── Level update ────────────────────────────────────────────────────────────
def _push_level(mono: np.ndarray):
    rms = float(np.sqrt(np.mean(mono ** 2))) * 100
    if _level_var and _root:
        _root.after(0, lambda v=min(rms * 3, 100): _level_var.set(v))


# ─── Audio buffer ────────────────────────────────────────────────────────────
def _append_audio(samples: np.ndarray):
    global _flush_frame_count
    if _rec_gain != 1.0:
        samples = np.clip(samples * _rec_gain, -1.0, 1.0)
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


# ─── soundcard loopback thread ───────────────────────────────────────────────
def _loopback_thread_fn(mic_also: bool):
    ctypes.windll.ole32.CoInitialize(None)
    spk_name = _speaker_var.get() if _speaker_var else None
    if spk_name:
        spk = next((s for s in sc.all_speakers() if s.name == spk_name), sc.default_speaker())
    else:
        spk = sc.default_speaker()
    loopback = sc.get_microphone(spk.id, include_loopback=True)
    mic_dev  = sc.default_microphone() if mic_also else None
    loop_rate = config.SAMPLE_RATE

    with loopback.recorder(samplerate=loop_rate, channels=1, blocksize=CHUNK_FRAMES) as loop_rec:
        if mic_also and mic_dev:
            with mic_dev.recorder(samplerate=loop_rate, channels=1, blocksize=CHUNK_FRAMES) as mic_rec:
                while not _stop_loop.is_set():
                    loop_chunk = loop_rec.record(numframes=CHUNK_FRAMES)[:, 0]
                    mic_chunk  = mic_rec.record(numframes=CHUNK_FRAMES)[:, 0]
                    if len(mic_chunk) != len(loop_chunk):
                        mic_chunk = np.resize(mic_chunk, len(loop_chunk))
                    mixed = np.clip(mic_chunk * 0.6 + loop_chunk * 0.4, -1.0, 1.0)
                    gained = np.clip(mixed * _rec_gain, -1.0, 1.0) if _rec_gain != 1.0 else mixed
                    with _lock:
                        if _recording:
                            _append_audio(mixed)  # gain appliqué dans _append_audio
                    _push_level(gained)
        else:
            while not _stop_loop.is_set():
                chunk = loop_rec.record(numframes=CHUNK_FRAMES)[:, 0]
                gained = np.clip(chunk * _rec_gain, -1.0, 1.0) if _rec_gain != 1.0 else chunk
                with _lock:
                    if _recording:
                        _append_audio(chunk)  # gain appliqué dans _append_audio
                _push_level(gained)


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
def _archive_audio(wav_path: str, start_time: datetime.datetime) -> str | None:
    from notion_push import _find_ffmpeg
    try:
        os.makedirs(config.AUDIO_ARCHIVE_DIR, exist_ok=True)
        ts = start_time.strftime("%Y%m%d_%H%M%S")
        ffmpeg = _find_ffmpeg()
        if ffmpeg:
            dest = os.path.join(config.AUDIO_ARCHIVE_DIR, f"meetnote_{ts}.opus")
            cmd = [ffmpeg, "-y", "-i", wav_path,
                   "-c:a", "libopus", "-b:a", "24k", "-ac", "1", "-ar", "16000", dest]
            result = subprocess.run(cmd, capture_output=True, timeout=300)
            if result.returncode == 0:
                return dest
        dest = os.path.join(config.AUDIO_ARCHIVE_DIR, f"meetnote_{ts}.wav")
        shutil.copy2(wav_path, dest)
        return dest
    except Exception as e:
        _log_error(f"Archive audio : {e}")
        return None


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
            f.write(job.transcript)
        return path
    except Exception as e:
        _log_error(f"Sauvegarde transcript : {e}")
        return None


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

    try:
        path = job.wav_path
        # Si l'audio archivé est opus, on doit utiliser le WAV temp s'il existe encore
        # (normalement le WAV temp est conservé en cas d'erreur, sinon archivé)
        if not os.path.isfile(path):
            raise FileNotFoundError(f"Fichier audio introuvable : {path}")

        language = None if job.language == "auto" else job.language

        if _whisper_model is None or _whisper_model_name != job.model_name:
            _set_progress(5)
            _set_status(f"Chargement modèle {job.model_name}…")
            if getattr(sys, "frozen", False):
                bundle_dir   = sys._MEIPASS
                embedded     = os.path.join(bundle_dir, "faster_whisper_models", job.model_name)
                exe_dir      = os.path.dirname(sys.executable)
                local_models = os.path.join(exe_dir, "models", job.model_name)
                if os.path.exists(embedded):
                    model_path = embedded
                elif os.path.exists(local_models):
                    model_path = local_models
                else:
                    _set_status(f"Téléchargement modèle {job.model_name}…")
                    from huggingface_hub import snapshot_download
                    os.makedirs(local_models, exist_ok=True)
                    snapshot_download(repo_id=f"Systran/faster-whisper-{job.model_name}",
                                      local_dir=local_models)
                    model_path = local_models
            else:
                model_path = job.model_name
            _whisper_model      = WhisperModel(model_path, device="cpu", compute_type="int8")
            _whisper_model_name = job.model_name

        _set_progress(20)

        if language is None:
            _, detect_info = _whisper_model.transcribe(path, language=None, beam_size=1,
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
        all_lines = []

        for i, seg_path in enumerate(seg_paths):
            if n_segs > 1:
                _set_status(f"Transcription segment {i+1}/{n_segs} [{lang_label}]…")
            seg_segments, seg_info = _whisper_model.transcribe(
                seg_path,
                language=detected,
                task=task,
                beam_size=5,
                vad_filter=True,
                vad_parameters={"min_silence_duration_ms": 500},
                condition_on_previous_text=False,
                no_speech_threshold=0.6,
                temperature=0,
            )
            seg_total = max(seg_info.duration, 1)
            # Offset temporel si plusieurs segments (N * segment_min * 60s)
            time_offset = i * config.TRANSCRIPTION_SEGMENT_MIN * 60
            for seg in seg_segments:
                text = seg.text.strip()
                if not text:
                    continue
                t = int(seg.start) + time_offset
                ts = f"[{t // 3600:02d}:{(t % 3600) // 60:02d}:{t % 60:02d}]"
                all_lines.append(f"{ts} {text}")
                frac = seg.end / seg_total / n_segs
                _set_progress(min(20 + int((i / n_segs + frac) * 65), 85))
            if seg_path != path:
                try: os.remove(seg_path)
                except OSError: pass

        job.transcript = "\n".join(all_lines)
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


def _do_step_notion(job: _Job) -> bool:
    """Pousse vers Notion. Met à jour job.notion_url + job.status_notion."""
    job.status_notion = "running"
    hist_mod.update(job)
    _refresh_ui()
    _set_status("Envoi vers Notion…")

    try:
        title = job.meeting_name or f"Réunion {job.start_time.strftime('%Y-%m-%d %H:%M')}"
        page = push_to_notion(
            job.transcript,
            source="PC",
            duration_min=job.duration_min,
            whisper_model=job.model_name,
            meeting_type=job.meeting_type,
            start_time=job.start_time,
            audio_path=job.wav_path,
            title_override=title,
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
    wav_tmp = job.wav_path  # chemin WAV temporaire avant archivage

    try:
        # 1. Archive audio locale
        _set_status("Archivage audio…")
        archived = _archive_audio(wav_tmp, job.start_time)
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


# ─── Worker ──────────────────────────────────────────────────────────────────
def _worker_loop():
    global _worker_busy
    while True:
        _work_event.wait()
        _work_event.clear()
        while True:
            with _queue_lock:
                if not _job_queue:
                    break
                job = _job_queue.popleft()
            _worker_busy = True
            _prevent_sleep()
            try:
                _process_job(job)
            finally:
                _allow_sleep()
                _worker_busy = False
        # Queue vide — forcer le retour à Idle
        _refresh_ui()


# ─── Relance d'étapes depuis l'historique ────────────────────────────────────
def _requeue_transcribe(job: _Job):
    """Relance la transcription (+ notion si pertinent) depuis le panel historique."""
    job.status_transcript = "queued"
    job.transcript = ""
    job.transcript_path = ""
    if job.output_mode != "fichier":
        job.status_notion = "pending"
    hist_mod.update(job)
    with _queue_lock:
        _job_queue.appendleft(job)  # priorité haute
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


# ─── Recording actions ────────────────────────────────────────────────────────
_SOURCE_MAP = {
    "🎤  Microphone uniquement": "micro",
    "🔊  Loopback (son du PC)":  "loopback",
    "🎤🔊  Mixte (micro + PC)":  "mixte",
}


def _do_start():
    global _recording, _audio_chunks, _stream_mic, _loop_thread, _rec_start_time
    global _wav_stream, _wav_stream_path, _flush_frame_count

    if _recording:
        return

    source = _SOURCE_MAP.get(_source_var.get(), "micro")
    _recording       = True
    _audio_chunks    = []
    _flush_frame_count = 0
    _rec_start_time  = datetime.datetime.now()
    _stop_loop.clear()
    _prevent_sleep()

    # Pré-remplir le nom de la réunion depuis Outlook
    if _meeting_name_var and not _meeting_name_var.get().strip():
        def _fetch_outlook():
            mtg = outlook_cal.get_current_or_next_meeting(window_minutes=30)
            if mtg and _meeting_name_var and _root:
                _root.after(0, lambda: _meeting_name_var.set(mtg["subject"]))
        threading.Thread(target=_fetch_outlook, daemon=True).start()

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

    _refresh_ui()


def _do_stop_transcribe():
    global _recording, _stream_mic

    if not _recording:
        return

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

    if not path:
        _allow_sleep()
        _set_status("Aucun audio enregistré.")
        _refresh_ui()
        return

    meeting_name = (_meeting_name_var.get().strip() if _meeting_name_var else "") or ""

    job = _Job(
        id=f"{stop_time.strftime('%Y%m%d_%H%M%S')}",
        wav_path=path,
        start_time=start_time,
        duration_min=duration_min,
        model_name=_model_var.get() if _model_var else config.WHISPER_MODEL,
        language=_lang_var.get() if _lang_var else config.WHISPER_LANGUAGE,
        meeting_type=(_type_var.get() if _type_var and _type_var.get() != "—" else ""),
        output_mode=_output_var.get() if _output_var else "notion",
        meeting_name=meeting_name,
    )

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
    global _recording, _stream_mic

    if not _recording:
        return

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

    _allow_sleep()

    if not path:
        _set_status("Aucun audio enregistré.")
        _refresh_ui()
        return

    meeting_name = (_meeting_name_var.get().strip() if _meeting_name_var else "") or ""

    job = _Job(
        id=f"{stop_time.strftime('%Y%m%d_%H%M%S')}",
        wav_path=path,
        start_time=start_time,
        duration_min=duration_min,
        model_name=_model_var.get() if _model_var else config.WHISPER_MODEL,
        language=_lang_var.get() if _lang_var else config.WHISPER_LANGUAGE,
        meeting_type=(_type_var.get() if _type_var and _type_var.get() != "—" else ""),
        output_mode=_output_var.get() if _output_var else "notion",
        meeting_name=meeting_name,
        status_transcript="queued",
        status_notion="pending",
    )

    with _all_jobs_lock:
        _all_jobs.insert(0, job)

    # Archiver l'audio en arrière-plan, sans mettre en file de transcription
    def _archive_only():
        _prevent_sleep()
        try:
            _set_status("Archivage audio…")
            archived = _archive_audio(path, start_time)
            if archived:
                job.wav_path = archived
            hist_mod.add(job)
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

    _set_status("Annulé.")
    _refresh_ui()


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
        _btn_start.config(state="disabled", bg="#4a1515")
        _btn_stop.config(state="normal", bg="#2e7d32", fg="white")
        _btn_stop_only.config(state="normal", bg="#5533aa", fg="white")
        _btn_cancel.config(state="normal", fg="#ff6666")
        _source_combo.config(state="disabled")
        if _speaker_combo: _speaker_combo.config(state="disabled")
        _status_var.set("Recording in progress…")
        if _status_dot:
            _status_dot.config(bg="#88d982")
        if _root and hasattr(_root, "_toggle_whisper"):
            _root._toggle_whisper(False)
    else:
        _btn_start.config(state="normal", bg="#b81c1c")
        _btn_stop.config(state="disabled", bg="#1e1e32", fg="#ab8985")
        _btn_stop_only.config(state="disabled", bg="#1e1e32", fg="#ab8985")
        _btn_cancel.config(state="disabled", fg="#ab8985")
        _source_combo.config(state="readonly")
        if _speaker_combo: _speaker_combo.config(state="readonly")
        if _root and hasattr(_root, "_toggle_whisper"):
            _root._toggle_whisper(True)
        with _queue_lock:
            pending = len(_job_queue) + (1 if _worker_busy else 0)
        if pending > 0:
            if _status_var: _status_var.set(f"Processing {pending} job(s)…")
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
        _root.after(0, lambda: (
            _root.deiconify(), _root.lift(), _root.focus_force(),
        ))

def _hide_window():
    if _root:
        _root.withdraw()


# ─── Tray menu ────────────────────────────────────────────────────────────────
def _build_menu():
    # Statut dynamique
    if _recording:
        status_label = "● Enregistrement en cours…"
    elif _job_queue or _worker_busy:
        n = len(_job_queue) + (1 if _worker_busy else 0)
        status_label = f"⏳ Traitement ({n} job(s))"
    else:
        status_label = "○ Prêt"

    return pystray.Menu(
        pystray.MenuItem(status_label, lambda i, it: None, enabled=False),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Ouvrir",    lambda i, it: _show_window(), default=True),
        pystray.Menu.SEPARATOR,
        # ── Actions enregistrement
        pystray.MenuItem("▶  Démarrer",               lambda i, it: _do_start(),            enabled=not _recording),
        pystray.MenuItem("⏹  Arrêter et transcrire",  lambda i, it: _do_stop_transcribe(),  enabled=_recording),
        pystray.MenuItem("⏸  Arrêter sans transcrire",lambda i, it: _do_stop_archive_only(),enabled=_recording),
        pystray.MenuItem("✕  Annuler",                lambda i, it: _do_cancel(),           enabled=_recording),
        pystray.Menu.SEPARATOR,
        # ── Historique & fichiers
        pystray.MenuItem("📋  Historique",             lambda i, it: _toggle_history_window()),
        pystray.MenuItem("📂  Importer un fichier audio", lambda i, it: _import_audio_file()),
        pystray.Menu.SEPARATOR,
        # ── Dossiers
        pystray.MenuItem("🎙  Dossier Audio",          lambda i, it: _open_audio_dir_tray()),
        pystray.MenuItem("📄  Dossier Transcripts",    lambda i, it: _open_transcript_dir_tray()),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("⚙  Paramètres",             lambda i, it: _open_settings()),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quitter", _quit_app),
    )

def _open_audio_dir_tray():
    os.makedirs(config.AUDIO_ARCHIVE_DIR, exist_ok=True)
    subprocess.Popen(f'explorer "{config.AUDIO_ARCHIVE_DIR}"')

def _open_transcript_dir_tray():
    os.makedirs(config.TRANSCRIPT_DIR, exist_ok=True)
    subprocess.Popen(f'explorer "{config.TRANSCRIPT_DIR}"')

def _quit_app(icon=None, item=None):
    if _recording:
        _do_cancel()
    else:
        _stop_loop.set()
    def _shutdown():
        if _root:
            _root.destroy()
    if _root:
        _root.after(0, _shutdown)
    def _stop_icon():
        if _icon:
            _icon.stop()
    threading.Thread(target=_stop_icon, daemon=True).start()


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
    GRN     = "#2e7d32"
    DIM     = "#333355"

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
        title  = job.meeting_name or f"Meeting {job.start_time.strftime('%Y-%m-%d %H:%M')}"
        dur    = f"{job.duration_min:.0f} min"

        tk.Label(top_row, text=dt_str, font=("Consolas", 7),
                 bg=S_CARD, fg=FG3, anchor="w").pack(side="left")

        # Badge durée (droite)
        dur_badge = tk.Label(top_row, text=dur, font=("Segoe UI", 7),
                             bg=S_HIGH, fg=FG3, padx=6, pady=1)
        dur_badge.pack(side="right")

        # Titre réunion
        title_row = tk.Frame(card, bg=S_CARD)
        title_row.pack(fill="x", padx=10, pady=(0, 6))
        tk.Label(title_row, text=title, font=("Segoe UI", 9, "bold"),
                 bg=S_CARD, fg=FG, anchor="w", wraplength=380).pack(fill="x")

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

        # ── Boutons d'action contextuels
        j = job
        can_transcribe = os.path.isfile(j.wav_path)
        has_btn = (
            can_transcribe or
            (j.status_transcript == "done" and j.status_notion in ("pending", "error") and j.output_mode != "fichier") or
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

            # Transcription : combo modèle + bouton (dispo si fichier audio présent)
            if can_transcribe:
                model_var_h = tk.StringVar(value=j.model_name)
                model_combo_h = ttk.Combobox(btn_row, textvariable=model_var_h,
                                             state="readonly", width=9,
                                             values=["tiny", "base", "small", "medium", "large-v3"],
                                             font=("Segoe UI", 7))
                model_combo_h.pack(side="left", padx=(0, 4))

                def _do_retranscribe(_j=j, _mv=model_var_h):
                    _j.model_name = _mv.get()
                    _requeue_transcribe(_j)
                    _refresh_history_panel()

                lbl = "📝 Transcribe" if j.status_transcript in ("queued", "error") else "🔄 Re-transcribe"
                _ghost_btn(btn_row, lbl, "#ffb3ac", _do_retranscribe)

            if j.status_transcript == "done" and j.status_notion in ("pending", "error") and j.output_mode != "fichier":
                _ghost_btn(btn_row, "→ Notion", "#88d982",
                           lambda _j=j: (_requeue_notion(_j), _refresh_history_panel()))

            if j.transcript_path and os.path.isfile(j.transcript_path):
                _ghost_btn(btn_row, "📄 View txt", FG2,
                           lambda p=j.transcript_path: subprocess.Popen(f'notepad "{p}"'))

            if j.notion_url:
                _ghost_btn(btn_row, "🌐 Open Notion", "#e4b5ff",
                           lambda u=j.notion_url: subprocess.Popen(f'start "" "{u}"', shell=True))
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

    if _history_win and _history_win.winfo_exists():
        _history_win.destroy()
        _history_win = None
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
             bg=S_MAIN, fg=FG).pack(side="left", padx=14, pady=(0, 10))
    tk.Button(hdr2, text="↺ Refresh", font=("Segoe UI", 8),
              bg=S_MAIN, fg=FG3, activebackground=S_CARD,
              activeforeground=FG, relief="flat", bd=0, cursor="hand2",
              command=_refresh_history_panel).pack(side="right", padx=(0, 14), pady=(0, 10))
    tk.Button(hdr2, text="⊕ Import audio", font=("Segoe UI", 8),
              bg=RED, fg="white", activebackground="#ff4444",
              activeforeground="white", relief="flat", bd=0, cursor="hand2",
              command=_import_audio_file).pack(side="right", padx=(0, 4), pady=(0, 10))

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
    global _meeting_name_var, _history_list_frame, _status_dot

    _root = tk.Tk()
    _root.title("MeetNote")
    _root.resizable(False, False)
    _root.configure(bg="#111125")
    _root.protocol("WM_DELETE_WINDOW", _hide_window)
    _root.bind("<Unmap>", lambda e: _hide_window() if _root.state() == "iconic" else None)

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
    GRN     = "#2e7d32"
    PURPLE  = "#5533aa"
    ERR     = "#ff4444"
    BG2     = S_CARD      # alias pour compatibilité _open_settings
    DIM     = "#333355"
    P       = 12

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

    # Quit en bas
    tk.Frame(nav_frame, bg=S_MAIN).pack(fill="both", expand=True)
    tk.Frame(nav_frame, bg=S_HIGH, height=1).pack(fill="x")
    quit_row = tk.Frame(nav_frame, bg=S_MAIN, cursor="hand2")
    quit_row.pack(fill="x")
    tk.Label(quit_row, text="⏻  Quit", font=("Segoe UI", 9),
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
    tk.Button(hdr_btns, text="⏻", font=("Segoe UI", 10),
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
    left_col.pack(side="left", fill="y", padx=(10, 4), pady=10)
    left_col.pack_propagate(False)

    right_col = tk.Frame(body, bg=S_BASE, width=RIGHT_W)
    right_col.pack(side="left", fill="y", padx=(4, 10), pady=10)
    right_col.pack_propagate(False)

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
    speakers     = [s.name for s in sc.all_speakers()]
    _speaker_var = tk.StringVar(value=sc.default_speaker().name)
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
    _lbl(rm, "Meeting name (auto-filled from Outlook calendar)")
    mtg_entry = tk.Entry(rm, textvariable=_meeting_name_var,
                         font=("Segoe UI", 9), bg=S_HIGH, fg=FG,
                         insertbackground=FG, relief="flat", bd=0,
                         highlightthickness=1, highlightbackground=S_TOP,
                         highlightcolor=RED)
    mtg_entry.pack(fill="x", pady=(2, 0), ipady=5)

    rt = _row(sec_mtg)
    _lbl(rt, "Meeting type")
    _type_var = tk.StringVar(value="—")
    type_combo = _combo(rt, _type_var,
                        ["—", "Gouvernance", "Technique", "Projet", "RH", "Fournisseur", "Autre"])
    _spacer(sec_mtg)

    # ════════════════════════
    # SECTION : TRANSCRIPTION
    # ════════════════════════
    sec_w = _section("📝  TRANSCRIPTION")

    mc, lc = _row(sec_w, cols=2)

    _lbl(mc, "Whisper Model")
    _model_var = tk.StringVar(value=config.WHISPER_MODEL)
    model_combo = _combo(mc, _model_var, ["tiny", "base", "small", "medium", "large-v3"])
    _MODEL_HINTS = {
        "tiny":    "Fastest — lower accuracy",
        "base":    "Fast — decent accuracy",
        "small":   "Balanced",
        "medium":  "High quality — slower",
        "large-v3":"Best quality — needs GPU",
    }
    _model_hint_var = tk.StringVar(value=_MODEL_HINTS.get(config.WHISPER_MODEL, ""))
    _hint(mc, _model_hint_var)
    _model_var.trace_add("write", lambda *_: _model_hint_var.set(_MODEL_HINTS.get(_model_var.get(), "")))

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

    _gain_label = tk.Label(rg, text="1.0x", font=("Consolas", 7),
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
    gain_slider.set(1.0)
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

    _btn_start = tk.Button(
        btns_frame, text="⏺  START RECORDING",
        font=("Segoe UI", 11, "bold"),
        bg=RED_DIM, fg="white",
        activebackground=RED, activeforeground="white",
        relief="flat", bd=0, pady=12, cursor="hand2",
        command=_do_start,
    )
    _btn_start.pack(fill="x", pady=(0, 4))

    _btn_stop = tk.Button(
        btns_frame, text="⏹  Stop and transcribe",
        font=("Segoe UI", 9),
        bg=S_CARD, fg=FG3,
        activebackground=GRN, activeforeground="white",
        relief="flat", bd=0, pady=8, cursor="hand2",
        state="disabled", command=_do_stop_transcribe,
    )
    _btn_stop.pack(fill="x", pady=(0, 4))

    _btn_stop_only = tk.Button(
        btns_frame, text="⏸  Stop without transcribing",
        font=("Segoe UI", 9),
        bg=S_CARD, fg=FG3,
        activebackground=PURPLE, activeforeground="white",
        relief="flat", bd=0, pady=8, cursor="hand2",
        state="disabled", command=_do_stop_archive_only,
    )
    _btn_stop_only.pack(fill="x", pady=(0, 4))

    _btn_cancel = tk.Button(
        btns_frame, text="✕  Cancel",
        font=("Segoe UI", 9),
        bg=S_CARD, fg=FG3,
        activebackground=S_HIGH, activeforeground=ERR,
        relief="flat", bd=0, pady=8, cursor="hand2",
        state="disabled", command=_do_cancel,
    )
    _btn_cancel.pack(fill="x")

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

    # ── System Console (log)
    console_hdr = tk.Frame(right_col, bg=S_BASE)
    console_hdr.pack(fill="x", pady=(0, 2))
    tk.Label(console_hdr, text="▶  SYSTEM CONSOLE", font=("Segoe UI", 7, "bold"),
             bg=S_BASE, fg=FG3).pack(side="left")

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
    import builtins
    _root._log_fn = _log_with_tags

    # Message d'initialisation
    _log_with_tags("MeetNote Engine initialized.")
    _log_with_tags("Audio backend ready (WASAPI).")

    # ── Polling Outlook : pré-remplir le champ réunion toutes les 60s
    def _poll_outlook():
        if not _recording and _meeting_name_var:
            def _fetch():
                mtg = outlook_cal.get_current_or_next_meeting(window_minutes=30)
                if mtg and _meeting_name_var and _root:
                    subject = mtg["subject"]
                    def _set():
                        # Ne pas écraser une valeur saisie manuellement
                        current = _meeting_name_var.get().strip()
                        if not current or current == _meeting_name_var._last_outlook:
                            _meeting_name_var.set(subject)
                            _meeting_name_var._last_outlook = subject
                    _root.after(0, _set)
            threading.Thread(target=_fetch, daemon=True).start()
        if _root:
            _root.after(60_000, _poll_outlook)

    _meeting_name_var._last_outlook = ""
    _root.after(1_000, _poll_outlook)  # premier appel 1s après démarrage

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
        meeting_type=mtype if mtype != "—" else "",
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
    act_general = _make_tab("general", "  Général  ")
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
            config.NOTION_TOKEN       = new_token
            config.NOTION_DATABASE_ID = new_db
            import notion_push as np_mod
            np_mod.HEADERS["Authorization"] = f"Bearer {new_token}"
            status_lbl.config(text="✓ Configuration sauvegardée.", fg="#00cc66")
        except Exception as e:
            status_lbl.config(text=f"Erreur : {e}", fg=ERR)

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

    # ── Footer boutons
    footer = tk.Frame(win, bg=S_MAIN)
    footer.pack(fill="x", side="bottom")
    tk.Button(footer, text="Discard", font=("Segoe UI", 9),
              bg=S_MAIN, fg=ERR, activebackground=S_CARD, activeforeground=ERR,
              relief="flat", bd=0, pady=9, cursor="hand2",
              command=win.destroy).pack(side="left", padx=16, pady=10)
    tk.Button(footer, text="SAVE CONFIGURATION", font=("Segoe UI", 9, "bold"),
              bg=RED_DIM, fg="white", activebackground=RED, activeforeground="white",
              relief="flat", bd=0, pady=9, cursor="hand2",
              command=_save_notion).pack(side="right", padx=16, pady=10, ipadx=12)

    # Activer onglet Notion par défaut
    act_notion()

    win.update_idletasks()
    wx = _root.winfo_x() + (_root.winfo_width()  - win.winfo_reqwidth())  // 2
    wy = _root.winfo_y() + (_root.winfo_height() - win.winfo_reqheight()) // 2
    win.geometry(f"+{wx}+{wy}")


# ─── Main ─────────────────────────────────────────────────────────────────────
_LOCK_FILE = os.path.join(os.path.expanduser("~"), "AppData", "Roaming",
                          "MeetNote", "meetnote.pid")

def _acquire_single_instance():
    """Tue toute instance précédente et écrit notre PID. Retourne True si OK."""
    import psutil
    os.makedirs(os.path.dirname(_LOCK_FILE), exist_ok=True)
    if os.path.isfile(_LOCK_FILE):
        try:
            old_pid = int(open(_LOCK_FILE).read().strip())
            if psutil.pid_exists(old_pid):
                proc = psutil.Process(old_pid)
                # Vérifier que c'est bien meetnote
                if "python" in proc.name().lower() or "meetnote" in proc.name().lower():
                    proc.terminate()
                    try:
                        proc.wait(timeout=3)
                    except psutil.TimeoutExpired:
                        proc.kill()
        except Exception:
            pass
    with open(_LOCK_FILE, "w") as f:
        f.write(str(os.getpid()))


def _release_single_instance():
    try:
        if os.path.isfile(_LOCK_FILE):
            os.remove(_LOCK_FILE)
    except Exception:
        pass


def main():
    global _icon, _all_jobs

    _acquire_single_instance()

    # Charger l'historique existant
    entries = hist_mod.load()
    with _all_jobs_lock:
        for e in entries:
            try:
                st = e.get("start_time", "")
                if isinstance(st, str) and st:
                    start = datetime.datetime.fromisoformat(st)
                else:
                    start = datetime.datetime.now()
                job = _Job(
                    id=e.get("id", ""),
                    wav_path=e.get("wav_path", ""),
                    start_time=start,
                    duration_min=e.get("duration_min", 0),
                    model_name=e.get("model_name", "base"),
                    language=e.get("language", "fr"),
                    meeting_type=e.get("meeting_type", ""),
                    output_mode=e.get("output_mode", "notion"),
                    meeting_name=e.get("meeting_name", ""),
                    status_audio=e.get("status_audio", "done"),
                    status_transcript=e.get("status_transcript", "done"),
                    status_notion=e.get("status_notion", "done"),
                    transcript=e.get("transcript", ""),
                    transcript_path=e.get("transcript_path", ""),
                    notion_url=e.get("notion_url", ""),
                    error_msg=e.get("error_msg", ""),
                )
                _all_jobs.append(job)
            except Exception:
                pass

    _icon = pystray.Icon(
        "meetnote", _make_icon(False), "MeetNote — Prêt", menu=_build_menu(),
    )
    _icon.default_action = lambda i, it: _show_window()
    threading.Thread(target=_icon.run, daemon=True).start()
    threading.Thread(target=_worker_loop, daemon=True).start()

    root = _build_window()
    root.mainloop()
    _release_single_instance()
    os._exit(0)


if __name__ == "__main__":
    main()
