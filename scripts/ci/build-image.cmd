@echo off
rem Build the app image.  Reads FULL_IMAGE and BUILD_TAG from the environment.
rem Run from anywhere — the script cd's to the repo root first.
setlocal
cd /d "%~dp0..\.."

rem Docker Desktop may still be starting when the build kicks off (its
rem context files stay locked during startup) — poll until the daemon
rem answers, then build.
for /l %%i in (1,1,30) do (
  docker info >nul 2>&1 && goto :docker_up
  timeout /t 2 /nobreak >nul
)
:docker_up
docker build -f infra/docker/Dockerfile -t %FULL_IMAGE%:%BUILD_TAG% .
