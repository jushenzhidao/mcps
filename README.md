# jieapi MCP 网关服务

把 **jieapi.com**（new-api 实例）上的模型能力，封装成 MCP 工具，供 Claude Desktop / Cursor 等
MCP 客户端直接调用。**一期：图片生成（同步）；二期：视频生成（任务型异步）**。

> 上游网关：`https://jieapi.com`（OpenAI 兼容）。国内网络不佳时可用回国入口 `https://apicn.jieapi.com`。

---

## 1. 快速开始

```bash
cd ~/PycharmProjects/AI/mcps
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

cp .env.example .env      # 填入 JIEAPI_KEY
.venv/bin/python scripts/stdio_smoke.py   # 冒烟：应与网关握手并列出 7 个工具
```

### MCP 客户端配置

Claude Desktop（`~/Library/Application Support/Claude/claude_desktop_config.json`）或
Cursor（`~/.cursor/mcp.json`）：

```json
{
  "mcpServers": {
    "jieapi": {
      "command": "/Users/betterme/PycharmProjects/AI/mcps/.venv/bin/python",
      "args": ["-m", "jieapi_mcp"],
      "cwd": "/Users/betterme/PycharmProjects/AI/mcps",
      "env": {
        "PYTHONPATH": "/Users/betterme/PycharmProjects/AI/mcps/src",
        "JIEAPI_KEY": "sk-你的令牌",
        "JIEAPI_BASE_URL": "https://jieapi.com"
      }
    }
  }
}
```

---

## 2. 工具清单

| 工具 | 说明 |
|---|---|
| `generate_image` | 文生图 / 图生图 / 图像编辑（**同步**返回）。参数：`model` `prompt` `image[]` `size` `output_format` `n` `response_format` |
| `create_video` | 视频生成（**异步**）。参数：`model` `prompt` `image[]` `video` `audio[]` `duration` `resolution` `ratio` `generate_audio` `watermark` `content[]`；立即返回 `task_id` |
| `get_task` | 查询任务状态，并向上游**推进一次**轮询。`wait_seconds` 可在本次调用内短等待（上限 60 秒） |
| `list_tasks` | 列出本进程内的任务，可按 `status` / `capability` 过滤 |
| `cancel_task` | 取消任务。⚠️ 视频上游**无取消端点**，此工具只停止**本地**跟踪 |
| `list_models` | 列出网关在售模型；可传 `capability='image'` / `'video'` 按本地能力表过滤（带约 5 分钟缓存） |
| `reload_config` | 热重载能力映射表与环境变量，改完 `capabilities.yaml` 无需重启 |

`generate_image` 的 `image` 参数接受：公网 URL、data URI，或**本机文件绝对路径**
（服务端自动读入并转 data URI；单文件上限 `IMAGE_MAX_FILE_MB`，默认 10MB）。

`response_format`：`url`（默认，**链接有有效期，过期需重新生成**）或 `b64_json`（内联返回，客户端直接显示）。

### `create_video`：异步工具怎么用

一次调用**只提交任务并返回 `task_id`**，不阻塞等出片；随后用 `get_task` 轮询。

- 参考图 `image[]` 与 `generate_image` 同规则：公网 URL 或本机路径（自动转 data URI）。
- `video` / `audio` 参考素材**必须是公网 http(s) URL** —— 上游要自己去下载。
- 实测 `minimax-h3-480p` + `duration=5`：提交后约 **2.5 分钟**出片，且**大部分时间花在上游排队**
  （13 次轮询都是 `queued`）。所以**别用长 `wait_seconds` 阻塞**：先拿 `task_id`，过一会儿再查。
- ⚠️ **提交即计费**；上游无取消端点；返回的视频链接有有效期，过期需重新生成。

---

## 3. 配置

### 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `JIEAPI_KEY` | 无（必填） | 网关令牌 |
| `JIEAPI_BASE_URL` | `https://jieapi.com` | 网关地址 |
| `IMAGE_MAX_FILE_MB` | `10` | `image` 本机文件大小上限 |
| `GATEWAY_TIMEOUT` | `120` | 网关请求超时（秒）；生图实测可达 40s+ |
| `MODELS_CACHE_TTL` | `300` | `/v1/models` 缓存秒数 |
| `CAPABILITIES_FILE` | 仓库根 `capabilities.yaml` | 能力映射表路径 |

### 能力映射表 `capabilities.yaml`

模型名 → 能力集合的唯一事实来源。工具调用前据此校验（FR-F5），未命中会报错并列出该能力下全部可用模型。

```yaml
models:
  flux-3-image:
    capabilities: [image, image_to_image]
  minimax-h3-480p:
    capabilities: [video]
```

`video` 类模型的上游端点是**聚合**的：所有视频家族共用 `/v3/contents/generations/tasks`
（提交 `POST`，查询 `GET /v3/contents/generations/tasks/{task_id}`），靠 `model` 区分家族，
无需按家族分叉路径。实测依据见 `src/jieapi_mcp/capabilities/video.py` 顶部注释。

---

## 4. 架构

```
MCP 客户端 (Claude Desktop / Cursor)
   │  stdio
   ▼
MCP Server（Python + mcp 2.x，单进程）
   ├─ 框架层  config / errors / transport / registry / tasks / server
   ├─ 能力插件 capabilities/image.py（同步）｜ capabilities/video.py（任务型异步）
   ▼
网关 jieapi.com ──POST /v1/images/generations──────────▶ 上游生图渠道
               └──POST /v3/contents/generations/tasks────▶ 上游视频渠道（异步；查用 GET）
```

| 文件 | 职责 |
|---|---|
| `src/jieapi_mcp/config.py` | 环境变量 + `capabilities.yaml` 加载与热重载 |
| `src/jieapi_mcp/errors.py` | 网关错误翻译（FR-F6） |
| `src/jieapi_mcp/transport.py` | httpx 客户端，能力无关的**唯一**对外网络出口 |
| `src/jieapi_mcp/registry.py` | 模型目录缓存（FR-F4）+ 能力校验（FR-F5） |
| `src/jieapi_mcp/context.py` | 依赖注入容器 |
| `src/jieapi_mcp/capabilities/` | 能力插件；`base.py` 定义 `ExecutionMode`（SYNC/TASK）；`video.py` 为任务型异步能力 |
| `src/jieapi_mcp/server.py` | Server 装配 + 框架工具注册 |

### 关于「异步」——本项目的关键设计缝

`capabilities/base.py` 里的 `ExecutionMode` 明确区分两种**工具调用语义**：

- `SYNC`：一次调用直接返回结果（**image**）。
- `TASK`：一次调用只返回任务句柄（task_id），结果另行轮询（**video**）。

框架层（注册 / 校验 / 错误翻译 / 热重载 / 任务表）对两者一视同仁。新增任务型能力只需：

1. 在 `capabilities/` 加一个模块（如 `video.py`），继承 `TaskCapability`；
2. 实现三个纯函数钩子：`build_submit_body` / `read_submit` / `read_poll`；
3. 在 `capabilities.yaml` 给相关模型加能力标签（如 `video`）。

**无需登记**（`capabilities/` 下非 `_` 前缀模块自动发现），**框架层无需改动**。
`get_task` / `list_tasks` / `cancel_task` 三个通用工具由框架统一提供，任务型能力自动获得。

轮询是**按需**的（`get_task` 时推进一次），不起后台线程 —— 既不空耗上游配额，
也让 stdio 单进程模型下的生命周期简单可控。`TaskStoreProtocol` 是留给多 worker / 多副本的外置存储缝。

### 并发与扩容（含"要不要换 Go"的结论）

**先定性**：本服务的耗时几乎全在等上游（生图实测 9~60s），CPU 基本空闲。
因此瓶颈是**上游延迟与限流**，不是语言或进程模型 —— 换 Go 并不会让它更快。

**单进程能扛多少**：SDK 对同步工具的实现是 `await anyio.to_thread.run_sync(...)`，
并发受 anyio 线程池限制（默认 40，可用 `MCP_MAX_CONCURRENCY` 调大）。即单进程可同时挂着几十个上游请求。

**何时才需要多 worker / 多副本**：要 HA（崩了不中断）或确有高并发时。此时有**两个不同性质**的坑，要一起解决：

| 坑 | 状态归属 | 解法 |
|---|---|---|
| MCP 会话（`Session not found`） | **SDK 进程内**对象（含 SSE 流/队列），**无法**序列化进 Redis | `MCP_STATELESS=1`（推荐）或 nginx 粘性会话 |
| `TaskStore` 查不到任务 | **我们自己**的内存字典 | 换成实现 `TaskStoreProtocol` 的 Redis/MySQL 后端（drop-in，框架与插件不用改） |

> ⚠️ 所以"用 Redis 解决"只对**第二个坑**成立；第一个坑不是我们的数据结构，Redis 帮不上。

实测（本机 gunicorn `-w 2`，**每次新建连接**）：

| 配置 | 结果 |
|---|---|
| 有会话（stateful） | 成功 2 / 失败 10，报 `Session not found` |
| `MCP_STATELESS=1` | 成功 12 / 失败 0 |

> 🔴 注意：**单客户端复用 keep-alive 连接时会天然粘在同一个 worker**，多 worker 也"看着正常"——这是假绿灯。
> 真实触发场景是**多客户端并发、连接重建、或前置 LB 轮询**。复现脚本：`scripts/worker_affinity_probe.py`。

**结论**：默认单进程（`workers=1`）；确要扩容，先上 `MCP_STATELESS=1` + 外置任务表。
只有出现**进程内 CPU 密集**需求（大规模图像解码/转码等）时，换 Go 才值得。

---

## 5. 与 PRD 的现实校准（实测差异）

| PRD 表述 | 实测（2026-10-13） | 代码里的处理 |
|---|---|---|
| 生图模型 `flux-3-image` | 初始不存在；用户添加后可用，实测 HTTP 200 / ~43s | `capabilities.yaml` 以实测清单为准 |
| 「404/无可用渠道」 | 网关返回 **503 + `code=model_not_found`** | 按 `code` 判定，不只看 status（`errors.py`） |
| 返回含 `revised_prompt` / `usage` 明细 | flux-3-image 返回极简 `data[].url`，无 `revised_prompt`，usage 为零 | 有则透传、无则省略，兼容三种形态（`image.py`） |
| 技术栈 Python + FastMCP | 最新 SDK 为 `mcp 2.3.0`，`FastMCP` 已改名 `MCPServer` | 使用 `mcp.server.mcpserver.MCPServer` |
| 视频各家族文档路径前缀不同（`/doubao/api/v3/…`、`/videos/api/v3/…`） | 实测三条前缀对 GET/POST **返回逐字一致** ⇒ 上游是**同一个聚合插件**，前缀被忽略 | 只用 `/v3/contents/generations/tasks` 一条路径，按 `model` 区分家族（`video.py`） |
| 以为有「取消任务」端点 | `DELETE` / `POST` 到 `…/tasks/{id}` 均 **405** ⇒ 上游**无取消** | `cancel_path = None`，`cancel_task` 只标记本地，描述里如实告知 |
| 路径写错会得到 404 | 未知路由回退到 new-api 的 **HTML 前端页**（HTTP 200，非 JSON） | 症状是「网关返回了非 JSON 响应」（`transport.py`），排查按此对照 |
| 视频会很快出片 | `minimax-h3-480p`+5s 实测约 **2.5 分钟**，其中 13 次轮询均为 `queued` | 工具立即返回 `task_id`；文档建议别用长 `wait_seconds` 阻塞 |

---

## 6. 测试

```bash
.venv/bin/python -m pytest --basetemp=/tmp/jieapi-mcp-pytest -q
```

全部**零网络**（`httpx.MockTransport` + 假上游），不发任何真实生成请求。
`tests/test_video.py` 覆盖 content 装配、提交/轮询解析、端到端编排，以及
「上游无取消端点 ⇒ 不得假装通知了上游」的负向断言。
`scripts/stdio_smoke.py` 会真实握手网关，但只调只读的 `list_models`（不触发计费）。
