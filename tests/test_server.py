"""Server 装配 + 能力自动发现 + 端到端（假上游）测试。全部零网络。"""

from __future__ import annotations

import base64

from mcp.types import TextContent

from jieapi_mcp.capabilities import discover_capabilities
from jieapi_mcp.capabilities.image import ImageCapability, render
from jieapi_mcp.server import build_server

from .conftest import body_json, json_handler, make_context

PNG_B64 = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32).decode()


def _image_routes(captured):
    def gen(request):
        captured.append(body_json(request))
        return __import__("httpx").Response(
            200,
            json={"created": 1, "data": [{"url": "https://img.example/1.png"}], "usage": {}},
        )

    return {("GET", "/v1/models"): (200, {"data": [{"id": "flux-3-image"}]}),
            ("POST", "/v1/images/generations"): gen}


# ---- 自动发现 -----------------------------------------------------------
def test_discover_finds_image():
    names = [c.name for c in discover_capabilities()]
    assert "image" in names


def test_discover_skips_underscore_template():
    """_task_template.py 是模板，不应被自动发现。"""
    names = [c.name for c in discover_capabilities()]
    assert "sample_task" not in names


# ---- 端到端 -------------------------------------------------------------
def test_generate_image_end_to_end():
    captured: list[dict] = []
    ctx = make_context(json_handler(_image_routes(captured)))
    cap = ImageCapability()

    payload = cap.build_payload(ctx, model="flux-3-image", prompt="a red apple")
    content = render(ctx.transport.post_json(ImageCapability.endpoint, payload) or {})

    assert captured[0]["model"] == "flux-3-image"
    assert any(isinstance(c, TextContent) and "img.example" in c.text for c in content)


# ---- 装配 ---------------------------------------------------------------
def test_build_server_registers_all_tools():
    ctx = make_context(json_handler({("GET", "/v1/models"): (200, {"data": []})}))
    server = build_server(ctx)
    names = {t.name for t in server._tool_manager.list_tools()}
    assert {"generate_image", "list_models", "reload_config"} <= names
    # 任务型通用工具
    assert {"get_task", "list_tasks", "cancel_task"} <= names


def test_build_server_fills_capability_registry():
    ctx = make_context(json_handler({}))
    build_server(ctx)
    assert "image" in ctx.capabilities
