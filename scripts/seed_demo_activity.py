#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成本地演示数据（借阅活动），让工作台/看板看起来像"真的有人用"

做法：走真实接口借书（保证库存扣减与业务规则一致），再把其中几条改成逾期，
这样"当前借出/逾期"统计是真实的，不是直接插 SQL 造成的假数据。

幂等：如果演示读者已有 10 条以上在借记录，则跳过（--force 可强制再加）。

用法：
    ai-service/.venv/bin/python scripts/seed_demo_activity.py
    ai-service/.venv/bin/python scripts/seed_demo_activity.py --force --overdue 6
"""

from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys

import httpx

ROOT = pathlib.Path(__file__).resolve().parent.parent


def load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--java", default="http://127.0.0.1:18080")
    parser.add_argument("--readers", type=int, default=10, help="参与演示的读者数量")
    parser.add_argument("--per-reader", type=int, default=2, help="每人借几本")
    parser.add_argument("--overdue", type=int, default=4, help="把几条改成逾期")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    env = load_env()
    internal = env.get("AI_INTERNAL_TOKEN", "dev-internal-token")
    reader_pw = "reader123"

    with httpx.Client(timeout=30) as client:
        # 1) 统计现有在借，决定是否跳过
        admin_login = client.post(f"{args.java}/api/auth/login",
                                  json={"username": "admin", "password": "admin123"}).json()
        if admin_login.get("code") != 200:
            raise SystemExit("管理员登录失败：请确认本地库 admin 密码为 admin123（或改脚本默认）")
        admin_token = admin_login["data"]["token"]
        headers_admin = {"Authorization": f"Bearer {admin_token}"}
        stats = client.get(f"{args.java}/api/dashboard", headers=headers_admin).json().get("data") or {}
        current = int(stats.get("currentBorrowed") or stats.get("borrowedCount") or 0)
        print(f"当前在借（工作台）：{current}")
        if current >= 10 and not args.force:
            print("已有足够演示数据，跳过（加 --force 强制追加）")
            return 0

        # 2) 真实借书
        book_ids = list(range(1, 61))
        created = 0
        for index in range(1, args.readers + 1):
            uname = "reader" if index == 1 else f"reader{index}"
            login = client.post(f"{args.java}/api/auth/login",
                                json={"username": uname, "password": reader_pw}).json()
            if login.get("code") != 200:
                print(f"  ⚠ {uname} 登录失败，跳过")
                continue
            token = login["data"]["token"]
            headers = {"Authorization": f"Bearer {token}", "X-Internal-Token": internal,
                       "Content-Type": "application/json"}
            for n in range(args.per_reader):
                book_id = book_ids[(index * args.per_reader + n) % len(book_ids)]
                resp = client.post(f"{args.java}/api/ai/internal/borrow", headers=headers,
                                   json={"bookId": book_id}).json()
                ok = resp.get("code") == 200
                created += 1 if ok else 0
                print(f"  {uname} 借 bookId={book_id}: {'✅' if ok else '❌ ' + str(resp.get('message'))}")

    # 3) 把最后 N 条改成逾期（库存已在借书时扣减，这里只改时间，数据自洽）
    if args.overdue > 0:
        sql = (f"UPDATE borrow_record SET borrowed_at = DATE_SUB(NOW(), INTERVAL 40 DAY), "
               f"due_at = DATE_SUB(NOW(), INTERVAL 10 DAY) "
               f"WHERE status='BORROWED' ORDER BY id DESC LIMIT {args.overdue};")
        out = subprocess.run(["/usr/local/mysql/bin/mysql", "-h127.0.0.1", "-P3308", "-uroot",
                              "library_db", "-e", sql], capture_output=True, text=True)
        print(("✅ 已设置逾期记录" if out.returncode == 0 else f"❌ 逾期设置失败：{out.stderr[:150]}"))

    print(f"\n完成：本次新增借阅 {created} 条")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
