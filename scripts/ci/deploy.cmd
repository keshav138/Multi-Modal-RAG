@echo off
rem Deploy to the EC2 box over SSH (Windows' built-in OpenSSH).  Reads
rem SSH_KEY, SSH_USER, EC2_HOST, GROQ_KEY, DOCKERHUB_USER, BUILD_TAG and
rem DEPLOY_DIR from the environment.
setlocal
cd /d "%~dp0..\.."

rem Build the box's .env locally, scp it with the compose file, then tell
rem the box to pull the fresh image and restart the stack.
(
  echo DATABASE_URL=postgresql://rag:rag@db:5432/rag
  echo GROQ_API_KEY=%GROQ_KEY%
  echo DOCKERHUB_USER=%DOCKERHUB_USER%
  echo IMAGE_TAG=%BUILD_TAG%
) > .env.deploy

scp -o StrictHostKeyChecking=no -i "%SSH_KEY%" .env.deploy "%SSH_USER%@%EC2_HOST%:%DEPLOY_DIR%/.env"
scp -o StrictHostKeyChecking=no -i "%SSH_KEY%" infra/docker/docker-compose.yml "%SSH_USER%@%EC2_HOST%:%DEPLOY_DIR%/docker-compose.yml"
ssh -o StrictHostKeyChecking=no -i "%SSH_KEY%" "%SSH_USER%@%EC2_HOST%" "cd %DEPLOY_DIR% && docker compose pull && docker compose up -d"
del .env.deploy
