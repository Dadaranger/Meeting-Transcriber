# -*- mode: python ; coding: utf-8 -*-
import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_all

repository_root = Path(SPECPATH).parent
source_root = repository_root / "src"
asset_root = source_root / "meeting_transcriber" / "assets"
helper_path = Path(os.environ["MEETING_TRANSCRIBER_MAC_AUDIO_HELPER"])
icon_path = Path(os.environ["MEETING_TRANSCRIBER_MAC_ICON"])
codesign_identity = os.environ.get("MEETING_TRANSCRIBER_CODESIGN_IDENTITY") or None

if not helper_path.is_file():
    raise FileNotFoundError(f"macOS system-audio helper was not built: {helper_path}")
if not icon_path.is_file():
    raise FileNotFoundError(f"macOS icon was not built: {icon_path}")

faster_whisper_datas, faster_whisper_binaries, faster_whisper_imports = collect_all(
    "faster_whisper"
)
ctranslate_datas, ctranslate_binaries, ctranslate_imports = collect_all("ctranslate2")
av_datas, av_binaries, av_imports = collect_all("av")
sounddevice_datas, sounddevice_binaries, sounddevice_imports = collect_all("sounddevice")

analysis = Analysis(
    [str(source_root / "meeting_transcriber" / "__main__.py")],
    pathex=[str(source_root)],
    binaries=(
        [(str(helper_path), ".")]
        + sounddevice_binaries
        + faster_whisper_binaries
        + ctranslate_binaries
        + av_binaries
    ),
    datas=(
        [(str(asset_root / "meeting-transcriber.svg"), "meeting_transcriber/assets")]
        + sounddevice_datas
        + faster_whisper_datas
        + ctranslate_datas
        + av_datas
    ),
    hiddenimports=(
        sounddevice_imports + faster_whisper_imports + ctranslate_imports + av_imports
    ),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pyannote", "torch", "torchaudio", "hf_xet", "pyaudiowpatch"],
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
    codesign_identity=codesign_identity,
    entitlements_file=str(repository_root / "packaging" / "macos-entitlements.plist"),
)

bundle_files = COLLECT(
    executable,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="Meeting Transcriber",
)

application = BUNDLE(
    bundle_files,
    name="Meeting Transcriber.app",
    icon=str(icon_path),
    bundle_identifier="com.dadaranger.meeting-transcriber",
    version=os.environ.get("MEETING_TRANSCRIBER_VERSION", "0.0.0"),
    info_plist={
        "CFBundleDisplayName": "Meeting Transcriber",
        "LSMinimumSystemVersion": "13.0",
        "NSHighResolutionCapable": True,
        "NSMicrophoneUsageDescription": (
            "Meeting Transcriber records the microphone you select for a meeting."
        ),
    },
)
