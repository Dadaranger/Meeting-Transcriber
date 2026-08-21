@echo off
setlocal

pushd "%~dp0.." || exit /b 1

if /I "%~1"=="--skip-sync" goto checks
uv sync --frozen --extra dev --extra transcription || goto failure

:checks
uv run --frozen ruff format --check . || goto failure
uv run --frozen ruff check . || goto failure
uv run --frozen mypy src tests || goto failure
uv run --frozen pytest -q || goto failure

popd
exit /b 0

:failure
set "check_exit=%errorlevel%"
popd
exit /b %check_exit%
