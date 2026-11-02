"""W3-1 离线语料构建测试：切分 / 字段 / 条数 / 幂等 / 加载校验。

全部离线：只构造假的行数据 + 临时 JSONL fixture，**不连接 MySQL**；
真实的 MySQL 抽取只由 ``scripts/build_corpus.py`` 在验收时手动跑一次。
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from app.rag.corpus import (
    CORPUS_PATH_ENV,
    DEFAULT_CORPUS_PATH,
    CorpusFormatError,
    CorpusNotFoundError,
    count_by_source,
    load_chunks,
    resolve_corpus_path,
)
from scripts.build_corpus import (
    FAQ_TEMPLATES,
    MAX_SEGMENT_CHARS,
    build_chunks,
    db_config_from_env,
    make_book_chunk,
    make_book_chunks,
    make_faq_chunks,
    make_rule_chunks,
    rule_values,
    source_stats,
    write_jsonl,
)

CHUNK_KEYS = {"id", "text", "source_type", "book_id", "title", "category", "url", "chunk_no"}


# ----------------------------------------------------------------------
# 测试数据（模拟 MySQL 行，不连库）
# ----------------------------------------------------------------------
@pytest.fixture
def book_rows() -> list[dict]:
    return [
        {
            "id": 262,
            "title": "三体",
            "author": "刘慈欣",
            "category": "文学",
            "publisher": "重庆出版社",
            "description": "科幻小说，讲述地球文明与三体文明的接触。",
            "available_stock": 2,
            "total_stock": 3,
        },
        {
            "id": 400,
            "title": "清朝十二帝",
            "author": "阎崇年",
            "category": "历史",
            "publisher": "中华书局",
            "description": "讲述清朝历史与人物。",
            "available_stock": 0,
            "total_stock": 1,
        },
        {
            "id": 1,
            "title": "Java编程思想",
            "author": "Bruce Eckel",
            "category": "计算机",
            "publisher": "机械工业出版社",
            "description": "系统学习Java核心知识。",
            "available_stock": 5,
            "total_stock": 5,
        },
    ]


@pytest.fixture
def rule_row() -> dict:
    return {
        "id": 1,
        "max_borrow_count": 5,
        "borrow_days": 30,
        "max_renew_count": 1,
        "renew_days": 15,
        "fine_per_day": Decimal("0.20"),
        "fine_max": Decimal("20.00"),
        "reserve_limit": 3,
        "hold_days": 3,
    }


@pytest.fixture
def corpus_file(tmp_path: Path, book_rows: list[dict], rule_row: dict) -> Path:
    """把「假 DB 行」构建成临时 JSONL fixture 并落盘。"""
    chunks = build_chunks(book_rows, rule_row)
    output = tmp_path / "knowledge.jsonl"
    write_jsonl(chunks, output)
    return output


# ----------------------------------------------------------------------
# 1. 切分与条数统计
# ----------------------------------------------------------------------
def test_build_chunks_counts(book_rows: list[dict], rule_row: dict) -> None:
    chunks = build_chunks(book_rows, rule_row)
    assert source_stats(chunks) == {"book": 3, "rule": 5, "faq": 30}
    assert len(chunks) == 38


def test_book_chunk_text_format_and_metadata() -> None:
    chunk = make_book_chunk(
        {
            "id": 262,
            "title": "三体",
            "author": "刘慈欣",
            "category": "文学",
            "publisher": "重庆出版社",
            "description": "科幻小说。",
            "available_stock": 2,
            "total_stock": 3,
        }
    )
    assert set(chunk) == CHUNK_KEYS
    assert chunk["id"] == "book:262"
    assert chunk["source_type"] == "book"
    assert chunk["book_id"] == 262
    assert chunk["title"] == "三体"
    assert chunk["category"] == "文学"
    assert chunk["url"] == "/books?id=262"
    assert chunk["chunk_no"] == 0
    # W3-1 约定的 text 格式
    assert chunk["text"] == (
        "《三体》 作者：刘慈欣 | 分类：文学 | 出版社：重庆出版社 | "
        "库存：可借 2/3 | 简介：科幻小说。"
    )


def test_book_chunk_fills_missing_metadata() -> None:
    chunk = make_book_chunk(
        {"id": 9, "title": "无题", "author": None, "category": None, "publisher": None,
         "description": None, "available_stock": None, "total_stock": None}
    )
    assert "作者：佚名" in chunk["text"]
    assert "分类：未分类" in chunk["text"]
    assert "出版社：未知出版社" in chunk["text"]
    assert "库存：可借 0/0" in chunk["text"]
    assert "简介：暂无简介。" in chunk["text"]


def test_book_chunks_one_chunk_per_book(book_rows: list[dict]) -> None:
    chunks = make_book_chunks(book_rows)
    assert len(chunks) == len(book_rows)
    assert [chunk["id"] for chunk in chunks] == ["book:262", "book:400", "book:1"]


def test_rule_chunks_split_into_five_readable_segments(rule_row: dict) -> None:
    chunks = make_rule_chunks(rule_row)
    assert 3 <= len(chunks) <= 5  # task-5 要求 3-5 段
    assert len(chunks) == 5
    assert len({chunk["id"] for chunk in chunks}) == 5
    assert [chunk["chunk_no"] for chunk in chunks] == [0, 1, 2, 3, 4]
    for chunk in chunks:
        assert chunk["source_type"] == "rule"
        assert chunk["book_id"] is None
        assert chunk["url"] is None
        assert len(chunk["text"]) <= MAX_SEGMENT_CHARS, chunk["id"]
        assert chunk["text"].startswith("借阅规则：") or chunk["id"].endswith((":1", ":2", ":3", ":4"))

    combined = "".join(chunk["text"] for chunk in chunks)
    for keyword in ("借阅", "续借", "逾期", "罚金", "预约", "保留"):
        assert keyword in combined, f"规则切分缺少语义段：{keyword}"


def test_rule_chunks_use_current_db_values() -> None:
    rule = {
        "id": 5,
        "max_borrow_count": 7,
        "borrow_days": 20,
        "max_renew_count": 2,
        "renew_days": 10,
        "fine_per_day": Decimal("0.50"),
        "fine_max": Decimal("10.00"),
        "reserve_limit": 4,
        "hold_days": 5,
    }
    chunks = make_rule_chunks(rule)
    texts = "".join(chunk["text"] for chunk in chunks)
    assert "7 本图书" in texts
    assert "20 天" in texts
    assert "0.50 元" in texts
    assert "10.00 元" in texts
    assert "保留 5 天" in texts
    assert chunks[0]["id"] == "rule:5:0"


def test_rule_values_money_and_cap_derivation() -> None:
    values = rule_values(None)  # 规则行缺失时用默认值
    assert values["max_borrow_count"] == 5
    assert values["borrow_days"] == 30
    assert values["fine_per_day"] == "0.20"
    assert values["fine_max"] == "20.00"
    assert values["fine_example_days"] == 10
    assert values["fine_example_amount"] == "2.00"
    assert values["fine_cap_days"] == 100  # 20.00 / 0.20 天后封顶
    assert values["fine_cap_amount"] == "20.00"


def test_faq_has_exactly_30_entries() -> None:
    assert len(FAQ_TEMPLATES) == 30
    assert len(make_faq_chunks(None)) == 30


def test_faq_covers_required_topics() -> None:
    texts = [chunk["text"] for chunk in make_faq_chunks(None)]
    combined = "".join(texts)
    required_keywords = [
        "借书", "借几本", "借期", "续借", "逾期", "罚金", "封顶", "预约", "保留", "顺延",
        "密码", "管理员", "分类", "ISBN", "代借", "遗失", "上限", "借阅记录", "跨馆",
        "荐购", "开放时间", "批量导入", "账号", "超期", "取消",
    ]
    missing = [keyword for keyword in required_keywords if keyword not in combined]
    assert not missing, f"FAQ 缺少主题：{missing}"


def test_faq_chunk_fields(rule_row: dict) -> None:
    chunks = make_faq_chunks(rule_row)
    assert [chunk["id"] for chunk in chunks[:2]] == ["faq:0", "faq:1"]
    for chunk in chunks:
        assert chunk["source_type"] == "faq"
        assert chunk["book_id"] is None
        assert chunk["url"] is None
        assert chunk["chunk_no"] == 0
        assert chunk["text"].startswith("问：")
        assert "\n答：" in chunk["text"]
        assert len(chunk["text"]) <= MAX_SEGMENT_CHARS


def test_chunk_ids_are_unique(book_rows: list[dict], rule_row: dict) -> None:
    chunks = build_chunks(book_rows, rule_row)
    assert len({chunk["id"] for chunk in chunks}) == len(chunks)


# ----------------------------------------------------------------------
# 2. JSONL 写出（幂等覆盖）
# ----------------------------------------------------------------------
def test_write_jsonl_schema_and_roundtrip(
    tmp_path: Path, book_rows: list[dict], rule_row: dict
) -> None:
    output = tmp_path / "nested" / "knowledge.jsonl"
    result = write_jsonl(build_chunks(book_rows, rule_row), output)

    assert output.exists()
    assert result["line_count"] == 38
    lines = [line for line in output.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(lines) == 38
    for line in lines:
        raw = json.loads(line)
        assert set(raw) == CHUNK_KEYS

    loaded = load_chunks(output)
    assert count_by_source(loaded) == {"book": 3, "rule": 5, "faq": 30}
    assert loaded[0]["text"] == make_book_chunks(book_rows)[0]["text"]


def test_write_jsonl_is_idempotent(
    tmp_path: Path, book_rows: list[dict], rule_row: dict
) -> None:
    output = tmp_path / "knowledge.jsonl"
    chunks = build_chunks(book_rows, rule_row)

    first = write_jsonl(chunks, output)
    first_bytes = output.read_bytes()
    second = write_jsonl(chunks, output)  # 重复执行：覆盖而非追加
    second_bytes = output.read_bytes()

    assert first["line_count"] == second["line_count"] == 38
    assert first_bytes == second_bytes
    # 原子写入的临时文件不能残留
    assert not list(tmp_path.glob(".knowledge-*"))


# ----------------------------------------------------------------------
# 3. 语料加载（缺失 / 格式错误 / 空行）
# ----------------------------------------------------------------------
def test_load_chunks_missing_file_raises_clear_chinese_error(tmp_path: Path) -> None:
    with pytest.raises(CorpusNotFoundError) as exc_info:
        load_chunks(tmp_path / "not-built-yet.jsonl")
    message = str(exc_info.value)
    assert "知识语料文件不存在" in message
    assert "build_corpus.py" in message  # 提示修复命令


def test_load_chunks_invalid_json_reports_line_number(tmp_path: Path) -> None:
    bad = tmp_path / "broken.jsonl"
    good_line = json.dumps({"id": "book:1", "text": "《X》", "source_type": "book"}, ensure_ascii=False)
    bad.write_text(good_line + "\n{not-json}\n", encoding="utf-8")
    with pytest.raises(CorpusFormatError) as exc_info:
        load_chunks(bad)
    assert "第 2 行" in str(exc_info.value)
    assert "不是合法 JSON" in str(exc_info.value)


def test_load_chunks_rejects_unknown_source_type(tmp_path: Path) -> None:
    bad = tmp_path / "bad-type.jsonl"
    bad.write_text(
        json.dumps({"id": "video:1", "text": "x", "source_type": "video"}) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(CorpusFormatError) as exc_info:
        load_chunks(bad)
    assert "source_type" in str(exc_info.value)


def test_load_chunks_rejects_duplicate_ids(tmp_path: Path) -> None:
    dup = tmp_path / "dup.jsonl"
    line = json.dumps({"id": "book:1", "text": "《X》", "source_type": "book"})
    dup.write_text(line + "\n" + line + "\n", encoding="utf-8")
    with pytest.raises(CorpusFormatError) as exc_info:
        load_chunks(dup)
    assert "id 重复" in str(exc_info.value)


def test_load_chunks_skips_blank_lines(tmp_path: Path) -> None:
    path = tmp_path / "with-blanks.jsonl"
    line = json.dumps({"id": "book:1", "text": "《X》", "source_type": "book"}, ensure_ascii=False)
    path.write_text("\n\n" + line + "\n\n", encoding="utf-8")
    chunks = load_chunks(path)
    assert len(chunks) == 1
    assert chunks[0]["book_id"] is None  # 缺省字段补 null


def test_resolve_corpus_path_env_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "custom.jsonl"
    monkeypatch.setenv(CORPUS_PATH_ENV, str(target))
    assert resolve_corpus_path() == target
    # 显式参数优先级最高
    assert resolve_corpus_path(tmp_path / "explicit.jsonl") == tmp_path / "explicit.jsonl"

    monkeypatch.delenv(CORPUS_PATH_ENV)
    assert resolve_corpus_path() == DEFAULT_CORPUS_PATH


# ----------------------------------------------------------------------
# 4. 数据库配置解析（纯函数，不连接）
# ----------------------------------------------------------------------
def test_db_config_defaults_to_isolated_mysql() -> None:
    config = db_config_from_env({})
    assert config == {
        "host": "127.0.0.1",
        "port": 3308,
        "user": "root",
        "password": "",
        "database": "library_db",
    }


def test_db_config_reads_db_url() -> None:
    config = db_config_from_env(
        {"DB_URL": "mysql://reader:p%40ss@db.local:3307/library?charset=utf8mb4"}
    )
    assert config["host"] == "db.local"
    assert config["port"] == 3307
    assert config["user"] == "reader"
    assert config["password"] == "p@ss"
    assert config["database"] == "library"


def test_db_config_rejects_bad_port() -> None:
    with pytest.raises(ValueError) as exc_info:
        db_config_from_env({"DB_PORT": "not-a-number"})
    assert "DB_PORT" in str(exc_info.value)
