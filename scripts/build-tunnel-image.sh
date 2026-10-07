#!/usr/bin/env bash
# Development/disposable-test helper. Production pulls the CI-published ECR image.
set -euo pipefail
root=$(cd "$(dirname "$0")/.." && pwd)
staging=$(mktemp -d)
trap 'rm -rf "$staging"' EXIT
input_digest=$("$root/scripts/prepare-tunnel-build-context.sh" "$staging/context")
docker --context "${DOCKER_CONTEXT:-rootless}" build --platform linux/amd64 \
  --label "org.opencontainers.image.revision=$(git -C "$root" rev-parse HEAD)" \
  --label "org.moomoo.tunnel.inputs-sha256=$input_digest" \
  --label "org.moomoo.tunnel.acceptance-profile=managed-official-client" \
  --tag "${TUNNEL_IMAGE_TAG:-moomoo-chatgpt-tunnel:development}" "$staging/context"
