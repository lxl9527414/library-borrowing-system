"""知识语料加载：把 ``data/corpus/knowledge.jsonl`` 读成内存中的 chunk 列表。

语料由离线脚本 ``ai-service/scripts/build_corpus.py`` 生成（该脚本允许直连 MySQL，
因为它是**构建期工具**）；AI 服务**运行时**不直连数据库，只读这份 JSONL 文件，
符合《AI 架构与接口契约》§1「AI 服务不直连数据库」的约束。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from app.obs.logging import get_logger

logger = get_logger("rag.corpus")

# ai-service/app/rag/corpus.py -> ai-service/
SERVICE_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CORPUS_PATH = SERVICE_ROOT / "data" / "corpus" / "knowledge.jsonl"

# 允许用环境变量覆盖语料路径（测试 / 多语料切换用）
CORPUS_PATH_ENV = "AI_RAG_CORPUS_PATH"

SOURCE_TYPES: tuple[str, ...] = ("book", "rule", "faq")


class CorpusNotFoundError(FileNotFoundError):
    """语料文件不存在（通常是还没跑 build_corpus.py）。"""


class CorpusFormatError(ValueError):
    """语料文件内容不符合 schema。"""


def resolve_corpus_path(path: str | Path | None = None) -> Path:
    """语料路径解析优先级：显式参数 > 环境变量 AI_RAG_CORPUS_PATH > 默认路径。"""
    if path is not None:
        return Path(path).expanduser()
    env_path = os.environ.get(CORPUS_PATH_ENV, "").strip()
    if env_path:
        return Path(env_path).expanduser()
    return DEFAULT_CORPUS_PATH


def _normalize_chunk(raw: dict[str, Any], line_no: int) -> dict[str, Any]:
    """校验并补齐元数据字段；字段严格对齐 W3-1 的 JSONL schema。"""
    missing = [key for key in ("id", "text", "source_type") if not raw.get(key)]
    if missing:
        raise CorpusFormatError(
            f"语料文件第 {line_no} 行缺少必填字段：{', '.join(missing)}（需要 id/text/source_type）"
        )

    source_type = str(raw["source_type"]).strip()
    if source_type not in SOURCE_TYPES:
        raise CorpusFormatError(
            f"语料文件第 {line_no} 行 source_type={source_type!r} 非法，"
            f"只能是 {'/'.join(SOURCE_TYPES)}"
        )

    text = str(raw["text"]).strip()
    if not text:
        raise CorpusFormatError(f"语料文件第 {line_no} 行 text 为空")

    book_id = raw.get("book_id")
    if book_id is not None:
        try:
            book_id = int(book_id)
        except (TypeError, ValueError) as exc:
            raise CorpusFormatError(
                f"语料文件第 {line_no} 行 book_id 必须是整数或 null，收到：{book_id!r}"
            ) from exc

    try:
        chunk_no = int(raw.get("chunk_no") or 0)
    except (TypeError, ValueError) as exc:
        raise CorpusFormatError(
            f"语料文件第 {line_no} 行 chunk_no 必须是整数，收到：{raw.get('chunk_no')!r}"
        ) from exc

    return {
        "id": str(raw["id"]).strip(),
        "text": text,
        "source_type": source_type,
        "book_id": book_id,
        "title": str(raw.get("title") or "").strip(),
        "category": str(raw.get("category") or "").strip(),
        "url": raw.get("url") or None,
        "chunk_no": chunk_no,
    }


def load_chunks(path: str | Path | None = None) -> list[dict[str, Any]]:
    """加载并校验 JSONL 语料。

    - 文件不存在：抛 :class:`CorpusNotFoundError`（中文提示里带修复命令）；
    - 某行不是合法 JSON / 字段不合 schema：抛 :class:`CorpusFormatError` 并给出行号；
    - 空行会被跳过（保证手改文件后仍可加载）。

    调用方（RagService）负责把异常降级为空检索，不向 HTTP 层抛。
    """
    corpus_path = resolve_corpus_path(path)
    if not corpus_path.exists():
        raise CorpusNotFoundError(
            f"知识语料文件不存在：{corpus_path}\n"
            "请先运行离线构建脚本生成语料：cd ai-service && .venv/bin/python scripts/build_corpus.py"
        )
    if not corpus_path.is_file():
        raise CorpusNotFoundError(f"语料路径不是普通文件：{corpus_path}")

    chunks: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    with corpus_path.open("r", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError as exc:
                raise CorpusFormatError(
                    f"语料文件第 {line_no} 行不是合法 JSON：{exc.msg}（列 {exc.colno}）"
                ) from exc
            if not isinstance(raw, dict):
                raise CorpusFormatError(f"语料文件第 {line_no} 行应为 JSON 对象")
            chunk = _normalize_chunk(raw, line_no)
            if chunk["id"] in seen_ids:
                raise CorpusFormatError(f"语料文件第 {line_no} 行 id 重复：{chunk['id']}")
            seen_ids.add(chunk["id"])
            chunks.append(chunk)

    stats = count_by_source(chunks)
    detail = ", ".join(f"{source_type}={count}" for source_type, count in stats.items())
    logger.info(
        f"语料加载完成：{len(chunks)} 块（{detail}）",
        extra={"code": "RAG_CORPUS_LOADED"},
    )
    return chunks


def count_by_source(chunks: list[dict[str, Any]]) -> dict[str, int]:
    """按 source_type 统计块数（保持 book/rule/faq 顺序，便于日志与验收对账）。"""
    result = {source_type: 0 for source_type in SOURCE_TYPES}
    for chunk in chunks:
        source_type = str(chunk.get("source_type") or "")
        result[source_type] = result.get(source_type, 0) + 1
    return result
