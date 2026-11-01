"""HTTP 请求/响应模型（与契约 §4 字段保持一致）。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    sessionId: str | None = Field(default=None, description="会话 ID，空则由服务端生成")
    message: str = Field(min_length=1, max_length=4000, description="用户输入")


class ConfirmRequest(BaseModel):
    sessionId: str = Field(min_length=1, description="会话 ID")
    confirmationId: str = Field(min_length=1, description="confirm_required 事件返回的确认 ID")
    approved: bool = Field(description="true=执行写操作，false=取消")


class SessionMessage(BaseModel):
    role: str
    content: str
    name: str | None = None
    toolCalls: list[dict[str, Any]] | None = None


class SessionResponse(BaseModel):
    sessionId: str
    messages: list[SessionMessage] = Field(default_factory=list)


class HealthResponse(BaseModel):
    status: str = "ok"
    llmProvider: str
    version: str
