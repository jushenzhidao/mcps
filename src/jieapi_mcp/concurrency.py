"""并发调优。

背景（**不要用多 worker 解决**）
-------------------------------
本服务的瓶颈不是 CPU，而是上游延迟（生图 9~60s）。SDK 对同步工具的实现是::

    await anyio.to_thread.run_sync(lambda: fn(**kwargs))

即同步工具跑在 anyio 的**默认线程池**里，默认容量 **40**。因此真正的并发上限
由这个池决定，而不是 uvicorn 的 worker 数。

为什么不能多开 worker：
* MCP Streamable HTTP 的会话管理器在进程内 ⇒ 跨 worker 会 session not found；
* 任务表 TaskStore 也在进程内 ⇒ 多 worker 会把任务状态切碎。

要提升并发，请调大 ``MCP_MAX_CONCURRENCY``（线程池容量）与
``MCP_HTTP_MAX_CONNECTIONS``（httpx 连接池）；上限最终受网关自身的限流约束。
"""

from __future__ import annotations


def apply_thread_limiter(max_concurrency: int) -> int:
    """设置 anyio 默认线程池容量，返回生效值。需在事件循环内/前调用。"""
    if max_concurrency is None or max_concurrency <= 0:
        return 0
    from anyio import to_thread

    limiter = to_thread.current_default_thread_limiter()
    limiter.total_tokens = int(max_concurrency)
    return limiter.total_tokens
