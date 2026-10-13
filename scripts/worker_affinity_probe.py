"""会话亲和性探针：验证 MCP 会话在多 worker 下的行为。

用法：
    MCP_URL=http://127.0.0.1:8004/mcp MCP_AUTH_TOKEN=xxx \
        .venv/bin/python scripts/worker_affinity_probe.py

原理：initialize 建立会话（拿到 mcp-session-id），随后用**同一个 session id**
连续请求 N 次。单 worker 应 0 失败；多 worker 时请求会被分发到没有该会话的
worker ⇒ 失败（session not found）。stateless_http 模式下无会话，跨 worker 也 0 失败。

🔴 **必须每次新建连接**（FORCE_NEW_CONN，默认开）：gunicorn 是「一个连接由一个 worker 处理」，
   若复用 keep-alive 连接，所有请求会**粘在同一个 worker**，跨 worker 的问题根本暴露不出来
   （实测：复用连接时 2 worker 也 12/12 通过，是假绿灯）。
   真实世界触发条件是：多客户端并发、连接重建、或前置 LB 轮询。
"""

from __future__ import annotations

import json
import os
import sys

import httpx

URL = os.environ.get("MCP_URL", "http://127.0.0.1:8004/mcp")
TOKEN = os.environ.get("MCP_AUTH_TOKEN", "")
ROUNDS = int(os.environ.get("ROUNDS", "12"))
FORCE_NEW_CONN = os.environ.get("FORCE_NEW_CONN", "1") == "1"
BASE = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
    "Authorization": f"Bearer {TOKEN}",
}


def main() -> int:
    # 建会话
    with httpx.Client(timeout=20, trust_env=False) as client:
        r = client.post(
            URL,
            headers=BASE,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "probe", "version": "0"},
                },
            },
        )
        session_id = r.headers.get("mcp-session-id")
        mode = "有会话（stateful）" if session_id else "无会话（stateless）"
        print(f"initialize: http={r.status_code} 模式={mode} session={session_id}")

        headers = dict(BASE)
        if session_id:
            headers["mcp-session-id"] = session_id
            client.post(URL, headers=headers, json={"jsonrpc": "2.0", "method": "notifications/initialized"})

    conn_mode = "每次新建连接" if FORCE_NEW_CONN else "复用 keep-alive 连接"
    print(f"请求方式：{conn_mode}，共 {ROUNDS} 次")

    ok = fail = 0
    first_error = ""
    shared = None if FORCE_NEW_CONN else httpx.Client(timeout=20, trust_env=False)
    try:
        for i in range(ROUNDS):
            payload = {"jsonrpc": "2.0", "id": i + 2, "method": "tools/list"}
            if FORCE_NEW_CONN:
                # 每次全新 client ⇒ 新 TCP 连接 ⇒ gunicorn 可能分给不同 worker
                with httpx.Client(timeout=20, trust_env=False) as c:
                    resp = c.post(URL, headers=headers, json=payload)
            else:
                resp = shared.post(URL, headers=headers, json=payload)
            good = resp.status_code == 200 and '"tools"' in resp.text and '"error"' not in resp.text
            if good:
                ok += 1
            else:
                fail += 1
                if not first_error:
                    first_error = f"http={resp.status_code} body={resp.text[:160]}"
    finally:
        if shared is not None:
            shared.close()

    print(f"连续 {ROUNDS} 次 tools/list：成功 {ok}，失败 {fail}")
    if first_error:
        print(f"首个失败样例：{first_error}")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
