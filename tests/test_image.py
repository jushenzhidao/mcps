"""图片能力测试（输入转换 + 三种响应形态归一化）。全部零网络。"""

from __future__ import annotations

import base64
from pathlib import Path

import pytest
from mcp.types import TextContent

from jieapi_mcp.capabilities.image import (
    ImageCapability,
    guess_format,
    normalize_items,
    render,
    to_data_uri,
)
from jieapi_mcp.errors import GatewayError, ModelNotAllowed

from .conftest import json_handler, make_context

PNG_B64 = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32).decode()


def test_normalize_openai_data_url():
    assert normalize_items({"data": [{"url": "https://img/1.png"}]}) == [{"url": "https://img/1.png"}]


def test_normalize_openai_data_b64_with_revised():
    items = normalize_items({"data": [{"b64_json": PNG_B64, "revised_prompt": "a cat"}]})
    assert items[0]["b64_json"] == PNG_B64
    assert items[0]["revised_prompt"] == "a cat"


def test_normalize_minimal_url_envelope():
    """image-adapter 档案里见过的极简形态 {"url": ...}。"""
    assert normalize_items({"url": "https://img/x.png"}) == [{"url": "https://img/x.png"}]


def test_normalize_empty():
    assert normalize_items({"data": []}) == []
    assert normalize_items({}) == []


def test_guess_format_png():
    assert guess_format(PNG_B64) == "png"


def test_data_uri_passthrough():
    assert to_data_uri("https://x/y.png", 10) == "https://x/y.png"
    assert to_data_uri("data:image/png;base64,AAAA", 10) == "data:image/png;base64,AAAA"


def test_data_uri_local_file(tmp_path: Path):
    p = tmp_path / "a.png"
    p.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 8)
    assert to_data_uri(str(p), 10 * 1024 * 1024).startswith("data:image/png;base64,")


def test_local_file_missing():
    with pytest.raises(GatewayError) as ei:
        to_data_uri("/no/such/file.png", 10 * 1024 * 1024)
    assert ei.value.kind == "bad_input"


def test_local_file_too_large(tmp_path: Path):
    p = tmp_path / "big.png"
    p.write_bytes(b"\x00" * 2048)
    with pytest.raises(GatewayError) as ei:
        to_data_uri(str(p), 1024)  # 上限 1KB
    assert ei.value.kind == "bad_input"
    assert "IMAGE_MAX_FILE_MB" in ei.value.message


def test_empty_ref():
    with pytest.raises(GatewayError) as ei:
        to_data_uri("   ", 1024)
    assert ei.value.kind == "bad_input"


def test_build_payload_gating():
    ctx = make_context(json_handler({}))
    cap = ImageCapability()
    with pytest.raises(ModelNotAllowed):
        cap.build_payload(ctx, model="ghost", prompt="x")
    payload = cap.build_payload(
        ctx, model="flux-3-image", prompt="x", size="1024x1024", n=2, response_format="url"
    )
    assert payload == {
        "model": "flux-3-image",
        "prompt": "x",
        "size": "1024x1024",
        "n": 2,
        "response_format": "url",
    }


def test_render_url_mentions_expiry():
    content = render({"data": [{"url": "https://img/1.png"}]})
    assert any(isinstance(c, TextContent) and "有效期" in c.text for c in content)


def test_render_b64_returns_image_content():
    from mcp.types import ImageContent

    content = render({"data": [{"b64_json": PNG_B64, "revised_prompt": "cat"}]})
    assert any(isinstance(c, ImageContent) for c in content)
    assert any(isinstance(c, TextContent) and "cat" in c.text for c in content)


def test_render_empty_raises():
    with pytest.raises(GatewayError) as ei:
        render({"data": []})
    assert ei.value.kind == "empty_result"
