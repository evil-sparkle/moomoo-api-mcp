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
# a client sends its first request. This opens a real MCP session to provoke
# that connection, which also means the restart assertions are made against a
# live client rather than an idle server.
#
# Both containers get restarted, because both used to break a client: the
# gateway's restart stranded the MCP server in a dead network namespace, and
# the MCP server's restart invalidated the client's session.
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

# Connections accepted by the gateway process running *now*. The stand-in
# prints LISTENING once when it starts, so counting only what follows the most
# recent one makes a restart reset the evidence: afterwards, anything counted
# here necessarily reached the new gateway. Comparing raw totals across a
# restart proved too weak — connections to the outgoing gateway pushed the
# count up on their own, and the assertion passed without a reconnect.
#
# The MCP server's own SDK dialling in is the evidence that trading-net
# resolves and routes end to end; a probe exec'd from this script would only
# prove that this script can reach the gateway.
gateway_connections() {
  dc logs opend 2>/dev/null |
    awk '/LISTENING/ { seen = 0; next } /CONNECT/ { seen++ } END { print seen + 0 }'
}

# One line per gateway process start, so this only increases when the container
# has genuinely come back — never while the old one is still being shut down.
gateway_starts() {
  dc logs opend 2>/dev/null | grep -c LISTENING || true
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
  mcp_request "" '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"smoke-test","version":"0"}}}' headers
}

# The id a stateful server would hand out. This one is stateless and issues
# none, which is the whole point: a client holding nothing has nothing a
# restart can invalidate. Captured anyway, so the calls below carry a real
# session id if the server is ever switched back, and so this script keeps
# testing whatever the server actually does rather than what it did once.
session_id_from() {
  # `|| true` because no match is the expected case, not a failure: a stateless
  # server sends no such header, and under `set -o pipefail` an unmatched grep
  # would take the whole script down without printing a thing.
  printf '%s' "$1" | grep -i '^mcp-session-id:' | tr -d '\r' | awk '{print $2}' ||
    true
}

# Proof the session still works: a tool this server defines comes back in the
# listing. A dead or forgotten session answers with an error instead.
session_still_lists_tools() {
  mcp_request "$1" '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' |
    grep -q '"name":"check_health"'
}

gateway_has_connections() {
  [ "$(gateway_connections)" -gt 0 ]
}

gateway_restarted_since() {
  [ "$(gateway_starts)" -gt "$1" ]
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
handshake="$(open_mcp_session)"
if ! printf '%s' "${handshake}" | grep -q '"serverInfo"'; then
  echo "FAILED: initialize did not return a server result, so no client can" \
    "use this server at all." >&2
  exit 1
fi
session="$(session_id_from "${handshake}")"
mcp_request "${session}" '{"jsonrpc":"2.0","method":"notifications/initialized"}' \
  > /dev/null

echo "==> the MCP server reaches the gateway at opend:11111"
wait_for 120 "the MCP server to reach opend:11111" gateway_has_connections
echo "    (connections accepted by this gateway: $(gateway_connections))"

# The regression itself. Everything above passed before the fix too; only this
# part did not.
echo "==> restarting the gateway"
mcp_before="$(mcp_instance)"
starts_before="$(gateway_starts)"
dc restart opend

# Wait for the gateway's own start marker before judging anything, so the count
# below is read against the new process and not the departing one.
wait_for 60 "the gateway to come back up" gateway_restarted_since "${starts_before}"

echo "==> the MCP server reconnects to the restarted gateway"
wait_for 180 "the MCP server to reconnect" gateway_has_connections
echo "    (connections accepted since the restart: $(gateway_connections))"

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

# The other half of the promise. A gateway restart is the easy one: the MCP
# server stays up and the SDK reconnects underneath it. This is the hard one —
# the process holding the client's session is the one going away. A stateful
# server answers the next call with 404 and the client has to initialize again,
# which is exactly the interruption someone has to notice and fix by hand.
echo "==> restarting the MCP server"
dc restart moomoo-mcp
wait_for 90 "the MCP server to come back" endpoint_rejects_anonymous

echo "==> the client keeps calling without re-initializing"
if ! session_still_lists_tools "${session}"; then
  echo "FAILED: a call that worked before the MCP server restarted no longer" \
    "does, so every client has to reconnect when it bounces." >&2
  exit 1
fi

echo "PASSED: the stack survives a restart of either container."
