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
#
# Note the order of the checks below. Under streamable HTTP the MCP lifespan
# runs per session, inside Server.run() — not once at process start. A freshly
# started container has therefore not dialled the gateway, and never will until
# a client initializes a session. This opens a real MCP session to provoke that
# connection, which also means the restart assertions below are made against a
# live client session rather than an idle server.
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

# An MCP client, reduced to the three calls this needs. initialize is what makes
# the server enter its lifespan and connect to the gateway; the session id it
# returns is what a real client would hold, and what must still work afterwards.
mcp_request() {
  local session="$1" body="$2" show_headers="${3:-}"
  local -a args=(
    -s --max-time 30
    -H "Authorization: Bearer ${TOKEN}"
    -H "Content-Type: application/json"
    -H "Accept: application/json, text/event-stream"
  )
  [ -n "${session}" ] && args+=(-H "mcp-session-id: ${session}")
  [ -n "${show_headers}" ] && args+=(-i)
  curl "${args[@]}" -d "${body}" "$ENDPOINT" || true
}

open_mcp_session() {
  mcp_request "" '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"smoke-test","version":"0"}}}' headers |
    grep -i '^mcp-session-id:' | tr -d '\r' | awk '{print $2}'
}

# Proof the session still works: a tool this server defines comes back in the
# listing. A dead or forgotten session answers with an error instead.
session_still_lists_tools() {
  mcp_request "$1" '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' |
    grep -q '"name":"check_health"'
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

echo "==> the MCP endpoint answers on 127.0.0.1:8000"
wait_for 60 "the MCP endpoint to answer" endpoint_rejects_anonymous

authorized="$(endpoint_status -H "Authorization: Bearer ${TOKEN}")"
if [ "${authorized}" = "401" ]; then
  echo "FAILED: a valid bearer token was rejected, so whatever answers on" \
    "127.0.0.1:8000 is not this server." >&2
  exit 1
fi

echo "==> a client can open an MCP session"
session="$(open_mcp_session)"
if [ -z "${session}" ]; then
  echo "FAILED: initialize returned no mcp-session-id, so no client can use" \
    "this server at all." >&2
  exit 1
fi
mcp_request "${session}" '{"jsonrpc":"2.0","method":"notifications/initialized"}' \
  > /dev/null

echo "==> the MCP server reaches the gateway at opend:11111"
wait_for 120 "the MCP server to reach opend:11111" gateway_accepted_more_than 0

# The regression itself. Everything above passed before the fix too; only this
# part did not.
echo "==> restarting the gateway"
mcp_before="$(mcp_instance)"
dc restart opend

# Counted after the restart returns, never before it. The stand-in is PID 1, so
# it ignores SIGTERM and keeps accepting for the whole stop grace period: a
# count taken before `restart` is still climbing while the gateway is on its way
# out, and an increase against it proves nothing. Against a count taken once the
# new container is up, only a connection to that container can satisfy it.
accepted_before="$(gateway_connections)"

echo "==> the MCP server reconnects to the restarted gateway"
wait_for 180 "the MCP server to reconnect" gateway_accepted_more_than "${accepted_before}"

echo "==> the MCP endpoint still answers"
wait_for 30 "the MCP endpoint to survive the gateway restart" \
  endpoint_rejects_anonymous

echo "==> the client's session survived the gateway restart"
if ! session_still_lists_tools "${session}"; then
  echo "FAILED: the MCP session opened before the restart no longer works, so a" \
    "gateway restart forces every client to reconnect." >&2
  exit 1
fi

echo "==> the MCP server itself was left running"
if [ "$(mcp_instance)" != "${mcp_before}" ]; then
  echo "FAILED: restarting the gateway restarted the MCP server too, which ends" \
    "every client session. Check depends_on in docker-compose.yml." >&2
  exit 1
fi

echo "PASSED: the stack survives a gateway restart."
