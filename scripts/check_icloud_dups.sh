#!/usr/bin/env bash
# =====================================================================
# 检测 iCloud Drive 同步冲突副本（如 "BookMapper 2.java"）
#
# 背景：项目放在 ~/Desktop 时会被 iCloud 同步；多设备/并发写入会生成
#       "文件名 2.ext" 形式的冲突副本。Maven 会把这些副本当源码编译，
#       导致「类重复」「公共类文件名不符」等成片报错。
#
# 用法：bash scripts/check_icloud_dups.sh
#       有冲突副本时打印清单并返回 1（可接入 CI / pre-commit）
# =====================================================================
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

EXCLUDES='-not -path ./.git/* -not -path ./node_modules/* -not -path ./.venv/* -not -path */target/* -not -path */dist/*'
FOUND=$(find . \( -name '* [0-9]' -o -name '* [0-9].*' \) $EXCLUDES 2>/dev/null | sort)

if [ -z "$FOUND" ]; then
  echo "[dups] ✓ 未发现同步冲突副本"
  exit 0
fi

echo "[dups] ✗ 发现 $(echo "$FOUND" | wc -l | tr -d ' ') 个疑似 iCloud 冲突副本："
echo "$FOUND" | head -20 | sed 's/^/  /'
echo
echo "处理建议："
echo "  1) 确认原文件存在后删除副本（脚本只删「原文件存在且内容一致」的）"
echo "  2) 长期方案：把项目移出 iCloud 同步目录（如 ~/Projects），"
echo "     或关闭「桌面与文稿」同步（系统设置 → Apple ID → iCloud → 云盘）"
exit 1
