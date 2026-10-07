#!/usr/bin/env bash
# Shared local/CI pin; keep aligned with scripts/update-openspec.sh.
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
npx -y @fission-ai/openspec@1.14.1 validate --all --strict --no-interactive
