"""通用 HTTP 传输层：框架**唯一**的对外网络出口，不感知任何具体能力。

设计要点
--------
* **能力无关**：只提供 ``get_json`` / ``post_json`` 这类通用方法。
  能力插件自带端点声明（``endpoint`` / ``submit_path`` …），加能力无需改动本文件。
* **鉴权集中**：令牌只在此处注入 ``Authorization`` 头，绝不写日志（FR-F3）。
* **错误集中**：所有失败经 :mod:`jieapi_mcp.errors` 翻译后抛出（FR-F6）。
* ``trust_env=False``：避免本机 HTTP_PROXY 劫持对外请求、吞掉带 Authorization 的 POST。
"""

from __future__ import annotations

from typing import Any

import httpx

from .config import Settings
from .errors import GatewayError, classify


class Transport:
    """new-api（OpenAI 兼容）网关的通用 HTTP 客户端。"""

    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._settings = settings
        self._client = httpx.Client(
            base_url=settings.api_base,
            timeout=settings.gateway_timeout,
            headers={
                "Authorization": f"Bearer {settings.api_key}",
                "Accept": "application/json",
                "User-Agent": "jieapi-mcp/0.1",
            },
            limits=httpx.Limits(
                max_connections=settings.http_max_connections,
                max_keepalive_connections=settings.http_max_keepalive,
            ),
            trust_env=False,
            transport=transport,
        )

    # ---- 生命周期 -------------------------------------------------------
    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "Transport":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ---- 通用请求 -------------------------------------------------------
    def request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        try:
            response = self._client.request(method, path, json=json_body, params=params)
        except httpx.TimeoutException as exc:
            raise classify(None, None, timed_out=True) from exc
        except httpx.HTTPError as exc:
            raise classify(None, None, network_error=type(exc).__name__) from exc

        if response.status_code >= 400:
            raise classify(response.status_code, response.content)

        if not response.content:
            return None
        try:
            return response.json()
        except ValueError as exc:
            raise GatewayError(
                message="网关返回了非 JSON 响应，无法解析。",
                kind="bad_response",
                status_code=response.status_code,
                raw=response.text[:500],
            ) from exc

    def get_json(self, path: str, *, params: dict[str, Any] | None = None) -> Any:
        return self.request("GET", path, params=params)

    def post_json(self, path: str, json_body: dict[str, Any] | None = None) -> Any:
        return self.request("POST", path, json_body=json_body)

    # ---- 框架用端点 -----------------------------------------------------
    def list_models(self) -> list[dict[str, Any]]:
        """``GET /v1/models``——框架级端点，供模型目录缓存使用（FR-F4）。"""
        payload = self.get_json("/v1/models")
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, list):
            raise GatewayError(message="网关 /v1/models 响应缺少 data 列表。", kind="bad_response")
        return [m for m in data if isinstance(m, dict)]
