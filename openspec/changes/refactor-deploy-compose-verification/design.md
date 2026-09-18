# Design: Resolve Deploy Verification Configuration Through Compose

## Responsibility split

The contract for the whole refactor: each component owns one thing, and
nothing owns two.

| Component | Owns | Must not own |
|---|---|---|
| `scripts/deploy.sh` | Commit selection, checkout, image selection, starting services, rollback | Token parsing, MCP response parsing |
| `scripts/compose-prod.sh` | The exact Docker context, Compose files, env-file arguments | Verification logic |
| `scripts/deploy_verify.py` | Reading Compose's resolved configuration, the probe, response validation, bounded retries | Interpreting dotenv syntax, changing Git state, restarting services |

The acceptance criterion for the split: **one** implementation of dotenv
interpretation exists in the deployment path — Compose's. The helper reads
`config --format json` output (the resolved model, not `config --environment`,
which reports interpolation inputs), takes
`services.moomoo-mcp.environment.MCP_AUTH_TOKEN`, and uses it unchanged with
exactly one exception: Compose escapes every literal `$` as `$$` in its
output, so the pairs are decoded back to the value the container received.

Resolution rules:

- **Nonempty string** — used exactly as resolved; no quote stripping,
  trimming or interpolation.
- **Explicit empty string** — no Authorization header is sent, preserving
  the unauthenticated configuration option.
- **Anything else** — Compose failed, no service, no token field, null,
  unexpected type, invalid JSON — a configuration error. There is no
  fallback to reading `.env`.
- **Header-unsafe values** (CR, LF, NUL, other controls, non-ASCII) — a
  configuration error. This is HTTP safety, not dotenv grammar: Compose may
  resolve a multiline value perfectly correctly, and it still cannot be sent
  as an Authorization header.

The resolved model stays in the helper's memory: it is never printed or
written to a file, and it can carry credentials beyond the token. Compose's
own error output is not relayed either — it can quote the env-file line it
choked on.

## Probe design

Curl stays the HTTP transport: one thing replaced at a time (configuration
parsing), not two. The token reaches curl on stdin (`--header @-`), never
argv and never a file — the 0600 header file and its cleanup trap are gone
with the parser. The helper passes `--disable` (never read a `.curlrc),
`--proto http,https`, `--noproxy *` (the token is for the endpoint, not a
proxy on the way to it), and no redirect following.

For this bounded curl-based verifier, an attempt can succeed only when
all of these hold:

```text
Successful curl transfer (exit 0)
    AND HTTP 200
    AND a complete JSON response or a complete SSE response event
    AND JSON-RPC 2.0 with the matching request id
    AND a structurally valid initialize result: protocolVersion (one of
        the known handshake versions), capabilities and serverInfo
        inside the result object, with the right types
    AND no JSON-RPC error in that response
```

A timeout, signal termination, or nonzero curl exit must never produce
verification success, regardless of captured response content. Python
exposes the child's exit status separately from its captured stdout, and
the helper retains and evaluates both: the exit status is checked before
any captured output is read, so a 200 status line printed by an aborted
transfer — or a complete, valid body delivered under an overstated
`Content-Length` (curl exit 18, partial transfer) — cannot verify. Adding
response parsing while ignoring curl's exit status would preserve exactly
that bug, which is why the test suite proves it with real curl delivering
valid content over a failed transfer.

Response validation is structural, not substring matching: a JSON-RPC
success with the matching request id and a correctly shaped initialize
result, over `application/json` or an SSE-framed stream, both supported by
the Streamable HTTP transport the probe speaks to. A string request id means
The fields are required *inside the result object*, where the MCP lifecycle
puts them — the same names appearing elsewhere in the response do not pass —
and `protocolVersion` must be a non-empty string. The probe confirms that
configured authentication can access the endpoint and receive a structurally
expected initialize response matching the request; it does not certify
protocol version compatibility or trading availability.

For SSE, a complete response event followed by a *cleanly completed*
transfer is required: a complete event inside a transfer curl exits nonzero
on does not verify. This is deliberately conservative for the finite
deployment probe; MCP's Streamable HTTP specification has the server close
the POST response stream after sending the JSON-RPC response.

| Result | Behavior |
|---|---|
| curl exit 0, HTTP 200, structurally valid initialize result | Succeed |
| Not answering yet: connection failure (curl 6/7/52/55/56), timeout (28), or HTTP 5xx | Retry within the deadline |
| Partial transfer (curl 18) or any other nonzero exit | Fail immediately; content never read |
| HTTP 401/403 | Fail immediately, authentication diagnosis |
| HTTP 200 with an invalid MCP result | Fail immediately |
| Any other HTTP status | Fail immediately |

The deadline is elapsed time (`time.monotonic`), not a counter of sleep
intervals, and each attempt's `--max-time` is cut to the time remaining, so
a slow endpoint cannot stretch the deadline by a full attempt.
`--timeout 0` is one attempt, no retries — the existing testing convention.
SIGINT/SIGTERM interrupt the probe; the helper re-raises the signal so the
caller sees an interruption rather than an ordinary failure.

## Ordering and rollback

```text
Check host dependencies (incl. python3 >= 3.10)
Select target commit and image
Save previous deployment state
Check out target commit
Write target .deploy.env
Run helper check-config
Pull image
Start services
Run helper verify
    success → report verified
    failure → existing rollback
```

Configuration validation runs after the target checkout and `.deploy.env`
rewrite, so it validates the deployment about to run. Deployment inputs are
treated as fixed during a deployment: the same env files and compose files
are used for validation, startup and verification. `compose config` is a
resolved configuration, not an inspection of a running container — it is not
an atomic guarantee against concurrent manual edits.

The helper is invoked from the checked-out target commit
(`$REPO_ROOT/scripts/deploy_verify.py`), never from the directory the
reexecuted Bash script runs in — after a self-reexec that is a temporary
file, and next to it there is no helper to find. Tests cover the transition
from main's pre-helper `deploy.sh` and from a checkout holding a stale
helper.

Failure to collect diagnostic logs never prevents rollback (`|| echo` under
`set -e`, in both the success and failure paths).
