"""
Transcription locale avec faster-whisper
"""
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))
import config

_model = None


def _get_model():
    global _model
    if _model is None:
        from faster_whisper import WhisperModel
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
