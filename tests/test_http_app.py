"""HTTP 服务端装配与鉴权测试。零网络。"""

from __future__ import annotations

import pytest

from jieapi_mcp.http_app import BearerAuthMiddleware, create_http_app, resolve_allowed_hosts
from jieapi_mcp.server import build_server

from .conftest import json_handler, make_context


@pytest.fixture
def server():
    ctx = make_context(json_handler({("GET", "/v1/models"): (200, {"data": []})}))
    return build_server(ctx)


def test_allowed_hosts_always_include_loopback(monkeypatch):
    monkeypatch.delenv("MCP_ALLOWED_HOSTS", raising=False)
    hosts = resolve_allowed_hosts("mcp.1task.cn")
    assert "mcp.1task.cn" in hosts
    assert "localhost" in hosts and "127.0.0.1" in hosts


def test_refuses_start_without_token(server, monkeypatch):
    """HTTP 模式无令牌必须拒绝启动——服务端握着会花钱的网关令牌。"""
    monkeypatch.delenv("MCP_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("MCP_ALLOW_NO_AUTH", raising=False)
    with pytest.raises(RuntimeError) as ei:
        create_http_app(server, auth_token="")
    assert "MCP_AUTH_TOKEN" in str(ei.value)


def test_explicit_debug_bypass(server, monkeypatch):
    monkeypatch.delenv("MCP_AUTH_TOKEN", raising=False)
    monkeypatch.setenv("MCP_ALLOW_NO_AUTH", "1")
    app = create_http_app(server, auth_token="")
    assert not isinstance(app, BearerAuthMiddleware)


def test_with_token_wraps_in_auth(server):
    app = create_http_app(server, auth_token="secret", allowed_hosts=["localhost", "127.0.0.1"])
    assert isinstance(app, BearerAuthMiddleware)


def test_health_route_registered(server):
    app = create_http_app(server, auth_token="secret", allowed_hosts=["localhost", "127.0.0.1"])
    inner = app.app  # 拆掉鉴权中间件看内部 app
    paths = {getattr(r, "path", None) for r in inner.router.routes}
    assert "/healthz" in paths
    assert "/mcp" in paths
