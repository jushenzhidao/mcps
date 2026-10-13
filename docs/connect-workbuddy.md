# 让 WorkBuddy 连接 jieapi MCP 服务

两种用法，任选：

- **【A】直接说给 WorkBuddy**（推荐）：把下面「提示词」整段复制给 WorkBuddy，它会自己写配置。
- **【B】手动配置**：自己把「配置内容」写进 `~/.workbuddy/mcp.json`。

> 🔴 本文**不含真实令牌**（仓库为公开）。令牌从以下位置取：
> 已有配置的 `~/.workbuddy/mcp.json` 里 `headers.Authorization`，
> 或服务端 `/opt/jieapi-mcp/.env` 的 `MCP_AUTH_TOKEN`。

---

## 提示词（复制这段给 WorkBuddy，并把 <MCP_AUTH_TOKEN> 换成真实令牌）

```text
帮我在这台机器上把 jieapi MCP 服务接入 WorkBuddy，并验证连通。请严格按以下步骤做：

1) 写入用户级 MCP 配置，文件：~/.workbuddy/mcp.json
   读取该文件（若不存在则新建），在 mcpServers 下**合并**以下条目，不要覆盖已有条目：

   "jieapi": {
     "type": "http",
     "url": "https://mcp.1task.cn/mcp",
     "headers": { "Authorization": "Bearer <MCP_AUTH_TOKEN>" },
     "description": "jieapi(new-api) 网关 MCP 服务。工具：generate_image（文生图/图生图/多图合成，一次调用即产生上游网关费用，URL 结果有有效期）、list_models（可按 capability 过滤如 image）、reload_config（热重载能力映射表），以及任务型通用工具 get_task/list_tasks/cancel_task。"
   }

2) 校验 JSON 语法合法，并确认原有条目（cowart/rea/logfire/logfire-images 等）未被改动。

3) 写完后告诉我：新配置不会自动生效，需要我在 WorkBuddy 里新开一个会话来加载工具。

4) 提示我注意：这个入口的 URL 必须带 /mcp 路径，https://mcp.1task.cn/ 是不带路径的根地址。

5) 告诉我最省钱的验证方式：新会话里先让它调用 list_models（只读、不产生费用），
   确认能返回模型列表后，再考虑 generate_image（会产生上游网关费用）。

注意：连接器管理页里的搜索框只搜官方连接器目录，自定义 MCP 不会出现在那里，
所以不要用"搜不到 jieapi"来判断是否配置成功。
```

---

## 配置内容（手动配置用）

写入 `~/.workbuddy/mcp.json`（用户级，所有项目通用）：

```json
{
  "mcpServers": {
    "jieapi": {
      "type": "http",
      "url": "https://mcp.1task.cn/mcp",
      "headers": {
        "Authorization": "Bearer <MCP_AUTH_TOKEN>"
      }
    }
  }
}
```

> 若文件已存在，只把 `"jieapi": {...}` 这一条**合并**进已有的 `mcpServers`，不要整体覆盖。

---

## 生效与验证

1. 保存后**的步骤**：配置不会自动生效。
   - 首选：**新开一个会话**（新任务），让 WorkBuddy 重新加载工具清单；
   - 或用：输入框左下角 `+` → 连接器 → 勾选 `jieapi`（会话级启用）。
2. 工具会以 `mcp__jieapi__*` 形式出现：
   `generate_image` / `list_models` / `reload_config` / `get_task` / `list_tasks` / `cancel_task`
3. **先验证再花钱**：让它调 `list_models`（只读、免费）。返回形如「具备 image 能力且网关在售的模型（25 个）」即打通。
4. 之后再用 `generate_image`。**注意：一次调用即产生上游网关费用**（约 0.2 元/张起）。

---

## 常见误区

| 现象 | 原因 |
|---|---|
| 连接器搜索框搜 `jieapi` 显示「未找到连接器」 | 那个框只搜**官方连接器目录**（键名带 `connector:`），自定义 MCP 不在其中。**不代表配置失败** |
| 配好了但当前会话调不到 | 工具清单在会话启动时加载；**信任/配置之后需新开会话** |
| 用 `https://mcp.1task.cn/` 连不上 | 端点必须带路径：`https://mcp.1task.cn/mcp` |

## 安全提示

`Authorization` 里的 Bearer token 等于"能消耗你网关额度的钥匙"，属于长期凭据：
不要提交进版本库、不要贴到公共设备或截图里。需要轮换时改服务端 `/opt/jieapi-mcp/.env`
的 `MCP_AUTH_TOKEN` 并 `docker compose up -d` 重建容器，然后同步更新 `mcp.json`。
