"""Local stdio MCP adapter for explicitly configured Clench cores.

Run with ``uv run --extra mcp python -m core.mcp_server --config path.json``.
Never give this process the caregiver approval credential.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator


class CoreConnection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    url: str
    token_env: str = Field(pattern=r"^[A-Z][A-Z0-9_]*$")

    @model_validator(mode="after")
    def validate_origin(self):
        url = urlsplit(self.url)
        if (not url.hostname or url.username or url.password or url.query or url.fragment
                or url.path not in ("", "/") or url.scheme not in ("http", "https")):
            raise ValueError("Use an HTTP(S) origin without credentials, path, query or fragment")
        if url.scheme == "http" and url.hostname not in ("127.0.0.1", "localhost", "::1"):
            raise ValueError("Non-loopback connections require HTTPS")
        return self


class PartnerClient:
    def __init__(self, connections: list[CoreConnection], *, transport=None) -> None:
        if not connections or len({c.id for c in connections}) != len(connections):
            raise ValueError("Configure at least one core with unique ids")
        self.connections = {c.id: c for c in connections}
        self.tokens: dict[str, SecretStr] = {}
        self.transport = transport
        for connection in connections:
            token = os.environ.get(connection.token_env, "")
            if not token:
                raise ValueError(f"Missing token environment variable: {connection.token_env}")
            self.tokens[connection.id] = SecretStr(token)

    async def request(self, core_id: str, method: str, path: str, body=None) -> dict:
        connection = self.connections.get(core_id)
        if connection is None:
            raise ValueError("Unknown core id; use list_cores")
        async with httpx.AsyncClient(
            timeout=5, follow_redirects=False, trust_env=False, transport=self.transport,
        ) as client:
            try:
                response = await client.request(
                    method, connection.url.rstrip("/") + "/api/partner/" + path,
                    headers={"Authorization": "Bearer " + self.tokens[core_id].get_secret_value()},
                    json=body,
                )
                response.raise_for_status()
            except httpx.TimeoutException:
                return {"core_id": core_id, "ok": False, "error": "timeout"}
            except httpx.HTTPStatusError:
                return {"core_id": core_id, "ok": False, "error": "http_error", "status": response.status_code}
            except httpx.RequestError:
                return {"core_id": core_id, "ok": False, "error": "unreachable"}
            try:
                data = response.json()
            except ValueError:
                return {"core_id": core_id, "ok": False, "error": "invalid_response"}
            if not isinstance(data, dict):
                return {"core_id": core_id, "ok": False, "error": "invalid_response"}
            return {"core_id": core_id, "ok": True, "data": data}


def build_server(client: PartnerClient):
    # Optional dependency: ordinary Core startup never imports the MCP SDK.
    from mcp.server.fastmcp import FastMCP
    from mcp.types import ToolAnnotations

    server = FastMCP("Clench partner", instructions=(
        "Each configured core is one shared patient session, not a device fleet. "
        "Proposals require separate caregiver review. Never claim a proposal is applied. "
        "No speech, messages, calls, gestures or emergency controls are exposed."
    ))
    read = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False)

    @server.tool(annotations=read)
    def list_cores() -> list[str]:
        """List the core identifiers this local integration is allowed to access."""
        return list(client.connections)

    @server.tool(annotations=read)
    async def get_device_status(core_id: str) -> dict:
        """Check core availability and board connection count; not physical tablet identity."""
        return await client.request(core_id, "GET", "status")

    @server.tool(annotations=read)
    async def get_board_settings(core_id: str) -> dict:
        """Read current language, pointing mode and scan interval, without patient history."""
        return await client.request(core_id, "GET", "board")

    @server.tool(annotations=ToolAnnotations(
        readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False,
    ))
    async def propose_board_update(core_id: str, lang: Literal["en", "es"]) -> dict:
        """Prepare an English/Spanish language switch for caregiver review; does not apply it.

        Uses existing bilingual tiles. Custom tile editing is not supported.
        """
        return await client.request(core_id, "POST", "proposals", {"lang": lang})

    return server


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    args = parser.parse_args()
    connections = [CoreConnection.model_validate(c) for c in json.loads(args.config.read_text(encoding="utf-8"))]
    build_server(PartnerClient(connections)).run(transport="stdio")


if __name__ == "__main__":
    main()
