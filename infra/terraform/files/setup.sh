#!/usr/bin/env bash
# Cloud-init script (run once at first boot) — prepares the EC2 box to run
# the docker-compose stack.  Deliberately does NOT clone code or write
# secrets: Jenkins delivers the compose file, .env and app image over SSH.
set -euo pipefail

DEBIAN_FRONTEND=noninteractive apt-get update -y
DEBIAN_FRONTEND=noninteractive apt-get install -y ca-certificates curl

# Docker Engine + compose plugin
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
  > /etc/apt/sources.list.d/docker.list
apt-get update -y
apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

# Let the default 'ubuntu' user run docker without sudo
usermod -aG docker ubuntu
systemctl enable --now docker

# Deploy directory: Jenkins scp's docker-compose.yml and writes .env here
mkdir -p "${deploy_dir}"
chown ubuntu:ubuntu "${deploy_dir}"
