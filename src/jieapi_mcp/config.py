"""配置层：环境变量 + 能力映射表（capabilities.yaml）的加载与热重载。

设计要点
--------
* 凭据只来自环境变量（FR-F3）；对象内不提供明文打印路径，``redacted()`` 供日志使用。
* 能力映射表通过 :class:`ConfigManager` 持有，``reload()`` 支持热重载（FR-F2）。
* 加载失败一律抛 :class:`ConfigError`，由入口转成可读信息并拒绝启动（FR-F1）。
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

DEFAULT_BASE_URL = "https://jieapi.com"
DEFAULT_CAPABILITIES_FILE = Path(__file__).resolve().parent.parent.parent / "capabilities.yaml"


class ConfigError(RuntimeError):
    """配置缺失或格式非法。"""


def _env_str(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    if value is None:
        return default
    value = value.strip()
    return value or default


def _env_int(name: str, default: int) -> int:
    raw = _env_str(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"环境变量 {name} 必须是整数，当前值：{raw!r}") from exc


def _env_float(name: str, default: float) -> float:
    raw = _env_str(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ConfigError(f"环境变量 {name} 必须是数字，当前值：{raw!r}") from exc


@dataclass(frozen=True)
class Settings:
    """不可变运行配置。凭据不参与 repr/日志。"""

    api_key: str = field(repr=False)
    base_url: str = DEFAULT_BASE_URL
    image_max_file_mb: int = 10
    gateway_timeout: float = 120.0
    models_cache_ttl: float = 300.0
    #: 进程内并发上限（同步工具由 SDK 放入 anyio 线程池执行，默认池容量即 40）
    max_concurrency: int = 40
    #: httpx 连接池上限
    http_max_connections: int = 100
    http_max_keepalive: int = 20
    capabilities_file: Path = DEFAULT_CAPABILITIES_FILE

    @property
    def api_base(self) -> str:
        return self.base_url.rstrip("/")

    @property
    def max_upload_bytes(self) -> int:
        return self.image_max_file_mb * 1024 * 1024

    def redacted(self) -> dict[str, Any]:
        """可安全写日志的配置快照（令牌脱敏）。"""
        return {
            "base_url": self.base_url,
            "api_key": "***" if self.api_key else "(未设置)",
            "image_max_file_mb": self.image_max_file_mb,
            "gateway_timeout": self.gateway_timeout,
            "models_cache_ttl": self.models_cache_ttl,
            "max_concurrency": self.max_concurrency,
            "capabilities_file": str(self.capabilities_file),
        }


def load_settings() -> Settings:
    """从环境变量构造 :class:`Settings`。缺少 JIEAPI_KEY 直接抛错。"""
    api_key = _env_str("JIEAPI_KEY")
    if not api_key:
        raise ConfigError(
            "缺少环境变量 JIEAPI_KEY。请在 MCP 客户端的 env 配置中提供网关令牌。"
        )

    base_url = _env_str("JIEAPI_BASE_URL", DEFAULT_BASE_URL) or DEFAULT_BASE_URL
    if not base_url.startswith(("http://", "https://")):
        raise ConfigError(f"JIEAPI_BASE_URL 必须以 http(s):// 开头，当前值：{base_url!r}")

    caps_file = _env_str("CAPABILITIES_FILE")
    capabilities_file = Path(caps_file).expanduser().resolve() if caps_file else DEFAULT_CAPABILITIES_FILE

    return Settings(
        api_key=api_key,
        base_url=base_url,
        image_max_file_mb=_env_int("IMAGE_MAX_FILE_MB", 10),
        gateway_timeout=_env_float("GATEWAY_TIMEOUT", 120.0),
        models_cache_ttl=_env_float("MODELS_CACHE_TTL", 300.0),
        max_concurrency=_env_int("MCP_MAX_CONCURRENCY", 40),
        http_max_connections=_env_int("MCP_HTTP_MAX_CONNECTIONS", 100),
        http_max_keepalive=_env_int("MCP_HTTP_MAX_KEEPALIVE", 20),
        capabilities_file=capabilities_file,
    )


@dataclass(frozen=True)
class CapabilityMap:
    """模型名 → 能力集合 的映射（FR-F5 的数据源）。"""

    models: dict[str, frozenset[str]]

    def models_for(self, capability: str) -> list[str]:
        """返回具备指定能力的全部模型（排序稳定，便于错误提示）。"""
        return sorted(name for name, caps in self.models.items() if capability in caps)

    def supports(self, model: str, capability: str) -> bool:
        caps = self.models.get(model)
        return caps is not None and capability in caps


def load_capability_map(path: Path) -> CapabilityMap:
    """从 YAML 读取能力映射表。任何结构问题都抛 :class:`ConfigError`。"""
    if not path.exists():
        raise ConfigError(f"能力映射表不存在：{path}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"能力映射表 YAML 解析失败：{path}\n{exc}") from exc

    if raw is None:
        raise ConfigError(f"能力映射表为空：{path}")
    if not isinstance(raw, dict) or "models" not in raw:
        raise ConfigError(f"能力映射表缺少顶层 `models` 段：{path}")

    models_raw = raw["models"]
    if not isinstance(models_raw, dict) or not models_raw:
        raise ConfigError(f"能力映射表 `models` 段必须是非空映射：{path}")

    models: dict[str, frozenset[str]] = {}
    for model_name, spec in models_raw.items():
        if not isinstance(model_name, str) or not model_name.strip():
            raise ConfigError(f"能力映射表存在非法模型名：{model_name!r}")
        if isinstance(spec, list):
            caps = spec
        elif isinstance(spec, dict):
            caps = spec.get("capabilities")
        else:
            raise ConfigError(f"模型 {model_name!r} 的定义必须是列表或含 `capabilities` 的映射")
        if not isinstance(caps, list) or not caps:
            raise ConfigError(f"模型 {model_name!r} 未声明任何能力（capabilities）")
        models[model_name] = frozenset(str(c).strip() for c in caps if str(c).strip())
    return CapabilityMap(models=models)


class ConfigManager:
    """线程安全地持有当前配置，支持热重载（FR-F2）。"""

    def __init__(self, settings: Settings, capability_map: CapabilityMap) -> None:
        self._settings = settings
        self._capability_map = capability_map
        self._lock = threading.RLock()

    @classmethod
    def bootstrap(cls) -> "ConfigManager":
        settings = load_settings()
        capability_map = load_capability_map(settings.capabilities_file)
        return cls(settings, capability_map)

    @property
    def settings(self) -> Settings:
        with self._lock:
            return self._settings

    @property
    def capability_map(self) -> CapabilityMap:
        with self._lock:
            return self._capability_map

    def reload(self) -> dict[str, Any]:
        """重新读取环境变量与能力映射表；失败时保持旧配置不变。"""
        new_settings = load_settings()
        new_map = load_capability_map(new_settings.capabilities_file)
        with self._lock:
            self._settings = new_settings
            self._capability_map = new_map
        return {
            "models": len(new_map.models),
            "capabilities_file": str(new_settings.capabilities_file),
        }
