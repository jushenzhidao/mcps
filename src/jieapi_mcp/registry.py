"""模型目录缓存（FR-F4）与能力校验（FR-F5）。"""

from __future__ import annotations

import threading
import time
from typing import Any

from .config import ConfigManager
from .errors import ModelNotAllowed
from .transport import Transport


class ModelCatalog:
    """``GET /v1/models`` 的内存缓存，默认 TTL 5 分钟。"""

    def __init__(self, transport: Transport, ttl: float = 300.0, clock=time.monotonic) -> None:
        self._transport = transport
        self._ttl = ttl
        self._clock = clock
        self._lock = threading.RLock()
        self._models: list[dict[str, Any]] | None = None
        self._expires_at = 0.0

    def list_models(self, *, force: bool = False) -> list[dict[str, Any]]:
        with self._lock:
            now = self._clock()
            if force or self._models is None or now >= self._expires_at:
                self._models = self._transport.list_models()
                self._expires_at = now + self._ttl
            return self._models

    def ids(self, *, force: bool = False) -> list[str]:
        return [str(m.get("id")) for m in self.list_models(force=force) if m.get("id")]

    def invalidate(self) -> None:
        with self._lock:
            self._models = None
            self._expires_at = 0.0


class CapabilityGuard:
    """按能力映射表校验模型是否被允许（FR-F5）。"""

    def __init__(self, config: ConfigManager) -> None:
        self._config = config

    def ensure_allowed(self, model: str, capability: str) -> None:
        cap_map = self._config.capability_map
        if not cap_map.supports(model, capability):
            raise ModelNotAllowed(
                model=model,
                capability=capability,
                available=cap_map.models_for(capability),
            )
