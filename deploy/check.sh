#!/usr/bin/env bash
set -euo pipefail

# 一条命令检查 ECS 上的运行状态, 演示前建议先跑一次。
#
# 用法:
#   ./deploy/check.sh
#   ECS_HOST=1.2.3.4 PUBLIC_URL=https://example.com ./deploy/check.sh

ECS_HOST="${ECS_HOST:-47.98.103.160}"
ECS_USER="${ECS_USER:-root}"
ECS_PORT="${ECS_PORT:-22}"
PUBLIC_URL="${PUBLIC_URL:-https://lxlssr.icu}"

echo "== ECS 服务状态 ($ECS_USER@$ECS_HOST) =="
ssh -p "$ECS_PORT" "$ECS_USER@$ECS_HOST" "bash -s" <<'REMOTE'
set -u

echo "--- systemd 服务 (active 为正常) ---"
for svc in mysqld redis-library library-server nginx; do
    printf '%-16s %s\n' "$svc" "$(systemctl is-active "$svc" 2>/dev/null || true)"
done

echo "--- 监听端口 (只应看到本机回环) ---"
ss -lntp | head -1
ss -lntp | grep -E ':(80|443|18080|3306|6379)\b' || true

echo "--- 资源 ---"
free -m | head -2
df -h / | tail -1

echo "--- 应用自检 ---"
APP_CODE="$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:18080/ || true)"
echo "127.0.0.1:18080 -> HTTP $APP_CODE"
echo "jar 修改时间: $(stat -c '%y' /opt/library/app/library-server.jar 2>/dev/null || echo '找不到 jar')"
REMOTE

echo
echo "== 公网检查 =="
PUBLIC_CODE="$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 "$PUBLIC_URL" || true)"
echo "$PUBLIC_URL -> HTTP $PUBLIC_CODE"
