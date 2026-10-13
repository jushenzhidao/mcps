"""任务基础设施：为「任务型异步」能力（视频生成等）提供的通用落点。

为什么需要它？
-------------
有些上游不是"一次请求一次结果"，而是：提交(submit) → 拿到上游 task_id → 轮询(poll)
→ 终态。这类能力的 MCP 工具必须**立刻返回一个任务句柄**，让调用方（LLM）自行决定何时查询，
否则一次工具调用会长时间阻塞。

本模块只做**编排**，不碰具体上游协议；协议差异由
:class:`~jieapi_mcp.capabilities.base.TaskCapability` 的子类通过钩子函数表达。

轮询策略：**按需轮询**（get_task 时推进一次），不做后台线程。
理由：MCP over stdio 是单进程、由客户端驱动的；让 LLM 主动轮询更可控、更易测试，
也避免线程生命周期在 stdio 场景下的麻烦。
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol


class TaskStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"

    @property
    def terminal(self) -> bool:
        return self in _TERMINAL


_TERMINAL = frozenset(
    {TaskStatus.SUCCEEDED, TaskStatus.FAILED, TaskStatus.CANCELLED, TaskStatus.EXPIRED}
)


@dataclass
class Task:
    id: str
    capability: str
    model: str
    status: TaskStatus = TaskStatus.QUEUED
    upstream_id: str | None = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    result: dict[str, Any] | None = None
    error: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def terminal(self) -> bool:
        return self.status.terminal

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "task_id": self.id,
            "capability": self.capability,
            "model": self.model,
            "status": self.status.value,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
        if self.upstream_id:
            out["upstream_id"] = self.upstream_id
        if self.result is not None:
            out["result"] = self.result
        if self.error:
            out["error"] = self.error
        if self.meta:
            out["meta"] = self.meta
        return out


class TaskStore:
    """进程内任务表（线程安全）。默认最多保留 200 条、1 小时过期。"""

    def __init__(
        self,
        *,
        max_tasks: int = 200,
        ttl: float = 3600.0,
        clock=time.time,
    ) -> None:
        self._tasks: dict[str, Task] = {}
        self._lock = threading.RLock()
        self._max = max_tasks
        self._ttl = ttl
        self._clock = clock

    def create(
        self,
        *,
        capability: str,
        model: str,
        upstream_id: str | None = None,
        status: TaskStatus = TaskStatus.QUEUED,
        meta: dict[str, Any] | None = None,
    ) -> Task:
        with self._lock:
            self._prune_locked()
            now = self._clock()
            task = Task(
                id=f"task-{uuid.uuid4().hex[:16]}",
                capability=capability,
                model=model,
                status=status,
                upstream_id=upstream_id,
                created_at=now,
                updated_at=now,
                meta=dict(meta or {}),
            )
            self._tasks[task.id] = task
            return task

    def get(self, task_id: str) -> Task | None:
        with self._lock:
            self._prune_locked()
            return self._tasks.get(task_id)

    def list(
        self,
        *,
        status: TaskStatus | None = None,
        capability: str | None = None,
        limit: int = 20,
    ) -> list[Task]:
        with self._lock:
            self._prune_locked()
            items = list(self._tasks.values())
        if status is not None:
            items = [t for t in items if t.status == status]
        if capability is not None:
            items = [t for t in items if t.capability == capability]
        items.sort(key=lambda t: t.created_at, reverse=True)
        return items[: max(0, limit)]

    def update(self, task_id: str, **fields: Any) -> Task:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                raise KeyError(task_id)
            for key, value in fields.items():
                if value is not None or key in ("result", "error", "upstream_id"):
                    setattr(task, key, value)
            task.updated_at = self._clock()
            return task

    # ---- 内部 -----------------------------------------------------------
    def _prune_locked(self) -> None:
        now = self._clock()
        expired = [tid for tid, t in self._tasks.items() if now - t.created_at > self._ttl]
        for tid in expired:
            del self._tasks[tid]
        if len(self._tasks) > self._max:
            ordered = sorted(self._tasks.values(), key=lambda t: t.created_at)
            for task in ordered[: len(self._tasks) - self._max]:
                self._tasks.pop(task.id, None)


class TaskStoreProtocol(Protocol):
    """任务表接口 —— **外置存储（Redis / MySQL）的扩展点**。

    为什么需要它：多 worker / 多副本部署时，进程内字典会导致 ``get_task`` 查不到任务。
    实现本协议的 Redis/MySQL 后端后，替换 :class:`AppContext` 里的 ``tasks`` 即可
    （drop-in，框架与能力插件都不用改）。

    ⚠️ 分布式实现下建议对 ``update`` 加锁（如 Redis SETNX）以避免多 worker 并发轮询同一任务、
    对上游造成重复请求。
    """

    def create(
        self,
        *,
        capability: str,
        model: str,
        upstream_id: str | None = None,
        status: TaskStatus = TaskStatus.QUEUED,
        meta: dict[str, Any] | None = None,
    ) -> Task: ...

    def get(self, task_id: str) -> Task | None: ...

    def list(
        self,
        *,
        status: TaskStatus | None = None,
        capability: str | None = None,
        limit: int = 20,
    ) -> list[Task]: ...

    def update(self, task_id: str, **fields: Any) -> Task: ...
