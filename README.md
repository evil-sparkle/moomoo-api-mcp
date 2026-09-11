# Moomoo API MCP Server

An MCP (Model Context Protocol) server for the Moomoo trading platform. This server allows AI agents (like Claude Desktop or Gemini) to access market data, account information, and execute trades via the moomoo-api Python SDK.

## 🚀 Build Your Own Trading Agent

**Take your trading to the next level with AI!**

This MCP server empowers developers to build custom trading skills and strategies. By integrating this tool, you can enable any compatible AI agent to interact directly with the Moomoo platform on your behalf. Whether you want an AI assistant that monitors the market, analyzes your portfolio, or automatically executes complex trading strategies, this server provides the seamless bridge between your custom AI logic and Moomoo's powerful trading infrastructure.

## About this fork

This repository is a fork of [Litash/moomoo-api-mcp](https://github.com/Litash/moomoo-api-mcp). The PyPI package `moomoo-api-mcp` (what `uvx moomoo-api-mcp` and `uv tool install moomoo-api-mcp` install) is published by upstream, not by this fork; this fork does not publish to PyPI, and `.github/workflows/python-publish.yml` and `manual-release.yml` are inherited from upstream and not maintained here. What this fork builds and maintains is the container deployment — CI (`.github/workflows/ci.yml`) builds the `moomoo-api-mcp` and `moomoo-opend` images, and `docs/deploy-vps.md` deploys them. A PyPI install therefore runs upstream's release, which can differ from this fork's code — use the Docker deployment or a local checkout (`uv run moomoo-api-mcp`) to run this fork.

## Features

- **Market Data**: Real-time quotes, historical K-lines, market snapshots, and order books.
- **Account Management**: Comprehensive account summaries, assets, positions, and cash flow analysis.
- **Trading**: Full order management including placing, modifying, and canceling
  orders, gated by an explicitly configured trading mode.
- **System Health**: Active, bounded health probes of the quote and trade connections to OpenD.
- **Extensible Architecture**: Built on FastMCP for easy extension of trading capabilities.

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
    "gateway_version": "9.2.5208"
  }
  ```

  A `connected` result means OpenD answered. It does **not** mean trading is unlocked, that an order would be accepted, or that a given market is authorized for the account. The server also starts even when OpenD is unreachable, so `check_health` stays callable while you diagnose the gateway.

### Account

- `get_accounts`: List all trading accounts (REAL and SIMULATE).
- `get_account_summary`: Get a complete summary of assets and positions for an account.
- `get_assets`: Retrieve account assets (cash, market value, buying power).
- `get_positions`: Get current stock positions with P/L data.
- `get_max_tradable`: Calculate maximum tradable quantity for a specific stock.
- `get_margin_ratio`: Check margin ratios for specific stocks.
- `get_cash_flow`: Retrieve historical cash flow records.
- `unlock_trade`: Unlock trading access for REAL accounts.

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

## Installation

### Quick Start (Recommended)

You can run the server directly using `uvx` (part of the [uv](https://github.com/astral-sh/uv) toolkit).
Using `--refresh` ensures you are always running the latest version. Note that this installs upstream's PyPI release (see [About this fork](#about-this-fork)):

```bash
# Optional: Set these environment variables for REAL trading access.
# If omitted, the server will safely run in SIMULATE-only (paper trading) mode.
export MOOMOO_TRADE_PASSWORD="your_trading_password"
export MOOMOO_SECURITY_FIRM="FUTUSG" # e.g. FUTUSG, FUTUINC, etc.

uvx --refresh moomoo-api-mcp
```

### Permanent Installation

To install it as a persistent tool available in your shell (this installs upstream's PyPI release — see [About this fork](#about-this-fork)):

```bash
uv tool install moomoo-api-mcp

# Optional: Set these environment variables for REAL trading access.
# If omitted, the server will safely run in SIMULATE-only (paper trading) mode.
export MOOMOO_TRADE_PASSWORD="your_trading_password"
export MOOMOO_SECURITY_FIRM="FUTUSG" # e.g. FUTUSG, FUTUINC, etc.

# Then run:
moomoo-api-mcp
```

> **Note**: The `moomoo-api` Python SDK and other dependencies will be installed automatically.

### Development Setup

1. **Clone the repository**:

   ```bash
   git clone https://github.com/evil-sparkle/moomoo-api-mcp.git
   cd moomoo-api-mcp
   ```

2. **Install dependencies**:

   ```bash
   uv sync
   ```

3. **Run locally**:
   ```bash
   uv run moomoo-api-mcp
   ```

4. **Run tests, linter and type checker**:
   ```bash
   uv sync --extra dev
   uv run ruff check .
   uv run basedpyright
   uv run pytest
   ```

   Install the git pre-commit hook so gitleaks, ruff, basedpyright and pytest
   run on every commit:
   ```bash
   uv run pre-commit install
   ```

   basedpyright compares against `.basedpyright/baseline.json`, which records
   type errors that predate the checker, so only new errors fail. After fixing
   baselined errors, shrink the file with `uv run basedpyright --writebaseline`.

5. **Release Tagging & Versioning**:
   This project follows [Semantic Versioning](https://semver.org/) (`vMAJOR.MINOR.PATCH`). To publish a new version:
   ```bash
   # 1. Bump the version in pyproject.toml (e.g. from 0.1.8 to 0.2.0)
   uv lock
   # 2. Stage the version files and any intended release changes, including new files
   git add pyproject.toml uv.lock
   git commit -m "chore: release v0.2.0"

   # 3. Create an annotated git tag
   git tag -a v0.2.0 -m "Release v0.2.0: Multi-leg options, market discovery, trading policy, container deployment"

   # 4. Push commit and tag to remote
   git push origin main --follow-tags
   ```

---

## 🐳 Containerized Deployment (Docker Compose)

Running OpenD and the MCP server via Docker isolates the OpenD gateway on an internal bridge network (`trading-net`), protects trading credentials, and persists device authorization across restarts.

### 1. Build the Images

```bash
docker compose build
```
*(OpenD is automatically downloaded and installed for `linux/amd64` via Rosetta/emulation on Apple Silicon or natively on x86_64 servers.)*

### 2. Initial Device Activation (Interactive Login)

OpenD requires an interactive verification code (SMS/2FA) on initial device registration:

```bash
docker compose run --rm -it -e OPEND_INTERACTIVE=1 opend
```
Without `OPEND_INTERACTIVE=1`, a headless start without a remembered token exits with an error instead of hanging.

Follow the prompts in your terminal:
1. **Account**: Enter your Moomoo ID, email, or phone number.
2. **Password**: Enter your login password.
3. **Remember Password? `[Y/N]`**: Enter **`Y`** *(Crucial: saves encrypted session token in the persistent `opend-data` Docker volume)*.
4. **Verification Code**: Enter the SMS code sent to your phone.

Once OpenD reports that login succeeded, exit or terminate the process (`Ctrl+C`). Your device credentials and session tokens are preserved in the named Docker volume `opend-data`.

### 3. Configure `.env` for Headless Operation

Copy the sample environment file:
```bash
cp .env.example .env
```

Set your account number so OpenD knows which saved session to load:
```env
MOOMOO_LOGIN_ACCOUNT=12345678
MOOMOO_LOGIN_REGION=sg        # sg (Singapore), us, hk, etc.
MOOMOO_SECURITY_FIRM=FUTUSG   # FUTUSG (Singapore), FUTUINC (US), etc.
MOOMOO_TRADING_MODE=READ_ONLY  # READ_ONLY (default), SIMULATE, or REAL
```

> **Security Note**: You **do not need to store your login password** in `.env`. When `MOOMOO_LOGIN_BY_REMEMBER=1` (default), OpenD authenticates headlessly using the encrypted token stored in `opend-data`.

### 4. Start the Stack

```bash
# Start OpenD gateway and MCP server in background
docker compose up -d

# Check OpenD logs
docker compose logs -f opend

# Check MCP server logs
docker compose logs -f moomoo-mcp
```

### 5. Stop the Stack

```bash
docker compose down
```
*(Your login state remains safely preserved in the `opend-data` volume.)*

---

## Configuration

### 1. Prerequisites

#### Moomoo OpenD (Required)

The MCP server communicates with the Moomoo API via **Moomoo OpenD**, a local gateway application. You **MUST** install and run this first.

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
   - **Note**: The MCP server connects to `127.0.0.1:11111` by default.

### 2. Environment Variables

To enable **REAL account** access, you must securely provide your credentials.

| Variable                    | Description                                                           | Example       |
| --------------------------- | --------------------------------------------------------------------- | ------------- |
| `MOOMOO_TRADING_MODE`       | Which writes this server may issue. Default `READ_ONLY`.              | `SIMULATE`    |
| `MOOMOO_TRADE_PASSWORD`     | Your trading password (plain text)                                    | `123456`      |
| `MOOMOO_TRADE_PASSWORD_MD5` | MD5 hash of 6-digit trade PIN (alternative to plain text)             | `e10adc...`   |
| `MOOMOO_SECURITY_FIRM`      | Your broker region (e.g., FUTUSG, FUTUINC)                            | `FUTUSG`      |
| `MCP_TRANSPORT`             | Optional: Transport mode (`streamable-http`, `sse`, `stdio`)          | `streamable-http` |
| `MCP_AUTH_TOKEN`            | Optional: Bearer token secret required for MCP HTTP/SSE clients       | `secret-token`|
| `MOOMOO_MAX_ORDER_QTY`      | Optional: Safety cap on maximum quantity/shares per order             | `500`         |
| `MOOMOO_MAX_ORDER_NOTIONAL` | Optional: Safety cap on maximum estimated notional ($) per order      | `25000`       |

#### Trading mode

`MOOMOO_TRADING_MODE` decides what this deployment is allowed to do, which is a
separate question from what your broker permits. It is enforced in the service
layer, before any request reaches OpenD.

| Mode                  | Account reads and previews | `trd_env='SIMULATE'` writes | `trd_env='REAL'` writes | `unlock_trade` |
| --------------------- | -------------------------- | --------------------------- | ----------------------- | -------------- |
| `READ_ONLY` (default) | Allowed                    | Denied                      | Denied                  | Denied         |
| `SIMULATE`            | Allowed                    | Allowed                     | Denied                  | Denied         |
| `REAL`                | Allowed                    | Allowed                     | Allowed                 | Allowed        |

- "Writes" means placing an order, placing a combo order, modifying an order,
  and cancelling an order. `READ_ONLY` blocks all four — including cancelling an
  order placed elsewhere.
- A denied request returns an explicit policy error. It is never rerouted into a
  different account environment.
- Configuring `MOOMOO_TRADE_PASSWORD` does **not** change the mode. Only `REAL`
  mode unlocks trading at startup, and a failed unlock leaves the mode as it was
  without retrying or placing anything.
- An unrecognized value fails startup rather than falling back to a permissive
  mode.
- Reads are still subject to your broker's own permissions and to `unlock_trade`
  for REAL account data. The mode caps what the server will attempt; it does not
  grant anything.

`check_health` reports the configured mode as `trading_mode`.

### 3. Configure Claude Desktop

Add the server to your `claude_desktop_config.json`:

#### Option A: Using PyPI Package (Recommended)

```json
{
  "mcpServers": {
    "moomoo": {
      "command": "uvx",
      "args": ["--refresh", "moomoo-api-mcp"],
      "env": {
        "MOOMOO_TRADING_MODE": "REAL",
        "MOOMOO_TRADE_PASSWORD": "your_trading_password",
        "MOOMOO_SECURITY_FIRM": "FUTUSG"
      }
    }
  }
}
```

> **Note**: The `--refresh` flag ensures you always have the latest version but may increase startup time due to version checking. You can remove it once you have the correct version installed.

#### Option B: Local Development

```json
{
  "mcpServers": {
    "moomoo": {
      "command": "uv",
      "args": [
        "--directory",
        "C:\\path\\to\\moomoo-api-mcp",
        "run",
        "moomoo-api-mcp"
      ],
      "env": {
        "MOOMOO_TRADING_MODE": "REAL",
        "MOOMOO_TRADE_PASSWORD": "your_trading_password",
        "MOOMOO_SECURITY_FIRM": "FUTUSG"
      }
    }
  }
}
```

#### Option C: Containerized Deployment (Docker Streamable HTTP)

When running the server via Docker Compose (`MCP_TRANSPORT=streamable-http`), the server listens on `http://127.0.0.1:8000/mcp`.

If `MCP_AUTH_TOKEN` is configured, client requests must provide the bearer token in the `Authorization` header:

```bash
claude mcp add --transport http -s user moomoo http://127.0.0.1:8000/mcp --header "Authorization: Bearer <token>"
```

Or configure it in your `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "moomoo": {
      "url": "http://127.0.0.1:8000/mcp",
      "headers": {
        "Authorization": "Bearer <token>"
      }
    }
  }
}
```

> **SSE Alternative**: Server-Sent Events (SSE) remains supported via `MCP_TRANSPORT=sse` at `http://127.0.0.1:8000/sse` (`claude mcp add --transport sse -s user moomoo http://127.0.0.1:8000/sse --header "Authorization: Bearer <token>"`).

> **Generating `MCP_AUTH_TOKEN`**: Generate a secure random token with `openssl rand -hex 32` and set it in your `.env` file (`MCP_AUTH_TOKEN=...`). If left unset, authentication is disabled (suitable for local-only STDIO).

> **Security**: Never commit your password to version control. The `env` block in the config file remains local.

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
   - Only if it fails asking for trading to be unlocked, call `unlock_trade`
     (it uses the env vars automatically, or takes a password), then retry.

3. **Only use SIMULATE accounts when explicitly requested** by the user. To use simulation:
   - Pass `trd_env='SIMULATE'` parameter explicitly.
   - No unlock is required for simulation accounts.

### Workflow Example

```text
User: "Show me my portfolio"

Agent Response:
"I'm accessing your REAL trading account to show your portfolio.
If you prefer to use a simulation account instead, please let me know."

[Proceeds to get_account_summary; only if that reports trading is locked
 does it call unlock_trade and retry]
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

## License

This project is licensed under the Apache License 2.0 - see the [LICENSE](LICENSE) file for details.

## Disclaimer

**Unofficial Project**: This software is an independent open-source project and is **not** affiliated with, endorsed by, or sponsored by Moomoo Inc., Futu Holdings Ltd., or their affiliates.

- **Use at your own risk**: Trading involves financial risk. The authors provide this software "as is" without warranty of any kind.
- **Test First**: Always test your agents and tools in the **Simulation (Paper Trading)** environment before using real funds.
