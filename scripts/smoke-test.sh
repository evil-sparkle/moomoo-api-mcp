#!/usr/bin/env bash
# Run the deployed container topology and prove it works.
#
# tests/test_compose_topology.py reads the compose file; it never runs it. This
# does. It asserts what broke before: the MCP server reaches the gateway by
# service name over trading-net, its endpoint is published by the container
# that serves it, and restarting the gateway leaves both intact. Under the
# previous topology — moomoo-mcp sharing OpenD's network namespace — that
# restart stranded the MCP container in an orphaned namespace, where its
# gateway connection was refused forever and its published port answered
# nothing.
#
# The real gateway needs an interactive device login and Moomoo's own servers,
# so docker-compose.smoke.yml swaps the OpenD binary for a stand-in listener.
# It swaps nothing else: the networking under test is the deployed file's.
set -euo pipefail
cd "$(dirname "$0")/.."

# Never the deployment's own project name. The cleanup below runs `down -v`,
# which under that name would take the real opend-data volume — and with it the
# device tokens that only an interactive login can replace.
PROJECT="moomoo-smoke"
ENDPOINT="http://127.0.0.1:8000/mcp"
TOKEN="smoke-test-token-not-a-secret"

ENV_FILE="$(mktemp)"
# Explicit, because Compose otherwise reads whatever .env a developer has, and
# this run would assert against their configuration instead of the default one.
printf 'MCP_AUTH_TOKEN=%s\n' "$TOKEN" > "$ENV_FILE"

dc() {
  docker compose -p "$PROJECT" --env-file "$ENV_FILE" \
    -f docker-compose.yml -f docker-compose.smoke.yml "$@"
}

cleanup() {
  local status=$?
  if [ "$status" -ne 0 ]; then
    echo "--- opend ---" >&2
    dc logs --tail=30 opend >&2 || true
    echo "--- moomoo-mcp ---" >&2
    dc logs --tail=30 moomoo-mcp >&2 || true
  fi
  dc down -v --remove-orphans >/dev/null 2>&1 || true
  rm -f "$ENV_FILE"
}
trap cleanup EXIT

# Connections the stand-in gateway has accepted. The MCP server's own SDK
# dialling in is the evidence that trading-net resolves and routes end to end;
# a probe exec'd from this script would only prove that this script can.
gateway_connections() {
  dc logs opend 2>/dev/null | grep -c CONNECT || true
}

endpoint_status() {
  curl -s -o /dev/null -w '%{http_code}' --max-time 5 "$@" "$ENDPOINT" || true
}

gateway_accepted_more_than() {
  [ "$(gateway_connections)" -gt "$1" ]
}

# 401 is this server answering: no other process on the host publishes 8000
# with a bearer check in front of it. A dead endpoint reports 000 instead.
endpoint_rejects_anonymous() {
  [ "$(endpoint_status)" = "401" ]
}

# Identity of the running MCP container. A gateway restart must not disturb it:
# restarting the server would drop every streamable-HTTP session, which are held
# in memory, and that is the disruption the separated namespaces exist to avoid.
mcp_instance() {
  local id
  id="$(dc ps -q moomoo-mcp)"
  printf '%s %s' "${id}" "$(docker inspect -f '{{.State.StartedAt}}' "${id}")"
}

wait_for() {
  local limit="$1" what="$2"
  shift 2
  local deadline=$(( SECONDS + limit ))
  until "$@"; do
    if [ "$SECONDS" -ge "$deadline" ]; then
      echo "FAILED: timed out after ${limit}s waiting for ${what}." >&2
      return 1
    fi
    sleep 2
  done
}

echo "==> starting the stack"
dc up -d --build --quiet-pull

echo "==> the MCP server reaches the gateway at opend:11111"
wait_for 180 "the MCP server to reach opend:11111" gateway_accepted_more_than 0

echo "==> the MCP endpoint answers on 127.0.0.1:8000"
wait_for 60 "the MCP endpoint to answer" endpoint_rejects_anonymous

authorized="$(endpoint_status -H "Authorization: Bearer ${TOKEN}")"
if [ "${authorized}" = "401" ]; then
  echo "FAILED: a valid bearer token was rejected, so whatever answers on" \
    "127.0.0.1:8000 is not this server." >&2
  exit 1
fi

# The regression itself. Everything above passed before the fix too; only this
# part did not.
echo "==> restarting the gateway"
accepted_before="$(gateway_connections)"
mcp_before="$(mcp_instance)"
dc restart opend

echo "==> the MCP server reconnects to the restarted gateway"
wait_for 180 "the MCP server to reconnect" gateway_accepted_more_than "${accepted_before}"

echo "==> the MCP endpoint still answers"
wait_for 30 "the MCP endpoint to survive the gateway restart" \
  endpoint_rejects_anonymous

echo "==> the MCP server itself was left running"
if [ "$(mcp_instance)" != "${mcp_before}" ]; then
  echo "FAILED: restarting the gateway restarted the MCP server too, which ends" \
    "every client session. Check depends_on in docker-compose.yml." >&2
  exit 1
fi

echo "PASSED: the stack survives a gateway restart."
