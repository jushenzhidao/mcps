"""视频能力测试（content 装配 + 提交/轮询解析 + 端到端编排）。全部零网络。"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from jieapi_mcp.capabilities import discover_capabilities
from jieapi_mcp.capabilities.video import (
    POLL_PATH,
    SUBMIT_PATH,
    VideoCapability,
    build_content,
    extract_artifact,
)
from jieapi_mcp.errors import GatewayError, ModelNotAllowed
from jieapi_mcp.tasks import TaskStatus

from .conftest import body_json, json_handler, make_context


# ---- content 装配 --------------------------------------------------------
def test_build_content_text_only():
    assert build_content(prompt="一只猫") == [{"type": "text", "text": "一只猫"}]


def test_build_content_image_uses_documented_role():
    items = build_content(image=["https://img/1.png"])
    assert items == [
        {
            "type": "image_url",
            "image_url": {"url": "https://img/1.png"},
            "role": "reference_image",
        }
    ]


def test_build_content_image_accepts_data_uri():
    items = build_content(image=["data:image/png;base64,AAAA"])
    assert items[0]["image_url"]["url"] == "data:image/png;base64,AAAA"


def test_build_content_full_multimodal():
    items = build_content(
        prompt="动起来",
        image=["https://img/1.png"],
        video="https://v/ref.mp4",
        audio=["https://a/bgm.mp3"],
    )
    kinds = [i["type"] for i in items]
    assert kinds == ["text", "image_url", "video_url", "audio_url"]
    assert items[2]["role"] == "reference_video"
    assert items[3]["role"] == "reference_audio"


def test_build_content_blank_prompt_skipped():
    assert build_content(prompt="   ", image=["https://img/1.png"])[0]["type"] == "image_url"


@pytest.mark.parametrize("field", ["video", "audio"])
def test_local_path_rejected_for_media(field):
    """视频/音频必须是公网 URL（上游自行下载），本机路径不能转 data URI。"""
    kwargs = {field: ["/tmp/x.mp4"] if field == "audio" else "/tmp/x.mp4"}
    with pytest.raises(GatewayError) as ei:
        build_content(**kwargs)
    assert ei.value.kind == "bad_input"
    assert "http(s) URL" in ei.value.message


def test_image_local_path_rejected_outside_tool(tmp_path: Path):
    """未经工具层编码的裸本机路径（非 URL / 非 data URI）应被拒绝，而不是静默提交。"""
    p = tmp_path / "a.png"
    p.write_bytes(b"\x89PNG\r\n\x1a\n")
    with pytest.raises(GatewayError) as ei:
        build_content(image=[str(p)])
    assert ei.value.kind == "bad_input"


def test_empty_image_ref_rejected():
    with pytest.raises(GatewayError) as ei:
        build_content(image=["   "])
    assert ei.value.kind == "bad_input"


# ---- 提交体 --------------------------------------------------------------
def test_build_submit_body_passes_optional_fields():
    cap = VideoCapability()
    body = cap.build_submit_body(
        {
            "model": "minimax-h3-480p",
            "prompt": "猫",
            "duration": 5,
            "resolution": "480p",
            "ratio": "16:9",
            "generate_audio": True,
            "watermark": False,
        }
    )
    assert body == {
        "model": "minimax-h3-480p",
        "content": [{"type": "text", "text": "猫"}],
        "duration": 5,
        "resolution": "480p",
        "ratio": "16:9",
        "generate_audio": True,
        "watermark": False,
    }


def test_build_submit_body_omits_none_fields():
    body = VideoCapability().build_submit_body({"model": "m", "prompt": "x", "duration": None})
    assert "duration" not in body


def test_build_submit_body_raw_content_passthrough():
    raw = [{"type": "image_url", "image_url": {"url": "https://i/1.png"}, "role": "first_frame"}]
    body = VideoCapability().build_submit_body({"model": "m", "content": raw})
    assert body["content"] == raw
    assert body["content"] is not raw  # 拷贝，不持有调用方对象


def test_build_submit_body_rejects_mixed_writing_styles():
    """content 与 prompt/image/... 两条写法互斥，避免出现「到底用哪个」的静默歧义。"""
    cap = VideoCapability()
    with pytest.raises(GatewayError) as ei:
        cap.build_submit_body({"model": "m", "content": [{"type": "text", "text": "a"}], "prompt": "b"})
    assert ei.value.kind == "bad_input"


def test_build_submit_body_requires_some_content():
    with pytest.raises(GatewayError) as ei:
        VideoCapability().build_submit_body({"model": "m"})
    assert ei.value.kind == "bad_input"


# ---- 响应解析 -----------------------------------------------------------
def test_read_submit_extracts_id(): 
    assert VideoCapability().read_submit({"id": "task_abc"}) == {
        "upstream_id": "task_abc",
        "status": TaskStatus.QUEUED,
    }


def test_read_submit_without_id_is_failed():
    """上游 200 却没给 id ⇒ 无法轮询，如实落成失败（保留原始响应便于排查）。"""
    info = VideoCapability().read_submit({})
    assert info["status"] is TaskStatus.FAILED
    assert "未返回任务 id" in info["error"]


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("queued", TaskStatus.QUEUED),
        ("pending", TaskStatus.QUEUED),
        ("running", TaskStatus.RUNNING),
        ("processing", TaskStatus.RUNNING),
        ("", TaskStatus.RUNNING),
        ("what_is_this", TaskStatus.RUNNING),
        ("expired", TaskStatus.EXPIRED),
        ("cancelled", TaskStatus.CANCELLED),
    ],
)
def test_read_poll_status_mapping(raw, expected):
    assert VideoCapability().read_poll({"status": raw})["status"] is expected


def test_read_poll_succeeded_requires_video_url():
    """完成判据 = 状态成功 **且** 产物非空；只有状态词不算成功。"""
    cap = VideoCapability()
    assert cap.read_poll({"status": "succeeded"})["status"] is TaskStatus.RUNNING
    info = cap.read_poll({"status": "succeeded", "content": {"video_url": "https://v/1.mp4"}})
    assert info["status"] is TaskStatus.SUCCEEDED
    assert info["result"]["video_url"] == "https://v/1.mp4"


def test_read_poll_succeeded_but_blank_url_is_running():
    info = VideoCapability().read_poll({"status": "succeeded", "content": {"video_url": "  "}})
    assert info["status"] is TaskStatus.RUNNING


def test_read_poll_failed_carries_error():
    info = VideoCapability().read_poll({"status": "failed", "error": {"message": "内容审核不通过"}})
    assert info["status"] is TaskStatus.FAILED
    assert info["error"] == "内容审核不通过"


def test_extract_artifact_includes_metadata():
    art = extract_artifact(
        {
            "content": {"video_url": "https://v/1.mp4"},
            "id": "task_abc",
            "duration": 5,
            "ratio": "16:9",
            "created_at": 1,
            "updated_at": 2,
            "error": None,
        }
    )
    assert art == {
        "video_url": "https://v/1.mp4",
        "id": "task_abc",
        "duration": 5,
        "ratio": "16:9",
        "created_at": 1,
        "updated_at": 2,
    }


def test_extract_artifact_none_when_no_url():
    assert extract_artifact({"status": "running"}) is None
    assert extract_artifact({"content": {}}) is None


# ---- 自动发现 -----------------------------------------------------------
def test_discover_finds_video():
    assert "video" in [c.name for c in discover_capabilities()]


def test_video_declares_no_cancel_endpoint():
    """上游 DELETE/POST 该路径均 405 ⇒ 不得假装能取消上游任务。"""
    assert VideoCapability.cancel_path is None


# ---- 端到端编排（假上游）------------------------------------------------
def _video_routes(captured: list[dict], polls: list[str]):
    def submit(request):
        captured.append(body_json(request))
        return httpx.Response(200, json={"id": "task_abc"})

    def poll(request):
        polls.append(request.url.path)
        if len(polls) < 2:
            return httpx.Response(200, json={"id": "task_abc", "status": "queued", "error": None})
        return httpx.Response(
            200,
            json={
                "content": {"video_url": "https://aivideomaker.1task.cn/v/x.mp4"},
                "created_at": 1791880167,
                "duration": 5,
                "error": None,
                "id": "task_abc",
                "ratio": "16:9",
                "status": "succeeded",
                "updated_at": 1791880221,
            },
        )

    return {
        ("POST", SUBMIT_PATH): submit,
        ("GET", f"{SUBMIT_PATH}/task_abc"): poll,
    }


def test_video_end_to_end_submit_then_poll():
    captured: list[dict] = []
    polls: list[str] = []
    ctx = make_context(json_handler(_video_routes(captured, polls)))
    cap = VideoCapability()

    task = cap.submit(ctx, model="minimax-h3-480p", params={"prompt": "一只猫", "duration": 5})

    assert task.status is TaskStatus.QUEUED
    assert task.upstream_id == "task_abc"
    assert captured[0] == {
        "model": "minimax-h3-480p",
        "content": [{"type": "text", "text": "一只猫"}],
        "duration": 5,
    }
    assert polls == []  # 提交阶段绝不轮询

    task = cap.advance(ctx, task)
    assert task.status is TaskStatus.QUEUED
    task = cap.advance(ctx, task)
    assert task.status is TaskStatus.SUCCEEDED
    assert task.result["video_url"] == "https://aivideomaker.1task.cn/v/x.mp4"
    assert task.result["duration"] == 5
    assert polls == [f"{SUBMIT_PATH}/task_abc"] * 2

    # 终态后不再打上游
    cap.advance(ctx, task)
    assert len(polls) == 2


def test_video_submit_gates_model_by_capability():
    """模型未在 capabilities.yaml 声明 video 能力时必须拒绝（FR-F5）。"""
    ctx = make_context(json_handler({}))
    with pytest.raises(ModelNotAllowed):
        VideoCapability().submit(ctx, model="flux-3-image", params={"prompt": "x"})


def test_cancel_is_local_only():
    """无上游取消端点 ⇒ cancel 只改本地状态，不能假装通知了上游。"""
    seen: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path))
        if request.method == "POST":
            return httpx.Response(200, json={"id": "task_1"})
        return httpx.Response(404, json={"error": {"code": "task_not_found"}})

    ctx = make_context(handler)
    cap = VideoCapability()
    task = cap.submit(ctx, model="video-model", params={"prompt": "x"})
    before = list(seen)

    task = cap.cancel(ctx, task)

    assert task.status is TaskStatus.CANCELLED
    assert seen == before, "cancel_path 为 None 时不得向上游发请求"
    assert POLL_PATH.format(task_id="task_1").endswith("/task_1")
