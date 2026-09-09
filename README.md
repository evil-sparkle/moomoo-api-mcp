# Moomoo API MCP Server

An MCP (Model Context Protocol) server for the Moomoo trading platform. This server allows AI agents (like Claude Desktop or Gemini) to access market data, account information, and execute trades via the moomoo-api Python SDK.

## 🚀 Build Your Own Trading Agent

**Take your trading to the next level with AI!**

This MCP server empowers developers to build custom trading skills and strategies. By integrating this tool, you can enable any compatible AI agent to interact directly with the Moomoo platform on your behalf. Whether you want an AI assistant that monitors the market, analyzes your portfolio, or automatically executes complex trading strategies, this server provides the seamless bridge between your custom AI logic and Moomoo's powerful trading infrastructure.

## Features

- **Market Data**: Real-time quotes, historical K-lines, market snapshots, and order books.
- **Account Management**: Comprehensive account summaries, assets, positions, and cash flow analysis.
- **Trading**: Full order management including placing, modifying, and canceling orders.
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
- `get_historical_klines`: Retrieve historical candlestick data (Day, Week, Min, etc.).
- `get_market_snapshot`: Get efficient market snapshots for multiple stocks.
- `get_order_book`: View real-time bid/ask order book depth.

### Trading

- `place_order`: Place a new order (Market, Limit, Stop, etc.).
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
Using `--refresh` ensures you are always running the latest version:

```bash
# Optional: Set these environment variables for REAL trading access.
# If omitted, the server will safely run in SIMULATE-only (paper trading) mode.
export MOOMOO_TRADE_PASSWORD="your_trading_password"
export MOOMOO_SECURITY_FIRM="FUTUSG" # e.g. FUTUSG, FUTUINC, etc.

uvx --refresh moomoo-api-mcp
```

### Permanent Installation

To install it as a persistent tool available in your shell:

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
   git clone https://github.com/Litash/moomoo-api-mcp.git
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

| Variable                | Description                                | Example  |
| ----------------------- | ------------------------------------------ | -------- |
| `MOOMOO_TRADE_PASSWORD` | Your trading password (plain text)         | `123456` |
| `MOOMOO_SECURITY_FIRM`  | Your broker region (e.g., FUTUSG, FUTUINC) | `FUTUSG` |

> **Note**: Without these, the server runs in **SIMULATE-only mode** (paper trading).

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
        "MOOMOO_TRADE_PASSWORD": "your_trading_password",
        "MOOMOO_SECURITY_FIRM": "FUTUSG"
      }
    }
  }
}
```

> **Security**: Never commit your password to version control. The `env` block in the config file remains local.

## AI Agent Guidance

> **IMPORTANT**: All account tools default to **REAL** trading accounts.

When using this MCP server, AI agents **MUST**:

1. **Notify the user clearly** before accessing REAL account data. Example:

   > "I'm about to access your **REAL trading account**. This will show your actual portfolio and balances."

2. **Follow the unlock workflow** for REAL accounts:
   - First call `unlock_trade` (it handles env vars automatically, or pass password if needed).
   - Then call account/trading tools (they default to `trd_env='REAL'`).

3. **Only use SIMULATE accounts when explicitly requested** by the user. To use simulation:
   - Pass `trd_env='SIMULATE'` parameter explicitly.
   - No unlock is required for simulation accounts.

### Workflow Example

```text
User: "Show me my portfolio"

Agent Response:
"I'm accessing your REAL trading account to show your portfolio.
If you prefer to use a simulation account instead, please let me know."

[Proceeds to unlock_trade → get_account_summary]
```

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
