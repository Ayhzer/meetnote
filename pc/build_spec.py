"""
build_spec.py — Generate meetnote.spec and run PyInstaller build.

Embarque :
  - ctranslate2, soundcard, sounddevice, tokenizers, ffmpeg (voir build_paths)
  - AUCUN modèle Whisper : il est téléchargé au premier lancement (modeles.py)
  - Écrit meetnote.spec, puis lance PyInstaller
"""

import os
import subprocess
import sys
import time

from build_paths import (
    collecter_datas,
    composer_bilan,
    dist_dir_name,
    formater_datas,
    produire_archive,
    resolve_python_home,
    resolve_site_packages,
)
from build_version import build_version_resource
from version import VERSION

# ─── Signature numérique ─────────────────────────────────────────────────────
# Certificat auto-signé MeetNote, installé dans CurrentUser\My + TrustedPublisher.
# Créé une seule fois via New-SelfSignedCertificate (voir setup_codesign.ps1).
_CODESIGN_SUBJECT = "CN=MeetNote"          # filtre pour retrouver le cert
_TIMESTAMP_SERVER = "http://timestamp.digicert.com"  # TSA publique gratuite


def _sign_exe(exe_path: str):
    """Signe exe_path avec le certificat MeetNote trouvé dans CurrentUser\\My."""
    ps = f"""
$cert = Get-ChildItem Cert:\\CurrentUser\\My |
    Where-Object {{ $_.Subject -like '*MeetNote*' -and $_.HasPrivateKey }} |
    Sort-Object NotAfter -Descending |
    Select-Object -First 1
if (-not $cert) {{
    Write-Error 'Certificat MeetNote introuvable dans CurrentUser\\My'
    exit 1
}}
$ts = '{_TIMESTAMP_SERVER}'
$r = Set-AuthenticodeSignature -FilePath '{exe_path}' -Certificate $cert `
        -HashAlgorithm SHA256 -TimestampServer $ts
if ($r.Status -ne 'Valid') {{
    Write-Error ("Signature echouee: " + $r.StatusMessage)
    exit 1
}}
Write-Host ("[build_spec] EXE signe: " + $r.Status + " — " + $cert.Thumbprint)
"""
    # Commande construite ici, sans entrée externe : S603/S607 non pertinents.
    r = subprocess.run(
        ["powershell", "-NonInteractive", "-Command", ps],
        capture_output=True, text=True, timeout=30,
    )
    print(r.stdout.strip())
    if r.returncode != 0:
        print(f"[build_spec] WARNING: signature échouée — {r.stderr.strip()[:300]}")


# ─── Paths ───────────────────────────────────────────────────────────────────
SCRIPT_DIR  = os.path.dirname(os.path.abspath(__file__))
VENV        = resolve_python_home()
SITE_PKG    = resolve_site_packages(VENV)
# Dossiers de travail hors OneDrive (verrouillages de sync). Le fichier de version
# est écrit à la racine, pas dans WORK_DIR que `pyinstaller --clean` vide.
BUILD_ROOT   = r"C:\Temp\meetnote_build"
WORK_DIR     = os.path.join(BUILD_ROOT, "work")
DIST_DIR_TMP = os.path.join(BUILD_ROOT, "dist")
VERSION_FILE = os.path.join(BUILD_ROOT, "meetnote.version.txt")
DIST_NAME    = dist_dir_name(VERSION)

# ─── Données embarquées ──────────────────────────────────────────────────────
# Aucun modèle Whisper dans le paquet : il se télécharge au premier lancement
# (modeles.py). Un cache Hugging Face vide sur ce poste n'arrête donc plus le build.
# Alertes retenues pour le bilan final : celles du début se noient dans le journal
# PyInstaller (poids de ffmpeg, ffmpeg absent...).
ALERTES = []
datas_block = formater_datas(
    collecter_datas(SITE_PKG, journal=lambda m: print(f"[build_spec] {m}"),
                    alertes=ALERTES, etape=os.path.join(BUILD_ROOT, "ffmpeg"))
)


# ─── Hidden imports ───────────────────────────────────────────────────────────
hidden_imports = [
    "soundcard",
    "soundcard.mediafoundation",
    "sounddevice",
    "_sounddevice",
    "_sounddevice_data",
    "faster_whisper",
    "faster_whisper.audio",
    "faster_whisper.feature_extractor",
    "faster_whisper.tokenizer",
    "faster_whisper.transcribe",
    "faster_whisper.utils",
    "faster_whisper.vad",
    "ctranslate2",
    "tokenizers",
    "tokenizers.models",
    "tokenizers.normalizers",
    "tokenizers.pre_tokenizers",
    "tokenizers.processors",
    "tokenizers.decoders",
    "pystray",
    "pystray._win32",
    "PIL",
    "PIL.Image",
    "PIL.ImageDraw",
    "PIL.ImageFont",
    "PIL._imaging",
    "tkinter",
    "tkinter.ttk",
    "huggingface_hub",
    "numpy",
    "av",
    "onnxruntime",
    "onnxruntime.capi",
    "cffi",
    "notion_push",
    "notion_api",
    "assistant_agenda",
    "assistant_audio",
    "assistant_fenetre",
    "assistant_notion",
    "config",
    "user_config",
    "fichier_json",
    "perf_profile",
    "modeles",
    "version",
    "history",
    "journal_app",
    "outlook_cal",
    "source_agenda",
    "outlook_mail",
    "calendar_digest",
    "teams_roster",
    "soundfile",
    "_soundfile",
    "wave",
    "glob",
    "json",
    "collections",
    "dataclasses",
    "shutil",
    "scipy",
    "scipy.signal",
    "noisereduce",
    "debruitage",
    "pycaw",
    "pycaw.pycaw",
    "comtypes",
    "psutil",
    "win32com",
    "win32com.client",
    "win32timezone",
    "pywintypes",
    "pythoncom",
    # EWS / Nouvel Outlook on-premise
    "ews_cal",
    "exchangelib",
    "exchangelib.autodiscover",
    "exchangelib.credentials",
    "exchangelib.configuration",
    "exchangelib.account",
    "exchangelib.items",
    "exchangelib.fields",
    "exchangelib.folders",
    "exchangelib.protocol",
    "requests_ntlm",
    "keyring",
    "keyring.backends",
    "keyring.backends.Windows",
    "sspilib",
    # Nom de module, pas le nom de distribution PyPI (« pyspnego ») : sinon
    # PyInstaller sort un ERROR « Hidden import not found » à chaque build.
    "spnego",
    "lxml",
    "lxml.etree",
    "defusedxml",
    "dns",
    "dns.resolver",
    "cryptography",
    "oauthlib",
    "requests_oauthlib",
]
hidden_imports_str = ",\n    ".join(f"'{h}'" for h in hidden_imports)

# Modules volontairement absents du paquet. `torch` (366 Mo) n'est importé par
# noisereduce que dans un try/except ImportError (variante GPU inutilisée) ; il
# entraînait `numba` puis `llvmlite` (115 Mo) via torch.testing._internal.
# Voir debruitage.py et PLAN.md (T029).
excludes = ['pandas', 'jupyter', 'IPython', 'PyQt5', 'wx', 'torch', 'numba', 'llvmlite']
excludes_str = ", ".join(f"'{e}'" for e in excludes)

# ─── Write the spec file ──────────────────────────────────────────────────────
ENTRY_POINT = os.path.join(SCRIPT_DIR, "meetnote-tray.py").replace("\\", "/")

# Ressource de version de l'exe (Propriétés → Détails), issue de version.py.
os.makedirs(BUILD_ROOT, exist_ok=True)
with open(VERSION_FILE, "w", encoding="utf-8") as f:
    f.write(build_version_resource(VERSION))
print(f"[build_spec] Version {VERSION} — ressource écrite : {VERSION_FILE}")

spec_content = f"""# -*- mode: python ; coding: utf-8 -*-
# Auto-generated by build_spec.py — do not edit manually

import sys
from PyInstaller.utils.hooks import collect_all, collect_data_files

block_cipher = None

# Collect all from ctranslate2 (picks up DLLs via hook)
ct2_datas, ct2_binaries, ct2_hiddenimports = collect_all('ctranslate2')
sd_datas, sd_binaries, sd_hiddenimports   = collect_all('sounddevice')

a = Analysis(
    [r'{ENTRY_POINT}'],
    pathex=[r'{SCRIPT_DIR.replace(chr(92), "/")}'],
    binaries=ct2_binaries + sd_binaries,
    datas=[
{datas_block}
    ] + ct2_datas + sd_datas,
    hiddenimports=[
    {hidden_imports_str},
    ] + ct2_hiddenimports + sd_hiddenimports,
    hookspath=[],
    hooksconfig={{}},
    runtime_hooks=[],
    excludes=[{excludes_str}],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='MeetNote',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,          # no console window (tray app)
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
    version=r'{VERSION_FILE}',
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='MeetNote',
)
"""

spec_path = os.path.join(SCRIPT_DIR, "meetnote.spec")
with open(spec_path, "w", encoding="utf-8") as f:
    f.write(spec_content)
print(f"[build_spec] Wrote {spec_path}")

# ─── Run PyInstaller ──────────────────────────────────────────────────────────
os.makedirs(WORK_DIR, exist_ok=True)
os.makedirs(DIST_DIR_TMP, exist_ok=True)

print("[build_spec] Starting PyInstaller build...")
pyinstaller_exe = os.path.join(VENV, "Scripts", "pyinstaller.exe")
if not os.path.exists(pyinstaller_exe):
    pyinstaller_exe = "pyinstaller"   # fallback to PATH

result = subprocess.run(
    [pyinstaller_exe, "--clean", "--noconfirm",
     "--workpath", WORK_DIR,
     "--distpath", DIST_DIR_TMP,
     spec_path],
    cwd=SCRIPT_DIR,
    capture_output=False,
)

if result.returncode == 0:
    import shutil as _shutil_final
    dist_dir_final = os.path.join(SCRIPT_DIR, "dist", DIST_NAME)
    # `--noconfirm` + COLLECT vident DIST_DIR_TMP\MeetNote avant d'y écrire
    # (PyInstaller, _make_clean_directory) : ce dossier ne contient que ce que ce
    # build vient de produire. C'est lui qu'on signe et qu'on archive.
    sortie_fraiche = os.path.join(DIST_DIR_TMP, "MeetNote")

    # Arrêter MeetNote s'il tourne (sinon MeetNote.exe est verrouillé)
    subprocess.run(["taskkill", "/F", "/IM", "MeetNote.exe"],
                   capture_output=True)
    time.sleep(1)

    # ── Signature numérique (certificat auto-signé, stocké dans CurrentUser\My) ──
    _sign_exe(os.path.join(sortie_fraiche, "MeetNote.exe"))

    # Report size
    total = 0
    count = 0
    for root, _dirs, files in os.walk(sortie_fraiche):
        for f in files:
            total += os.path.getsize(os.path.join(root, f))
            count += 1
    print(f"[build_spec] {count} files, total size: {total / 1024 / 1024:.1f} MB")

    # Dossier livré (compatibilité) : fusionné avec l'existant, rien n'est supprimé.
    print(f"[build_spec] Copying output to {dist_dir_final} ...")
    _shutil_final.copytree(sortie_fraiche, dist_dir_final, dirs_exist_ok=True)
    print(f"\n[build_spec] SUCCESS — output: {dist_dir_final}")

    # ── Archive à télécharger : pc\dist\MeetNote-<version>.zip, depuis la sortie
    # fraîche seule ; les restes d'anciens builds du dossier livré sont nommés. ──
    produire_archive(sortie_fraiche, VERSION, os.path.join(SCRIPT_DIR, "dist"),
                     journal=lambda m: print(f"[build_spec] {m}"),
                     alertes=ALERTES, dossier_livre=dist_dir_final)
    for ligne in composer_bilan(ALERTES):
        print(f"[build_spec] {ligne}")
else:
    print(f"\n[build_spec] FAILED with exit code {result.returncode}")
    sys.exit(result.returncode)
