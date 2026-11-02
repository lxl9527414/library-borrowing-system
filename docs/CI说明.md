# CI 说明（W4-2）

> 目标：让「测试全绿」变成 GitHub Actions 上**可验证的门禁**，而不是口头承诺。
> 工作流文件：`.github/workflows/ci.yml`；CI 专用依赖子集：`.github/ci/ai-service-ci-requirements.txt`。

## 1. 触发条件与全局配置

| 项 | 配置 | 说明 |
|---|---|---|
| 触发 | `push`（main/master）、`pull_request`、`workflow_dispatch` | 手动可重跑 |
| 权限 | `permissions: contents: read` | 最小权限，CI 不需要写仓库/PR |
| 并发 | `concurrency: ci-<workflow>-<ref>` | 同分支重复触发时取消旧运行；PR 取消防抖，main 分支保留历史结果（`cancel-in-progress` 只在 PR 事件为 true） |
| 超时 | 每个 job `timeout-minutes: 15` | 防止依赖下载卡死白占 runner |
| 密钥 | **无任何 Secret** | 所有测试离线，见 §4；需要真实 Key 的 `scripts/eval_answers.py` 不接入 CI |

## 2. 五个并行 job

| job | 核心命令 | 覆盖 | 依赖服务 |
|---|---|---|---|
| `backend` | `cd backend && mvn -B clean test` | 71 个 Mockito 单测 | 无（纯单元测试，不需要 MySQL/Redis） |
| `ai-service` | `python -m pytest ai-service -q` | 319 个测试（全离线） | 无（mock 模型 + MockTransport + fakeredis） |
| `frontend` | `cd frontend && npm ci && npm run build` | Vue3 + Vite 生产构建 | 无 |
| `rag-eval` | `RAG_RETRIEVAL_MODE=bm25 RAG_RERANK_ENABLED=false python scripts/run_eval.py --min-recall 0.95` | 检索层 Recall@5 门禁 | 无（仓库内语料 + 本地 BM25） |

细节：

- **backend**：`actions/setup-java@v4` 显式 `temurin 17`（与 `pom.xml` 的 `java.version=17` 一致；本机默认 JDK 27，不能依赖本地版本），Maven 缓存基于 `backend/pom.xml`。
- **ai-service**：`actions/setup-python@v5` 固定 `3.11`，pip 缓存基于 CI 依赖子集文件；只装测试真正 import 的包（见 §5），不装 uvicorn / 本地 embedding 等重依赖。
- **frontend**：`actions/setup-node@v4` 固定 `20`（Vite 7 要求 Node ≥ 20.19），`npm ci` 严格按 `package-lock.json` 安装，`npm run build` 产物写入 `backend/src/main/resources/static/`（已被 `.gitignore` 忽略，CI 中无副作用）。
- **rag-eval**：只跑**检索层**评测（不需要 LLM 当裁判）。评测语料 `ai-service/data/corpus/knowledge.jsonl`（535 块）与评测集 `ai-service/eval/golden_qa.jsonl`（85 条）都在仓库里，BM25 索引从语料内存构建，**不依赖** `scripts/build_corpus.py`（要 MySQL）与 `data/embeddings/`（未入库）。报告写到 `$RUNNER_TEMP`，不污染工作区。

## 3. 本地复现（不需要 GitHub）

```bash
# ---------- job1 后端 ----------
cd backend
JAVA_HOME=$(/usr/libexec/java_home -v 17) mvn -B clean test   # macOS
# Linux: JAVA_HOME=/usr/lib/jvm/temurin-17-jdk-amd64 mvn -B clean test

# ---------- job2 AI 服务 ----------
cd ai-service
python3 -m venv .venv        # 已有 .venv 可跳过
.venv/bin/pip install -i https://mirrors.aliyun.com/pypi/simple/ \
  --trusted-host mirrors.aliyun.com -r ../.github/ci/ai-service-ci-requirements.txt
cd .. && ai-service/.venv/bin/python -m pytest ai-service -q

# ---------- job3 前端 ----------
cd frontend
npm ci --no-audit --no-fund
npm run build

# ---------- job4 RAG 检索门禁 ----------
cd ai-service
RAG_RETRIEVAL_MODE=bm25 RAG_RERANK_ENABLED=false \
  .venv/bin/python scripts/run_eval.py --min-recall 0.95 --out /tmp/eval_report_bm25.json
```

> 国内网络下本地安装用阿里云镜像；**CI 内使用官方 PyPI + pip 缓存**（GitHub runner 在海外，阿里云镜像反而慢，见任务清单 W4-2 的风险提示）。

## 4. 为什么 AI 服务测试能「全离线」

319 个测试不依赖网络、API Key、MySQL、Redis，靠三层替身：

| 外部依赖 | 替身 | 位置 |
|---|---|---|
| LLM（DeepSeek/通义/Ollama） | `MockChatModel`：按意图规则产出确定性的 tool_call / 流式文本 | `ai-service/app/llm/mock.py`、测试用 `ScriptedGateway` 注入错误分支 |
| Java 内部工具接口 | `httpx.MockTransport`：内置真实响应形状（含 `myReservation` 缺省、`data:null`、401/5xx） | `ai-service/tests/fakes.py` |
| Redis | `fakeredis` + 指向未监听端口的降级路径 | `ai-service/tests/test_redis_store.py`、`test_checkpointer.py` |
| Embedding / 向量库 | `FakeEmbeddingProvider` + 内存 `numpy` 向量库；BM25 纯本地 | `ai-service/app/rag/embedding.py`、`vector_store.py` |

因此 CI 不需要任何 Secret；需要真实 Key 的 `scripts/eval_answers.py`（答案层 LLM-as-judge）**刻意不接入**。

## 5. 依赖瘦身：为什么不装全量 `requirements.txt`

- `ai-service/requirements.txt` 是「本地/容器运行」的完整依赖（含 `uvicorn[standard]`、`langgraph-checkpoint-redis` 等），直接装到 CI 会引入测试用不到的包，拖慢流水线（目标：AI job < 5 分钟）；
- 本地 embedding 的 `torch` / `sentence-transformers` 在 `app/rag/embedding.py` 中是**可选懒加载**依赖（当前环境拉不到模型 → 自动降级 BM25），CI 更不应为它付安装成本；
- `langgraph-checkpoint-redis` 是生产可选的 Redis 检查点后端：缺失时 `app/agent/checkpointer.py` 自动降级 `InMemorySaver`，测试用假 saver 注入，不需要真实安装；
- **`numpy` 目前在 `requirements.txt` 中缺失**（`app/rag/*` 直接 `import numpy`），而测试必须用到 → 已在 CI 子集中显式补上。

取舍：CI 用 `.github/ci/ai-service-ci-requirements.txt` 这一份**显式 pin 的子集**，而不是 `requirements.txt`。

- 优点：依赖面稳定可控（以后有人往 `requirements.txt` 加模型依赖，CI 不会被拖慢）；安装量小、缓存命中后 ~10s 内跑完；
- 代价：需要人工同步版本；测试新增第三方 import 而 CI 未安装时会**直接失败**，属于预期的「漂移告警」；
- 维护约定：升级 `requirements.txt` 版本时同步改 CI 子集；长期建议改成「生产 `requirements.txt` + 测试 `requirements-dev.txt` + 重依赖 extras」，本次因写区限制只新增 `.github/` 与本文档，未改 `ai-service/requirements.txt`（见 §8 遗留）。

缓存策略：`setup-java`（Maven `~/.m2`）、`setup-python`（pip cache，key 为 CI 子集文件哈希）、`setup-node`（npm cache，key 为 `package-lock.json`）。首次运行冷缓存约 2–4 分钟，之后约 1 分钟。

## 6. 当前本地验证结果（与 CI 同命令）

| 检查 | 结果 |
|---|---|
| 后端 `mvn -B clean test`（JDK 17） | `Tests run: 71, Failures: 0, Errors: 0` / `BUILD SUCCESS`（11.7s，热 Maven 缓存） |
| AI 服务 `python -m pytest ai-service -q` | `319 passed in 8.55s` |
| 前端 `npm run build`（`npm ci` 已在干净树验证） | `✓ built in 5.03s` |
| RAG 门禁（BM25） | `Recall@5=0.9733  MRR=0.8593`，`--min-recall 0.95` 通过（exit 0） |

另外做了一次**干净检出模拟**：把仓库按 `.gitignore` 剔除 `.venv`、`node_modules`、`data/embeddings`、`backend/target`、`.env` 后复制到临时目录，用全新 venv 只装 CI 依赖子集逐个复跑——5 个 job 全部通过（`319 passed`、`71 tests`、前端构建成功、`Recall@5=0.9733`）。

## 7. 常见排查

- **首次 push 后**：GitHub 仓库 Actions 页签查看 5 个 job；本地命令与 CI 一一对应（§3），失败时先本地复现；
- **AI job 报 `ModuleNotFoundError`**：说明测试新增了第三方 import，把对应包 pin 进 `.github/ci/ai-service-ci-requirements.txt`；
- **RAG 门禁失败**：看日志里 `未召回样例` 与 `by_category` 表格；阈值与评测集在 `ai-service/scripts/run_eval.py`、`ai-service/eval/golden_qa.jsonl`；
- **前端 job 慢/失败**：`npm ci` 依赖 `package-lock.json`（不要手改）；`frontend/.npmrc` 用的是 npmmirror 镜像，lockfile 中多数包指向官方源，二者混用时以 lockfile 的 `resolved` 为准。

## 8. 已知限制与后续

1. **仓库还没有 remote**：workflow 目前只做了静态校验（YAML + 本地逐 job 复现），真实 Actions 运行结果需要 push 到 GitHub 后确认；README 徽章需要 remote URL 后再补（本次写区不含 README）；
2. CI 用 **Python 3.11**，本机验证用 3.10.9（本机无 3.11）——依赖 pin 与代码均兼容两者，但 3.11 下的真实运行留待首次 push 验证；
3. `rag-eval` 只覆盖**检索层**（Recall@5/MRR）；答案层 faithfulness 需要真实 Key，暂不入门禁；
4. CI 不启动 MySQL / Redis / 真实模型，覆盖的是单元测试与离线评测，不含端到端联调（e2e 脚本 `scripts/e2e_ai.py` 需本地服务，未接入）；
5. 建议后续：把 `numpy` 补进 `ai-service/requirements.txt`；将依赖拆成运行时/测试/可选 extras 三份；remote 建好后补 README 徽章。
