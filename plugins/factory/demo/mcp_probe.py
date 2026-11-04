"""Start ``sdlc-graph mcp`` on stdio the way Claude Code does, then act as the agent: list the tools,
ask for the blast radius of a file, ask what the contract needs next, and try a write an agent
must not make. Prints a short report; exits non-zero if any expectation fails.

Usage: python demo/mcp_probe.py <path to sdlc-graph> <repo name> <contract slug> [<file for graph_impact>]
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def _payload(result) -> dict:
    if result.structuredContent is not None:
        return result.structuredContent
    return json.loads(result.content[0].text)


async def probe(cli: str, repo: str, slug: str, path: str = "app/auth.py") -> int:
    params = StdioServerParameters(command=cli, args=["mcp"], env=dict(os.environ))
    failures: list[str] = []
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            tools = (await session.list_tools()).tools
            names = [t.name for t in tools]
            read_only = [t.name for t in tools if t.annotations and t.annotations.readOnlyHint]
            print(f"server {init.serverInfo.name}: {len(names)} tools ({len(read_only)} read-only)")
            print("  " + ", ".join(names))
            if len(names) != 23:
                failures.append(f"expected 23 tools, got {len(names)}")

            impact = _payload(await session.call_tool("graph_impact", {"paths": [path], "repo": repo}))
            if impact.get("ok") is False:
                failures.append(f"graph_impact failed: {impact}")
            else:
                print(f"graph_impact {path}: {len(impact['tests'])} tests, routes {impact['routes']}")

            nxt = _payload(await session.call_tool("workflow_next", {"slug": slug}))
            print(f"workflow_next {slug}: state={nxt.get('state')} done={nxt.get('done')}")

            refused = _payload(await session.call_tool(
                "contract_transition", {"slug": slug, "to_state": "approved", "actor": "@ish"}))
            print(f"contract_transition -> approved, forging actor=@ish (the server acts as its own agent identity): ok={refused.get('ok')} "
                  f"{json.dumps(refused.get('error') or refused.get('missing'))}")
            if refused.get("ok") is not False:
                failures.append("an agent was allowed into approved")
    for f in failures:
        print(f"FAIL: {f}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    if len(sys.argv) not in (4, 5):
        sys.exit(__doc__)
    sys.exit(asyncio.run(probe(*sys.argv[1:])))
