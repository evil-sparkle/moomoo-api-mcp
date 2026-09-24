#!/usr/bin/env bash
# Exercise the documented ownership/mode boundary in a disposable container.
set -euo pipefail
cd "$(dirname "$0")/.."
image=${1:?Supply the built application image}
fixture="$(pwd)/tests/fixtures/tunnel_host_permissions.py"
installer="$(pwd)/scripts/install_private_chatgpt_credential.py"

docker run --rm --network none --user 0:0 \
  --mount "type=bind,src=$fixture,dst=/tmp/tunnel_host_permissions.py,readonly" \
  --mount "type=bind,src=$installer,dst=/tmp/install_private_chatgpt_credential.py,readonly" \
  --entrypoint python "$image" /tmp/tunnel_host_permissions.py
