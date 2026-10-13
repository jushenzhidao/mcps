"""框架级工具：对所有能力通用，由 :func:`jieapi_mcp.server.build_server` 注册一次。

* ``list_models``     模型目录（FR-F4）
* ``reload_config``   热重载配置（FR-F2）
* ``get_task``        查询任务（并推进一次轮询）—— 对所有任务型能力通用
* ``list_tasks``      列出任务 —— 对所有任务型能力通用
* ``cancel_task``     取消任务 —— 对所有任务型能力通用

任务工具之所以放在框架层而非各能力内：它们只依赖
:class:`~jieapi_mcp.tasks.Task` 与 ``TaskCapability`` 的公共编排接口，
因此新增任何任务型能力都自动获得这三个工具，无需重复实现。
"""

from __future__ import annotations

import json
import time
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.types import TextContent

from .capabilities.base import TaskCapability
from .context import AppContext
from .tasks import TaskStatus

#: ``get_task`` 单次调用内最多阻塞等待的秒数（避免占住 stdio 通道过久）
MAX_WAIT_SECONDS = 60.0
_POLL_INTERVAL = 2.0


def register_framework_tools(mcp: MCPServer, ctx: AppContext) -> None:
    _register_list_models(mcp, ctx)
    _register_reload_config(mcp, ctx)
    _register_task_tools(mcp, ctx)


# ---- 模型目录 -----------------------------------------------------------
def _register_list_models(mcp: MCPServer, ctx: AppContext) -> None:
    @mcp.tool(
        name="list_models",
        description=(
            "列出网关当前可用的模型 ID（带约 5 分钟内存缓存）。"
            "可选 capability 参数按本地能力映射表过滤，例如 capability='image' "
            "只返回可用于生图的模型；refresh=true 强制刷新缓存。"
        ),
    )
    def list_models(capability: str | None = None, refresh: bool = False) -> str:
        ids = ctx.catalog.ids(force=refresh)
        if capability:
            allowed = set(ctx.config.capability_map.models_for(capability))
            ids = [i for i in ids if i in allowed]
            header = f"具备 `{capability}` 能力且网关在售的模型（{len(ids)} 个）："
        else:
            header = f"网关当前可用模型（{len(ids)} 个）："
        body = "\n".join(f"- {i}" for i in ids) if ids else "（无）"
        return f"{header}\n{body}"


# ---- 配置热重载 ---------------------------------------------------------
def _register_reload_config(mcp: MCPServer, ctx: AppContext) -> None:
    @mcp.tool(
        name="reload_config",
        description=(
            "热重载能力映射表（capabilities.yaml）与环境变量，无需重启进程。"
            "修改映射表后调用即可让新模型/能力立即生效。"
        ),
    )
    def reload_config() -> str:
        try:
            info = ctx.config.reload()
        except Exception as exc:  # 保持旧配置不变，把原因回给调用方
            return f"重载失败，已保留原配置：{exc}"
        ctx.catalog.invalidate()
        return (
            f"配置已热重载：{info['models']} 个模型映射，"
            f"来源 {info['capabilities_file']}；模型目录缓存已失效。"
        )


# ---- 任务工具（所有任务型能力通用）--------------------------------------
def _register_task_tools(mcp: MCPServer, ctx: AppContext) -> None:
    @mcp.tool(
        name="get_task",
        description=(
            "查询任务型能力（如视频生成）的任务状态与结果。"
            "若任务尚未完成，会向该能力的上游推进一次轮询。"
            "wait_seconds>0 时会在本次调用内短暂轮询等待（上限 60 秒），"
            "适合上游很快返回的场景；长任务建议先返回、之后再查。"
        ),
    )
    def get_task(task_id: str, wait_seconds: float = 0.0) -> str:
        task = ctx.tasks.get(task_id)
        if task is None:
            return f"未找到任务 {task_id}（可能已过期，或 id 有误）。"

        capability = ctx.capabilities.get(task.capability)
        wants_wait = wait_seconds and wait_seconds > 0
        deadline = time.monotonic() + min(float(wait_seconds or 0.0), MAX_WAIT_SECONDS)

        while True:
            if not task.terminal and isinstance(capability, TaskCapability):
                try:
                    task = capability.advance(ctx, task)
                except Exception as exc:  # 推进失败：如实回写，不吞错
                    return json.dumps(
                        {"task_id": task.id, "status": task.status.value, "advance_error": str(exc)},
                        ensure_ascii=False,
                        indent=2,
                    )
            if task.terminal or not wants_wait or time.monotonic() >= deadline:
                break
            time.sleep(min(_POLL_INTERVAL, max(0.0, deadline - time.monotonic())))

        return json.dumps(task.to_dict(), ensure_ascii=False, indent=2)

    @mcp.tool(
        name="list_tasks",
        description=(
            "列出本进程内的任务。可按 status（queued/running/succeeded/failed/cancelled/expired）"
            "与 capability（如 video）过滤。仅保留最近若干条且有时效。"
        ),
    )
    def list_tasks(status: str | None = None, capability: str | None = None, limit: int = 20) -> str:
        parsed: TaskStatus | None = None
        if status:
            try:
                parsed = TaskStatus(status)
            except ValueError:
                return f"未知 status：{status}；可选：{[s.value for s in TaskStatus]}"
        tasks = ctx.tasks.list(status=parsed, capability=capability, limit=limit)
        if not tasks:
            return "（无匹配任务）"
        lines = [
            f"- {t.id} | {t.capability} | {t.model} | {t.status.value}"
            + (f" | {t.error}" if t.error else "")
            for t in tasks
        ]
        return f"共 {len(tasks)} 条：\n" + "\n".join(lines)

    @mcp.tool(
        name="cancel_task",
        description="取消一个仍在进行中的任务型任务（若该能力声明了上游取消端点则一并通知上游）。",
    )
    def cancel_task(task_id: str) -> str:
        task = ctx.tasks.get(task_id)
        if task is None:
            return f"未找到任务 {task_id}。"
        if task.terminal:
            return f"任务 {task_id} 已是终态（{task.status.value}），无需取消。"
        capability = ctx.capabilities.get(task.capability)
        if not isinstance(capability, TaskCapability):
            return f"任务 {task_id} 所属能力 `{task.capability}` 不支持取消。"
        try:
            task = capability.cancel(ctx, task)
        except Exception as exc:
            return f"取消失败：{exc}"
        return f"任务 {task_id} 已取消（{task.status.value}）。"
