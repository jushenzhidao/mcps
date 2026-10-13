"""Streamable HTTP 服务端装配：鉴权 + 健康检查 + Host 校验。

安全模型（重要）
----------------
本服务端持有 ``JIEAPI_KEY``（会花钱的网关令牌）。**绝不能无鉴权暴露到公网**，
否则任何拿到 URL 的人都能消耗你的额度。

因此：
* HTTP 模式下若未设置 ``MCP_AUTH_TOKEN``，服务端**拒绝启动**（除非显式
  设置 ``MCP_ALLOW_NO_AUTH=1``，仅建议本机调试时使用）；
* 所有非健康检查请求必须携带 ``Authorization: Bearer <MCP_AUTH_TOKEN>``；
* 采用**纯 ASGI 中间件**（非 BaseHTTPMiddleware），以免破坏 SSE 流式响应。
* SDK 默认开启 DNS rebinding 防护，故需通过 ``MCP_ALLOWED_HOSTS`` 显式放行域名。
"""

from __future__ import annotations

import hmac
import os
from typing import Iterable

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings

HEALTH_PATH = "/healthz"


class BearerAuthMiddleware:
    """纯 ASGI 的 Bearer 鉴权中间件（流式响应安全）。"""

    def __init__(self, app, token: str, *, allow_paths: Iterable[str] = (HEALTH_PATH,)) -> None:
        self.app = app
        self.token = token
        self.allow_paths = frozenset(allow_paths)

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http" or not self.token:
            return await self.app(scope, receive, send)
        if scope.get("path") in self.allow_paths:
            return await self.app(scope, receive, send)

        headers = {k.decode().lower(): v.decode() for k, v in (scope.get("headers") or [])}
        provided = headers.get("authorization", "")
        expected = f"Bearer {self.token}"
        if not hmac.compare_digest(provided, expected):
            body = b'{"error":"unauthorized","hint":"need Authorization: Bearer <MCP_AUTH_TOKEN>"}'
            await send(
                {
                    "type": "http.response.start",
                    "status": 401,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"content-length", str(len(body)).encode()),
                        (b"www-authenticate", b"Bearer"),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": body})
            return
        return await self.app(scope, receive, send)


async def _health(_: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "server": "jieapi-gateway"})


def resolve_allowed_hosts(explicit: str | None = None, *, port: int | None = None) -> list[str]:
    """解析允许的 Host，始终附带本地回环。

    ⚠️ SDK 的 DNS rebinding 防护按 **Host 头逐字**比对，而带端口的请求
    Host 形如 ``127.0.0.1:8801``。因此需要同时放行「裸主机名」与「主机名:端口」，
    否则会得到 421 Invalid Host header（上到 nginx 后面会表现为难以定位的 502/421）。
    """
    raw = explicit if explicit is not None else os.environ.get("MCP_ALLOWED_HOSTS", "")
    hosts = [h.strip() for h in raw.split(",") if h.strip()]
    for local in ("localhost", "127.0.0.1"):
        if local not in hosts:
            hosts.append(local)

    if port:
        for host in list(hosts):
            with_port = f"{host}:{port}"
            if with_port not in hosts:
                hosts.append(with_port)
    return hosts


def create_http_app(
    server: MCPServer,
    *,
    path: str = "/mcp",
    auth_token: str | None = None,
    allowed_hosts: list[str] | None = None,
    port: int | None = None,
    stateless: bool | None = None,
) -> object:
    """构造带鉴权与健康检查的 ASGI 应用。

    ``stateless``（或环境变量 ``MCP_STATELESS=1``）
        开启后每个请求独立、不建立会话 ⇒ **可多 worker / 多副本横向扩容**。
        代价：会话内的服务端推送能力（sampling / elicitation / 进度通知）不可用。
        本网关只做「请求-响应 + 显式轮询工具调用」，因此通常可以接受。
    """
    token = auth_token if auth_token is not None else os.environ.get("MCP_AUTH_TOKEN", "")
    if stateless is None:
        stateless = os.environ.get("MCP_STATELESS", "0") == "1"
    transport_security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=allowed_hosts or resolve_allowed_hosts(port=port),
    )
    app: Starlette = server.streamable_http_app(
        streamable_http_path=path,
        stateless_http=bool(stateless),
        transport_security=transport_security,
    )
    app.router.routes.append(Route(HEALTH_PATH, _health, methods=["GET"]))

    if not token:
        if os.environ.get("MCP_ALLOW_NO_AUTH") == "1":
            return app  # 显式放行的本机调试模式
        raise RuntimeError(
            "HTTP 模式下必须设置 MCP_AUTH_TOKEN（本服务端持有会花钱的网关令牌）。"
            "如仅本机调试，可设 MCP_ALLOW_NO_AUTH=1。"
        )
    return BearerAuthMiddleware(app, token)
