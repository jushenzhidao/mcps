# MCP 网关反代（回环 39020）
proxy_pass http://127.0.0.1:39020;
proxy_http_version 1.1;
proxy_set_header Host              $host;
proxy_set_header X-Real-IP         $remote_addr;
proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
proxy_set_header X-Forwarded-Proto $scheme;

# MCP Streamable HTTP 是 SSE 长连接：必须关缓冲、放宽读超时，
# 否则事件被缓冲、长任务被掐断。
proxy_buffering off;
proxy_cache off;
proxy_read_timeout 900s;
proxy_send_timeout 900s;
proxy_set_header Connection "";

# 生图请求体可能含多张参考图（base64 JSON）
client_max_body_size 30m;
