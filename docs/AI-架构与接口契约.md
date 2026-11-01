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
| `error` | 异常结束 | `{"code":"LLM_TIMEOUT","message":"模型响应超时，已为您切换到备用模型"}` |

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
