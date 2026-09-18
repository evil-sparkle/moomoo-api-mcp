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
# The token the server is started with, so the verification probe can
# authenticate the way a real client does. Only ever sent as a header, never
# echoed; the parsing mirrors the ECR_REGISTRY block above.
mcp_auth_token="${MCP_AUTH_TOKEN:-}"
if [ -z "$mcp_auth_token" ] && [ -f .env ]; then
  while IFS= read -r line; do
    if [ "${line%%=*}" = MCP_AUTH_TOKEN ]; then
      mcp_auth_token="${line#*=}"
    fi
  done < .env
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
    # A plain GET proves only that something is listening, and with bearer auth
    # on it answers 401 — which is exactly the line an operator then finds at
    # the bottom of a *successful* deploy's logs. Probe as a real client
    # instead: the same authenticated initialize smoke-test.sh sends. The
    # access log then shows POST /mcp 200 for a healthy deploy, and a 401/403
    # stops the deploy with a diagnosis instead of decorating a success.
    local -a probe_args=(
      -s -o /dev/null -w '%{http_code}' --max-time 10
      -H 'Content-Type: application/json'
      -H 'Accept: application/json, text/event-stream'
    )
    if [ -n "$mcp_auth_token" ]; then
      probe_args+=(-H "Authorization: Bearer ${mcp_auth_token}")
    fi
    # Note: Verification proves the MCP endpoint completes an initialize, not
    # that OpenD is logged in to the broker.
    while :; do
      local code
      code="$(curl "${probe_args[@]}" -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"deploy-verify","version":"0"}}}' "$verify_url" 2>/dev/null || true)"
      [ -z "$code" ] && code="000"
      case "$code" in
        # Not listening yet, or a server error it may grow out of: keep polling.
        000 | 5?? ) ;;
        # The endpoint completed an MCP handshake.
        200 ) verified=true; break ;;
        # Listening but refusing the probe: a wrong MCP_AUTH_TOKEN, or a
        # verify_url that does not speak MCP. Polling longer cannot fix either.
        * ) break ;;
      esac
      if [ "$timeout" = "0" ] || [ "$elapsed" -ge "$timeout" ]; then
        break
      fi
      sleep 3
      elapsed=$((elapsed + 3))
    done
    if [ "$verified" = true ]; then
      echo "Deploy verified: ${short} as ${image_tag}" >&2
      ./scripts/compose-prod.sh logs --tail=200 moomoo-mcp
    else
      echo "Deploy verification failed (last HTTP status from ${verify_url}: ${code})." >&2
      if [ "$code" = 401 ] || [ "$code" = 403 ]; then
        echo "The server is up but rejected the deploy probe: MCP_AUTH_TOKEN in .env does not match what the server was started with. Clients using that token are refused the same way." >&2
      fi
      ./scripts/compose-prod.sh logs --tail=200 moomoo-mcp
      rollback true
    fi
  else
    ./scripts/compose-prod.sh pull
  fi
}
finish_deploy
