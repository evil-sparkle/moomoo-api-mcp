#!/usr/bin/env bash
# Regenerate managed integrations with the repo's exact CLI and workflow profile.
set -euo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
profile_dir="$(mktemp -d)"
trap 'rm -rf -- "$profile_dir"' EXIT
mkdir -p "$profile_dir/openspec"
cp "$repo_root/openspec/profile.json" "$profile_dir/openspec/config.json"
cd "$repo_root"
XDG_CONFIG_HOME="$profile_dir" OPENSPEC_TELEMETRY=0 \
  npx -y @fission-ai/openspec@1.14.1 init \
    --tools antigravity,claude,codex,opencode --profile custom --force --no-animation
