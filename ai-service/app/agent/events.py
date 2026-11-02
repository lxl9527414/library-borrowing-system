"""Agent 事件模型与事件出口。

事件有两条来源，最终都汇入同一条 SSE 流：
1. 图节点内部：用 LangGraph 官方 `get_stream_writer()` 推送（见 RunContext.emit）；
2. 图执行结束后：runner 直接写 done / error / confirm_required 事件。

兼容性说明：LangGraph 1.x 的 `get_stream_writer()` / `interrupt()` 依赖
`var_child_runnable_config` contextvar 在 asyncio 任务间传播，而这一点只有
Python >= 3.11 才自动成立；本项目运行在 Python 3.10，所以在节点外层用
`_with_config_context` 显式设置该 contextvar（见 app/agent/graph.py），
使官方 API 在 3.10 下行为与 3.11 完全一致。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any


@dataclass
class AgentEvent:
    """一个 SSE 事件（event 名 + data）。"""

    event: str
    data: dict[str, Any]


# 内部哨兵：表示图执行彻底结束（避免与用户事件重名，前缀双下划线）
END_OF_STREAM = AgentEvent("__end__", {})


class EventEmitter:
    """把节点内的自定义事件汇入 SSE 事件队列（单请求一个实例）。"""

    def __init__(self) -> None:
        self.queue: asyncio.Queue[AgentEvent] = asyncio.Queue()

    def emit(self, event: str, data: dict[str, Any] | None = None) -> None:
        self.queue.put_nowait(AgentEvent(event, data or {}))

    def close(self) -> None:
        self.queue.put_nowait(END_OF_STREAM)


@dataclass
class AgentRunStats:
    """单次请求的模型用量与工具调用统计（done 事件用）。"""

    prompt_tokens: int = 0
    completion_tokens: int = 0

    def add_usage(self, prompt_tokens: int, completion_tokens: int) -> None:
        self.prompt_tokens += prompt_tokens
        self.completion_tokens += completion_tokens

    def as_usage(self) -> dict[str, int]:
        return {
            "promptTokens": self.prompt_tokens,
            "completionTokens": self.completion_tokens,
        }
