@echo off
rem Health-check the deployed API on the box.  Reads SSH_KEY, SSH_USER and
rem EC2_HOST from the environment.
setlocal

rem Jenkins writes the SSH key to a temp file that inherits broad ACLs from
rem the workspace, which Windows OpenSSH rejects ("bad permissions").
rem Strip the BUILTIN\Users entry so only the owner retains access.
icacls "%SSH_KEY%" /remove:g "BUILTIN\Users" >nul 2>&1

ssh -o StrictHostKeyChecking=no -i "%SSH_KEY%" "%SSH_USER%@%EC2_HOST%" "curl -fsS http://localhost:8000/health && curl -fsS http://localhost:8000/readiness"
