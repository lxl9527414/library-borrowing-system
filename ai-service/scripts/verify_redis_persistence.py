#!/usr/bin/env python3
"""验收脚本：验证会话历史在 AI 服务重启后仍在（W2 Redis 迁移）。

流程（全部自动完成，结束时清理自己启动的进程）：
1. 用子进程在指定端口启动真实服务（LLM_PROVIDER=mock，AI_REDIS_URL 指向本机 Redis）；
2. 轮询 `/api/ai/health`，要求 `sessionStore == "redis"`；
3. 发一条 chat（SSE），等到终止事件（done / error / confirm_required）；
4. 读 `/api/ai/sessions/{id}` 拿到会话历史；
5. 停掉服务（SIGTERM），确认端口释放；用同一端口重新启动服务；
6. 再读同一 session：历史必须与重启前一致 → 打印「重启后会话仍在」。

用法（任选其一，脚本会自动找到 ai-service/ 目录）：
    cd ai-service && .venv/bin/python scripts/verify_redis_persistence.py
    ai-service/.venv/bin/python ai-service/scripts/verify_redis_persistence.py

可调参数：
    --port 8020              服务端口（默认 8020，避免与 Lead 的 8000 验收端口冲突）
    --redis-url URL          默认取环境变量 AI_REDIS_URL，否则 redis://127.0.0.1:6379/1
    --java-api-base URL      默认指向未监听端口，避免真实 Java 后端的鉴权状态影响本脚本
                             （会话持久化不依赖 Java；如需联调真实后端可显式传入）
    --session-id ID          默认自动生成唯一 sessionId
    --timeout 30             单次等待服务就绪的秒数
    --keep-server            验证完不关服务（调试用，默认关闭）

退出码：0 成功；1 验证失败；2 端口被占用 / 环境不满足。
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

import httpx

SERVICE_DIR = Path(__file__).resolve().parents[1]
DEFAULT_PORT = 8020
DEFAULT_REDIS_URL = "redis://127.0.0.1:6379/1"
DEFAULT_MESSAGE = "帮我找三体"
TERMINAL_EVENTS = {"done", "error", "confirm_required"}


# ----------------------------------------------------------------------
# 小工具
# ----------------------------------------------------------------------
def log(message: str) -> None:
    print(message, flush=True)


def _port_is_free(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.3)
        return sock.connect_ex((host, port)) != 0


def _wait_port_free(host: str, port: int, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _port_is_free(host, port):
            return True
        time.sleep(0.1)
    return _port_is_free(host, port)


def _tail(path: str, lines: int = 40) -> str:
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return "\n".join(text.splitlines()[-lines:])


def _parse_sse(lines) -> list[tuple[str, dict[str, Any]]]:
    events: list[tuple[str, dict[str, Any]]] = []
    event_name: str | None = None
    data_lines: list[str] = []
    for raw in lines:
        line = raw.rstrip("\r") if isinstance(raw, str) else raw.decode("utf-8")
        if line.startswith("event:"):
            event_name = line.split(":", 1)[1].strip()
        elif line.startswith("data:"):
            data_lines.append(line.split(":", 1)[1].strip())
        elif line == "":
            if event_name:
                try:
                    payload = json.loads(data_lines[-1]) if data_lines else {}
                except ValueError:
                    payload = {}
                events.append((event_name, payload))
            event_name, data_lines = None, []
    if event_name and data_lines:  # 兜底：最后一个事件没有空行结尾
        try:
            events.append((event_name, json.loads(data_lines[-1])))
        except ValueError:
            pass
    return events


# ----------------------------------------------------------------------
# 服务进程管理
# ----------------------------------------------------------------------
class ServiceProcess:
    """在固定端口起停 uvicorn（每次重启都是全新进程 = 真正的重启验证）。"""

    def __init__(
        self, *, host: str, port: int, redis_url: str, java_api_base: str, log_prefix: str
    ) -> None:
        self.host = host
        self.port = port
        self.redis_url = redis_url
        self.java_api_base = java_api_base
        self._log_prefix = log_prefix
        self._proc: subprocess.Popen[bytes] | None = None
        self._log_path: str | None = None

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def start(self, timeout: float) -> None:
        if not _port_is_free(self.host, self.port):
            raise SystemExit(
                f"端口 {self.port} 已被占用，请换一个端口：--port <PORT>（例如 8021）"
            )
        fd, log_path = tempfile.mkstemp(prefix=f"ai-verify-{self._log_prefix}-", suffix=".log")
        os.close(fd)
        self._log_path = log_path

        env = dict(os.environ)
        env["LLM_PROVIDER"] = "mock"
        env["AI_REDIS_URL"] = self.redis_url
        env["JAVA_API_BASE"] = self.java_api_base
        env["AI_INTERNAL_TOKEN"] = "verify-internal-token"
        env["PYTHONUNBUFFERED"] = "1"
        env["PYTHONPATH"] = (
            f"{SERVICE_DIR}{os.pathsep}{env['PYTHONPATH']}" if env.get("PYTHONPATH") else str(SERVICE_DIR)
        )
        cmd = [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            self.host,
            "--port",
            str(self.port),
            "--log-level",
            "warning",
        ]
        log(f"▶ 启动 AI 服务（{self._log_prefix}）：{' '.join(cmd)}")
        with open(log_path, "wb") as log_file:
            self._proc = subprocess.Popen(cmd, cwd=str(SERVICE_DIR), env=env, stdout=log_file, stderr=subprocess.STDOUT)
        try:
            self._wait_health(timeout)
        except Exception:
            self._dump_log()
            raise

    def _wait_health(self, timeout: float) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        last_error: str = ""
        with httpx.Client(trust_env=False, timeout=3.0) as client:
            while time.monotonic() < deadline:
                if self._proc is not None and self._proc.poll() is not None:
                    raise RuntimeError(f"服务进程提前退出（exit={self._proc.returncode}）")
                try:
                    response = client.get(f"{self.base_url}/api/ai/health")
                    if response.status_code == 200:
                        body = response.json()
                        if body.get("sessionStore") == "redis":
                            log(
                                f"✓ 服务就绪：sessionStore={body.get('sessionStore')}，"
                                f"checkpointer={body.get('checkpointer')}，llmProvider={body.get('llmProvider')}"
                            )
                            if body.get("checkpointer") != "redis":
                                log(
                                    "⚠ 注意：checkpointer=memory（本机 Redis 缺少 RedisJSON/RediSearch 模块，"
                                    "AsyncRedisSaver 自动降级；会话历史仍由 Redis 持久化）"
                                )
                            return body
                        raise RuntimeError(
                            "health 显示 sessionStore="
                            f"{body.get('sessionStore')!r}，Redis 未生效。请确认 Redis 可用且 AI_REDIS_URL 正确："
                            f" {self.redis_url}"
                        )
                except (httpx.HTTPError, RuntimeError) as exc:
                    last_error = str(exc)
                time.sleep(0.2)
        raise RuntimeError(f"等待服务就绪超时（{timeout}s）：{last_error}")

    def stop(self) -> None:
        proc = self._proc
        self._proc = None
        if proc is None:
            return
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
        if not _wait_port_free(self.host, self.port):
            raise RuntimeError(f"服务进程已退出但端口 {self.port} 仍被占用")
        log(f"■ 已停止 AI 服务（{self._log_prefix}），端口 {self.port} 已释放")

    def _dump_log(self) -> None:
        if self._log_path:
            tail = _tail(self._log_path)
            if tail:
                print("--- 服务日志（尾部）---", file=sys.stderr)
                print(tail, file=sys.stderr)
                print("------------------------", file=sys.stderr)

    def cleanup_files(self) -> None:
        if self._log_path:
            try:
                os.unlink(self._log_path)
            except OSError:
                pass
            self._log_path = None


# ----------------------------------------------------------------------
# HTTP 步骤
# ----------------------------------------------------------------------
def chat_and_wait_terminal(base_url: str, session_id: str, message: str) -> list[tuple[str, dict[str, Any]]]:
    headers = {"Authorization": "Bearer verify-token", "Accept": "text/event-stream"}
    with httpx.Client(trust_env=False, timeout=60.0) as client:
        with client.stream(
            "POST",
            f"{base_url}/api/ai/chat",
            json={"sessionId": session_id, "message": message},
            headers=headers,
        ) as response:
            response.raise_for_status()
            events = _parse_sse(response.iter_lines())
    names = [name for name, _ in events]
    log(f"  chat 事件序列：{names}")
    if not names or names[-1] not in TERMINAL_EVENTS:
        raise AssertionError(f"SSE 流未以终止事件结束：{names}")
    if names[-1] == "confirm_required":
        raise AssertionError("验证脚本期望一条只读对话，不该出现 confirm_required")
    return events


def fetch_history(base_url: str, session_id: str, *, retries: int = 25) -> list[dict[str, Any]]:
    """落库发生在 SSE 生成器的 finally 里，读之前做有限重试。"""
    last: list[dict[str, Any]] = []
    with httpx.Client(trust_env=False, timeout=5.0) as client:
        for _ in range(retries):
            response = client.get(f"{base_url}/api/ai/sessions/{session_id}")
            response.raise_for_status()
            messages = response.json().get("messages") or []
            if messages:
                return messages
            last = messages
            time.sleep(0.2)
    return last


def _cleanup_redis_keys(redis_url: str, session_id: str) -> None:
    """只清理本脚本产生的 key（session + 该 thread 的 checkpoint，best-effort）。"""
    try:
        import redis  # noqa: PLC0415 - 脚本内可选依赖
    except ImportError:
        return
    try:
        client = redis.Redis.from_url(redis_url, decode_responses=True, socket_connect_timeout=1.0)
        keys = [f"ai:session:{session_id}"]
        for pattern in ("ai:checkpoint*", "ai:checkpoint_write*"):
            for key in client.scan_iter(match=pattern, count=200):
                if session_id in key:
                    keys.append(key)
        if keys:
            client.delete(*keys)
        client.close()
    except Exception:  # best-effort，清理失败不影响验收结论
        pass


# ----------------------------------------------------------------------
# 主流程
# ----------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="验证 AI 服务重启后 Redis 中的会话历史仍在")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--redis-url", default=os.environ.get("AI_REDIS_URL") or DEFAULT_REDIS_URL)
    parser.add_argument(
        "--java-api-base",
        default=os.environ.get("VERIFY_JAVA_API_BASE") or "http://127.0.0.1:18099",
        help="Java 工具接口地址（默认指向未监听端口，让对话在无 Java 后端时也能稳定走完 done）",
    )
    parser.add_argument("--session-id", default=f"verify-{uuid.uuid4().hex[:12]}")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--keep-server", action="store_true", help="验证完保留服务进程（调试用）")
    args = parser.parse_args(argv)

    session_id = args.session_id
    service = ServiceProcess(
        host=args.host, port=args.port, redis_url=args.redis_url,
        java_api_base=args.java_api_base, log_prefix="run1",
    )
    restarted = ServiceProcess(
        host=args.host, port=args.port, redis_url=args.redis_url,
        java_api_base=args.java_api_base, log_prefix="run2",
    )
    keep = False
    try:
        log(
            f"== 步骤 1/4：首次启动服务（Redis={args.redis_url}，port={args.port}，"
            f"JAVA_API_BASE={args.java_api_base}）=="
        )
        service.start(args.timeout)

        log("== 步骤 2/4：发一条 mock 对话，确认会话已写入 Redis ==")
        events = chat_and_wait_terminal(service.base_url, session_id, DEFAULT_MESSAGE)
        terminal = events[-1]
        log(f"  终止事件：{terminal[0]}")

        before = fetch_history(service.base_url, session_id)
        if not before:
            raise AssertionError("首次启动后 /sessions 没有历史，会话未写入存储")
        log(f"  重启前会话历史：{len(before)} 条消息（sessionId={session_id}）")

        log("== 步骤 3/4：重启服务（新进程，模拟服务更新/崩溃恢复）==")
        service.stop()
        restarted.start(args.timeout)

        log("== 步骤 4/4：重启后再读同一 session ==")
        after = fetch_history(restarted.base_url, session_id)
        if not after:
            raise AssertionError("重启后 /sessions 历史为空，会话没有持久化到 Redis")
        if after != before:
            raise AssertionError(f"重启前后会话历史不一致：before={before}，after={after}")

        log(f"✅ 重启后会话仍在：共 {len(after)} 条消息（sessionId={session_id}）")
        log(f"   首条消息：{json.dumps(after[0], ensure_ascii=False)}")
        log(f"   末条消息：{json.dumps(after[-1], ensure_ascii=False)}")
        log("== 验证通过：会话状态已持久化到 Redis，服务重启后历史可恢复 ==")
        return 0
    except SystemExit:
        raise
    except Exception as exc:
        print(f"❌ 验证失败：{type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        return 1
    finally:
        keep = args.keep_server
        if not keep:
            for proc in (service, restarted):
                try:
                    proc.stop()
                except Exception as exc:  # noqa: BLE001 - 清理阶段不掩盖验收结论
                    print(f"⚠ 停止服务时出现问题：{exc}", file=sys.stderr, flush=True)
            _cleanup_redis_keys(args.redis_url, session_id)
        for proc in (service, restarted):
            proc.cleanup_files()
        if keep:
            log(f"⚠ --keep-server：服务保留在 {restarted.base_url}（记得手动关闭）")


if __name__ == "__main__":
    sys.exit(main())
