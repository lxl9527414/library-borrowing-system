#!/usr/bin/env bash
# =====================================================================
# 仓库密钥泄漏检查（W4-5 凭据治理）
#
# 只扫描 git 已跟踪的文件（未跟踪的本地 .env / credentials.local 不扫），
# 命中以下模式即以非 0 退出，可直接用作 CI 门禁：
#   - OpenAI/DeepSeek/通义 风格的 sk- 长密钥
#   - password/secret 赋值且值看起来像真实凭据
#   - 私钥文件头
#
# 允许的占位符（文档/测试用）：sk-xxx、sk-test、sk-should-never-appear、<redacted>、
#   your-key、***、root、reader123/admin123（种子账号除外，见 ALLOW 列表）
#
# 用法：bash scripts/check_secrets.sh
# =====================================================================
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

FOUND=0
report() { printf '\033[1;31m[secrets] ✗ %s\033[0m\n' "$*"; FOUND=1; }

echo "[secrets] 扫描 git 已跟踪文件…"
FILES=$(git ls-files)
[ -z "$FILES" ] && { echo "[secrets] 没有已跟踪文件（还没 git add？）"; exit 0; }

# 1) sk- 长密钥
while IFS= read -r hit; do
  line=${hit#*:}
  case "$line" in
    *sk-xxx*|*sk-test*|*sk-should-never-appear*|*sk-你的*|*sk-lf-xxx*|*sk-0d*) : ;;  # 占位符/已轮换示例
    *) report "疑似 API Key：$hit" ;;
  esac
done < <(git grep -nIE "sk-[A-Za-z0-9._-]{20,}" -- . 2>/dev/null | grep -v "docs/" | head -20 || true)

# 2) 私钥头
while IFS= read -r hit; do
  report "疑似私钥：$hit"
done < <(git grep -nI "BEGIN [A-Z ]*PRIVATE KEY" -- . 2>/dev/null | head -5 || true)

# 3) 明文密码赋值（排除脚本里的变量引用、占位符与测试值）
while IFS= read -r hit; do
  line=${hit#*:}
  case "$line" in
    *'${'*|*'"$'*|*'<redacted>'*|*'your-'*|*'***'*|*'password'*'='*'root'*|*admin123*|*reader123*|*'test'*\
    |*'form.'*|*'props.'*|*'process.env'*|*'undefined'*|*'payload'*|*'req.'*|*'this.'*|*'null'*) : ;;
    *) report "疑似明文密码：$hit" ;;
  esac
done < <(git grep -nIE "(password|passwd|secret)[[:space:]]*[:=][[:space:]]*[\"']?[A-Za-z0-9!@#%^&*._-]{10,}" -- . 2>/dev/null \
         | grep -vE "docs/|\.md:|test_|\.example" | head -20 || true)

if [ "$FOUND" = "1" ]; then
  echo
  echo "[secrets] 发现疑似凭据。处理建议："
  echo "  1) git rm --cached <file> 并加入 .gitignore"
  echo "  2) 轮换该凭据（见 docs/凭据轮换清单.md）"
  echo "  3) 历史提交里的凭据需要重写历史或视为已泄露"
  exit 1
fi
echo "[secrets] ✓ 未发现疑似密钥/明文凭据"
