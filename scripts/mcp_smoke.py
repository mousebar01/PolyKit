"""Smoke-test PolyKit's MCP stdio transport without starting an Agent host."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


ROOT = Path(__file__).resolve().parents[1]


async def main() -> None:
    server = StdioServerParameters(
        command=sys.executable,
        args=[str(ROOT / "api" / "mcp_server.py")],
        cwd=str(ROOT),
    )
    async with stdio_client(server) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.list_tools()

    names = sorted(tool.name for tool in result.tools)
    if not names:
        raise SystemExit("MCP server initialized but exposed no tools")

    print(f"PolyKit MCP OK: {len(names)} tools")
    for name in names:
        print(f"- {name}")


if __name__ == "__main__":
    asyncio.run(main())
