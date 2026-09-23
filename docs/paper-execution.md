# Persistent paper execution

Stage 2 persists SIMULATE operations in a dedicated SQLite journal. Both SIMULATE
and REAL deployments use the **same paper journal**: REAL mode unlocks eligibility
for real trading without disabling paper trading. Real orders are not journaled
by Stage 2; a separate REAL journal is deferred. Do not share a database between
paper and real execution.

## Deployment

Add `docker-compose.paper.yml` to the existing Compose file list when enabling
paper execution. Preserve the Compose project name across restarts, upgrades and
mode changes: it determines the named volume's identity. The overlay adds
`execution-data` at `/var/lib/moomoo-mcp/data`; it retains the existing OpenD
`opend-data` mount and uid 10001 ownership at `/home/opend/.com.moomoo.OpenD`.
The image prepares the journal directory for uid 10001, and a new named volume
inherits that ownership. Use a local POSIX filesystem honoring fsync and advisory
locks. Network filesystems without these guarantees are unsupported.

Supply the following values through your deployment's existing settings mechanism:

| Setting | Meaning |
| --- | --- |
| `MOOMOO_TRADING_MODE` | `SIMULATE` or `REAL`; neither changes paper storage identity |
| `MOOMOO_SIMULATED_ACC_IDS` | Explicit positive paper account IDs; the allowlist cannot contain 0 |
| `MOOMOO_JOURNAL_PATH` | Overlay fixes this to `/var/lib/moomoo-mcp/data/execution.sqlite3` |
| `MOOMOO_CREATE_JOURNAL` | `1` only for explicitly authorized first initialization; normally `0` |
| `MOOMOO_JOURNAL_LOCK_WAIT_MS` | Bounded SQLite wait, 1–60000 milliseconds; default 5000 |
| `MCP_AUTH_TOKEN` | Normal authenticated tool access |
| `MCP_OPERATOR_TOKEN` | Separate operator credential, different from the normal token |

Existing REAL trading credentials and account allowlists still apply to REAL
orders. Paper journaling adds no authority to trade REAL accounts. Use the US or
NONE market filter for the supported paper execution scope.

For example, pass the same project, overlays and explicit operator settings file
to every deployment command (substitute your existing settings file path):

```sh
docker compose --project-name moomoo --env-file /path/to/operator-settings \
  -f docker-compose.yml -f docker-compose.paper.yml up -d
```

If using the production image overlay, include `docker-compose.prod.yml` before
the paper overlay and supply its required image settings as usual. Set
`MOOMOO_CREATE_JOURNAL=1` only when intentionally provisioning the first empty
journal. Once created, return it to `0` before normal operation so a missing mount
fails closed. Never use initialization to erase or bypass an unresolved outcome.
Do not run `down --volumes` against this deployment.

READ_ONLY deployments omit the paper overlay and need no journal volume. Switching
an existing executor temporarily to READ_ONLY must retain its original volume for
later use; READ_ONLY does not open the database even if journal settings exist.

Exactly one executor may hold a journal at a time. A second process or container
fails closed on `execution.lock`. Scaling the executor horizontally against a
shared journal is unsupported. Each process start creates a new admission epoch;
startup review must complete before new mutations can proceed. Retain each
operation ID **and its admission epoch** across client/network retries. Never
refresh either merely because a response was lost.

## Backup and restore

Prefer SQLite's online backup API. It produces a consistent, self-contained
snapshot while the executor remains active; copying the live main database file
alone does not. A simple backup executed as the image's normal uid 10001 is:

```sh
docker compose --project-name moomoo --env-file /path/to/operator-settings \
  -f docker-compose.yml -f docker-compose.paper.yml exec -T moomoo-mcp python - <<'PY'
import os
import sqlite3
from pathlib import Path

source = Path('/var/lib/moomoo-mcp/data/execution.sqlite3')
backup = source.with_name('execution-backup.sqlite3')
# Exclusive creation refuses an existing backup and restricts it to this uid.
fd = os.open(backup, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
os.close(fd)
with sqlite3.connect(source.as_uri() + '?mode=ro', uri=True) as live:
    with sqlite3.connect(backup) as snapshot:
        live.backup(snapshot)
        assert snapshot.execute('PRAGMA integrity_check').fetchone() == ('ok',)
PY
```

Retain backups outside the running volume according to your own retention policy.
Treat execution records as sensitive operational data. Confirm the copied backup
passes `PRAGMA integrity_check` before relying on it.

For an offline main-file copy, prevent **all** executor starts for the entire copy
and restore interval, acquire the process lock, and require a cleanly closed or
recovered database. Conservatively refuse a database-only copy whenever a sibling
`execution.sqlite3-journal` exists. A crash releases the process lock but can leave
a hot rollback journal; an unheld lock alone proves nothing about copy safety.
Recover the original database with SQLite while no executor can start, or preserve
and restore the database **and its matching rollback journal together** as a set.
Never discard a rollback journal to make a copy appear clean. The isolated C04 test
creates a genuine crash with a hot rollback journal and proves this conservative
copy procedure rejects it despite the now-unheld lock.

Before restoring, stop the executor, disable automatic restarts, prevent concurrent
starts, and preserve the current database and any sidecars for investigation.
Restore into the original journal volume with uid 10001 ownership and retain the
OpenD authorization volume unchanged. Restart with creation disabled. Restore
creates a new process epoch and requires recovery review; it cannot reconstruct
rows missing from the snapshot. Unknown tokens carrying retired epochs are refused,
and existing uncertain/nonterminal rows gate new admissions until accounted for.
A missing restored token is not evidence that its broker request never happened.

## Recovery and operator review

Use `check_health` to obtain the current admission/recovery epoch and journal
state; use `get_execution(operation_id)` for the recorded receipt, broker status,
reconciliation observations and any durable accounted facts. A new paper placement
must include its caller-generated operation ID, the current admission epoch, an
explicit SIMULATE environment, and a decimal-string limit price. Preserve its token
and original request through every retry; a retry never changes the request or
refreshes the epoch. Only the first admission can dispatch. Admission and the
DISPATCHING marker commit in separate short transactions before the one SDK call;
no database transaction spans that call. ACKNOWLEDGED means the gateway accepted
the request, not that the order filled. UNKNOWN_OUTCOME means possibly sent and
blocks new paper mutations, including cancellations.

Inspect journal health and operation status first. Use reconciliation only where
provider evidence reliably identifies the submitted order and its outcome.
Account explicitly for uncertain outcomes through the authenticated operator
recovery interface, supplying the current recovery epoch, observed state, reason,
evidence reference and verified accounted facts. Ordinary tool credentials do not
have operator acknowledgement authority. Operator recovery is available only over
streamable HTTP. SSE and stdio refuse recovery acknowledgement so a long-lived
session cannot reuse another request's operator identity. Never invent broker
evidence to clear a gate. Acknowledgement is audited and does not replay a possibly sent request.

**Reinitializing, replacing or repointing the journal for the same broker account
is not an approved recovery method.** Preserve the original journal and unresolved
records. Continued experimentation requires a separately authorized, independently
verified isolated paper environment. It does not reconcile the previous environment.
An unresolved operation can block execution indefinitely; elapsed time, missing
history and operator risk acceptance are not recovery evidence. Only
`TERMINAL_ACCOUNTED` is supported: pass `operator_id="operator"`, the current
recovery epoch and observed local state, a reason, `broker-order:<id>`, and facts
for final status, filled quantity, average fill price, zero remaining executable
quantity and resulting position. These are checked against fresh order/history
and position data, then committed with the audit record before the gate can clear.
Never provide the operator credential to a trading agent.

A provider's “reset paper account” button is not sufficient evidence of isolation:
verify what it does to outstanding orders, pending requests and account identity.

## Verification boundary

`tests/test_paper_compose.py` checks Compose's rendered topology with isolated
settings. `scripts/test-paper-container.sh IMAGE [RECREATED_IMAGE]` runs C01–C04
using unique disposable volumes and real containers from built application images,
all with `--network none`. It exercises nonroot persistence through recreation,
competing executor exclusion, online backup, older restore, retired-token refusal,
recovery review, hot-journal copy rejection, and READ_ONLY startup plus a quote tool
read against a mocked SDK without a journal volume. It never starts OpenD or calls a
provider. Supply a second built image to verify replacement across image versions.

Live paper-provider acceptance, response-loss recovery against OpenD, retention
measurement, and ZeroClaw/Telegram retry propagation belong to the separate
`validate-paper-execution-provider` follow-up. Automated development completion
and archive do not claim those live results.

## Dependent modifications

A modification acknowledgement does not prove its new order fields are visible to
queries. Before another modification to that same account/order, a fresh order
observation must match the earlier acknowledged request's total quantity and price.
Otherwise the dependent operation is durably REFUSED before dispatch, with no SDK
mutation. Retrying that refused operation returns its refusal; it does not become a
pending order or automatically dispatch when observations catch up. A later,
separately authorized intent uses a new operation ID. Never replace an uncertain
operation's token. Matching retries of the original acknowledged modification also
never redispatch.

This visibility requirement persists across restarts. It is specific to dependent
modifications; reads, unrelated orders and individual cancellation remain available
subject to their existing safety and recovery gates. Divergent external edits or
provider price normalization can keep dependent modifications refused; the server
does not guess a replacement merge. Actual provider visibility timing remains a
live-validation task.

Journal schema 2 adds durable modification-observation tracking. Existing schema 1
journals upgrade atomically without removing operations or changing their original
admission epochs. Their acknowledged modifications conservatively start unobserved;
conflicting old unobserved changes can therefore keep dependent modifications
refused. Older schema-1 executors refuse the upgraded journal rather than ignoring
this protection. Keep a consistent backup before upgrading.
