"""任务基础设施测试（TaskStore + TaskCapability 编排 + 异步模板）。零网络。"""

from __future__ import annotations

import httpx
import pytest

from jieapi_mcp.capabilities._task_template import SampleTaskCapability
from jieapi_mcp.tasks import TaskStatus, TaskStore

from .conftest import body_json, json_handler, make_context


# ---- TaskStore ----------------------------------------------------------
def test_store_create_get_update():
    store = TaskStore()
    task = store.create(capability="video", model="video-model")
    assert task.status is TaskStatus.QUEUED
    assert store.get(task.id) is task
    updated = store.update(task.id, status=TaskStatus.RUNNING)
    assert updated.status is TaskStatus.RUNNING


def test_store_list_filters():
    store = TaskStore()
    a = store.create(capability="video", model="v")
    b = store.create(capability="image", model="i")
    store.update(b.id, status=TaskStatus.SUCCEEDED)
    assert {t.id for t in store.list()} == {a.id, b.id}
    assert [t.id for t in store.list(capability="image")] == [b.id]
    assert [t.id for t in store.list(status=TaskStatus.SUCCEEDED)] == [b.id]


def test_store_ttl_prunes():
    now = [0.0]
    store = TaskStore(ttl=10.0, clock=lambda: now[0])
    t = store.create(capability="video", model="v")
    now[0] = 5.0
    assert store.get(t.id) is not None
    now[0] = 20.0
    assert store.get(t.id) is None


def test_store_max_prunes_oldest():
    store = TaskStore(max_tasks=2)
    t1 = store.create(capability="video", model="v")
    t2 = store.create(capability="video", model="v")
    t3 = store.create(capability="video", model="v")
    ids = {t.id for t in store.list(limit=10)}
    assert t3.id in ids and t2.id in ids
    assert t1.id not in ids


def test_terminal_flag():
    assert TaskStatus.SUCCEEDED.terminal
    assert TaskStatus.FAILED.terminal
    assert not TaskStatus.RUNNING.terminal
    assert not TaskStatus.QUEUED.terminal


# ---- TaskCapability 编排 -------------------------------------------------
def _stateful_routes():
    state = {"polls": 0}
    captured: list[dict] = []

    def submit(request):
        captured.append(body_json(request))
        return httpx.Response(200, json={"id": "up-1", "status": "queued"})

    def poll(request):
        state["polls"] += 1
        if state["polls"] < 2:
            return httpx.Response(200, json={"status": "running"})
        return httpx.Response(200, json={"status": "succeeded", "result": {"url": "https://v/1.mp4"}})

    routes = {
        ("POST", "/v1/sample/generations"): submit,
        ("GET", "/v1/sample/generations/up-1"): poll,
        ("POST", "/v1/sample/generations/up-1/cancel"): (200, {}),
    }
    return routes, captured, state


def test_submit_returns_immediately():
    routes, captured, _ = _stateful_routes()
    ctx = make_context(json_handler(routes))
    cap = SampleTaskCapability()

    task = cap.submit(ctx, model="sample-model", params={"prompt": "hi"})

    assert task.status is TaskStatus.QUEUED
    assert task.upstream_id == "up-1"
    assert captured[0]["model"] == "sample-model"
    assert captured[0]["prompt"] == "hi"
    # 提交阶段不轮询
    assert ctx.tasks.get(task.id) is task


def test_advance_polls_until_terminal():
    routes, _, state = _stateful_routes()
    ctx = make_context(json_handler(routes))
    cap = SampleTaskCapability()
    task = cap.submit(ctx, model="sample-model", params={"prompt": "hi"})

    task = cap.advance(ctx, task)
    assert task.status is TaskStatus.RUNNING
    task = cap.advance(ctx, task)
    assert task.status is TaskStatus.SUCCEEDED
    assert task.result == {"url": "https://v/1.mp4"}
    # 终态后不再打上游
    before = state["polls"]
    cap.advance(ctx, task)
    assert state["polls"] == before


def test_cancel_marks_cancelled():
    routes, _, _ = _stateful_routes()
    ctx = make_context(json_handler(routes))
    cap = SampleTaskCapability()
    task = cap.submit(ctx, model="sample-model", params={"prompt": "hi"})
    task = cap.cancel(ctx, task)
    assert task.status is TaskStatus.CANCELLED


def test_read_poll_requires_result_for_success():
    """完成判据必须是「状态成功 且 产物非空」，不能只看状态词。"""
    cap = SampleTaskCapability()
    assert cap.read_poll({"status": "succeeded"})["status"] is TaskStatus.RUNNING
    assert cap.read_poll({"status": "succeeded", "result": {"url": "x"}})["status"] is TaskStatus.SUCCEEDED
    assert cap.read_poll({"status": "failed", "error": "boom"})["status"] is TaskStatus.FAILED
    assert cap.read_poll({"status": "expired"})["status"] is TaskStatus.EXPIRED
