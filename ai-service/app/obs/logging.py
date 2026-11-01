"""结构化日志：JSON 行输出，统一带 traceId / sessionId / tool / durationMs。

W1 只做“日志可检索”，字段命名与 Langfuse 的 trace / span 对齐，
W4 接入 Langfuse 时只需在 logger 上加一个 handler 转发到 SDK，无需改动业务代码。
"""

from __future__ import annotations

import json
import logging
import sys
import time
import uuid
from datetime import datetime, timezone
from typing import Any

# 结构化字段白名单：只有这些 extra 会被写进 JSON，避免把任意对象打进日志
_STRUCTURED_FIELDS = (
    "traceId",
    "sessionId",
    "tool",
    "durationMs",
    "code",
    "provider",
    "step",
    "event",
    "confirmationId",
)

_configured = False


class JsonLineFormatter(logging.Formatter):
    """单行 JSON 格式化器：便于 grep / ELK / Loki 采集。"""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for field in _STRUCTURED_FIELDS:
            value = getattr(record, field, None)
            if value is not None:
                payload[field] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def setup_logging(level: str = "INFO") -> None:
    """初始化根日志（幂等，重复调用只调整级别）。"""
    global _configured
    root = logging.getLogger()
    if not _configured:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(JsonLineFormatter())
        root.handlers = [handler]
        root.setLevel(level.upper())
        # 压掉第三方库的噪音日志
        for noisy in ("httpx", "httpcore", "openai", "urllib3"):
            logging.getLogger(noisy).setLevel(logging.WARNING)
        _configured = True
    else:
        root.setLevel(level.upper())


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"ai-service.{name}")


def new_trace_id() -> str:
    """每次请求生成一个 traceId，串联同一次对话的所有日志。"""
    return f"t-{uuid.uuid4().hex[:16]}"


class Stopwatch:
    """毫秒级计时器，配合结构化日志记录耗时。"""

    def __init__(self) -> None:
        self._start = time.perf_counter()

    @property
    def elapsed_ms(self) -> int:
        return int((time.perf_counter() - self._start) * 1000)

    def __enter__(self) -> "Stopwatch":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        return None

