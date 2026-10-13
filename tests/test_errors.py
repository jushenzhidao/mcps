"""错误翻译测试（FR-F6）——注意 503+model_not_found 的现实修正。"""

from __future__ import annotations

from jieapi_mcp.errors import (
    GatewayError,
    ModelNotAllowed,
    classify,
    extract_gateway_code,
    extract_gateway_message,
)


def test_extract_message_from_new_api_envelope():
    body = (
        '{"error":{"code":"model_not_found","message":"No available channel for model X '
        'under group auto (distributor) (request id: abc)","type":"new_api_error"}}'
    )
    assert "No available channel" in extract_gateway_message(body)
    assert extract_gateway_code(body) == "model_not_found"


def test_model_not_found_is_model_unavailable_even_on_503():
    """实测：网关用 503 表示模型无渠道，而不是 404。"""
    body = '{"error":{"code":"model_not_found","message":"No available channel for model flux-9"}}'
    err = classify(503, body)
    assert err.kind == "model_unavailable"
    assert "list_models" in err.message
    assert "No available channel" in err.message  # 原文透传


def test_auth_401():
    err = classify(401, '{"error":{"message":"invalid token"}}')
    assert err.kind == "auth"
    assert "JIEAPI_KEY" in err.message
    assert "invalid token" in err.message


def test_rate_limited_429():
    err = classify(429, None)
    assert err.kind == "rate_limited"


def test_upstream_5xx_keeps_original():
    err = classify(500, '{"error":{"message":"internal boom"}}')
    assert err.kind == "upstream"
    assert "internal boom" in err.message


def test_timeout_mentions_gateway_timeout():
    err = classify(None, None, timed_out=True)
    assert err.kind == "timeout"
    assert "GATEWAY_TIMEOUT" in err.message


def test_network_error():
    err = classify(None, None, network_error="ConnectError")
    assert err.kind == "network"
    assert "ConnectError" in err.message


def test_model_not_allowed_lists_available():
    err = ModelNotAllowed(model="ghost", capability="image", available=["flux-3-image", "dall-e-3"])
    assert "flux-3-image" in err.message
    assert "dall-e-3" in err.message


def test_gateway_error_to_dict():
    err = GatewayError(message="x", kind="auth", status_code=401, code="bad")
    assert err.to_dict()["kind"] == "auth"
    assert isinstance(err, RuntimeError)
