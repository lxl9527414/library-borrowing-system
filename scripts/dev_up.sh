#!/usr/bin/env bash
# =====================================================================
# W1 本地一键启动：隔离 MySQL + Spring Boot 后端 + Python AI 服务（mock 模型）
#
#   bash scripts/dev_up.sh          # 启动全套并自检
#   ai-service/.venv/bin/python scripts/e2e_ai.py   # 跑端到端验证
#   bash scripts/dev_down.sh        # 停掉全套
#
# 设计原则：
#   1. 用独立端口 + 独立数据目录（./.dev/mysql），绝不碰你本机已有的 3306 数据库
#   2. 幂等：重复执行不会重复起进程；已在跑的组件会跳过
#   3. 每一步都有可读的中文日志，失败时告诉你去看哪个日志文件
# =====================================================================
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
DEV="$ROOT/.dev"
mkdir -p "$DEV"

log()  { printf '\033[1;36m[dev_up]\033[0m %s\n' "$*"; }
ok()   { printf '\033[1;32m[dev_up] ✓\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m[dev_up] ✗\033[0m %s\n' "$*" >&2; exit 1; }

# ---------- 0. 读取 .env ----------
if [ -f .env ]; then
  set -a; . ./.env; set +a
  log "已加载 .env"
else
  log "未找到 .env，使用默认值（mock 模型）。建议先 cp .env.example .env"
fi
AI_INTERNAL_TOKEN="${AI_INTERNAL_TOKEN:-dev-internal-token}"
LLM_PROVIDER="${LLM_PROVIDER:-mock}"
DB_NAME="${DB_NAME:-library_db}"
MYSQL_PORT="${MYSQL_PORT:-3308}"
BACKEND_PORT="${BACKEND_PORT:-18080}"
AI_PORT="${AI_PORT:-8000}"
REDIS_HOST="${REDIS_HOST:-127.0.0.1}"
REDIS_PORT="${REDIS_PORT:-6379}"

# ---------- 1. 前置检查 ----------
JDK17="$(/usr/libexec/java_home -v 17 2>/dev/null || true)"
if [ -n "$JDK17" ]; then
  JAVA_HOME="$JDK17"          # 项目 target=17，优先用 17，避免系统默认 JDK 27 带来的兼容问题
elif [ -n "${JAVA_HOME:-}" ] && [ -x "$JAVA_HOME/bin/java" ]; then
  log "未找到 JDK 17，回退使用 JAVA_HOME=$JAVA_HOME"
else
  die "未找到 JDK 17，请安装或手动 export JAVA_HOME=<jdk17 路径>"
fi
[ -x "$JAVA_HOME/bin/java" ] || die "JAVA_HOME 不可用：$JAVA_HOME"
export JAVA_HOME

command -v mvn >/dev/null 2>&1 || die "未找到 mvn（需要 Maven 3.9+）"
command -v curl >/dev/null 2>&1 || die "未找到 curl"

if ! redis-cli -t 2 -h "$REDIS_HOST" -p "$REDIS_PORT" ping >/dev/null 2>&1; then
  die "Redis 未运行（登录 token 依赖它）。启动方式：brew services start redis  或  docker run -d -p 6379:6379 redis:7-alpine"
fi
ok "JDK 17 / Maven / Redis 就绪（$JAVA_HOME）"

VENV="$ROOT/ai-service/.venv"
[ -x "$VENV/bin/python" ] || die "缺少 Python 虚拟环境。执行：
  python3 -m venv ai-service/.venv
  ai-service/.venv/bin/pip install -r ai-service/requirements.txt -i https://mirrors.aliyun.com/pypi/simple/ --trusted-host mirrors.aliyun.com"

# ---------- 2. 隔离 MySQL ----------
MYSQLD="${MYSQLD:-$(command -v mysqld || true)}"
if [ -z "$MYSQLD" ]; then
  for candidate in /usr/local/mysql/bin/mysqld /opt/homebrew/opt/mysql/bin/mysqld /opt/homebrew/bin/mysqld; do
    [ -x "$candidate" ] && MYSQLD="$candidate" && break
  done
fi
[ -n "$MYSQLD" ] || die "未找到 mysqld。替代方案：用 Docker（见 README 的 docker compose 说明）"
MYSQL_BIN="$(cd "$(dirname "$MYSQLD")" && pwd)"
MYSQL_BASE="$(cd "$MYSQL_BIN/.." && pwd)"
MYSQLADMIN="$MYSQL_BIN/mysqladmin"
MYSQL_CLI="$MYSQL_BIN/mysql"
MYSQL_DATA="$DEV/mysql"
MYSQL_SOCK="$DEV/mysql.sock"

cat > "$DEV/mysql.cnf" <<EOF
[mysqld]
basedir=$MYSQL_BASE
datadir=$MYSQL_DATA
port=$MYSQL_PORT
socket=$MYSQL_SOCK
pid-file=$DEV/mysql.pid
log-error=$DEV/mysql.log
mysqlx=0
EOF

if [ ! -d "$MYSQL_DATA/mysql" ]; then
  log "首次初始化隔离 MySQL 数据目录 .dev/mysql（约 20 秒）…"
  "$MYSQLD" --defaults-file="$DEV/mysql.cnf" --initialize-insecure >/dev/null 2>&1 \
    || die "MySQL 初始化失败，查看 $DEV/mysql.log"
fi

if "$MYSQLADMIN" --socket="$MYSQL_SOCK" -uroot ping >/dev/null 2>&1; then
  ok "MySQL 已在 $MYSQL_PORT 运行（复用）"
else
  log "启动 MySQL :$MYSQL_PORT（数据目录 .dev/mysql，与你的 3306 完全隔离）"
  nohup "$MYSQLD" --defaults-file="$DEV/mysql.cnf" >/dev/null 2>&1 &
  for _ in $(seq 1 60); do
    "$MYSQLADMIN" --socket="$MYSQL_SOCK" -uroot ping >/dev/null 2>&1 && break
    sleep 0.5
  done
  "$MYSQLADMIN" --socket="$MYSQL_SOCK" -uroot ping >/dev/null 2>&1 \
    || die "MySQL 启动失败，查看 $DEV/mysql.log"
  ok "MySQL 就绪 :$MYSQL_PORT"
fi
"$MYSQL_CLI" --socket="$MYSQL_SOCK" -uroot \
  -e "CREATE DATABASE IF NOT EXISTS \`$DB_NAME\` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;" \
  || die "创建数据库 $DB_NAME 失败"

# ---------- 3. 业务后端 ----------
wait_http() {  # wait_http <url> <秒数>
  local url="$1" limit="$2"
  for _ in $(seq 1 "$limit"); do
    local code
    code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 2 "$url" 2>/dev/null || true)"
    [ -n "$code" ] && [ "$code" != "000" ] && return 0
    sleep 1
  done
  return 1
}

if curl -s -o /dev/null --max-time 2 "http://127.0.0.1:$BACKEND_PORT/api/auth/login" 2>/dev/null; then
  ok "后端已在 :$BACKEND_PORT 运行（复用）"
else
  log "启动业务后端 :$BACKEND_PORT（首次编译约 30~60 秒）…"
  (
    cd backend
    JAVA_HOME="$JAVA_HOME" \
    SERVER_PORT="$BACKEND_PORT" \
    DB_URL="jdbc:mysql://127.0.0.1:$MYSQL_PORT/$DB_NAME?useUnicode=true&characterEncoding=utf8&serverTimezone=Asia/Shanghai&useSSL=false&allowPublicKeyRetrieval=true" \
    DB_USERNAME=root DB_PASSWORD= SQL_INIT_MODE=always \
    REDIS_HOST="$REDIS_HOST" REDIS_PORT="$REDIS_PORT" REDIS_PASSWORD="${REDIS_PASSWORD:-}" \
    AI_INTERNAL_TOKEN="$AI_INTERNAL_TOKEN" \
    nohup mvn -B -q spring-boot:run > "$DEV/backend.log" 2>&1 &
    echo $! > "$DEV/backend.pid"
  )
  if ! wait_http "http://127.0.0.1:$BACKEND_PORT/api/auth/login" 150; then
    die "后端 150 秒内未就绪，查看 $DEV/backend.log（常见原因：MySQL 未起 / 端口占用）"
  fi
  ok "后端就绪 http://127.0.0.1:$BACKEND_PORT（日志 .dev/backend.log）"
fi

# ---------- 4. AI 服务 ----------
if curl -s -o /dev/null --max-time 2 "http://127.0.0.1:$AI_PORT/api/ai/health" 2>/dev/null; then
  ok "AI 服务已在 :$AI_PORT 运行（复用）"
else
  log "启动 AI 服务 :$AI_PORT（LLM_PROVIDER=$LLM_PROVIDER）…"
  (
    cd ai-service
    LLM_PROVIDER="$LLM_PROVIDER" \
    JAVA_API_BASE="http://127.0.0.1:$BACKEND_PORT" \
    AI_INTERNAL_TOKEN="$AI_INTERNAL_TOKEN" \
    DEEPSEEK_API_KEY="${DEEPSEEK_API_KEY:-}" \
    DASHSCOPE_API_KEY="${DASHSCOPE_API_KEY:-}" \
    CORS_ORIGINS="${CORS_ORIGINS:-http://localhost:5173}" \
    nohup .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port "$AI_PORT" > "$DEV/ai-service.log" 2>&1 &
    echo $! > "$DEV/ai-service.pid"
  )
  if ! wait_http "http://127.0.0.1:$AI_PORT/api/ai/health" 60; then
    die "AI 服务 60 秒内未就绪，查看 $DEV/ai-service.log"
  fi
  ok "AI 服务就绪 http://127.0.0.1:$AI_PORT（日志 .dev/ai-service.log）"
fi

# ---------- 5. 自检 + 下一步 ----------
HEALTH="$(curl -s "http://127.0.0.1:$AI_PORT/api/ai/health")"
echo
echo "============================================================"
ok "全套服务已就绪"
echo "  业务后端   http://127.0.0.1:$BACKEND_PORT      （admin/admin123，reader/reader123）"
echo "  AI 服务    http://127.0.0.1:$AI_PORT$HEALTH"
echo "  MySQL     127.0.0.1:$MYSQL_PORT（root 空密码，数据目录 .dev/mysql）"
echo
echo "  验证端到端（推荐）："
echo "    ai-service/.venv/bin/python scripts/e2e_ai.py"
echo
echo "  手动试一句（SSE 流式输出，Ctrl+C 退出）："
echo "    TOKEN=\$(curl -s -X POST http://127.0.0.1:$BACKEND_PORT/api/auth/login \\"
echo "      -H 'Content-Type: application/json' -d '{\"username\":\"reader\",\"password\":\"reader123\"}' \\"
echo "      | python3 -c 'import sys,json;print(json.load(sys.stdin)[\"data\"][\"token\"])')"
echo "    curl -N -X POST http://127.0.0.1:$AI_PORT/api/ai/chat \\"
echo "      -H 'Content-Type: application/json' -H \"Authorization: Bearer \$TOKEN\" \\"
echo "      -d '{\"message\":\"帮我找红楼梦\"}'"
echo
echo "  停止全套： bash scripts/dev_down.sh"
echo "============================================================"
