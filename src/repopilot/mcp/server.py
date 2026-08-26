from __future__ import annotations

import mcp.server.stdio
import mcp.types as types
from mcp.server.lowlevel import NotificationOptions, Server
from mcp.server.models import InitializationOptions

from repopilot.mcp.adapter import McpToolAdapter


def build_mcp_server(adapter: McpToolAdapter) -> Server:
    server = Server(
        "repopilot",
        version="0.1.0",
        instructions="Local, run-scoped access to six sandboxed repository tools.",
    )

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        return adapter.list_tools()

    @server.call_tool(validate_input=False)
    async def call_tool(name: str, arguments: dict[str, object]) -> types.CallToolResult:
        return await adapter.call_tool(name, arguments)

    return server


async def serve_stdio(adapter: McpToolAdapter) -> None:
    server = build_mcp_server(adapter)
    options = InitializationOptions(
        server_name="repopilot",
        server_version="0.1.0",
        capabilities=server.get_capabilities(
            notification_options=NotificationOptions(),
            experimental_capabilities={},
        ),
        instructions="Local stdio only; all calls retain RepoPilot sandbox policy.",
    )
    async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, options)
