#!/usr/bin/env bash
# No real credentials: only disposable local fixtures. Requires a built broker image.
set -euo pipefail
cd "$(dirname "$0")/.."
exec uv run --frozen python tests/fixtures/tunnel_container_checks.py \
  --docker-context "${DOCKER_CONTEXT:-rootless}" \
  --broker-image "${1:?Supply an existing brokerage image}" \
  --tunnel-image "${2:-moomoo-chatgpt-tunnel:development}"
