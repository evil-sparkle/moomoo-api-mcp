"""Actionable tool failures at the MCP boundary, with success schemas intact."""

import inspect
import json
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.server.fastmcp.utilities.func_metadata import FuncMetadata
from mcp.types import CallToolResult, TextContent
from mcp.types import Tool as MCPTool
from pydantic import ValidationError

from moomoo_mcp.client_authorization import (
    ClientAuthorizationError,
    authorize_tool,
    tool_visible,
)
from moomoo_mcp.services.broker_dispatch import BrokerReadTimeoutError
from moomoo_mcp.services.clock import utc_now_iso
from moomoo_mcp.services.execution_identity import IdentityTokenValidationError
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
from moomoo_mcp.tools.trading_contract import published_input_schema

AGENT_INSTRUCTIONS = (
    "Tool errors provide error_code, retryable and next_action. Follow that "
    "guidance and continue independent work rather than repeating unchanged "
    "failed requests. Respect reported rate-limit retry_after_seconds; a wait "
    "does not authorize another mutation. Obtain explicit trading authorization "
    "for the exact account, REAL/SIMULATE environment, instrument, side, size, "
    "price, order type, validity, session and submission timing. Inspect actual "
    "served tools/list; descriptions cached by a client may be stale. Before "
    "preparation or submission, generate uuid.uuid4().hex once per authorized "
    "intent and durably persist the exact request, account, environment, "
    "operation_id and admission_epoch. Both tokens must be nonblank printable "
    "strings of 1–64 characters. Do not retry writes automatically. An input "
    "rejection with no record "
    "created in this invocation does not prove no earlier attempt exists. A "
    "technical correction is permitted only within existing user authorization "
    "and with evidence that the invalid request was never admitted or sent in "
    "any attempt. Respect literal limits on tool invocations, including "
    "after an unsent validation failure. Do not change economic terms, account, "
    "environment or timing; an approval for an exact operation ID does not "
    "transfer to a "
    "corrected ID. Prepare and obtain approval again for changed approved "
    "parameters. If prior identity or dispatch is uncertain, recover the "
    "original persisted ID and epoch and inspect get_execution with the exact "
    "trd_env and scoped broker orders. Never allocate a replacement "
    "ID, refresh an epoch, or resend to bypass uncertainty. Preserve the user's "
    "account and REAL/SIMULATE environment; never switch them to bypass a "
    "failure. session=RTH controls execution eligibility; it does not enforce "
    "submission during regular market hours. For an explicit submit-only-in-RTH "
    "condition, verify current instrument market state and the market-local "
    "calendar immediately before submission, including holidays and half days; "
    "if timing is closed or uncertain, defer without submitting. The server "
    "does not schedule a future submission. Report acknowledgement and observed "
    "broker status separately; never promise a fill, pending status, DAY "
    "rollover or automatic future submission. Failed order lookups do not prove "
    "an order is absent. For uncertain or unreadable order outcomes, inspect "
    "the original execution and broker orders before any further mutation. "
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


def _execution_evidence(
    payload: dict[str, Any], phase: str, journal_record_created: bool | None = None
) -> None:
    payload.update(
        validation_phase=phase,
        journal_record_created=journal_record_created,
        journal_record_scope="THIS_INVOCATION",
        identity_status="UNKNOWN",
        prior_attempt_outcome="UNKNOWN",
    )


def _validation_issues(error: ValidationError) -> list[dict[str, Any]]:
    """Read typed Pydantic facts internally; return no supplied values or messages."""
    issues = []
    constraints = {
        "missing": "required",
        "string_type": "strict_string",
        "string_too_short": "min_length",
        "string_too_long": "max_length",
    }
    for issue in error.errors(
        include_input=True, include_context=True, include_url=False
    ):
        path = list(issue["loc"])
        diagnostic: dict[str, Any] = {"path": path, "type": issue["type"]}
        typed_error = issue.get("ctx", {}).get("error")
        if isinstance(typed_error, IdentityTokenValidationError):
            diagnostic.update(typed_error.metadata())
        elif path and path[-1] in {"operation_id", "admission_epoch"}:
            constraint = constraints.get(issue["type"])
            if constraint is not None:
                value = issue.get("input")
                diagnostic.update(
                    IdentityTokenValidationError(
                        str(path[-1]),
                        constraint,
                        len(value) if isinstance(value, str) else None,
                    ).metadata()
                )
        issues.append(diagnostic)
    return issues


def _input_validation_evidence(
    payload: dict[str, Any],
    issues: list[dict[str, Any]],
    *,
    consequential: bool = False,
) -> None:
    _execution_evidence(payload, "INPUT_VALIDATION", False)
    payload.update(mutation_outcome="NOT_SENT", validation_issues=issues)
    fields = list(dict.fromkeys(issue["field"] for issue in issues if "field" in issue))
    if fields:
        payload["next_action"] = (
            "For a confirmed first attempt, correct "
            + ", ".join(fields)
            + " to 1–64 printable nonblank characters, preserving exact intent, "
            "account and environment; prepare and approve the corrected request "
            "where required. Invoke again only if valid authorization permits "
            "another invocation: literal submit-once authorization is not "
            "reusable, and approval bound to an exact operation ID cannot "
            "transfer to a replacement. If any prior attempt or delivery "
            "uncertainty exists, "
            "recover its original operation ID and epoch and inspect get_execution "
            "and broker orders; do not replace or refresh that identity. "
            "This invocation created no journal record; prior identity status "
            "remains unknown. No request was resent automatically."
        )
    elif consequential:
        payload["next_action"] = (
            "For a confirmed first attempt, correct the schema inputs while "
            "preserving intent, account and environment. Invoke again only if "
            "valid authorization permits another invocation; literal submit-once "
            "authorization is not reusable and approval bound to an exact "
            "operation ID cannot transfer. For any prior attempt or delivery "
            "uncertainty, inspect the original execution and state first; retain "
            "its original identity. No request was resent automatically."
        )
    payload["retry_condition"] = (
        "Do not repeat the unchanged request. Correct the identified input only "
        "after establishing whether this was a first attempt; preserve any "
        "previously attempted operation's identity and resolve its outcome first."
    )


def tool_error_result(
    error: ToolError,
    name: str,
    *,
    input_model: str,
    consequential: bool,
    read_only: bool,
    input_validation: bool = False,
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
        _execution_evidence(payload, "UNKNOWN")
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
        if isinstance(item, (OrderOutcomeUnknownError, OrderReceiptUnreadableError)):
            acknowledged = isinstance(item, OrderReceiptUnreadableError)
            category(
                "ORDER_RECEIPT_UNREADABLE" if acknowledged else "ORDER_OUTCOME_UNKNOWN",
                sanitize_error(item),
                "Do not resend. Inspect get_execution for the original operation "
                "and get_orders/get_order in the original account and environment.",
            )
            _execution_evidence(
                payload, "ACKNOWLEDGED" if acknowledged else "DISPATCHED"
            )
            payload["mutation_outcome"] = "ACKNOWLEDGED" if acknowledged else "UNKNOWN"
            return error_result(payload)

    for item in causes:
        if isinstance(item, OrderNotSentError):
            category(
                "ORDER_NOT_SENT",
                sanitize_error(item),
                "This invocation sent no mutation. Inspect get_execution for any "
                "original operation and resolve its recorded state before further "
                "mutation. An admitted refusal retains immutable identity; do not "
                "replace its operation ID or refresh its epoch. Preserve approval, "
                "policy, account and environment. No request was resent automatically.",
            )
            _execution_evidence(
                payload,
                "ADMITTED" if item.admitted else "PRE_DISPATCH",
                True if item.admitted else None,
            )
            payload["mutation_outcome"] = "NOT_SENT"
            payload["retry_condition"] = (
                "Do not repeat the unchanged refusal or infer that a replacement "
                "operation is allowed. Resolve the original execution and refusal "
                "under the same policy and approval requirements."
            )
            token_error = next(
                (
                    cause
                    for cause in causes
                    if isinstance(cause, IdentityTokenValidationError)
                ),
                None,
            )
            if token_error is not None and not item.admitted:
                _input_validation_evidence(
                    payload,
                    [
                        {
                            "path": [token_error.field],
                            "type": "identity_token",
                            **token_error.metadata(),
                        }
                    ],
                )
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
        is_input = input_validation and problem.title == input_model
        category(
            "INVALID_ARGUMENT" if is_input else "INVALID_RESPONSE",
            "Tool arguments do not match the schema."
            if is_input
            else "The tool returned data that does not match its response schema.",
            "Correct arguments; preserve account and environment."
            if is_input
            else "Treat the response as unavailable; do not fabricate values.",
        )
        payload["validation_issues"] = _validation_issues(problem)
        if is_input:
            _input_validation_evidence(
                payload, payload["validation_issues"], consequential=consequential
            )
        if consequential and not is_input:
            uncertain_mutation()
    elif isinstance(problem, IdentityTokenValidationError) and not consequential:
        category(
            "INVALID_ARGUMENT",
            "An execution identity token violates its input contract.",
            "Correct the identified input while preserving account and environment.",
        )
        _input_validation_evidence(
            payload,
            [{"path": [problem.field], "type": "identity_token", **problem.metadata()}],
        )
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
        _execution_evidence(payload, "UNKNOWN")
        if consequential:
            payload["mutation_outcome"] = "UNKNOWN"
    elif isinstance(problem, ExecutionStoreError):
        category(
            "EXECUTION_JOURNAL_BLOCKED",
            "Execution journal access is blocked.",
            "Use check_health and recovery; do not create a new operation.",
        )
        _execution_evidence(payload, "UNKNOWN")
        if consequential:
            payload["mutation_outcome"] = "UNKNOWN"
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
    elif isinstance(problem, BrokerReadTimeoutError):
        category(
            "READ_DEADLINE_EXCEEDED",
            str(problem),
            "Treat this read as unavailable. Check health; late SDK results are "
            "discarded. Do not assume that OpenD aborted the blocking call.",
        )
        payload.update(
            operation=problem.operation,
            phase=problem.phase,
            timing=problem.timing,
            underlying_sdk_cancelled=False,
        )
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


class _OpaqueIdentityMetadata(FuncMetadata):
    """Keep optional identity strings opaque through the SDK's JSON convenience."""

    def pre_parse_json(self, data: dict[str, Any]) -> dict[str, Any]:
        parsed = super().pre_parse_json(data)
        for field in ("operation_id", "admission_epoch"):
            if field in self.arg_model.model_fields and isinstance(
                data.get(field), str
            ):
                parsed[field] = data[field]
        return parsed


class AgentFriendlyMCP(FastMCP):
    """Normalize HTTP/stdio errors while retaining Python call semantics."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        # FastMCP parses JSON-string arguments before Pydantic validation.
        # Retain that input behavior and the SDK's success conversion.
        self._mcp_server.call_tool(validate_input=False)(self._call_tool_with_errors)

    def add_tool(self, fn: Any, *args: Any, **kwargs: Any) -> None:
        super().add_tool(fn, *args, **kwargs)
        name = kwargs.get("name") or (args[0] if args else None) or fn.__name__
        tool = self._tool_manager.get_tool(name)
        if tool is not None and any(
            field in tool.fn_metadata.arg_model.model_fields
            for field in ("operation_id", "admission_epoch")
        ):
            metadata = tool.fn_metadata
            tool.fn_metadata = _OpaqueIdentityMetadata(
                arg_model=metadata.arg_model,
                output_schema=metadata.output_schema,
                output_model=metadata.output_model,
                wrap_output=metadata.wrap_output,
            )

    async def list_tools(self) -> list[MCPTool]:
        return [
            tool.model_copy(
                update={
                    "inputSchema": published_input_schema(tool.name, tool.inputSchema)
                }
            )
            for tool in await super().list_tools()
            if tool_visible(tool.name)
        ]

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
            # Establish input phase here, before any tool/service code runs.
            # A later ValidationError can use this same model/title after a
            # mutation; its class or title alone cannot prove non-dispatch.
            try:
                parsed = tool.fn_metadata.pre_parse_json(arguments)
                tool.fn_metadata.arg_model.model_validate(parsed)
            except ValidationError as cause:
                error = ToolError("Tool arguments do not match the schema.")
                error.__cause__ = cause
                return tool_error_result(
                    error,
                    name,
                    input_model=tool.fn_metadata.arg_model.__name__,
                    consequential=bool(
                        tool.annotations and tool.annotations.destructiveHint
                    ),
                    read_only=bool(tool.annotations and tool.annotations.readOnlyHint),
                    input_validation=True,
                )
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
