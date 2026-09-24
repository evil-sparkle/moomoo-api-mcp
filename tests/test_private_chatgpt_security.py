"""Security boundaries that the optional tunnel must preserve."""

from unittest.mock import patch

import pytest
from starlette.testclient import TestClient

import moomoo_mcp.server as server

REQUEST = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
TOKEN = "ordinary-read-only-fixture-token"


@pytest.fixture(autouse=True)
def reset_session_manager():
    server.mcp._session_manager = None
    yield
    server.mcp._session_manager = None


@pytest.mark.parametrize(
    "authorization",
    [None, "Bearer wrong", "Bearer connector-forwarded-override"],
)
def test_rejected_credentials_never_construct_broker_services(authorization) -> None:
    headers = {"Accept": "application/json"}
    if authorization is not None:
        headers["Authorization"] = authorization
    app = server.create_streamable_http_app(auth_token=TOKEN)
    with (
        patch.object(server, "get_services") as get_services,
        TestClient(app) as client,
    ):
        response = client.post("/mcp", json=REQUEST, headers=headers)

    assert response.status_code == 401
    get_services.assert_not_called()


def test_valid_ordinary_credential_returns_a_real_mcp_tool_list() -> None:
    app = server.create_streamable_http_app(auth_token=TOKEN)
    with patch.object(server, "get_services"), TestClient(app) as client:
        response = client.post(
            "/mcp",
            json=REQUEST,
            headers={
                "Authorization": f"Bearer {TOKEN}",
                "Accept": "application/json",
                "Host": "127.0.0.1:8000",
            },
        )

    assert response.status_code == 200
    assert "check_health" in {
        tool["name"] for tool in response.json()["result"]["tools"]
    }


def test_unexpected_origin_fails_closed_before_any_broker_operation() -> None:
    app = server.create_streamable_http_app(auth_token=TOKEN)
    with patch.object(server, "get_services"), TestClient(app) as client:
        response = client.post(
            "/mcp",
            json=REQUEST,
            headers={
                "Authorization": f"Bearer {TOKEN}",
                "Accept": "application/json",
                "Host": "127.0.0.1:8000",
                "Origin": "https://unexpected.example",
            },
        )

    assert response.status_code == 403


def test_unexpected_forwarded_host_fails_closed() -> None:
    app = server.create_streamable_http_app(auth_token=TOKEN)
    with patch.object(server, "get_services"), TestClient(app) as client:
        response = client.post(
            "/mcp",
            json=REQUEST,
            headers={
                "Authorization": f"Bearer {TOKEN}",
                "Accept": "application/json",
                "Host": "public.example",
            },
        )

    assert response.status_code == 421
