"""
Transcription locale avec faster-whisper.
Si le modèle n'est pas présent localement, il est téléchargé automatiquement
depuis HuggingFace avec une fenêtre de progression Tkinter.
"""
import sys
import os
import threading

sys.path.insert(0, os.path.dirname(__file__))
import config

_model = None

# Répertoire modèles bundlé (présent dans build complet, absent dans slim)
_BUNDLED_MODELS_DIR = os.path.join(
    getattr(sys, "_MEIPASS", os.path.dirname(__file__)),
    "faster_whisper_models",
)


def _model_path(name: str) -> str | None:
    """Retourne le chemin local du modèle bundlé si disponible."""
    p = os.path.join(_BUNDLED_MODELS_DIR, name)
    if os.path.isfile(os.path.join(p, "model.bin")):
        return p
    return None


def _download_with_progress(model_name: str):
    """
    Télécharge le modèle HuggingFace avec une fenêtre Tkinter de progression.
    Bloquant — s'exécute dans le thread courant.
    """
    import tkinter as tk
    from tkinter import ttk

    repo_id = f"Systran/faster-whisper-{model_name}"

    # ── Fenêtre progress ──────────────────────────────────────────────────────
    root = tk.Tk()
    root.title("MeetNote — Téléchargement modèle")
    root.geometry("420x130")
    root.resizable(False, False)
    root.configure(bg="#111125")
    root.attributes("-topmost", True)

    tk.Label(root, text=f"Téléchargement du modèle Whisper « {model_name} »",
             font=("Segoe UI", 10, "bold"), bg="#111125", fg="#e2e0fc").pack(pady=(18, 4))

    lbl_status = tk.Label(root, text="Connexion à HuggingFace…",
                          font=("Segoe UI", 8), bg="#111125", fg="#ab8985")
    lbl_status.pack()

    bar = ttk.Progressbar(root, mode="indeterminate", length=360)
    bar.pack(pady=10)
    bar.start(12)

    _done   = threading.Event()
    _error  = [None]

    def _download():
        try:
            from huggingface_hub import snapshot_download
            root.after(0, lambda: lbl_status.config(
                text=f"Téléchargement {repo_id} en cours…"))
            snapshot_download(
                repo_id=repo_id,
                local_dir=os.path.join(
                    os.path.expanduser("~"), ".cache", "huggingface", "hub",
                    f"models--Systran--faster-whisper-{model_name}",
                    "snapshots", "downloaded",
                ),
                ignore_patterns=["*.msgpack", "flax_model*", "tf_model*", "rust_model*"],
            )
        except Exception as e:
            _error[0] = str(e)
        finally:
            _done.set()
            root.after(0, root.destroy)

    threading.Thread(target=_download, daemon=True).start()
    root.mainloop()

    if _error[0]:
        raise RuntimeError(
            f"Impossible de télécharger le modèle « {model_name} » : {_error[0]}\n"
            "Vérifiez votre connexion internet."
        )


def _get_model():
    global _model
    if _model is not None:
        return _model

    from faster_whisper import WhisperModel

    # 1. Modèle bundlé (build complet)
    local = _model_path(config.WHISPER_MODEL)
    if local:
        _model = WhisperModel(local, device="cpu", compute_type="int8")
        return _model

    # 2. Cache HuggingFace déjà présent ?
    hf_cache = os.path.join(
        os.path.expanduser("~"), ".cache", "huggingface", "hub",
        f"models--Systran--faster-whisper-{config.WHISPER_MODEL}",
    )
    if os.path.isdir(hf_cache):
        _model = WhisperModel(config.WHISPER_MODEL, device="cpu", compute_type="int8")
        return _model

    # 3. Téléchargement avec fenêtre progress
    _download_with_progress(config.WHISPER_MODEL)
    _model = WhisperModel(config.WHISPER_MODEL, device="cpu", compute_type="int8")
    return _model


def transcribe_file(audio_path: str) -> str:
    """Transcrit un fichier WAV et retourne le texte complet."""
    model = _get_model()
    segments, info = model.transcribe(
        audio_path,
        language=config.WHISPER_LANGUAGE,
        beam_size=5,
        vad_filter=True,
    )
    lines = [seg.text.strip() for seg in segments if seg.text.strip()]
    return "\n".join(lines)
