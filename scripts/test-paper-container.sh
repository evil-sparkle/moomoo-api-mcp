#!/usr/bin/env bash
# C01-C04: disposable containers/volumes, no gateway and no provider traffic.
# Usage: scripts/test-paper-container.sh IMAGE [RECREATED_IMAGE]
# Supply two independently built app images to exercise an image replacement.
set -euo pipefail
cd "$(dirname "$0")/.."
image=${1:?Supply the built application image}
recreated_image=${2:-$image}
project="paper-journal-check-$(date +%s)-$$"
execution_volume="$project-execution"
device_volume="$project-device"
holder="$project-holder"
fixture="$(pwd)/tests/fixtures/paper_container_checks.py"
cleanup() {
  for action in seed recreated holder contender restore dirty read-only; do
    docker rm -f "$project-$action" >/dev/null 2>&1 || true
  done
  docker volume rm "$execution_volume" "$device_volume" >/dev/null 2>&1 || true
}
trap cleanup EXIT
# Names are unique to this invocation. Existing deployment volumes are untouched.
docker volume create "$execution_volume" >/dev/null
docker volume create "$device_volume" >/dev/null
run() {
  docker run --rm --name "$project-$1" --network none \
    --mount "type=volume,src=$execution_volume,dst=/var/lib/moomoo-mcp/data" \
    --mount "type=volume,src=$device_volume,dst=/home/opend/.com.moomoo.OpenD" \
    --mount "type=bind,src=$fixture,dst=/tmp/paper_container_checks.py,readonly" \
    --entrypoint python "$recreated_image" /tmp/paper_container_checks.py "$@"
}
# First writer comes from the original image; all later probes use its replacement.
docker run --rm --name "$project-seed" --network none \
  --mount "type=volume,src=$execution_volume,dst=/var/lib/moomoo-mcp/data" \
  --mount "type=volume,src=$device_volume,dst=/home/opend/.com.moomoo.OpenD" \
  --mount "type=bind,src=$fixture,dst=/tmp/paper_container_checks.py,readonly" \
  --entrypoint python "$image" /tmp/paper_container_checks.py seed
run recreated
docker run -d --name "$holder" --network none \
  --mount "type=volume,src=$execution_volume,dst=/var/lib/moomoo-mcp/data" \
  --mount "type=bind,src=$fixture,dst=/tmp/paper_container_checks.py,readonly" \
  --entrypoint python "$recreated_image" /tmp/paper_container_checks.py hold >/dev/null
run contender
holder_status=$(docker wait "$holder")
docker logs "$holder"
[[ "$holder_status" == "0" ]]
run restore
run dirty
# No volume of either kind in this container.
docker run --rm --name "$project-read-only" --network none \
  --mount "type=bind,src=$fixture,dst=/tmp/paper_container_checks.py,readonly" \
  --entrypoint python "$recreated_image" /tmp/paper_container_checks.py read-only
printf '%s\n' 'C01-C04 passed (isolated container checks; live provider validation is separate).'
