#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""只读压测：对 localhost 上的应用发起 GET 请求，输出 QPS 与延迟分位。"""

import argparse
import http.client
import json
import threading
import time


class Stats(object):
    def __init__(self):
        self.lock = threading.Lock()
        self.latencies = []
        self.errors = {}
        self.requests = 0

    def record(self, latency_ms, error=None):
        with self.lock:
            self.requests += 1
            if error is None:
                self.latencies.append(latency_ms)
            else:
                self.errors[error] = self.errors.get(error, 0) + 1


def percentile(values, ratio):
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * ratio
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * weight


def worker(host, port, path, token, deadline, stats):
    connection = http.client.HTTPConnection(host, port, timeout=15)
    headers = {"Authorization": "Bearer " + token, "Connection": "keep-alive"}
    while time.time() < deadline:
        started = time.time()
        try:
            connection.request("GET", path, headers=headers)
            response = connection.getresponse()
            response.read()
            latency = (time.time() - started) * 1000.0
            if response.status == 200:
                stats.record(latency)
            else:
                stats.record(latency, "HTTP %d" % response.status)
        except Exception as exc:  # noqa: BLE001
            stats.record((time.time() - started) * 1000.0, type(exc).__name__)
            try:
                connection.close()
            except Exception:  # noqa: BLE001
                pass
            connection = http.client.HTTPConnection(host, port, timeout=15)
    try:
        connection.close()
    except Exception:  # noqa: BLE001
        pass


def run(host, port, path, token, concurrency, duration):
    stats = Stats()
    deadline = time.time() + duration
    threads = [
        threading.Thread(target=worker, args=(host, port, path, token, deadline, stats))
        for _ in range(concurrency)
    ]
    started = time.time()
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    elapsed = time.time() - started
    latencies = stats.latencies
    result = {
        "path": path,
        "concurrency": concurrency,
        "seconds": round(elapsed, 2),
        "total": stats.requests,
        "qps": round(stats.requests / elapsed, 1),
        "success": len(latencies),
        "avg_ms": round(sum(latencies) / len(latencies), 1) if latencies else 0,
        "p50_ms": round(percentile(latencies, 0.50), 1),
        "p90_ms": round(percentile(latencies, 0.90), 1),
        "p95_ms": round(percentile(latencies, 0.95), 1),
        "p99_ms": round(percentile(latencies, 0.99), 1),
        "max_ms": round(max(latencies), 1) if latencies else 0,
        "errors": stats.errors,
    }
    print(json.dumps(result, ensure_ascii=False))
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18080)
    parser.add_argument("--token", required=True)
    parser.add_argument("--paths", required=True, help="逗号分隔的路径列表")
    parser.add_argument("--levels", default="10,25,50", help="并发级别，逗号分隔")
    parser.add_argument("--duration", type=int, default=15)
    args = parser.parse_args()

    paths = [item for item in args.paths.split(",") if item]
    levels = [int(item) for item in args.levels.split(",") if item]

    # 预热
    for path in paths:
        run(args.host, args.port, path, args.token, 1, 3)

    for path in paths:
        for level in levels:
            run(args.host, args.port, path, args.token, level, args.duration)


if __name__ == "__main__":
    main()
