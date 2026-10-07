#!/usr/bin/env bash
# Manual deployment entrypoint: CI supplies both images; settings live in .env.
# Usage: deploy.sh [--prepare] [--chatgpt|--no-chatgpt] [commit]
set -euo pipefail
ORIGINAL_ARGS=("$@")

REPO_ROOT="${REPO_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
cd "$REPO_ROOT"
prepare=false
tunnel_mode=retain
while [ "$#" -gt 0 ]; do
  case "$1" in
    --prepare) prepare=true ;;
    --chatgpt|--no-chatgpt)
      if [ "$tunnel_mode" != retain ]; then
        echo 'Choose only one of --chatgpt and --no-chatgpt.' >&2
        exit 1
      fi
      if [ "$1" = --chatgpt ]; then tunnel_mode=enable; else tunnel_mode=disable; fi
      ;;
    --*) echo 'Unknown deployment option.' >&2; exit 1 ;;
    *) break ;;
  esac
  shift
done
if [ "$#" -gt 1 ]; then
  echo 'Usage: scripts/deploy.sh [--prepare] [--chatgpt|--no-chatgpt] [commit]' >&2
  exit 1
fi
if [ "$prepare" = true ] && [ "$tunnel_mode" = disable ]; then
  echo '--no-chatgpt stops the tunnel; omit --prepare.' >&2
  exit 1
fi
registry="${ECR_REGISTRY:-}"
if [ -z "$registry" ] && [ -f .deploy.env ]; then
  while IFS= read -r line; do
    if [ "${line%%=*}" = ECR_REGISTRY ]; then
      registry="${line#*=}"
    fi
  done < .deploy.env
fi
if [ -z "$registry" ]; then
  echo 'Set ECR_REGISTRY to the Terraform ecr_registry output for the first deploy.' >&2
  exit 1
fi
IFS=. read -r account dkr ecr region domain suffix extra <<< "$registry"
if [ "$dkr.$ecr.$domain.$suffix" != 'dkr.ecr.amazonaws.com' ] || [ -n "${extra:-}" ] || [ -z "$region" ]; then
  echo 'ECR_REGISTRY must be a private ECR registry hostname.' >&2
  exit 1
fi
tools=(git aws docker python3)
if [ "$prepare" = false ]; then
  tools+=(curl)
  case "${DEPLOY_VERIFY_TIMEOUT:-90}" in
    ""|*[!0-9]*)
      echo "Error: DEPLOY_VERIFY_TIMEOUT must be a numeric value." >&2
      exit 1
      ;;
  esac
fi
for tool in "${tools[@]}"; do
  command -v "$tool" >/dev/null || { echo "Missing required command: $tool" >&2; exit 1; }
done
# scripts/deploy_verify.py runs on the host, not in the image: standard library
# only, so any Python 3.10 or newer will do (Ubuntu 24.04 ships 3.12).
if ! python3 -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>/dev/null; then
  echo 'deploy.sh needs Python 3.10 or newer on the host as python3.' >&2
  exit 1
fi
if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
  echo 'Commit or stash tracked working-tree changes before deploying.' >&2
  exit 1
fi
git fetch --quiet origin main
commit="$(git rev-parse --verify --end-of-options "${1:-origin/main}^{commit}")"
short="${commit:0:7}"
tunnel_selected=false
if [ "$tunnel_mode" = enable ] || { [ -f .chatgpt-deploy.json ] && [ "$tunnel_mode" != disable ]; }; then
  tunnel_selected=true
  if ! git show "${commit}:scripts/compose-prod.sh" | grep -q chatgpt-selection-env-v2; then
    echo 'Disable the tunnel before selecting an older unsupported deployment.' >&2
    exit 1
  fi
  if [ -f .chatgpt-deploy.json ]; then python3 scripts/tunnel_deployment.py check-start; fi
fi

if [ "${DEPLOY_REEXEC:-0}" != "1" ]; then
  if git cat-file -e "${commit}:scripts/deploy.sh" 2>/dev/null; then
    if ! git diff --quiet "${commit}" -- scripts/deploy.sh 2>/dev/null; then
      echo "scripts/deploy.sh has changed in ${short}; re-executing latest deploy script..." >&2
      reexec_file="$(mktemp "${TMPDIR:-/tmp}/deploy.XXXXXX")"
      trap 'rm -f "${reexec_file}"' EXIT INT TERM
      git show "${commit}:scripts/deploy.sh" > "${reexec_file}"
      chmod +x "${reexec_file}"
      export DEPLOY_REEXEC=1
      export REPO_ROOT
      if [ "${#ORIGINAL_ARGS[@]}" -eq 0 ]; then
        "${reexec_file}"
      else
        "${reexec_file}" "${ORIGINAL_ARGS[@]}"
      fi
      exit $?
    fi
  fi
fi

# CI tags every main build with its short commit, so the image matches the source
# about to be checked out; git v* tags are bookmarks and are deliberately ignored here.
ecr_has_tag() {
  local image="$1" tag="$2"
  local got
  got="$(aws ecr batch-get-image --region "$region" --registry-id "$account" \
    --repository-name "$image" --image-ids "imageTag=$tag" \
    --query 'images[0].imageId.imageDigest' --output text)"
  [ -n "$got" ] && [ "$got" != None ]
}

# The application image contains both the gateway and the server.
if ecr_has_tag moomoo-api-mcp "${short}"; then
  image_tag="${short}"
else
  # Not necessarily "no such tag": a denied or failed probe lands here too, and
  # its AWS error is printed above. Saying "missing" would hide that.
  echo "Aborting deploy of ${short}: could not confirm moomoo-api-mcp:${short} (missing, or the AWS check failed — see any error above). Check that CI's main push landed." >&2
  exit 1
fi
tunnel_image=""
if [ "$tunnel_selected" = true ]; then
  if ! tunnel_digest="$(aws ecr batch-get-image --region "$region" --registry-id "$account" \
    --repository-name moomoo-api-mcp --image-ids "imageTag=tunnel-${short}" \
    --query 'images[0].imageId.imageDigest' --output text)" \
    || [[ ! "$tunnel_digest" =~ ^sha256:[0-9a-f]{64}$ ]]; then
    echo "Aborting deploy of ${short}: could not confirm moomoo-api-mcp:tunnel-${short}. Check CI publication and any AWS error above." >&2
    exit 1
  fi
  tunnel_image="$registry/moomoo-api-mcp@$tunnel_digest"
fi
echo "Deploying ${short} as ${image_tag}" >&2

# Read the whole function before checkout can replace this script on disk.
finish_deploy() {
  local previous_commit=""
  local previous_env=""
  local has_previous_env=false
  local previous_image=""
  local previous_selection=""
  local tunnel_stopped=false
  local deployment_project=""
  if [ -f .chatgpt-deploy.json ]; then
    previous_selection="$(cat .chatgpt-deploy.json)"
  fi

  cleanup_images() {
    local current_image images repository image_repository tag image_id
    repository="$registry/moomoo-api-mcp"
    # Inspect the actual container: --prepare may have overwritten the saved
    # tag, and mutable tags may no longer identify the image it was using.
    if [ -z "$previous_image" ]; then
      echo 'Skipping image cleanup: no previous container image was identified.' >&2
      return 0
    fi
    if ! current_image="$(docker --context rootless container inspect \
      --format '{{.Image}}' moomoo-api-mcp)" || [ -z "$current_image" ]; then
      echo 'Skipping image cleanup: could not identify the current container image.' >&2
      return 0
    fi
    # A redeploy of the same image must not evict the older rollback image.
    if [ "$current_image" = "$previous_image" ]; then
      return 0
    fi
    if ! images="$(docker --context rootless image ls --no-trunc \
      --format '{{.Repository}} {{.Tag}} {{.ID}}' "$repository")"; then
      echo 'Skipping image cleanup: could not list local images.' >&2
      return 0
    fi
    while read -r image_repository tag image_id; do
      [ "$image_repository" = "$repository" ] || continue
      [ "$tag" != '<none>' ] && [ -n "$tag" ] || continue
      # Tunnel images share the ECR repository, but have their own lifecycle.
      [[ "$tag" == tunnel-* ]] && continue
      [ "$image_id" != "$current_image" ] || continue
      [ "$image_id" != "$previous_image" ] || continue
      # Remove repository tags, never force image-ID deletion: shared tags
      # in other repositories and images used by containers stay protected.
      docker --context rootless image rm "$repository:$tag" \
        || echo "Could not remove old app image $repository:$tag; continuing." >&2
    done <<< "$images"
    return 0
  }

  rollback() {
    local started_services="${1:-true}"
    if [ "$tunnel_stopped" = true ]; then started_services=true; fi
    if [ -n "$previous_selection" ]; then
      printf '%s\n' "$previous_selection" > .chatgpt-deploy.json
    else
      if [ "$started_services" = true ] && [ -f .chatgpt-deploy.json ]; then
        ./scripts/compose-prod.sh stop chatgpt-tunnel >/dev/null 2>&1 \
          || echo 'Could not stop the newly enabled tunnel; rollback will retry cleanup.' >&2
        ./scripts/compose-prod.sh rm -f chatgpt-tunnel >/dev/null 2>&1 \
          || echo 'Could not remove the newly enabled tunnel; verify rollback cleanup.' >&2
      fi
      rm -f .chatgpt-deploy.json
    fi
    if [ "$has_previous_env" = true ]; then
      printf '%s\n' "$previous_env" > .deploy.env
      git checkout --quiet --detach "$previous_commit"
      local prev_short="${previous_commit:0:7}"
      if [ "$started_services" = true ]; then
        local rollback_project=()
        if [ -z "$previous_selection" ] && [ -n "$deployment_project" ]; then
          rollback_project=(-p "$deployment_project")
        fi
        if ! ./scripts/compose-prod.sh "${rollback_project[@]}" up -d --remove-orphans; then
          echo "Rolled back configuration and checkout to ${prev_short}, but restarting prior images failed. Stack needs manual attention: retry scripts/deploy.sh for that commit." >&2
          exit 1
        fi
      fi
      echo "Rolled back to ${prev_short}" >&2
    else
      rm -f .deploy.env
      git checkout --quiet --detach "$previous_commit"
      echo "No previous deploy state to roll back to; checkout restored." >&2
    fi
    exit 1
  }

  previous_commit="$(git rev-parse HEAD)"
  if [ -f .deploy.env ]; then
    previous_env="$(cat .deploy.env)"
    has_previous_env=true
  fi
  if [ "$prepare" = false ]; then
    previous_image="$(docker --context rootless container inspect \
      --format '{{.Image}}' moomoo-api-mcp 2>/dev/null)" || previous_image=""
  fi

  if [ -f .chatgpt-deploy.json ]; then
    local values
    values="$(python3 scripts/tunnel_deployment.py compose-values)" || rollback false
    deployment_project="${values##*$'\n'}"
  elif [ -f .deploy.env ]; then
    local line
    while IFS= read -r line; do
      if [ "${line%%=*}" = COMPOSE_PROJECT_NAME ]; then
        deployment_project="${line#*=}"
      fi
    done < .deploy.env
  fi
  if [ "$tunnel_mode" = disable ] && [ -f .chatgpt-deploy.json ]; then
    tunnel_stopped=true
    python3 scripts/tunnel_deployment.py disable || rollback true
  fi

  if [ "$tunnel_selected" = true ] && [ -z "$deployment_project" ]; then
    deployment_project="$(docker --context rootless container inspect \
      --format '{{index .Config.Labels "com.docker.compose.project"}}' moomoo-api-mcp 2>/dev/null)" || deployment_project=""
    # The default Compose project name is the checkout directory on a new host.
    if [ -z "$deployment_project" ]; then
      local existing
      existing="$(docker --context rootless container ls -aq \
        --filter 'name=^/moomoo-api-mcp$')" || rollback false
      if [ -n "$existing" ]; then
        echo 'Could not identify the existing Compose project; deployment stopped.' >&2
        rollback false
      fi
      deployment_project="$(basename "$REPO_ROOT")"
    fi
  fi

  git checkout --quiet --detach "$commit" || rollback false
  if [ ! -f docker-compose.prod.yml ] || [ ! -x scripts/compose-prod.sh ] \
    || [ ! -f scripts/deploy_verify.py ]; then
    echo 'Target commit lacks production deployment files; choose a newer commit.' >&2
    rollback false
  fi
  printf 'ECR_REGISTRY=%s\nIMAGE_TAG=%s\n' "$registry" "${image_tag}" > .deploy.env
  if [ -n "$deployment_project" ]; then
    if [[ ! "$deployment_project" =~ ^[a-z0-9][a-z0-9_-]*$ ]]; then
      echo 'Invalid saved Compose project; deployment stopped.' >&2
      rollback false
    fi
    printf 'COMPOSE_PROJECT_NAME=%s\n' "$deployment_project" >> .deploy.env
  fi
  if [ -n "$tunnel_image" ]; then
    if [ -f .chatgpt-deploy.json ]; then
      python3 scripts/tunnel_deployment.py set-image --image "$tunnel_image" || rollback false
    else
      python3 scripts/tunnel_deployment.py select --image "$tunnel_image" \
        --project "$deployment_project" || rollback false
    fi
  fi

  # Compose resolves the configuration; the helper only reads the result. It
  # runs from the checkout just made, never from wherever this script was
  # started — after a self-reexec that is a temporary file — so the helper,
  # compose-prod.sh and the compose files all come from the target commit, with
  # the .deploy.env just written.
  local verify_helper="$REPO_ROOT/scripts/deploy_verify.py"
  if [ "$prepare" = false ]; then
    # Nothing has been started yet, so there is nothing to restart either.
    python3 "$verify_helper" check-config || rollback false
    ./scripts/compose-prod.sh pull || rollback false
    # --remove-orphans clears containers left behind by manual troubleshooting,
    # which otherwise fail the start with "container name is already in use".
    ./scripts/compose-prod.sh up -d --remove-orphans || rollback true
    # Verified means the endpoint accepted the configured token and answered
    # an MCP initialize with a valid result — not that OpenD is logged in to
    # the broker.
    if python3 "$verify_helper" verify \
      --url "${DEPLOY_VERIFY_URL:-http://127.0.0.1:8000/mcp}" \
      --timeout "${DEPLOY_VERIFY_TIMEOUT:-90}"; then
      echo "MCP deployment verified: ${short} as ${image_tag}" >&2
      # Readiness is separate from deployment verification: missing login state,
      # broker outages and health diagnostics cannot be fixed by image rollback.
      if python3 "$verify_helper" gateway-readiness \
        --url "${DEPLOY_VERIFY_URL:-http://127.0.0.1:8000/mcp}" \
        --timeout "${DEPLOY_GATEWAY_TIMEOUT:-60}"; then
        :
      else
        local gateway_status=$?
        if [ "$gateway_status" -ge 128 ]; then
          echo 'Readiness check interrupted; the verified deployment remains running.' >&2
          return "$gateway_status"
        fi
        echo 'OpenD readiness not confirmed; the verified deployment remains running.' >&2
      fi
      ./scripts/compose-prod.sh logs --tail=200 moomoo-mcp \
        || echo 'Could not collect the moomoo-mcp logs.' >&2
      cleanup_images
    else
      echo "Deploy verification failed for ${short}; see the reason above." >&2
      # Diagnostics only: failing to collect them must not stop the rollback.
      ./scripts/compose-prod.sh logs --tail=200 moomoo-mcp \
        || echo 'Could not collect the moomoo-mcp logs.' >&2
      rollback true
    fi
  else
    python3 "$verify_helper" check-config || rollback false
    ./scripts/compose-prod.sh pull || rollback false
  fi
}
finish_deploy
