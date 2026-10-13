"""打印 MCP Streamable HTTP 的**原始报文**，用来说明"调用到底是怎么发生的"。

用法：
    MCP_URL=https://mcp.1task.cn/mcp MCP_AUTH_TOKEN=xxx \
        .venv/bin/python scripts/mcp_wire_trace.py

只调用只读的 list_models（不计费）。
"""

from __future__ import annotations

import json
import os
import sys

import httpx

URL = os.environ.get("MCP_URL", "https://mcp.1task.cn/mcp")
TOKEN = os.environ.get("MCP_AUTH_TOKEN", "")
BASE = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",  # 🔴 两个都要写，缺 event-stream 会被拒
    "Authorization": f"Bearer {TOKEN}",
}


def show(title: str, payload: dict) -> None:
    print(f"\n{title}")
    print(json.dumps(payload, ensure_ascii=False))


def sse_lines(text: str, limit: int = 3) -> None:
    shown = 0
    for line in text.splitlines():
        if line:
            print("   ", line[:300])
            shown += 1
            if shown >= limit:
                break


def main() -> int:
    with httpx.Client(timeout=60, trust_env=False) as c:
        # ---- 1) initialize：唯一一个不带 session 的请求 ----
        req = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "wire-trace", "version": "0"},
            },
        }
        print("=" * 78)
        print(f"① POST {URL}   （establish session）")
        print("   headers:", {k: (v[:24] + "…" if k == "Authorization" else v) for k, v in BASE.items()})
        show("   body:", req)
        r = c.post(URL, headers=BASE, json=req)
        print(f"   ← HTTP {r.status_code}  content-type={r.headers.get('content-type')}")
        sid = r.headers.get("mcp-session-id")
        print(f"   ← mcp-session-id: {sid}   ← 🔴 后续每个请求都要带上它")
        sse_lines(r.text)

        if not sid:
            print("   （无 session ⇒ 服务端处于 stateless 模式）")
        H = {**BASE}
        if sid:
            H["mcp-session-id"] = sid

        # ---- 2) initialized 通知：无 id、无响应体 ----
        print("\n" + "=" * 78)
        print("② POST /mcp   （notifications/initialized）   ← 通知：没有 id，不期待响应")
        if sid:
            print(f"   headers: mcp-session-id: {sid}")
        r = c.post(URL, headers=H, json={"jsonrpc": "2.0", "method": "notifications/initialized"})
        print(f"   ← HTTP {r.status_code}")

        # ---- 3) tools/list ----
        print("\n" + "=" * 78)
        print("③ POST /mcp   （tools/list）")
        r = c.post(URL, headers=H, json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        print(f"   ← HTTP {r.status_code}  content-type={r.headers.get('content-type')}  ← 注意是 SSE 流")
        data = [json.loads(l[5:].strip()) for l in r.text.splitlines() if l.startswith("data:")]
        names = sorted(t["name"] for t in data[-1]["result"]["tools"])
        print(f"   ← tools: {names}")

        # ---- 4) tools/call ----
        print("\n" + "=" * 78)
        print("④ POST /mcp   （tools/call → list_models）")
        call = {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "list_models", "arguments": {"capability": "image"}},
        }
        show("   body:", call)
        r = c.post(URL, headers=H, json=call)
        data = [json.loads(l[5:].strip()) for l in r.text.splitlines() if l.startswith("data:")]
        result = next((d["result"] for d in data if "result" in d), None)
        text = "".join(x.get("text", "") for x in result["content"])
        print(f"   ← HTTP {r.status_code}，返回 content[{len(result['content'])}]，首行：")
        print("   ", text.splitlines()[0])

    print("\n" + "=" * 78)
    print("结论：MCP = JSON-RPC 2.0 over HTTP(SSE)。没有 REST 语义，")
    print("      全靠 method 字段分派；会话靠 mcp-session-id 头维系。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
