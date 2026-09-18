#!/usr/bin/env bash
# Run the deployed container and prove the recovery policy works.
#
# tests/test_compose_topology.py reads the compose file; it never runs it. This
# does. It asserts what the single-container design promises and what earlier
# arrangements got wrong:
#
#   * the MCP server reaches the gateway over container loopback, and nothing
#     outside the container can reach the gateway at all;
#   * killing the gateway process restarts *the gateway*, leaving the container,
#     the MCP endpoint and a live client session untouched;
#   * killing the MCP server takes the whole container down, and Docker's
#     restart policy brings it back.
#
# That asymmetry is the policy in moomoo_mcp/supervisor.py, and it is the part
# packaging cannot be trusted to provide on its own: a supervisor that merely
# backgrounded OpenD would leave a live container serving a gateway that is no
# longer there, and Docker would never notice.
#
# The real gateway needs an interactive device login and Moomoo's own servers,
# so docker-compose.smoke.yml points OPEND_BINARY at a stand-in. It swaps
# nothing else: the image, the supervisor, the ports and the volume under test
# are the deployed file's.
#
# Note the order of the checks below. Under streamable HTTP the MCP lifespan
# runs per session, inside Server.run() — not once at process start. A freshly
# started container has therefore not dialled the gateway, and never will until
# a client sends its first request. This opens a real MCP session to provoke
# that connection, which also means the restart assertions are made against a
# live client rather than an idle server.
set -euo pipefail
cd "$(dirname "$0")/.."

# Never the deployment's own project name. The cleanup below runs `down -v`,
# which under that name would take the real opend-data volume — and with it the
# device tokens that only an interactive login can replace.
PROJECT="moomoo-smoke"
SERVICE="moomoo-mcp"
ENDPOINT="http://127.0.0.1:8000/mcp"
TOKEN="smoke-test-token-not-a-secret"
PROBE_IMAGE="python:3.12-slim"

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
    echo "--- ${SERVICE} ---" >&2
    dc logs --tail=60 "$SERVICE" >&2 || true
  fi
  dc down -v --remove-orphans >/dev/null 2>&1 || true
  rm -f "$ENV_FILE"
}
trap cleanup EXIT

# Both processes log to the same stream now, so the prefix is noise and the
# markers below are the stand-in gateway's own.
logs() {
  dc logs --no-log-prefix "$SERVICE" 2>/dev/null || true
}

# Connections accepted by the gateway process running *now*. The stand-in prints
# LISTENING once when it starts, so counting only what follows the most recent
# one makes a restart reset the evidence: afterwards, anything counted here
# necessarily reached the new gateway process. Comparing raw totals across a
# restart proved too weak — connections to the outgoing gateway pushed the count
# up on their own, and the assertion passed without a reconnect.
#
# The MCP server's own SDK dialling in is the evidence that the two halves reach
# each other; a probe run from this script would only prove that this script can.
gateway_connections() {
  logs | awk '/^LISTENING / { seen = 0; next } /^CONNECT / { seen++ } END { print seen + 0 }'
}

# One line per gateway process start, so this only increases when the gateway
# has genuinely come back.
gateway_starts() {
  logs | grep -c '^LISTENING ' || true
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
# listing, and the response is not a JSON-RPC error. A dead or forgotten session
# answers with an error instead.
#
# Three things, because this assertion has already earned its keep: the reply is
# a tool listing, it is not a JSON-RPC error, and this server's own tool is in
# it. The last one is what caught the supervisor launching the server as
# `python -m`, which served an empty tool list behind a perfectly healthy
# endpoint -- HTTP 200, sessions opening, and `{"tools":[]}` inside.
#
# The tool name is matched on its own rather than as `"name":"check_health"`:
# the response arrives as an SSE data line and the SDK's spacing is not this
# repository's to pin. The `"tools"` and `"error"` checks are what keep that
# from being a weaker test than the one it replaces.
LAST_TOOLS_BODY=""
session_still_lists_tools() {
  LAST_TOOLS_BODY="$(mcp_request "$1" '{"jsonrpc":"2.0","id":2,"method":"tools/list"}')"
  printf '%s' "${LAST_TOOLS_BODY}" | grep -q '"error"' && return 1
  printf '%s' "${LAST_TOOLS_BODY}" | grep -q '"tools"' || return 1
  printf '%s' "${LAST_TOOLS_BODY}" | grep -q 'check_health'
}

# What the server actually said, so a failure here is diagnosable from the log
# rather than from a second CI run.
show_last_tools_response() {
  echo "    the server answered:" >&2
  printf '%s\n' "${LAST_TOOLS_BODY}" | head -c 2000 >&2
  echo >&2
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

container_id() {
  dc ps -q "$SERVICE"
}

# Identity *and* incarnation. A gateway restart must disturb neither: the
# supervisor is supposed to replace one child, not let the container exit.
# Prints "gone" while the container is between incarnations, so a caller can
# tell "restarted" from "not back yet".
container_instance() {
  local id
  id="$(container_id 2>/dev/null || true)"
  if [ -z "${id}" ]; then
    printf 'gone'
    return 0
  fi
  printf '%s %s' "${id}" \
    "$(docker inspect -f '{{.State.StartedAt}}' "${id}" 2>/dev/null || echo unknown)"
}

container_restarted_since() {
  local now
  now="$(container_instance)"
  [ "${now}" != "gone" ] && [ "${now}" != "$1" ]
}

# Find one process inside the container, by scanning /proc rather than reaching
# for pgrep: the deployed image has no procps, and adding one for a test would
# change the thing under test.
#
# Finding and killing are separate on purpose. A kill that matches nothing has
# to fail *here*, loudly, rather than further down as a mystery timeout waiting
# for a container that was never going to restart -- which is exactly how a
# stale pattern hid itself once already.
#
# The second argument excludes: "moomoo-api-mcp" is a substring of
# "moomoo-api-mcp-supervisor", so asking for the server would otherwise also
# name PID 1.
pid_in_container() {
  dc exec -T "$SERVICE" sh -c '
    for proc in /proc/[0-9]*; do
      pid="${proc#/proc/}"
      # This shell carries the patterns in its own argv, and PID 1 is the
      # supervisor: naming either would aim the kill at the wrong process.
      [ "$pid" = "$$" ] && continue
      [ "$pid" = "1" ] && continue
      grep -qa "$1" "$proc/cmdline" 2>/dev/null || continue
      if [ -n "$2" ] && grep -qa "$2" "$proc/cmdline" 2>/dev/null; then
        continue
      fi
      echo "$pid"
    done
  ' sh "$1" "${2:-}" | tr -d "\r"
}

kill_in_container() {
  local what="$1" pattern="$2" exclude="${3:-}" pid
  pid="$(pid_in_container "${pattern}" "${exclude}" | head -1)"
  if [ -z "${pid}" ]; then
    echo "FAILED: no process inside the container matches ${pattern}, so this" \
      "test is aiming at something that is not there. If the way ${what} is" \
      "launched changed, this pattern has to change with it." >&2
    exit 1
  fi
  echo "    (${what} is pid ${pid})"
  # Tolerated: killing the server brings the container down, and the exec can
  # lose its own connection on the way out. The assertions below are the verdict.
  dc exec -T "$SERVICE" sh -c 'kill "$1"' sh "${pid}" || true
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

# The supervisor builds OpenD's command line, and that line carries the trade
# PIN hash when a deployment uses one. docker-compose.smoke.yml hands it a fake
# hash for exactly this assertion: nothing that builds a gateway command may put
# a credential where `docker logs` will keep it.
echo "==> credentials do not reach the logs"
if logs | grep -q 'smoketestnotarealhash'; then
  echo "FAILED: the gateway's login hash was written to the container log." \
    "Redact it in moomoo_mcp.supervisor before this ships." >&2
  logs | grep 'smoketestnotarealhash' | head -3 >&2
  exit 1
fi

echo "==> the MCP server reaches the gateway at 127.0.0.1:11111"
wait_for 120 "the MCP server to reach the gateway" gateway_has_connections
echo "    (connections accepted by this gateway: $(gateway_connections))"

# Loopback is the claim; this is what makes it one. The stand-in binds whatever
# -api_ip it was handed, so a supervisor that widened the listener would show a
# bridge address here instead.
if ! logs | grep -q '^CONNECT 127\.0\.0\.1'; then
  echo "FAILED: the gateway accepted a connection from something other than" \
    "container loopback, so it is listening wider than it should be:" >&2
  logs | grep '^CONNECT ' | tail -5 >&2
  exit 1
fi

# The exposure this whole arrangement exists to close. Port 8000 is the control:
# the same probe, on the same network, must reach the endpoint — otherwise a
# refused 11111 would prove nothing but a broken probe.
echo "==> the gateway is unreachable from another container"
target_ip="$(docker inspect \
  -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' "$(container_id)")"
if [ -z "${target_ip}" ]; then
  echo "FAILED: could not determine the container's address to probe." >&2
  exit 1
fi
network="$(docker inspect \
  -f '{{range $name, $_ := .NetworkSettings.Networks}}{{$name}}{{end}}' \
  "$(container_id)")"
docker run --rm --network "${network}" "${PROBE_IMAGE}" python3 -c '
import socket
import sys

host = sys.argv[1]


def reachable(port):
    probe = socket.socket()
    probe.settimeout(5)
    try:
        probe.connect((host, port))
    except OSError:
        return False
    finally:
        probe.close()
    return True


if not reachable(8000):
    print("FAILED: the probe cannot reach port 8000 either, so this says "
          "nothing about 11111.")
    sys.exit(1)
if reachable(11111):
    print("FAILED: the OpenD API answered another container. It has no "
          "authentication of its own and must listen on loopback only.")
    sys.exit(1)
print("    (8000 reachable, 11111 refused)")
' "${target_ip}"

# The policy's first half. `docker compose restart` cannot express this: the
# gateway *process* has to die while the container keeps running, which is
# exactly what happens when OpenD crashes in production.
echo "==> killing the gateway process"
instance_before="$(container_instance)"
starts_before="$(gateway_starts)"
kill_in_container "the gateway" opend-stub

echo "==> the supervisor restarts the gateway in place"
wait_for 60 "the gateway to come back" gateway_restarted_since "${starts_before}"

echo "==> the MCP server reconnects to the restarted gateway"
wait_for 180 "the MCP server to reconnect" gateway_has_connections
echo "    (connections accepted since the restart: $(gateway_connections))"

echo "==> the MCP endpoint still answers"
wait_for 30 "the MCP endpoint to survive the gateway restart" \
  endpoint_rejects_anonymous

echo "==> the client's session survived the gateway restart"
if ! session_still_lists_tools "${session}"; then
  echo "FAILED: the MCP session opened before the gateway died no longer works," \
    "so a gateway restart forces every client to reconnect." >&2
  show_last_tools_response
  exit 1
fi

echo "==> the container itself was left alone"
if [ "$(container_instance)" != "${instance_before}" ]; then
  echo "FAILED: a dead gateway took the whole container down with it. That" \
    "costs every client the ~30s OpenD needs to log back in, which the" \
    "asymmetric policy in moomoo_mcp/supervisor.py exists to avoid." >&2
  exit 1
fi

# The policy's other half, and the failure mode packaging introduces: a child
# that dies inside a container whose PID 1 keeps running is invisible to
# Docker's restart policy. The supervisor has to make it visible by exiting.
echo "==> killing the MCP server process"
instance_before="$(container_instance)"
kill_in_container "the MCP server" moomoo-api-mcp supervisor

echo "==> the container is replaced and comes back serving"
wait_for 120 "the container to be restarted" \
  container_restarted_since "${instance_before}"
wait_for 120 "the MCP endpoint to come back" endpoint_rejects_anonymous

echo "==> the client keeps calling without re-initializing"
if ! session_still_lists_tools "${session}"; then
  echo "FAILED: a call that worked before the server died no longer does, so" \
    "every client has to reconnect when the container is replaced." >&2
  show_last_tools_response
  exit 1
fi

echo "PASSED: the gateway restarts alone, the server takes the container with it."
