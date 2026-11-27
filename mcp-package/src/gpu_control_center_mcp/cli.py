"""Stdio-to-Streamable-HTTP MCP bridge for GPU Control Center.

Allows any stdio-only MCP host (Claude Desktop, Cursor, Windsurf, Cline, LangChain, etc.)
to connect securely to a local or remote GPU Control Center MCP server using a Bearer token,
inheriting all server-side tools, resources, prompts, RBAC permissions, and audit attribution.
"""
import argparse
import asyncio
import base64
import os
import sys

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from mcp.server.lowlevel import Server
from mcp.server.lowlevel.helper_types import ReadResourceContents
from mcp.server.stdio import stdio_server
import mcp.types as types


def build_bridge_server(session: ClientSession, instructions: str | None = None) -> Server:
    server = Server('GPU Control Center', instructions=instructions)

    @server.list_tools()
    async def handle_list_tools() -> list[types.Tool]:
        result = await session.list_tools()
        return list(result.tools)

    @server.call_tool(validate_input=False)
    async def handle_call_tool(name: str, arguments: dict) -> types.CallToolResult:
        return await session.call_tool(name, arguments or {})

    @server.list_resources()
    async def handle_list_resources() -> list[types.Resource]:
        result = await session.list_resources()
        return list(result.resources)

    @server.read_resource()
    async def handle_read_resource(uri) -> list[ReadResourceContents]:
        result = await session.read_resource(uri)
        items: list[ReadResourceContents] = []
        for entry in result.contents:
            if hasattr(entry, 'text') and entry.text is not None:
                items.append(ReadResourceContents(content=entry.text, mime_type=entry.mimeType))
            elif hasattr(entry, 'blob') and entry.blob is not None:
                items.append(ReadResourceContents(content=base64.b64decode(entry.blob), mime_type=entry.mimeType))
        return items

    @server.list_prompts()
    async def handle_list_prompts() -> list[types.Prompt]:
        result = await session.list_prompts()
        return list(result.prompts)

    @server.get_prompt()
    async def handle_get_prompt(name: str, arguments: dict[str, str] | None) -> types.GetPromptResult:
        return await session.get_prompt(name, arguments)

    return server


async def run_bridge(url: str, token: str = '', timeout: float = 30.0, sse_read_timeout: float = 660.0) -> None:
    headers: dict[str, str] = {}
    if token.strip():
        headers['Authorization'] = f'Bearer {token.strip()}'

    async with streamablehttp_client(
        url,
        headers=headers or None,
        timeout=timeout,
        sse_read_timeout=sse_read_timeout,
    ) as (read, write, _):
        async with ClientSession(read, write) as remote_session:
            init_result = await remote_session.initialize()
            server = build_bridge_server(remote_session, instructions=init_result.instructions)
            async with stdio_server() as (stdio_read, stdio_write):
                await server.run(
                    stdio_read,
                    stdio_write,
                    server.create_initialization_options(),
                )


def main() -> None:
    parser = argparse.ArgumentParser(
        prog='gpu-control-center-mcp',
        description='Bridge stdio MCP clients to a GPU Control Center Streamable HTTP MCP server.',
    )
    parser.add_argument(
        '--url',
        default=os.getenv('GPU_CONTROL_CENTER_MCP_URL', 'http://localhost:8090/mcp'),
        help='Remote GPU Control Center MCP endpoint URL (env: GPU_CONTROL_CENTER_MCP_URL)',
    )
    parser.add_argument(
        '--token',
        default=os.getenv('GPU_CONTROL_CENTER_MCP_TOKEN', ''),
        help='Bearer token issued by GPU Control Center (env: GPU_CONTROL_CENTER_MCP_TOKEN)',
    )
    parser.add_argument(
        '--timeout',
        type=float,
        default=30.0,
        help='HTTP request timeout in seconds (default: 30)',
    )
    parser.add_argument(
        '--read-timeout',
        type=float,
        default=660.0,
        help='Long-running tool read timeout in seconds (default: 660)',
    )
    args = parser.parse_args()
    try:
        asyncio.run(run_bridge(args.url, token=args.token, timeout=args.timeout, sse_read_timeout=args.read_timeout))
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == '__main__':
    main()
