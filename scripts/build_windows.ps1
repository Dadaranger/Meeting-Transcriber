param(
    [switch]$SkipSync
)

$ErrorActionPreference = "Stop"
$repositoryRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
if (-not $IsWindows -and $env:OS -ne "Windows_NT") {
    throw "The Windows application bundle must be built on Windows."
}

Push-Location $repositoryRoot
try {
    if (-not $SkipSync) {
        & uv sync --frozen --extra packaging --extra transcription
        if ($LASTEXITCODE -ne 0) { throw "Dependency synchronization failed." }
    }
    & uv run --frozen --extra packaging --extra transcription python scripts/generate_icon.py
    if ($LASTEXITCODE -ne 0) { throw "Icon generation failed." }

    $env:SOURCE_DATE_EPOCH = (& git log -1 --format=%ct).Trim()
    & uv run --frozen --extra packaging --extra transcription pyinstaller `
        --noconfirm `
        --clean `
        --workpath build/pyinstaller `
        --distpath dist `
        packaging/meeting-transcriber.spec
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed." }

    $executable = Join-Path $repositoryRoot "dist\Meeting Transcriber\MeetingTranscriber.exe"
    if (-not (Test-Path -LiteralPath $executable -PathType Leaf)) {
        throw "Expected application executable was not produced: $executable"
    }
    $smokeMarker = Join-Path $repositoryRoot "build\pyinstaller\package-smoke-ok.txt"
    Remove-Item -LiteralPath $smokeMarker -Force -ErrorAction SilentlyContinue
    $smokeArgument = '"--package-smoke-test={0}"' -f $smokeMarker
    $smokeTest = Start-Process `
        -FilePath $executable `
        -ArgumentList $smokeArgument `
        -Wait `
        -PassThru `
        -WindowStyle Hidden
    if ($smokeTest.ExitCode -ne 0) {
        throw "Packaged application smoke test failed with exit code $($smokeTest.ExitCode)."
    }
    if (-not (Test-Path -LiteralPath $smokeMarker -PathType Leaf)) {
        throw "Packaged application entry point did not produce its smoke marker."
    }
    $smokeEvidence = (Get-Content -LiteralPath $smokeMarker -Raw).Trim()
    if ($smokeEvidence -notmatch '^meeting-transcriber-package-smoke:\d+\.\d+\.\d+$') {
        throw "Packaged application produced invalid smoke evidence: $smokeEvidence"
    }
    Write-Output $executable
}
finally {
    if ($smokeMarker) {
        Remove-Item -LiteralPath $smokeMarker -Force -ErrorAction SilentlyContinue
    }
    Remove-Item Env:SOURCE_DATE_EPOCH -ErrorAction SilentlyContinue
    Pop-Location
}
