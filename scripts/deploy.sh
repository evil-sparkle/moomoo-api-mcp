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
for tool in git aws docker; do
  command -v "$tool" >/dev/null || { echo "Missing required command: $tool" >&2; exit 1; }
done
if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
  echo 'Commit or stash tracked working-tree changes before deploying.' >&2
  exit 1
fi
git fetch --quiet origin main
commit="$(git rev-parse --verify --end-of-options "${1:-origin/main}^{commit}")"
short="${commit:0:7}"

# Resolve which ECR tag to deploy under. The project's source of truth for
# release identity is `pyproject.toml`'s `version = "X.Y.Z"` — read it from
# the *target* commit so local edits don't lie. The local git tag `v<X.Y.Z>`
# must point at the same commit (validate); the ECR `:v<X.Y.Z>` tag must
# exist on both images (the lifecycle policy keeps every 'v*' image). If
# any link is broken, fall through to ':latest'.
#
# Two-stage validation, both proven:
#   git tag --points-at   — release tag really sits at this commit
#   aws ecr batch-get-image — the build matching the release is in ECR
ecr_has_tag() {
  local image="$1" tag="$2"
  local got
  got="$(aws ecr batch-get-image --region "$region" --registry-id "$account" \
    --repository-name "$image" --image-ids "imageTag=$tag" \
    --query 'images[0].imageId.imageDigest' --output text)"
  [ -n "$got" ] && [ "$got" != None ]
}

# Read pyproject.toml into a tempdir holding the target commit; this lets us
# access it even before we checkout onto that commit. `git show` is cheap.
version="$(git show "${commit}:pyproject.toml" \
  | awk -F'"' '/^version[[:space:]]*=/ { print $2; exit }')"

resolve_image_tag() {
  if [ -n "${version}" ]; then
    local release_tag="v${version}"
    if git tag --points-at "${commit}" "${release_tag}" >/dev/null 2>&1; then
      if ecr_has_tag moomoo-api-mcp "${release_tag}" && ecr_has_tag moomoo-opend "${release_tag}"; then
        printf '%s' "${release_tag}"
        return 0
      fi
      echo "Release tag ${release_tag} exists at ${short} but ECR is missing one or both images; falling back to :latest." >&2
    else
      echo "pyproject.toml at ${short} declares version=${version} but no ${release_tag} git tag is at this commit; falling back to :latest." >&2
    fi
  fi
  if ecr_has_tag moomoo-api-mcp latest && ecr_has_tag moomoo-opend latest; then
    printf '%s' latest
    return 0
  fi
  return 1
}

image_tag="$(resolve_image_tag || true)"
if [ -z "${image_tag}" ]; then
  echo "Aborting deploy of ${short}: ECR is missing :${release_tag:-v${version:-<none>}} and/or :latest on one or both images. Check that CI's main push landed for both moomoo-api-mcp and moomoo-opend." >&2
  exit 1
fi
echo "Deploying ${short} as ${image_tag}" >&2

# Read the whole function before checkout can replace this script on disk.
finish_deploy() {
  git checkout --quiet --detach "$commit"
  if [ ! -f docker-compose.prod.yml ] || [ ! -x scripts/compose-prod.sh ]; then
    echo 'Target commit lacks production deployment files; choose a newer commit.' >&2
    return 1
  fi
  printf 'ECR_REGISTRY=%s\nIMAGE_TAG=%s\n' "$registry" "${image_tag}" > .deploy.env
  ./scripts/compose-prod.sh pull
  if [ "$prepare" = false ]; then
    # --remove-orphans clears containers left behind by manual troubleshooting,
    # which otherwise fail the start with "container name is already in use".
    ./scripts/compose-prod.sh up -d --remove-orphans
    ./scripts/compose-prod.sh logs --tail=200 opend moomoo-mcp
  fi
}
finish_deploy
