#!/usr/bin/env bash
# Usage: deploy.sh [--prepare] [commit]
set -euo pipefail
cd "$(dirname "$0")/.."
prepare=false
if [ "${1:-}" = "--prepare" ]; then
  prepare=true
  shift
fi
if [ "$#" -gt 1 ]; then
  echo 'Usage: scripts/deploy.sh [--prepare] [commit]' >&2
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
tools=(git aws docker)
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
if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
  echo 'Commit or stash tracked working-tree changes before deploying.' >&2
  exit 1
fi
git fetch --quiet origin main
commit="$(git rev-parse --verify --end-of-options "${1:-origin/main}^{commit}")"
short="${commit:0:7}"

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

if ecr_has_tag moomoo-api-mcp "${short}" && ecr_has_tag moomoo-opend "${short}"; then
  image_tag="${short}"
else
  # Not necessarily "no such tag": a denied or failed probe lands here too, and
  # its AWS error is printed above. Saying "missing" would hide that.
  echo "Aborting deploy of ${short}: could not confirm :${short} on both images (missing, or the AWS check failed — see any error above). Check that CI's main push landed for both moomoo-api-mcp and moomoo-opend." >&2
  exit 1
fi
echo "Deploying ${short} as ${image_tag}" >&2

# Read the whole function before checkout can replace this script on disk.
finish_deploy() {
  local previous_commit=""
  local previous_env=""
  local has_previous_env=false

  rollback() {
    local started_services="${1:-true}"
    if [ "$has_previous_env" = true ]; then
      printf '%s\n' "$previous_env" > .deploy.env
      git checkout --quiet --detach "$previous_commit"
      local prev_short="${previous_commit:0:7}"
      if [ "$started_services" = true ]; then
        if ! ./scripts/compose-prod.sh up -d --remove-orphans; then
          echo "Rolled back .deploy.env and the checkout to ${prev_short} but restarting the previous images FAILED. Stack needs manual attention: scripts/compose-prod.sh up -d" >&2
          exit 1
        fi
      fi
      echo "Rolled back to ${prev_short}" >&2
    else
      echo "No previous deploy state to roll back to." >&2
    fi
    exit 1
  }

  if [ "$prepare" = false ]; then
    previous_commit="$(git rev-parse HEAD)"
    if [ -f .deploy.env ]; then
      previous_env="$(cat .deploy.env)"
      has_previous_env=true
    fi
  fi

  git checkout --quiet --detach "$commit"
  if [ ! -f docker-compose.prod.yml ] || [ ! -x scripts/compose-prod.sh ]; then
    echo 'Target commit lacks production deployment files; choose a newer commit.' >&2
    return 1
  fi
  printf 'ECR_REGISTRY=%s\nIMAGE_TAG=%s\n' "$registry" "${image_tag}" > .deploy.env

  if [ "$prepare" = false ]; then
    ./scripts/compose-prod.sh pull || rollback false
    # --remove-orphans clears containers left behind by manual troubleshooting,
    # which otherwise fail the start with "container name is already in use".
    ./scripts/compose-prod.sh up -d --remove-orphans || rollback true
    local timeout="${DEPLOY_VERIFY_TIMEOUT:-90}"
    local verify_url="${DEPLOY_VERIFY_URL:-http://127.0.0.1:8000/mcp}"
    local elapsed=0
    local verified=false
    # Note: Verification proves containers + MCP listening only, not OpenD login.
    while :; do
      local ps_out=""
      ps_out="$(./scripts/compose-prod.sh ps --status running --services 2>/dev/null || true)"
      local opend_running=false
      local mcp_running=false
      while IFS= read -r sline; do
        if [ "$sline" = "opend" ]; then opend_running=true; fi
        if [ "$sline" = "moomoo-mcp" ]; then mcp_running=true; fi
      done <<< "$ps_out"
      if [ "$opend_running" = true ] && [ "$mcp_running" = true ]; then
        local code
        code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "$verify_url" 2>/dev/null || true)"
        [ -z "$code" ] && code="000"
        case "$code" in
          000 | 5?? | ? | ?? | ????* ) ;;
          *) verified=true; break ;;
        esac
      fi
      if [ "$timeout" = "0" ] || [ "$elapsed" -ge "$timeout" ]; then
        break
      fi
      sleep 3
      elapsed=$((elapsed + 3))
    done
    if [ "$verified" = true ]; then
      echo "Deploy verified: ${short} as ${image_tag}" >&2
      ./scripts/compose-prod.sh logs --tail=200 opend moomoo-mcp
    else
      echo "Deploy verification failed." >&2
      ./scripts/compose-prod.sh logs --tail=200 opend moomoo-mcp
      rollback true
    fi
  else
    ./scripts/compose-prod.sh pull
  fi
}
finish_deploy
