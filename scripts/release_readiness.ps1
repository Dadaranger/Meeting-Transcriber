param(
    [switch]$SkipSync,
    [switch]$SkipBuild
)

$ErrorActionPreference = "Stop"
$repositoryRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
Push-Location $repositoryRoot
try {
    if (-not $SkipSync) {
        & uv sync --frozen --extra dev --extra packaging --extra transcription
        if ($LASTEXITCODE -ne 0) { throw "Dependency synchronization failed." }
    }

    & .\scripts\check.cmd --skip-sync
    if ($LASTEXITCODE -ne 0) { throw "Repository checks failed." }

    $failureGateTests = @(
        "tests/unit/capture/test_capture_audit.py",
        "tests/unit/capture/test_dual_source_recorder.py",
        "tests/unit/app/test_recording_service.py",
        "tests/unit/app/test_transcription_service.py",
        "tests/integration/test_forced_termination_recovery.py"
    )
    & uv run --frozen pytest -q @failureGateTests
    if ($LASTEXITCODE -ne 0) { throw "Failure-injection and simulated-soak gates failed." }

    if (-not $SkipBuild) {
        & .\scripts\build_windows.ps1 -SkipSync
        if ($LASTEXITCODE -ne 0) { throw "Packaged application gate failed." }
    }

    Write-Output "Automated release-readiness gates passed."
    Write-Output "Real hardware, accessibility, accuracy, signing, and clean-machine gates remain manual."
}
finally {
    Pop-Location
}
