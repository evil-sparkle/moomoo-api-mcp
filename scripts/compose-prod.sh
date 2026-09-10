#!/usr/bin/env bash
# Use identical deployment settings from interactive shells and systemd.
set -euo pipefail
cd "$(dirname "$0")/.."
# The saved deployment is authoritative, including after a new deployment.
unset ECR_REGISTRY IMAGE_TAG
exec docker --context rootless compose --env-file .env --env-file .deploy.env \
  -f docker-compose.yml -f docker-compose.prod.yml "$@"
