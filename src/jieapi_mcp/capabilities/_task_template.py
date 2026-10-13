"""任务型能力的「复制即用」模板（文件名以 ``_`` 开头 ⇒ 不会被自动发现）。

新增一个异步能力（例如二期视频生成）的完整步骤：
1. 复制本文件为 ``capabilities/<能力名>.py``（去掉下划线前缀）；
2. 改 ``name`` / ``submit_path`` / ``poll_path`` / ``cancel_path``；
3. 实现四个钩子 / 提交工具（见下）；
4. 在 ``capabilities.yaml`` 给相关模型加能力标签。

框架会自动提供 ``get_task`` / ``list_tasks`` / ``cancel_task`` 三个通用工具，
**无需**在本文件里重复实现。

约定要点
--------
* 提交工具**立刻返回** ``task_id``，绝不阻塞等待上游完成；
* 上游「无提交」的失败（参数错等）应在提交工具内同步报错；
  上游已受理后的失败写进任务记录（:class:`~jieapi_mcp.tasks.TaskStatus.FAILED`），
  并在 ``error`` 里说清「有没有提交、有没有计费」；
* ``read_poll`` 必须用上游真实的完成判据（业务码 + 产物非空），不要靠 HTTP 200 就判成功。
"""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.types import TextContent

from ..context import AppContext
from ..tasks import TaskStatus
from .base import ExecutionMode, TaskCapability

# 上游「排队中」与「运行中」的状态词（按实际上游补充）
_QUEUED_WORDS = {"queued", "pending", "submitted", "created", "accepted", "waiting"}
_RUNNING_WORDS = {"running", "processing", "in_progress", "executing", "generating"}


class SampleTaskCapability(TaskCapability):
    """示例：把某上游的「提交 → 轮询」适配成 MCP 任务型能力。"""

    name = "sample_task"
    execution_mode = ExecutionMode.TASK

    submit_path = "/v1/sample/generations"
    poll_path = "/v1/sample/generations/{task_id}"
    cancel_path = "/v1/sample/generations/{task_id}/cancel"

    # ---- 钩子 1：构造提交体 -------------------------------------------------
    def build_submit_body(self, params: dict[str, Any]) -> dict[str, Any]:
        return dict(params)

    # ---- 钩子 2：解析提交响应 -----------------------------------------------
    def read_submit(self, response: Any) -> dict[str, Any]:
        response = response or {}
        upstream_id = response.get("id") or response.get("task_id")
        raw_status = str(response.get("status", "")).lower()
        if raw_status in _RUNNING_WORDS:
            status = TaskStatus.RUNNING
        else:
            status = TaskStatus.QUEUED  # 含 queued / 未知 / 缺省
        return {"upstream_id": upstream_id, "status": status}

    # ---- 钩子 3：解析轮询响应 -----------------------------------------------
    def read_poll(self, response: Any) -> dict[str, Any]:
        response = response or {}
        raw_status = str(response.get("status", "")).lower()
        if raw_status in _QUEUED_WORDS or raw_status in _RUNNING_WORDS or not raw_status:
            return {"status": TaskStatus.RUNNING}
        # 完成判据：业务码成功 **且** 产物非空（不要只看 HTTP 200）
        result = response.get("result") or {}
        if raw_status in {"succeeded", "success", "completed"} and result:
            return {"status": TaskStatus.SUCCEEDED, "result": result}
        if raw_status in {"failed", "error"}:
            return {"status": TaskStatus.FAILED, "error": response.get("error") or raw_status}
        if raw_status in {"expired", "timeout"}:
            return {"status": TaskStatus.EXPIRED, "error": response.get("error") or raw_status}
        return {"status": TaskStatus.RUNNING}

    # ---- 提交工具：立刻返回 task_id ----------------------------------------
    def register_tools(self, mcp: MCPServer, ctx: AppContext) -> None:
        @mcp.tool(
            name="sample_submit",  # 换成业务名，如 create_video
            description=(
                "提交一个示例任务（异步）。立即返回 task_id，"
                "随后用 get_task 查询进度（上游较慢时请勿阻塞等待）。"
            ),
        )
        def sample_submit(model: str, prompt: str) -> str:
            task = self.submit(ctx, model=model, params={"prompt": prompt})
            return f"已提交：task_id={task.id}，状态={task.status.value}。用 get_task 查询进度。"


# 供显式导入测试用（自动发现会跳过本模块）
__all__ = ["SampleTaskCapability"]
