@echo off
rem Health-check the deployed API on the box.  Reads SSH_KEY, SSH_USER and
rem EC2_HOST from the environment.
setlocal
ssh -o StrictHostKeyChecking=no -i "%SSH_KEY%" "%SSH_USER%@%EC2_HOST%" "curl -fsS http://localhost:8000/health && curl -fsS http://localhost:8000/readiness"
