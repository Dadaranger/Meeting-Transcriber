# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

from PyInstaller.utils.hooks import collect_all

repository_root = Path(SPECPATH).parent
source_root = repository_root / "src"
asset_root = source_root / "meeting_transcriber" / "assets"

faster_whisper_datas, faster_whisper_binaries, faster_whisper_imports = collect_all(
    "faster_whisper"
)
ctranslate_datas, ctranslate_binaries, ctranslate_imports = collect_all("ctranslate2")

analysis = Analysis(
    [str(source_root / "meeting_transcriber" / "main.py")],
    pathex=[str(source_root)],
    binaries=faster_whisper_binaries + ctranslate_binaries,
    datas=(
        [(str(asset_root / "meeting-transcriber.svg"), "meeting_transcriber/assets")]
        + faster_whisper_datas
        + ctranslate_datas
    ),
    hiddenimports=faster_whisper_imports + ctranslate_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pyannote", "torch", "torchaudio"],
    noarchive=False,
    optimize=1,
)
python_archive = PYZ(analysis.pure)

executable = EXE(
    python_archive,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="MeetingTranscriber",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(repository_root / "packaging" / "meeting-transcriber.ico"),
    version=str(repository_root / "packaging" / "windows-version.txt"),
)

bundle = COLLECT(
    executable,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="Meeting Transcriber",
)
