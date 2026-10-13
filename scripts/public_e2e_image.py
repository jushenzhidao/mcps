"""公网端到端测试：真实域名 + TLS，一路测到真实生图并落盘。

用法：
    MCP_URL=https://mcp.1task.cn/mcp MCP_AUTH_TOKEN=xxx \
        .venv/bin/python scripts/public_e2e_image.py

⚠️ 计费：会真实调用一次生图（默认用最便宜的 doubao-seedream-5-0-260128，约 0.22 元/张）。
      前面的 401 / initialize / tools/list / list_models 均不计费。
"""

from __future__ import annotations

import base64
import json
import os
import sys
from pathlib import Path

import httpx

URL = os.environ.get("MCP_URL", "https://mcp.1task.cn/mcp")
TOKEN = os.environ.get("MCP_AUTH_TOKEN", "")
MODEL = os.environ.get("IMAGE_MODEL", "doubao-seedream-5-0-260128")
PROMPT = os.environ.get("IMAGE_PROMPT", "a red apple on a white table, studio light")
OUT = Path(os.environ.get("OUT_DIR", "/tmp/mcp-public-e2e"))
BASE = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
    "Authorization": f"Bearer {TOKEN}",
}


def parse_sse(text: str) -> list[dict]:
    """收集 SSE 里所有 data: 事件（streamable HTTP 可能先发进度再发结果）。"""
    out = []
    for line in text.splitlines():
        if line.startswith("data:"):
            try:
                out.append(json.loads(line[5:].strip()))
            except ValueError:
                pass
    return out


def pick_result(events: list[dict]) -> dict | None:
    for e in events:
        if isinstance(e, dict) and "result" in e and "id" in e:
            return e["result"]
    return None


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    with httpx.Client(timeout=300, trust_env=False) as c:
        # 1) 错误令牌必须 401（公网+鉴权生效）
        r = c.post(
            URL,
            headers={**BASE, "Authorization": "Bearer wrong-token"},
            json={"jsonrpc": "2.0", "id": 0, "method": "ping"},
        )
        assert r.status_code == 401, f"预期 401，实际 {r.status_code}"
        print("[1] 错误令牌 → 401  OK")

        # 2) 健康检查（免鉴权）
        hz = c.get(URL.rsplit("/mcp", 1)[0] + "/healthz")
        assert hz.status_code == 200, f"healthz {hz.status_code}"
        print(f"[2] /healthz → 200  OK  {hz.json()}")

        # 3) initialize
        r = c.post(
            URL,
            headers=BASE,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "public-e2e", "version": "0"},
                },
            },
        )
        sid = r.headers.get("mcp-session-id")
        assert r.status_code == 200 and sid, f"initialize 失败 {r.status_code}"
        print(f"[3] initialize → 200，session={sid}  OK")
        H = {**BASE, "mcp-session-id": sid}
        c.post(URL, headers=H, json={"jsonrpc": "2.0", "method": "notifications/initialized"})

        # 4) 只读：工具清单 + 模型列表（不计费）
        r = c.post(URL, headers=H, json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        names = sorted(t["name"] for t in pick_result(parse_sse(r.text))["tools"])
        print(f"[4] tools/list → {names}  OK")

        r = c.post(
            URL,
            headers=H,
            json={
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "list_models", "arguments": {"capability": "image"}},
            },
        )
        txt = "".join(x.get("text", "") for x in pick_result(parse_sse(r.text))["content"])
        assert MODEL in txt, f"{MODEL} 不在可用清单"
        print(f"[5] list_models → 含 {MODEL}  OK（{txt.splitlines()[0]}）")

        # 6) 真实生图（计费！走 b64_json 以验证内联图片能穿 TLS+nginx 回来）
        print(f"[6] generate_image 开始（真实计费，model={MODEL}）…")
        r = c.post(
            URL,
            headers=H,
            json={
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {
                    "name": "generate_image",
                    "arguments": {
                        "model": MODEL,
                        "prompt": PROMPT,
                        "size": "1024x1024",
                        "n": 1,
                        "response_format": "b64_json",
                    },
                },
            },
        )
        result = pick_result(parse_sse(r.text))
        assert result is not None, f"无结果：http={r.status_code} {r.text[:300]}"

        blocks = result.get("content", [])
        saved = 0
        for i, blk in enumerate(blocks):
            if blk.get("type") == "image" and blk.get("data"):
                raw = base64.b64decode(blk["data"])
                ext = "png" if raw[:4] == b"\x89PNG" else ("jpg" if raw[:2] == b"\xff\xd8" else "png")
                p = OUT / f"public-e2e-{i}.{ext}"
                p.write_bytes(raw)
                print(f"[6] 内联图片已落盘：{p}（{len(raw)} 字节）")
                saved += 1
            elif blk.get("type") == "text":
                print(f"[6] 文本块：{blk['text'][:200]}")

        if saved == 0:
            # 网关没给 b64_json ⇒ 回退成 URL
            urls = [b["text"] for b in blocks if b.get("type") == "text" and "http" in b.get("text", "")]
            assert urls, f"既无内联图也无 URL：{blocks}"
            link = urls[0].split("：")[-1].strip()
            print(f"[6] 返回 URL，回退下载：{link}")
            img = httpx.get(link, timeout=120, trust_env=False)
            img.raise_for_status()
            p = OUT / "public-e2e-url.png"
            p.write_bytes(img.content)
            print(f"[6] 已下载：{p}（{len(img.content)} 字节）")
            saved = 1

        print(f"\nPUBLIC-E2E OK —— 产物在 {OUT}")
        return 0


if __name__ == "__main__":
    sys.exit(main())
