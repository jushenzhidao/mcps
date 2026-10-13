"""应用上下文：把框架组件打包成单一依赖对象，注入各能力插件。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .config import ConfigManager
from .registry import CapabilityGuard, ModelCatalog
from .tasks import TaskStore
from .transport import Transport

if TYPE_CHECKING:  # pragma: no cover
    from .capabilities.base import BaseCapability
    from .tasks import TaskStoreProtocol


@dataclass
class AppContext:
    config: ConfigManager
    transport: Transport
    catalog: ModelCatalog
    guard: CapabilityGuard
    #: 任务表。默认进程内实现；多 worker/多副本场景可替换为实现 TaskStoreProtocol 的
    #: Redis / MySQL 后端（drop-in）。
    tasks: "TaskStore | TaskStoreProtocol"
    #: 能力名 → 能力实例；由 :func:`jieapi_mcp.server.build_server` 填充
    capabilities: dict[str, "BaseCapability"] = field(default_factory=dict)


__all__ = ["AppContext", "TaskStore", "TaskStoreProtocol"]
