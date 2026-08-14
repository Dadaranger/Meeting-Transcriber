from __future__ import annotations

import re
import tomllib
from pathlib import Path

from meeting_transcriber import __version__

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def test_release_versions_stay_synchronized() -> None:
    project = tomllib.loads((REPOSITORY_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    version_resource = (REPOSITORY_ROOT / "packaging/windows-version.txt").read_text(
        encoding="utf-8"
    )
    installer = (REPOSITORY_ROOT / "packaging/meeting-transcriber.iss").read_text(encoding="utf-8")

    assert project["project"]["version"] == __version__
    assert f"StringStruct('ProductVersion', '{__version__}')" in version_resource
    assert f'#define AppVersion "{__version__}"' in installer
    assert 'Name: "{app}\\_internal\\hf_xet"' in installer


def test_installer_is_per_user_and_never_deletes_meeting_data() -> None:
    installer = (REPOSITORY_ROOT / "packaging/meeting-transcriber.iss").read_text(encoding="utf-8")
    information = (REPOSITORY_ROOT / "packaging/installer-info.txt").read_text(encoding="utf-8")

    assert "PrivilegesRequired=lowest" in installer
    assert "DefaultDirName={localappdata}\\Programs\\{#AppName}" in installer
    assert "[UninstallDelete]" not in installer
    assert not re.search(r"Documents.*delete|delete.*Documents", installer, re.IGNORECASE)
    assert "leaves recordings, transcripts, notes, reviews, and downloaded models" in information


def test_workflow_artifact_name_is_safe_for_pull_request_refs() -> None:
    workflow = (REPOSITORY_ROOT / ".github/workflows/windows-package.yml").read_text(
        encoding="utf-8"
    )
    upload_step = workflow.split("- name: Upload Windows artifacts", maxsplit=1)[1].split(
        "- name:", maxsplit=1
    )[0]

    assert "name: meeting-transcriber-windows-${{ github.run_id }}" in upload_step
    assert "github.ref_name" not in upload_step


def test_ci_installs_the_optional_transcription_runtime_it_tests() -> None:
    workflow = (REPOSITORY_ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    install_step = workflow.split("- name: Install locked dependencies", maxsplit=1)[1].split(
        "- name:", maxsplit=1
    )[0]

    assert "uv sync --frozen --extra dev --extra transcription" in install_step


def test_windows_bundle_runs_the_real_entry_point_and_requires_smoke_evidence() -> None:
    specification = (REPOSITORY_ROOT / "packaging/meeting-transcriber.spec").read_text(
        encoding="utf-8"
    )
    build_script = (REPOSITORY_ROOT / "scripts/build_windows.ps1").read_text(encoding="utf-8")

    assert 'meeting_transcriber" / "__main__.py"' in specification
    assert 'meeting_transcriber" / "main.py"' not in specification
    assert "--package-smoke-test={0}" in build_script
    assert "entry point did not produce its smoke marker" in build_script
