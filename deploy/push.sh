#!/usr/bin/env bash
set -euo pipefail

# 把本地打好的 jar 发布到 ECS 并重启服务。
#
# 用法:
#   ./deploy/push.sh               # 打包 + 上传 + 重启 + 自检
#   ./deploy/push.sh --skip-build  # 已经打过包, 只上传重启
#   ./deploy/push.sh --rollback    # 回滚到服务器上的上一个版本
#   DRY_RUN=1 ./deploy/push.sh --skip-build   # 只打印要执行的命令, 不真正执行
#
# 可覆盖的环境变量(换服务器时再改):
#   ECS_HOST   默认 47.98.103.160
#   ECS_USER   默认 root
#   ECS_PORT   默认 22
#   PUBLIC_URL 默认 https://lxlssr.icu

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
JAR="$ROOT_DIR/backend/target/library-server.jar"

ECS_HOST="${ECS_HOST:-47.98.103.160}"
ECS_USER="${ECS_USER:-root}"
ECS_PORT="${ECS_PORT:-22}"
PUBLIC_URL="${PUBLIC_URL:-https://lxlssr.icu}"

MODE="deploy"
BUILD=1

for arg in "$@"; do
    case "$arg" in
        --skip-build) BUILD=0 ;;
        --rollback) MODE="rollback" ;;
        --help|-h) sed -n '4,16p' "$0"; exit 0 ;;
        *) echo "未知参数: $arg (用 --help 查看用法)" >&2; exit 1 ;;
    esac
done

run() {
    if [ "${DRY_RUN:-0}" = "1" ]; then
        printf '[dry-run] %s\n' "$*"
    else
        "$@"
    fi
}

remote_script() {
    cat <<'REMOTE'
set -euo pipefail
APP_DIR=/opt/library/app

wait_ready() {
    for i in $(seq 1 45); do
        if curl -fsS -o /dev/null http://127.0.0.1:18080/; then
            echo "应用已就绪(第 ${i} 次探测通过)"
            return 0
        fi
        sleep 2
    done
    echo "!! 应用 90 秒内没有就绪, 最近 30 行日志:"
    journalctl -u library-server -n 30 --no-pager || true
    echo "!! 可回滚: 在服务器执行 cp -a ${APP_DIR}/library-server.jar.previous ${APP_DIR}/library-server.jar && systemctl restart library-server"
    return 1
}

if [ "${1}" = "rollback" ]; then
    if [ ! -f "${APP_DIR}/library-server.jar.previous" ]; then
        echo "!! 服务器上没有可回滚的备份 ${APP_DIR}/library-server.jar.previous"
        exit 1
    fi
    cp -a "${APP_DIR}/library-server.jar.previous" "${APP_DIR}/library-server.jar"
    systemctl restart library-server
    wait_ready
    echo "已回滚到上一个版本"
else
    if [ ! -f /root/library-server.jar.new ]; then
        echo "!! 服务器上没有找到 /root/library-server.jar.new"
        exit 1
    fi
    cp -a "${APP_DIR}/library-server.jar" "${APP_DIR}/library-server.jar.previous"
    install -o library -g library -m 0644 /root/library-server.jar.new "${APP_DIR}/library-server.jar"
    rm -f /root/library-server.jar.new
    systemctl restart library-server
    wait_ready
    echo "jar 修改时间: $(stat -c '%y' "${APP_DIR}/library-server.jar")"
fi
REMOTE
}

run_remote() {
    local body
    body="$(remote_script "$1")"
    if [ "${DRY_RUN:-0}" = "1" ]; then
        printf '[dry-run] 将在服务器上执行 (mode=%s):\n%s\n' "$1" "$body"
    else
        printf '%s\n' "$body" | ssh -p "$ECS_PORT" "$ECS_USER@$ECS_HOST" "bash -s -- $1"
    fi
}

if [ "$MODE" = "rollback" ]; then
    echo "==> 回滚到上一个版本 ($ECS_USER@$ECS_HOST)"
    run_remote rollback
else
    if [ "$BUILD" = "1" ]; then
        echo "==> [1/4] 本地打包(前端 + 后端)"
        run "$ROOT_DIR/build.sh"
    else
        echo "==> [1/4] 跳过打包"
    fi

    if [ ! -f "$JAR" ] && [ "${DRY_RUN:-0}" != "1" ]; then
        echo "!! 找不到 $JAR, 先执行 ./build.sh 或去掉 --skip-build" >&2
        exit 1
    fi

    echo "==> [2/4] 上传 jar 到 $ECS_USER@$ECS_HOST"
    run scp -P "$ECS_PORT" "$JAR" "$ECS_USER@$ECS_HOST:/root/library-server.jar.new"

    echo "==> [3/4] 备份旧版本, 替换 jar 并重启服务"
    run_remote deploy
fi

if [ "${DRY_RUN:-0}" = "1" ]; then
    echo "==> [dry-run] 跳过公网自检"
else
    echo "==> 公网自检: $PUBLIC_URL"
    HTTP_CODE="$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 "$PUBLIC_URL" || true)"
    echo "HTTP $HTTP_CODE"
    if [ "$HTTP_CODE" != "200" ]; then
        echo "注意: 公网返回 $HTTP_CODE, Cloudflare 可能有缓存或回源延迟, 用无痕窗口再试一次"
    fi
fi

echo
echo "完成。查看日志: ssh -p $ECS_PORT $ECS_USER@$ECS_HOST 'journalctl -u library-server -f'"
