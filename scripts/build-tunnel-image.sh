#!/usr/bin/env bash
# Only enumerated public inputs enter the build context. No repository-wide COPY.
set -euo pipefail
root=$(cd "$(dirname "$0")/.." && pwd)
staging=$(mktemp -d)
trap 'rm -rf "$staging"' EXIT
for name in Dockerfile runtime.py tunnel-client.yaml; do
  cp "$root/deploy/tunnel-client/container/$name" "$staging/$name"
done
cp "$root/deploy/tunnel-client/release.json" "$root/deploy/tunnel-client/install.py" "$staging/"
cp "$root/scripts/private_chatgpt_preflight.py" "$staging/"
input_digest=$(python3 - "$staging" <<'HASH'
import hashlib, pathlib, sys
root = pathlib.Path(sys.argv[1])
digest = hashlib.sha256()
for path in sorted(root.iterdir()):
    digest.update(path.name.encode() + b"\0" + path.read_bytes() + b"\0")
print(digest.hexdigest())
HASH
)
docker --context "${DOCKER_CONTEXT:-rootless}" build --platform linux/amd64 \
  --label "org.opencontainers.image.revision=$(git -C "$root" rev-parse HEAD)" \
  --label "org.moomoo.tunnel.inputs-sha256=$input_digest" \
  --label "org.moomoo.tunnel.acceptance-profile=managed-official-client" \
  --tag "${TUNNEL_IMAGE_TAG:-moomoo-chatgpt-tunnel:development}" "$staging"
