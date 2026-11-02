#!/usr/bin/env python3
"""离线知识语料构建（W3-1）：MySQL → ``data/corpus/knowledge.jsonl``。

【重要：构建期 vs 运行时】

- **AI 服务运行时绝不直连数据库**（《AI 架构与接口契约》§1：一切业务读写都经过 Java 层）。
  运行时的 RAG 检索只读本脚本生成的 ``data/corpus/knowledge.jsonl``。
- **本脚本是离线构建工具**，允许直连 MySQL：它不参与在线请求链路，只在部署/更新语料时
  手动执行一次，用于把结构化数据（book / borrow_rule）转成可检索文档。
  因此这里的 pymysql 直连不违反契约 §1。

产出（每行一个 JSON chunk，schema 与 W3-1 一致）::

    {"id":"book:12","text":"...","source_type":"book|rule|faq",
     "book_id":12|null,"title":"...","category":"...","url":"/books?id=12"|null,"chunk_no":0}

切分策略：

- ``book``：每本一块，不切分（简介很短，W3-1 决策：以「不切」为默认）；
- ``borrow_rule``：转成 5 段可读文本（借阅 / 续借 / 逾期罚金 / 预约 / 取书与取消），每段 ≤ 400 字；
- ``faq``：内置 30 条常见问题，每条一块（问答同块，便于 BM25 同时命中问题与答案）。

幂等：输出先写同目录临时文件再 ``os.replace`` 原子覆盖；重复执行只覆盖、不追加。
运行：

    cd ai-service && .venv/bin/python scripts/build_corpus.py
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import tempfile
import urllib.parse
from collections import Counter
from pathlib import Path
from typing import Any, Mapping, Sequence

# ai-service/scripts/build_corpus.py -> ai-service/（保证直接执行脚本时能 import app.*）
SERVICE_DIR = Path(__file__).resolve().parents[1]
if str(SERVICE_DIR) not in sys.path:
    sys.path.insert(0, str(SERVICE_DIR))

from app.obs.logging import get_logger, setup_logging  # noqa: E402

logger = get_logger("scripts.build_corpus")

DEFAULT_OUTPUT = SERVICE_DIR / "data" / "corpus" / "knowledge.jsonl"
DEFAULT_DB_HOST = "127.0.0.1"
DEFAULT_DB_PORT = 3308  # 本机隔离 MySQL；生产/其它环境用 DB_HOST/DB_PORT 覆盖
DEFAULT_DB_NAME = "library_db"
DEFAULT_DB_USER = "root"

# 规则 / FAQ 单块上限：超过就说明切分逻辑需要调整（书籍块豁免，见切分策略）
MAX_SEGMENT_CHARS = 400

# 借阅规则缺省值（与 backend 默认值一致；DB 不可用时仅用于让 FAQ 文案自洽）
DEFAULT_RULE: dict[str, Any] = {
    "id": 0,
    "max_borrow_count": 5,
    "borrow_days": 30,
    "max_renew_count": 1,
    "renew_days": 15,
    "fine_per_day": 0.20,
    "fine_max": 20.00,
    "reserve_limit": 3,
    "hold_days": 3,
}


# ======================================================================
# 一、数据库连接与抽取（仅离线构建期使用，运行时服务不 import 本段）
# ======================================================================
def db_config_from_env(env: Mapping[str, str] | None = None) -> dict[str, Any]:
    """解析数据库连接配置：优先 ``DB_URL``，否则 ``DB_HOST/DB_PORT/DB_USERNAME/DB_PASSWORD/DB_NAME``。

    默认指向本机隔离库 ``127.0.0.1:3308/root/空密码/library_db``（无密码仅限本机开发）。
    """
    env = env if env is not None else os.environ
    db_url = (env.get("DB_URL") or "").strip()
    if db_url:
        parsed = urllib.parse.urlparse(db_url)
        if parsed.scheme and not parsed.scheme.startswith("mysql"):
            raise ValueError(f"DB_URL 只支持 mysql:// 协议，收到：{parsed.scheme!r}")
        port_raw = parsed.port or DEFAULT_DB_PORT
        return {
            "host": parsed.hostname or DEFAULT_DB_HOST,
            "port": int(port_raw),
            "user": urllib.parse.unquote(parsed.username or DEFAULT_DB_USER),
            "password": urllib.parse.unquote(parsed.password or ""),
            "database": (parsed.path or "").lstrip("/") or DEFAULT_DB_NAME,
        }

    port_text = (env.get("DB_PORT") or "").strip() or str(DEFAULT_DB_PORT)
    try:
        port = int(port_text)
    except ValueError as exc:
        raise ValueError(f"DB_PORT 必须是整数，收到：{port_text!r}") from exc
    return {
        "host": (env.get("DB_HOST") or "").strip() or DEFAULT_DB_HOST,
        "port": port,
        "user": (env.get("DB_USERNAME") or env.get("DB_USER") or "").strip() or DEFAULT_DB_USER,
        "password": env.get("DB_PASSWORD") or "",
        "database": (env.get("DB_NAME") or "").strip() or DEFAULT_DB_NAME,
    }


def connect_mysql(config: Mapping[str, Any]) -> Any:
    """建立 MySQL 连接（DictCursor + utf8mb4）。

    【离线构建专用】AI 服务运行时没有这条路径；不要在任何 FastAPI 请求里调用本函数。
    """
    try:
        import pymysql  # 局部导入：让本模块被测试 import 时不强依赖数据库驱动
    except ImportError as exc:  # pragma: no cover - 环境已装 pymysql
        raise RuntimeError(
            "缺少 pymysql 依赖，无法直连 MySQL。请先安装："
            ".venv/bin/pip install -r requirements.txt"
        ) from exc

    try:
        return pymysql.connect(
            host=config["host"],
            port=int(config["port"]),
            user=config["user"],
            password=config["password"],
            database=config["database"],
            charset="utf8mb4",
            cursorclass=pymysql.cursors.DictCursor,
            connect_timeout=5,
            read_timeout=30,
        )
    except Exception as exc:
        raise RuntimeError(
            f"连接 MySQL 失败（{config['user']}@{config['host']}:{config['port']}/{config['database']}）：{exc}\n"
            "请确认隔离库已启动：127.0.0.1:3308，库名 library_db（root 空密码）。"
        ) from exc


def fetch_books(connection: Any) -> list[dict[str, Any]]:
    """抽取全部未删除图书（每本一块，不切分）。"""
    sql = (
        "SELECT id, title, author, category, publisher, description, "
        "available_stock, total_stock "
        "FROM book WHERE deleted = 0 ORDER BY id"
    )
    with connection.cursor() as cursor:
        cursor.execute(sql)
        rows = list(cursor.fetchall())
    logger.info(f"从 MySQL 读取图书：{len(rows)} 条", extra={"event": "fetch_books"})
    return rows


def fetch_borrow_rule(connection: Any) -> dict[str, Any] | None:
    """读取当前生效的借阅规则（取最新一条；表空则返回 None，后续用 DEFAULT_RULE）。"""
    with connection.cursor() as cursor:
        cursor.execute("SELECT * FROM borrow_rule ORDER BY id DESC LIMIT 1")
        row = cursor.fetchone()
    if row is None:
        logger.warning("borrow_rule 表为空，规则段将使用默认值", extra={"event": "fetch_rule_empty"})
        return None
    logger.info(f"从 MySQL 读取借阅规则：id={row.get('id')}", extra={"event": "fetch_rule"})
    return dict(row)


# ======================================================================
# 二、切分：book / rule / faq → 统一 chunk
# ======================================================================
def _clean_text(value: Any) -> str:
    """压缩空白、去首尾空格；None → 空串。"""
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def rule_values(rule_row: Mapping[str, Any] | None) -> dict[str, Any]:
    """把规则行规整成模板变量（FAQ / 规则文本共用，保证数字与 DB 一致）。

    金额统一格式化为两位小数字符串（如 ``0.20``）；并额外派生罚金示例的
    ``fine_example_*`` / ``fine_cap_*`` 变量，避免文案里写死与 DB 冲突的数字。
    """
    source: Mapping[str, Any] = rule_row or {}
    values: dict[str, Any] = {
        "rule_id": _as_int(source.get("id"), DEFAULT_RULE["id"]),
        "max_borrow_count": _as_int(source.get("max_borrow_count"), DEFAULT_RULE["max_borrow_count"]),
        "borrow_days": _as_int(source.get("borrow_days"), DEFAULT_RULE["borrow_days"]),
        "max_renew_count": _as_int(source.get("max_renew_count"), DEFAULT_RULE["max_renew_count"]),
        "renew_days": _as_int(source.get("renew_days"), DEFAULT_RULE["renew_days"]),
        "reserve_limit": _as_int(source.get("reserve_limit"), DEFAULT_RULE["reserve_limit"]),
        "hold_days": _as_int(source.get("hold_days"), DEFAULT_RULE["hold_days"]),
    }
    fine_per_day = max(_as_float(source.get("fine_per_day"), DEFAULT_RULE["fine_per_day"]), 0.0)
    fine_max = max(_as_float(source.get("fine_max"), DEFAULT_RULE["fine_max"]), 0.0)
    values["fine_per_day"] = f"{fine_per_day:.2f}"
    values["fine_max"] = f"{fine_max:.2f}"

    example_days = 10
    example_amount = min(fine_per_day * example_days, fine_max)
    values["fine_example_days"] = example_days
    values["fine_example_amount"] = f"{example_amount:.2f}"
    # 达到封顶所需天数（罚金为 0 时没有封顶概念，用 0 占位）
    values["fine_cap_days"] = int(math.ceil(fine_max / fine_per_day)) if fine_per_day > 0 else 0
    values["fine_cap_amount"] = f"{fine_max:.2f}"
    return values


def make_book_chunk(row: Mapping[str, Any]) -> dict[str, Any]:
    """单本书 → 单块。text 格式与 W3-1 约定一致（书名/作者/分类/出版社/库存/简介）。"""
    book_id = _as_int(row.get("id"), 0)
    title = _clean_text(row.get("title")) or f"图书 {book_id}"
    author = _clean_text(row.get("author")) or "佚名"
    category = _clean_text(row.get("category")) or "未分类"
    publisher = _clean_text(row.get("publisher")) or "未知出版社"
    description = _clean_text(row.get("description")) or "暂无简介。"
    available = _as_int(row.get("available_stock"), 0)
    total = _as_int(row.get("total_stock"), 0)
    text = (
        f"《{title}》 作者：{author} | 分类：{category} | 出版社：{publisher} | "
        f"库存：可借 {available}/{total} | 简介：{description}"
    )
    return {
        "id": f"book:{book_id}",
        "text": text,
        "source_type": "book",
        "book_id": book_id,
        "title": title,
        "category": category,
        "url": f"/books?id={book_id}",
        "chunk_no": 0,
    }


def make_book_chunks(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """批量生成书籍块（按输入顺序；每本一块，不切分）。"""
    return [make_book_chunk(row) for row in rows]


RULE_SEGMENTS: tuple[tuple[str, str], ...] = (
    (
        "借阅",
        "借阅规则：每位读者最多可同时借阅 {max_borrow_count} 本图书，单次借期 {borrow_days} 天，"
        "自借出当日起算，借阅数量包含所有已借未还的图书。借书前请确认馆藏库存为“可借”，"
        "库存为 0 时只能预约或等待归还。",
    ),
    (
        "续借",
        "续借规则：图书到期前可办理续借，每本图书最多续借 {max_renew_count} 次，每次续借 {renew_days} 天；"
        "续借后在原到期日基础上顺延，而不是从续借当天重新计算。图书已逾期、已被其他读者预约"
        "或已达续借次数上限时，续借会失败。",
    ),
    (
        "逾期罚金",
        "逾期与罚金：图书超过应还日期未还即视为逾期，逾期罚金按每天 {fine_per_day} 元累计，"
        "从应还日次日开始计算，单笔罚金封顶 {fine_max} 元。例如逾期 {fine_example_days} 天罚金为 "
        "{fine_example_amount} 元；逾期达到 {fine_cap_days} 天及以上时只收取封顶金额 {fine_cap_amount} 元。"
        "罚金未结清前无法继续借书或续借。",
    ),
    (
        "预约",
        "预约规则：当图书可借库存为 0 时读者可以预约，每位读者最多同时预约 {reserve_limit} 本。"
        "预约按提交时间先后排队、先到先得；前一位读者取消预约或超时未取，系统会自动把取书资格"
        "顺延给下一位排队读者。",
    ),
    (
        "取书与取消",
        "取书与取消：预约图书到馆后为读者保留 {hold_days} 天，请在保留期内到馆取书。超过保留期未取，"
        "预约会自动取消并顺延给下一位排队读者。预约可在“我的预约”中主动取消；已到馆待取的图书"
        "不计入在借数量。",
    ),
)


def make_rule_chunks(rule_row: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """借阅规则行 → 5 段可读文本（借阅/续借/罚金/预约/取书），每段 ≤ 400 字。

    数字全部来自数据库当前规则行；规则行缺失时退回 DEFAULT_RULE，保证 FAQ 与规则段自洽。
    """
    values = rule_values(rule_row)
    rule_id = values["rule_id"]
    chunks: list[dict[str, Any]] = []
    for chunk_no, (label, template) in enumerate(RULE_SEGMENTS):
        chunks.append(
            {
                "id": f"rule:{rule_id}:{chunk_no}",
                "text": template.format(**values),
                "source_type": "rule",
                "book_id": None,
                "title": f"借阅规则 · {label}",
                "category": "借阅规则",
                "url": None,
                "chunk_no": chunk_no,
            }
        )
    return chunks


# 内置 FAQ：30 条，覆盖借阅/续借/罚金/预约/账号/馆藏/管理端等高频问题。
# 文案中的数字用 {变量} 占位，构建时用 borrow_rule 当前值填充，避免文档与规则打架。
FAQ_TEMPLATES: tuple[tuple[str, str], ...] = (
    (
        "第一次借书要怎么操作？",
        "登录读者账号后，在馆藏检索页搜索书名或作者，进入图书详情页，库存为“可借”时点击“借阅”即可。"
        "借阅成功后会生成借阅记录，请在到期日前归还；借书数量受每人上限 {max_borrow_count} 本约束。",
    ),
    (
        "一次能借几本书？",
        "每位读者最多可同时借阅 {max_borrow_count} 本图书，包含已借出尚未归还的全部图书。"
        "达到上限后需要先归还部分图书才能继续借书。",
    ),
    (
        "借期是多久？",
        "单次借期为 {borrow_days} 天，从借出当天开始计算。请在到期日之前归还，"
        "逾期会产生罚金并影响后续借阅。",
    ),
    (
        "可以续借吗？续借规则是什么？",
        "可以。每本图书最多续借 {max_renew_count} 次，每次续借 {renew_days} 天；"
        "续借从原到期日顺延，不是从续借当天重新计算。图书已逾期或被他人预约时无法续借。",
    ),
    (
        "逾期罚金怎么算？有封顶吗？",
        "逾期罚金按每天 {fine_per_day} 元计算，从应还日次日开始累计，单笔罚金封顶 {fine_max} 元。"
        "例如逾期 {fine_example_days} 天罚金为 {fine_example_amount} 元；逾期达到 {fine_cap_days} 天"
        "及以上时只收取封顶金额 {fine_cap_amount} 元。",
    ),
    (
        "预约是怎么排队的？",
        "当图书可借库存为 0 时可以预约。预约按提交时间先后顺序排队、先到先得；"
        "前一位读者取消预约或超时未取，系统会自动把取书资格顺延给下一位排队读者。",
    ),
    (
        "预约的书到馆后保留几天？",
        "预约图书到馆后为读者保留 {hold_days} 天，请在保留期内到馆取书。"
        "超过保留期仍未取书，预约会自动取消。",
    ),
    (
        "预约超时未取会怎样？",
        "预约到馆后超过 {hold_days} 天未取，系统会自动取消该预约并把取书资格顺延给下一位排队读者；"
        "本次取消不会额外收取罚金。",
    ),
    (
        "罚金怎么交？",
        "罚金在归还逾期图书时由系统自动计算并生成记录，可在“我的借阅 / 罚金”中查看并结清。"
        "罚金未结清前，通常无法继续借书或办理续借。",
    ),
    (
        "账号密码忘了怎么办？",
        "请联系图书馆管理员重置密码，或使用登录页的找回入口按提示验证身份后重置。"
        "为保护账号安全，请勿把密码告诉他人。",
    ),
    (
        "管理员能做什么？",
        "管理员可以维护图书与馆藏信息、办理借还书、管理读者账号状态、调整借阅规则"
        "（借阅上限、借期、续借、罚金、预约保留天数等）、处理预约与超期记录，以及查看操作日志。",
    ),
    (
        "馆藏图书是怎么分类的？",
        "馆藏按学科与主题分类，例如文学、历史、计算机、科普、少儿、哲学、心理、经济管理、"
        "传记、艺术、教育、社科、生活等；分类会显示在图书详情页，也可在检索时按分类筛选。",
    ),
    (
        "ISBN 是什么？",
        "ISBN 是国际标准书号，用来唯一标识一本图书的版本。馆藏详情页会显示 ISBN，"
        "检索时输入 ISBN 也能精确定位图书。",
    ),
    (
        "可以让别人代借吗？",
        "借阅记录与账号绑定，原则上需使用本人账号借书；如需代借，请先咨询管理员并按馆内规定办理，"
        "避免产生归属不清的借阅记录。",
    ),
    (
        "书丢了怎么处理？",
        "图书遗失请尽快联系管理员登记。通常需要按图书价格或馆内规定赔偿，并结清相关费用；"
        "在遗失处理完成前，该书的借阅记录仍会显示为未归还。",
    ),
    (
        "借书数量到上限了怎么办？",
        "每人最多同时借阅 {max_borrow_count} 本。达到上限时请先归还部分图书，或先处理逾期罚金后再借；"
        "续借不会增加在借数量，但需要满足续借条件。",
    ),
    (
        "怎么查自己的借阅记录？",
        "登录后进入“我的借阅”即可查看在借、已归还与逾期的记录，包括借出日期、应还日期、"
        "续借次数和罚金状态。",
    ),
    (
        "可以跨馆借还吗？",
        "系统按本馆馆藏管理借还。跨馆借还属于馆际互借业务，需线下联系管理员确认是否支持，"
        "系统内暂不直接办理跨馆借还。",
    ),
    (
        "最多能预约几本？",
        "每位读者最多同时预约 {reserve_limit} 本图书。预约到馆后请在 {hold_days} 天内取书，"
        "超时未取会自动取消并顺延给下一位。",
    ),
    (
        "续借为什么会失败？",
        "常见原因：图书已逾期、该书已被其他读者预约、已达到最多续借 {max_renew_count} 次的限制、"
        "或账号存在未结清罚金。请先归还图书或处理罚金后再试。",
    ),
    (
        "逾期会有什么影响？",
        "逾期会产生每天 {fine_per_day} 元的罚金（单笔封顶 {fine_max} 元），罚金未结清前无法继续借书"
        "或续借；长期不还还可能影响账号状态，建议尽快归还。",
    ),
    (
        "可以推荐采购图书吗？",
        "可以通过“图书荐购”入口提交想看的书名、作者和理由，管理员会汇总评估后决定是否采购。"
        "荐购结果可在荐购记录中查看。",
    ),
    (
        "图书馆的开放时间是什么？",
        "开放时间以馆内公告为准，通常工作日白天开放、周末部分时段开放。"
        "线上检索与借阅记录查询不受开放时间限制。",
    ),
    (
        "管理员可以修改借阅规则吗？",
        "可以。管理员可在规则配置中调整借阅上限、借期、续借次数与天数、逾期罚金与封顶、"
        "预约上限和取书保留天数；修改后立即对新的借阅与预约生效。",
    ),
    (
        "怎么批量导入图书？",
        "管理员可使用图书管理中的批量导入功能，按模板填写 ISBN、书名、作者、分类、出版社、库存等字段后上传；"
        "导入后会校验 ISBN 唯一性，重复数据会被跳过或提示修正。",
    ),
    (
        "读者账号有哪些状态？",
        "账号通常分为正常、冻结/禁用等状态。账号被冻结后无法借书或预约，需要联系管理员核实原因并解除；"
        "账号状态可在个人中心查看。",
    ),
    (
        "超期未还的书会怎么处理？",
        "图书超过应还日期即进入逾期状态，系统按天累计罚金（单笔封顶 {fine_max} 元）。"
        "请尽快归还并结清罚金；长期未还时管理员会通过站内通知或线下方式提醒。",
    ),
    (
        "能举个罚金计算的例子吗？",
        "例如借期 {borrow_days} 天、逾期 {fine_example_days} 天：罚金 = {fine_example_days} × "
        "{fine_per_day} = {fine_example_amount} 元。若累计超过 {fine_max} 元，则只收取 {fine_max} 元封顶金额。",
    ),
    (
        "怎么取消预约？",
        "在“我的预约”中选中对应记录点击取消即可。取消后预约名额释放，取书资格会顺延给下一位排队读者；"
        "已到馆待取的预约也可以取消。",
    ),
    (
        "借阅记录会保留多久？",
        "借阅记录会长期保留在系统中，用于查询历史借阅、统计与纠纷核对；归还后记录仍可查询，"
        "管理员可查看全馆借阅与操作日志。",
    ),
)


def make_faq_chunks(rule_row: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    """内置 FAQ 30 条 → 每条一块（问答同块，问题与答案都参与 BM25 匹配）。"""
    values = rule_values(rule_row)
    chunks: list[dict[str, Any]] = []
    for index, (question, answer_template) in enumerate(FAQ_TEMPLATES):
        answer = answer_template.format(**values)
        chunks.append(
            {
                "id": f"faq:{index}",
                "text": f"问：{question}\n答：{answer}",
                "source_type": "faq",
                "book_id": None,
                "title": question,
                "category": "常见问题",
                "url": None,
                "chunk_no": 0,
            }
        )
    return chunks


def build_chunks(
    book_rows: Sequence[Mapping[str, Any]],
    rule_row: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """组装最终语料：书籍块 + 规则块 + FAQ 块（顺序固定，保证输出幂等）。"""
    chunks = [
        *make_book_chunks(book_rows),
        *make_rule_chunks(rule_row),
        *make_faq_chunks(rule_row),
    ]
    validate_chunks(chunks)
    return chunks


def validate_chunks(chunks: Sequence[Mapping[str, Any]]) -> None:
    """校验 id 唯一与单块长度（规则/FAQ ≤ 400 字；书籍短文本按策略不切、豁免）。"""
    seen_ids: set[str] = set()
    for chunk in chunks:
        chunk_id = str(chunk.get("id") or "")
        if not chunk_id:
            raise ValueError("存在缺少 id 的 chunk")
        if chunk_id in seen_ids:
            raise ValueError(f"chunk id 重复：{chunk_id}")
        seen_ids.add(chunk_id)
        text = str(chunk.get("text") or "")
        if not text:
            raise ValueError(f"chunk {chunk_id} 的 text 为空")
        if chunk.get("source_type") in {"rule", "faq"} and len(text) > MAX_SEGMENT_CHARS:
            raise ValueError(
                f"chunk {chunk_id} 长度 {len(text)} 超过 {MAX_SEGMENT_CHARS} 字，请调整切分"
            )


def source_stats(chunks: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    """条数统计（book / rule / faq 顺序）。"""
    counter = Counter(str(chunk.get("source_type")) for chunk in chunks)
    return {"book": counter.get("book", 0), "rule": counter.get("rule", 0), "faq": counter.get("faq", 0)}


# ======================================================================
# 三、输出（原子覆盖，保证幂等）
# ======================================================================
def write_jsonl(chunks: Sequence[Mapping[str, Any]], output_path: str | Path) -> dict[str, Any]:
    """把 chunk 列表写成 JSONL：先写临时文件再原子替换，重复执行只覆盖不追加。"""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    fd, tmp_name = tempfile.mkstemp(
        prefix=".knowledge-", suffix=".jsonl.tmp", dir=str(output_path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            for chunk in chunks:
                handle.write(json.dumps(dict(chunk), ensure_ascii=False) + "\n")
        os.replace(tmp_name, output_path)  # 原子覆盖：不会出现写一半的语料文件
        os.chmod(output_path, 0o644)  # mkstemp 默认 0600，语料文件应为常规可读
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise

    line_count = 0
    with output_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                line_count += 1
    return {
        "path": output_path,
        "line_count": line_count,
        "size_bytes": output_path.stat().st_size,
    }


# ======================================================================
# 四、CLI 入口
# ======================================================================
def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="离线构建 RAG 知识语料：MySQL → data/corpus/knowledge.jsonl（W3-1）",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="输出 JSONL 路径（默认 ai-service/data/corpus/knowledge.jsonl）",
    )
    parser.add_argument(
        "--book-limit",
        type=int,
        default=0,
        help="只取前 N 本书（0=全部；调试用）",
    )
    parser.add_argument(
        "--log-level",
        default=os.environ.get("LOG_LEVEL", "INFO"),
        help="日志级别",
    )
    return parser.parse_args(argv)


def run(args: argparse.Namespace) -> int:
    """执行构建；返回进程退出码（0 成功 / 1 失败）。"""
    config = db_config_from_env()
    logger.info(
        f"读取 MySQL：{config['user']}@{config['host']}:{config['port']}/{config['database']}",
        extra={"event": "db_connect"},
    )
    connection = connect_mysql(config)
    try:
        book_rows = fetch_books(connection)
        rule_row = fetch_borrow_rule(connection)
    finally:
        connection.close()

    if args.book_limit and args.book_limit > 0:
        book_rows = book_rows[: args.book_limit]
        logger.info(f"按 --book-limit 截断到 {len(book_rows)} 本书", extra={"event": "book_limit"})

    chunks = build_chunks(book_rows, rule_row)
    stats = source_stats(chunks)
    result = write_jsonl(chunks, args.output)

    print("=" * 64)
    print("知识语料构建完成（幂等覆盖）")
    print(f"  输出文件：{result['path']}")
    print(f"  条数统计：book {stats['book']} / rule {stats['rule']} / faq {stats['faq']} / 合计 {len(chunks)}")
    print(f"  JSONL 行数：{result['line_count']}（与统计一致：{'是' if result['line_count'] == len(chunks) else '否'}）")
    print(f"  文件大小：{result['size_bytes'] / 1024:.1f} KB")
    print("=" * 64)
    if result["line_count"] != len(chunks):
        logger.warning("JSONL 行数与内存在 chunk 数不一致，请检查写入过程", extra={"code": "LINE_COUNT_MISMATCH"})
        return 1
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    setup_logging(args.log_level)
    try:
        return run(args)
    except Exception as exc:
        print(f"[build_corpus] 构建失败：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
