"""gunicorn 配置（**可选**；默认部署用 uvicorn 直跑即可）。

用法（需先 pip install gunicorn uvicorn-worker）：
    gunicorn -c gunicorn_conf.py jieapi_mcp.asgi:app

🔴 workers 必须为 1，不可改大 —— 这不是保守参数，是架构约束：
   * MCP Streamable HTTP 的会话管理器在进程内；多 worker ⇒ 同一会话的后续请求
     可能落到别的 worker ⇒ "session not found"；
   * 任务表 TaskStore 也在进程内；多 worker ⇒ 任务状态被切碎，get_task 查不到。
   需要吞吐就纵向扩容单进程，或先把任务表外置 + 用 stateless_http + 会话亲和。
"""

bind = "0.0.0.0:8000"
workers = 1  # 🔴 不可改大，见上方说明
worker_class = "uvicorn_worker.UvicornWorker"
timeout = 600          # 生图/视频提交可能较慢，留足余量
graceful_timeout = 30
keepalive = 65
accesslog = "-"
errorlog = "-"
loglevel = "info"
