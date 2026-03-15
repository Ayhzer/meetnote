"""
MeetNote Tray — icône systray Windows + fenêtre tkinter
- Sélecteur source : Micro (sounddevice) / Loopback (soundcard) / Mixte
- VU-mètre, barre progression transcription, journal erreurs
"""
import sys
import os
import threading
import ctypes
import tkinter as tk
from tkinter import ttk
import pystray
from PIL import Image, ImageDraw
import sounddevice as sd
import soundcard as sc
import numpy as np
import wave
import datetime

sys.path.insert(0, os.path.dirname(__file__))
import config
from notion_push import push_to_notion

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
_recording     = False
_audio_chunks  = []
_lock          = threading.Lock()
_stream_mic    = None
_loop_thread   = None
_stop_loop     = threading.Event()
_icon          = None
_root          = None
_level_var     = None
_progress_var  = None
_status_var    = None
_source_var    = None
_speaker_var   = None
_model_var     = None
_lang_var      = None
_type_var      = None   # type de réunion
_output_var    = None   # "notion" | "fichier"
_log_text      = None
_btn_start     = None
_btn_stop      = None
_btn_cancel    = None
_source_combo  = None
_speaker_frame = None
_speaker_combo = None
_rec_start_time = None  # datetime de début d'enregistrement

CHUNK_FRAMES   = 1024        # frames par bloc soundcard


# ─── Whisper ─────────────────────────────────────────────────────────────────
_whisper_model      = None
_whisper_model_name = None  # nom du modèle actuellement chargé

def _transcribe(path: str, progress_cb=None) -> str:
    global _whisper_model, _whisper_model_name
    from faster_whisper import WhisperModel

    # Modèle et langue depuis l'UI (ou config par défaut)
    model_name = _model_var.get() if _model_var else config.WHISPER_MODEL
    lang_val   = _lang_var.get()  if _lang_var  else config.WHISPER_LANGUAGE
    language   = None if lang_val == "auto" else lang_val

    # Recharge le modèle si changement
    if _whisper_model is None or _whisper_model_name != model_name:
        if progress_cb: progress_cb(5)
        _set_status(f"Chargement modèle {model_name}…")
        import sys
        if getattr(sys, 'frozen', False):
            # Running from PyInstaller bundle — use embedded models
            bundle_dir = sys._MEIPASS
            model_path = os.path.join(bundle_dir, "faster_whisper_models", model_name)
            if not os.path.exists(model_path):
                model_path = model_name  # fallback
        else:
            model_path = model_name
        _whisper_model      = WhisperModel(model_path, device="cpu", compute_type="int8")
        _whisper_model_name = model_name

    if progress_cb: progress_cb(20)

    # Première passe rapide pour détecter la langue si mode auto
    if language is None:
        _, detect_info = _whisper_model.transcribe(path, language=None, beam_size=1,
                                                    vad_filter=True, temperature=0,
                                                    max_new_tokens=1)
        detected = detect_info.language
    else:
        detected = language

    # Si la langue n'est pas le français → traduction vers l'anglais
    task = "transcribe" if detected == "fr" else "translate"
    lang_label = detected.upper()
    _set_status(f"Transcription [{lang_label}{'→EN' if task == 'translate' else ''}]…")

    segments, info = _whisper_model.transcribe(
        path,
        language=detected,
        task=task,
        beam_size=10,
        vad_filter=True,
        condition_on_previous_text=True,
        temperature=0,
    )

    lines = []
    total = max(info.duration, 1)
    for seg in segments:
        lines.append(seg.text.strip())
        if progress_cb:
            progress_cb(min(20 + int((seg.end / total) * 65), 85))
    return "\n".join(l for l in lines if l)


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
    new_len = int(len(data) * dst / src)
    return np.interp(
        np.linspace(0, len(data) - 1, new_len),
        np.arange(len(data)),
        data,
    ).astype(np.float32)


# ─── Level update ────────────────────────────────────────────────────────────
def _push_level(mono: np.ndarray):
    rms = float(np.sqrt(np.mean(mono ** 2))) * 100
    if _level_var and _root:
        _root.after(0, lambda v=min(rms * 3, 100): _level_var.set(v))


# ─── sounddevice callback (micro) ────────────────────────────────────────────
def _mic_callback(indata, frames, time, status):
    mono = indata[:, 0]
    # Détecte le sample rate natif du device via config ou sounddevice
    src_rate = int(sd.query_devices(sd.default.device[0])["default_samplerate"])
    resampled = _resample(mono, src_rate, config.SAMPLE_RATE)
    with _lock:
        if _recording:
            _audio_chunks.append(resampled.copy())
    _push_level(resampled)


# ─── soundcard loopback thread ───────────────────────────────────────────────
def _loopback_thread_fn(mic_also: bool):
    """Capture le loopback (+ micro si mixte) via soundcard."""
    ctypes.windll.ole32.CoInitialize(None)
    # Utilise le speaker sélectionné dans l'UI, sinon celui par défaut
    spk_name = _speaker_var.get() if _speaker_var else None
    if spk_name:
        spk = next((s for s in sc.all_speakers() if s.name == spk_name), sc.default_speaker())
    else:
        spk = sc.default_speaker()
    loopback = sc.get_microphone(spk.id, include_loopback=True)
    mic_dev     = sc.default_microphone() if mic_also else None

    loop_rate = config.SAMPLE_RATE  # soundcard accepte n'importe quel rate

    with loopback.recorder(samplerate=loop_rate, channels=1, blocksize=CHUNK_FRAMES) as loop_rec:
        if mic_also and mic_dev:
            with mic_dev.recorder(samplerate=loop_rate, channels=1, blocksize=CHUNK_FRAMES) as mic_rec:
                while not _stop_loop.is_set():
                    loop_chunk = loop_rec.record(numframes=CHUNK_FRAMES)[:, 0]
                    mic_chunk  = mic_rec.record(numframes=CHUNK_FRAMES)[:, 0]
                    if len(mic_chunk) != len(loop_chunk):
                        mic_chunk = np.resize(mic_chunk, len(loop_chunk))
                    mixed = np.clip((loop_chunk + mic_chunk) * 0.5, -1.0, 1.0)
                    with _lock:
                        if _recording:
                            _audio_chunks.append(mixed.copy())
                    _push_level(mixed)
        else:
            while not _stop_loop.is_set():
                chunk = loop_rec.record(numframes=CHUNK_FRAMES)[:, 0]
                with _lock:
                    if _recording:
                        _audio_chunks.append(chunk.copy())
                _push_level(chunk)


# ─── Save audio ──────────────────────────────────────────────────────────────
def _save_audio_to_tempfile() -> str | None:
    with _lock:
        chunks = list(_audio_chunks)
    if not chunks:
        return None
    os.makedirs(config.TEMP_DIR, exist_ok=True)
    ts    = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    path  = os.path.join(config.TEMP_DIR, f"rec_{ts}.wav")
    audio = np.concatenate(chunks, axis=0)
    audio = np.clip(audio, -1.0, 1.0)
    with wave.open(path, "w") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(config.SAMPLE_RATE)
        wf.writeframes((audio * 32767).astype(np.int16).tobytes())
    return path


# ─── Recording actions ───────────────────────────────────────────────────────
def _do_start():
    global _recording, _audio_chunks, _stream_mic, _loop_thread, _rec_start_time

    if _recording:
        return

    source = _SOURCE_MAP.get(_source_var.get(), "micro")
    _recording      = True
    _audio_chunks   = []
    _rec_start_time = datetime.datetime.now()
    _stop_loop.clear()
    _prevent_sleep()

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
            _stream_mic = sd.InputStream(
                samplerate=int(sd.query_devices(sd.default.device[0])["default_samplerate"]),
                channels=1, dtype="float32",
                callback=_mic_callback,
            )
            _stream_mic.start()
            _loop_thread = threading.Thread(
                target=_loopback_thread_fn, args=(False,), daemon=True
            )
            # En mode mixte, le thread loopback capture les deux
            # On stoppe le stream mic et laisse le thread tout gérer
            _stream_mic.stop(); _stream_mic.close(); _stream_mic = None
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

    _allow_sleep()

    if _level_var and _root:
        _root.after(0, lambda: _level_var.set(0))

    _set_status("Transcription en cours…")
    _set_progress(0)
    _refresh_ui()

    def _pipeline():
        path = _save_audio_to_tempfile()
        if not path:
            _set_status("Aucun audio enregistré.")
            _set_progress(0)
            _refresh_ui()
            return

        # Durée en minutes
        stop_time    = datetime.datetime.now()
        start_time   = _rec_start_time or stop_time
        duration_min = (stop_time - start_time).total_seconds() / 60

        try:
            transcript = _transcribe(path, progress_cb=_set_progress)
        except Exception as e:
            _log_error(f"Erreur Whisper : {e}")
            _set_status("Erreur transcription.")
            _set_progress(0)
            _refresh_ui()
            return

        _set_progress(90)
        output_mode = _output_var.get() if _output_var else "notion"

        if output_mode == "fichier":
            # Sauvegarde locale en .txt
            try:
                out_dir = os.path.join(os.path.expanduser("~"), "Documents", "MeetNote")
                os.makedirs(out_dir, exist_ok=True)
                fname = f"transcript_{start_time.strftime('%Y%m%d_%H%M%S')}.txt"
                out_path = os.path.join(out_dir, fname)
                with open(out_path, "w", encoding="utf-8") as f:
                    f.write(f"Date : {start_time.strftime('%Y-%m-%d %H:%M')}\n")
                    f.write(f"Durée : {duration_min:.1f} min\n")
                    f.write(f"Modèle : {_model_var.get() if _model_var else config.WHISPER_MODEL}\n")
                    f.write("-" * 60 + "\n\n")
                    f.write(transcript)
                # Ouvrir le dossier dans l'explorateur
                import subprocess
                subprocess.Popen(f'explorer /select,"{out_path}"')
            except Exception as e:
                _log_error(f"Erreur sauvegarde fichier : {e}")
                _set_status("Erreur sauvegarde fichier — voir le journal.")
                _set_progress(0)
                _refresh_ui()
                return
            finally:
                try: os.remove(path)
                except OSError: pass

            _set_progress(100)
            _set_status(f"✓ Fichier sauvegardé dans Documents/MeetNote/")
        else:
            _set_status("Envoi vers Notion…")
            try:
                push_to_notion(
                    transcript,
                    source="PC",
                    duration_min=duration_min,
                    whisper_model=_model_var.get() if _model_var else config.WHISPER_MODEL,
                    meeting_type=_type_var.get() if _type_var and _type_var.get() != "—" else "",
                    start_time=start_time,
                )
            except Exception as e:
                _log_error(f"Erreur Notion : {e}")
                _set_status("Erreur envoi Notion — voir le journal.")
                _set_progress(0)
                _refresh_ui()
                return
            finally:
                try: os.remove(path)
                except OSError: pass

            _set_progress(100)
            _set_status("✓ Envoyé dans Notion !")

        _refresh_ui()
        if _root:
            _root.after(2000, lambda: _set_progress(0))

    threading.Thread(target=_pipeline, daemon=True).start()


def _do_cancel():
    global _recording, _stream_mic, _audio_chunks

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
        _btn_start.config(state="disabled")
        _btn_stop.config(state="normal")
        _btn_cancel.config(state="normal")
        _source_combo.config(state="disabled")
        if _speaker_combo: _speaker_combo.config(state="disabled")
        _status_var.set("⏺  Enregistrement en cours…")
        if _root and hasattr(_root, "_toggle_whisper"):
            _root._toggle_whisper(False)
    else:
        _btn_start.config(state="normal")
        _btn_stop.config(state="disabled")
        _btn_cancel.config(state="disabled")
        _source_combo.config(state="readonly")
        if _speaker_combo: _speaker_combo.config(state="readonly")
        if _root and hasattr(_root, "_toggle_whisper"):
            _root._toggle_whisper(True)


# ─── Window show / hide ──────────────────────────────────────────────────────
def _show_window():
    if _root:
        _root.after(0, lambda: (
            _root.deiconify(), _root.lift(), _root.focus_force(),
        ))

def _hide_window():
    if _root:
        _root.withdraw()


# ─── Tray menu ───────────────────────────────────────────────────────────────
def _build_menu():
    return pystray.Menu(
        pystray.MenuItem(
            "● Enregistrement en cours…" if _recording else "○ Prêt",
            lambda i, it: None, enabled=False,
        ),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Ouvrir", lambda i, it: _show_window(), default=True),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Démarrer",             lambda i, it: _do_start(),           enabled=not _recording),
        pystray.MenuItem("Arrêter / Transcrire", lambda i, it: _do_stop_transcribe(), enabled=_recording),
        pystray.MenuItem("Annuler",              lambda i, it: _do_cancel(),          enabled=_recording),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quitter", _quit_app),
    )

def _quit_app(icon=None, item=None):
    if _recording:
        _do_cancel()
    if _icon:
        _icon.stop()
    if _root:
        _root.after(0, _root.destroy)


# ─── Tkinter window ──────────────────────────────────────────────────────────
_SOURCE_MAP = {
    "🎤  Microphone uniquement": "micro",
    "🔊  Loopback (son du PC)":  "loopback",
    "🎤🔊  Mixte (micro + PC)":  "mixte",
}

def _field(parent, label, bg, fg):
    """Helper : label + widget dans un cadre uniforme."""
    f = tk.Frame(parent, bg=bg)
    f.pack(fill="x", padx=12, pady=(6, 0))
    tk.Label(f, text=label, font=("Segoe UI", 8), bg=bg, fg=fg, anchor="w").pack(fill="x")
    return f


def _build_window():
    global _root, _status_var, _progress_var, _level_var, _source_var, _speaker_var
    global _btn_start, _btn_stop, _btn_cancel, _source_combo, _log_text
    global _speaker_frame, _speaker_combo, _model_var, _lang_var, _output_var

    _root = tk.Tk()
    _root.title("MeetNote")
    _root.resizable(False, False)
    _root.configure(bg="#1a1a2e")
    _root.protocol("WM_DELETE_WINDOW", _hide_window)
    _root.bind("<Unmap>", lambda e: _hide_window() if _root.state() == "iconic" else None)

    _output_var = tk.StringVar(value="notion")

    BG   = "#1a1a2e"
    BG2  = "#16213e"
    FG   = "#e0e0e0"
    FG2  = "#888888"
    HINT = "#6699cc"
    RED  = "#dc3232"
    GRN  = "#2e7d32"
    DIM  = "#333355"
    P    = 20          # marge horizontale globale

    def _section(title):
        """Bloc avec fond BG2 et titre de section."""
        outer = tk.Frame(_root, bg=BG)
        outer.pack(fill="x", padx=P, pady=(6, 0))
        tk.Label(outer, text=title, font=("Segoe UI", 8, "bold"),
                 bg=BG, fg=FG2).pack(anchor="w", pady=(0, 2))
        inner = tk.Frame(outer, bg=BG2)
        inner.pack(fill="x")
        return inner

    def _row(parent, cols=1):
        f = tk.Frame(parent, bg=BG2)
        f.pack(fill="x", padx=12, pady=(8, 0))
        if cols == 1:
            return f
        frames = []
        for i in range(cols):
            cf = tk.Frame(f, bg=BG2)
            cf.pack(side="left", fill="x", expand=True,
                    padx=(0, 8) if i < cols - 1 else 0)
            frames.append(cf)
        return frames

    def _label(parent, text):
        tk.Label(parent, text=text, font=("Segoe UI", 8),
                 bg=BG2, fg=FG2, anchor="w").pack(fill="x")

    def _hint(parent, var):
        tk.Label(parent, textvariable=var, font=("Segoe UI", 7),
                 bg=BG2, fg=HINT, anchor="w", wraplength=340,
                 justify="left").pack(fill="x", pady=(1, 0))

    def _combo(parent, var, values, width=None):
        kw = {"width": width} if width else {}
        c = ttk.Combobox(parent, textvariable=var, state="readonly",
                         font=("Segoe UI", 9), values=values, **kw)
        c.pack(fill="x", pady=(2, 0))
        return c

    def _spacer(parent, h=8):
        tk.Frame(parent, bg=BG2, height=h).pack()

    # ── En-tête
    tk.Label(_root, text="🎙  MeetNote", font=("Segoe UI", 15, "bold"),
             bg=BG, fg="#ffffff").pack(pady=(18, 2))
    tk.Label(_root, text="Enregistrement  →  Whisper  →  Notion",
             font=("Segoe UI", 9), bg=BG, fg=FG2).pack(pady=(0, 10))

    # ════════════════════════════════════════
    # SECTION 1 — Source audio
    # ════════════════════════════════════════
    sec_src = _section("SOURCE AUDIO")

    r = _row(sec_src)
    _label(r, "Source")
    _source_var = tk.StringVar(value="🎤🔊  Mixte (micro + PC)")
    _source_combo = _combo(r, _source_var, list(_SOURCE_MAP.keys()))

    _SOURCE_HINTS = {
        "🎤  Microphone uniquement": "Votre voix uniquement. Les autres participants en visio ne sont PAS captés.",
        "🔊  Loopback (son du PC)":  "Tout le son du PC (voix de tous en visio, musique…). Votre voix n'est PAS captée.",
        "🎤🔊  Mixte (micro + PC)":  "Votre voix + tout le son PC. Tous les participants sont captés, vous y compris.",
    }
    _hint_var = tk.StringVar(value=_SOURCE_HINTS["🎤🔊  Mixte (micro + PC)"])
    _hint(r, _hint_var)
    _source_var.trace_add("write", lambda *_: _hint_var.set(_SOURCE_HINTS.get(_source_var.get(), "")))

    # Speaker (loopback/mixte)
    _speaker_frame = tk.Frame(sec_src, bg=BG2)
    rs = tk.Frame(_speaker_frame, bg=BG2)
    rs.pack(fill="x", padx=12, pady=(6, 0))
    _label(rs, "Sortie audio à capturer")
    speakers    = [s.name for s in sc.all_speakers()]
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
        _root.geometry(f"{_root.winfo_reqwidth()}x{_root.winfo_reqheight()}")

    _source_var.trace_add("write", _on_source_change)
    _spacer(sec_src)

    # ════════════════════════════════════════
    # SECTION 2 — Transcription
    # ════════════════════════════════════════
    sec_w = _section("TRANSCRIPTION")

    # Modèle + Langue côte à côte
    mc, lc = _row(sec_w, cols=2)

    _label(mc, "Modèle Whisper")
    _model_var = tk.StringVar(value=config.WHISPER_MODEL)
    model_combo = _combo(mc, _model_var, ["tiny", "base", "small", "medium", "large-v3"])
    _MODEL_HINTS = {
        "tiny":    "Très rapide — qualité faible",
        "base":    "Rapide — qualité correcte",
        "small":   "Bon compromis vitesse/qualité",
        "medium":  "Haute qualité — plus lent",
        "large-v3":"Qualité maximale — lent (~5x base)",
    }
    _model_hint_var = tk.StringVar(value=_MODEL_HINTS.get(config.WHISPER_MODEL, ""))
    _hint(mc, _model_hint_var)
    _model_var.trace_add("write", lambda *_: _model_hint_var.set(_MODEL_HINTS.get(_model_var.get(), "")))

    _label(lc, "Langue audio")
    _lang_var = tk.StringVar(value="auto")
    lang_combo = _combo(lc, _lang_var, ["auto", "fr", "en", "es", "de", "it", "pt", "nl", "pl", "ja", "zh"])
    _LANG_HINTS = {
        "auto": "Détection automatique",
        "fr": "Transcrit en français",
        "en": "Traduit en anglais",
    }
    _lang_hint_var = tk.StringVar(value="Détection automatique")
    _hint(lc, _lang_hint_var)
    _lang_var.trace_add("write", lambda *_: _lang_hint_var.set(
        _LANG_HINTS.get(_lang_var.get(), "Traduit en anglais si nécessaire")))

    # Type de réunion
    rt = _row(sec_w)
    _label(rt, "Type de réunion")
    _type_var = tk.StringVar(value="—")
    type_combo = _combo(rt, _type_var,
                        ["—", "Gouvernance", "Technique", "Projet", "RH", "Fournisseur", "Autre"])
    _spacer(sec_w)

    def _toggle_whisper_controls(enabled: bool):
        state = "readonly" if enabled else "disabled"
        for c in (model_combo, lang_combo, type_combo):
            c.config(state=state)
    _root._toggle_whisper = _toggle_whisper_controls

    # ════════════════════════════════════════
    # SECTION 3 — Niveaux
    # ════════════════════════════════════════
    sec_lev = _section("NIVEAUX")
    style = ttk.Style()
    style.theme_use("default")

    rl = _row(sec_lev)
    tk.Label(rl, text="Micro / Source", font=("Segoe UI", 8),
             bg=BG2, fg=FG2, width=14, anchor="w").pack(side="left")
    _level_var = tk.DoubleVar(value=0)
    style.configure("VU.Horizontal.TProgressbar",
                    troughcolor="#0f3460", background="#00cc66", thickness=10, borderwidth=0)
    ttk.Progressbar(rl, variable=_level_var, maximum=100,
                    style="VU.Horizontal.TProgressbar").pack(side="left", fill="x", expand=True)

    def _vu_decay():
        if not _recording and _level_var.get() > 0:
            _level_var.set(max(0, _level_var.get() - 3))
        _root.after(50, _vu_decay)
    _root.after(100, _vu_decay)

    rp = _row(sec_lev)
    tk.Label(rp, text="Transcription", font=("Segoe UI", 8),
             bg=BG2, fg=FG2, width=14, anchor="w").pack(side="left")
    _progress_var = tk.DoubleVar(value=0)
    style.configure("Prog.Horizontal.TProgressbar",
                    troughcolor="#0f3460", background=RED, thickness=10, borderwidth=0)
    ttk.Progressbar(rp, variable=_progress_var, maximum=100,
                    style="Prog.Horizontal.TProgressbar").pack(side="left", fill="x", expand=True)
    _spacer(sec_lev)

    # ════════════════════════════════════════
    # BOUTONS
    # ════════════════════════════════════════
    btn_frame = tk.Frame(_root, bg=BG)
    btn_frame.pack(fill="x", padx=P, pady=(10, 0))

    _btn_start = tk.Button(
        btn_frame, text="⏺  Démarrer", font=("Segoe UI", 11, "bold"),
        bg=RED, fg="white", activebackground="#b02020", activeforeground="white",
        relief="flat", bd=0, pady=10, cursor="hand2", command=_do_start,
    )
    _btn_start.pack(fill="x", pady=(0, 4))

    _btn_stop = tk.Button(
        btn_frame, text="⏹  Arrêter et transcrire", font=("Segoe UI", 10),
        bg=GRN, fg="white", activebackground="#1b5e20", activeforeground="white",
        relief="flat", bd=0, pady=9, cursor="hand2",
        state="disabled", command=_do_stop_transcribe,
    )
    _btn_stop.pack(fill="x", pady=(0, 4))

    _btn_cancel = tk.Button(
        btn_frame, text="✕  Annuler (sans push)", font=("Segoe UI", 9),
        bg=DIM, fg="#cccccc", activebackground="#222244", activeforeground="white",
        relief="flat", bd=0, pady=7, cursor="hand2",
        state="disabled", command=_do_cancel,
    )
    _btn_cancel.pack(fill="x")

    # ── Boutons secondaires : Paramètres + Quitter
    bottom_frame = tk.Frame(_root, bg=BG)
    bottom_frame.pack(fill="x", padx=P, pady=(6, 0))

    def _open_settings():
        """Fenêtre modale paramètres — onglets Notion / Général."""
        win = tk.Toplevel(_root)
        win.title("Paramètres MeetNote")
        win.configure(bg=BG)
        win.resizable(False, False)
        win.grab_set()

        # ── Onglets
        nb = ttk.Notebook(win)
        nb.pack(fill="both", expand=True, padx=0, pady=0)

        style2 = ttk.Style()
        style2.configure("TNotebook", background=BG, borderwidth=0)
        style2.configure("TNotebook.Tab", background=DIM, foreground=FG2,
                         padding=[12, 5], font=("Segoe UI", 9))
        style2.map("TNotebook.Tab", background=[("selected", BG2)],
                   foreground=[("selected", "#ffffff")])

        tab_notion  = tk.Frame(nb, bg=BG, padx=20, pady=12)
        tab_general = tk.Frame(nb, bg=BG, padx=20, pady=12)
        nb.add(tab_notion,  text="  Notion  ")
        nb.add(tab_general, text="  Général  ")

        # ════════════════════════════════
        # ONGLET NOTION
        # ════════════════════════════════
        def _lbl(parent, text, bold=False):
            font = ("Segoe UI", 8, "bold") if bold else ("Segoe UI", 8)
            tk.Label(parent, text=text, font=font, bg=BG, fg=FG2, anchor="w",
                     wraplength=340, justify="left").pack(fill="x", pady=(0, 2))

        def _entry_field(parent, label, value, show=None):
            tk.Label(parent, text=label, font=("Segoe UI", 8),
                     bg=BG, fg=FG2, anchor="w").pack(fill="x", pady=(8, 1))
            kw = {"show": show} if show else {}
            var = tk.StringVar(value=value)
            tk.Entry(parent, textvariable=var, font=("Segoe UI", 9),
                     bg="#0f3460", fg=FG, insertbackground=FG,
                     relief="flat", bd=4, **kw).pack(fill="x", ipady=4)
            return var

        # Instructions
        tk.Label(tab_notion, text="Configuration Notion", font=("Segoe UI", 10, "bold"),
                 bg=BG, fg="#ffffff").pack(anchor="w", pady=(0, 8))

        INSTRUCTIONS = (
            "1. Créer une intégration Notion\n"
            "   → Aller sur notion.so/my-integrations\n"
            "   → Cliquer « New integration »\n"
            "   → Donner un nom (ex: MeetNote)\n"
            "   → Copier le « Internal Integration Secret » (commence par ntn_ ou secret_)\n"
            "   → Le coller dans le champ Token ci-dessous\n\n"
            "2. Obtenir le Database ID\n"
            "   → Ouvrir votre database Notion dans le navigateur\n"
            "   → L'URL ressemble à :\n"
            "      notion.so/xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx?v=...\n"
            "   → Copier les 32 caractères avant le « ?v= »\n"
            "   → Les coller dans le champ Database ID ci-dessous\n\n"
            "3. Partager la database avec l'intégration\n"
            "   → Dans Notion, ouvrir la database\n"
            "   → Cliquer « ••• » en haut à droite → « Connections »\n"
            "   → Ajouter votre intégration MeetNote"
        )
        txt_frame = tk.Frame(tab_notion, bg="#0a0a1a")
        txt_frame.pack(fill="x", pady=(0, 10))
        instr_text = tk.Text(txt_frame, height=13, bg="#0a0a1a", fg="#aaccff",
                             font=("Consolas", 8), relief="flat", bd=0,
                             wrap="word", state="normal")
        instr_text.insert("1.0", INSTRUCTIONS)
        instr_text.config(state="disabled")
        instr_text.pack(fill="x", padx=6, pady=6)

        token_var = _entry_field(tab_notion, "Notion Token (ntn_... ou secret_...)",
                                 config.NOTION_TOKEN, show="•")
        db_var    = _entry_field(tab_notion, "Database ID (32 caractères hex)",
                                 config.NOTION_DATABASE_ID)

        status_lbl = tk.Label(tab_notion, text="", font=("Segoe UI", 8),
                              bg=BG, fg=HINT, wraplength=340)
        status_lbl.pack(pady=(6, 0))

        def _save_notion():
            new_token = token_var.get().strip()
            new_db    = db_var.get().strip()
            if not new_token or not new_db:
                status_lbl.config(text="Token et Database ID requis.", fg="#ff6666")
                return
            cfg_path = os.path.join(os.path.dirname(__file__), "config.py")
            try:
                import re
                with open(cfg_path, "r", encoding="utf-8") as f:
                    content = f.read()
                content = re.sub(r'NOTION_TOKEN\s*=\s*"[^"]*"',
                                 f'NOTION_TOKEN = "{new_token}"', content)
                content = re.sub(r'NOTION_DATABASE_ID\s*=\s*"[^"]*"',
                                 f'NOTION_DATABASE_ID = "{new_db}"', content)
                with open(cfg_path, "w", encoding="utf-8") as f:
                    f.write(content)
                config.NOTION_TOKEN       = new_token
                config.NOTION_DATABASE_ID = new_db
                import notion_push as np_mod
                np_mod.HEADERS["Authorization"] = f"Bearer {new_token}"
                status_lbl.config(text="✓ Sauvegardé.", fg="#00cc66")
            except Exception as e:
                status_lbl.config(text=f"Erreur : {e}", fg="#ff6666")

        tk.Button(tab_notion, text="Enregistrer", font=("Segoe UI", 9, "bold"),
                  bg="#2e5e8e", fg="white", activebackground="#1e4e7e",
                  relief="flat", bd=0, pady=7, cursor="hand2",
                  command=_save_notion).pack(fill="x", pady=(8, 0))

        # ════════════════════════════════
        # ONGLET GÉNÉRAL
        # ════════════════════════════════
        tk.Label(tab_general, text="Destination du transcript",
                 font=("Segoe UI", 10, "bold"), bg=BG, fg="#ffffff").pack(anchor="w", pady=(0, 8))

        _lbl(tab_general, "Choisissez où envoyer le transcript après transcription :")

        out_frame = tk.Frame(tab_general, bg=BG2)
        out_frame.pack(fill="x", pady=(4, 0))

        current_output = _output_var.get() if _output_var else "notion"

        rb_notion = tk.Radiobutton(
            out_frame, text="  Envoyer vers Notion (nécessite un token)",
            variable=_output_var, value="notion",
            font=("Segoe UI", 9), bg=BG2, fg=FG, selectcolor=BG2,
            activebackground=BG2, activeforeground=FG,
            relief="flat", cursor="hand2",
        )
        rb_notion.pack(anchor="w", padx=10, pady=(8, 2))
        tk.Label(out_frame, text="     Le transcript est créé comme page Notion avec\n     toutes les métadonnées (date, durée, modèle…)",
                 font=("Segoe UI", 8), bg=BG2, fg=FG2, justify="left").pack(anchor="w", padx=10)

        tk.Frame(out_frame, bg="#334", height=1).pack(fill="x", padx=10, pady=8)

        rb_file = tk.Radiobutton(
            out_frame, text="  Sauvegarder en fichier texte local",
            variable=_output_var, value="fichier",
            font=("Segoe UI", 9), bg=BG2, fg=FG, selectcolor=BG2,
            activebackground=BG2, activeforeground=FG,
            relief="flat", cursor="hand2",
        )
        rb_file.pack(anchor="w", padx=10, pady=(0, 2))
        tk.Label(out_frame,
                 text="     Le transcript est sauvegardé dans\n     Documents\\MeetNote\\ et l'explorateur s'ouvre.",
                 font=("Segoe UI", 8), bg=BG2, fg=FG2, justify="left").pack(anchor="w", padx=10, pady=(0, 8))

        tk.Label(tab_general, text="✓ La sélection est appliquée immédiatement.",
                 font=("Segoe UI", 8), bg=BG, fg="#00cc66").pack(anchor="w", pady=(8, 0))

        # Fermer
        tk.Button(win, text="Fermer", font=("Segoe UI", 9),
                  bg=DIM, fg="#cccccc", activebackground="#222244",
                  relief="flat", bd=0, pady=7, cursor="hand2",
                  command=win.destroy).pack(fill="x", padx=20, pady=(8, 16))

        # Centrer
        win.update_idletasks()
        wx = _root.winfo_x() + (_root.winfo_width()  - win.winfo_reqwidth())  // 2
        wy = _root.winfo_y() + (_root.winfo_height() - win.winfo_reqheight()) // 2
        win.geometry(f"+{wx}+{wy}")

    tk.Button(bottom_frame, text="⚙  Paramètres", font=("Segoe UI", 8),
              bg=DIM, fg="#aaaaaa", activebackground="#222244", activeforeground="white",
              relief="flat", bd=0, pady=5, cursor="hand2",
              command=_open_settings).pack(side="left", fill="x", expand=True, padx=(0, 4))

    tk.Button(bottom_frame, text="⏻  Quitter", font=("Segoe UI", 8),
              bg="#2a1010", fg="#cc4444", activebackground="#3a1010", activeforeground="#ff6666",
              relief="flat", bd=0, pady=5, cursor="hand2",
              command=_quit_app).pack(side="left", fill="x", expand=True)

    # ── Status
    _status_var = tk.StringVar(value="Prêt")
    tk.Label(_root, textvariable=_status_var, font=("Segoe UI", 9),
             bg=BG, fg="#aaaaaa", wraplength=340).pack(pady=(8, 4))

    # ════════════════════════════════════════
    # JOURNAL
    # ════════════════════════════════════════
    tk.Label(_root, text="JOURNAL", font=("Segoe UI", 8, "bold"),
             bg=BG, fg=FG2, anchor="w").pack(fill="x", padx=P, pady=(4, 0))
    log_frame = tk.Frame(_root, bg="#0a0a1a")
    log_frame.pack(fill="x", padx=P, pady=(2, 16))
    _log_text = tk.Text(
        log_frame, height=3, bg="#0a0a1a", fg="#ff6666",
        font=("Consolas", 8), relief="flat", bd=0, state="disabled", wrap="word",
    )
    sb = tk.Scrollbar(log_frame, command=_log_text.yview,
                      bg="#0a0a1a", troughcolor="#0a0a1a")
    _log_text.config(yscrollcommand=sb.set)
    sb.pack(side="right", fill="y")
    _log_text.pack(side="left", fill="both", expand=True, padx=4, pady=4)

    # Centrer
    _root.update_idletasks()
    w = _root.winfo_reqwidth()
    h = _root.winfo_reqheight()
    sw, sh = _root.winfo_screenwidth(), _root.winfo_screenheight()
    _root.geometry(f"{w}x{h}+{(sw-w)//2}+{(sh-h)//2}")

    return _root


# ─── Main ────────────────────────────────────────────────────────────────────
def main():
    global _icon

    _icon = pystray.Icon(
        "meetnote", _make_icon(False), "MeetNote — Prêt", menu=_build_menu(),
    )
    _icon.default_action = lambda i, it: _show_window()
    threading.Thread(target=_icon.run, daemon=True).start()

    root = _build_window()
    root.mainloop()


if __name__ == "__main__":
    main()
