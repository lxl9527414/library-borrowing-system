#!/usr/bin/env bash
# =====================================================================
# 停止 scripts/dev_up.sh 启动的全部服务（只停本脚本起的，不动系统服务）
# =====================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
DEV="$ROOT/.dev"

log() { printf '\033[1;36m[dev_down]\033[0m %s\n' "$*"; }
ok()  { printf '\033[1;32m[dev_down] ✓\033[0m %s\n' "$*"; }

stop_pidfile() {  # stop_pidfile <文件> <名称>
  local file="$1" label="$2"
  if [ -f "$file" ]; then
    local pid
    pid="$(cat "$file" 2>/dev/null || true)"
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null || true
      sleep 1
      kill -9 "$pid" 2>/dev/null || true
      ok "已停止 $label（pid $pid）"
    else
      log "$label 进程已不在（清理 pid 文件）"
    fi
    rm -f "$file"
  else
    log "$label 未运行"
  fi
}

# 1) AI 服务
stop_pidfile "$DEV/ai-service.pid" "AI 服务"

# 2) 后端（mvn spring-boot:run 会再 fork 出 java 子进程，需要按端口兜底）
stop_pidfile "$DEV/backend.pid" "业务后端"
if lsof -ti :18080 >/dev/null 2>&1; then
  lsof -ti :18080 | xargs kill 2>/dev/null || true
  sleep 1
  lsof -ti :18080 | xargs kill -9 2>/dev/null || true
  ok "已停止占用 18080 的后端进程"
fi
pkill -f "spring-boot:run" 2>/dev/null || true

# 3) 隔离 MySQL（优雅 shutdown，避免数据文件损坏）
if [ -f "$DEV/mysql.sock" ] || [ -f "$DEV/mysql.pid" ]; then
  if /usr/bin/env mysqladmin --socket="$DEV/mysql.sock" -uroot shutdown >/dev/null 2>&1; then
    ok "已停止隔离 MySQL（.dev/mysql）"
  elif [ -f "$DEV/mysql.pid" ]; then
    kill "$(cat "$DEV/mysql.pid")" 2>/dev/null || true
    ok "已按 pid 停止隔离 MySQL"
  fi
  rm -f "$DEV/mysql.pid"
else
  log "隔离 MySQL 未运行"
fi

echo
log "完成。数据仍保留在 .dev/mysql（下次启动秒起，无需重新初始化）；彻底清理：rm -rf .dev"
