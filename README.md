# jieapi MCP 网关服务

把 **jieapi.com**（new-api 实例）上的模型能力，封装成 MCP 工具，供 Claude Desktop / Cursor 等
MCP 客户端直接调用。一期交付**图片生成**，框架层已为「任务型异步能力」（如视频生成）预留扩展点。

> 上游网关：`https://jieapi.com`（OpenAI 兼容）。国内网络不佳时可用回国入口 `https://apicn.jieapi.com`。

---

## 1. 快速开始

```bash
cd ~/PycharmProjects/AI/mcps
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

cp .env.example .env      # 填入 JIEAPI_KEY
.venv/bin/python scripts/stdio_smoke.py   # 冒烟：应与网关握手并列出 3 个工具
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
| `generate_image` | 文生图 / 图生图 / 图像编辑。参数：`model` `prompt` `image[]` `size` `output_format` `n` `response_format` |
| `list_models` | 列出网关在售模型；可传 `capability='image'` 按本地能力表过滤（带约 5 分钟缓存） |
| `reload_config` | 热重载能力映射表与环境变量，改完 `capabilities.yaml` 无需重启 |

`generate_image` 的 `image` 参数接受：公网 URL、data URI，或**本机文件绝对路径**
（服务端自动读入并转 data URI；单文件上限 `IMAGE_MAX_FILE_MB`，默认 10MB）。

`response_format`：`url`（默认，**链接有有效期，过期需重新生成**）或 `b64_json`（内联返回，客户端直接显示）。

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
```

---

## 4. 架构

```
MCP 客户端 (Claude Desktop / Cursor)
   │  stdio
   ▼
MCP Server（Python + mcp 2.x，单进程）
   ├─ 框架层  config / errors / gateway / registry / server
   ├─ 能力插件 capabilities/image.py（同步）｜ video / search / parse（二期，同框架接入）
   ▼
网关 jieapi.com ──POST /v1/images/generations──▶ 上游生图渠道
```

| 文件 | 职责 |
|---|---|
| `src/jieapi_mcp/config.py` | 环境变量 + `capabilities.yaml` 加载与热重载 |
| `src/jieapi_mcp/errors.py` | 网关错误翻译（FR-F6） |
| `src/jieapi_mcp/gateway.py` | httpx 客户端，唯一对外网络出口 |
| `src/jieapi_mcp/registry.py` | 模型目录缓存（FR-F4）+ 能力校验（FR-F5） |
| `src/jieapi_mcp/context.py` | 依赖注入容器 |
| `src/jieapi_mcp/capabilities/` | 能力插件；`base.py` 定义 `ExecutionMode` 扩展点 |
| `src/jieapi_mcp/server.py` | Server 装配 + 框架工具注册 |

### 关于「异步」——本项目的关键设计缝

`capabilities/base.py` 里的 `ExecutionMode` 明确区分两种**工具调用语义**：

- `SYNC`：一次调用直接返回结果（**本期 image**）。
- `TASK`：一次调用只返回任务句柄（task_id），结果另行轮询（**未来 video / 3D 等流式上游**）。

框架层（注册 / 校验 / 错误翻译 / 热重载）对两者一视同仁。新增任务型能力只需：
1. 在 `capabilities/` 加一个模块，实现 `BaseCapability` 子类并声明 `execution_mode = TASK`；
2. 在 `capabilities/__init__.py` 的 `CAPABILITY_MODULES` 注册；
3. 在 `capabilities.yaml` 给相关模型加能力标签。

**框架层无需改动。**

---

## 5. 与 PRD 的现实校准（实测差异）

| PRD 表述 | 实测（2026-10-13） | 代码里的处理 |
|---|---|---|
| 生图模型 `flux-3-image` | 初始不存在；用户添加后可用，实测 HTTP 200 / ~43s | `capabilities.yaml` 以实测清单为准 |
| 「404/无可用渠道」 | 网关返回 **503 + `code=model_not_found`** | 按 `code` 判定，不只看 status（`errors.py`） |
| 返回含 `revised_prompt` / `usage` 明细 | flux-3-image 返回极简 `data[].url`，无 `revised_prompt`，usage 为零 | 有则透传、无则省略，兼容三种形态（`image.py`） |
| 技术栈 Python + FastMCP | 最新 SDK 为 `mcp 2.3.0`，`FastMCP` 已改名 `MCPServer` | 使用 `mcp.server.mcpserver.MCPServer` |

---

## 6. 测试

```bash
.venv/bin/python -m pytest --basetemp=/tmp/jieapi-mcp-pytest -q
```

全部**零网络**（`httpx.MockTransport` + 假上游），不发任何真实生成请求。
`scripts/stdio_smoke.py` 会真实握手网关，但只调只读的 `list_models`（不触发计费）。
