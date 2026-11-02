"""系统提示词：身份来自 Java 接口、规则数值不硬编码、罚金口径正确。"""

from __future__ import annotations

from app.agent.prompts import build_system_prompt

CONTEXT = {
    "user": {"id": 2, "username": "reader", "realName": "张小明", "role": "READER"},
    "rule": {
        "maxBorrowCount": 5,
        "borrowDays": 30,
        "maxRenewCount": 1,
        "renewDays": 15,
        "finePerDay": 0.2,
        "fineMax": 20.0,
        "reserveLimit": 3,
        "holdDays": 3,
    },
    "borrowedCount": 2,
    "overdueCount": 1,
    "activeReservationCount": 1,
    "pendingPickupCount": 0,
}


def test_prompt_injects_user_identity_and_rule_values_from_backend() -> None:
    prompt = build_system_prompt(CONTEXT, "每人最多同时借阅 5 本，借期 30 天。")
    assert "张小明" in prompt
    assert "READER" in prompt
    # 规则数值全部来自接口，而不是硬编码
    assert "最多同时借阅 5 本" in prompt
    assert "借期 30 天" in prompt
    assert "逾期每天罚金 0.2 元" in prompt
    assert "每人最多同时借阅 5 本，借期 30 天。" in prompt


def test_prompt_forbids_reporting_fine_for_on_loan_books() -> None:
    """现网罚金恒为 0，只有归还时结算：提示词必须约束模型不要报在借罚金金额。"""
    prompt = build_system_prompt(CONTEXT, None)
    assert "不要对在借图书报罚金金额" in prompt
    assert "只报逾期天数" in prompt


def test_prompt_requires_tools_for_facts_and_mentions_write_confirmation() -> None:
    prompt = build_system_prompt(CONTEXT, None)
    assert "必须先调用工具" in prompt
    assert "确认卡片" in prompt


def test_prompt_without_context_degrades_gracefully() -> None:
    prompt = build_system_prompt(None, None)
    assert "无法连接业务后端" in prompt
    assert "写操作会在执行时再次校验" in prompt
