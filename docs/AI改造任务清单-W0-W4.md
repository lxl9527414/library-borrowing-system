# AI 原生改造 · 全量任务清单（W0–W4）

> **用途**：这是本项目从「传统 CRUD 系统」改造成「AI 应用开发工程师作品集项目」的全量任务台账。
> 每周开始时看「⬜ 待办」，每周结束前把完成的改成「✅ 已完成」并补上提交哈希与验证证据。
>
> **维护规则**：完成一个任务 → 改状态 → 在「证据」列写提交哈希 / 命令输出 / 文件路径。
> 状态变了但没证据 = 没完成。

| 项 | 值 |
|---|---|
| 最后更新 | 2026-11-02 |
| 仓库 | `springboot_vue_system`（云阁图书借阅系统 + AI 服务） |
| 当前进度 | **W0 / W1 / W2 / W3 全部完成；W4 完成（Langfuse 未真机验证）** |
| 已完成提交 | 9 个（`37a459c` → `a2e6f67`，工作区干净） |
| 测试基线 | 后端 71 个单测、AI 服务 **319** 个单测、端到端 16 项 + RAG 端到端 5 类断言、UI 18 项断言 |
| 阻塞项 | W3 开工前需定 embedding 路线（见 §9） |

**状态图例**：`✅ 已完成` / `🟡 进行中` / `⬜ 待办` / `⛔ 阻塞` / `💤 可选（有余量再做）`

---

## 1. 全局视图

```text
W0 基建 ──✅──▶ W1 链路打通 ──✅──▶ W2 可演示 ──✅──▶ W3 有智商(RAG) ──⬜──▶ W4 能投简历 ──⬜──▶ 投递
       (0.5d)        (2d)            (2.5d)              (5.5d)                 (5d)
```

| 周 | 主题 | 一句话目标 | 出口验收（不满足不算完成） | 状态 |
|---|---|---|---|---|
| **W0** | 工地平整 | 让项目具备「能被评估」的工程底子 | git 有提交历史、无密钥入库、缺的压测脚本补上、docker-compose 可校验 | ✅ 已完成 |
| **W1** | 链路打通 | 自然语言 → AI 服务 → 真实业务系统 | 一句「帮我借红楼梦」能走到确认卡，确认后真实落库 | ✅ 已完成 |
| **W2** | 可演示 | 浏览器里能点、能截图、能换会话 | 无头 Chrome 自动走查通过 + 6 张截图 + Redis 会话可跨重启 | ✅ 已完成 |
| **W3** | 有智商 | 知识问答带引用、能拒答、有评测数字 | 一份包含 recall / faithfulness / 延迟的 RAG 评测报告 | ✅ 已完成 |
| **W4** | 能投简历 | 可观测、CI、成本数据、文档与 Demo | 陌生人 10 分钟能跑起来 + 一份 3 分钟演示视频/GIF | ✅ 已完成（Langfuse 代码/测试完成但真机未验证，如实标注） |

**工作量口径**：W0–W2 实际投入约 1 个工作日（多 Agent 并行）+ 若干轮联调修复；
W3 预计 5.5 人日、W4 预计 5 人日（按每周 15–20 小时 ≈ 2.5 周）。若时间收紧，优先砍 §8 的可选项，**不要砍评测（W3-6）和文档（W4-7）**。

---

## 2. W0 · 工地平整（✅ 全部完成）

### W0-1 git 初始化与基线提交
- **状态**：✅ 已完成
- **目标**：让"演进过程"可见——作品集最忌讳 0 提交历史
- **交付物**：`.git` 初始化；基线提交导入原有 120 个文件
- **验收**：`git log` 有基线提交；工作区干净
- **证据**：`37a459c chore: 导入图书借阅系统基线（... 42 单测）`
- **备注**：提交作者是占位身份 `Library Dev <dev@localhost>`，投递前需改成你自己的名字/邮箱并 `git rebase --root --exec 'git commit --amend --reset-author --no-edit'`

### W0-2 密钥与忽略规则治理
- **状态**：✅ 已完成
- **目标**：任何密钥、构建产物、本地数据绝不进仓库
- **交付物**：`.env.example`（全部环境变量模板）；`.gitignore` 增量规则（`.env*`、`ai-service/.venv/`、`__pycache__/`、`.dev/`、`frontend/dist/`）
- **验收**：`git ls-files | grep -E "\.env$|credentials"` 无输出；`docker compose config -q` 通过
- **证据**：`.env.example`、`.gitignore`；已确认 `.env` 未被跟踪

### W0-3 容器编排
- **状态**：✅ 已完成
- **目标**：一条命令起 MySQL / Redis / pgvector / AI 服务
- **交付物**：`docker-compose.yml`（含 `DOCKER_MIRROR` 镜像前缀覆盖、健康检查、`REDIS_IMAGE` 备选说明）
- **验收**：`docker compose config -q` 通过；服务定义齐全
- **证据**：`docker-compose.yml`；`b53b45b`（补充 Redis Stack 说明）
- **风险**：本机 Docker Hub 不可达 + 钥匙串报错（`Keychain Error -67674`），实际验证改用本机隔离 MySQL；文档已给镜像站替代方案

### W0-4 补齐缺失的压测脚本
- **状态**：✅ 已完成
- **目标**：修掉「文档引用了但文件不存在」的硬伤
- **交付物**：`docs/loadtest.py`（纯标准库、固定并发模型、nearest-rank 分位数、`--header` 自定义请求头、失败原因采集、`--json` 输出）
- **验收**：与 `docs/压测报告.md` 中的命令行完全兼容；对真实后端可跑
- **证据**：实测 `/api/ai/internal/context` 并发 10 → 2573 QPS / P95 6.8ms / 0 失败

### W0-5 一键开发脚本
- **状态**：✅ 已完成
- **目标**：把十几个环境变量和启动步骤收敛成两条命令
- **交付物**：`scripts/dev_up.sh`、`scripts/dev_down.sh`
- **验收**：冷启动一次成功；幂等（重复执行复用已运行组件）；停止后端口全部释放
- **证据**：冷启动 7 秒；`b53b45b` 修复隔离 MySQL 监听 `*:3308` 的问题（改为 `127.0.0.1`）
- **备注**：**不要**用 `run_in_background` 方式启动（进程组会被回收导致服务消失）

---

## 3. W1 · 链路打通（✅ 全部完成）

### W1-1 接口契约文档
- **状态**：✅ 已完成
- **目标**：Java 与 Python 两侧并行开发的唯一依据
- **交付物**：`docs/AI-架构与接口契约.md`（架构图、8 个内部端点、6 个 LLM 工具、SSE 事件协议、错误码、确认流程、目录归属）
- **验收**：两侧实现与本文件零偏差（偏差记入 §9）
- **证据**：契约文档 §1–§8；实测差异补入 §9

### W1-2 Java 内部工具 API
- **状态**：✅ 已完成
- **目标**：让 AI 能安全地"动"业务数据
- **交付物**：`backend/src/main/java/com/library/ai/`（8 端点 + `AiInternalTokenFilter` + 7 个 VO + 3 个 DTO）；新增 29 个单测
- **验收**：`mvn -B clean test` → 71 passed（原有 42 + 新增 29）
- **证据**：`5f4bc64`；实测 403（缺内部密钥）/ 401（缺 token）/ 借还落库 / 伪造 userId 被忽略
- **关键设计**：用户身份只从 `Authorization` 解析（DTO 里没有 userId）；写操作复用现有 `@Transactional` 服务，零业务逻辑复制

### W1-3 Python AI 服务
- **状态**：✅ 已完成
- **目标**：Agent 编排 + 流式输出 + 工具调用 + 模型网关
- **交付物**：`ai-service/`（`app/{llm,tools,agent,api,memory,obs}`；LangGraph 状态机；mock 模型；OpenAI 兼容网关；结构化日志）
- **验收**：`pytest` 91 passed（离线、无需 Key）；`/api/ai/health` 正常
- **证据**：`fb2ad0e`；契约 §9 实测

### W1-4 人在回路确认
- **状态**：✅ 已完成
- **目标**：写操作必须用户点确认才执行
- **交付物**：`authorize` 节点 + `interrupt` + `/api/ai/chat/confirm`；确认状态带 TTL、一次性消费
- **验收**：确认前查库无新增；确认后 +1；拒绝后不执行且自然回复
- **证据**：`scripts/e2e_ai.py` 16 项断言中 6 项专测此事

### W1-5 端到端验证脚本
- **状态**：✅ 已完成
- **交付物**：`scripts/e2e_ai.py`（身份透传 / 人在回路 / 真实落库，16 项断言）
- **验收**：连续两次执行均全绿（脚本自清理，见 W2-6）
- **证据**：`✅ 全部断言通过：身份透传 / 人在回路 / 真实落库 三条命题均成立`

### W1-6 压测脚本增强（顺带）
- **状态**：✅ 已完成
- **交付物**：`docs/loadtest.py --header`、失败原因采集
- **理由**：AI 内部接口需要 `X-Internal-Token`，否则脚本无法压测

---

## 4. W2 · 可演示（✅ 全部完成）

### W2-1 前端流式对话页
- **状态**：✅ 已完成
- **交付物**：`frontend/src/views/ChatView.vue`(863) + `components/ai/{ChatMessage,ConfirmCard,ToolTraceItem,AiMarkdown,ToolMeta}` + `frontend/src/api/ai.js`、`frontend/src/api/sse.js`
- **验收**：`npm run build` 通过（ChatView chunk 122 kB）；真实浏览器走查 0 控制台错误；390×844 无横向溢出
- **证据**：`d6306a1`；截图 `docs/screenshots/01–04`

### W2-2 会话与 checkpointer 迁移 Redis
- **状态**：✅ 已完成
- **交付物**：`ai-service/app/memory/redis_store.py`、`ai-service/app/agent/checkpointer.py`、`ai-service/scripts/verify_redis_persistence.py`；health 暴露 `sessionStore` / `checkpointer`
- **验收**：111 passed；重启后会话历史仍在；Redis 不可用自动降级不阻断启动
- **证据**：`d6306a1`；`ai:session:*` TTL 3600s
- **遗留**：本机 Redis 缺 RedisJSON/RediSearch，`AsyncRedisSaver` 自动降级为内存（`checkpointer=memory`）；换 Redis Stack 即启用

### W2-3 修复生产构建下 `/chat` 404
- **状态**：✅ 已完成
- **根因**：`SpaController` 是硬编码 SPA 路由白名单；Vite dev 有 fallback 所以本地发现不了
- **验收**：`mvn clean test` 71 passed；`curl :18080/chat → 200`
- **证据**：`d6306a1`

### W2-4 生产部署配套
- **状态**：✅ 已完成
- **交付物**：`deploy/nginx/library.conf` 新增 `/api/ai/`（`proxy_buffering off` + `X-Accel-Buffering: no` + 300s）与 `/api/ai/internal/ { return 404; }`
- **验收**：配置语法合法；内部接口公网不可达
- **证据**：`d6306a1`

### W2-5 界面截图工具
- **状态**：✅ 已完成
- **交付物**：`scripts/ui_screenshot.mjs`（自写 CDP，无 Playwright 依赖；自动登录 → 提问 → 确认 → 截图 → 回归检查 → 自动归还）
- **验收**：一条命令产出 6 张关键状态图；可反复执行
- **证据**：`docs/screenshots/01-welcome…06-category-query.png`
- **备注**：无头 Chrome 里 `Target.createTarget + attachToTarget` 会 `Inspector.targetCrashed`，改用 per-target WebSocket

### W2-6 修复「新对话」体验缺陷
- **状态**：✅ 已完成
- **根因**：① `canReset` 依赖"存在用户消息"→ 点一次就变灰；② `resetChat` 每次 `createWelcome()` → 开场白重复
- **验收**：18 项 UI 断言全过（连点 3 次、流式中点、重置后再聊）
- **证据**：`0ad0fd4`；`docs/screenshots/05-new-chat.png`

### W2-7 Mock 模型增强与局限说明
- **状态**：✅ 已完成
- **交付物**：13 个分类别名 + 主题词抽取（「有X类的书吗」→ 按 category 或 keyword 检索）；兜底回复不再复读开场白；徽标 tooltip 说明 mock 局限
- **验收**：`有地理类的书吗`→6 本、`有文学类的书吗`→101 本、`有计算机类的书吗`→55 本；111 passed
- **证据**：`a2e6f67`；`docs/screenshots/06-category-query.png`

### W2-8 真实模型联调脚手架
- **状态**：✅ 已完成（2026-11-02 已用真实 DeepSeek 跑通）
- **交付物**：`scripts/check_llm.py`（连通性 / function calling 兼容性 / 首 token 延迟 / 输出速度）
- **验收**：mock 与"缺 Key"两种状态均给出清晰中文提示
- **实测**：DeepSeek 连通性 ✅ / function calling ✅（正确产出 `search_books({"keyword":"三体"})`）/ TTFT **521 ms** / 输出 **47.3 字/秒**
- **注意**：Key 变量名必须是 `DEEPSEEK_API_KEY`（写成通用 `API_KEY=` 不会被读取）

---

## 5. W3 · 有智商：RAG 知识问答（⬜ 待办）

> **本周目标**：让 AI 能回答"逾期罚金怎么算""有没有讲清朝历史的书""这本书适合几年级读"这类知识型问题，
> 答案带可点击引用、不知道时明确拒答，并产出一份**有数字**的评测报告。
>
> **开工前必须先定**：embedding 路线（§9 决策项 1），否则 W3-2/W3-4 无法选型。

### W3-1 知识语料构建与切分
- **状态**：✅ 已完成（2026-11-02）　**预估**：0.5 人日　**依赖**：无
- **目标**：把"能查到的结构化数据"变成"能语义检索的文档"
- **交付物**
  - `ai-service/scripts/build_corpus.py`：从 MySQL 抽取 500 本书（书名/作者/分类/简介/库存）+ 借阅规则 + 30 条 FAQ，生成统一 JSONL
  - 切分策略：书籍按"书名+作者+分类+简介"整段（短文本不切）；规则/FAQ 按语义段落切，单块 ≤ 400 字
  - 每块带元数据：`source_type`(book/rule/faq)、`book_id`、`title`、`category`、`url`（前端跳转用）
- **验收**：语料条数可统计（≈500 + 规则若干 + 30）；随机抽 10 条人工检查切片无断句、元数据齐全
- **证据**：`ai-service/scripts/build_corpus.py`；实跑 **book 500 / rule 5 / faq 30 = 535 块**（204 KB），连续两次构建 sha256 一致
- **涉及文件**：`ai-service/scripts/build_corpus.py`、`ai-service/data/corpus/*.jsonl`（是否入库需定，建议入库小样本 + 生成脚本）
- **风险**：500 本书简介较短，可能不适合切分 → 以"不切"为默认

### W3-2 embedding 管线与 pgvector 入库
- **状态**：🟡 进行中（接口与真实索引已完成；完整评测受通义额度限制）　**预估**：1 人日　**依赖**：W3-1 + §9 决策项 1
- **目标**：向量化 + 建索引，支持按相似度召回
- **交付物**
  - `ai-service/app/rag/embedding.py`：embedding 抽象（通义 `text-embedding-v3` / 本地 `bge-m3` 二选一，可切换）
  - `ai-service/app/rag/store.py`：pgvector 建表（`id, content, embedding vector(N), metadata jsonb`）、HNSW 索引、批量 upsert（幂等，按 `source_type + source_id + chunk_no` 去重）
  - `ai-service/scripts/index_corpus.py`：分批（每批 ≤ 20）embedding + 入库 + 进度日志 + 失败重试
- **验收**：`docker compose up -d pgvector` 后一条命令完成建表+入库；重复执行不产生重复行；查询 `SELECT count(*)` 与语料条数一致
- **已交付（2026-11-02）**：`app/rag/embedding.py`（dashscope/local/fake + auto 工厂）、`app/rag/vector_store.py`（NumpyVectorStore + PgVectorStore 降级）、`scripts/index_corpus.py`
- **实测**：535×1024 索引，18.2 s / 54 批 / 34 ms 每条；幂等（二次执行跳过，`--force` 重建逐位一致）
- **涉及文件**：`ai-service/app/rag/*`、`scripts/`、`docker-compose.yml`（已含 pgvector 服务）
- **阻塞**：通义账号欠费（`Arrearage`），无法复跑需要 embedding 的评测
- **风险**：embedding 维度与模型必须对应（`text-embedding-v3` 1024 / `bge-m3` 1024）；换模型需重建索引 → 写入 README

### W3-3 混合检索（向量 + 关键词）
- **状态**：🟡 进行中（**BM25 基线档已完成**，向量 + RRF 融合待 W3-2）　**预估**：0.5 人日　**依赖**：W3-2
- **已交付**：`ai-service/app/rag/{tokenizer,bm25,corpus,service}.py`；零依赖中文 unigram+bigram 分词；BM25 k1=1.5/b=0.75 + 非负 IDF；语料缺失降级为空结果不抛异常
- **基线实测**：Recall@5 **0.973** / MRR **0.859**（book 1.000/0.951、faq 1.000/1.000、rule 0.867/0.394），详见 `docs/RAG评测报告.md`
- **第二轮（2026-11-02）**：混合检索（BM25 + 通义向量 + 等权 RRF）已实现 → Recall@5 **1.000** / MRR **0.883**（rule R@5 1.000、MRR 0.450）；**发现等权 RRF 不如纯向量（0.916）**，已记为待办（加权 RRF 或向量+重排）
- **目标**：解决"书名精确词用向量召回会翻车"的问题
- **交付物**：`ai-service/app/rag/retriever.py`
  - 向量检索 top-20（余弦）
  - 关键词检索 top-20（Postgres 全文检索 `to_tsvector('simple', ...)` 或 `ILIKE` 兜底）
  - RRF 融合（`score = Σ 1/(k + rank)`，k=60）→ 取 top-20 候选
- **验收**：对 5 个构造用例（精确书名/分类/规则/FAQ/生僻问法）能召回目标块；输出候选数与耗时日志
- **风险**：中文全文检索需 `zhparser` 或退回 `ILIKE` → 先用 `ILIKE` 保证可跑，写清局限

### W3-4 重排（Reranker）
- **状态**：✅ 已完成（2026-11-02）　**预估**：0.5 人日　**依赖**：W3-3
- **目标**：20 候选 → 5 精排，这是"会调 API"和"懂 RAG"的分水岭
- **交付物**：`ai-service/app/rag/reranker.py`
  - 方案 A：本地 `bge-reranker-v2-m3`（CPU、约 2GB，sentence-transformers）
  - 方案 B：通义 `gte-rerank`（零下载、按量计费）
  - 可配置开关（`RAG_RERANK_ENABLED`），默认开
- **验收**：同一 query 加/不加 rerank，目标块排名提升可观测（记录 before/after rank 日志）
- **已交付**：`app/rag/rerank.py`（通义 `gte-rerank-v2` 首选 + DeepSeek listwise 可选 + Noop + 失败回退）、42 个离线单测
- **实测**：单次重排 ~240 ms；「逾期罚金怎么算」把规则块从最后一位提到第一；BM25+重排 MRR 0.859→**0.884**（book MRR→1.000、rule MRR 0.394→0.422）
- **风险**：本地模型首次加载慢 → 当前走 API，不受影响

### W3-5 RAG 工具接入 Agent
- **状态**：✅ 已完成（2026-11-02）　**预估**：1 人日　**依赖**：W3-4
- **目标**：知识问答成为 Agent 的第 7 个工具（只读）
- **交付物**
  - `ai-service/app/tools/schemas.py` 新增 `search_knowledge(query)` 工具（只读，无需确认）
  - `ai-service/app/tools/executor.py` 接入 retriever + reranker，返回 `{chunks: [{text, source_type, title, book_id, score, citation_no}]}`
  - `ai-service/app/agent/prompts.py`：系统提示词加入引用规则（必须带 `[1]` 引用；证据不足时明确拒答；不得编造馆藏与规则）
  - 拒答策略：top1 分数 < 阈值 或 无候选 → 回复"馆藏资料里没有找到依据，要不要我帮你检索馆藏图书？"
- **验收**：`scripts/e2e_rag.py`（新增）覆盖 6 类问题：规则类 / 馆藏类 / FAQ 类 / 需拒答类 / 需混合工具类（先查知识再查库存）/ 引用格式
- **已交付**：`search_knowledge` 工具（schema/executor/阈值 `RAG_TOOL_MIN_SCORE`）+ 5 条引用拒答提示词 + 31 个离线测试
- **实测**：真实 DeepSeek 下 rule/faq 走知识库并带 `[1][3]` 引用；馆藏与库存类正确改用 `search_books`；WiFi 类明确拒答；单次知识问答 2426 ms / 4578→235 tokens
- **风险**：阈值 0.67 会误伤 4/85 低分有效问题（已记录）

### W3-6 评测集与评测报告 ⭐本周最高价值
- **状态**：✅ 已完成（2026-11-02，检索层 + 答案层）　**预估**：1.5 人日　**依赖**：W3-5
- **已交付**：`ai-service/scripts/gen_golden_qa.py`（85 条：book 30 / faq 30 / rule 15 / refuse 10）、`ai-service/scripts/run_eval.py`（Recall@k / MRR / 拒答负样本分数分布，支持 `--min-recall` 作 CI 门禁）、`docs/RAG评测报告.md` v0.1
- **基线数字**：总体 Recall@5 0.973 / MRR 0.859；**rule 类 MRR 仅 0.394**（被同形 FAQ 抢位）
- **第二轮结果（v0.3 已完成全部四档）**：纯向量 **MRR 0.916**（最优）> 向量+重排 0.902 > 混合+重排 0.889 = BM25+重排 0.889 > 混合 0.883 > BM25 0.859；向量召回使 rule R@5 0.867→1.000
- **已修复缺陷**：重排此前只看到 top-5 候选（无效），改为先取 20 候选再重排
- **已知问题**：rule/FAQ 多源等价使评分口径偏严；重排约 8% 调用失败需重试；等权 RRF 不如纯向量
- **目标**：把"效果不错"变成可复核的数字，这是简历差异化的根
- **交付物**
  - `ai-service/eval/golden_qa.jsonl`：60–80 条（规则 20 / 馆藏 25 / FAQ 15 / 应拒答 10），带 `question / ground_truth / expected_source_ids`
  - `ai-service/eval/run_eval.py`：跑全量问答 → 采集答案、引用、延迟 → 调 RAGAS 计算 4 指标
  - `ai-service/eval/ragas_config.py`：LLM-as-judge 用同一个模型（避免额外依赖）
  - `docs/RAG评测报告.md`：**三档对比**（纯向量 / 向量+关键词 / +rerank）× 4 指标 + P95 延迟 + 失败样例分析
- **验收**：报告里每个数字都有复现命令；至少指出 3 个 badcase 及原因；引用准确率（引用块确实支持答案）≥ 0.85
- **指标口径**：context_recall / context_precision / faithfulness / answer_relevancy（RAGAS 标准）
- **风险**：golden set 标注耗时 → 用现有 500 本书 + 规则自动生成草稿，人工抽检 20 条修正

### W3-7 前端引用卡片
- **状态**：✅ 已完成（2026-11-02）　**预估**：0.5 人日　**依赖**：W3-5
- **交付物**：`frontend/src/components/ai/ChatMessage.vue` 增加"引用来源"区：`[1]《三体》· 馆藏`（点击跳 `/books?id=1` 或规则页）；无引用时显示"本条回答无馆藏依据"
- **验收**：UI 走查（CDP）通过 + 截图入库；移动端不溢出
- **证据**：`docs/screenshots/07-rag-citation.png`（真实模型+真实检索）、`08-rag-refusal.png`；41 项纯逻辑单测 + 40 项浏览器 stub 冒烟；移动端 390×844 无溢出

**W3 出口验收**：`docs/RAG评测报告.md` 有三个档次的真实数字 + 引用截图 + `scripts/e2e_rag.py` 全绿。

---

## 6. W4 · 能投简历：工程闭环与作品集化（⬜ 待办）

> **本周目标**：让一个陌生人（面试官）**10 分钟能跑起来**、看得见数据、看得出工程能力。

### W4-1 Langfuse 全链路追踪
- **状态**：🟡 进行中（代码已接入并测试；本机无法起 Langfuse，未做真机验证）　**预估**：1 人日　**依赖**：W3 完成（有 RAG 才有意思）
- **交付物**：`docker-compose.yml` 启用 Langfuse；`ai-service/app/obs/langfuse.py` 接入 trace（每次请求：prompt、工具调用、检索块、token、成本、延迟）；看板截图
- **验收**：Langfuse 里能看到一次完整对话的 span 树（LLM → tool → LLM）；截图入库
- **证据目标**：`docs/screenshots/08-langfuse-trace.png`
- **风险**：Langfuse 镜像较大 + 需要 Postgres → 复用 pgvector 实例或单独库

### W4-2 CI 流水线
- **状态**：✅ 已完成（本地验证；真实 Actions 需 push 后确认）　**预估**：0.5 人日　**依赖**：需要把仓库推到 GitHub
- **交付物**：`.github/workflows/ci.yml`
  - job1：后端 `mvn -B clean test`（JDK 17，Maven 缓存）
  - job2：AI 服务 `pytest`（Python 3.11，pip 缓存，mock 模型）
  - job3：前端 `npm ci && npm run build`
  - job4（可选）：RAG 评测阈值门禁（faithfulness ≥ 阈值才通过）
- **验收**：一次 push 触发全绿；徽章贴进 README
- **风险**：GitHub Actions 拉取 aliyun 镜像/pip 源可能慢 → 用官方源 + 缓存

### W4-3 成本与性能报告
- **状态**：✅ 已完成（2026-11-02）　**预估**：0.5 人日　**依赖**：真实模型配置（§9 决策项 2）
- **交付物**：`docs/AI性能与成本报告.md`
  - 首 token 延迟 P50/P95（`scripts/check_llm.py` + 100 次采样）
  - 单次对话平均 token 与成本（按 DeepSeek/Qwen 定价）
  - 每千次对话预估成本；语义缓存命中率与降本比例（若实现缓存）
  - 与 `docs/压测报告.md` 同样的"方法论 + 局限说明"风格
- **验收**：数字可复现；明确写出"测的是什么、没测什么"

### W4-4 MCP Server 封装
- **状态**：✅ 已完成（2026-11-02，selftest 验证）　**预估**：0.5 人日　**依赖**：W1-2
- **交付物**：`backend` 或 `ai-service` 侧 MCP Server（stdio/sse），把 6 个工具暴露为标准 MCP tools；`docs/MCP集成说明.md`
- **验收**：MCP Inspector 或简单 client 能列出 6 个 tools 并成功调用 `search_books`
- **价值**：2026 高频新词，且是对已有工具的薄封装，投入产出比高

### W4-5 凭据治理与轮换
- **状态**：✅ 已完成（2026-11-02）　**预估**：0.5 人日　**依赖**：无（越早越好，见 §9 决策项 4）
- **交付物**
  - `deploy/credentials.local` → 改为部署时注入（`.env` / systemd `EnvironmentFile`）
  - `docs/凭据轮换清单.md`：需轮换的账号、改法、改完要同步的位置
- **验收**：仓库与部署目录中不再有明文密码文件；`git ls-files` 无凭据

### W4-6 前端收尾
- **状态**：✅ 已完成（2026-11-02）　**预估**：0.5 人日　**依赖**：W3-7
- **交付物**
  - `frontend/src/layout/MainLayout.vue` 菜单改为**由路由 meta 生成**（目前是硬编码 `allItems`，新增页面要改两处）
  - 真实模型模式下的截图替换 mock 截图
  - 移动端（390×844）复查：输入框、确认卡、引用卡
  - 加载态/空态/错误态统一
- **验收**：CDP 走查 18+ 项断言通过；截图更新

### W4-7 README 与文档作品集化 ⭐投递门面
- **状态**：✅ 已完成（2026-11-02）　**预估**：1 人日　**依赖**：W3、W4-1/3/6
- **交付物**
  - README：一句话定位 + 架构图（Mermaid/图片）+ 60 秒 Demo GIF + 关键数字（测试数、评测指标、延迟、成本）+ 快速启动
  - `docs/ADR.md`：4–6 条技术决策记录（为什么 Java+Python 双栈、为什么 mock、为什么 pgvector、为什么人在回路）
  - 简历 bullet 3–4 条（含量化数字）
  - `docs/面试问答.md`：20 个高频追问及回答要点（为什么不用 Dify / 权限怎么保证 / 幻觉怎么控 / 模型挂了怎么办 / 成本怎么控 / 怎么评测）
- **验收**：找一位非项目成员按 README 从零跑通（记录卡点并修复）

### W4-8 演示物料
- **状态**：✅ 已完成（2026-11-02，GIF 1.4MB）　**预估**：0.5 人日　**依赖**：W4-6
- **交付物**
  - `scripts/demo_flow.mjs`：一条命令跑完整演示（登录→问书→借书确认→还书→RAG 问答→引用卡），产出 GIF 帧或成片
  - 3 分钟演示脚本（口播稿：背景 30s / 架构 30s / 现场演示 90s / 工程亮点 30s）
- **验收**：录制一遍无冷场；GIF ≤ 5MB 放 README

### 💤 W4 可选（有余量再做，不进主线）
- 个性化推荐（借阅序列 embedding + 协同过滤召回 + LLM 重排 + 理由）
- Text2SQL（管理员自然语言查统计，只读白名单 + 注入防护）
- 语义缓存（Redis + 向量相似度，降本数字进报告）
- K8s 部署清单 / LoRA 微调小模型做意图分类

---

## 7. 风险登记表

| # | 风险 | 概率 | 影响 | 预案 |
|---|---|---|---|---|
| R1 | embedding 未定/Key 未配，W3 停摆 | 高 | 高 | 开工前先定路线（§9）；本地 `bge-m3` 可离线兜底 |
| R2 | RAG 评测集标注超时 | 中 | 中 | 自动生成草稿 + 只人工抽检 20 条 |
| R3 | 换 embedding 模型导致索引失效 | 中 | 中 | 维度/模型写入配置与文档，建索引脚本幂等重建 |
| R4 | 真实模型不稳定/限流 | 中 | 高 | 网关已有降级链 + 重试；演示前预热与兜底 mock 截图 |
| R5 | 本机 Docker Hub 不可达 | 高 | 中 | `DOCKER_MIRROR` 覆盖；或本机隔离 MySQL + 本机 Redis（已验证可行） |
| R6 | Redis 缺 RedisJSON/RediSearch → checkpointer 降级 | 高（现状） | 低 | 已降级可用；换 Redis Stack 即启用，文档已写 |
| R7 | 凭据已在会话中出现，存在泄露风险 | 高（已发生） | 高 | 尽快轮换线上密码（§9 决策项 4） |
| R8 | 4 周排期被压缩 | 中 | 中 | 按 §5/§6 优先级砍：先砍可选项，保评测与文档 |
| R9 | 演示时服务起不来 | 低 | 高 | `scripts/dev_up.sh` + 预演清单；演示前跑一遍 `scripts/e2e_ai.py` 与 UI 截图 |

---

## 8. 优先级与砍单顺序

**必须保住（简历含金量所在）**：W3-5、W3-6、W4-7、W4-3
**可以压缩**：W3-4（先用 API rerank 不部署本地模型）、W4-1（只接 trace 不做全看板）、W4-8（只做 GIF 不做视频）
**可以砍**：W4-4（MCP）、W4-2 的 RAG 阈值门禁、§6 的可选项

---

## 9. 待决策项（会阻塞后续任务）

| # | 决策 | 选项 | 阻塞 | 建议 |
|---|---|---|---|---|
| 1 | **embedding 路线** | A. 通义 `text-embedding-v3`（需 `DASHSCOPE_API_KEY`）<br>B. 本地 `bge-m3`（~2GB，离线）<br>C. A + 本地 `bge-reranker` 重排 | W3-2、W3-4 | **C**（最能讲清召回-重排链路，面试加分） |
| 2 | **真实模型 Key** | ✅ **已完成**（DeepSeek 已跑通：function calling + TTFT 521ms） | — | 已解决 |
| 2b | **embedding 来源**（拆分自原决策 1） | A. 补 `DASHSCOPE_API_KEY`（通义 text-embedding-v3，最快）<br>B. 本地 `bge-small-zh`（torch+sentence-transformers+ModelScope 下载，离线可跑） | W3-2 语义检索 | DeepSeek **没有** embedding 接口；需二选一 |
| 3 | **是否换 Redis Stack** | 换则 checkpointer 跨重启真持久化 | W4-1 多实例演示 | 演示前换；本地已能自动降级 |
| 4 | **线上密码轮换** | 管理员/读者/MySQL/Redis 全部重置 | 安全（已发生泄露） | 尽快，改完同步 `deploy/` 配置 |
| 5 | **仓库推到 GitHub** | 需要 remote + Actions | W4-2 CI、投递 | 投递前必须做（作品集要有链接） |

---

## 10. 验收命令速查

```bash
# 启动/停止全套（隔离 MySQL，不碰本机 3306）
bash scripts/dev_up.sh
bash scripts/dev_down.sh

# 后端单测（71）
cd backend && JAVA_HOME=$(/usr/libexec/java_home -v 17) mvn -B clean test

# AI 服务单测（111，离线）
ai-service/.venv/bin/python -m pytest ai-service -q

# 端到端（16 项断言：身份透传 / 人在回路 / 真实落库）
ai-service/.venv/bin/python scripts/e2e_ai.py

# 会话跨重启持久化
ai-service/.venv/bin/python ai-service/scripts/verify_redis_persistence.py

# 界面截图 + 「新对话」回归检查
node scripts/ui_screenshot.mjs

# RAG 语料构建（离线，直连 MySQL 构建；幂等）
cd ai-service && .venv/bin/python scripts/build_corpus.py

# RAG 评测集生成 + 检索评测（离线，无需 Key；--min-recall 可作 CI 门禁）
cd ai-service && .venv/bin/python scripts/gen_golden_qa.py
cd ai-service && .venv/bin/python scripts/run_eval.py --min-recall 0.95

# RAG 向量索引（通义 embedding，幂等）
cd ai-service && .venv/bin/python scripts/index_corpus.py --provider dashscope

# RAG 四档评测（串行跑，勿并行；需要通义额度）
cd ai-service && RAG_RETRIEVAL_MODE=hybrid RAG_RERANK_ENABLED=true .venv/bin/python scripts/run_eval.py

# 真实模型兼容性（填 Key 后）
ai-service/.venv/bin/python scripts/check_llm.py

# 只读压测
python3 docs/loadtest.py --token <token> --paths "/api/books?current=1&size=10" --levels 10,25 --duration 15

# 前端构建（产物进 backend static）
cd frontend && npm run build
```

---

## 11. 进度记录

| 日期 | 里程碑 | 提交 / 证据 |
|---|---|---|
| 2026-11-01 | W0 完成 | `37a459c`、`5f4bc64`、`62ccf7b` |
| 2026-11-01 | W1 完成（后端 71 单测 / AI 111 单测 / E2E 16 断言） | `5f4bc64`、`fb2ad0e` |
| 2026-11-01 | W2 完成（6 张截图 / Redis 会话 / 三处缺陷修复） | `d6306a1`、`0ad0fd4`、`a2e6f67` |
| 2026-11-01 | 安全修复：隔离库收敛到回环地址 | `b53b45b` |
| 2026-11-02 | 本文档创建 | — |
| 2026-11-02 | W3-1 完成（535 块语料，幂等） | `ai-service/data/corpus/knowledge.jsonl` |
| 2026-11-02 | W3-3 BM25 基线完成（Recall@5 0.973 / MRR 0.859） | `docs/RAG评测报告.md` |
| 2026-11-02 | W3-6 评测集与检索评测完成（85 条用例） | `ai-service/eval/` |
| 2026-11-02 | **真实模型联调完成**（DeepSeek：function calling + E2E 全绿 + TTFT 521ms） | `scripts/check_llm.py`、`scripts/e2e_ai.py` |
| 2026-11-02 | 发现 Key 命名问题并修正（`API_KEY` → `DEEPSEEK_API_KEY`） | `.env`（未入库） |
| 2026-11-02 | W3-2 通义 embedding 索引完成（535×1024，18.2s） | `scripts/index_corpus.py` |
| 2026-11-02 | W3-3 混合检索完成（R@5 1.000 / MRR 0.883） | `app/rag/service.py` |
| 2026-11-02 | W3-4 重排完成（通义 gte-rerank-v2，240ms） | `app/rag/rerank.py` |
| 2026-11-02 | ⚠️ 通义账号欠费，剩余两档评测阻塞 | `docs/RAG评测报告.md` §10.3 |
| 2026-11-02 | 重排候选池缺陷修复（top-5→20），六档评测完成 | `f279b7e` |
| 2026-11-02 | W3-5 完成：search_knowledge 接入 + 引用/拒答（319 测试全绿） | `docs/RAG评测报告.md` §12 |
| 2026-11-02 | W3-7 完成：前端引用卡片 + [n] 联动 + 真实截图 | `docs/screenshots/07,08` |
| 2026-11-02 | W3-6 完成：答案层 faithfulness 0.94 / 引用准确率 0.913 / 拒答 9-10 | `docs/RAG评测报告.md` §13 |
| 2026-11-02 | **W3 全部完成** | — |
| 2026-11-02 | W4-2 CI 完成（4 job + 干净检出模拟验证） | `.github/workflows/ci.yml`、`docs/CI说明.md` |
| 2026-11-02 | W4-3 成本与性能报告完成（TTFT P50 897ms / 84 字每秒 / 单轮 ¥0.0094） | `docs/AI性能与成本报告.md` |
| 2026-11-02 | W4-6 前端收尾完成（菜单路由驱动 + 移动端 2 处溢出修复） | `frontend/src/layout/MainLayout.vue` 等 |
| 2026-11-02 | W4-7 部分完成：README 重写为作品集风格 + ADR（8 条决策） | `README.md`、`docs/ADR.md` |
| 2026-11-02 | 真模型重拍截图（徽标显示 deepseek）+ 确认卡片书名回填修复 | `docs/screenshots/01-05` |
| 2026-11-02 | W4-7 完成：简历素材 + 20 条面试问答 + README 作品集化 + ADR | `docs/简历素材.md`、`docs/面试问答.md`、`docs/ADR.md` |
| 2026-11-02 | W4-8 完成：`scripts/demo.sh` 一键演示 + CDP 录屏 + Pillow 合成 GIF | `docs/demo.gif`（1.4 MB） |
| 2026-11-02 | W4-1 代码完成：Langfuse ingestion 埋点 + 熔断/采样 + 7 个离线测试（真机未验证） | `ai-service/app/obs/langfuse_tracer.py`、`docs/可观测性说明.md` |
| 2026-11-02 | W4-5 完成：凭据轮换清单 + 模板 + 密钥扫描脚本 + CI 门禁 | `docs/凭据轮换清单.md`、`scripts/check_secrets.sh` |
| 2026-11-02 | W4-4 完成：MCP Server（7 工具，写操作 confirm 两段式，selftest 实测） | `ai-service/scripts/mcp_server.py`、`docs/MCP集成说明.md` |
| 2026-11-02 | 收口：投递前检查清单 | `docs/投递前检查清单.md` |
| 2026-11-02 | **公网部署完成**：jar 更新（/chat 从 404 → 200）+ AI 服务上线（nginx 代理 + 内部接口封锁），公网对话/引用/确认卡实测通过 | `deploy/AI-SERVICE-DEPLOY.md` |
| 2026-11-02 | 公网演示数据：生产库扩充到 1 管理员 + 20 读者（每人强随机密码） | `deploy/credentials.local.prod-demo`（gitignored） |
| 待定 | W3 开工（需决策项 1/2） | ⬜ |
| 待定 | W4 开工 | ⬜ |
