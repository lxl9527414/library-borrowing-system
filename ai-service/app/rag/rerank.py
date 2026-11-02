"""W3-4 重排（Reranker）：通义 ``gte-rerank-v2`` 优先 + 全程优雅降级。

## 为什么用通义原生重排接口，而不是让 LLM 输出 JSON 排序

- **稳定**：``gte-rerank-v2`` 是专用 cross-encoder 重排模型，请求/响应都是结构化
  JSON（``output.results[].index/relevance_score``），不存在「模型不按格式输出、
  需要解析 Markdown/自然语言」的问题；
- **便宜且快**：按输入 token 计费，一次 20 个候选（每块截断 512 字）约 1 万 token 以内，
  价格远低于让 chat 模型做 listwise 排序；典型耗时几百毫秒；
- **可控**：``top_n`` 由我们传，接口只返回排序结果与分数，不会「编」出候选。

DeepSeek listwise（:class:`LLMReranker`）作为可选项保留：只有显式配置
``RAG_RERANK_PROVIDER=deepseek`` 且存在 ``DEEPSEEK_API_KEY`` 时才会使用，
解析失败同样回退原顺序。

## 契约（service.py 按此调用，见 task-7）

- :func:`get_reranker` 返回对象，含 ``name`` 属性与
  ``rerank(query, candidates, top_k) -> list[dict]``；
- 返回**重排后的候选子集**（长度 ≤ ``top_k``），每项保留候选原有全部字段
  （实现上返回 ``dict(candidate)`` 浅拷贝，不修改输入）；
- **任何失败都不抛异常**：无 Key / 超时（默认 20s）/ 非 200 / 响应解析异常 →
  返回原顺序前 ``top_k`` 条并打 WARNING 日志；
- 最近一次调用的统计记录在实例的 ``last_stats`` 字典里（供评测 / 运维读取）：

  ``provider`` / ``model`` / ``candidates``（输入候选数）/ ``requested``（top_n）/
  ``returned``（返回条数）/ ``elapsed_ms``（HTTP + 解析耗时，毫秒，保留 1 位小数）/
  ``ok`` / ``fallback`` / ``error`` / ``scores``（返回项的相关度分数）。

  便捷属性：``last_elapsed_ms`` / ``last_candidate_count``。

## 配置（与 ``app/rag/rag_config.py`` 的读取优先级一致：进程环境变量 > .env）

- ``RAG_RERANK_ENABLED``：默认 ``true``；false 时返回 :class:`NoopReranker`；
- ``RAG_RERANK_PROVIDER``：``dashscope``（默认）| ``deepseek`` | ``none``；
  兼容旧值 ``llm``（等价 deepseek）、``qwen``（等价 dashscope）；
- ``DASHSCOPE_API_KEY`` / ``DEEPSEEK_API_KEY``：缺失时降级 Noop + WARNING，**绝不打印 Key**；
- ``RAG_RERANK_MAX_CHARS``：候选文本截断长度，默认 512（仅对单块 ``text`` 生效）；
- ``RAG_RERANK_TIMEOUT_SECONDS``：HTTP 超时，默认 20.0s。

## 与 ``rag_config.RagConfig`` 的关系

``RagConfig.rerank_provider`` 的历史默认值是 ``"llm"`` 且合法值只有 llm/none，
而 W3-4 的默认实现是通义原生重排。本模块因此**直接复用 rag_config 的
``rag_env`` / ``rag_flag`` 读取函数**（保证环境变量/.env 优先级与全项目一致），
但 provider 的默认值与别名映射由本模块维护，避免耦合另一个并行任务对
``RagConfig`` 默认值的调整。
"""

from __future__ import annotations

import json
import math
import time
from abc import ABC, abstractmethod
from typing import Any, Sequence

import httpx

from app.obs.logging import get_logger
from app.rag.rag_config import rag_env, rag_flag, rag_float, rag_int

logger = get_logger("rag.rerank")

# ----------------------------------------------------------------------
# 常量 / 配置项名称
# ----------------------------------------------------------------------
DASHSCOPE_RERANK_URL = (
    "https://dashscope.aliyuncs.com/api/v1/services/rerank/text-rerank/text-rerank"
)
DASHSCOPE_RERANK_MODEL = "gte-rerank-v2"
DEFAULT_DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"
DEFAULT_DEEPSEEK_MODEL = "deepseek-chat"

DEFAULT_PROVIDER = "dashscope"
DEFAULT_MAX_CHARS = 512
DEFAULT_TIMEOUT_SECONDS = 20.0

RERANK_ENABLED_ENV = "RAG_RERANK_ENABLED"
RERANK_PROVIDER_ENV = "RAG_RERANK_PROVIDER"
RERANK_MAX_CHARS_ENV = "RAG_RERANK_MAX_CHARS"
RERANK_TIMEOUT_ENV = "RAG_RERANK_TIMEOUT_SECONDS"
DASHSCOPE_API_KEY_ENV = "DASHSCOPE_API_KEY"
DEEPSEEK_API_KEY_ENV = "DEEPSEEK_API_KEY"

# 对外声明的合法 provider（none 表示直通）
RERANK_PROVIDERS: tuple[str, ...] = ("dashscope", "deepseek", "none")

# 兼容旧配置 / 常见别名；键统一小写
_PROVIDER_ALIASES: dict[str, str] = {
    "dashscope": "dashscope",
    "qwen": "dashscope",
    "tongyi": "dashscope",
    "gte": "dashscope",
    "deepseek": "deepseek",
    "llm": "deepseek",
    "none": "none",
    "off": "none",
    "noop": "none",
}


# ----------------------------------------------------------------------
# 工具函数
# ----------------------------------------------------------------------
def _elapsed_ms(started: float | None) -> float:
    """从 ``time.perf_counter()`` 起点算到现在的毫秒数。"""
    if started is None:
        return 0.0
    return (time.perf_counter() - started) * 1000.0


def _truncate(text: Any, max_chars: int) -> str:
    """截断候选文本（重排接口不需要全文）；空文本用占位符避免接口 400。"""
    value = str(text or "").strip()
    if not value:
        return "（无文本）"
    limit = max(1, int(max_chars))
    return value[:limit]


def _candidate_document(candidate: dict[str, Any], max_chars: int) -> str:
    """取一条候选用于重排的文本：优先 text，其次 title，最后占位符。"""
    text = str(candidate.get("text") or "").strip()
    if not text:
        text = str(candidate.get("title") or "").strip()
    return _truncate(text, max_chars)


# ----------------------------------------------------------------------
# 抽象基类
# ----------------------------------------------------------------------
class Reranker(ABC):
    """重排器抽象：``name`` + ``rerank(query, candidates, top_k)``。

    子类实现只需保证「失败回退、不抛异常」，统计与日志由基类辅助方法统一记录。
    """

    name: str = "base"

    def __init__(self) -> None:
        self.last_stats: dict[str, Any] = {
            "provider": self.name,
            "model": None,
            "candidates": 0,
            "requested": 0,
            "returned": 0,
            "elapsed_ms": 0.0,
            "ok": False,
            "fallback": False,
            "error": None,
            "scores": [],
        }

    # ------------------------------------------------------------------
    # 子类实现
    # ------------------------------------------------------------------
    @abstractmethod
    def rerank(
        self,
        query: str,
        candidates: Sequence[dict[str, Any]],
        top_k: int,
    ) -> list[dict[str, Any]]:
        """返回重排后的候选子集（长度 ≤ top_k，保留原有字段，失败回退原顺序）。"""

    # ------------------------------------------------------------------
    # 统计 / 日志
    # ------------------------------------------------------------------
    @property
    def last_elapsed_ms(self) -> float:
        """最近一次重排调用的耗时（毫秒）。"""
        return float(self.last_stats.get("elapsed_ms") or 0.0)

    @property
    def last_candidate_count(self) -> int:
        """最近一次重排调用收到的候选数。"""
        return int(self.last_stats.get("candidates") or 0)

    def _record(
        self,
        *,
        candidates: int = 0,
        requested: int = 0,
        returned: int = 0,
        elapsed_ms: float = 0.0,
        ok: bool = True,
        fallback: bool = False,
        error: str | None = None,
        scores: Sequence[float] | None = None,
    ) -> None:
        """记录最近一次调用统计（供评测 / 运维读取；字段名见模块 docstring）。"""
        self.last_stats = {
            "provider": self.name,
            "model": getattr(self, "model", None),
            "candidates": int(candidates),
            "requested": int(requested),
            "returned": int(returned),
            "elapsed_ms": round(float(elapsed_ms), 1),
            "ok": bool(ok),
            "fallback": bool(fallback),
            "error": str(error) if error else None,
            "scores": [round(float(score), 6) for score in (scores or [])],
        }

    def _normalize(
        self,
        query: str,
        candidates: Sequence[dict[str, Any]] | None,
        top_k: int,
    ) -> tuple[str, list[dict[str, Any]], int]:
        """参数归一化：非法 top_k → 0，非法 candidates → 空列表。"""
        query_text = str(query or "").strip()
        try:
            limit = int(top_k)
        except (TypeError, ValueError):
            limit = 0
        try:
            candidate_list = list(candidates or [])
        except TypeError:
            candidate_list = []
        return query_text, candidate_list, max(limit, 0)

    def _fallback(
        self,
        candidates: list[dict[str, Any]],
        limit: int,
        *,
        reason: str,
        started: float | None = None,
        requested: int = 0,
        code: str = "RAG_RERANK_FALLBACK",
    ) -> list[dict[str, Any]]:
        """失败降级：返回原顺序前 ``limit`` 条，记录统计 + WARNING，不抛异常。"""
        result = list(candidates[:limit]) if limit > 0 else []
        self._record(
            candidates=len(candidates),
            requested=requested,
            returned=len(result),
            elapsed_ms=_elapsed_ms(started),
            ok=False,
            fallback=True,
            error=reason,
        )
        logger.warning(
            f"重排失败，回退原顺序前 {len(result)} 条（{self.name}）：{reason}",
            extra={"code": code},
        )
        return result

    def _truncate_message(self, exc: Exception) -> str:
        """异常信息转成短原因，避免日志过长（也不打印任何 Key）。"""
        message = f"{type(exc).__name__}: {exc}"
        return message[:300]


# ----------------------------------------------------------------------
# Noop：直通（未启用 / 无 Key / provider=none 时使用）
# ----------------------------------------------------------------------
class NoopReranker(Reranker):
    """空重排器：保持原顺序，只做 top_k 截断。"""

    name = "none"

    def __init__(self, reason: str = "disabled") -> None:
        super().__init__()
        self.reason = reason

    def rerank(
        self,
        query: str,
        candidates: Sequence[dict[str, Any]],
        top_k: int,
    ) -> list[dict[str, Any]]:
        _, candidate_list, limit = self._normalize(query, candidates, top_k)
        started = time.perf_counter()
        result = list(candidate_list[:limit]) if limit > 0 else []
        self._record(
            candidates=len(candidate_list),
            requested=limit,
            returned=len(result),
            elapsed_ms=_elapsed_ms(started),
            ok=True,
            fallback=False,
        )
        return result


# ----------------------------------------------------------------------
# 通义 gte-rerank-v2
# ----------------------------------------------------------------------
class DashScopeReranker(Reranker):
    """通义原生重排（``gte-rerank-v2``）：cross-encoder 打分，稳定且便宜。"""

    name = "dashscope-gte-rerank-v2"

    def __init__(
        self,
        api_key: str | None = None,
        *,
        model: str = DASHSCOPE_RERANK_MODEL,
        url: str = DASHSCOPE_RERANK_URL,
        max_chars: int = DEFAULT_MAX_CHARS,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        client: httpx.Client | None = None,
    ) -> None:
        super().__init__()
        # 只在内存里持有 Key；日志/统计里绝不出现
        self.api_key = str(api_key or "").strip()
        self.model = model
        self.url = url
        self.max_chars = max(1, int(max_chars))
        self.timeout_seconds = float(timeout_seconds)
        self._client = client

    # ------------------------------------------------------------------
    # HTTP
    # ------------------------------------------------------------------
    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _post(self, payload: dict[str, Any]) -> httpx.Response:
        """发请求：测试可注入 ``client``（MockTransport），生产按次建短连接。"""
        if self._client is not None:
            return self._client.post(self.url, json=payload, headers=self._headers())
        with httpx.Client(timeout=self.timeout_seconds) as client:
            return client.post(self.url, json=payload, headers=self._headers())

    # ------------------------------------------------------------------
    # 解析
    # ------------------------------------------------------------------
    @staticmethod
    def _parse_results(data: Any, candidate_count: int) -> list[tuple[float, int]]:
        """解析 ``{"output":{"results":[{"index":int,"relevance_score":float}]}}``。

        任何结构不合法都抛 ``ValueError``（由调用方统一降级），保证调用方不抛。
        """
        output = data.get("output") if isinstance(data, dict) else None
        results = output.get("results") if isinstance(output, dict) else None
        if not isinstance(results, list) or not results:
            raise ValueError("响应缺少 output.results 或为空")

        parsed: list[tuple[float, int]] = []
        seen: set[int] = set()
        for item in results:
            if not isinstance(item, dict):
                raise ValueError("output.results 元素不是对象")
            raw_index = item.get("index")
            raw_score = item.get("relevance_score")
            if raw_index is None or raw_score is None:
                raise ValueError("output.results 元素缺少 index/relevance_score")
            index = int(raw_index)
            score = float(raw_score)
            if not math.isfinite(score):
                raise ValueError("relevance_score 不是有限数值")
            if index < 0 or index >= candidate_count:
                raise ValueError(f"index 越界：{index}")
            if index in seen:
                continue
            seen.add(index)
            parsed.append((score, index))

        if not parsed:
            raise ValueError("output.results 中没有可用结果")
        # 分数降序；同分按原始下标升序，保证结果可复现
        parsed.sort(key=lambda pair: (-pair[0], pair[1]))
        return parsed

    # ------------------------------------------------------------------
    # 对外接口
    # ------------------------------------------------------------------
    def rerank(
        self,
        query: str,
        candidates: Sequence[dict[str, Any]],
        top_k: int,
    ) -> list[dict[str, Any]]:
        query_text, candidate_list, limit = self._normalize(query, candidates, top_k)
        if limit <= 0 or not candidate_list:
            self._record(candidates=len(candidate_list), returned=0, ok=True)
            return []
        if not query_text:
            return self._fallback(
                candidate_list,
                limit,
                reason="query 为空，跳过重排",
                code="RAG_RERANK_EMPTY_QUERY",
            )
        if not self.api_key:
            return self._fallback(
                candidate_list,
                limit,
                reason="未配置 DASHSCOPE_API_KEY",
                code="RAG_RERANK_NO_KEY",
            )

        top_n = min(limit, len(candidate_list))
        payload = {
            "model": self.model,
            "input": {
                "query": query_text,
                "documents": [
                    _candidate_document(candidate, self.max_chars)
                    for candidate in candidate_list
                ],
            },
            "parameters": {"return_documents": False, "top_n": top_n},
        }
        started = time.perf_counter()
        try:
            response = self._post(payload)
            if response.status_code != 200:
                return self._fallback(
                    candidate_list,
                    limit,
                    reason=f"HTTP {response.status_code}",
                    started=started,
                    requested=top_n,
                    code="RAG_RERANK_HTTP_ERROR",
                )
            data = response.json()
            ranked = self._parse_results(data, len(candidate_list))
            selected = ranked[:limit]
            result = [dict(candidate_list[index]) for _, index in selected]
        except Exception as exc:  # noqa: BLE001 - 重排是增强能力，任何异常都降级
            return self._fallback(
                candidate_list,
                limit,
                reason=self._truncate_message(exc),
                started=started,
                requested=top_n,
                code="RAG_RERANK_FAILED",
            )

        elapsed = _elapsed_ms(started)
        self._record(
            candidates=len(candidate_list),
            requested=top_n,
            returned=len(result),
            elapsed_ms=elapsed,
            ok=True,
            fallback=False,
            scores=[score for score, _ in selected],
        )
        logger.info(
            f"通义重排完成：candidates={len(candidate_list)} top_n={top_n} "
            f"returned={len(result)} elapsed_ms={elapsed:.1f}",
            extra={"code": "RAG_RERANK_DONE", "provider": self.name},
        )
        return result


# ----------------------------------------------------------------------
# DeepSeek listwise（可选实现：显式配置才启用）
# ----------------------------------------------------------------------
class LLMReranker(Reranker):
    """DeepSeek listwise 重排：让模型只输出 ``{"ranking":[...]}``。

    默认不启用（默认 provider 是 dashscope）。相比通义原生重排，它多了一次
    chat 生成、需要解析模型输出、成本和延迟都更高，因此仅作为可选项保留。
    """

    name = "deepseek-listwise"

    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str = DEFAULT_DEEPSEEK_BASE_URL,
        model: str = DEFAULT_DEEPSEEK_MODEL,
        max_chars: int = DEFAULT_MAX_CHARS,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        client: httpx.Client | None = None,
    ) -> None:
        super().__init__()
        self.api_key = str(api_key or "").strip()
        self.base_url = str(base_url or DEFAULT_DEEPSEEK_BASE_URL).rstrip("/")
        self.model = model
        self.max_chars = max(1, int(max_chars))
        self.timeout_seconds = float(timeout_seconds)
        self._client = client

    @property
    def endpoint(self) -> str:
        return f"{self.base_url}/chat/completions"

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _post(self, payload: dict[str, Any]) -> httpx.Response:
        if self._client is not None:
            return self._client.post(self.endpoint, json=payload, headers=self._headers())
        with httpx.Client(timeout=self.timeout_seconds) as client:
            return client.post(self.endpoint, json=payload, headers=self._headers())

    def _build_messages(
        self, query: str, candidates: list[dict[str, Any]]
    ) -> list[dict[str, str]]:
        lines = []
        for index, candidate in enumerate(candidates):
            text = _candidate_document(candidate, self.max_chars).replace("\n", " ").strip()
            lines.append(f"[{index}] {text}")
        system = (
            "你是图书馆检索结果重排助手。给定用户问题和候选文档，按与问题的相关程度从高到低排序。"
            '只输出 JSON，格式严格为 {"ranking": [下标, 下标, ...]}，'
            "ranking 中的下标从 0 开始、必须覆盖全部候选、不得重复、不得包含解释或 Markdown。"
        )
        user = (
            f"用户问题：{query}\n\n候选文档：\n" + "\n".join(lines) + "\n\n只输出 JSON。"
        )
        return [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]

    @staticmethod
    def _parse_ranking(content: Any, candidate_count: int) -> list[int]:
        """从模型输出里提取 ranking；结构不合法抛 ``ValueError``。"""
        text = str(content or "").strip()
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("模型输出中没有 JSON 对象")
        data = json.loads(text[start : end + 1])
        if not isinstance(data, dict):
            raise ValueError("模型输出的 JSON 不是对象")
        ranking = data.get("ranking")
        if not isinstance(ranking, list) or not ranking:
            raise ValueError("模型输出缺少非空 ranking 列表")

        order: list[int] = []
        seen: set[int] = set()
        for raw_index in ranking:
            index = int(raw_index)  # 非整数（含 "a"）直接抛 ValueError
            if index < 0 or index >= candidate_count:
                raise ValueError(f"ranking 下标越界：{index}")
            if index in seen:
                raise ValueError(f"ranking 下标重复：{index}")
            seen.add(index)
            order.append(index)
        return order

    def rerank(
        self,
        query: str,
        candidates: Sequence[dict[str, Any]],
        top_k: int,
    ) -> list[dict[str, Any]]:
        query_text, candidate_list, limit = self._normalize(query, candidates, top_k)
        if limit <= 0 or not candidate_list:
            self._record(candidates=len(candidate_list), returned=0, ok=True)
            return []
        if not query_text:
            return self._fallback(
                candidate_list,
                limit,
                reason="query 为空，跳过重排",
                code="RAG_RERANK_EMPTY_QUERY",
            )
        if not self.api_key:
            return self._fallback(
                candidate_list,
                limit,
                reason="未配置 DEEPSEEK_API_KEY",
                code="RAG_RERANK_NO_KEY",
            )

        payload = {
            "model": self.model,
            "messages": self._build_messages(query_text, candidate_list),
            "temperature": 0,
            "max_tokens": 256,
            "stream": False,
        }
        started = time.perf_counter()
        try:
            response = self._post(payload)
            if response.status_code != 200:
                return self._fallback(
                    candidate_list,
                    limit,
                    reason=f"HTTP {response.status_code}",
                    started=started,
                    code="RAG_RERANK_HTTP_ERROR",
                )
            data = response.json()
            content = data["choices"][0]["message"]["content"]
            order = self._parse_ranking(content, len(candidate_list))
            # 模型没排到的候选按原顺序补在末尾，保证输出稳定且覆盖完整
            order.extend(index for index in range(len(candidate_list)) if index not in set(order))
            result = [dict(candidate_list[index]) for index in order[:limit]]
        except Exception as exc:  # noqa: BLE001 - 重排是增强能力，任何异常都降级
            return self._fallback(
                candidate_list,
                limit,
                reason=self._truncate_message(exc),
                started=started,
                code="RAG_RERANK_FAILED",
            )

        elapsed = _elapsed_ms(started)
        self._record(
            candidates=len(candidate_list),
            requested=limit,
            returned=len(result),
            elapsed_ms=elapsed,
            ok=True,
            scores=[],
        )
        logger.info(
            f"DeepSeek listwise 重排完成：candidates={len(candidate_list)} "
            f"returned={len(result)} elapsed_ms={elapsed:.1f}",
            extra={"code": "RAG_RERANK_DONE", "provider": self.name},
        )
        return result


# ----------------------------------------------------------------------
# 工厂
# ----------------------------------------------------------------------
def _warn_noop(message: str, code: str) -> NoopReranker:
    logger.warning(message, extra={"code": code})
    return NoopReranker(reason=message)


def get_reranker() -> Reranker:
    """按配置构造重排器（每次调用重新读配置，便于测试/脚本切换环境变量）。

    配置读取顺序与 ``app/rag/rag_config.py`` 一致（进程环境变量 > .env > 默认值）：

    - ``RAG_RERANK_ENABLED=false`` → :class:`NoopReranker`（WARNING）；
    - ``RAG_RERANK_PROVIDER=dashscope``（默认）→ :class:`DashScopeReranker`；
    - ``RAG_RERANK_PROVIDER=deepseek`` → :class:`LLMReranker`；
    - ``none`` → :class:`NoopReranker`；
    - 未知 provider / 对应 Key 缺失 → :class:`NoopReranker` + WARNING（绝不让检索层崩）。
    """
    if not rag_flag(RERANK_ENABLED_ENV, True):
        return _warn_noop(
            "重排已关闭（RAG_RERANK_ENABLED=false），使用 NoopReranker 直通",
            "RAG_RERANK_DISABLED",
        )

    raw_provider = (
        rag_env(RERANK_PROVIDER_ENV, DEFAULT_PROVIDER) or DEFAULT_PROVIDER
    ).strip().lower()
    provider = _PROVIDER_ALIASES.get(raw_provider)
    if provider is None:
        return _warn_noop(
            f"未知重排 provider：{raw_provider!r}（可选 dashscope/deepseek/none），"
            "降级为 NoopReranker 直通",
            "RAG_RERANK_PROVIDER_UNKNOWN",
        )
    if provider == "none":
        logger.info(
            "RAG_RERANK_PROVIDER=none，使用 NoopReranker 直通",
            extra={"code": "RAG_RERANK_DISABLED"},
        )
        return NoopReranker(reason="provider-none")

    max_chars = rag_int(RERANK_MAX_CHARS_ENV, DEFAULT_MAX_CHARS)
    timeout_seconds = rag_float(
        RERANK_TIMEOUT_ENV, DEFAULT_TIMEOUT_SECONDS, minimum=1.0
    )

    if provider == "dashscope":
        api_key = rag_env(DASHSCOPE_API_KEY_ENV)
        if not api_key:
            return _warn_noop(
                "未配置 DASHSCOPE_API_KEY，重排降级为 NoopReranker",
                "RAG_RERANK_NO_KEY",
            )
        return DashScopeReranker(
            api_key=api_key,
            max_chars=max_chars,
            timeout_seconds=timeout_seconds,
        )

    # provider == "deepseek"
    api_key = rag_env(DEEPSEEK_API_KEY_ENV)
    if not api_key:
        return _warn_noop(
            "未配置 DEEPSEEK_API_KEY，重排降级为 NoopReranker",
            "RAG_RERANK_NO_KEY",
        )
    base_url = (
        rag_env("DEEPSEEK_BASE_URL", DEFAULT_DEEPSEEK_BASE_URL) or DEFAULT_DEEPSEEK_BASE_URL
    )
    model = rag_env("DEEPSEEK_MODEL", DEFAULT_DEEPSEEK_MODEL) or DEFAULT_DEEPSEEK_MODEL
    return LLMReranker(
        api_key=api_key,
        base_url=base_url,
        model=model,
        max_chars=max_chars,
        timeout_seconds=timeout_seconds,
    )


__all__ = [
    "DASHSCOPE_RERANK_MODEL",
    "DASHSCOPE_RERANK_URL",
    "DEFAULT_MAX_CHARS",
    "DEFAULT_PROVIDER",
    "DEFAULT_TIMEOUT_SECONDS",
    "DashScopeReranker",
    "LLMReranker",
    "NoopReranker",
    "RERANK_PROVIDERS",
    "Reranker",
    "get_reranker",
]
