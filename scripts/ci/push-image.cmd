@echo off
rem Push the built image to Docker Hub.  Reads FULL_IMAGE, BUILD_TAG,
rem DH_USER and DH_TOKEN from the environment.
setlocal
cd /d "%~dp0..\.."

rem `<nul set /p=` echoes WITHOUT a trailing CRLF — plain `echo x |` would
rem append \r\n and Docker sends the token with a stray \r (auth fails).
<nul set /p="%DH_TOKEN%" | docker login -u %DH_USER% --password-stdin
docker tag %FULL_IMAGE%:%BUILD_TAG% %FULL_IMAGE%:latest
docker push %FULL_IMAGE%:%BUILD_TAG%
docker push %FULL_IMAGE%:latest
