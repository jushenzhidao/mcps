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


@dataclass
class AppContext:
    config: ConfigManager
    transport: Transport
    catalog: ModelCatalog
    guard: CapabilityGuard
    tasks: TaskStore
    #: 能力名 → 能力实例；由 :func:`jieapi_mcp.server.build_server` 填充
    capabilities: dict[str, "BaseCapability"] = field(default_factory=dict)
