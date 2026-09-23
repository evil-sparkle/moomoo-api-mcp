#!/usr/bin/env bash
# Usage: deploy.sh [--prepare] [commit]
set -euo pipefail
ORIGINAL_ARGS=("$@")

REPO_ROOT="${REPO_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
cd "$REPO_ROOT"
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

# One image now carries both the gateway and the server, so there is one tag to
# confirm rather than two that had to agree.
if ecr_has_tag moomoo-api-mcp "${short}"; then
  image_tag="${short}"
else
  # Not necessarily "no such tag": a denied or failed probe lands here too, and
  # its AWS error is printed above. Saying "missing" would hide that.
  echo "Aborting deploy of ${short}: could not confirm moomoo-api-mcp:${short} (missing, or the AWS check failed — see any error above). Check that CI's main push landed." >&2
  exit 1
fi
echo "Deploying ${short} as ${image_tag}" >&2

# Read the whole function before checkout can replace this script on disk.
finish_deploy() {
  local previous_commit=""
  local previous_env=""
  local has_previous_env=false
  local previous_image=""

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
    previous_image="$(docker --context rootless container inspect \
      --format '{{.Image}}' moomoo-api-mcp 2>/dev/null)" || previous_image=""
    previous_commit="$(git rev-parse HEAD)"
    if [ -f .deploy.env ]; then
      previous_env="$(cat .deploy.env)"
      has_previous_env=true
    fi
  fi

  git checkout --quiet --detach "$commit"
  if [ ! -f docker-compose.prod.yml ] || [ ! -x scripts/compose-prod.sh ] \
    || [ ! -f scripts/deploy_verify.py ]; then
    echo 'Target commit lacks production deployment files; choose a newer commit.' >&2
    return 1
  fi
  printf 'ECR_REGISTRY=%s\nIMAGE_TAG=%s\n' "$registry" "${image_tag}" > .deploy.env

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
      echo "Deploy verified: ${short} as ${image_tag}" >&2
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
    python3 "$verify_helper" check-config
    ./scripts/compose-prod.sh pull
  fi
}
finish_deploy
