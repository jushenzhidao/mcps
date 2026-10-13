"""Streamable HTTP 端到端冒烟：真实 MCP 协议握手 + 工具调用。

用法：
    MCP_URL=http://127.0.0.1:8802/mcp MCP_AUTH_TOKEN=xxx \
        .venv/bin/python scripts/http_smoke.py

注意：仅调用只读的 list_models（不触发计费）。
"""

from __future__ import annotations

import json
import os
import sys

import httpx

URL = os.environ.get("MCP_URL", "http://127.0.0.1:8802/mcp")
TOKEN = os.environ.get("MCP_AUTH_TOKEN", "")
HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
    "Authorization": f"Bearer {TOKEN}",
}


def parse_sse(text: str) -> dict | None:
    for line in text.splitlines():
        if line.startswith("data:"):
            return json.loads(line[5:].strip())
    return None


def main() -> int:
    with httpx.Client(timeout=30, trust_env=False) as client:
        # 1. 无令牌必须被拒
        r = client.post(URL, json={"jsonrpc": "2.0", "id": 0, "method": "ping"}, headers={**HEADERS, "Authorization": "Bearer wrong"})
        assert r.status_code == 401, f"预期 401，实际 {r.status_code}"
        print("[1] 鉴权拦截 OK（错误令牌 → 401）")

        # 2. initialize
        r = client.post(
            URL,
            headers=HEADERS,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "smoke", "version": "0"},
                },
            },
        )
        assert r.status_code == 200, f"initialize 失败：{r.status_code} {r.text[:200]}"
        session_id = r.headers.get("mcp-session-id")
        assert session_id, "缺少 mcp-session-id 响应头"
        print(f"[2] initialize OK，session={session_id}")

        headers = {**HEADERS, "mcp-session-id": session_id}

        # 3. initialized 通知
        r = client.post(URL, headers=headers, json={"jsonrpc": "2.0", "method": "notifications/initialized"})
        assert r.status_code in (200, 202), f"initialized 失败：{r.status_code}"
        print("[3] initialized OK")

        # 4. tools/list
        r = client.post(URL, headers=headers, json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        payload = parse_sse(r.text)
        names = sorted(t["name"] for t in payload["result"]["tools"])
        print(f"[4] tools/list OK：{names}")
        required = {
            "generate_image",
            "create_video",
            "list_models",
            "reload_config",
            "get_task",
            "list_tasks",
            "cancel_task",
        }
        assert required <= set(names), f"缺少工具：{required - set(names)}"

        # 5. 只读调用
        r = client.post(
            URL,
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "list_models", "arguments": {"capability": "image"}},
            },
        )
        payload = parse_sse(r.text)
        text = "".join(c.get("text", "") for c in payload["result"]["content"])
        assert "flux-3-image" in text, text[:200]
        print("[5] tools/call list_models OK，含 flux-3-image")
        print(text.splitlines()[0])

    print("HTTP-SMOKE OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
