#!/usr/bin/env bash
# Use identical deployment settings from interactive shells and systemd.
set -euo pipefail
cd "$(dirname "$0")/.."
# The saved deployment is authoritative, including after a new deployment.
unset ECR_REGISTRY IMAGE_TAG COMPOSE_PROJECT_NAME
# chatgpt-selection-env-v2: CI-managed immutable images; credentials stay in .env.
optional=()
if [ -f .chatgpt-deploy.json ]; then
  if ! values="$(python3 scripts/tunnel_deployment.py compose-values)"; then
    printf '%s\n' 'Invalid tunnel selection; review saved metadata.' >&2
    exit 1
  fi
  mapfile -t selected <<< "$values"
  export CHATGPT_TUNNEL_IMAGE="${selected[0]}"
  # Preserve the paper volume across READ_ONLY/SIMULATE mode changes. READ_ONLY
  # never opens the journal; SIMULATE still requires explicit account admission
  # and first-time journal initialization through the paper overlay settings.
  optional=(-p "${selected[1]}" -f docker-compose.paper.yml -f docker-compose.chatgpt.yml)
  for argument in "$@"; do
  case "$argument" in
    up|start|restart|run|create)
      python3 scripts/tunnel_deployment.py check-start
      python3 scripts/deploy_verify.py check-config
      ;;
  esac
  done
  if [ "$#" = 1 ] && [ "$1" = pull ]; then
    if [[ "${selected[0]}" == sha256:* ]]; then
      # Local image IDs remain supported for disposable tests and development.
      set -- pull moomoo-mcp
    else
      set -- pull moomoo-mcp chatgpt-tunnel
    fi
  fi
else
  # Every writable mode selects its durable storage. Configured paper accounts
  # retain their independent paper overlay alongside REAL execution.
  execution_files="$(python3 scripts/deploy_verify.py execution-overlays)"
  if [ -n "$execution_files" ]; then
    mapfile -t storage_overlays <<< "$execution_files"
    for overlay in "${storage_overlays[@]}"; do
      optional+=(-f "$overlay")
    done
  fi
fi
exec docker --context rootless compose --env-file .env --env-file .deploy.env \
  -f docker-compose.yml -f docker-compose.prod.yml "${optional[@]}" "$@"
