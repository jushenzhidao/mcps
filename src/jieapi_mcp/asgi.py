"""ASGI 入口：供 uvicorn / gunicorn 等外部 ASGI 服务器引用。

    uvicorn jieapi_mcp.asgi:app --host 0.0.0.0 --port 8000

⚠️ 只允许**单进程**运行（勿用多 worker / 多副本共享同一实例）：
   * MCP Streamable HTTP 的会话管理器是进程内的，跨 worker 会 session not found；
   * 任务表（TaskStore）也在进程内，多 worker 会导致 get_task 查不到任务。
   若要横向扩容，需先把任务表外置（Redis/DB）并改用 stateless_http + 会话亲和负载均衡。
"""

from __future__ import annotations

from .config import ConfigManager
from .http_app import create_http_app
from .server import build_server, _make_context


def build_app():
    """构建带鉴权的 ASGI 应用（导入即失败 = 配置有问题，快速暴露）。"""
    ctx = _make_context(ConfigManager.bootstrap())
    server = build_server(ctx)
    return create_http_app(server)


app = build_app()
