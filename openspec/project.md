# Project Context

This is orientation, not a second specification. The requirements live in
`openspec/specs/`, and the deployment runbooks live in `docs/`. When this file
disagrees with the code, or with a spec, the code and the spec win: fix this
file. It deliberately contains no copy-pastable implementation patterns. An
earlier version did, and its lifespan example taught the per-session connection
model the code had already abandoned.

## Purpose

An MCP server that gives AI agents (Claude Code, Gemini CLI and others) market
data, account data and order management on Moomoo, through the `moomoo-api`
SDK and a Moomoo OpenD gateway.

This repository is a fork of `Litash/moomoo-api-mcp`. Upstream publishes the
PyPI package. This fork maintains the container deployment (CI image build,
`docs/deploy-vps.md`) and does not publish to PyPI.

## Runtime and toolchain

These are the real constraints. Where the sources below disagree, fix the
source.

| Constraint | Value | Source |
| --- | --- | --- |
| Supported Python | `>=3.10` | `pyproject.toml` `requires-python` |
| Syntax and type-check target | 3.10 | `pyproject.toml` ruff `target-version = "py310"`, basedpyright `pythonVersion = "3.10"` |
| Interpreter in the image | 3.12.13, uv-managed | `Dockerfile` `UV_PYTHON` |
| Interpreter in CI tests | 3.12 | `.github/workflows/ci.yml` `test` job |
| MCP SDK | `mcp>=1.10.0,<2` (FastMCP; 2.x renamed it) | `pyproject.toml` |
| Moomoo SDK | `moomoo-api>=10.10.7008` | `pyproject.toml` |
| Package manager | uv, locked by `uv.lock` | |

Write code that runs on 3.10. Features from later versions (PEP 649 deferred
annotations, t-strings, `except*`, PEP 695 `type` aliases) are out until
`requires-python` moves, and moving it is a deliberate change of its own.

## Module map

`src/moomoo_mcp/`:

- `server.py`: the FastMCP instance, the process-owned gateway connections
  (`get_services`, `close_services`), startup auto-unlock, bearer-token
  middleware, and the transport entry point `main`.
- `supervisor.py`: the container's PID 1. It starts OpenD and the MCP server
  and applies the recovery policy. It imports no application code.
- `services/`: SDK wrappers. `base_service.py` (quote connection, health
  aggregation), `trade_service.py` (trade connection, READ_ONLY lock on connect
  and reconnect, just-in-time unlock), `trading_policy.py` (the
  `MOOMOO_TRADING_MODE` gate), `market_data_service.py`, `health.py` (bounded
  probes), `sdk_response.py` (narrowing `(ret, data)`), `validation.py`,
  `clock.py`.
- `tools/`: MCP tools by area (`account.py`, `market_data.py`, `trading.py`,
  `system.py`), plus `offload.py` (running blocking SDK calls off the event
  loop), `serialization.py` (identifiers as strings at the response boundary)
  and `kline_cursor.py` (pagination cursors).

## Architectural contracts

The code follows these rules. Each points to where it is specified or explained.

- **Gateway connections belong to the process.** `get_services()` builds the
  quote and trade connections once, lazily, on the first request, and every
  session shares them. They are released at process exit. `app_lifespan` only
  yields them. Never construct or close a connection inside the lifespan: under
  stateless HTTP it runs per request. Tools read services through
  `ctx.request_context.lifespan_context`. The rationale is in the `app_lifespan`
  docstring.
- **Streamable HTTP is stateless.** No session id is issued or honoured, and
  authentication is checked per request (`stateless_http=True` in `server.py`).
- **The OpenD gateway may be absent.** A failed connection is logged and the
  server keeps serving, so `check_health` stays callable.
- **Blocking SDK calls leave the event loop.** Tools start blocking work with
  `tools/offload.py` `run_blocking` and wait on existing futures with
  `await_futures`.
- **The trading mode is enforced before the gateway.** `READ_ONLY`, `SIMULATE`
  or `REAL` (`MOOMOO_TRADING_MODE`) decides, in the service layer, which writes
  and unlocks are refused. A configured password never changes the mode.
- **Trading commands are never replayed.** A lost order response is not treated
  as a result, and recovery never resubmits the command.

The process-ownership, stateless-transport and no-replay contracts are being
specified by the active change `update-container-restart-resilience`. Until it
is archived, `docs/state-and-restarts.md` is their fullest description.

## Deployment

One container, `moomoo-mcp`, runs two processes under
`moomoo-api-mcp-supervisor` as PID 1, with no `init: true`:

- OpenD listens on `127.0.0.1:11111`, container loopback only, and is published
  nowhere. The MCP server dials it there.
- The MCP endpoint is published on host loopback, `127.0.0.1:8000`.
- The `opend-data` volume (`/home/opend/.com.moomoo.OpenD`, uid 10001) holds the
  device authorization and survives container replacement.

Recovery policy (`supervisor.py`):

| Event | Response |
| --- | --- |
| OpenD exits | Restart OpenD in place with backoff; MCP keeps serving |
| OpenD exits again past `OPEND_MAX_RESTARTS` within `OPEND_RESTART_WINDOW_SECONDS` | Stop MCP, exit non-zero; Docker replaces the container |
| MCP exits | Stop OpenD, exit non-zero; Docker replaces the container |
| OpenD running, broker unavailable | Nothing. Health reports it; no restart |
| No usable OpenD login configured | Log why, run MCP without a gateway |
| SIGTERM / SIGINT | Forward to both, bounded wait, then SIGKILL |

The policy is specified by `container-deployment` › `Paired Process
Supervision`, currently in the active change
`refactor-single-container-deployment`. That change is implemented on `main` and
waits on verification against the real OpenD binary before it is archived.

## Where things are specified

| Document | Owns |
| --- | --- |
| `openspec/specs/container-deployment` | Packaging, loopback binding, supervision policy, volume persistence, non-root execution, download integrity |
| `openspec/specs/transport-sessions` (pending, see above) | Stateless Streamable HTTP, process-owned connections, transport-level restart semantics |
| `openspec/specs/trade-unlock`, `trading-policy` | Mode-dependent authorization, startup and just-in-time unlock, reconnect locking (pending, see above) |
| Other `openspec/specs/*` | One capability per tool family |
| `docs/state-and-restarts.md` | Operator view: what state lives where, what each restart costs |
| `docs/deploy-vps.md`, `docs/rootless-docker.md` | Operator commands, upgrade and recovery procedures |

Descriptions of the old two-container stack belong only in history, migration
or rollback notes.

## Development

```bash
uv sync --all-extras --dev
uv run pytest
uv run ruff check . && uv run ruff format --check .
uv run basedpyright
uv run pre-commit install && uv run pre-commit install --hook-type commit-msg
./scripts/smoke-test.sh      # needs a Docker daemon; CI runs it
npx -y @fission-ai/openspec@1.13.1 validate --all --strict --no-interactive
```

- CI runs the same lint, type-check and test steps as the pre-commit hooks,
  followed by the container smoke test. The smoke test swaps OpenD for
  `tests/fixtures/opend_stub.py`, so it proves topology and restart policy, not
  a broker login.
- The OpenSpec CLI is the npm package `@fission-ai/openspec`, not the unscoped
  `openspec`. Use the version CI pins.
- Tests mock the SDK. None of them needs a running OpenD.
- Commit messages follow Conventional Commits, enforced by the `commit-msg`
  hook. Branch off `main` and merge by pull request.
- Ruff, line length 88. Tool docstrings are what an agent reads as the tool
  description, so they state preconditions and failure modes, not just
  arguments.

## Domain notes

- **OpenD** is Moomoo's gateway process. It logs in to Moomoo, and the SDK talks
  to it over TCP (default port 11111). Its API has no authentication of its own,
  which is why it listens on loopback only.
- **Codes** take the form `MARKET.SYMBOL`: `HK.00700`, `US.AAPL`, `SH.600519`,
  `SZ.000001`.
- **SDK calls** return `(ret, data)`. `data` is a DataFrame on success and an
  error string otherwise. Narrow it through `services/sdk_response.py`.
- **Trading environments**: `TrdEnv.SIMULATE` (paper) and `TrdEnv.REAL`. Which
  one a request may use is decided by the trading policy, not by the caller.
- **Limits**: subscription quotas depend on the account tier, and OpenD rate
  limits requests.

## Constraints

- Never commit credentials, account numbers or trade passwords. Configuration
  comes from the environment. `.env.example` is the template.
- An order whose outcome is unknown must be reconciled by querying orders or
  deals, never by retrying.
