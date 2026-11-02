"""Langfuse 可观测性接入（W4-1）

为什么不用官方 `langfuse` SDK：
- SDK v3/v4 的 API 变化较大（`trace/span` → `observation`），且引入额外依赖；
- Langfuse 的 **ingestion HTTP 协议**是稳定契约（`POST /api/public/ingestion`，Basic Auth），
  直接发 batch 事件即可，还能用 `httpx.MockTransport` 完全离线测试。

埋点方式：`RunContext.emit()` 是所有 SSE 事件的唯一出口，在这里把事件转成 Langfuse 的
trace / observation / generation，业务代码零侵入。

设计要点：
1. **默认 Noop**：未配置 Key 或 `LANGFUSE_ENABLED=false` 时什么都不发，不影响主链路；
2. **永不抛异常**：任何网络/序列化错误只 log.warn 一次并自禁用；
3. **按 trace 缓冲**：一个用户回合的事件攒成一个 batch，在 done/error 时一次发出，减少请求数；
4. **字段与结构化日志对齐**：traceId / sessionId / tool / durationMs / code。
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.obs.logging import get_logger

logger = get_logger("obs.langfuse")

# Langfuse ingestion 的事件类型（只用到这三种）
EVENT_TRACE = "trace-create"
EVENT_OBSERVATION = "observation-create"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


@dataclass
class TracerConfig:
    enabled: bool = False
    host: str = "http://127.0.0.1:3000"
    public_key: str = ""
    secret_key: str = ""
    timeout_seconds: float = 5.0
    # 采样率：1.0 全量；小流量演示场景默认全量
    sample_rate: float = 1.0

    @property
    def configured(self) -> bool:
        return bool(self.enabled and self.public_key and self.secret_key and self.host)


class NoopTracer:
    """未启用时的空实现：所有方法都是 no-op，保证调用方无需判空。"""

    enabled = False

    def on_agent_event(self, event: str, data: dict[str, Any] | None = None, *,
                       trace_id: str = "", session_id: str = "") -> None:  # noqa: D401
        return None

    def flush(self) -> None:
        return None


class LangfuseTracer:
    """把 Agent 事件转成 Langfuse trace/observation/generation，并在回合结束时批量上报。"""

    enabled = True

    def __init__(self, config: TracerConfig, transport: Any = None) -> None:
        self.config = config
        self._transport = transport  # 测试注入 httpx.MockTransport
        self._buffers: dict[str, list[dict[str, Any]]] = {}
        self._tool_started: dict[str, float] = {}
        self._failed = False
        self._warned = False
        self._sample = self._should_sample()

    # ------------------------------------------------------------------
    # 对外：事件入口
    # ------------------------------------------------------------------
    def on_agent_event(self, event: str, data: dict[str, Any] | None = None, *,
                       trace_id: str = "", session_id: str = "") -> None:
        if self._failed or not self._sample or not trace_id:
            return
        payload = data or {}
        try:
            if event == "tool_call":
                self._ensure_trace(trace_id, session_id)
                self._tool_started[str(payload.get("callId"))] = time.perf_counter()
            elif event == "tool_result":
                self._record_tool(trace_id, payload)
            elif event == "done":
                self._record_generation(trace_id, payload)
                self._append(trace_id, EVENT_TRACE, {
                    "id": trace_id,
                    "timestamp": _now_iso(),
                    "name": "library-agent-turn",
                    "sessionId": session_id or None,
                    "output": {"latencyMs": payload.get("latencyMs")},
                    "metadata": {"usage": payload.get("usage") or {}},
                })
                self.flush(trace_id)
            elif event == "error":
                self._append(trace_id, EVENT_TRACE, {
                    "id": trace_id,
                    "timestamp": _now_iso(),
                    "name": "library-agent-turn",
                    "sessionId": session_id or None,
                    "level": "ERROR",
                    "statusMessage": payload.get("message") or payload.get("code"),
                })
                self.flush(trace_id)
        except Exception as exc:  # noqa: BLE001 - 可观测性绝不能影响主链路
            self._warn_once(f"Langfuse 埋点失败（已忽略）：{type(exc).__name__}: {exc}")

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------
    def _should_sample(self) -> bool:
        rate = self.config.sample_rate
        if rate >= 1.0:
            return True
        if rate <= 0.0:
            return False
        return int(time.time() * 1000) % 100 < int(rate * 100)

    def _ensure_trace(self, trace_id: str, session_id: str) -> None:
        if trace_id in self._buffers:
            return
        self._buffers[trace_id] = []
        self._append(trace_id, EVENT_TRACE, {
            "id": trace_id,
            "timestamp": _now_iso(),
            "name": "library-agent-turn",
            "sessionId": session_id or None,
        })

    def _record_tool(self, trace_id: str, payload: dict[str, Any]) -> None:
        self._ensure_trace(trace_id, "")
        call_id = str(payload.get("callId"))
        started = self._tool_started.pop(call_id, None)
        duration_ms = round((time.perf_counter() - started) * 1000, 1) if started else None
        self._append(trace_id, EVENT_OBSERVATION, {
            "id": f"{trace_id}-{call_id}",
            "traceId": trace_id,
            "type": "SPAN",
            "name": f"tool:{payload.get('name')}",
            "startTime": _now_iso(),
            "endTime": _now_iso(),
            "input": payload.get("args"),
            "output": {"ok": payload.get("ok"), "summary": payload.get("summary")},
            "metadata": {"durationMs": duration_ms} if duration_ms is not None else {},
            "level": "DEFAULT" if payload.get("ok") else "WARNING",
        })

    def _record_generation(self, trace_id: str, payload: dict[str, Any]) -> None:
        """done 事件带该轮累计 usage：记成一条 GENERATION observation，用于成本统计。"""
        usage = payload.get("usage") or {}
        prompt = usage.get("promptTokens") or 0
        completion = usage.get("completionTokens") or 0
        if not prompt and not completion:
            return
        self._append(trace_id, EVENT_OBSERVATION, {
            "id": f"{trace_id}-generation",
            "traceId": trace_id,
            "type": "GENERATION",
            "name": "llm-generation",
            "startTime": _now_iso(),
            "endTime": _now_iso(),
            "model": payload.get("model"),
            "usageDetails": {"input": prompt, "output": completion, "total": prompt + completion},
            "metadata": {"latencyMs": payload.get("latencyMs")},
        })

    def _append(self, trace_id: str, event_type: str, body: dict[str, Any]) -> None:
        buffer = self._buffers.setdefault(trace_id, [])
        buffer.append({
            "id": str(uuid.uuid4()),
            "type": event_type,
            "timestamp": _now_iso(),
            "body": body,
        })

    def flush(self, trace_id: str | None = None) -> None:
        """把缓冲区里的 batch 发到 Langfuse；失败只告警并自禁用。"""
        if self._failed or not self.config.configured:
            return
        targets = [trace_id] if trace_id else list(self._buffers)
        for key in targets:
            batch = self._buffers.pop(key, None)
            if not batch:
                continue
            try:
                import httpx

                client = httpx.Client(timeout=self.config.timeout_seconds, transport=self._transport)
                resp = client.post(
                    f"{self.config.host.rstrip('/')}/api/public/ingestion",
                    auth=(self.config.public_key, self.config.secret_key),
                    json={"batch": batch},
                )
                if resp.status_code >= 300:
                    raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:120]}")
            except Exception as exc:  # noqa: BLE001
                self._failed = True
                self._warn_once(f"Langfuse 上报失败，后续不再上报：{type(exc).__name__}: {exc}")

    def _warn_once(self, message: str) -> None:
        if self._warned:
            return
        self._warned = True
        try:
            logger.warning(message, extra={"code": "LANGFUSE_ERROR"})
        except Exception:  # noqa: BLE001 - 连日志都失败就彻底放弃
            pass


_singleton: Any = None


def configure_tracer(config: TracerConfig | None = None, transport: Any = None) -> Any:
    """显式配置（测试或启动时调用）。未配置或不满足条件时返回 NoopTracer。"""
    global _singleton
    if config is None or not config.configured:
        _singleton = NoopTracer()
    else:
        _singleton = LangfuseTracer(config, transport=transport)
    return _singleton


def get_tracer() -> Any:
    """懒加载单例：从 Settings 读 LANGFUSE_* 配置；任何异常都退化为 Noop。"""
    global _singleton
    if _singleton is not None:
        return _singleton
    try:
        from app.config import get_settings

        settings = get_settings()
        config = TracerConfig(
            enabled=bool(getattr(settings, "langfuse_enabled", False)),
            host=str(getattr(settings, "langfuse_host", "") or ""),
            public_key=str(getattr(settings, "langfuse_public_key", "") or ""),
            secret_key=str(getattr(settings, "langfuse_secret_key", "") or ""),
        )
        _singleton = LangfuseTracer(config) if config.configured else NoopTracer()
    except Exception:  # noqa: BLE001
        _singleton = NoopTracer()
    return _singleton


def reset_tracer() -> None:
    """测试用：清空单例。"""
    global _singleton
    _singleton = None


def tracer_payload_preview(tracer: Any) -> str:
    """调试辅助：返回当前缓冲区摘要（不发送网络）。"""
    buffers = getattr(tracer, "_buffers", {})
    return json.dumps({k: len(v) for k, v in buffers.items()}, ensure_ascii=False)
