# ai-prod 部署说明（mcp.1task.cn）

## 现状（2026-10-13 已上线）

| 项 | 值 |
|---|---|
| 公网入口 | **https://mcp.1task.cn/mcp**（Streamable HTTP） |
| 机器 | ai-prod `210.121.44.245`（Docker 29.8.0 / x86_64） |
| 端口 | `127.0.0.1:39020`（仅回环，对外走 nginx） |
| 镜像 | `ghcr.io/jushenzhidao/mcps:sha-a11c5e8`（**钉 sha**，CI 制品） |
| 部署目录 | `/opt/jieapi-mcp/` |
| 证书 | acme.sh（Let's Encrypt），`/www/server/panel/vhost/cert/mcp.1task.cn/` |
| 鉴权 | `Authorization: Bearer <MCP_AUTH_TOKEN>`（在 `/opt/jieapi-mcp/.env`） |

> ⚠️ 本服务持有**会花钱**的上游网关令牌。HTTP 模式**无 `MCP_AUTH_TOKEN` 会拒绝启动**，
> 请勿关闭鉴权、也不要把令牌写进镜像。

## 部署形态：拉 CI 制品

镜像由 `.github/workflows/docker-publish.yml` 在 push `main` / 打 tag 时构建推送 GHCR，
**服务器不构建**（ci 制品可追溯、可回滚）。

## 首次部署

```bash
ssh -i ~/.ssh/id_ed25519_ops root@210.121.44.245 'mkdir -p /opt/jieapi-mcp'
# 上传源码与部署件（Dockerfile / src / requirements.txt / capabilities.yaml / deploy/ai-prod/*）
scp -i ~/.ssh/id_ed25519_ops -r src Dockerfile requirements.txt capabilities.yaml \
    root@210.121.44.245:/opt/jieapi-mcp/
scp -i ~/.ssh/id_ed25519_ops deploy/ai-prod/docker-compose.yml root@210.121.44.245:/opt/jieapi-mcp/
scp -i ~/.ssh/id_ed25519_ops -r deploy/ai-prod/nginx root@210.121.44.245:/opt/jieapi-mcp/

# .env（从 .env.example 填：MCP_IMAGE / JIEAPI_KEY / MCP_AUTH_TOKEN）
ssh ... 'chmod 600 /opt/jieapi-mcp/.env'

# 起容器
ssh ... 'cd /opt/jieapi-mcp && docker compose pull && docker compose up -d'

# nginx :80（ACME 通道）+ DNS A 记录 + TLS 一键
ssh ... 'cp /opt/jieapi-mcp/nginx/mcp.1task.cn.conf /www/server/panel/vhost/nginx/ && /www/server/nginx/sbin/nginx -t'
ssh ... '/www/server/nginx/sbin/nginx -s reload'
ssh ... 'bash /opt/jieapi-mcp/nginx/apply-https.sh'
```

## 升级（发新版本）

```bash
# 1. push main 触发 CI；2. 取新 sha 标签
gh run list -R jushenzhidao/mcps --limit 3
# 3. 改 /opt/jieapi-mcp/.env 的 MCP_IMAGE 为新 sha
# 4. 拉取并重建
ssh ... 'cd /opt/jieapi-mcp && docker compose pull && docker compose up -d'
```

## 验证

```bash
# 公网端到端（会真实握手 + 调只读工具，不计费）
MCP_URL=https://mcp.1task.cn/mcp MCP_AUTH_TOKEN=<token> \
  python scripts/http_smoke.py
```

预期：401（错令牌）→ initialize → tools/list **7** 个工具（含 `create_video`）→ tools/call `list_models` 含 `flux-3-image`。

> ⚠️ 冒烟脚本会把 `create_video` 列为必需工具 ⇒ **先升级部署再跑**，否则老版本会报缺少工具。

## 客户端接入

```json
{
  "mcpServers": {
    "jieapi": {
      "type": "http",
      "url": "https://mcp.1task.cn/mcp",
      "headers": { "Authorization": "Bearer <MCP_AUTH_TOKEN>" }
    }
  }
}
```

## 扩容

单进程（**勿加 workers**）为默认。要横向扩容：`MCP_STATELESS=1` + 把 `TaskStore`
换成外部实现（Redis/MySQL，见 `src/jieapi_mcp/tasks.py::TaskStoreProtocol`），
详见仓库根 `README.md` 的「并发与扩容」。
