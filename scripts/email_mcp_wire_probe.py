"""Wire-protocol probe for email-mcp: spawn the stdio server, list tools, call a read tool.

Proves the FastMCP stdio wire end-to-end (init -> list_tools -> call_tool), not just the
backend. Run with secrets injected so email_list returns real data:

    bws run --project-id <id> --shell bash -- \
      "uv run --extra server python /path/to/email_mcp_wire_probe.py"
"""

from __future__ import annotations

import asyncio
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main() -> int:
    # StdioServerParameters does NOT inherit os.environ by default (minimal safe env), so pass
    # it explicitly — this is how a captain dispatching the MCP must forward EMAIL_* creds.
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "email_mcp.server"], env=dict(os.environ)
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools = await session.list_tools()
            names = sorted(t.name for t in tools.tools)
            print(f"tools advertised: {names}")
            expected = {"email_list", "email_read", "email_send", "email_archive"}
            if set(names) != expected:
                print(f"FAIL: expected {sorted(expected)}")
                return 1

            result = await session.call_tool("email_list", {"limit": 3})
            payload = result.structuredContent or {}
            items = payload.get("result", []) if isinstance(payload, dict) else []
            print(f"email_list returned {len(items)} message(s) over the wire")
            for m in items[:3]:
                print(f"  - {m.get('subject', '(no subject)')}")
            print("WIRE OK" if items else "WIRE OK (tools callable; 0 messages / unconfigured)")
            return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
