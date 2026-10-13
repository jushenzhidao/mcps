"""网关错误翻译（FR-F6）。

现实与 PRD 的偏差（2026-10-13 实测）：
* 网关对"模型无可用渠道"返回 **HTTP 503 + `code=model_not_found`**，而不是 PRD 假设的 404。
  ⇒ 判定必须优先看 **body 里的 code / message**，不能只看 status。
* 认证失败为 401/403；限流 429；上游 5xx。

所有分类结果都保留网关原始 message，供 LLM 客户端展示（FR-F6 末条）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

# new-api 在"模型没有渠道"时使用的错误码（实测见上）。
_MODEL_MISSING_CODES = frozenset({"model_not_found", "no_available_channel", "channel_not_found"})


def _parse_body(body: str | bytes | None) -> Any:
    if body is None:
        return None
    if isinstance(body, bytes):
        try:
            body = body.decode("utf-8", "replace")
        except Exception:  # pragma: no cover - 极端情况
            return None
    text = body.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return text


def extract_gateway_message(body: str | bytes | None) -> str | None:
    """尽力从网关响应体里取出人类可读的 message。"""
    data = _parse_body(body)
    if isinstance(data, str):
        return data or None
    if not isinstance(data, dict):
        return None
    err = data.get("error")
    if isinstance(err, dict):
        for key in ("message", "msg", "detail"):
            value = err.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    if isinstance(err, str) and err.strip():
        return err.strip()
    for key in ("message", "msg", "detail"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def extract_gateway_code(body: str | bytes | None) -> str | None:
    """从网关响应体里取出机器可读的 code（如 model_not_found）。"""
    data = _parse_body(body)
    if not isinstance(data, dict):
        return None
    err = data.get("error")
    if isinstance(err, dict):
        code = err.get("code") or err.get("type")
        if isinstance(code, str) and code.strip():
            return code.strip()
    code = data.get("code")
    if isinstance(code, str) and code.strip():
        return code.strip()
    return None


@dataclass
class GatewayError(RuntimeError):
    """带分类信息的网关异常。``message`` 面向用户，已含网关原文。"""

    message: str
    kind: str = "gateway"
    status_code: int | None = None
    code: str | None = None
    raw: str | None = None

    def __post_init__(self) -> None:
        super().__init__(self.message)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "status_code": self.status_code,
            "code": self.code,
            "message": self.message,
        }


def classify(
    status_code: int | None,
    body: str | bytes | None,
    *,
    timed_out: bool = False,
    network_error: str | None = None,
) -> GatewayError:
    """把一次失败的网关往返翻译成带指引的 :class:`GatewayError`。"""
    raw_message = extract_gateway_message(body)
    code = extract_gateway_code(body)
    suffix = f"（网关原文：{raw_message}）" if raw_message else ""

    if timed_out:
        return GatewayError(
            message=(
                "网关请求超时。该模型可能较慢（生图常见 30–60s），请稍后重试，"
                "或调大 GATEWAY_TIMEOUT 环境变量。" + suffix
            ),
            kind="timeout",
            code=code,
            raw=raw_message,
        )
    if network_error:
        return GatewayError(
            message=f"无法连接网关（{network_error}）。请检查网络与 JIEAPI_BASE_URL。" + suffix,
            kind="network",
            code=code,
            raw=raw_message,
        )

    if status_code in (401, 403):
        return GatewayError(
            message="网关鉴权失败：请检查 JIEAPI_KEY 是否正确、是否已过期。" + suffix,
            kind="auth",
            status_code=status_code,
            code=code,
            raw=raw_message,
        )

    if code in _MODEL_MISSING_CODES or (raw_message and "no available channel" in raw_message.lower()):
        return GatewayError(
            message=(
                "该模型在网关上没有可用渠道（可能未配置、已下线或未对本令牌开放）。"
                "请用 list_models 查看当前可用模型，或在 capabilities.yaml 中修正映射。" + suffix
            ),
            kind="model_unavailable",
            status_code=status_code,
            code=code,
            raw=raw_message,
        )

    if status_code == 404:
        return GatewayError(
            message="网关未找到该接口或模型（HTTP 404）。请确认 JIEAPI_BASE_URL 与模型名。" + suffix,
            kind="not_found",
            status_code=status_code,
            code=code,
            raw=raw_message,
        )
    if status_code == 429:
        return GatewayError(
            message="触发网关限流（HTTP 429），请稍后重试。" + suffix,
            kind="rate_limited",
            status_code=status_code,
            code=code,
            raw=raw_message,
        )
    if status_code is not None and 500 <= status_code < 600:
        return GatewayError(
            message="网关或上游服务返回错误（HTTP %d），请稍后重试。" % status_code + suffix,
            kind="upstream",
            status_code=status_code,
            code=code,
            raw=raw_message,
        )
    if status_code is not None and 400 <= status_code < 500:
        return GatewayError(
            message="网关拒绝了该请求（HTTP %d），请检查参数。" % status_code + suffix,
            kind="bad_request",
            status_code=status_code,
            code=code,
            raw=raw_message,
        )
    return GatewayError(
        message="网关请求失败。" + suffix,
        kind="gateway",
        status_code=status_code,
        code=code,
        raw=raw_message,
    )


@dataclass
class ModelNotAllowed(ValueError):
    """FR-F5：模型不在能力映射表 / 不具备该能力。"""

    model: str
    capability: str
    available: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.available:
            hint = "该能力下可用模型：" + "、".join(self.available)
        else:
            hint = f"当前能力映射表中没有任何模型声明了 `{self.capability}` 能力，请检查 capabilities.yaml。"
        self.message = f"模型 `{self.model}` 未被允许用于 `{self.capability}` 能力。{hint}"
        super().__init__(self.message)
