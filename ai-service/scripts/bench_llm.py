#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""LLM 延迟/吞吐基准（W4-3 成本与性能报告的数据来源）

测三件事，都直接打真实接口（不是从 mock 推）：
1. TTFT（Time To First Token，首 token 延迟）—— 决定用户感知"快不快"
2. 完整响应耗时与输出速度（字/秒）
3. 多次采样的 P50 / P95（单次测量会被网络抖动带偏）

用法：
    ai-service/.venv/bin/python scripts/bench_llm.py                    # 默认 15 次
    ai-service/.venv/bin/python scripts/bench_llm.py --samples 30
    ai-service/.venv/bin/python scripts/bench_llm.py --provider qwen    # 需 DASHSCOPE_API_KEY

输出：终端表格 + `ai-service/eval/bench_llm.json`（供成本报告引用）
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

AI_ROOT = Path(__file__).resolve().parent.parent

PROVIDERS = {
    "deepseek": {
        "base_env": "DEEPSEEK_BASE_URL",
        "base_default": "https://api.deepseek.com/v1",
        "model_env": "DEEPSEEK_MODEL",
        "model_default": "deepseek-chat",
        "key_env": "DEEPSEEK_API_KEY",
    },
    "qwen": {
        "base_env": "QWEN_BASE_URL",
        "base_default": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model_env": "QWEN_MODEL",
        "model_default": "qwen-plus",
        "key_env": "DASHSCOPE_API_KEY",
    },
}

PROMPTS = [
    "用一句话说明图书馆借阅规则中最容易忽略的一条。",
    "用户问：我的书逾期了会怎样？请用两步说明罚金如何计算。",
    "帮我把「借书、续借、归还、预约」四个动作按读者视角排序并解释理由。",
]


def load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    env_file = AI_ROOT.parent / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def percentile(values: list[float], pct: float) -> float:
    """最近秩法分位数（与 docs/loadtest.py 口径一致）。"""
    if not values:
        return 0.0
    data = sorted(values)
    rank = max(1, int(round(pct / 100.0 * len(data) + 0.5)) - 1)
    return data[min(rank, len(data) - 1)]


def main() -> int:
    parser = argparse.ArgumentParser(description="LLM 延迟/吞吐基准")
    parser.add_argument("--provider", default="deepseek", choices=list(PROVIDERS))
    parser.add_argument("--samples", type=int, default=15)
    parser.add_argument("--max-tokens", type=int, default=120)
    parser.add_argument("--out", default=str(AI_ROOT / "eval" / "bench_llm.json"))
    args = parser.parse_args()

    env = load_env()
    conf = PROVIDERS[args.provider]
    api_key = env.get(conf["key_env"], "")
    if not api_key:
        raise SystemExit(f"缺少 {conf['key_env']}，无法基准测试")
    base_url = env.get(conf["base_env"], conf["base_default"]).rstrip("/")
    model = env.get(conf["model_env"], conf["model_default"])

    import httpx

    print(f"基准 provider={args.provider} model={model} samples={args.samples}\n")
    ttfts: list[float] = []
    totals: list[float] = []
    speeds: list[float] = []
    prompt_tokens: list[int] = []
    completion_tokens: list[int] = []
    failures = 0

    with httpx.Client(timeout=120) as client:
        for index in range(args.samples):
            prompt = PROMPTS[index % len(PROMPTS)]
            started = time.perf_counter()
            ttft = None
            chunks: list[str] = []
            usage: dict = {}
            try:
                with client.stream(
                    "POST",
                    f"{base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                    json={
                        "model": model,
                        "messages": [{"role": "user", "content": prompt}],
                        "max_tokens": args.max_tokens,
                        "temperature": 0.3,
                        "stream": True,
                        "stream_options": {"include_usage": True},
                    },
                ) as resp:
                    resp.raise_for_status()
                    for line in resp.iter_lines():
                        if not line.startswith("data:"):
                            continue
                        raw = line[len("data:"):].strip()
                        if raw == "[DONE]":
                            break
                        try:
                            event = json.loads(raw)
                        except json.JSONDecodeError:
                            continue
                        if event.get("usage"):
                            usage = event["usage"]
                        choices = event.get("choices") or []
                        delta = (choices[0].get("delta") or {}).get("content") if choices else None
                        if delta:
                            if ttft is None:
                                ttft = (time.perf_counter() - started) * 1000
                            chunks.append(delta)
                total = (time.perf_counter() - started) * 1000
                text = "".join(chunks)
                if not text or ttft is None:
                    raise RuntimeError("没有收到内容")
                ttfts.append(ttft)
                totals.append(total)
                speeds.append(len(text) / (total / 1000))
                prompt_tokens.append(int(usage.get("prompt_tokens") or 0))
                completion_tokens.append(int(usage.get("completion_tokens") or 0))
                print(f"  [{index + 1}/{args.samples}] TTFT {ttft:7.0f} ms | 总 {total:7.0f} ms | "
                      f"{len(text):3d} 字 | {len(text) / (total / 1000):5.1f} 字/秒")
            except Exception as exc:  # noqa: BLE001
                failures += 1
                print(f"  [{index + 1}/{args.samples}] ❌ {type(exc).__name__}: {str(exc)[:80]}")
            time.sleep(0.5)

    if not ttfts:
        print("\n全部失败，无法统计")
        return 1

    result = {
        "provider": args.provider,
        "model": model,
        "samples_requested": args.samples,
        "samples_ok": len(ttfts),
        "failures": failures,
        "ttft_ms": {
            "p50": round(percentile(ttfts, 50), 1),
            "p95": round(percentile(ttfts, 95), 1),
            "min": round(min(ttfts), 1),
            "max": round(max(ttfts), 1),
        },
        "total_ms": {
            "p50": round(percentile(totals, 50), 1),
            "p95": round(percentile(totals, 95), 1),
            "avg": round(statistics.fmean(totals), 1),
        },
        "chars_per_second_avg": round(statistics.fmean(speeds), 1),
        "tokens": {
            "prompt_avg": round(statistics.fmean(prompt_tokens), 1) if prompt_tokens else None,
            "completion_avg": round(statistics.fmean(completion_tokens), 1) if completion_tokens else None,
        },
    }

    print("\n" + "=" * 62)
    print(f"TTFT   P50 {result['ttft_ms']['p50']} ms | P95 {result['ttft_ms']['p95']} ms")
    print(f"总耗时 P50 {result['total_ms']['p50']} ms | P95 {result['total_ms']['p95']} ms")
    print(f"输出速度 {result['chars_per_second_avg']} 字/秒")
    if result["tokens"]["completion_avg"]:
        print(f"平均 token：输入 {result['tokens']['prompt_avg']} / 输出 {result['tokens']['completion_avg']}")
    if failures:
        print(f"⚠️ 失败 {failures} 次（已从统计中剔除，生产需重试）")
    print(f"\n结果已写入 {args.out}")
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
