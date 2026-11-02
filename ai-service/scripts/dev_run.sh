#!/usr/bin/env bash
# 本地开发启动脚本（无参数）：
#   bash scripts/dev_run.sh
# 默认 mock 模型 + 8000 端口，不需要任何 API Key；
# 可用环境变量覆盖：LLM_PROVIDER / AI_PORT / PYTHON_BIN
set -euo pipefail

SERVICE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$SERVICE_DIR"

PYTHON_BIN="${PYTHON_BIN:-$SERVICE_DIR/.venv/bin/python}"
if [ ! -x "$PYTHON_BIN" ]; then
  echo "未找到 Python 解释器：$PYTHON_BIN" >&2
  echo "请先在 ai-service/ 下创建虚拟环境并安装依赖：" >&2
  echo "  python3.10 -m venv .venv && .venv/bin/pip install -i https://mirrors.aliyun.com/pypi/simple/ --trusted-host mirrors.aliyun.com -r requirements.txt" >&2
  exit 1
fi

# 无 Key 时默认 mock 模式，保证脚本开箱可用
export LLM_PROVIDER="${LLM_PROVIDER:-mock}"
export PYTHONPATH="$SERVICE_DIR${PYTHONPATH:+:$PYTHONPATH}"
PORT="${AI_PORT:-8000}"

echo "启动 AI 服务：LLM_PROVIDER=$LLM_PROVIDER  http://127.0.0.1:$PORT/api/ai/health  (Ctrl+C 退出)"
exec "$PYTHON_BIN" -m uvicorn app.main:app --host 127.0.0.1 --port "$PORT" --reload
