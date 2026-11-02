"""pytest 全局配置：确保 `import app` 可用（两条验收命令都要能跑）。

- 在 ai-service/ 下执行 `python -m pytest -q`
- 在仓库根目录执行 `python -m pytest ai-service -q`
两种方式都通过本文件的 sys.path 注入找到 app 包。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

SERVICE_ROOT = Path(__file__).resolve().parent
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

# 测试永远使用 mock 模型，禁止依赖真实网络与 API Key
os.environ["LLM_PROVIDER"] = "mock"
os.environ.setdefault("AI_INTERNAL_TOKEN", "test-internal-token")
