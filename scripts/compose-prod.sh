#!/usr/bin/env bash
# Use identical deployment settings from interactive shells and systemd.
set -euo pipefail
cd "$(dirname "$0")/.."
# The saved deployment is authoritative, including after a new deployment.
unset ECR_REGISTRY IMAGE_TAG
# chatgpt-selection-schema-v1: kept across checkouts; no credential values here.
optional=()
if [ -f .chatgpt-deploy.json ]; then
  values="$(python3 scripts/tunnel_deployment.py compose-values)"
  mapfile -t selected <<< "$values"
  export CHATGPT_TUNNEL_IMAGE="${selected[0]}"
  export CHATGPT_TUNNEL_SECRET_DIR="${selected[1]}"
  optional=(-p "${selected[2]}" -f docker-compose.chatgpt.yml)
  for argument in "$@"; do
  case "$argument" in
    up|start|restart|run|create)
      python3 scripts/tunnel_deployment.py check-release
      python3 scripts/deploy_verify.py check-config
      ;;
  esac
  done
  if [ "$#" = 1 ] && [ "$1" = pull ]; then
    set -- pull moomoo-mcp
  fi
fi
exec docker --context rootless compose --env-file .env --env-file .deploy.env \
  -f docker-compose.yml -f docker-compose.prod.yml "${optional[@]}" "$@"
