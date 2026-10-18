#!/usr/bin/env bash
# Use identical deployment settings from interactive shells and systemd.
set -euo pipefail
cd "$(dirname "$0")/.."
# Lifecycle entrypoints (including the legacy systemd unit) must participate in
# the same transaction as rollout and rollback. Read-only config resolution can
# recurse through this wrapper, so leave diagnostics outside the mutation lock.
for argument in "$@"; do
  case "$argument" in
    up|start|stop|restart|run|create|down|rm|kill|pause|unpause|pull|build|exec|scale|watch)
      if [ -z "${MOOMOO_DEPLOY_LOCK_TOKEN:-}" ]; then
        exec python3 scripts/deployment_lock.py run -- bash "$0" "$@"
      fi
      python3 scripts/deployment_lock.py run -- true
      break
      ;;
  esac
done
# The saved deployment is authoritative, including after a new deployment.
unset ECR_REGISTRY IMAGE_TAG COMPOSE_PROJECT_NAME
unset MOOMOO_APP_IMAGE
# A verified snapshot contains the exact private effective configuration. Normal
# lifecycle actions keep using it when the operator edits .env for the next release.
snapshot="${MOOMOO_COMPOSE_CONFIG:-}"
if [ -z "$snapshot" ] && [ -f .deploy-config.json ]; then
  snapshot=.deploy-config.json
  for argument in "$@"; do
    if [ "$argument" = config ]; then snapshot=""; fi
  done
fi
if [ -n "$snapshot" ]; then
  exec docker --context rootless compose -f "$snapshot" "$@"
fi
# chatgpt-selection-env-v2: CI-managed immutable images; credentials stay in .env.
optional=()
resolve_storage=true
# Stopping/removing a selected tunnel must work without validating the current
# trading settings. No service definitions are added by the storage overlays.
if [ -f .chatgpt-deploy.json ]; then
  for argument in "$@"; do
    case "$argument" in
      stop|rm) resolve_storage=false ;;
    esac
  done
fi
# Resolve through Compose so .env values and shell overrides have identical
# meaning here and in the container. Controls apply with or without a tunnel.
if [ "$resolve_storage" = true ]; then
  execution_files="$(python3 scripts/deploy_verify.py execution-overlays)"
  if [ -n "$execution_files" ]; then
    mapfile -t storage_overlays <<< "$execution_files"
    for overlay in "${storage_overlays[@]}"; do
      optional+=(-f "$overlay")
    done
  fi
fi
if [ -f .chatgpt-deploy.json ]; then
  if ! values="$(python3 scripts/tunnel_deployment.py compose-values)"; then
    printf '%s\n' 'Invalid tunnel selection; review saved metadata.' >&2
    exit 1
  fi
  mapfile -t selected <<< "$values"
  export CHATGPT_TUNNEL_IMAGE="${selected[0]}"
  # Exposure and execution are independent: base storage remains mounted while
  # execution_overlays selects journal settings only for configured capabilities.
  optional=(-p "${selected[1]}" "${optional[@]}")
  optional+=(-f docker-compose.chatgpt.yml)
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
fi
exec docker --context rootless compose --env-file .env --env-file .deploy.env \
  -f docker-compose.yml -f docker-compose.prod.yml "${optional[@]}" "$@"

