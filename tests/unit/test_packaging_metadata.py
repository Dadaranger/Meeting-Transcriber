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
