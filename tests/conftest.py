"""共享夹具：全部零网络。"""

from __future__ import annotations

import json

import httpx
import pytest

from jieapi_mcp.config import CapabilityMap, ConfigManager, Settings
from jieapi_mcp.context import AppContext
from jieapi_mcp.registry import CapabilityGuard, ModelCatalog
from jieapi_mcp.tasks import TaskStore
from jieapi_mcp.transport import Transport

CAPS = {
    "flux-3-image": ["image", "image_to_image"],
    "text-model": ["chat"],
    "video-model": ["video"],
    "minimax-h3-480p": ["video"],
    "sample-model": ["sample_task"],
}


def make_settings(**overrides) -> Settings:
    base = dict(
        api_key="sk-test",
        base_url="https://example.test",
        image_max_file_mb=10,
        gateway_timeout=120.0,
        models_cache_ttl=300.0,
    )
    base.update(overrides)
    return Settings(**base)


def make_context(handler, **settings_overrides) -> AppContext:
    """用 MockTransport 构造一个不发真实请求的上下文。"""
    settings = make_settings(**settings_overrides)
    transport = Transport(settings, transport=httpx.MockTransport(handler))
    config = ConfigManager(settings, CapabilityMap(models={k: frozenset(v) for k, v in CAPS.items()}))
    return AppContext(
        config=config,
        transport=transport,
        catalog=ModelCatalog(transport, ttl=settings.models_cache_ttl),
        guard=CapabilityGuard(config),
        tasks=TaskStore(),
    )


def json_handler(mapping):
    """按 (method, path) 返回预置响应；值可为 (status, payload) 或 callable。"""

    def handler(request: httpx.Request) -> httpx.Response:
        spec = mapping.get((request.method, request.url.path))
        if spec is None:
            return httpx.Response(404, json={"error": {"message": "no route", "code": "not_found"}})
        if callable(spec):
            return spec(request)
        status, payload = spec
        return httpx.Response(status, json=payload)

    return handler


def body_json(request: httpx.Request) -> dict:
    return json.loads(request.content.decode())


@pytest.fixture
def ok_models():
    return {("GET", "/v1/models"): (200, {"data": [{"id": "flux-3-image"}, {"id": "text-model"}]})}
