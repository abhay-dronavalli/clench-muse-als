import asyncio
import json
import os
import sys

import httpx
import pytest

from core.mcp_server import CoreConnection, PartnerClient, build_server


def test_stdio_protocol_handshake_and_tool_call(tmp_path):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    config = tmp_path / "cores.json"
    config.write_text(json.dumps([{"id": "demo", "url": "http://127.0.0.1:8001",
                                  "token_env": "TEST_PARTNER_TOKEN"}]))
    async def run():
        params = StdioServerParameters(command=sys.executable,
            args=["-m", "core.mcp_server", "--config", str(config)],
            env={**os.environ, "TEST_PARTNER_TOKEN": "test-token"})
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = await session.list_tools()
                assert len(tools.tools) == 4
                result = await session.call_tool("list_cores", {})
                assert not result.isError
                assert "demo" in str(result.content)
                result = await session.call_tool("get_device_status", {"core_id": "unknown"})
                assert result.isError
    asyncio.run(run())


def make_client(monkeypatch, handler):
    monkeypatch.setenv("TEST_PARTNER_TOKEN", "private-token")
    return PartnerClient([CoreConnection(id="demo", url="http://127.0.0.1:8001", token_env="TEST_PARTNER_TOKEN")],
                         transport=httpx.MockTransport(handler))


@pytest.mark.parametrize("url", ["http://remote.example", "https://example.com/path",
    "https://user:pass@example.com", "https://example.com?token=x", "file:///tmp/test"])
def test_invalid_origins(url):
    with pytest.raises(ValueError):
        CoreConnection(id="demo", url=url, token_env="TEST_TOKEN")


def test_no_missing_credentials_or_duplicate_ids(monkeypatch):
    connection = CoreConnection(id="demo", url="http://127.0.0.1:8001", token_env="TEST_TOKEN")
    monkeypatch.delenv("TEST_TOKEN", raising=False)
    with pytest.raises(ValueError, match="Missing token"):
        PartnerClient([connection])
    with pytest.raises(ValueError, match="unique"):
        PartnerClient([connection, connection])


def test_tools_expose_only_scoped_operations(monkeypatch):
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"lang": "en"})
    server = build_server(make_client(monkeypatch, handler))
    async def run():
        tools = await server.list_tools()
        assert {t.name for t in tools} == {"list_cores", "get_device_status", "get_board_settings", "propose_board_update"}
        await server.call_tool("get_board_settings", {"core_id": "demo"})
        await server.call_tool("propose_board_update", {"core_id": "demo", "lang": "es"})
    asyncio.run(run())
    assert [r.url.path for r in requests] == ["/api/partner/board", "/api/partner/proposals"]
    assert requests[0].headers["Authorization"] == "Bearer private-token"
    assert requests[1].method == "POST"


@pytest.mark.parametrize("kind,expected", [("timeout", "timeout"), ("connect", "unreachable"),
    ("auth", "http_error"), ("redirect", "http_error"), ("json", "invalid_response")])
def test_failures_are_sanitized(monkeypatch, kind, expected):
    def handler(request):
        if kind == "timeout":
            raise httpx.ReadTimeout("private-token", request=request)
        if kind == "connect":
            raise httpx.ConnectError("private-token", request=request)
        if kind == "auth":
            return httpx.Response(401, text="private-token")
        if kind == "redirect":
            return httpx.Response(302, headers={"Location": "https://elsewhere.example"})
        return httpx.Response(200, text="private-token")
    client = make_client(monkeypatch, handler)
    result = asyncio.run(client.request("demo", "GET", "status"))
    assert result["error"] == expected
    assert "private-token" not in str(result)
    with pytest.raises(ValueError, match="Unknown core"):
        asyncio.run(client.request("unconfigured", "GET", "status"))
