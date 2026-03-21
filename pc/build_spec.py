"""
build_spec.py — Generate meetnote.spec and run PyInstaller build.

Discovers:
  - faster-whisper model files in the HF cache (base, small — whichever are present)
  - ctranslate2, soundcard, sounddevice, tokenizers package files
  - Writes meetnote.spec, then invokes PyInstaller
"""

import os
import sys
import subprocess

# ─── Paths ───────────────────────────────────────────────────────────────────
SCRIPT_DIR  = os.path.dirname(os.path.abspath(__file__))
HF_CACHE    = os.path.join(os.path.expanduser("~"), ".cache", "huggingface", "hub")
VENV        = os.path.join(os.path.dirname(os.path.dirname(SCRIPT_DIR)), ".venv")
SITE_PKG    = os.path.join(VENV, "Lib", "site-packages")

# ─── Discover faster-whisper model snapshots ─────────────────────────────────
MODEL_NAMES = ["tiny", "base", "small"]   # large-v3 is 3 GB — excluded by default

def find_model_snapshot(name: str):
    """Return the snapshot dir for a given model name if it exists."""
    model_dir = os.path.join(HF_CACHE, f"models--Systran--faster-whisper-{name}")
    snapshots_dir = os.path.join(model_dir, "snapshots")
    if not os.path.isdir(snapshots_dir):
        return None
    snaps = [d for d in os.listdir(snapshots_dir)
             if os.path.isdir(os.path.join(snapshots_dir, d))]
    if not snaps:
        return None
    return os.path.join(snapshots_dir, snaps[0])


# Collect (src_dir, dest_inside_bundle) for each present model
model_datas = []
for mname in MODEL_NAMES:
    snap = find_model_snapshot(mname)
    if snap:
        dest = f"faster_whisper_models/{mname}"
        model_datas.append((snap, dest))
        print(f"[build_spec] Found model '{mname}': {snap}")
    else:
        print(f"[build_spec] Model '{mname}' not found — skipping")

if not model_datas:
    print("[build_spec] ERROR: No faster-whisper models found in HF cache!")
    sys.exit(1)

# ─── Build the datas list for the spec ───────────────────────────────────────
# Format for spec file: list of (src, dst) tuples
datas_lines = []

# 1. Whisper models
for src, dst in model_datas:
    # Normalize backslashes for the spec (use raw strings / forward slashes)
    src_norm = src.replace("\\", "/")
    datas_lines.append(f"    (r'{src_norm}', r'{dst}'),")

# 2. faster_whisper assets (silero_vad_v6.onnx)
fw_assets = os.path.join(SITE_PKG, "faster_whisper", "assets")
if os.path.isdir(fw_assets):
    datas_lines.append(f"    (r'{fw_assets.replace(chr(92), '/')}', 'faster_whisper/assets'),")

# 3. sounddevice portaudio DLLs
sd_data = os.path.join(SITE_PKG, "_sounddevice_data")
if os.path.isdir(sd_data):
    datas_lines.append(f"    (r'{sd_data.replace(chr(92), '/')}', '_sounddevice_data'),")

# 4. ctranslate2 package (DLLs live in its directory)
ct2_dir = os.path.join(SITE_PKG, "ctranslate2")
if os.path.isdir(ct2_dir):
    datas_lines.append(f"    (r'{ct2_dir.replace(chr(92), '/')}', 'ctranslate2'),")

# 5. tokenizers package (tokenizers.pyd and sub-modules)
tk_dir = os.path.join(SITE_PKG, "tokenizers")
if os.path.isdir(tk_dir):
    datas_lines.append(f"    (r'{tk_dir.replace(chr(92), '/')}', 'tokenizers'),")

# 6. soundcard mediafoundation CFFI module
sc_dir = os.path.join(SITE_PKG, "soundcard")
if os.path.isdir(sc_dir):
    datas_lines.append(f"    (r'{sc_dir.replace(chr(92), '/')}', 'soundcard'),")

# ─── Embed ffmpeg + ffprobe si disponibles (avant écriture du spec) ───────────
import shutil as _shutil
for _bin in ("ffmpeg.exe", "ffprobe.exe"):
    _found = _shutil.which(_bin.replace(".exe", "")) or _shutil.which(_bin)
    if _found:
        datas_lines.append(f"    (r'{_found.replace(chr(92), '/')}', r'.'),")
        print(f"[build_spec] Bundling {_bin}: {_found}")
    else:
        print(f"[build_spec] {_bin} not found in PATH — audio compression requires ffmpeg at runtime")

datas_block = "\n".join(datas_lines)

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
    "config",
    "user_config",
    "history",
    "outlook_cal",
    "wave",
    "glob",
    "json",
    "collections",
    "dataclasses",
    "shutil",
    "scipy",
    "scipy.signal",
    "noisereduce",
    "pycaw",
    "pycaw.pycaw",
    "comtypes",
    "psutil",
    "win32com",
    "win32com.client",
    "pywintypes",
]
hidden_imports_str = ",\n    ".join(f"'{h}'" for h in hidden_imports)

# ─── Write the spec file ──────────────────────────────────────────────────────
ENTRY_POINT = os.path.join(SCRIPT_DIR, "meetnote-tray.py").replace("\\", "/")

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
    excludes=['pandas', 'jupyter', 'IPython', 'PyQt5', 'wx'],
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
print("[build_spec] Starting PyInstaller build...")
pyinstaller_exe = os.path.join(VENV, "Scripts", "pyinstaller.exe")
if not os.path.exists(pyinstaller_exe):
    pyinstaller_exe = "pyinstaller"   # fallback to PATH

result = subprocess.run(
    [pyinstaller_exe, "--clean", "--noconfirm", spec_path],
    cwd=SCRIPT_DIR,
    capture_output=False,
)

if result.returncode == 0:
    dist_dir = os.path.join(SCRIPT_DIR, "dist", "MeetNote")
    print(f"\n[build_spec] SUCCESS — output: {dist_dir}")
    # Report size
    total = 0
    count = 0
    for root, dirs, files in os.walk(dist_dir):
        for f in files:
            total += os.path.getsize(os.path.join(root, f))
            count += 1
    print(f"[build_spec] {count} files, total size: {total / 1024 / 1024:.1f} MB")
else:
    print(f"\n[build_spec] FAILED with exit code {result.returncode}")
    sys.exit(result.returncode)
