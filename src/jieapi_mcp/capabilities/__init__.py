"""能力插件包 —— 自动发现。

新增能力（含任务型异步能力）三步：
1. 在本包内新增模块，定义 :class:`~jieapi_mcp.capabilities.base.BaseCapability`
   （同步）或 :class:`~jieapi_mcp.capabilities.base.TaskCapability`（任务型）子类，
   类属性 ``name`` 需与 capabilities.yaml 中的能力标签一致；
2. **无需登记**——本包的 :func:`discover_capabilities` 会自动扫描所有非 ``_`` 前缀模块；
3. 在 capabilities.yaml 中为相关模型声明该能力标签。

框架层（server / transport / tasks / errors / config）无需改动。
``_`` 前缀的模块会被跳过（用于模板与内部实现）。
"""

from __future__ import annotations

import importlib
import pkgutil
from typing import TYPE_CHECKING

from .base import BaseCapability, ExecutionMode, TaskCapability, is_concrete_capability

if TYPE_CHECKING:  # pragma: no cover
    pass

__all__ = [
    "BaseCapability",
    "TaskCapability",
    "ExecutionMode",
    "discover_capabilities",
]


def discover_capabilities() -> list[BaseCapability]:
    """扫描本包，实例化全部能力插件（按能力名排序）。"""
    found: dict[str, BaseCapability] = {}
    for module_info in pkgutil.iter_modules(__path__):
        if module_info.name.startswith("_"):
            continue  # 跳过模板/内部模块
        module = importlib.import_module(f"{__name__}.{module_info.name}")
        for obj in vars(module).values():
            if is_concrete_capability(obj):
                instance: BaseCapability = obj()
                found[instance.name] = instance
    return [found[name] for name in sorted(found)]
