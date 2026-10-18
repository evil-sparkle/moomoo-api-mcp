"""Actionable tool failures at the MCP boundary, with success schemas intact."""

import inspect
import json
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import CallToolResult, TextContent
from mcp.types import Tool as MCPTool
from pydantic import ValidationError

from moomoo_mcp.client_authorization import (
    ClientAuthorizationError,
    authorize_tool,
    tool_visible,
)
from moomoo_mcp.services.clock import utc_now_iso
from moomoo_mcp.services.execution_store import ExecutionConflict, ExecutionStoreError
from moomoo_mcp.services.health import sanitize_error
from moomoo_mcp.services.order_errors import (
    OrderNotSentError,
    OrderOutcomeUnknownError,
    OrderReceiptUnreadableError,
)
from moomoo_mcp.services.query_contracts import TradeQueryError
from moomoo_mcp.services.quote_errors import QuotePermissionDeniedError
from moomoo_mcp.services.rate_limit import ProviderRateLimitError
from moomoo_mcp.services.sdk_response import SDKResponseError
from moomoo_mcp.services.trading_controls import TradingControlError
from moomoo_mcp.services.trading_policy import TradingPolicyError
from moomoo_mcp.tools.kline_cursor import CursorError
from moomoo_mcp.tools.serialization import IdentifierSerializationError

AGENT_INSTRUCTIONS = (
    "Tool errors provide error_code, retryable and next_action. Follow that "
    "guidance and continue independent work rather than repeating unchanged "
    "failed requests. Respect reported rate-limit retry_after_seconds. Correct "
    "invalid arguments using the tool schema. Preserve the user's account and "
    "REAL/SIMULATE environment; never switch them to bypass a failure. Failed "
    "order lookups do not prove an order is absent. For uncertain or unreadable "
    "order outcomes, inspect the original execution and broker orders before "
    "any further mutation; never resend blindly or invent a new operation ID. "
    "Preserve structured execution states and recovery evidence. Empty results "
    "mean no matches within the reported query scope; preserve unavailable "
    "fields rather than inventing values. Connected health confirms gateway "
    "connectivity, not quote access or permission to trade. "
    "OpenD quote rights are separate from app permissions and trading mode. "
    "When a tool returns QUOTE_PERMISSION_DENIED, stop repeating equivalent "
    "quote requests to this OpenD session until its permissions or quote-right "
    "allocation change. Continue the task using another available market-data "
    "tool or web source. Confirm the exact instrument, exchange listing and "
    "currency, and report the source, quote timestamp/timezone and delayed or "
    "stale status. Label unverified quote times or freshness as unknown, and "
    "do not present those quotes as real-time. Holdings costs and account "
    "valuations are not verified live market quotes. Continue independent "
    "account reads when those calls succeed. "
    "If no suitable alternative source is available, report the missing prices "
    "as unavailable and complete the parts of the task that do not require them."
)


def error_result(payload: dict[str, Any]) -> CallToolResult:
    """Use the same diagnostic in both MCP content views."""
    return CallToolResult(
        isError=True,
        content=[TextContent(type="text", text=json.dumps(payload))],
        structuredContent=payload,
    )


def quote_permission_result(
    error: QuotePermissionDeniedError, tool: str
) -> CallToolResult:
    """Keep the diagnostic in the response; HTTP drops logging notifications."""
    payload = {
        "error_code": "QUOTE_PERMISSION_DENIED",
        "message": "OpenD denied quote permission for this request.",
        "provider": "Moomoo OpenD",
        "tool": tool,
        "operation": error.operation,
        "checked_at": error.checked_at,
        "requested_codes": error.requested_codes,
        "denied_codes": error.denied_codes,
        "provider_message": error.provider_message,
        "retryable": False,
        "retry_condition": "OpenAPI quote permissions or session rights change.",
        "next_action": "Use another available market-data tool or web source.",
        "agent_guidance": [
            "Stop repeating equivalent OpenD quote requests for the denied data.",
            "Confirm the alternative source's exact instrument, exchange listing "
            "and currency; do not assume a ticker suffix or currency.",
            "Report the alternative source, quote timestamp/timezone and "
            "whether the price is delayed or stale.",
            "Label unverified quote times or freshness as unknown; do not "
            "present those quotes as real-time.",
            "Continue independent balance and holdings reads when they succeed; "
            "their costs and valuations are not verified live market quotes.",
            "If no suitable source is available, report prices as unavailable "
            "and continue the parts of the task that do not require them.",
            "Only explicitly named symbols appear in denied_codes; a failed "
            "batch does not establish that every requested symbol is denied.",
        ],
    }
    return error_result(payload)


QUERY_ACTIONS = {
    "CURSOR_EXPIRED": "Restart without a cursor; snapshot time may change.",
    "CURSOR_SCOPE": "Use the original cursor and filters, or restart.",
    "SNAPSHOT_LIMIT": "Narrow scope and preserve coverage limits.",
    "AMBIGUOUS_ORDER": "Inspect exact account, environment and order identity.",
    "INVALID_IDENTIFIER": "Refetch exact IDs; do not round or reconstruct them.",
    "INVALID_RESPONSE": "Treat data as unavailable; do not invent fields.",
}
QUOTE_TOOLS = frozenset(
    {
        "get_stock_quote",
        "get_market_snapshot",
        "get_order_book",
        "get_historical_klines",
        "get_historical_klines_page",
        "get_option_chain",
        "get_option_expiration_date",
    }
)


def tool_error_result(
    error: ToolError,
    name: str,
    *,
    input_model: str,
    consequential: bool,
    read_only: bool,
) -> CallToolResult:
    """Use existing typed facts, giving order outcomes precedence over causes."""
    causes: list[BaseException] = []
    cause: BaseException | None = error.__cause__
    while cause is not None and cause not in causes:
        causes.append(cause)
        cause = cause.__cause__
    problem = causes[0] if causes else error
    payload: dict[str, Any] = {
        "error_code": "TOOL_EXECUTION_FAILED",
        "tool": name,
        "operation": name,
        "checked_at": utc_now_iso(),
        "message": "The tool could not complete this request.",
        "retryable": False,
        "next_action": "Use check_health; continue independent work.",
    }

    def category(code: str, message: str, action: str) -> None:
        payload.update(error_code=code, message=message, next_action=action)

    def uncertain_mutation() -> None:
        payload["mutation_outcome"] = "UNKNOWN"
        payload["next_action"] = (
            "Unlock state is uncertain. Do not repeat unlock blindly. Use "
            "check_health; use lock_trade when unlock access is no longer required."
            if name == "unlock_trade"
            else "Do not resend; inspect the original execution and broker orders."
        )

    # Nested validation/permission failures cannot override what the order
    # boundary established about dispatch or acknowledgement.
    for item in causes:
        if isinstance(item, OrderNotSentError):
            category(
                "ORDER_NOT_SENT",
                sanitize_error(item),
                "Resolve the refusal while preserving approval, policy, account, "
                "environment and existing operation identity before retrying.",
            )
            payload["mutation_outcome"] = "NOT_SENT"
            return error_result(payload)
        if isinstance(item, (OrderOutcomeUnknownError, OrderReceiptUnreadableError)):
            acknowledged = isinstance(item, OrderReceiptUnreadableError)
            category(
                "ORDER_RECEIPT_UNREADABLE" if acknowledged else "ORDER_OUTCOME_UNKNOWN",
                sanitize_error(item),
                "Do not resend. Inspect get_execution for the original operation "
                "and get_orders/get_order in the original account and environment.",
            )
            payload["mutation_outcome"] = "ACKNOWLEDGED" if acknowledged else "UNKNOWN"
            return error_result(payload)

    if isinstance(problem, ClientAuthorizationError):
        category(
            "CLIENT_PERMISSION_DENIED",
            "The authenticated client is not authorized for this operation or account.",
            "Use a client credential with the required grant; "
            "preserve the requested scope.",
        )
        return error_result(payload)
    if isinstance(problem, QuotePermissionDeniedError) and not consequential:
        return quote_permission_result(problem, name)
    if isinstance(problem, ValidationError):
        is_input = problem.title == input_model
        category(
            "INVALID_ARGUMENT" if is_input else "INVALID_RESPONSE",
            "Tool arguments do not match the schema."
            if is_input
            else "The tool returned data that does not match its response schema.",
            "Correct arguments; preserve account and environment."
            if is_input
            else "Treat the response as unavailable; do not fabricate values.",
        )
        payload["validation_issues"] = [
            {"path": list(issue["loc"]), "type": issue["type"]}
            for issue in problem.errors(
                include_input=False, include_context=False, include_url=False
            )
        ]
        if consequential and not is_input:
            uncertain_mutation()
    elif isinstance(problem, (IdentifierSerializationError, SDKResponseError)):
        category(
            "INVALID_RESPONSE",
            "The provider response is unusable.",
            "Treat affected data as unavailable; fetch exact identifiers again "
            "and never reconstruct missing fields.",
        )
        if consequential:
            uncertain_mutation()
    elif isinstance(problem, TradeQueryError):
        category(
            problem.code,
            sanitize_error(problem),
            QUERY_ACTIONS.get(
                problem.code,
                "Check scope and health; failed reads do not prove order absence.",
            ),
        )
        payload["operation"] = problem.operation
    elif isinstance(problem, ExecutionConflict):
        category(
            "EXECUTION_CONFLICT",
            "The operation conflicts with its recorded identity.",
            "Use get_execution; preserve original ID, epoch and request.",
        )
    elif isinstance(problem, ExecutionStoreError):
        category(
            "EXECUTION_JOURNAL_BLOCKED",
            "Execution journal access is blocked.",
            "Use check_health and recovery; do not create a new operation.",
        )
    elif isinstance(problem, TradingControlError):
        category(
            "TRADING_CONTROLS_BLOCKED",
            sanitize_error(problem),
            "Use get_prepared_trade; preserve approval and budget guards.",
        )
    elif isinstance(problem, TradingPolicyError):
        category(
            "TRADING_POLICY_REFUSED",
            sanitize_error(problem),
            "Use get_trading_capabilities; preserve guards and environment.",
        )
    elif consequential:
        category(
            "MUTATION_OUTCOME_UNKNOWN"
            if name == "unlock_trade"
            else "ORDER_OUTCOME_UNKNOWN",
            "The mutation failed without a verified outcome.",
            "Inspect the state before any further mutation.",
        )
        uncertain_mutation()
    elif isinstance(problem, ProviderRateLimitError):
        category(
            "RATE_LIMITED",
            "The provider request budget is temporarily exhausted.",
            "Wait retry_after_seconds before a bounded retry.",
        )
        payload.update(
            retryable=read_only or name in QUOTE_TOOLS,
            retry_after_seconds=problem.retry_after_seconds,
            provider=problem.provider,
            operation=problem.operation,
        )
        if not payload["retryable"]:
            payload["next_action"] = (
                "Wait retry_after_seconds; review the state-changing request "
                "and its authorization before any retry."
            )
    elif isinstance(problem, ConnectionError) or str(problem) in {
        "Quote context not connected",
        "Trade context not connected",
    }:
        category(
            "CONNECTION_UNAVAILABLE",
            "The gateway connection is unavailable.",
            "Use check_health; retry reads after connectivity recovers.",
        )
    elif isinstance(problem, CursorError):
        category(
            "INVALID_CURSOR",
            "The cursor is invalid for this query.",
            "Use the cursor unchanged with its original query, or restart without it.",
        )
    elif isinstance(problem, ValueError):
        if str(problem).startswith(
            "Deal and fee queries are supported only for REAL accounts"
        ):
            category(
                "UNSUPPORTED_OPERATION",
                "This query is unavailable in the selected environment.",
                "Use order queries for SIMULATE; do not switch to REAL.",
            )
        else:
            category(
                "INVALID_ARGUMENT",
                "The tool rejected an argument or selected scope.",
                "Check schema; use get_accounts in the same environment.",
            )
            # Validation of sensitive tools must not echo passwords or hashes.
            if name != "unlock_trade":
                payload["detail"] = sanitize_error(problem)
    elif name in QUOTE_TOOLS:
        lowered = str(problem).casefold()
        if "subscription quota" in lowered and any(
            word in lowered for word in ("exceeded", "insufficient", "not enough")
        ):
            category(
                "SUBSCRIPTION_CAPACITY_EXCEEDED",
                "Market-data subscription capacity is exhausted.",
                "Use get_subscriptions or another source; no global unsubscribe.",
            )
        elif "unknown stock" in lowered:
            category(
                "SYMBOL_UNAVAILABLE",
                "This provider could not resolve the requested instrument.",
                "Verify exact listing and provider code, or use another source.",
            )
    if isinstance(problem, RuntimeError) and name != "unlock_trade":
        payload["detail"] = sanitize_error(problem)
    return error_result(payload)


class AgentFriendlyMCP(FastMCP):
    """Normalize HTTP/stdio errors while retaining Python call semantics."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        # FastMCP parses JSON-string arguments before Pydantic validation.
        # Retain that input behavior and the SDK's success conversion.
        self._mcp_server.call_tool(validate_input=False)(self._call_tool_with_errors)

    async def list_tools(self) -> list[MCPTool]:
        return [tool for tool in await super().list_tools() if tool_visible(tool.name)]

    async def _call_tool_with_errors(self, name: str, arguments: dict[str, Any]) -> Any:
        tool = self._tool_manager.get_tool(name)
        if tool is None:
            return error_result(
                {
                    "error_code": "UNKNOWN_TOOL",
                    "tool": name,
                    "operation": name,
                    "checked_at": utc_now_iso(),
                    "message": "This tool is not available.",
                    "retryable": False,
                    "next_action": "Inspect tools/list and choose an available tool.",
                }
            )
        try:
            environment_parameter = inspect.signature(tool.fn).parameters.get("trd_env")
            default_environment = (
                environment_parameter.default
                if environment_parameter is not None
                and isinstance(environment_parameter.default, str)
                else None
            )
            authorize_tool(name, arguments, default_environment=default_environment)
            return await super().call_tool(name, arguments)
        except ClientAuthorizationError:
            return error_result(
                {
                    "error_code": "CLIENT_PERMISSION_DENIED",
                    "tool": name,
                    "operation": name,
                    "checked_at": utc_now_iso(),
                    "message": (
                        "The authenticated client is not authorized "
                        "for this operation or account."
                    ),
                    "retryable": False,
                    "next_action": (
                        "Use a client credential with the required grant; "
                        "preserve the requested scope."
                    ),
                }
            )
        except ToolError as error:
            return tool_error_result(
                error,
                name,
                input_model=tool.fn_metadata.arg_model.__name__,
                consequential=bool(
                    tool.annotations and tool.annotations.destructiveHint
                ),
                read_only=bool(tool.annotations and tool.annotations.readOnlyHint),
            )
