"""MCP Server 装配：自动发现能力插件 + 注册框架工具。

装配顺序
--------
1. 构建上下文（配置 / 传输 / 模型目录 / 能力校验 / 任务表）；
2. **自动发现** ``capabilities/`` 下全部能力插件（无需登记）；
3. 注册框架级工具（``list_models`` / ``reload_config`` / 任务三件套）；
4. 让每个能力注册自己的工具。

加能力不改本文件。
"""

from __future__ import annotations

import sys
from contextlib import asynccontextmanager

from mcp.server.mcpserver import MCPServer

from .capabilities import BaseCapability, discover_capabilities
from .concurrency import apply_thread_limiter
from .config import ConfigManager
from .context import AppContext
from .errors import GatewayError, ModelNotAllowed
from .framework_tools import register_framework_tools
from .registry import CapabilityGuard, ModelCatalog
from .tasks import TaskStore
from .transport import Transport

SERVER_NAME = "jieapi-gateway"


def _make_lifespan(ctx: AppContext):
    """启动钩子：设置并发上限（必须在事件循环内执行）。"""

    @asynccontextmanager
    async def lifespan(_server: MCPServer):
        applied = apply_thread_limiter(ctx.config.settings.max_concurrency)
        # 🔴 必须写 stderr：stdio 模式下 stdout 是 JSON-RPC 协议通道，写入会破坏协议
        print(
            f"[jieapi-mcp] 并发上限（anyio 线程池）={applied}，"
            f"httpx 连接池={ctx.config.settings.http_max_connections}",
            file=sys.stderr,
            flush=True,
        )
        yield {}

    return lifespan


def build_context() -> AppContext:
    """按环境变量构建上下文（传输层被目录缓存与能力共享同一实例）。"""
    return _make_context(ConfigManager.bootstrap())


def _make_context(config: ConfigManager) -> AppContext:
    """基于给定配置构建上下文。"""
    settings = config.settings
    transport = Transport(settings)
    return AppContext(
        config=config,
        transport=transport,
        catalog=ModelCatalog(transport, ttl=settings.models_cache_ttl),
        guard=CapabilityGuard(config),
        tasks=TaskStore(),
    )


def build_server(ctx: AppContext | None = None) -> MCPServer:
    """创建并装配 MCP Server。"""
    if ctx is None:
        ctx = _make_context(ConfigManager.bootstrap())

    capabilities = discover_capabilities()
    if not capabilities:
        raise RuntimeError("未发现任何能力插件（capabilities/ 目录为空？）")
    ctx.capabilities = {c.name: c for c in capabilities}

    mcp = MCPServer(SERVER_NAME, lifespan=_make_lifespan(ctx))
    register_framework_tools(mcp, ctx)
    for capability in capabilities:
        capability.register_tools(mcp, ctx)
    return mcp


__all__ = [
    "build_server",
    "build_context",
    "discover_capabilities",
    "SERVER_NAME",
    "GatewayError",
    "ModelNotAllowed",
    "BaseCapability",
]
