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

Response validation is structural, not substring matching: a JSON-RPC
success with the matching request id and a correctly shaped initialize
result (`protocolVersion`, `capabilities`, `serverInfo`), over
`application/json` or an SSE-framed stream, both supported by the
Streamable HTTP transport the probe speaks to. A string request id means a
response with id `1` or `true` cannot compare equal to it.

| Result | Behavior |
|---|---|
| Valid initialize result | Succeed |
| Connection failure or HTTP 5xx | Retry within the deadline |
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
