#!/usr/bin/env bash
# jieapi MCP 网关 · ai-prod 域名入口一键落地（幂等；DNS 未就绪会拒绝执行）
#
# 用法（在 ai-prod 上跑，root）：
#   bash /opt/jieapi-mcp/nginx/apply-https.sh
#
# 它做什么：DNS 预检 → ACME 通道自检 → acme.sh 签发（HTTP-01 webroot）→ install-cert
#          （含续期 reloadcmd）→ 投 :443 vhost → nginx -t + reload → 验证矩阵 + 终态断言
# 前置：:80 的 vhost 已投好（本脚本第 2 步会自检挑战路径可达）。
# 注：本服务 /healthz 免鉴权且恒 200，用它做存活断言；/mcp 无令牌应 401。
set -euo pipefail

HOST="${HOST:-mcp.1task.cn}"
IP_EXPECT="${IP_EXPECT:-210.121.44.245}"
VHOST_DIR=/www/server/panel/vhost/nginx
CERT_DIR="/www/server/panel/vhost/cert/$HOST"
ACME=/root/.acme.sh/acme.sh
NGINX=/www/server/nginx/sbin/nginx
ENV_FILE=/opt/jieapi-mcp/.env

echo "== 1) DNS 预检：$HOST 必须已指向 $IP_EXPECT =="
# 预检走**多解析器**：权威 NS 最准（无缓存），公共解析器兜底
GOT=""
for R in dns13.hichina.com dns14.hichina.com 8.8.8.8 1.1.1.1 223.5.5.5; do
  GOT=$(dig +short "$HOST" @"$R" 2>/dev/null | grep -E '^[0-9]+\.' | head -1 || true)
  [ "$GOT" = "$IP_EXPECT" ] && break
done
if [ "$GOT" != "$IP_EXPECT" ]; then
  echo "❌ DNS 未就绪：$HOST -> '${GOT:-<空>}'（期望 $IP_EXPECT）—— 先加 A 记录，再跑本脚本"
  exit 2
fi
echo "✅ $HOST -> $GOT"

echo "== 2) ACME 通道自检（挑战文件名必须能经本 vhost 取回）=="
mkdir -p /www/wwwroot/acme/.well-known/acme-challenge
echo "preflight-$$" > /www/wwwroot/acme/.well-known/acme-challenge/preflight
CODE=$(curl -s -o /dev/null -w '%{http_code}' -H "Host: $HOST" http://127.0.0.1/.well-known/acme-challenge/preflight || true)
[ "$CODE" = "200" ] || { echo "❌ 挑战路径返回 $CODE（应 200）—— 检查 $VHOST_DIR/$HOST.conf 的 ^~ location"; exit 3; }
echo "✅ 挑战路径 200"

echo "== 3) 签发证书（已存在则跳过）=="
if [ -d "/root/.acme.sh/${HOST}_ecc" ]; then
  echo "· 证书已存在，跳过签发"
else
  "$ACME" --issue -d "$HOST" --webroot /www/wwwroot/acme --keylength ec-256
fi
mkdir -p "$CERT_DIR"
"$ACME" --install-cert -d "$HOST" --ecc \
  --key-file       "$CERT_DIR/privkey.pem" \
  --fullchain-file "$CERT_DIR/fullchain.pem" \
  --reloadcmd      "$NGINX -s reload"
echo "✅ 证书就位：$CERT_DIR"

echo "== 4) 投 :443 vhost（含 -proxy.inc）并 reload（幂等）=="
SSL_CONF="$VHOST_DIR/$HOST-ssl.conf"
INC_CONF="$VHOST_DIR/mcp-proxy.inc"
[ -f "$INC_CONF" ] || cp -f /opt/jieapi-mcp/nginx/mcp-proxy.inc "$INC_CONF"
if [ ! -f "$SSL_CONF" ]; then
  cp -f "/opt/jieapi-mcp/nginx/$HOST-ssl.conf" "$SSL_CONF"
  echo "· 已写入 $SSL_CONF"
else
  echo "· $SSL_CONF 已存在，跳过"
fi
# ⚠️ `nginx -t && nginx -s reload` 写成一行是**错的**：set -e 对 AND-OR 列表首命令不生效，
#    ⇒ `-t` 失败会被吞掉，脚本照样打印"✅ 完成"。必须分两行。
"$NGINX" -t
"$NGINX" -s reload
echo "✅ nginx reloaded"

echo "== 5) 验证矩阵 =="
# 🔴 矩阵区关 set -e：curl 连接层失败（reload 后 SSL 竞态窗口）会让赋值语句非零退出。
#    打印尽力而为，成败由底部终态断言裁决。
set +e
[ -f "$ENV_FILE" ] || { echo "❌ 缺 $ENV_FILE（令牌断言需要）"; exit 6; }
TOKEN="$(grep -E '^MCP_AUTH_TOKEN=' "$ENV_FILE" | head -1 | cut -d= -f2- | tr -d '"' | tr -d "'")"
[ -n "$TOKEN" ] || { echo "❌ $ENV_FILE 里没有 MCP_AUTH_TOKEN"; exit 6; }

R="--resolve $HOST:443:127.0.0.1"
# 🔴 reload 是**异步**的：老 worker 会继续服务片刻 ⇒ 先等到 :443 真的应答，再跑矩阵。
for _ in $(seq 1 20); do
  C=$(curl -s -o /dev/null -w '%{http_code}' $R "https://$HOST/healthz")
  [ "$C" = "200" ] && break
  sleep 0.5
done

INIT_BODY='{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"verify","version":"0"}}}'
printf '%-42s %s\n' ":80 → :443 301"          "$(curl -s -o /dev/null -w '%{http_code}' -H "Host: $HOST" http://127.0.0.1/)"
printf '%-42s %s\n' ":443 /healthz（免鉴权）"   "$(curl -s -o /dev/null -w '%{http_code}' $R https://$HOST/healthz)"
printf '%-42s %s\n' ":443 /mcp 无令牌"          "$(curl -s -o /dev/null -w '%{http_code}' $R -X POST https://$HOST/mcp -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' -d "$INIT_BODY")"
printf '%-42s %s\n' ":443 /mcp 带令牌 initialize" "$(curl -s -o /dev/null -w '%{http_code}' $R -X POST https://$HOST/mcp -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' -d "$INIT_BODY")"
printf '%-42s %s\n' "证书链校验（不加 -k）"      "$(curl -s -o /dev/null -w '%{http_code} ssl_verify=%{ssl_verify_result}' $R https://$HOST/healthz)"
printf '%-42s %s\n' "未登记 Host 应 404"        "$(curl -s -o /dev/null -w '%{http_code}' -H 'Host: nope.example.com' http://127.0.0.1/)"
echo
echo "证书：$(openssl x509 -in "$CERT_DIR/fullchain.pem" -noout -subject -dates 2>/dev/null | tr '\n' ' ')"

# 🔴 终态断言：只打印不判定，就是"看着像成功"的假绿灯。
HZ=$(curl -s -o /dev/null -w '%{http_code}' $R https://$HOST/healthz)
NOAUTH=$(curl -s -o /dev/null -w '%{http_code}' $R -X POST https://$HOST/mcp -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' -d "$INIT_BODY")
WITHAUTH=$(curl -s -o /dev/null -w '%{http_code}' $R -X POST https://$HOST/mcp -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' -d "$INIT_BODY")
SV=$(curl -s -o /dev/null -w '%{ssl_verify_result}' $R https://$HOST/healthz)

[ "$HZ" = "200" ]      || { echo "❌ /healthz 返回 $HZ（应 200）"; exit 4; }
[ "$NOAUTH" = "401" ]  || { echo "❌ /mcp 无令牌返回 $NOAUTH（应 401，鉴权未生效？）"; exit 5; }
[ "$WITHAUTH" = "200" ]|| { echo "❌ /mcp 带令牌返回 $WITHAUTH（应 200）"; exit 7; }
[ "$SV" = "0" ]        || { echo "❌ 证书链校验失败 ssl_verify=$SV"; exit 8; }
echo "✅ 完成：https://$HOST/mcp"
