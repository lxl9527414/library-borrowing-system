# AI 原生改造 · 架构与接口契约（W1 定稿）

> 本文件是 **Java 工具层** 与 **Python AI 服务** 之间的唯一契约。
> 任何一侧需要改接口，先改本文件，再改代码。版本：W1-v1（2026-11-01）

---

## 1. 总体架构

```text
浏览器 (Vue3)
   │  POST /api/ai/chat          SSE 流式
   ▼
┌─────────────────────────────────────────────┐
│ ai-service（Python 3.10+ / FastAPI）        │
│  ├─ api/chat.py        SSE 出口             │
│  ├─ agent/graph.py     LangGraph 状态机      │
│  ├─ llm/gateway.py     模型路由/降级/计费    │
│  └─ tools/*.py         6 个 LLM 工具         │
└───────────────┬─────────────────────────────┘
                │ 内部 HTTP（转发用户 token + 内部密钥）
                ▼
┌─────────────────────────────────────────────┐
│ backend（Spring Boot 3.5，现有项目）        │
│  /api/ai/internal/**   ← 新增：内部工具 API  │
│  复用：AuthInterceptor / RBAC / 事务 / 预约  │
└─────────────────────────────────────────────┘
```

**核心原则**

1. AI 服务**不直连数据库**，一切业务读写都经过 Java 层，复用现有事务与规则校验。
2. **身份由用户 token 决定**，不由 LLM 决定：AI 服务把浏览器带来的 `Authorization` 原样转发，Java 侧解析出 `UserSession`。LLM 无法伪造 userId。
3. 写操作（借书/还书/预约）必须经用户显式确认后才发起（见 §6）。
4. 内部接口额外要求 `X-Internal-Token`，防止公网直接访问。

---

## 2. Java 侧：内部工具 API

**Base**：`/api/ai/internal`
**鉴权**：必须同时满足
- `Authorization: Bearer <用户 token>`（沿用现有 token 机制，`AuthInterceptor` 校验）
- `X-Internal-Token: <AI_INTERNAL_TOKEN>`（新增，配置项 `app.ai.internal-token`，默认空串=开发期不校验）

**统一响应**：`ApiResponse<T>` = `{ "code": 200, "message": "操作成功", "data": ... }`
- 业务失败：HTTP 200，`code` 为 400/403/404，`message` 为中文可读原因
- 未登录/令牌过期：HTTP 401；越权：HTTP 403

### 2.1 `GET /api/ai/internal/context` — 会话上下文（非 LLM 工具，服务端注入）

```json
{
  "code": 200,
  "data": {
    "user": { "id": 2, "username": "reader", "realName": "张小明", "role": "READER" },
    "rule": {
      "maxBorrowCount": 3, "borrowDays": 30, "maxRenewCount": 1, "renewDays": 15,
      "finePerDay": 0.20, "fineMax": 20.00, "reserveLimit": 3, "holdDays": 3
    },
    "borrowedCount": 2,
    "overdueCount": 1,
    "activeReservationCount": 1,
    "pendingPickupCount": 0
  }
}
```

### 2.2 `GET /api/ai/internal/rules/text` — 借阅规则自然语言化（非 LLM 工具，注入 prompt）

```json
{ "code": 200, "data": { "text": "每人最多同时借阅 3 本，借期 30 天，最多续借 1 次（每次 15 天），逾期每天罚金 0.20 元，单笔封顶 20 元……" } }
```

### 2.3 `GET /api/ai/internal/books` — 检索图书（= LLM 工具 `search_books`）

Query：`keyword`（可选，书名/作者/ISBN 模糊）、`category`（可选）、`current`（默认 1）、`size`（默认 5，上限 20）

```json
{
  "code": 200,
  "data": {
    "records": [
      { "id": 1, "isbn": "9787536692930", "title": "三体", "author": "刘慈欣",
        "publisher": "重庆出版社", "category": "科幻", "price": 23.00,
        "totalStock": 2, "availableStock": 0, "description": "……" }
    ],
    "total": 3, "current": 1, "size": 5
  }
}
```

### 2.4 `GET /api/ai/internal/books/{id}` — 图书详情（= LLM 工具 `get_book_detail`）

```json
{
  "code": 200,
  "data": {
    "book": { "id": 1, "title": "三体", "availableStock": 0, "totalStock": 2, "...": "同 2.3" },
    "waitingCount": 2,
    "myReservation": { "id": 9, "status": "READY", "expireAt": "2026-11-03 12:00:00" }
  }
}
```
`myReservation` 无则为 `null`。

### 2.5 `GET /api/ai/internal/borrows/my` — 我的在借记录（= LLM 工具 `get_my_borrows`）

```json
{
  "code": 200,
  "data": [
    { "id": 10, "bookId": 1, "bookTitle": "三体", "bookIsbn": "9787536692930",
      "status": "BORROWED", "borrowedAt": "2026-10-01 10:00:00", "dueAt": "2026-10-31 10:00:00",
      "overdue": true, "overdueDays": 1, "renewCount": 0,
      "fineAmount": 0.20, "fineStatus": "UNPAID" }
  ]
}
```

### 2.6 写操作（3 个 LLM 工具，必须经用户确认后才由 AI 服务调用）

| 方法 | 路径 | 请求体 | 成功 data |
|---|---|---|---|
| POST | `/api/ai/internal/borrow` | `{"bookId": 1}` | `BorrowRecord`（id/bookId/status/dueAt/...） |
| POST | `/api/ai/internal/return` | `{"borrowId": 10}` | `BorrowRecord` |
| POST | `/api/ai/internal/reserve` | `{"bookId": 1}` | `BookReservation` |

写操作失败示例：`{"code":400,"message":"该图书暂无可借库存，可预约排队","data":null}`

> 写接口直接复用 `BorrowService.borrow / returnBook`、`ReservationService.reserve`，
> **不得复制业务逻辑**，事务与规则校验保持单一来源。

---

## 3. LLM 工具清单（6 个，暴露给模型）

工具名、描述、参数 schema 以 Python 侧 `app/tools/schemas.py` 为准，Java 侧只需实现 §2 的端点。

| # | 工具名 | 类型 | 说明 |
|---|---|---|---|
| 1 | `search_books` | 只读 | 按书名/作者/分类检索馆藏，返回库存状态 |
| 2 | `get_book_detail` | 只读 | 单本详情 + 预约队列长度 + 我的预约状态 |
| 3 | `get_my_borrows` | 只读 | 我的在借、到期时间、逾期天数、罚金 |
| 4 | `borrow_book` | **写** | 借阅，参数 `bookId` |
| 5 | `return_book` | **写** | 归还，参数 `borrowId` |
| 6 | `reserve_book` | **写** | 预约排队，参数 `bookId` |

只读工具直接执行；**写工具一律进入确认流程**。

---

## 4. AI 服务：SSE 协议

### 4.1 `POST /api/ai/chat`

请求头：`Authorization: Bearer <token>`、`Content-Type: application/json`

```json
{ "sessionId": "可选，空则由服务端生成", "message": "帮我找三体，如果没货就预约" }
```

响应：`text/event-stream`，事件序列（`event:` 名 + `data:` 单行 JSON）

| 事件 | 时机 | data 示例 |
|---|---|---|
| `meta` | 流开始 | `{"sessionId":"s-1","traceId":"t-1"}` |
| `token` | 模型增量文本 | `{"text":"《三体》"}` |
| `tool_call` | 即将调用工具 | `{"callId":"c1","name":"search_books","args":{"keyword":"三体"}}` |
| `tool_result` | 工具返回 | `{"callId":"c1","name":"search_books","ok":true,"summary":"找到 1 本，可借 0 本","data":[...]}` |
| `confirm_required` | 写操作待确认，流在此结束 | `{"confirmationId":"cf-1","tool":"reserve_book","args":{"bookId":1},"summary":"《三体》暂无库存，是否为您预约排队？"}` |
| `done` | 正常结束 | `{"sessionId":"s-1","usage":{"promptTokens":812,"completionTokens":96},"latencyMs":1840}` |
| `error` | 异常结束 | `{"code":"LLM_TIMEOUT","message":"模型响应超时，已为您切换到备用模型"}`<br>`{"code":"CONFIRMATION_PENDING","message":"上一笔写操作还在等待确认…","pendingAvailable":true,"confirmationId":"cf-1","summary":"…","tools":[…]}` |

约束：
- 每个流**必须以 `done` 或 `error` 或 `confirm_required` 之一结束**
- `confirm_required` 是"暂停"而非错误，前端据此弹确认卡片

### 4.2 `POST /api/ai/chat/confirm`

```json
{ "sessionId": "s-1", "confirmationId": "cf-1", "approved": true }
```

响应：**同一套 SSE 事件流**（继续执行被暂停的工具，然后继续对话到 `done`）。
`approved=false` 时跳过写操作，模型会收到"用户已取消"的工具结果并自然回复，最后 `done`。

### 4.3 其他端点

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/ai/health` | `{"status":"ok","llmProvider":"mock","version":"w1"}` |
| GET | `/api/ai/sessions/{sessionId}` | 会话历史（调试用，可返回最近 20 条消息） |
| GET | `/api/ai/chat/pending?sessionId=...` | 查询该会话当前待确认的写操作；返回 `{"pending": {confirmationId, summary, tools}}` 或 `{"pending": null}`。用于页面刷新/确认卡丢失后的恢复 |

---

## 5. 内部错误码（`error` 事件 / 日志）

| code | 含义 | 处理 |
|---|---|---|
| `LLM_TIMEOUT` | 主模型超时 | 自动降级到 fallback 链 |
| `LLM_AUTH_ERROR` | Key 无效/未配置 | 立即报错，提示配置 `.env` |
| `LLM_RATE_LIMIT` | 触发限流 | 退避重试 1 次后降级 |
| `TOOL_NOT_FOUND` | 模型调了不存在的工具 | 回灌工具错误，让模型自我纠正（最多 3 轮） |
| `TOOL_UPSTREAM_ERROR` | Java 内部接口异常 | 回灌错误信息，不中断对话 |
| `UPSTREAM_UNAUTHENTICATED` | 用户 token 失效 | 直接把 401 透传给前端，要求重新登录 |
| `AGENT_MAX_STEPS` | 超过最大循环步数（默认 8） | 强制结束并 `done` |
| `CONFIRMATION_PENDING` | 会话有未确认的写操作（LangGraph 停在 interrupt） | 错误事件携带 `confirmationId/summary/tools`（若记录仍在），前端渲染确认卡供确认或取消；记录已过期时提示「开始新对话」 |

---

## 6. 写操作确认流程（LangGraph 实现要点）

```text
用户输入 → agent 节点（LLM 决策）
             ├─ 无 tool_calls → 输出文本 → END
             ├─ 只有只读工具 → tools 节点执行 → 回到 agent
             └─ 含写工具 → authorize 节点 → interrupt(确认信息)
                              ├─ 用户 approved=true  → tools 节点执行 → agent
                              └─ 用户 approved=false → 回灌"用户已拒绝" → agent
```

- `interrupt()` 前不能有副作用（LangGraph 恢复时会重放节点）
- 确认状态存 `confirmationId → pending tool_calls`，TTL 5 分钟
- 同一次响应里多个写工具：**一次性合并确认**，避免连环弹窗

---

## 7. 环境变量

以仓库根目录 `.env.example` 为唯一来源，Java 与 Python 都从环境变量读取，**不得硬编码密钥**。
关键项：`LLM_PROVIDER`、`DEEPSEEK_API_KEY`、`DASHSCOPE_API_KEY`、`JAVA_API_BASE`、`AI_INTERNAL_TOKEN`。

---

## 8. 目录归属（并行开发写区划分）

| 目录/文件 | 归属 | 说明 |
|---|---|---|
| `backend/src/main/java/com/library/ai/**` | Java 工具层 | 新增包，不修改现有业务类 |
| `backend/src/test/java/com/library/ai/**` | Java 工具层 | 新增测试 |
| `ai-service/**` | Python AI 服务 | 独立目录 |
| `docker-compose.yml`、`.env.example`、`docs/**`、`README.md` | 集成方（Lead） | 其他人不要改 |

**铁律：不改动现有业务代码**（`service/`、`controller/`、`entity/` 等），现有 42 个单测必须继续全绿。
唯一例外：`application.yml` 增加 `app.ai.*` 配置段由集成方统一添加。

---

## 9. W1 实测结果与已知差异（2026-11-01 更新）

> 实测环境：隔离 MySQL 8.0.40（127.0.0.1:3308，临时 datadir，未触碰本机既有数据库）+ 本机 Redis 7 + `mvn spring-boot:run` 后端。
> 种子数据：`data.sql` 自动初始化 500 本图书 / 6 个用户。

### 9.1 已实测通过

| 验证项 | 结果 |
|---|---|
| 登录获取 token | ✅ admin / reader 均成功 |
| `GET /context` | ✅ 真实返回用户、规则（maxBorrowCount=5, borrowDays=30）、借阅/预约计数 |
| 缺 `X-Internal-Token` | ✅ HTTP 403 `{"code":403,"message":"内部接口鉴权失败"}` |
| 缺 `Authorization` | ✅ HTTP 401 `{"code":401,"message":"请先登录"}` |
| `GET /rules/text` | ✅ 返回中文自然语言规则 |
| `GET /books?keyword=` | ✅ total=500，分页与库存字段正确 |
| `GET /books/{id}` | ✅ 图书详情 + waitingCount=0 |
| `POST /borrow` | ✅ dueAt 自动 +30 天，`borrow_no` 生成，库存 6→5 |
| `GET /borrows/my` | ✅ overdue=false, fineAmount=0.0, fineStatus=NONE |
| `POST /return` | ✅ status→RETURNED，returnedAt 落库，库存 5→6 恢复 |
| `POST /reserve`（有库存） | ✅ code=400「该图书当前有可借库存，可直接借阅」 |
| 伪造 `userId` 字段 | ✅ 被忽略，仍按 token 身份执行 |
| `@Auditable` 操作日志 | ✅ SUCCESS 与 FAIL 双状态均落库（含 URI/参数/IP/耗时/错误信息） |
| 数据库落库校验 | ✅ `borrow_record.user_id=2`，状态流转正确 |

### 9.2 与示例的差异（Python 侧必须按实际解析）

1. **null 字段被省略**：Jackson 配置 `default-property-inclusion: non_null`，因此 `myReservation` 为 null 时字段**不存在**，不是 `null`。用 `.get()` 判空。
2. **在借记录罚金恒为 0**：现有系统罚金在归还时才结算，`get_my_borrows` 返回 `fineAmount=0.0, fineStatus="NONE"`。AI 对在借图书只能报逾期天数，禁止报罚金金额。
3. **规则数值以接口为准**：`maxBorrowCount=5`、`borrowDays=30`（README 未写死，但不要在前端/提示词里硬编码）。
4. **`size` 上限 20**：`/books` 的 `size` 收敛到 1~20。
5. **ADMIN 调用 `/borrows/my` 沿用全局语义**：与前端借阅列表一致（W1 已知限制，如要求严格"我的"需给 BorrowService 新增方法）。

### 9.3 回归命令

```bash
cd backend && JAVA_HOME=/Users/dd/Library/Java/JavaVirtualMachines/ms-17.0.18/Contents/Home mvn -B clean test
# 结果：Tests run: 71, Failures: 0, Errors: 0（原有 42 + 新增 29）
```

> 注意：必须带 `clean`。本机 Java 语言服务器会在 `target/` 写入带编译错误的 class，导致裸 `mvn test` 增量编译跳过而报 2 个假失败。

### 9.4 端到端验证：AI 服务 + 真实后端 + 真实数据库

脚本：`scripts/e2e_ai.py`（`ai-service/.venv/bin/python scripts/e2e_ai.py`）

实测事件流（mock 模型 + 真实 Java 后端 + MySQL）：

```text
帮我借红楼梦
  meta              → sessionId/traceId
  tool_call         → search_books {"keyword":"红楼梦"}
  tool_result       → ok, 找到 1 本，可借 6 本
  confirm_required  → cf-xxx「即将为你借阅《红楼梦》（bookId=3），确认借阅吗？」
  （此时查库：borrowedCount 仍为 0 —— 未确认绝不落库）
用户确认 approved=true
  tool_call         → borrow_book {"bookId":3}
  tool_result       → ok「借阅成功，应还日期 2026-12-01」
  token × N → done   （查库：borrowedCount = 1，borrow_record 新增一行）
我要还书
  tool_call → get_my_borrows → confirm_required「归还《红楼梦》（borrowId=2）」→ 确认
  → return_book → ok → done（查库：库存从 5/6 恢复为 6/6，status=RETURNED）
```

**断言结果：全部通过**

| 安全/工程命题 | 验证方式 | 结果 |
|---|---|---|
| 人在回路：确认前不落库 | 收到 `confirm_required` 后查库，借阅数不变 | ✅ |
| 确认后才执行写操作 | 确认后查库 +1，`borrow_record` 新增行 | ✅ |
| 身份隔离：reader 看不到 reader2 的数据 | reader 借书后 reader2 的 `/borrows/my` 为 0 条 | ✅ |
| 越权拦截：reader2 不能归还 reader 的记录 | `POST /return` → `code=403「只能归还自己的借阅记录」` | ✅ |
| 工具调用可审计 | `operation_log` 记录 5 条 AI 工具动作（含 SUCCESS/FAIL、耗时、URI、参数） | ✅ |
| 库存一致性 | 借出 6→5，归还 5→6，无并发丢失 | ✅ |
| token 失效透传 | 用 fake token 请求 → AI 服务直接回 HTTP 401，不进入 Agent | ✅ |

### 9.5 W1 期间修复的缺陷

1. **mock 模型关键词抽取**：`帮我借红楼梦` 被抽成 `借红楼梦` 导致检索为空，写操作确认流程无法触发。修复方式：`_NOISE_WORDS` 补齐「帮我借/我要借/想借/借一本…」等动词短语，并按长度倒序剔除。
2. **压测脚本不支持自定义请求头**：无法压 `/api/ai/internal/**` 这类需要 `X-Internal-Token` 的接口。修复方式：新增 `--header`（可重复）与失败原因采集。
   修复后实测：`/api/ai/internal/context` 并发 10 下 2573 QPS / P95 6.8ms / 0 失败（本机 M 系列芯片 + 本地 MySQL，仅作脚本可用性验证，不是容量结论）。

### 9.6 测试总览

| 层 | 用例数 | 命令 | 结果 |
|---|---|---|---|
| Java（含原有 42） | 71 | `mvn -B clean test` | 全绿 |
| Python（AI 服务） | 91 | `ai-service/.venv/bin/python -m pytest ai-service -q` | 全绿 |
| 端到端 | 16 项断言 | `python scripts/e2e_ai.py` | 全绿 |

---

## 10. W2 实测结果（2026-11-01）

### 10.1 前端流式对话页

| 验证项 | 结果 |
|---|---|
| `npm run build` | ✅ 4.67s，`ChatView` chunk 122.25 kB（gzip 50.70 kB） |
| Vite 代理顺序（`/api/ai` 在 `/api` 之前） | ✅ `/api/ai/health` 经 5173 代理返回 200 |
| 无头 Chrome 真实走查（CDP） | ✅ 见 `docs/screenshots/01~04`：欢迎态 / 工具轨迹 / 确认卡片 / 成功回执 |
| **浏览器点「确认执行」是否真落库** | ✅ `borrow_record` 新增 id=4（user 2 / book 3 / BORROWED），库存 6→5，`operation_log` 记录「AI借阅图书 SUCCESS 85ms」 |
| 确认前不落库 | ✅ 确认卡出现时查库无新增记录 |
| 移动端 390×844 | ✅ 无横向溢出 |
| 401 处理 | ✅ 清 token 跳 `/login` |
| 控制台错误 | ✅ 主流程 0 未捕获异常 |

**截图**：`docs/screenshots/{01-welcome,02-found,03-confirm,04-success}.png`

> 截图流程由 `scripts/ui_screenshot.mjs` 驱动（自写 CDP，不依赖 Playwright），可随时重跑生成最新界面。

### 10.2 会话与 checkpointer 迁移 Redis

| 验证项 | 结果 |
|---|---|
| Python 单测 | ✅ 111 passed（原 91 + 新增 20） |
| `GET /api/ai/health` | ✅ `{"sessionStore":"redis","checkpointer":"memory"}` |
| 服务重启后会话恢复 | ✅ `ai-service/scripts/verify_redis_persistence.py` 两进程验证：重启后 4 条消息仍在 |
| Redis 不可用降级 | ✅ 服务仍可启动，health 显示 `memory`，问答正常以 `done` 结束 |
| Redis key 规范 | `ai:session:{id}`（TTL 3600s）、`ai:confirmation:{id}`（TTL 300s，GETDEL 一次性消费） |

**已知限制**：本机 Redis 为纯 `redis-server`，缺 RedisJSON + RediSearch 模块，因此 LangGraph 的
`AsyncRedisSaver` 自动降级为 `InMemorySaver`（`checkpointer=memory`）：会话历史与确认状态**已**真持久化，
但"图检查点/中断暂停态"仍在进程内。换成 Redis Stack 即自动启用，无需改代码；`docker-compose.yml`
里已注明替换镜像的方式。

### 10.3 W2 期间修复的缺陷

1. **生产构建下 `/chat` 返回 404**：`SpaController` 的 SPA 路由是硬编码白名单，漏了新页面。
   Vite dev 有 history fallback 所以本地发现不了，只有打包成 jar 后直接访问才暴露。
   已补 `/chat` 并加注释提醒"新增页面必须同步"。
2. **Vite 代理顺序陷阱**：`/api` 若声明在 `/api/ai` 之前，AI 的 SSE 请求会被错误转发到业务后端 18080。
   已在 `vite.config.js` 用注释固化顺序约定。
3. **nginx 未给 SSE 关缓冲**：新增 `/api/ai/` location（`proxy_buffering off` + `X-Accel-Buffering: no` +
   300s 读超时），并显式 `location /api/ai/internal/ { return 404; }` 封掉内部工具接口的公网入口。

### 10.4 尚未完成（需真实模型 Key）

- 真实 DeepSeek/Qwen 联调：本机无 Key，Agent 主链路由 mock 模型驱动（协议与流程已全覆盖）。
  你填 Key 后可先跑 `ai-service/.venv/bin/python scripts/check_llm.py` 验证连通性 / function calling / 首 token 延迟，
  再跑 `scripts/e2e_ai.py` 走真实链路。
- 前端"确认执行"路径对真实后端的写库验证已完成（见 10.1）；真实模型下的工具选择准确率待评测（W3）。

### 10.5 W2 体验缺陷修复（对话页「新对话」）

两个真实体验问题，均由 `scripts/ui_screenshot.mjs` 的回归检查固化：

| 现象 | 根因 | 修复 |
|---|---|---|
| 「新对话」只能点一次，点完就变灰 | `canReset` 定义为「正在流式 或 存在用户消息」，重置后只剩开场白 → 立刻又禁用 | 改为常驻可点；流式中点击等同「停止并清空」，是幂等操作 |
| 每点一次新对话都重新"打招呼" | `resetChat` 里 `messages = [createWelcome()]` 每次新建一条欢迎语 | 复用已渲染的那条开场白（`find(m => m.welcome)`），只清用户/助手消息；开场白在页面生命周期内只出现一次 |

验证：`scripts/ui_screenshot.mjs` 现在会在截图后自动执行「连续点 2 次新对话 → 断言只剩 1 条开场白 + 按钮未禁用」，
并输出 `docs/screenshots/05-new-chat.png`；同时脚本会**自动归还**演示借出的书，`scripts/e2e_ai.py` 也会在开始前
清理同一本书的残留借阅，两个脚本因此都可反复执行、互不污染。

### 10.6 Mock 模型的边界与增强（2026-11-01）

**背景**：默认 `LLM_PROVIDER=mock` 是为离线开发/CI 准备的**关键词假模型**，不是真大模型。
用户问「有地理类的书吗」这类自由表达时，早期 mock 认不出意图，回落到固定开场白，容易被误解为"AI 坏了"。

**增强内容**：

| 项 | 修复前 | 修复后 |
|---|---|---|
| 分类类问句 | 认不出，回落到开场白 | 支持「有X类的书吗 / 推荐几本X的书」：先抽主题词，命中 13 个真实分类则按 `category` 检索，否则按 `keyword` 检索（如"地理"→匹配书名） |
| 兜底回复 | 复读开场白，看起来像死循环 | 明确说明「当前离线 Mock 模式，只能按关键词应答」，并给出可用的问法与切换到真实模型的方法 |
| 前端提示 | tooltip 只显示 provider 名 | mock 模式下 tooltip 明确说明局限与切换方式 |

**验证**：`有地理类的书吗` → `search_books(keyword=地理)` 命中 6 本；`有文学类的书吗` → `category=文学` 101 本；
`有计算机类的书吗` → `category=计算机` 55 本；界面截图 `docs/screenshots/06-category-query.png`。111 个单测保持全绿。

**仍然的边界**：mock 只能按内置词表应答，无法真正理解自由语言。**要演示"自然语言理解"，必须配置真实模型**：
`.env` 设 `LLM_PROVIDER=deepseek` + `DEEPSEEK_API_KEY=...`，先用 `scripts/check_llm.py` 验证，再重启 `dev_up.sh`。
