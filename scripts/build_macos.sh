#!/usr/bin/env bash
set -euo pipefail

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "The macOS application bundle must be built on macOS." >&2
  exit 1
fi

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$repo_root"

if [[ "${1:-}" != "--skip-sync" ]]; then
  uv sync --frozen --extra packaging --extra transcription
fi

build_root="$repo_root/build/macos"
helper="$build_root/MeetingTranscriberSystemAudio"
iconset="$build_root/MeetingTranscriber.iconset"
icon="$build_root/meeting-transcriber.icns"
mkdir -p "$build_root"

architecture="$(uname -m)"
export MACOSX_DEPLOYMENT_TARGET=13.0
swiftc \
  -parse-as-library \
  -target "${architecture}-apple-macos13.0" \
  "$repo_root/native/macos/MeetingTranscriberSystemAudio.swift" \
  -o "$helper"

uv run --frozen --extra packaging --extra transcription python scripts/generate_macos_icon.py
iconutil --convert icns --output "$icon" "$iconset"

version="$(uv run --frozen python -c 'from meeting_transcriber import __version__; print(__version__)')"
export MEETING_TRANSCRIBER_VERSION="$version"
export MEETING_TRANSCRIBER_MAC_AUDIO_HELPER="$helper"
export MEETING_TRANSCRIBER_MAC_ICON="$icon"
export SOURCE_DATE_EPOCH="$(git log -1 --format=%ct)"

uv run --frozen --extra packaging --extra transcription pyinstaller \
  --noconfirm \
  --clean \
  --workpath build/pyinstaller-macos \
  --distpath dist \
  packaging/meeting-transcriber-macos.spec

application="$repo_root/dist/Meeting Transcriber.app"
executable="$application/Contents/MacOS/MeetingTranscriber"
if [[ ! -x "$executable" ]]; then
  echo "Expected application executable was not produced: $executable" >&2
  exit 1
fi
codesign --verify --deep --strict "$application"

smoke_marker="$repo_root/build/pyinstaller-macos/package-smoke-ok.txt"
rm -f "$smoke_marker"
QT_QPA_PLATFORM=offscreen "$executable" "--package-smoke-test=$smoke_marker"
if [[ ! -f "$smoke_marker" ]]; then
  echo "Packaged application entry point did not produce its smoke marker." >&2
  exit 1
fi
if ! grep -Eq '^meeting-transcriber-package-smoke:[0-9]+\.[0-9]+\.[0-9]+$' "$smoke_marker"; then
  echo "Packaged application produced invalid smoke evidence." >&2
  exit 1
fi

asset_arch="${MEETING_TRANSCRIBER_ARCH_LABEL:-$architecture}"
asset_root="$repo_root/dist/macos"
staging="$repo_root/build/macos-dmg"
mkdir -p "$asset_root"
case "$staging" in
  "$repo_root"/build/*) ;;
  *) echo "Refusing to replace unexpected staging path: $staging" >&2; exit 1 ;;
esac
rm -rf "$staging"
mkdir -p "$staging"
ditto "$application" "$staging/Meeting Transcriber.app"
ln -s /Applications "$staging/Applications"

dmg="$asset_root/Meeting-Transcriber-${version}-macOS-${asset_arch}.dmg"
zip="$asset_root/Meeting-Transcriber-${version}-macOS-${asset_arch}.zip"
hdiutil create -volname "Meeting Transcriber" -srcfolder "$staging" -ov -format UDZO "$dmg"
ditto -c -k --sequesterRsrc --keepParent "$application" "$zip"

checksums="$asset_root/SHA256SUMS-macOS-${asset_arch}.txt"
(
  cd "$asset_root"
  shasum -a 256 "$(basename "$dmg")" "$(basename "$zip")"
) > "$checksums"

printf '%s\n' "$dmg" "$zip" "$checksums"
