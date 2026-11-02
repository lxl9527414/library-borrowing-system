#!/usr/bin/env bash
# =====================================================================
# 生成 Spring Security 兼容的 bcrypt 密码哈希（用于轮换 admin/reader 密码）
#
# 为什么需要它：数据库里存的是 bcrypt 哈希，不能直接存明文；
# 而服务器上不一定有 python-bcrypt，所以这里按可用性依次降级：
#   1) htpasswd（httpd-tools，输出 $2y$，Spring 兼容）
#   2) python3 + bcrypt 模块（输出 $2b$）
#   3) python3 + passlib（如有）
#
# 用法（建议从 stdin 读密码，避免进 shell 历史）：
#   read -rs NEW_PW && printf '%s' "$NEW_PW" | bash scripts/gen_bcrypt_hash.sh
#   bash scripts/gen_bcrypt_hash.sh 'YourStrongPassword'
# =====================================================================
set -euo pipefail
PW="${1:-}"
if [ -z "$PW" ]; then
  read -r PW || true
fi
[ -n "$PW" ] || { echo "用法：bash scripts/gen_bcrypt_hash.sh '<密码>' 或从 stdin 传入" >&2; exit 1; }

if command -v htpasswd >/dev/null 2>&1; then
  # -n 输出到 stdout；-b 从参数取密码；-B 用 bcrypt；-C 10 轮
  printf '%s\n' "$PW" | { IFS= read -r p; htpasswd -nbBC 10 user "$p"; } | cut -d: -f2
  exit 0
fi

if python3 -c "import bcrypt" >/dev/null 2>&1; then
  python3 - "$PW" <<'PY'
import sys, bcrypt
print(bcrypt.hashpw(sys.argv[1].encode(), bcrypt.gensalt(rounds=10, prefix=b"2b")).decode())
PY
  exit 0
fi

if python3 -c "from passlib.hash import bcrypt" >/dev/null 2>&1; then
  python3 - "$PW" <<'PY'
import sys
from passlib.hash import bcrypt
print(bcrypt.using(rounds=10, ident="2b").hash(sys.argv[1]))
PY
  exit 0
fi

echo "❌ 没有可用的 bcrypt 工具。请安装其一后重试：" >&2
echo "   CentOS: yum install -y httpd-tools     # 提供 htpasswd" >&2
echo "   或:     python3 -m pip install bcrypt" >&2
exit 1
