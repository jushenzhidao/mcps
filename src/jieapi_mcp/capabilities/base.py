"""能力插件基类与执行模式——本项目的核心扩展点。

两层抽象
--------
1. :class:`ExecutionMode` —— 声明能力的**工具调用语义**：
   ``SYNC`` 一次调用直接返回结果；``TASK`` 一次调用只返回任务句柄（task_id）。
2. :class:`BaseCapability` / :class:`TaskCapability` —— 两种能力的基类。
   框架层（装配 / 校验 / 错误翻译 / 热重载 / 任务表）完全不区分二者。

新增一个能力 = 写一个子类 + 放到 ``capabilities/`` 下（自动发现）+ 在
``capabilities.yaml`` 加标签。**无需改动框架层任何文件。**

同步能力：继承 :class:`BaseCapability`，实现 ``register_tools()``。
任务型能力：继承 :class:`TaskCapability`，只实现三个纯函数钩子
（``build_submit_body`` / ``read_submit`` / ``read_poll``），
框架会自动提供 ``submit`` / ``advance`` / ``cancel`` 编排与 ``get_task`` / ``list_tasks`` /
``cancel_task`` 通用工具。
"""

from __future__ import annotations

import inspect
from abc import ABC, abstractmethod
from enum import Enum
from typing import TYPE_CHECKING, Any, ClassVar, Protocol

if TYPE_CHECKING:  # pragma: no cover
    from mcp.server.mcpserver import MCPServer

    from ..context import AppContext
    from ..tasks import Task


class ExecutionMode(str, Enum):
    """工具调用的返回语义。"""

    SYNC = "sync"
    """同步：调用即得结果（如本期 image）。"""

    TASK = "task"
    """任务型：调用返回 task_id，结果需另行轮询（如未来的 video / 3D）。"""


class Capability(Protocol):
    """能力插件的结构化协议（无需显式继承）。"""

    name: ClassVar[str]
    execution_mode: ClassVar[ExecutionMode]

    def register_tools(self, mcp: "MCPServer", ctx: "AppContext") -> None: ...


class BaseCapability(ABC):
    """同步能力基类。"""

    name: ClassVar[str] = ""
    execution_mode: ClassVar[ExecutionMode] = ExecutionMode.SYNC

    #: 该能力对应的上游端点（仅供子类记录与自检，框架不读取）
    endpoint: ClassVar[str | None] = None

    @abstractmethod
    def register_tools(self, mcp: "MCPServer", ctx: "AppContext") -> None:
        """把本能力的工具注册到 MCP Server 上。"""

    # ---- 供子类复用的公共辅助 -------------------------------------------
    @staticmethod
    def guard_model(ctx: "AppContext", model: str, capability: str) -> None:
        """FR-F5：调用前校验模型是否被允许用于该能力。"""
        ctx.guard.ensure_allowed(model, capability)


class TaskCapability(BaseCapability):
    """任务型（异步）能力基类。

    子类只需声明端点与三个纯函数钩子；编排由框架完成。

    钩子契约
    --------
    ``build_submit_body(params) -> dict``
        由调用参数构造提交给上游的请求体。
    ``read_submit(response) -> dict``
        解析提交响应，返回 ``{"upstream_id"?, "status"?, "result"?, "error"?}``。
    ``read_poll(response) -> dict``
        解析轮询响应，返回 ``{"status", "result"?, "error"?}``；``status`` 为
        :class:`~jieapi_mcp.tasks.TaskStatus`。
    """

    execution_mode = ExecutionMode.TASK

    #: 提交端点，如 ``/v1/videos/generations``
    submit_path: ClassVar[str] = ""
    #: 轮询端点，可用 ``{task_id}`` 占位（取上游 id，缺省用网关任务 id）
    poll_path: ClassVar[str] = ""
    #: 取消端点（可选），可用 ``{task_id}`` 占位
    cancel_path: ClassVar[str | None] = None

    # ---- 子类必须实现的三/四个钩子 --------------------------------------
    def build_submit_body(self, params: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    def read_submit(self, response: Any) -> dict[str, Any]:
        raise NotImplementedError

    def read_poll(self, response: Any) -> dict[str, Any]:
        raise NotImplementedError

    # ---- 框架提供的编排 --------------------------------------------------
    def submit(
        self,
        ctx: "AppContext",
        *,
        model: str,
        params: dict[str, Any],
        meta: dict[str, Any] | None = None,
    ) -> "Task":
        """提交任务：校验模型 → 调上游 → 建立本地任务记录 → 立即返回。"""
        from ..tasks import TaskStatus

        self.guard_model(ctx, model, self.name)
        body = self.build_submit_body({"model": model, **params})
        response = ctx.transport.post_json(self.submit_path, body)
        info = self.read_submit(response)
        task = ctx.tasks.create(
            capability=self.name,
            model=model,
            upstream_id=info.get("upstream_id"),
            meta=meta or {"params": params},
        )
        return ctx.tasks.update(
            task.id,
            status=info.get("status") or TaskStatus.QUEUED,
            result=info.get("result"),
            error=info.get("error"),
        )

    def advance(self, ctx: "AppContext", task: "Task") -> "Task":
        """推进一次：若任务未终态，轮询上游并回写状态。"""
        if task.terminal:
            return task
        path = self.poll_path.format(task_id=task.upstream_id or task.id)
        response = ctx.transport.get_json(path)
        info = self.read_poll(response)
        return ctx.tasks.update(
            task.id,
            status=info.get("status", task.status),
            result=info.get("result"),
            error=info.get("error"),
        )

    def cancel(self, ctx: "AppContext", task: "Task") -> "Task":
        """取消任务（若上游支持取消端点）。"""
        from ..tasks import TaskStatus

        if task.terminal:
            return task
        if self.cancel_path and task.upstream_id:
            ctx.transport.post_json(self.cancel_path.format(task_id=task.upstream_id), {})
        return ctx.tasks.update(task.id, status=TaskStatus.CANCELLED)

    def register_tools(self, mcp: "MCPServer", ctx: "AppContext") -> None:
        """任务型能力默认不注册额外工具（提交工具由子类自行注册）。

        通用的 ``get_task`` / ``list_tasks`` / ``cancel_task`` 由框架统一注册在
        :mod:`jieapi_mcp.framework_tools`，对所有任务型能力生效。
        """


def is_concrete_capability(obj: Any) -> bool:
    """判断一个类是否是可实例化的能力插件（用于自动发现）。"""
    return (
        inspect.isclass(obj)
        and issubclass(obj, BaseCapability)
        and not inspect.isabstract(obj)
        and bool(getattr(obj, "name", ""))
    )
