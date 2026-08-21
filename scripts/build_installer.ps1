param(
    [string]$AppVersion = "0.1.5"
)

$ErrorActionPreference = "Stop"
$repositoryRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$bundleDirectory = Join-Path $repositoryRoot "dist\Meeting Transcriber"
$outputDirectory = Join-Path $repositoryRoot "dist\installer"
$innoCandidates = @(
    (Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe"),
    (Join-Path $env:ProgramFiles "Inno Setup 6\ISCC.exe")
)
$compiler = $innoCandidates | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
if (-not $compiler) {
    $command = Get-Command iscc.exe -ErrorAction SilentlyContinue
    if ($command) { $compiler = $command.Source }
}
if (-not $compiler) {
    throw "Inno Setup 6 was not found. Install it, then run this script again."
}
if (-not (Test-Path -LiteralPath (Join-Path $bundleDirectory "MeetingTranscriber.exe"))) {
    throw "Build the Windows bundle before compiling the installer."
}
New-Item -ItemType Directory -Path $outputDirectory -Force | Out-Null

& $compiler `
    "/DSourceDir=$bundleDirectory" `
    "/DOutputDir=$outputDirectory" `
    "/DAppVersion=$AppVersion" `
    (Join-Path $repositoryRoot "packaging\meeting-transcriber.iss")
if ($LASTEXITCODE -ne 0) { throw "Inno Setup compilation failed." }

$portableArchive = Join-Path $outputDirectory "Meeting-Transcriber-$AppVersion-portable.zip"
Compress-Archive -Path (Join-Path $bundleDirectory "*") -DestinationPath $portableArchive -Force
$artifacts = Get-ChildItem -LiteralPath $outputDirectory -File | Where-Object { $_.Extension -in ".exe", ".zip" }
$checksumLines = foreach ($artifact in $artifacts | Sort-Object Name) {
    $hash = (Get-FileHash -LiteralPath $artifact.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    "$hash  $($artifact.Name)"
}
$checksumLines | Set-Content -LiteralPath (Join-Path $outputDirectory "SHA256SUMS.txt") -Encoding ascii
$artifacts.FullName
