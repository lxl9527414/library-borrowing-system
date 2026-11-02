#!/usr/bin/env bash
# =====================================================================
# 部署/更新公网 AI 服务（FastAPI + LangGraph）
#
# 前置（首次部署，见 deploy/AI-SERVICE-DEPLOY.md）：
#   服务器已装 /opt/miniconda3/envs/ai（Python 3.11），
#   已有 /opt/library/config/ai.env 与 systemd 单元 library-ai.service
#
# 用法：
#   ./deploy/push-ai.sh              # 打包上传 + 重启 + 健康自检
#   ./deploy/push-ai.sh --skip-restart  # 只上传，不重启
#   DRY_RUN=1 ./deploy/push-ai.sh    # 只打印命令
#
# 环境变量（换服务器时覆盖）：
#   ECS_HOST 默认 47.98.103.160  ECS_USER 默认 root  ECS_PORT 默认 22
#   PUBLIC_URL 默认 https://lxlssr.icu
# =====================================================================
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
ECS_HOST="${ECS_HOST:-47.98.103.160}"
ECS_USER="${ECS_USER:-root}"
ECS_PORT="${ECS_PORT:-22}"
PUBLIC_URL="${PUBLIC_URL:-https://lxlssr.icu}"
TARBALL=/tmp/library-ai-service.tar.gz
RESTART=1
[ "${1:-}" = "--skip-restart" ] && RESTART=0

run() { if [ "${DRY_RUN:-0}" = "1" ]; then printf '[dry-run] %s\n' "$*"; else "$@"; fi; }

echo "==> [1/4] 打包 ai-service（排除 .venv/缓存/测试）"
run tar czf "$TARBALL" -C "$ROOT_DIR" \
  --exclude='ai-service/.venv' --exclude='__pycache__' --exclude='.pytest_cache' \
  --exclude='ai-service/tests' --exclude='ai-service/eval_report*' ai-service

echo "==> [2/4] 上传到 $ECS_USER@$ECS_HOST"
run scp -P "$ECS_PORT" "$TARBALL" "$ECS_USER@$ECS_HOST:/tmp/library-ai-service.tar.gz"

echo "==> [3/4] 解压并（可选）重启服务"
run ssh -p "$ECS_PORT" "$ECS_USER@$ECS_HOST" "bash -s -- $RESTART" <<'REMOTE'
set -euo pipefail
RESTART="${1:-1}"
APP=/opt/library/ai-service
# 保留上一份代码用于回滚
rm -rf "${APP}.previous" && cp -a "$APP" "${APP}.previous" 2>/dev/null || true
tar xzf /tmp/library-ai-service.tar.gz -C /opt/library
rm -f /tmp/library-ai-service.tar.gz
if [ "$RESTART" = "1" ]; then
  systemctl restart library-ai
  for i in $(seq 1 30); do
    if curl -fsS -o /dev/null http://127.0.0.1:8000/api/ai/health; then
      echo "AI 服务已就绪（第 ${i} 次探测）"; exit 0
    fi
    sleep 2
  done
  echo "!! AI 服务 60 秒内未就绪，最近日志："
  journalctl -u library-ai -n 30 --no-pager || true
  echo "!! 回滚：cp -a /opt/library/ai-service.previous/* /opt/library/ai-service/ && systemctl restart library-ai"
  exit 1
fi
REMOTE

echo "==> [4/4] 公网自检"
if [ "${DRY_RUN:-0}" = "1" ]; then
  echo "  [dry-run] 跳过"
else
  curl -s --max-time 15 "$PUBLIC_URL/api/ai/health" && echo
  printf "  内部接口（应 404）→ HTTP "
  curl -s -o /dev/null -w '%{http_code}\n' --max-time 10 "$PUBLIC_URL/api/ai/internal/context"
fi
echo "完成。日志：ssh -p $ECS_PORT $ECS_USER@$ECS_HOST 'journalctl -u library-ai -f'"
