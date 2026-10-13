"""视频生成能力（任务型异步）。

上游：jieapi 网关的**聚合**视频任务端点（家族无关）
--------------------------------------------------
* 提交：``POST /v3/contents/generations/tasks``
* 查询：``GET  /v3/contents/generations/tasks/{task_id}``
* 取消：**无**

为什么是「聚合端点」（2026-10-14 探测实测）
-------------------------------------------
Apifox 里各视频家族文档写的路径前缀互不一致（seedance 写 ``/doubao/api/v3/...``、
qwen 写 ``/videos/api/v3/...``、使用文档写 ``{base_url}/{plug_in}/api/v3/...``），
但实测三条前缀对 GET/POST **返回完全一致的响应**：

    GET  /v3/contents/generations/tasks/probe     → 404 {"code":"task_not_found"}
    GET  /doubao/api/v3/contents/generations/tasks/probe → 同上，逐字一致
    GET  /videos/api/v3/contents/generations/tasks/probe → 同上，逐字一致
    POST 同上三条                                   → 400 {"code":"invalid_request",
                                                        "message":"model \"X\" is not served by this plugin"}

⇒ 上游由**同一个视频插件**服务，``{plug_in}`` 前缀被忽略。故本能力只用 ``/v3/...`` 一条路径，
  minimax-h3 / seedance / qwen / wan 等家族靠 ``model`` 区分，**无需按家族分叉路径**。

判别器：路径写错时上游不会给 404，而是回退到 new-api 的 **HTML 前端页**（HTTP 200）
⇒ 症状是「网关返回了非 JSON 响应」而不是「404」。排查时可对照这一点。

其他实测事实
------------
* 提交响应只有 ``{"id": "task_..." | "cgt-..."}``；查询响应形如::

      {"content": {"video_url": "https://.../v/xxx.mp4"}, "created_at": 1791880167,
       "duration": 5, "error": null, "id": "task_...", "ratio": "16:9",
       "status": "succeeded", "updated_at": 1791880221}

* **没有取消端点**：``DELETE`` / ``POST`` 到 ``/v3/contents/generations/tasks/{id}`` 均 **405**。
  因此 ``cancel_path = None``——``cancel_task`` 只把**本地**任务标记为已取消，
  上游任务仍在继续跑、**可能继续计费**（工具描述里已如实告知）。
* 模型校验先于建任务：给一个上游不服务的模型，会在建任务前返回
  ``model "X" is not served by this plugin``（所以可以零成本探测模型归属）。
"""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import MCPServer

from ..context import AppContext
from ..errors import GatewayError
from ..tasks import TaskStatus
from .base import ExecutionMode, TaskCapability
from .image import to_data_uri

SUBMIT_PATH = "/v3/contents/generations/tasks"
POLL_PATH = "/v3/contents/generations/tasks/{task_id}"

#: 上游状态词 → 本地 TaskStatus。上游实际用词可能随版本变，故按语义分组、宽松匹配。
_QUEUED_WORDS = {"queued", "pending", "submitted", "created", "accepted", "waiting", "in_queue", "inqueue"}
_RUNNING_WORDS = {"running", "processing", "in_progress", "in-progress", "executing", "generating"}
_SUCCEEDED_WORDS = {"succeeded", "success", "completed", "done", "finished"}
_FAILED_WORDS = {"failed", "error", "failure"}
_EXPIRED_WORDS = {"expired", "timeout", "timed_out"}
_CANCELLED_WORDS = {"cancelled", "canceled", "cancelled_by_user"}

_REMOTE_PREFIXES = ("http://", "https://")

#: 参考图统一用上游 schema 里声明的 role（首帧/尾帧同族；seedance 的 first_frame/last_frame
#: 属于「示例」而非 schema 枚举，需要时走 content 原生数组）。
DEFAULT_IMAGE_ROLE = "reference_image"


class VideoCapability(TaskCapability):
    """文/图生视频。提交立刻返回 task_id，进度用框架通用工具 ``get_task`` 查询。"""

    name = "video"
    execution_mode = ExecutionMode.TASK
    endpoint = SUBMIT_PATH

    submit_path = SUBMIT_PATH
    poll_path = POLL_PATH
    #: 上游无取消端点（DELETE/POST 均 405，实测）⇒ 取消只在本地生效
    cancel_path = None

    # ---- 钩子 1：构造提交体 -------------------------------------------------
    def build_submit_body(self, params: dict[str, Any]) -> dict[str, Any]:
        """把调用参数装配成上游请求体（纯函数，不碰 ctx：素材编码在工具层完成）。"""
        raw_content = params.get("content")
        if raw_content is not None:
            ergonomic = any(
                params.get(key) not in (None, "", [], {})
                for key in ("prompt", "image", "video", "audio")
            )
            if ergonomic:
                raise GatewayError(
                    message="content 与 prompt/image/video/audio 是互斥的两种写法，请二选一。",
                    kind="bad_input",
                )
            items = [dict(item) for item in raw_content]
        else:
            items = build_content(
                prompt=params.get("prompt"),
                image=params.get("image"),
                video=params.get("video"),
                audio=params.get("audio"),
            )

        if not items:
            raise GatewayError(
                message=(
                    "视频任务必须提供内容：prompt（文本提示词）、image（参考图）、"
                    "video（参考视频）、audio（参考音频）或 content（原生数组）至少其一。"
                ),
                kind="bad_input",
            )

        body: dict[str, Any] = {"model": params.get("model"), "content": items}
        for key in ("duration", "resolution", "ratio", "generate_audio", "watermark"):
            value = params.get(key)
            if value is not None:
                body[key] = value
        return body

    # ---- 钩子 2：解析提交响应 -----------------------------------------------
    def read_submit(self, response: Any) -> dict[str, Any]:
        response = response or {}
        upstream_id = response.get("id") or response.get("task_id")
        if not upstream_id:
            # 上游 HTTP 200 却没给 id ⇒ 无法后续轮询，如实落成失败并保留原始响应便于排查
            return {
                "status": TaskStatus.FAILED,
                "error": f"上游未返回任务 id，无法查询进度。上游原始响应：{response}",
            }
        return {"upstream_id": str(upstream_id), "status": TaskStatus.QUEUED}

    # ---- 钩子 3：解析轮询响应 -----------------------------------------------
    def read_poll(self, response: Any) -> dict[str, Any]:
        response = response or {}
        raw = str(response.get("status") or "").strip().lower()
        artifact = extract_artifact(response)

        # 完成判据：状态词成功 **且** 产物（video_url）非空 —— 只看状态词会把
        # 「已产出但还没落 URL」误判为成功，让调用方拿到空结果。
        if raw in _SUCCEEDED_WORDS:
            if artifact:
                return {"status": TaskStatus.SUCCEEDED, "result": artifact}
            return {"status": TaskStatus.RUNNING}
        if raw in _FAILED_WORDS:
            return {"status": TaskStatus.FAILED, "error": error_text(response) or f"上游状态：{raw}"}
        if raw in _EXPIRED_WORDS:
            return {"status": TaskStatus.EXPIRED, "error": error_text(response) or f"上游状态：{raw}"}
        if raw in _CANCELLED_WORDS:
            return {"status": TaskStatus.CANCELLED}
        if raw in _QUEUED_WORDS:
            return {"status": TaskStatus.QUEUED}
        # running / 未知 / 缺省：一律当作「进行中」，绝不误判终态
        return {"status": TaskStatus.RUNNING}

    # ---- 提交工具：立刻返回 task_id ----------------------------------------
    def register_tools(self, mcp: MCPServer, ctx: AppContext) -> None:
        @mcp.tool(
            name="create_video",
            description=(
                "提交一个视频生成任务（**异步**）。立即返回 task_id，不等上游出片；"
                "随后用 get_task 查询进度（可传 wait_seconds 做≤60 秒的短等待）。\n"
                "- model：视频模型，需在 capabilities.yaml 中声明 video 能力"
                "（如 minimax-h3-480p / minimax-h3-720p / doubao-seedance-2-0-260128）。\n"
                "- prompt：画面描述（文本提示词）。\n"
                "- image：参考图数组（首帧/参考图），元素可为公网 URL 或本机文件绝对路径"
                "（本机路径由服务端读入转 data URI，上限 IMAGE_MAX_FILE_MB）。\n"
                "- video / audio：参考视频 / 参考音频，**必须是公网 http(s) URL**（上游需自行下载）。\n"
                "- duration：时长（秒），如 5；resolution：480p/720p/1080p；ratio：16:9 / 9:16 / 1:1 等。\n"
                "- generate_audio：是否生成同步音频；watermark：是否加水印。\n"
                "- content：原生 content 数组（多模态精调，与上述 prompt/image/video/audio 互斥）。\n"
                "注意：提交即在上游受理并**产生费用**；上游**不支持取消**，cancel_task 只停止本地跟踪；"
                "结果中的视频链接有有效期，过期需重新生成。"
            ),
        )
        def create_video(
            model: str,
            prompt: str | None = None,
            image: list[str] | None = None,
            video: str | None = None,
            audio: list[str] | None = None,
            duration: int | None = None,
            resolution: str | None = None,
            ratio: str | None = None,
            generate_audio: bool | None = None,
            watermark: bool | None = None,
            content: list[dict[str, Any]] | None = None,
        ) -> str:
            params: dict[str, Any] = {}
            if prompt is not None:
                params["prompt"] = prompt
            if image:
                max_bytes = ctx.config.settings.max_upload_bytes
                params["image"] = [to_data_uri(ref, max_bytes) for ref in image]
            if video is not None:
                params["video"] = video
            if audio:
                params["audio"] = list(audio)
            for key, value in (
                ("duration", duration),
                ("resolution", resolution),
                ("ratio", ratio),
                ("generate_audio", generate_audio),
                ("watermark", watermark),
            ):
                if value is not None:
                    params[key] = value
            if content is not None:
                params["content"] = list(content)

            task = self.submit(ctx, model=model, params=params)

            lines = [f"已提交视频任务：task_id={task.id}，状态={task.status.value}。"]
            if task.upstream_id:
                lines.append(f"上游任务 id：{task.upstream_id}。")
            if task.error:
                lines.append(f"⚠️ 上游返回错误：{task.error}")
            else:
                lines.append("用 get_task 查询进度（可传 wait_seconds 做短等待，上限 60 秒）。")
            lines.append("注意：上游无取消端点，cancel_task 仅停止本地跟踪；视频链接有有效期。")
            return "\n".join(lines)


# ---- 输入装配 -----------------------------------------------------------
def build_content(
    *,
    prompt: str | None = None,
    image: list[str] | None = None,
    video: str | None = None,
    audio: list[str] | None = None,
) -> list[dict[str, Any]]:
    """按上游 schema 装配 ``content`` 数组（文本 + 首帧/参考图 + 参考视频 + 参考音频）。"""
    items: list[dict[str, Any]] = []
    if prompt and prompt.strip():
        items.append({"type": "text", "text": prompt})
    for ref in image or []:
        items.append(
            {
                "type": "image_url",
                "image_url": {"url": _require_remote_or_data(ref, "image")},
                "role": DEFAULT_IMAGE_ROLE,
            }
        )
    if video:
        items.append(
            {
                "type": "video_url",
                "video_url": {"url": _require_remote(video, "video")},
                "role": "reference_video",
            }
        )
    for ref in audio or []:
        items.append(
            {
                "type": "audio_url",
                "audio_url": {"url": _require_remote(ref, "audio")},
                "role": "reference_audio",
            }
        )
    return items


def _require_remote(ref: str, field: str) -> str:
    value = (ref or "").strip()
    if not value.startswith(_REMOTE_PREFIXES):
        raise GatewayError(
            message=(
                f"{field} 必须是公网 http(s) URL（上游需自行下载该素材），"
                f"不接受本机路径或 data URI。当前值：{value!r}"
            ),
            kind="bad_input",
        )
    return value


def _require_remote_or_data(ref: str, field: str) -> str:
    """参考图允许公网 URL 或 data URI（本机路径已在工具层编码为 data URI）。"""
    value = (ref or "").strip()
    if not value:
        raise GatewayError(message=f"{field} 参数包含空字符串。", kind="bad_input")
    if not (value.startswith(_REMOTE_PREFIXES) or value.startswith("data:")):
        raise GatewayError(
            message=f"{field} 必须是公网 http(s) URL 或 data URI。当前值：{value!r}",
            kind="bad_input",
        )
    return value


# ---- 输出归一化 ---------------------------------------------------------
def extract_artifact(response: dict[str, Any]) -> dict[str, Any] | None:
    """从查询响应里取出产物；无 ``video_url`` 视为「还没好」。"""
    video_url: str | None = None
    content = response.get("content")
    if isinstance(content, dict):
        for key in ("video_url", "url"):
            candidate = content.get(key)
            if isinstance(candidate, str) and candidate.strip():
                video_url = candidate.strip()
                break
    elif isinstance(content, str) and content.strip():
        video_url = content.strip()
    if video_url is None and isinstance(response.get("video_url"), str) and response["video_url"].strip():
        video_url = response["video_url"].strip()
    if video_url is None:
        return None

    artifact: dict[str, Any] = {"video_url": video_url}
    for key in ("id", "duration", "ratio", "created_at", "updated_at"):
        if response.get(key) is not None:
            artifact[key] = response[key]
    return artifact


def error_text(response: dict[str, Any]) -> str | None:
    """尽力取出人类可读的错误说明。"""
    err = response.get("error")
    if isinstance(err, str) and err.strip():
        return err.strip()
    if isinstance(err, dict):
        for key in ("message", "msg", "detail", "code"):
            value = err.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


__all__ = [
    "VideoCapability",
    "build_content",
    "extract_artifact",
    "error_text",
    "SUBMIT_PATH",
    "POLL_PATH",
]
