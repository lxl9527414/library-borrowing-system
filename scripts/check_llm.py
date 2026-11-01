#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""真实大模型兼容性自检（W2 起用，mock 模式不需要）

用途：你填好 `.env` 里的 Key 之后，不用起整套服务就能回答三个问题：
  1. 这个模型/网关**能不能连通**（Key、Base URL、网络是否正确）
  2. 它**支不支持 function calling**（Agent 的命门：不支持就只能退化成普通问答）
  3. 流式首 token 延迟、输出速度、token 用量大概多少（面试时可以直接报数字）

运行：
    # 默认读 .env 里的 LLM_PROVIDER
    ai-service/.venv/bin/python scripts/check_llm.py

    # 也可以显式指定
    ai-service/.venv/bin/python scripts/check_llm.py --provider deepseek
    ai-service/.venv/bin/python scripts/check_llm.py --provider qwen --model qwen-plus

退出码：0 = 全部通过；1 = 有检查项失败（报告会打印原因）。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = ROOT / ".env"

PROVIDERS = {
    "deepseek": {
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
        "key_env": "DEEPSEEK_API_KEY",
    },
    "qwen": {
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen-plus",
        "key_env": "DASHSCOPE_API_KEY",
    },
    "ollama": {
        "base_url": "http://127.0.0.1:11434/v1",
        "model": "qwen2.5:7b",
        "key_env": None,  # Ollama 不需要 Key
    },
}

# 与 AI 服务里真实使用的一组工具（用于验证 function calling 兼容性）
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_books",
            "description": "按书名、作者或分类检索图书馆馆藏，返回库存情况",
            "parameters": {
                "type": "object",
                "properties": {
                    "keyword": {"type": "string", "description": "书名、作者或 ISBN 关键词"},
                    "category": {"type": "string", "description": "分类，可选"},
                },
                "required": ["keyword"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "borrow_book",
            "description": "为当前用户借阅一本书（写操作，需用户确认后调用）",
            "parameters": {
                "type": "object",
                "properties": {"bookId": {"type": "integer", "description": "图书 ID"}},
                "required": ["bookId"],
            },
        },
    },
]


def load_env() -> None:
    """把仓库根目录 .env 读进环境变量（不覆盖已存在的）。"""
    if not ENV_FILE.exists():
        return
    for raw in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        name, value = name.strip(), value.strip().strip('"').strip("'")
        os.environ.setdefault(name, value)


def mask(secret: str) -> str:
    if not secret:
        return "(空)"
    return f"{secret[:6]}…{secret[-4:]}" if len(secret) > 12 else "***"


def check(name: str, ok: bool, detail: str = "") -> bool:
    print(f"  {'✅' if ok else '❌'} {name}" + (f"  —— {detail}" if detail else ""))
    return ok


def main() -> int:
    parser = argparse.ArgumentParser(description="真实大模型兼容性自检")
    parser.add_argument("--provider", default=None, help="deepseek | qwen | ollama")
    parser.add_argument("--model", default=None, help="覆盖默认模型名")
    parser.add_argument("--base-url", default=None, help="覆盖默认 Base URL")
    parser.add_argument("--timeout", type=float, default=30)
    args = parser.parse_args()

    load_env()
    provider = (args.provider or os.getenv("LLM_PROVIDER") or "mock").strip().lower()

    print("=" * 68)
    print(f"真实模型兼容性自检 · provider = {provider}")
    print("=" * 68)

    if provider == "mock":
        print("\n当前是 mock 模式（离线、无需 Key）。要联调真实模型，请编辑 .env：")
        print("  LLM_PROVIDER=deepseek")
        print("  DEEPSEEK_API_KEY=sk-xxxx")
        print("\n然后重新执行本脚本。")
        return 0

    if provider not in PROVIDERS:
        print(f"\n❌ 未知 provider：{provider}（可选：{', '.join(PROVIDERS)} 或 mock）")
        return 1

    conf = PROVIDERS[provider]
    key_env = conf["key_env"]
    api_key = os.getenv(key_env) if key_env else "ollama"
    base_url = args.base_url or os.getenv(f"{provider.upper()}_BASE_URL") or conf["base_url"]
    model = args.model or os.getenv(f"{provider.upper()}_MODEL") or conf["model"]

    print(f"\n[1] 配置检查")
    print(f"  · Base URL : {base_url}")
    print(f"  · 模型      : {model}")
    if key_env:
        print(f"  · API Key   : {key_env} = {mask(api_key or '')}")
    ok = True
    if key_env and not api_key:
        ok &= check(f"环境变量 {key_env}", False, "未配置，先填到 .env")
        print("\n配置不全，终止。")
        return 1
    ok &= check("配置齐全", True)

    try:
        from openai import OpenAI
    except ImportError:
        print("\n❌ 缺少 openai 依赖，请在 ai-service 虚拟环境里运行本脚本：")
        print("   ai-service/.venv/bin/python scripts/check_llm.py")
        return 1

    client = OpenAI(api_key=api_key or "ollama", base_url=base_url, timeout=args.timeout)

    # ---- 2) 基础连通性 ----
    print("\n[2] 基础对话（连通性）")
    started = time.perf_counter()
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "只回复两个字：收到"}],
            max_tokens=16,
            temperature=0,
        )
        latency = (time.perf_counter() - started) * 1000
        content = (resp.choices[0].message.content or "").strip()
        ok &= check("模型可调用", True, f"{latency:.0f} ms，返回：{content[:40]!r}")
        if resp.usage:
            print(f"       token 用量：prompt {resp.usage.prompt_tokens} / completion {resp.usage.completion_tokens}")
    except Exception as exc:  # noqa: BLE001
        ok &= check("模型可调用", False, f"{type(exc).__name__}: {exc}")
        print("\n常见原因：Key 错误 / 余额不足 / Base URL 不对 / 网络需要代理")
        return 1

    # ---- 3) function calling 兼容性（Agent 的命门）----
    print("\n[3] Function Calling（Agent 工具调用的前提）")
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "你是图书馆助手。需要检索馆藏时必须调用工具，不要自己编造书名。"},
                {"role": "user", "content": "帮我找一下《三体》，看看还有没有库存"},
            ],
            tools=TOOLS,
            tool_choice="auto",
            temperature=0,
        )
        message = resp.choices[0].message
        calls = message.tool_calls or []
        ok &= check("返回了 tool_calls", bool(calls), f"共 {len(calls)} 个")
        for call in calls:
            print(f"       → {call.function.name}({call.function.arguments})")
        if calls:
            ok &= check("工具名在允许列表内", all(c.function.name in {"search_books", "borrow_book"} for c in calls))
            try:
                json.loads(calls[0].function.arguments)
                ok &= check("arguments 是合法 JSON", True)
            except json.JSONDecodeError:
                ok &= check("arguments 是合法 JSON", False, calls[0].function.arguments[:120])
        else:
            print("       ⚠️ 该模型可能不支持 function calling，或倾向于直接回答。")
            print("          若是 DeepSeek/Qwen 之外的模型，请确认其 OpenAI 兼容层是否实现 tools 参数。")
    except Exception as exc:  # noqa: BLE001
        ok &= check("Function Calling", False, f"{type(exc).__name__}: {exc}")

    # ---- 4) 流式与首 token 延迟 ----
    print("\n[4] 流式输出（TTFT = 首 token 延迟，决定用户感知快慢）")
    try:
        started = time.perf_counter()
        ttft = None
        chunks: list[str] = []
        stream = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "用一句话介绍图书馆的借阅流程"}],
            max_tokens=80,
            stream=True,
            temperature=0.3,
        )
        for chunk in stream:
            delta = chunk.choices[0].delta.content if chunk.choices else None
            if delta:
                if ttft is None:
                    ttft = (time.perf_counter() - started) * 1000
                chunks.append(delta)
        total = (time.perf_counter() - started) * 1000
        text = "".join(chunks)
        chars = len(text)
        tps = chars / (total / 1000) if total else 0
        ok &= check("流式可用", chars > 0, f"TTFT {ttft or 0:.0f} ms / 总耗时 {total:.0f} ms / {chars} 字")
        if ttft:
            print(f"       输出速度约 {tps:.1f} 字/秒；这组数字可以直接写进项目文档和简历")
    except Exception as exc:  # noqa: BLE001
        ok &= check("流式可用", False, f"{type(exc).__name__}: {exc}")

    print("\n" + "=" * 68)
    if ok:
        print("✅ 全部通过：可以把 .env 的 LLM_PROVIDER 切到该模型，重启 AI 服务即可真实联调")
        print("   bash scripts/dev_down.sh && bash scripts/dev_up.sh")
        print("   ai-service/.venv/bin/python scripts/e2e_ai.py")
        return 0
    print("⚠️ 有检查项未通过，见上方 ❌ 明细。Agent 主链路需要 function calling 支持。")
    return 1


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
