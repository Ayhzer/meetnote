"""
Test d'integration : la transcription en parallele doit rendre exactement le meme
transcript que la transcription en serie.

C'est le seul risque reel introduit par le parallelisme — des segments reassembles
dans l'ordre d'arrivee et non dans l'ordre du fichier donneraient des timestamps
incoherents et une diarisation fausse, sans jamais lever d'erreur.

Lent (quelques minutes, transcription reelle), donc hors des tests unitaires :
    py -3.12 pc\\test_transcription_parallele.py <fichier_audio>

Sans argument, prend le dernier enregistrement de Documents/MeetNote/audio.
"""
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import wave

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

DUREE_EXTRAIT_S = 180
DECOUPE_MIN = 1          # segments d'une minute : 3 segments sur l'extrait
VARIABLE_TEST_LENT = "MEETNOTE_TEST_LENT"


def _charger_tray():
    """Importe meetnote-tray.py (nom non importable tel quel : tiret)."""
    ici = os.path.dirname(os.path.abspath(__file__))
    chemin = os.path.join(ici, "meetnote-tray.py")
    spec = importlib.util.spec_from_file_location("meetnote_tray", chemin)
    module = importlib.util.module_from_spec(spec)
    sys.modules["meetnote_tray"] = module
    spec.loader.exec_module(module)      # sans effet de bord : main() est sous garde
    return module


def _extrait_wav(source: str) -> str:
    """Decode et decoupe DUREE_EXTRAIT_S de la partie la plus sonore de `source`."""
    import numpy as np
    plein = os.path.join(tempfile.gettempdir(), "test_par_full.wav")
    if not os.path.isfile(plein):
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise RuntimeError("ffmpeg introuvable dans le PATH")
        # Arguments construits ici, aucune entree externe dans la commande.
        subprocess.run(
            [ffmpeg, "-y", "-i", source, "-ar", "16000", "-ac", "1", plein],
            capture_output=True, check=True)
    with wave.open(plein) as wf:
        data = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16)
    fenetre = DUREE_EXTRAIT_S * 16000
    if len(data) <= fenetre:
        debut = 0
    else:
        pas = 16000 * 30
        debut = max((np.abs(data[i:i + fenetre]).mean(), i)
                    for i in range(0, len(data) - fenetre, pas))[1]
    extrait = os.path.join(tempfile.gettempdir(), "test_par_clip.wav")
    with wave.open(extrait, "w") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(16000)
        out.writeframes(data[debut:debut + fenetre].tobytes())
    return extrait


def _transcrire(tray, wav: str, fils: int) -> str:
    """Lance une transcription complete avec un nombre de flux impose."""
    import datetime

    import perf_profile
    profil_force = perf_profile.ProfilExecution(fils, max(2, 8 // fils), "test")
    origine = perf_profile.choisir
    perf_profile.choisir = lambda *a, **k: profil_force
    # L'historique reel de l'utilisateur n'a pas a recevoir les jobs de test.
    maj_origine = tray.hist_mod.update
    tray.hist_mod.update = lambda job: None
    tray._whisper_model = None
    tray._whisper_model_name = None
    try:
        job = tray._Job(
            id=f"test-{fils}", wav_path=wav,
            start_time=datetime.datetime.now(tz=datetime.timezone.utc),
            duration_min=DUREE_EXTRAIT_S / 60,
            model_name="medium", language="fr",
            meeting_type="Test", output_mode="fichier",
        )
        if not tray._do_step_transcribe(job):
            raise RuntimeError(f"transcription echouee : {job.error_msg}")
        return job.whisper_transcript
    finally:
        perf_profile.choisir = origine
        tray.hist_mod.update = maj_origine


def main(source: str | None = None) -> int:
    """Rejoue le test ; `source` : fichier audio, sinon le dernier enregistrement."""
    if source is None:
        dossier = os.path.join(os.path.expanduser("~"), "Documents",
                               "MeetNote", "audio")
        fichiers = sorted(f for f in os.listdir(dossier)
                          if f.endswith((".opus", ".wav")))
        if not fichiers:
            print("Aucun enregistrement disponible pour le test.")
            return 1
        source = os.path.join(dossier, fichiers[-1])
    print(f"Source : {os.path.basename(source)}")

    wav = _extrait_wav(source)
    tray = _charger_tray()
    import config
    config.TRANSCRIPTION_SEGMENT_MIN = DECOUPE_MIN   # force le decoupage sur 3 min

    serie = _transcrire(tray, wav, fils=1)
    print(f"serie     : {len(serie.splitlines())} lignes")
    parallele = _transcrire(tray, wav, fils=2)
    print(f"parallele : {len(parallele.splitlines())} lignes")

    if not serie.strip():
        print("ECHEC : la transcription de reference est vide, test non concluant.")
        return 1

    if serie != parallele:
        print("ECHEC : les deux transcriptions different.")
        paires = zip(serie.splitlines(), parallele.splitlines(), strict=False)
        for i, (a, b) in enumerate(paires):
            if a != b:
                print(f"  ligne {i+1}\n    serie     : {a}\n    parallele : {b}")
                break
        return 1

    horodatages = [ligne[1:9] for ligne in parallele.splitlines()
                   if ligne.startswith("[")]
    if horodatages != sorted(horodatages):
        print("ECHEC : les horodatages ne sont pas croissants.")
        return 1

    residus = [f for f in os.listdir(os.path.dirname(wav))
               if f.startswith(os.path.basename(wav)[:-4] + "_seg")]
    if residus:
        print(f"ECHEC : segments temporaires non nettoyes : {residus}")
        return 1

    print("OK : transcriptions identiques, horodatages croissants, aucun residu.")
    return 0


@unittest.skipUnless(
    os.environ.get(VARIABLE_TEST_LENT) == "1",
    f"test d'integration lent (modele Whisper + ffmpeg) : definir {VARIABLE_TEST_LENT}=1")
class TestTranscriptionParallele(unittest.TestCase):
    def test_parallele_identique_a_serie(self):
        self.assertEqual(main(), 0)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else None))
