#!/usr/bin/env bash
# Shared CI/development preparation; stdout is the digest of public build inputs.
set -euo pipefail
if [ "$#" != 1 ]; then
  echo 'Usage: prepare-tunnel-build-context.sh <new-directory>' >&2
  exit 1
fi
root=$(cd "$(dirname "$0")/.." && pwd)
staging="$1"
# Refuse an existing context rather than transmit any unlisted files in it.
mkdir "$staging"
for name in Dockerfile runtime.py tunnel-client.yaml; do
  cp "$root/deploy/tunnel-client/container/$name" "$staging/$name"
done
cp "$root/deploy/tunnel-client/release.json" "$root/deploy/tunnel-client/install.py" "$staging/"
cp "$root/scripts/private_chatgpt_preflight.py" "$staging/"
python3 - "$staging" <<'HASH'
import hashlib, pathlib, sys
root = pathlib.Path(sys.argv[1])
digest = hashlib.sha256()
for path in sorted(root.iterdir()):
    digest.update(path.name.encode() + b"\0" + path.read_bytes() + b"\0")
print(digest.hexdigest())
HASH
