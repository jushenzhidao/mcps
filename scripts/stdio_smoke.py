"""以真实 MCP stdio 协议握手，验证工具清单与一次假上游调用。零真实计费。

用法：
    .venv/bin/python scripts/stdio_smoke.py
退出码 0 表示通过。
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from mcp import ClientSession, StdioServerParameters  # noqa: E402
from mcp.client.stdio import stdio_client  # noqa: E402


async def main() -> int:
    env = dict(os.environ)
    env["JIEAPI_KEY"] = env.get("JIEAPI_KEY") or "sk-smoke-placeholder"
    params = StdioServerParameters(
        command=str(ROOT / ".venv/bin/python"),
        args=["-m", "jieapi_mcp"],
        env={**env, "PYTHONPATH": str(ROOT / "src")},
        cwd=str(ROOT),
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            names = sorted(t.name for t in tools.tools)
            print("工具清单：", names)
            assert {"generate_image", "create_video", "list_models", "reload_config"} <= set(names), names

            # 只读工具：不触发计费
            res = await session.call_tool("list_models", {"capability": "image"})
            text = "".join(c.text for c in res.content if getattr(c, "type", "") == "text")
            print("list_models(image) 前 200 字：", text[:200])
            assert "flux-3-image" in text, text
    print("STDIO-SMOKE OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
