# Moomoo API MCP Server

An MCP (Model Context Protocol) server for the Moomoo trading platform. This server allows AI agents (like Claude Desktop or Gemini) to access market data, account information, and execute trades via the moomoo-api Python SDK.

## About this fork

This fork maintains a container deployment with OpenD and MCP in one supervised
container, plus an optional ChatGPT tunnel. **Rootless Docker is the recommended
way to run it.** CI publishes images to ECR; production hosts pull those images
through `scripts/deploy.sh`.

Forked from [Litash/moomoo-api-mcp](https://github.com/Litash/moomoo-api-mcp)
and maintained for personal use. This fork is distributed through container
images and source checkouts; it does not publish to PyPI. Its trading safeguards,
configuration and tool contracts have diverged, so compatibility with upstream
is not guaranteed. The instructions below run this repository's code.

## Features

- **Market Data**: Real-time quotes, historical K-lines, market snapshots, and order books.
- **Account Management**: Comprehensive account summaries, assets, positions, and cash flow analysis.
- **Trading**: Full order management including placing, modifying, and canceling
  orders, gated by an explicitly configured trading mode.
- **System Health**: Active, bounded health probes of the quote and trade connections to OpenD.
- **Extensible Architecture**: Built on FastMCP for easy extension of trading capabilities.

## Run the server

### Recommended: rootless Docker

The container includes both OpenD and MCP, runs their processes as UID 10001,
and persists OpenD device authorization in a named volume. The rootless daemon
runs under the deploy user's account. OpenD stays on container loopback;
authenticated MCP is published on host `127.0.0.1:8000`. The default trading mode
is READ_ONLY.

**For an Ubuntu VPS, follow [the deployment runbook](docs/deploy-vps.md).** It
covers rootless Docker, ECR access, configuration, one-time interactive OpenD
login, systemd and recovery. After that setup, routine deployments use:

```bash
cd "$HOME/moomoo"
./scripts/deploy.sh                 # current origin/main; wait for its CI images
./scripts/deploy.sh <commit>        # a specific published commit or rollback
```

`deploy.sh` is the sole manual production deployment entrypoint. It selects
images, applies the saved Compose configuration, verifies authenticated MCP and
reports gateway readiness. Broker login and trading readiness are separate from
MCP availability. Do not run a separate OpenD application on the host for this
container deployment.

- **Optional ChatGPT access:** follow [the tunnel runbook](docs/deploy-vps.md#optional-chatgpt-access)
  and enable it with `./scripts/deploy.sh --chatgpt`. It permits READ_ONLY or
  SIMULATE, including real-account reads and supported paper orders.
- **Local macOS development:** [the rootless Lima setup](README.md#local-rootless-docker-on-macos)
  provides a Linux Docker daemon for local image builds and container checks.
- **Paper execution:** [the paper runbook](docs/deploy-vps.md#paper-execution-and-recovery) covers its
  allowlist, journal, persistence and recovery. The production deployment script
  automatically selects the paper overlay in SIMULATE and for selected tunnels.

### Alternative: local Docker with a rootful daemon

The same image and base Compose file can be used with an existing rootful Docker
daemon for local development. Application processes still run as UID 10001;
this does not make the Docker daemon rootless. Select the intended Docker context
and build with:

```bash
docker compose build
```

Use `.env.example` for configuration, including READ_ONLY and an MCP bearer token,
before starting the base stack with `docker compose up -d`. A new OpenD volume
needs interactive login; use the login sequence in the
[VPS runbook](docs/deploy-vps.md#7-prepare-image-then-perform-interactive-opend-login)
with `docker compose` in place of the production wrapper for this local setup.
Preserve the volume and never publish port 11111.

Production `deploy.sh` and its Compose wrapper explicitly use the `rootless`
context; they do not switch to a rootful daemon based on the active context.

### Alternative: run this fork from source

Use this when developing the MCP server or connecting it to an OpenD gateway you
already run. Python 3.12 is the local development interpreter.

```bash
git clone https://github.com/evil-sparkle/moomoo-api-mcp.git
cd moomoo-api-mcp
uv sync
uv run moomoo-api-mcp
```

Set up the separate gateway and environment as described under
[configuration](#configuration), including `MCP_AUTH_TOKEN` in the server
process environment. Source launches use the same stateless Streamable HTTP
endpoint as containers; connect clients to the running server at `/mcp`.
See [contributor guidance](#contributing) for checks, hooks and OpenSpec workflows.

## Documentation

- [Production runbook](docs/deploy-vps.md): deployment, login, diagnostics,
  restarts, ChatGPT access and persistent-state recovery.
- [Contributing](#contributing): development checks and OpenSpec regeneration.
- [OpenSpec requirements](openspec/specs/): current architecture and behavioral
  contracts. Active changes track proposed work; archived changes record history.
- Implementation details and exact versions live in source, tests,
  `pyproject.toml`, `uv.lock`, `Dockerfile` and CI.

## Tools

### System

- `check_health`: Actively probe the Moomoo OpenD gateway with read-only quote and trade calls.

  Returns `status` (`connected` when both probes succeed, `degraded` when exactly one does, `disconnected` when neither does), `host`, a UTC `checked_at` observation time, per-service `quote` and `trade` results, and `gateway_version` when OpenD reports one. The whole check is bounded to five seconds, and repeated calls during a stuck probe reuse the in-flight worker rather than starting another.

  ```json
  {
    "status": "degraded",
    "host": "127.0.0.1:11111",
    "checked_at": "2026-09-10T12:00:00Z",
    "quote": { "status": "ok", "logged_in": true },
    "trade": { "status": "error", "reason": "gateway_error", "error": "trade svr not ready" },
    "trade_market": "NONE",
    "gateway_version": "9.2.5208"
  }
  ```

  A `connected` result means OpenD answered. It does **not** mean trading is unlocked, that an order would be accepted, or that a given market is authorized for the account. The server also starts even when OpenD is unreachable, so `check_health` stays callable while you diagnose the gateway.

### Account

- `get_accounts`: List accounts across markets and environments, or filter one
  response with `market="US"` and `trd_env="SIMULATE"`.
- `get_account_summary`: Get a complete summary of assets and positions for an account.
- `get_assets`: Retrieve account assets (cash, market value, buying power).
- `get_positions`: Get current stock positions with P/L data.
- `get_max_tradable`: Calculate maximum tradable quantity for a specific stock.
- `get_margin_ratio`: Check margin ratios for specific stocks.
- `get_cash_flow`: Retrieve historical cash flow records.
- `unlock_trade`: Unlock trading access for REAL accounts.

#### Accounting interpretation

`get_positions` and the positions in `get_account_summary` return broker-reported
position data, not an independently reconciled accounting ledger. Preserve the
field names and accounting basis when presenting costs and P/L.

For securities accounts, `cost_price` is diluted cost and `pl_ratio` is the
diluted-cost P/L percentage. Do not label these as average purchase cost
or unrealized return.

Additional position fields, when reported:

| Field | Broker-reported meaning |
| --- | --- |
| `average_cost` | Average cost price. |
| `diluted_cost` | Diluted cost price. |
| `pl_ratio_avg_cost` | P/L percentage using average cost. |
| `unrealized_pl` | Unrealized P/L amount. |
| `realized_pl` | Realized P/L amount. |

`average_cost`, `pl_ratio_avg_cost`, `unrealized_pl` and `realized_pl` are not
applicable to SIMULATE securities accounts. In universal securities accounts,
`unrealized_pl` and `realized_pl` use the average-cost basis. For futures accounts,
`cost_price` is average cost; `diluted_cost`, `pl_ratio` and `pl_ratio_avg_cost` are
not applicable. Preserve unavailable fields as reported; do not reconstruct them.
These definitions do not prove a suspected upstream accounting mechanism.
See the official [position field definitions](https://openapi.moomoo.com/moomoo-api-doc/en/trade/get-position-list.html)
and [account-type applicability](https://openapi.moomoo.com/moomoo-api-doc/trade/get-position-list.html).

Do not treat a current position row as lifetime P/L for the underlying
and all its derivatives. Do not infer the inclusion of option premiums,
fees, dividends or closed positions without establishing the scope.

A reported app/API discrepancy must remain explicitly unresolved until
verified. Do not replace broker fields with reconstructed values or
present disputed P/L as verified portfolio performance.

Different average and diluted costs, or an unusually large percentage,
do not alone prove an error. Internal arithmetic consistency and provider
validity flags are not independent financial reconciliation.

### Market Data

- `get_stock_quote`: Get real-time stock quotes.
- `get_historical_klines`: Retrieve historical candlestick data (Day, Week, Min, etc.). Returns a **single page** — the provider's continuation token is discarded, so for a wide date range the list can be a prefix of the range with no indication that more exists. Kept unchanged for existing callers.
- `get_historical_klines_page`: The same query with explicit continuation, returning `{data, next_cursor, has_more}`. One call fetches one page; loop until `next_cursor` is null. The cursor is bound to the query that produced it, so replaying it with a different symbol, interval, or adjustment is rejected rather than silently mixing series. An empty `data` list with `has_more: true` is possible and does not mean the range is finished.
- `get_market_snapshot`: Get efficient market snapshots for multiple stocks.
- `get_order_book`: View real-time bid/ask order book depth.
- `get_subscriptions`: List the market-data subscriptions held by this server's quote connection, with usage figures split into `connection` (this server) and `provider` (every client on the same OpenD gateway). Only fields the provider actually reports are present — an absent quota means "not reported", never "unlimited".
- `unsubscribe_market_data`: Release specific codes and subscription types held by this connection. Never global, and never another client's subscriptions. A provider that enforces a minimum subscription duration can refuse an early release; that is returned as an error and is not retried. Reading the symbol again re-subscribes it.
- `get_market_state`: Get each instrument's current session state (`MORNING`, `REST`, `CLOSED`, `PRE_MARKET_BEGIN`, …) as the provider reports it, with a UTC observation time. It is an observation, not a schedule.
- `get_trading_days`: Get a market's trading calendar for a date range. Dates are **market-local** calendar dates, holidays are simply absent from the list, and half days are distinguished by `trade_date_type`. Session opening and closing times are not part of the response and are never inferred. A trading date does not imply that a given instrument, or your account, may trade that day.
- `get_option_expiration_date`: List an underlying's available option expiry dates.
- `get_user_security_group`: List the user's watchlist groups from the Moomoo app.
- `get_user_security`: List the securities in one watchlist group.
- `get_option_chain`: Get option contracts for an underlying within a range of expiry dates, filtered to calls, puts, or all. Returns the exact provider contract symbols to use in quotes, previews, and orders — never build an option symbol by hand. The provider accepts a range of at most 30 days; a wider range is rejected rather than truncated.

  Option-chain requests use the shared [broker request dispatcher](#broker-request-limits), with a budget of 10 calls per 30 seconds.

### Trading

- `place_order`: Place a new order (Market, Limit, Stop, etc.).
- `preview_combo_order`: Preview what a multi-leg package would do to an account — net liquidation value, initial and maintenance margin, option buying power, withdrawable amount, and buying-power decrease — using the broker's own calculation. Read-only: it places nothing, unlocks nothing, and reserves nothing, so it works in every trading mode. A field the broker did not report comes back as `null` rather than `0`. The values are point-in-time estimates, not a quote or an acceptance.
- `place_combo_order`: Place a multi-leg option strategy (vertical spread, straddle, etc.) as a single atomic order. Use this rather than several `place_order` calls for any multi-leg strategy — the package fills as one unit, so a strategy can't be left half-executed. To **close** a strategy, first call `get_positions(show_option_strategy_view=True)` and pass each leg's `position_id`, which the API requires on closing orders.
- `modify_order`: Modify price or quantity of an open order.
- `cancel_order`: Cancel an open order.
- `get_orders`: Get list of orders for the current day.
- `get_deals`: Get list of executed trades (deals) for the current day.
- `get_history_orders`: Search historical orders.
- `get_history_deals`: Search historical deals.

#### History coverage and executions

For both history tools, omit dates with empty strings. The installed
`moomoo-api 10.10.7008` uses these rules, consistent with the official
[historical orders](https://openapi.moomoo.com/moomoo-api-doc/en/trade/get-history-order-list.html)
and [historical deals](https://openapi.moomoo.com/moomoo-api-doc/en/trade/get-history-order-fill-list.html)
documentation:

| `start` | `end` | Requested period |
| --- | --- | --- |
| Omitted | Omitted | 90 days before today through today, using the SDK host's current date. |
| Omitted | Supplied | 90 days before `end` through `end`. |
| Supplied | Omitted | `start` through 90 days after `start`, not necessarily today. |
| Supplied | Supplied | The explicit range, without a 90-day cap imposed by the SDK. |

Date-only bounds expand to `00:00:00` for `start` and `23:59:59` for `end`;
`YYYY-MM-DD HH:MM:SS` timestamps retain their supplied times. Broker availability
and filters still apply to explicit ranges. To investigate earlier activity,
request explicit ranges covering the intended period and all relevant instruments.
A successful response, including an empty list, does not establish lifetime
completeness: dates, account, code and order-status filters limit scope, and broker
history availability has not been independently established. A code filter does
not automatically include that underlying's derivatives. The provider documents
historical deals for REAL accounts; SIMULATE availability must not be assumed.

Order `qty` and `price` describe the request. `dealt_qty` and `dealt_avg_price`
report the executed quantity and average fill price; deal `qty` and `price`
describe individual actual fills. A `CANCELLED_PART` order retains its partial
fills after the unfilled remainder is cancelled. Do not discard those executions
or treat the full requested quantity as filled. These are broker-reported fields,
not independently verified accounting results.

## Configuration

### 1. Prerequisites

#### Separate OpenD gateway (source installations)

The recommended container deployment already includes OpenD; configure it through
[the VPS runbook](docs/deploy-vps.md). The steps below apply when running the MCP
server outside that container and connecting it to a separate gateway.

1. **Download OpenD**:
   - Visit the [Moomoo Open API Download Page](https://www.moomoo.com/download/opend).
   - Download the version appropriate for your OS (Windows/Mac/Linux).
   - **Version**: this server requires `moomoo-api>=10.10.7008` (for combo orders). The
     SDK and the gateway share a version line, so run an OpenD of at least that version
     to avoid protocol mismatches.

2. **Install & Run**:
   - Install the application.
   - Launch **Moomoo OpenD**.
   - Log in with your Moomoo account credentials.

3. **Configure**:
   - Ensure the listening port is set to `11111` (this is the default).
   - **Note**: The MCP server connects to `127.0.0.1:11111` by default; set
     `MOOMOO_OPEND_HOST` / `MOOMOO_OPEND_PORT` to point elsewhere. The Docker
     Compose stack runs its own gateway and sets these to `127.0.0.1:11111`
     inside the container for you.

### 2. Environment Variables

For this fork, `.env.example` is the configuration template. READ_ONLY permits
account and market reads but refuses order mutations and trading unlocks. A trade
password alone never enables REAL writes. Container operators should use the
[VPS configuration procedure](docs/deploy-vps.md#6-write-env).

| Variable                    | Description                                                           | Example       |
| --------------------------- | --------------------------------------------------------------------- | ------------- |
| `MOOMOO_TRADING_MODE`       | Which writes this server may issue. Default `READ_ONLY`.              | `SIMULATE`    |
| `MOOMOO_TRADING_MARKET`     | Trade account discovery filter. Default `NONE`; use `HK` for the former HK-only scope. | `NONE` |
| `MOOMOO_TRADE_PASSWORD`     | Your trading password (plain text)                                    | `123456`      |
| `MOOMOO_TRADE_PASSWORD_MD5` | MD5 hash of 6-digit trade PIN (alternative to plain text)             | `e10adc...`   |
| `MOOMOO_SECURITY_FIRM`      | Your broker region (e.g., FUTUSG, FUTUINC)                            | `FUTUSG`      |
| `MCP_TRANSPORT`             | Optional: Only `streamable-http` is accepted (stateless, JSON responses) | `streamable-http` |
| `MOOMOO_REAL_ACC_IDS`       | **Required in `REAL` mode.** Comma-separated accounts REAL writes may target | `12345678` |
| `MCP_AUTH_TOKEN`            | **Required** for the HTTP endpoint           | `secret-token`|
| `MCP_ALLOW_UNAUTHENTICATED_HTTP` | Optional: `1` serves HTTP without a token, honoured only in `READ_ONLY` | `1`     |
| `MOOMOO_MAX_ORDER_QTY`      | Optional: cap on quantity per order; for a combo, on the largest leg   | `500`         |
| `MOOMOO_MAX_ORDER_NOTIONAL_BY_CURRENCY` | Notional caps, one per currency. Compose defaults to `USD:500` when unset or empty | `USD:500` |

#### Order limits

When a notional cap is configured, an order is valued as **reference price x
quantity x contract multiplier**, in the instrument's own currency. The
reference price is the limit price for a BUY limit order, and otherwise the
larger of the order's own prices and the market.

The limits **fail closed**. An order whose value cannot be established is
refused, not permitted — including an order in a currency with no cap, on a
market whose quote currency has not been verified, or with no usable market
price. The supported US stocks, ETFs and options are valued in USD. Options use
OpenD's `option_contract_multiplier` for premium value; a missing or invalid
multiplier is refused, without substituting deliverable size or assuming 100.
Combo premium limits are not maximum-loss limits.

#### Gateway lock and the execution halt

In `REAL` mode with a stored trade credential, the server owns the gateway's
lock: it keeps the gateway locked at rest and unlocks only for the instant one
order is dispatched. Manual `unlock_trade` is refused in that configuration,
because it would leave the gateway unlocked indefinitely.

If the re-lock after an order fails, the gateway may still be unlocked, so
execution **halts**: new placements and `NORMAL`/`ENABLE` modifications are
refused while cancellations stay allowed. `check_health` reports the halt, and
a successful `lock_trade` is the only way to clear it.

#### Trading mode

`MOOMOO_TRADING_MODE` decides what this deployment is allowed to do, which is a
separate question from what your broker permits. It is enforced in the service
layer, before any request reaches OpenD.

| Mode                  | Account reads and previews | `trd_env='SIMULATE'` writes | `trd_env='REAL'` writes | `unlock_trade` |
| --------------------- | -------------------------- | --------------------------- | ----------------------- | -------------- |
| `READ_ONLY` (default) | Allowed                    | Denied                      | Denied                  | Denied         |
| `SIMULATE`            | Allowed                    | Allowed                     | Denied                  | Denied         |
| `REAL`                | Allowed                    | Allowed                     | Allowed                 | Only without a stored credential, with an explicit password |

- "Writes" means placing an order, placing a combo order, modifying an order,
  and cancelling an order. `READ_ONLY` blocks all four — including cancelling an
  order placed elsewhere.
- A denied request returns an explicit policy error. It is never rerouted into a
  different account environment.
- Configuring `MOOMOO_TRADE_PASSWORD` does **not** change the mode. Nothing
  unlocks at startup. In REAL mode with a stored credential, the server unlocks
  only around an authorized write and re-locks afterward.
- An unrecognized value fails startup rather than falling back to a permissive
  mode.
- Reads are still subject to your broker's own permissions and to `unlock_trade`
  for REAL account data. The mode caps what the server will attempt; it does not
  grant anything.

`check_health` reports the configured mode as `trading_mode` and the account
discovery filter as `trade_market`, even when OpenD is unavailable.

#### Trade account discovery and explicit targeting

`MOOMOO_TRADING_MARKET` configures the single trade context for the process.
It defaults to `NONE`, asking the provider to return all securities markets
available to the configured login and firm. This replaces the SDK's previous
implicit `HK` discovery filter. Set `MOOMOO_TRADING_MARKET=HK` to retain that
former scope. Accepted values are `NONE`, `HK`, `US`, `CN`, `HKCC`, `SG`, `AU`,
`JP`, `MY`, and `CA`. Changing it requires restarting the process.

The filter controls discovery only. It does not grant or restrict trading
permission, change `MOOMOO_TRADING_MODE`, or limit quote-market queries. These
settings describe different things: `MOOMOO_LOGIN_REGION=sg` selects the OpenD
login region, `MOOMOO_SECURITY_FIRM=FUTUSG` selects the securities firm, `US`
is an account market, and `SIMULATE` is a trading environment.

Use a response filter to locate the account, then pass its exact string ID to
the next read or write:

```text
get_accounts(market="US", trd_env="SIMULATE")
get_account_summary(trd_env="SIMULATE", acc_id="<exact returned account ID>")
```

An account authorized for several markets appears once per response. A
`market="US"` filter cannot create a US account when the configured context
returns none. Account-bound reads resolve `acc_id="0"` only when exactly one
account matches `trd_env`; zero or several matches fail before the account
query. An explicit ID must still belong to that environment. The resolver for
reads does not use the REAL write allowlist.

### 3. Connect an MCP client

#### HTTP endpoint: Claude Code and other HTTP clients

The container deployment and source launches serve authenticated Streamable HTTP
in stateless mode with JSON responses at `http://127.0.0.1:8000/mcp`. That address refers to the Docker host; a client on
another machine needs an authorized private route or port forward to it. Do not
publish OpenD's port 11111. The token must match the deployed `MCP_AUTH_TOKEN`.

For Claude Code, the [official MCP configuration guide](https://code.claude.com/docs/en/mcp)
documents HTTP transport and bearer headers:

```bash
claude mcp add --transport http --scope user moomoo http://127.0.0.1:8000/mcp --header "Authorization: Bearer <token>"
```

The token above is a placeholder; configure actual credentials privately through
your client's supported credential mechanism. For other clients, choose
Streamable HTTP, the reachable `/mcp` endpoint and its bearer header using that
client's configuration format. A shared protocol does not establish that every
client supports the same JSON configuration or authentication options.

Claude's [remote connectors](https://support.claude.com/en/articles/11175166-get-started-with-custom-connectors-using-remote-mcp)
connect from Anthropic infrastructure. A host-loopback URL is not reachable by
that path; this repository does not provide a ready-to-use Claude remote connector
or a stdio-to-HTTP bridge for its private container endpoint. Do not assume a
`url`/`headers` entry in Desktop's local process configuration creates one.

#### ChatGPT

Use the [optional official tunnel](docs/deploy-vps.md#optional-chatgpt-access)
through `deploy.sh --chatgpt`. It connects to this fork's existing container and
permits READ_ONLY or SIMULATE. ChatGPT can read real accounts and, in SIMULATE,
trade only explicitly allowlisted paper accounts through the execution journal.
Real writes and unlocking are refused; disable the tunnel before switching to
REAL. The tunnel uses the same stateless HTTP endpoint. Validate tool discovery
and `check_health` in the actual client before relying on the integration.

## AI Agent Guidance

> **IMPORTANT**: All account tools default to **REAL** trading accounts, and the
> server refuses order writes unless `MOOMOO_TRADING_MODE` permits them.

When using this MCP server, AI agents **MUST**:

0. **Check the configured trading mode** with `check_health` before proposing an
   order. In `READ_ONLY` — the default — placing, modifying, and cancelling
   orders all fail with a policy error, so offer analysis rather than a trade.

1. **Notify the user clearly** before accessing REAL account data. Example:

   > "I'm about to access your **REAL trading account**. This will show your actual portfolio and balances."

2. **Read first, unlock only if the read says so.** Unlocking is the
   *gateway's* trading lock and is separate from reading account data. Do not
   call `unlock_trade` pre-emptively: it is denied unless
   `MOOMOO_TRADING_MODE=REAL`, so an unnecessary unlock turns a read that would
   have succeeded into a policy error.
   - Call the read you want (`get_account_summary`, `get_positions`, …) with
     `trd_env='REAL'`.
   - If it reports a locked gateway, inspect the configured mode and credential
     policy. Manual `unlock_trade` requires REAL mode, no stored trade credential,
     and an explicitly supplied password/hash. With a stored credential, manual
     unlock is refused; writes use just-in-time unlocking. Do not change modes
     or request a trade password merely to bypass a failed read.

3. **Only use SIMULATE accounts when explicitly requested** by the user. To use simulation:
   - Pass `trd_env='SIMULATE'` parameter explicitly.
   - No unlock is required for simulation accounts.

4. **Select and bind an account explicitly** when more than one account can
   match the requested environment. For a US paper read, call
   `get_accounts(market="US", trd_env="SIMULATE")`, then pass the exact string
   `acc_id` returned to `get_account_summary`, `get_assets`, `get_positions`, or
   order-history reads. `market` and `trd_env` filters are discovery filters;
   they do not change the process connection or authorize a write.

### Workflow Example

```text
User: "Show me my portfolio"

Agent Response:
"I'm accessing your REAL trading account to show your portfolio.
If you prefer to use a simulation account instead, please let me know."

[Calls get_account_summary; if access fails, reports the error and checks
 the configured policy before considering an authorized recovery action]
```

### Managing Subscriptions

`get_stock_quote` and `get_order_book` subscribe automatically, so symbols
accumulate on this server's quote connection as they are read. To release some:

```text
1. get_subscriptions()                                  → what this connection holds
2. unsubscribe_market_data(codes=[...], sub_types=[...]) → release the ones you picked
3. get_subscriptions()                                  → confirm what is still held
```

Step 2 reports `acknowledged`, meaning the provider accepted the request — step
3 is how you confirm the state actually settled. The provider quota is shared
across every client attached to the same OpenD gateway, so a small
`connection` figure does not by itself mean there is headroom.

### Paging Through Historical Candles

```text
page = get_historical_klines_page(code="US.AAPL", start="2025-01-01", end="2025-12-31")
rows = page["data"]
while page["has_more"]:
    page = get_historical_klines_page(
        code="US.AAPL", start="2025-01-01", end="2025-12-31",
        cursor=page["next_cursor"],
    )
    rows += page["data"]
```

Pass every non-cursor argument through unchanged on each iteration, bound the
loop, and treat a failed page as an error rather than as the end of the data.
Omitted `start`/`end` are resolved once on the first page and carried in the
cursor, so a traversal that crosses midnight keeps reading the same window.

### Option Strategy Workflow

Discovery comes before pricing, and pricing before submission:

```text
1. get_option_expiration_date("US.AAPL")   → pick an expiry
2. get_option_chain("US.AAPL", start=expiry, end=expiry, option_type="CALL")
                                           → exact contract symbols
3. preview_combo_order(legs, price, qty)   → margin and buying-power impact
4. [confirm with the user]
5. place_combo_order(...)                  → requires MOOMOO_TRADING_MODE
```

Pass contract symbols through from step 2 to steps 3 and 5 exactly as
received. To **close** an existing strategy, get each leg's `position_id` from
`get_positions(show_option_strategy_view=True)` first and include it in the
legs, again unchanged.

### Order Status Filter Usage

When using `get_orders` or `get_history_orders`, the `status_filter_list` parameter accepts an array of **string values**:

```json
["SUBMITTED", "FILLED_ALL", "CANCELLED_ALL"]
```

**Valid status strings:**

- `UNSUBMITTED`, `WAITING_SUBMIT`, `SUBMITTING`, `SUBMIT_FAILED`
- `SUBMITTED`, `FILLED_PART`, `FILLED_ALL`
- `CANCELLING_PART`, `CANCELLING_ALL`, `CANCELLED_PART`, `CANCELLED_ALL`
- `REJECTED`, `DISABLED`, `DELETED`, `FAILED`, `NONE`

> **Note**: The server automatically converts these strings to the required SDK enum format. If no orders match the filter, an empty list is returned.

## Migration Notes

### Stateless Streamable HTTP is the only transport

`streamable-http` is the default for both containers and source launches. Older
`MCP_TRANSPORT=sse` or `stdio` settings fail startup; change them to
`streamable-http` or remove the setting. Clients must connect by HTTP to `/mcp`
and send a valid bearer token. Command-based stdio server configurations are
no longer supported. The endpoint returns JSON and issues no session ID.

Without `MCP_AUTH_TOKEN`, startup fails unless the explicit READ_ONLY development
opt-out `MCP_ALLOW_UNAUTHENTICATED_HTTP=1` is enabled.

### Trading mode must be configured before writing

`MOOMOO_TRADING_MODE` defaults to `READ_ONLY`, which refuses every order write
and every unlock. A deployment that submits orders must now set it explicitly:

- paper trading → `MOOMOO_TRADING_MODE=SIMULATE`, and keep passing
  `trd_env='SIMULATE'` on each call;
- live trading → `MOOMOO_TRADING_MODE=REAL`.

Previously the server relied on the presence of a trade password to imply
simulation-only operation, but nothing enforced that: tool defaults were `REAL`
and any caller could submit a live order. The mode now decides, and the password
no longer implies anything about it.

### Trade account market selection changes the discovery default

`MOOMOO_TRADING_MARKET` now defaults to `NONE` instead of the SDK's implicit
`HK`, so `get_accounts()` can return both HK and US accounts when the provider
exposes them. Deployments that need the old discovery scope can set
`MOOMOO_TRADING_MARKET=HK`. This is a breaking discovery change: zero-ID reads
that used to let the SDK pick its first account now fail if multiple accounts
match `trd_env`. Call `get_accounts` and use its exact string ID. This does not
change mutation authorization, REAL allowlists, trading-mode gates, or order
limits.

### Account, position, and combo identifiers are strings

Account tools return `acc_id`, `position_id`, and `combo_id` as decimal
**strings** in both the text and structured content of a response. This covers
`get_accounts`, `get_assets`, `get_positions`, and `get_account_summary`,
including the positions nested inside a summary.

These are 64-bit values around 3e18. A client that parses JSON numbers as
IEEE-754 doubles — which a JavaScript-based MCP client does — rounds anything
above 2^53 into a different, still valid-looking integer. That corruption cannot
be detected or repaired on the way back, which is why the value has to leave as
a string.

What to change in a client:

- Pass identifiers back exactly as received. Do not call `Number()`, `parseInt`,
  or `int()` on them.
- Replace any numeric comparison or arithmetic on an id with a string
  comparison.
- Balances, quantities, prices, and every other field are unchanged and remain
  numbers.

Tool *inputs* already accepted string identifiers, so no call site needs a new
argument type. An identifier that reaches the boundary as a float or a boolean
is rejected with an explicit error rather than emitted as a plausible id: the
precision was already lost upstream, and a silent replacement would send a
request against the wrong account or position.

## Broker request limits

Every runtime SDK operation with a published timed limit passes through one
process-owned dispatcher. PyrateLimiter maintains rolling-window history;
reservations remain counted until the SDK starts or the caller cancels. Public
MCP calls wait asynchronously for up to five seconds for capacity, before taking
an SDK worker. Direct synchronous service calls fail immediately when full.
Errors name the quota pool and include `retry_after_seconds`, a rounded estimate
that other callers may affect. Dispatched attempts remain charged on provider
failure or cancellation. The server never automatically retries broker calls.

All limits below are calls per rolling **30 seconds**, with a **0.1-second safety
margin**. Each operation has an independent pool unless explicitly grouped below.
Account pools use the resolved account ID, including when a caller supplies
`acc_id="0"`. Quote and user pools span local clients and services; internal order
checks, instrument snapshots, and paper recovery use the same pools as tools.

| SDK operation and broker documentation | Calls | Local quota pool / condition |
| --- | ---: | --- |
| [accinfo_query](https://openapi.moomoo.com/moomoo-api-doc/en/trade/get-funds.html) | 10 | Per account; only `refresh_cache=True` |
| [position_list_query](https://openapi.moomoo.com/moomoo-api-doc/en/trade/get-position-list.html) | 10 | Per account; only `refresh_cache=True` |
| [order_list_query](https://openapi.moomoo.com/moomoo-api-doc/en/trade/get-order-list.html) | 10 | Per account; only `refresh_cache=True` |
| [deal_list_query](https://openapi.moomoo.com/moomoo-api-doc/en/trade/get-order-fill-list.html) | 10 | Per account; only `refresh_cache=True` |
| [history_order_list_query](https://openapi.moomoo.com/moomoo-api-doc/en/trade/get-history-order-list.html) | 10 | Per account |
| [history_deal_list_query](https://openapi.moomoo.com/moomoo-api-doc/en/trade/get-history-order-fill-list.html) | 10 | Per account |
| [acctradinginfo_query](https://openapi.moomoo.com/moomoo-api-doc/en/trade/get-max-trd-qtys.html), [comboorder_tradinginfo_query](https://openapi.moomoo.com/moomoo-api-doc/en/trade/comboorder-tradinginfo-query.html) | 10 | Shared max-quantity pool per account (conservative interpretation; see below) |
| [get_margin_ratio](https://openapi.moomoo.com/moomoo-api-doc/en/trade/get-margin-ratio.html) | 10 | Per gateway user |
| [get_acc_cash_flow](https://openapi.moomoo.com/moomoo-api-doc/en/trade/get-acc-cash-flow.html) | 20 | Per account |
| [unlock_trade](https://openapi.moomoo.com/moomoo-api-doc/en/trade/unlock.html) | 10 | Per gateway user; both unlocking and locking |
| [place_order](https://openapi.moomoo.com/moomoo-api-doc/en/trade/place-order.html), [place_combo_order](https://openapi.moomoo.com/moomoo-api-doc/en/trade/place-combo-order.html) | 15 | Shared placement pool per account; starts at least 20 ms apart |
| [modify_order](https://openapi.moomoo.com/moomoo-api-doc/en/trade/modify-order.html), including cancellations | 20 | Per account; starts at least 40 ms apart |
| [get_market_snapshot](https://openapi.moomoo.com/moomoo-api-doc/en/quote/get-market-snapshot.html) | 60 | Shared quote pool for this operation |
| [request_history_kline](https://openapi.moomoo.com/moomoo-api-doc/en/quote/request-history-kline.html) | 60 | Initial pages only; continuation requests are exempt |
| [get_option_expiration_date](https://openapi.moomoo.com/moomoo-api-doc/en/quote/get-option-expiration-date.html) | 60 | Shared quote pool for this operation |
| [get_option_chain](https://openapi.moomoo.com/moomoo-api-doc/en/quote/get-option-chain.html) | 10 | Shared quote pool for this operation |
| [get_market_state](https://openapi.moomoo.com/moomoo-api-doc/en/quote/get-market-state.html) | 10 | Shared quote pool for this operation |
| [request_trading_days](https://openapi.moomoo.com/moomoo-api-doc/en/quote/request-trading-days.html) | 30 | Shared quote pool for this operation |
| [get_user_security_group](https://openapi.moomoo.com/moomoo-api-doc/en/quote/get-user-security-group.html) | 10 | Shared quote pool for this operation |
| [get_user_security](https://openapi.moomoo.com/moomoo-api-doc/en/quote/get-user-security.html) | 10 | Shared quote pool for this operation |

The combo max-quantity documentation describes a limit for max-quantity query
APIs collectively. This implementation conservatively groups both APIs; that
wording does not explicitly establish whether the broker keeps separate pools.
Placement sharing is explicitly documented by the broker.

Credential-managed REAL writes reserve two unlock-interface calls before
unlocking, so a write cannot exhaust the capacity needed to relock. Paper writes
reserve mutation capacity before persisting a dispatch marker. Admission errors
report that no order was sent; errors after SDK dispatch preserve the existing
outcome and reconciliation rules. Paced pools admit only one pending start at a
time, so worker congestion cannot turn old reservations into clustered starts.

The following runtime APIs have no published numeric frequency limit and pass
through without a timed budget: [get_acc_list](https://openapi.moomoo.com/moomoo-api-doc/en/trade/get-acc-list.html),
[get_global_state](https://openapi.moomoo.com/moomoo-api-doc/en/quote/get-global-state.html),
[get_stock_basicinfo](https://openapi.moomoo.com/moomoo-api-doc/en/quote/get-static-info.html),
[get_stock_quote](https://openapi.moomoo.com/moomoo-api-doc/en/quote/get-stock-quote.html),
[get_order_book](https://openapi.moomoo.com/moomoo-api-doc/en/quote/get-order-book.html),
[subscribe / unsubscribe](https://openapi.moomoo.com/moomoo-api-doc/en/quote/sub.html),
and [query_subscription](https://openapi.moomoo.com/moomoo-api-doc/en/quote/query-subscription.html).
Subscription capacity and the provider's minimum hold period still apply.

The supported deployment runs one MCP process per OpenD gateway/user. These
budgets do not coordinate other processes or independent OpenD clients, and
restarting the server resets local history. Provider rate-limit errors therefore
remain possible and are returned without replay. Python integrations creating
several service wrappers must inject the same `BrokerRequestDispatcher` instance
for wrappers using the same gateway/user.

## Contributing

Use Python 3.12 locally and preserve Python 3.10 compatibility. `pyproject.toml`
defines the supported syntax and type target; `uv.lock` pins dependencies.
Tests mock the SDK and do not need a live OpenD gateway.
On macOS, the CI script tests need Bash 4 or newer (`mapfile` is unavailable in
Apple's Bash 3.2). Install `bash` with Homebrew and put its `bin` directory first
on PATH for the test command. The tests execute `bash` from PATH.

### Checks and completion

Follow the [completion policy](AGENTS.md#completion-policy).
Select the relevant checks below; this is a command reference, not a requirement
to run every command for every task.

```bash
uv sync --all-extras --dev
uv run ruff check .
uv run ruff format --check .
uv run basedpyright
uv run pytest
npx --yes @fission-ai/openspec@1.14.1 validate --all --strict
```

Select checks by scope:

| Change | Required evidence |
| --- | --- |
| Python behavior | Relevant tests during iteration, then full pytest, Ruff, and basedpyright |
| Shell, Compose, deployment, or CI YAML | Relevant script/topology tests and full pytest; lint/type checks for affected Python. OpenSpec-only tooling uses the checks below. |
| Container behavior | Applicable `scripts/smoke-test.sh`, `scripts/test-paper-container.sh`, or `scripts/test-tunnel-container.sh` with Docker |
| Documentation | Review acceptance criteria, links, and consistency |
| Specs, managed OpenSpec instructions, or OpenSpec tooling scripts | Strict OpenSpec validation and script syntax checks; for regeneration, confirm version, exact workflow inventory, and repeatability |

CI selects Python checks, container smoke checks, and image builds separately.
Known prose and OpenSpec tooling skip Python checks; unknown paths run them.
Application source, container configuration, dependencies, and smoke/paper
fixtures trigger container smoke checks. Image builds follow their actual
build inputs on PRs and pushes; main still retags unchanged images and rebuilds
if the baseline image is unavailable. Tunnel integration runs in both Docker
contexts for tunnel inputs and shared server/transport configuration, not every
script or test. Shared CI workflow changes remain conservative and run broad
checks. Keep path filters current when adding build or integration dependencies.

Use Conventional Commits and merge through pull requests. Install local hooks:

```bash
uv run pre-commit install
uv run pre-commit install --hook-type commit-msg
```

Ruff uses an 88-character line length. Tool docstrings are the descriptions agents
read, so include preconditions and failure modes as well as arguments.
The container smoke test substitutes `tests/fixtures/opend_stub.py` for OpenD.
The paper container checks cover persistence, process locking, backup/restore,
dirty rollback journals, and READ_ONLY without journal storage. Live provider
response-loss and external retry-chain acceptance remain separate evidence.

### OpenSpec integrations

Use **`@fission-ai/openspec`**; the unscoped npm package is unrelated. The CLI
version is pinned to 1.14.1, matching CI. Check `openspec --version` for generated
integration differences. CLI `validate` checks artifact structure; review code
and tests against the change artifacts to verify behavior.

Regenerate from the repository root with:

```bash
npx --yes @fission-ai/openspec@1.14.1 update
```

Review the generated diff and workflow inventory, then regenerate again to
confirm no further changes. Do not hand-edit managed integrations. Keep
`openspec/config.yaml` limited to invariants and pointers; architectural
requirements belong in `openspec/specs/`.

### Local rootless Docker on macOS

Docker Engine needs Linux. On this Apple Silicon Mac, Lima runs an ARM Linux
VM with a rootless Docker daemon; Rosetta executes the x86_64 image binaries.
The Compose service targets `linux/amd64`, matching the remote x86_64 server.
The VM itself remains ARM64, so this is not a native x86_64 runtime test.

#### Initial setup

Install the host tools:

```sh
brew install lima docker docker-compose docker-buildx
```

Add `/opt/homebrew/lib/docker/cli-plugins` to `cliPluginsExtraDirs` in
`~/.docker/config.json`, preserving any existing settings.

Create the VM and Docker context:

```sh
limactl start --name=moomoo-rootless --vm-type=vz --rosetta \
  --cpus=4 --memory=6 --disk=40 --mount-none -y template:docker
docker context create lima-moomoo-rootless \
  --docker "host=unix://$HOME/.lima/moomoo-rootless/sock/docker.sock"
docker context use lima-moomoo-rootless
```

No host directories are mounted into the VM. Docker sends the build context
through its socket; Compose's default named volume stores OpenD state inside
the VM. A macOS path in `OPEND_DATA_DIR` would require a separately configured
VM mount. Avoid deleting the VM if it holds session data you need.

#### Everyday use

```sh
limactl start moomoo-rootless
docker context use lima-moomoo-rootless
docker info --format '{{json .SecurityOptions}}'
# Must include name=rootless.
docker compose build
```

To release VM resources:

```sh
limactl stop moomoo-rootless
```

Building does not start the application or log into Moomoo. Start the stack
separately once its runtime configuration is ready.

This VM is for local development. Production `deploy.sh` uses the Linux host's
`rootless` context and CI-published images; local builds are not copied there.

## License

This project is licensed under the Apache License 2.0 - see the [LICENSE](LICENSE) file for details.

## Disclaimer

**Unofficial Project**: This software is an independent open-source project and is **not** affiliated with, endorsed by, or sponsored by Moomoo Inc., Futu Holdings Ltd., or their affiliates.

- **Use at your own risk**: Trading involves financial risk. The authors provide this software "as is" without warranty of any kind.
- **Test First**: Always test your agents and tools in the **Simulation (Paper Trading)** environment before using real funds.
