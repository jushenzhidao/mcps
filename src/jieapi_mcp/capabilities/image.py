"""图片生成能力（同步，一期主线）。

上游端点：``POST /v1/images/generations``

现实校准（2026-10-13 实测 flux-3-image）
---------------------------------------
* 返回是标准 OpenAI 形态 ``{"created":..,"data":[{"url":..}],"usage":{..}}``；
  ``revised_prompt`` 与真实 token 用量**可能缺失**（实测两者皆无/为零）——
  归一化按"有则透传、无则省略"，不臆造字段。
* 兼容三种历史形态：OpenAI ``data[]``、极简 ``{"url":..}``、``b64_json``。
"""

from __future__ import annotations

import base64
import mimetypes
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.types import ImageContent, TextContent

from ..context import AppContext
from ..errors import GatewayError
from .base import BaseCapability, ExecutionMode

ENDPOINT = "/v1/images/generations"

_LOCAL_PREFIXES = ("http://", "https://", "data:")


class ImageCapability(BaseCapability):
    name = "image"
    execution_mode = ExecutionMode.SYNC
    endpoint = ENDPOINT

    def register_tools(self, mcp: MCPServer, ctx: AppContext) -> None:
        @mcp.tool(
            name="generate_image",
            description=(
                "生成或编辑图片（同步返回）。传 prompt 即可文生图；同时传 image 参考图则为"
                "图生图/图像编辑/多图合成。\n"
                "- model：生图模型，需在 capabilities.yaml 中声明 image 能力（如 flux-3-image）。\n"
                "- prompt：画面描述或编辑指令，例如“给这张图加水彩效果”。\n"
                "- image：参考图数组，元素可为公网 URL、data URI，或本机文件绝对路径"
                "（本机路径由服务端自动读入并转 data URI；单文件上限见 IMAGE_MAX_FILE_MB）。\n"
                "- size：尺寸，如 auto 或 1024x1024；各模型支持的表可能不同，透传给网关。\n"
                "- output_format：输出格式，如 png。n：生成张数。\n"
                "- response_format：url（默认，返回图片链接，**链接有有效期，过期需重新生成**）"
                "或 b64_json（内联返回，客户端可直接显示）。\n"
                "注意：带 image 输入的调用通常计费更高；一次调用即产生网关费用。"
            ),
        )
        def generate_image(
            model: str,
            prompt: str,
            image: list[str] | None = None,
            size: str | None = None,
            output_format: str | None = None,
            n: int | None = None,
            response_format: str | None = None,
        ) -> list[TextContent | ImageContent]:
            payload = self.build_payload(
                ctx,
                model=model,
                prompt=prompt,
                image=image,
                size=size,
                output_format=output_format,
                n=n,
                response_format=response_format,
            )
            return render(ctx.transport.post_json(ENDPOINT, payload) or {})

    # 便于单测直接调用（不经 MCP 装饰器）
    def build_payload(
        self,
        ctx: AppContext,
        *,
        model: str,
        prompt: str,
        image: list[str] | None = None,
        size: str | None = None,
        output_format: str | None = None,
        n: int | None = None,
        response_format: str | None = None,
    ) -> dict[str, Any]:
        self.guard_model(ctx, model, self.name)
        max_bytes = ctx.config.settings.max_upload_bytes
        payload: dict[str, Any] = {"model": model, "prompt": prompt}
        if image:
            payload["image"] = [to_data_uri(ref, max_bytes) for ref in image]
        if size:
            payload["size"] = size
        if output_format:
            payload["output_format"] = output_format
        if n is not None:
            payload["n"] = n
        if response_format:
            payload["response_format"] = response_format
        return payload


# ---- 输入处理 -----------------------------------------------------------
def to_data_uri(ref: str, max_bytes: int) -> str:
    """把 http(s)/data URI 原样透传；本机路径读入并转 data URI。"""
    ref = ref.strip()
    if not ref:
        raise GatewayError(message="image 参数包含空字符串。", kind="bad_input")
    if ref.startswith(_LOCAL_PREFIXES):
        return ref

    path = Path(ref).expanduser()
    if not path.exists() or not path.is_file():
        raise GatewayError(
            message=f"参考图本地路径不存在或不是文件：{ref}。请改用公网 URL 或确认路径。",
            kind="bad_input",
        )
    size = path.stat().st_size
    if size > max_bytes:
        raise GatewayError(
            message=(
                f"参考图 {path.name} 大小 {size / 1024 / 1024:.1f}MB，超过上限 "
                f"{max_bytes / 1024 / 1024:.0f}MB（IMAGE_MAX_FILE_MB）。请改用公网 URL。"
            ),
            kind="bad_input",
        )
    if size == 0:
        raise GatewayError(message=f"参考图文件为空：{ref}。", kind="bad_input")

    mime = mimetypes.guess_type(path.name)[0] or "image/png"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


# ---- 输出处理 -----------------------------------------------------------
def normalize_items(result: dict[str, Any]) -> list[dict[str, Any]]:
    """把网关返回归一到 ``[{"url"|"b64_json", "revised_prompt"?}]``。

    兼容：OpenAI ``data[]`` / 极简 ``{"url":..}`` / 顶层 ``b64_json``。
    """
    items: list[dict[str, Any]] = []
    data = result.get("data")
    if isinstance(data, list):
        items.extend(e for e in data if isinstance(e, dict))
    elif isinstance(data, dict):
        items.append(data)

    if not items and (isinstance(result.get("url"), str) or isinstance(result.get("b64_json"), str)):
        items.append(result)

    normalized: list[dict[str, Any]] = []
    for entry in items:
        item: dict[str, Any] = {}
        if isinstance(entry.get("url"), str) and entry["url"]:
            item["url"] = entry["url"]
        if isinstance(entry.get("b64_json"), str) and entry["b64_json"]:
            item["b64_json"] = entry["b64_json"]
        if isinstance(entry.get("revised_prompt"), str) and entry["revised_prompt"]:
            item["revised_prompt"] = entry["revised_prompt"]
        if item:
            normalized.append(item)
    return normalized


def render(result: dict[str, Any]) -> list[TextContent | ImageContent]:
    items = normalize_items(result)
    if not items:
        raw = result.get("error") or result
        raise GatewayError(
            message=f"网关未返回任何图片数据（data 为空）。网关原始响应：{raw}",
            kind="empty_result",
        )

    content: list[TextContent | ImageContent] = []
    total = len(items)
    for index, item in enumerate(items, start=1):
        prefix = f"[{index}/{total}] " if total > 1 else ""
        if "b64_json" in item:
            fmt = guess_format(item["b64_json"])
            content.append(ImageContent(type="image", data=item["b64_json"], mimeType=f"image/{fmt}"))
            content.append(TextContent(type="text", text=f"{prefix}（内联图片已返回）"))
        elif "url" in item:
            content.append(
                TextContent(
                    type="text",
                    text=f"{prefix}图片链接（有效期由网关决定，过期需重新生成）：{item['url']}",
                )
            )
        if "revised_prompt" in item:
            content.append(TextContent(type="text", text=f"{prefix}修订后提示词：{item['revised_prompt']}"))

    usage = result.get("usage")
    if isinstance(usage, dict) and any(v for v in usage.values()):
        content.append(TextContent(type="text", text=f"用量：{usage}"))
    return content


def guess_format(b64: str) -> str:
    """从 base64 前缀猜图片格式，猜不出默认 png。"""
    try:
        head = base64.b64decode(b64[:24] + "==")
    except Exception:  # pragma: no cover
        return "png"
    if head.startswith(b"\x89PNG"):
        return "png"
    if head.startswith(b"\xff\xd8"):
        return "jpg"
    if head[:4] == b"RIFF":
        return "webp"
    if head[:3] == b"GIF":
        return "gif"
    return "png"
