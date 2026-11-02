#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""云阁图书借阅系统 · 轻量只读压测脚本

与 docs/压测报告.md 中的命令完全兼容：

    PYTHONIOENCODING=utf-8 python3 docs/loadtest.py \
        --token "$TOKEN" \
        --paths "/api/books?current=1&size=10,/api/borrows?current=1&size=10,/api/dashboard" \
        --levels 10,25,50 \
        --duration 15

设计要点（面试可讲）：
- 只用标准库（http.client + ThreadPoolExecutor），不依赖 pip 包，任何机器可直接跑
- 固定并发模型：每个档次起 N 个线程循环发请求，统计窗口内的真实吞吐（不是"发完算平均"）
- 分位数用最近秩法（nearest-rank），与 wrk / ab 的口径一致，避免线性插值失真
- 连接不复用（每个请求新建连接），比 keep-alive 更保守，测的是「冷连接 + 服务端处理」能力
- 输出机器可读的 JSON（--json）方便和压测报告的数据对比

注意：本脚本只做 GET 只读压测，不写业务数据。
"""

from __future__ import annotations

import argparse
import http.client
import json
import statistics
import sys
import threading
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field


@dataclass
class Sample:
    """单个档次的统计结果。"""

    path: str
    concurrency: int
    requests: int = 0
    failures: int = 0
    latencies_ms: list[float] = field(default_factory=list)
    status_codes: dict[int, int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    duration_s: float = 0.0

    # ---- 派生指标 ----
    @property
    def qps(self) -> float:
        return self.requests / self.duration_s if self.duration_s else 0.0

    def _percentile(self, pct: float) -> float:
        """最近秩法分位数。"""
        if not self.latencies_ms:
            return 0.0
        data = sorted(self.latencies_ms)
        rank = max(1, int(round(pct / 100.0 * len(data) + 0.5)) - 1)
        return data[min(rank, len(data) - 1)]

    @property
    def avg(self) -> float:
        return statistics.fmean(self.latencies_ms) if self.latencies_ms else 0.0

    @property
    def p50(self) -> float:
        return self._percentile(50)

    @property
    def p90(self) -> float:
        return self._percentile(90)

    @property
    def p95(self) -> float:
        return self._percentile(95)

    @property
    def p99(self) -> float:
        return self._percentile(99)

    @property
    def peak(self) -> float:
        return max(self.latencies_ms) if self.latencies_ms else 0.0

    def as_row(self) -> dict:
        return {
            "path": self.path,
            "concurrency": self.concurrency,
            "requests": self.requests,
            "failures": self.failures,
            "qps": round(self.qps, 1),
            "avg_ms": round(self.avg, 1),
            "p50_ms": round(self.p50, 1),
            "p90_ms": round(self.p90, 1),
            "p95_ms": round(self.p95, 1),
            "p99_ms": round(self.p99, 1),
            "max_ms": round(self.peak, 1),
            "status_codes": self.status_codes,
        }


def _one_request(host: str, port: int, path: str, token: str | None,
                 timeout: float, extra_headers: dict[str, str] | None = None,
                 ) -> tuple[int | None, float, str | None]:
    """发一个请求，返回 (状态码, 耗时ms, 错误信息)。"""
    started = time.perf_counter()
    conn = None
    try:
        conn = http.client.HTTPConnection(host, port, timeout=timeout)
        headers = {"Accept": "application/json", "Connection": "close"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if extra_headers:
            headers.update(extra_headers)
        conn.request("GET", path, headers=headers)
        resp = conn.getresponse()
        # 必须读完 body，否则连接关闭时机不准，会低估耗时
        body = resp.read()
        elapsed = (time.perf_counter() - started) * 1000
        code = resp.status
        if code >= 400:
            return code, elapsed, f"HTTP {code}: {body[:120]!r}"
        return code, elapsed, None
    except Exception as exc:  # noqa: BLE001 - 压测脚本需要把任何异常记为失败
        elapsed = (time.perf_counter() - started) * 1000
        return None, elapsed, f"{type(exc).__name__}: {exc}"
    finally:
        if conn is not None:
            conn.close()


def _worker(host: str, port: int, path: str, token: str | None, timeout: float,
            stop: threading.Event, sample: Sample, lock: threading.Lock,
            extra_headers: dict[str, str] | None = None) -> None:
    """单个压测线程：在 stop 置位前持续发请求。"""
    local_latencies: list[float] = []
    local_codes: dict[int, int] = {}
    local_requests = 0
    local_failures = 0
    local_errors: list[str] = []
    while not stop.is_set():
        code, elapsed, error = _one_request(host, port, path, token, timeout, extra_headers)
        local_requests += 1
        if error is None:
            local_latencies.append(elapsed)
            local_codes[code] = local_codes.get(code, 0) + 1
        else:
            local_failures += 1
            if len(local_errors) < 3 and error not in local_errors:
                local_errors.append(error)
    with lock:
        sample.requests += local_requests
        sample.failures += local_failures
        sample.latencies_ms.extend(local_latencies)
        for code, count in local_codes.items():
            sample.status_codes[code] = sample.status_codes.get(code, 0) + count
        sample.errors.extend(e for e in local_errors if e not in sample.errors)


def run_level(host: str, port: int, path: str, token: str | None, concurrency: int,
              duration: float, timeout: float,
              extra_headers: dict[str, str] | None = None) -> Sample:
    """跑一个并发档次。"""
    sample = Sample(path=path, concurrency=concurrency)
    stop = threading.Event()
    lock = threading.Lock()

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = [
            pool.submit(_worker, host, port, path, token, timeout, stop, sample, lock,
                        extra_headers)
            for _ in range(concurrency)
        ]
        time.sleep(duration)
        stop.set()
        for future in futures:
            future.result()
    # 统计窗口固定取 duration：等待线程退出的尾部时间不计入，避免拉低 QPS 口径
    sample.duration_s = duration
    return sample


def _parse_target(base: str) -> tuple[str, int]:
    parsed = urllib.parse.urlparse(base if "://" in base else f"http://{base}")
    return parsed.hostname or "127.0.0.1", parsed.port or 80


def _print_table(rows: list[dict]) -> None:
    headers = ["路径", "并发", "请求数", "失败", "QPS", "平均", "P50", "P90",
               "P95", "P99", "最大"]
    keys = ["path", "concurrency", "requests", "failures", "qps", "avg_ms",
            "p50_ms", "p90_ms", "p95_ms", "p99_ms", "max_ms"]
    widths = [len(h) for h in headers]
    for row in rows:
        for idx, key in enumerate(keys):
            widths[idx] = max(widths[idx], len(str(row[key])))

    def fmt(values: list[str]) -> str:
        return " | ".join(str(v).ljust(widths[i]) for i, v in enumerate(values))

    print(fmt(headers))
    print("-+-".join("-" * w for w in widths))
    for row in rows:
        print(fmt([str(row[k]) for k in keys]))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="只读压测脚本（仅标准库实现）")
    parser.add_argument("--base", default="http://127.0.0.1:18080", help="被测服务地址")
    parser.add_argument("--token", default="", help="Bearer token（登录接口返回）")
    parser.add_argument("--paths", required=True, help="逗号分隔的 GET 路径")
    parser.add_argument("--levels", default="10,25,50", help="并发梯度，逗号分隔")
    parser.add_argument("--duration", type=float, default=15, help="单档次持续秒数")
    parser.add_argument("--warmup", type=float, default=3, help="预热秒数")
    parser.add_argument("--timeout", type=float, default=10, help="单请求超时秒数")
    parser.add_argument("--header", action="append", default=[], metavar="'K: V'",
                        help="额外请求头，可重复，如 --header 'X-Internal-Token: xxx'")
    parser.add_argument("--json", default="", help="把结果写入该 JSON 文件")
    args = parser.parse_args(argv)

    host, port = _parse_target(args.base)
    paths = [p.strip() for p in args.paths.split(",") if p.strip()]
    levels = [int(x) for x in args.levels.split(",") if x.strip()]
    token = args.token or None
    extra_headers: dict[str, str] = {}
    for raw in args.header:
        name, _, value = raw.partition(":")
        if not name.strip():
            parser.error(f"非法请求头: {raw!r}")
        extra_headers[name.strip()] = value.strip()

    print(f"目标 {args.base} | 并发 {levels} | 每档 {args.duration:g}s | 预热 {args.warmup:g}s")
    if token:
        print(f"鉴权 Bearer {token[:8]}...{token[-4:] if len(token) > 12 else ''}")

    # 预热：并发 1，避免 JVM / 页面缓存冷启动影响第一档数据
    if args.warmup > 0:
        print("预热中…", end="", flush=True)
        for path in paths:
            deadline = time.perf_counter() + args.warmup
            while time.perf_counter() < deadline:
                _one_request(host, port, path, token, args.timeout, extra_headers)
        print(" 完成\n")

    rows: list[dict] = []
    for path in paths:
        print(f"▶ {path}")
        for level in levels:
            sample = run_level(host, port, path, token, level, args.duration, args.timeout,
                               extra_headers)
            rows.append(sample.as_row())
            print(f"  并发 {level:>3} → {sample.qps:7.1f} QPS, "
                  f"P95 {sample.p95:7.1f}ms, 失败 {sample.failures}")
            for err in sample.errors[:2]:
                print(f"      失败原因: {err[:160]}")
        print()

    print("=" * 96)
    _print_table(rows)
    print("=" * 96)

    total = sum(r["requests"] for r in rows)
    failures = sum(r["failures"] for r in rows)
    best = max(rows, key=lambda r: r["qps"]) if rows else None
    print(f"总请求 {total}，失败 {failures}，成功率 "
          f"{(1 - failures / total) * 100 if total else 0:.2f}%")
    if best:
        print(f"峰值吞吐 {best['qps']} QPS（{best['path']}，并发 {best['concurrency']}）")

    if args.json:
        with open(args.json, "w", encoding="utf-8") as fp:
            json.dump({"base": args.base, "rows": rows}, fp, ensure_ascii=False, indent=2)
        print(f"结果已写入 {args.json}")

    return 0 if failures == 0 else 1


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
