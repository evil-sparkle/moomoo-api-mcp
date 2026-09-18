# Change: Run OpenD and the MCP Server as Supervised Processes in One Container

## Why

OpenD's API has no authentication of its own. Anything that can reach
`opend:11111` can place trades once the gateway is unlocked. Today the gateway
answers on `0.0.0.0:11111` on the `trading-net` bridge, so the boundary
protecting it is "no container other than `moomoo-mcp` is attached to that
bridge" — a property of the compose file, not of the network stack. Nothing in
the stack enforces it, and a future sidecar, a `docker run --network`, or a
debug container added by a tired operator quietly crosses it.

Two facts in `docs/state-and-restarts.md` sharpen this. The gateway is reachable
across namespaces only because OpenD is told `-api_ip=0.0.0.0`, and that flag
has never been exercised against a real OpenD build — the smoke test replaces
the binary with a stand-in, and the unit test asserts the flag's text. So the
current deployment depends on an unverified flag to widen an unauthenticated
listener, in order to cross a namespace boundary that exists for reasons the
gateway itself does not care about.

Putting both programs in one container removes the listener from every network
it does not need to be on: OpenD binds `127.0.0.1:11111` inside the container,
and only a process in that container can reach it. Moomoo's own documentation
says protocol encryption is usually unnecessary when the SDK and OpenD share a
machine, which is the arrangement this produces. It also drops the `-api_ip`
dependency entirely, since loopback is OpenD's default.

**This change is not motivated by restart resilience.** An earlier arrangement
where `moomoo-mcp` borrowed OpenD's network namespace did break on restart, and
that is already fixed — the two containers hold separate namespaces today and
`tests/test_compose_topology.py` keeps them that way. The proposal below must
preserve that fix, not re-earn it, which is why its supervision policy restarts
a dead OpenD in place rather than cycling the whole container.

## What Changes

- **BREAKING (deployment)**: the stack becomes one service instead of two.
  Operators running `docker compose restart opend` or `docker compose logs
  moomoo-mcp` need new commands; the published endpoint and the `opend-data`
  volume are unchanged.
- `container-deployment`: the gateway listener moves from `0.0.0.0` on
  `trading-net` to `127.0.0.1` inside the container, and the requirement that
  isolates it is restated in terms of the process boundary rather than the
  bridge.
- `container-deployment`: a new requirement for paired process supervision — a
  supervisor owns both processes, reaps children, forwards stop signals, keeps
  the MCP endpoint serving across an OpenD restart, and exits the container when
  recovery is not possible.
- One image replaces `Dockerfile` and `Dockerfile.opend`, built on `ubuntu:22.04`
  with a uv-managed interpreter, running both processes as one unprivileged uid.
- `MOOMOO_OPEND_HOST` defaults to `127.0.0.1`; `OPEND_API_IP` is retired.
- `tests/test_compose_topology.py` and `scripts/smoke-test.sh` are rewritten
  against the single-service topology, keeping every assertion that still has
  meaning — above all that an OpenD restart leaves a live client unaffected.

## Impact

- Affected specs: `container-deployment`
- Affected code: `Dockerfile`, `Dockerfile.opend` (merged), `docker-compose.yml`,
  `docker-compose.prod.yml`, `docker-compose.smoke.yml`,
  `tests/test_compose_topology.py`, `scripts/smoke-test.sh`,
  `scripts/deploy.sh`, `.github/workflows/ci.yml` (two image builds become one),
  `docs/deploy-vps.md`, `docs/state-and-restarts.md`, `README.md`, `.env.example`
- Unaffected: `system-health` — `check_health` already reports `degraded` while
  the gateway is away, which is exactly the signal the supervisor must not act on.
- Not addressed here: reconciling account and order state after a restart, which
  this change makes no easier and no harder. Retrying an order whose outcome is
  unknown remains unsafe.
