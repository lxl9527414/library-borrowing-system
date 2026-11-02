#!/usr/bin/env bash
# =====================================================================
# 一键演示录制：起依赖 → 走完整业务流程 → 录帧 → 合成 GIF
#
#   bash scripts/demo.sh                 # 产出 docs/demo.gif
#   bash scripts/demo.sh --no-record     # 只跑流程（不录 GIF，调试用）
#
# 依赖：node（自带 WebSocket）、系统 python3 + Pillow（Pillow 仅用于合成 GIF）
# =====================================================================
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

log() { printf '\033[1;36m[demo]\033[0m %s\n' "$*"; }
ok()  { printf '\033[1;32m[demo] ✓\033[0m %s\n' "$*"; }
die() { printf '\033[1;31m[demo] ✗\033[0m %s\n' "$*" >&2; exit 1; }

port_up() { curl -s -o /dev/null --max-time 2 "http://127.0.0.1:$1$2" 2>/dev/null; }

# 1) 后端 + AI 服务
if port_up 18080 /api/auth/login && port_up 8000 /api/ai/health; then
  ok "后端(18080) 与 AI 服务(8000) 已在运行"
else
  log "启动后端与 AI 服务…"
  bash scripts/dev_up.sh >/dev/null 2>&1 || die "dev_up.sh 失败，查看 .dev/*.log"
  ok "后端与 AI 服务已启动"
fi

# 2) 前端 dev server（5173，负责把 /api/ai 代理到 AI 服务）
STARTED_VITE=0
if port_up 5173 /chat; then
  ok "前端(5173) 已在运行"
else
  log "启动前端 dev server…"
  ( cd frontend && npm run dev > /tmp/demo-vite.log 2>&1 & )
  STARTED_VITE=1
  for _ in $(seq 1 40); do port_up 5173 /chat && break; sleep 1; done
  port_up 5173 /chat || die "前端启动失败，查看 /tmp/demo-vite.log"
  ok "前端已就绪"
fi

# 3) 录制
if [ "${1:-}" = "--no-record" ]; then
  node scripts/demo_record.mjs --no-record
else
  node scripts/demo_record.mjs
  log "合成 GIF…"
  python3 scripts/frames_to_gif.py --out docs/demo.gif
  ok "完成：docs/demo.gif"
fi

# 4) 若本次自己起的 vite，录制完保留（方便你继续体验）；提示如何关闭
if [ "$STARTED_VITE" = "1" ]; then
  log "前端仍在运行（http://localhost:5173/chat），关闭：lsof -ti :5173 | xargs kill"
fi
