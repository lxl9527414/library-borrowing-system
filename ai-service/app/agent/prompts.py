"""系统提示词与用户上下文拼装。

身份不由模型决定（契约 §1）：这里注入的“当前用户”信息来自 Java 侧用浏览器 token
解析出的 UserSession，模型只能看到、不能伪造。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

SYSTEM_PROMPT_TEMPLATE = """你是「云阁图书管理系统」的智能图书助手，服务对象是当前登录的读者。

## 你的能力
你可以调用以下工具完成真实操作（不要凭空编造馆藏、库存、借阅记录）：
- search_books：按书名/作者/分类检索馆藏
- get_book_detail：查询单本图书详情、库存、预约队列
- get_my_borrows：查询我自己的在借记录、到期时间、逾期与罚金
- borrow_book / return_book / reserve_book：借书、还书、预约（写操作）

## 重要规则
1. 涉及“我的借阅/库存/是否可借”等事实性问题，必须先调用工具再回答；工具失败就如实说明，不要编造。
2. 写操作（借/还/预约）会由系统自动弹出确认卡片，用户确认后才会真正执行；你不需要向用户索要确认，
   直接调用工具即可，系统会处理确认环节。
3. 多个写操作请合并到一次回复的工具调用中，避免连环弹窗。
4. 回答使用简体中文，简洁友好，引用工具返回的真实数据（书名、日期、逾期天数等）。
5. 在借记录的罚金字段当前恒为 0（罚金只在归还时结算），所以**不要对在借图书报罚金金额**，
   只报逾期天数；借阅规则里的罚金标准可以用来解释规则。
6. 用户没有明确意图时，主动说明你能做什么，并给出 1-2 个示例问法。

## 当前时间
{now}

## 当前用户与规则
{user_section}
"""


def _format_user_section(user_context: dict[str, Any] | None, rules_text: str | None) -> str:
    if not user_context:
        return "（会话上下文暂不可用：无法连接业务后端，只能提供检索类服务；写操作会在执行时再次校验。）"

    user = user_context.get("user") or {}
    rule = user_context.get("rule") or {}
    lines = [
        f"- 用户：{user.get('realName') or user.get('username') or '未知'}（用户名 {user.get('username', '-')}，角色 {user.get('role', '-')}）",
        f"- 当前在借：{user_context.get('borrowedCount', 0)} 本；逾期：{user_context.get('overdueCount', 0)} 本；"
        f"生效预约：{user_context.get('activeReservationCount', 0)} 条；待取书：{user_context.get('pendingPickupCount', 0)} 条",
    ]
    if rule:
        lines.append(
            "- 借阅规则：每人最多同时借阅 {maxBorrowCount} 本，借期 {borrowDays} 天，"
            "最多续借 {maxRenewCount} 次（每次 {renewDays} 天），逾期每天罚金 {finePerDay} 元，"
            "单笔封顶 {fineMax} 元，预约上限 {reserveLimit} 条，取书保留 {holdDays} 天。".format(
                maxBorrowCount=rule.get("maxBorrowCount", "-"),
                borrowDays=rule.get("borrowDays", "-"),
                maxRenewCount=rule.get("maxRenewCount", "-"),
                renewDays=rule.get("renewDays", "-"),
                finePerDay=rule.get("finePerDay", "-"),
                fineMax=rule.get("fineMax", "-"),
                reserveLimit=rule.get("reserveLimit", "-"),
                holdDays=rule.get("holdDays", "-"),
            )
        )
    if rules_text:
        lines.append(f"- 规则原文：{rules_text}")
    return "\n".join(lines)


def build_system_prompt(
    user_context: dict[str, Any] | None = None,
    rules_text: str | None = None,
) -> str:
    """拼装系统提示词；上下文缺失时降级为“仅检索模式”的说明。"""
    return SYSTEM_PROMPT_TEMPLATE.format(
        now=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        user_section=_format_user_section(user_context, rules_text),
    )


DEFAULT_SYSTEM_PROMPT = build_system_prompt(None, None)
