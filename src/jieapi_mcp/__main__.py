"""入口：支持 stdio（本地）与 streamable-http（服务端）两种传输。

用法：
    python -m jieapi_mcp                       # stdio（默认，给本地 MCP 客户端）
    python -m jieapi_mcp --transport streamable-http --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import argparse
import os
import sys

from .config import ConfigError, ConfigManager
from .server import _make_context, build_server


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="jieapi_mcp", description="jieapi MCP 网关服务")
    parser.add_argument(
        "--transport",
        choices=["stdio", "streamable-http"],
        default=os.environ.get("MCP_TRANSPORT", "stdio"),
        help="传输方式（默认 stdio；服务端部署用 streamable-http）",
    )
    parser.add_argument("--host", default=os.environ.get("MCP_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("MCP_PORT", "8000")))
    parser.add_argument("--path", default=os.environ.get("MCP_PATH", "/mcp"))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    try:
        ctx = _make_context(ConfigManager.bootstrap())
        server = build_server(ctx)
    except ConfigError as exc:
        print(f"[jieapi-mcp] 配置错误，服务未启动：\n{exc}", file=sys.stderr)
        return 2

    if args.transport == "stdio":
        server.run(transport="stdio")
        return 0

    # ---- Streamable HTTP（服务端）--------------------------------------
    import uvicorn

    from .http_app import create_http_app, resolve_allowed_hosts

    try:
        app = create_http_app(server, path=args.path, port=args.port)
    except RuntimeError as exc:
        print(f"[jieapi-mcp] {exc}", file=sys.stderr)
        return 2

    print(
        f"[jieapi-mcp] Streamable HTTP 启动：http://{args.host}:{args.port}{args.path}"
        f"（允许 Host：{resolve_allowed_hosts(port=args.port)}）",
        file=sys.stderr,
    )
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
