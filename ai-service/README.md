# 云阁图书馆 AI 服务（W1）

FastAPI + LangGraph 实现的 AI Agent 服务：把用户的自然语言请求翻译成对 Java 业务后端的工具调用，
通过 SSE 流式返回，并对「借书 / 还书 / 预约」这类写操作做**显式确认**。

> 契约唯一来源：仓库根目录 `docs/AI-架构与接口契约.md`（§3 工具、§4 SSE、§5 错误码、§6 确认流程）。
> 本服务不直连数据库，一切业务读写都通过 Java 内部接口 `/api/ai/internal/**`，用户身份只来自浏览器 token。

---

## 1. 快速开始（离线 mock 模式，无需任何 Key）

```bash
cd ai-service

# 1) 创建虚拟环境（本机 Python 3.10.9 已验证；3.11/3.12 亦可）
python3 -m venv .venv
.venv/bin/pip install -i https://mirrors.aliyun.com/pypi/simple/ --trusted-host mirrors.aliyun.com -r requirements.txt

# 2) 启动（默认 LLM_PROVIDER=mock，端口 8000）
bash scripts/dev_run.sh
#   或：LLM_PROVIDER=mock .venv/bin/uvicorn app.main:app --port 8000

# 3) 冒烟
curl http://127.0.0.1:8000/api/ai/health
curl -N -X POST http://127.0.0.1:8000/api/ai/chat \
  -H 'Content-Type: application/json' -H 'Authorization: Bearer fake-token' \
  -d '{"message":"帮我找三体"}'
```

Docker（与 `docker-compose.yml` 的 `ai-service` 服务一致）：

```bash
docker compose up -d --build ai-service   # 镜像内同样支持 mock 模式
```

配置全部来自环境变量（`pydantic-settings` 读取仓库根 `.env` 与 `ai-service/.env`）：
`LLM_PROVIDER`、`DEEPSEEK_API_KEY`、`DASHSCOPE_API_KEY`、`OLLAMA_*`、`JAVA_API_BASE`、
`AI_INTERNAL_TOKEN`、`LLM_TIMEOUT_SECONDS`、`LLM_FALLBACK_CHAIN`、`AI_SESSION_TTL_SECONDS` 等，
完整清单见 `.env.example`。**代码中不出现任何硬编码密钥**；缺 Key 时返回
`LLM_AUTH_ERROR` 并提示具体要配置的环境变量。

---

## 2. 目录结构

```text
ai-service/
├── app/
│   ├── main.py              FastAPI 入口：CORS、路由、异常兜底
│   ├── config.py            pydantic-settings 配置（环境变量 / .env）
│   ├── obs/logging.py       结构化 JSON 日志（traceId/sessionId/tool/耗时）
│   ├── llm/
│   │   ├── base.py          LLMError / 错误码 / ProviderConfig / Provider 抽象
│   │   ├── mock.py          MockChatModel：确定性意图识别 + 流式输出
│   │   ├── openai_compat.py ChatOpenAI 封装（DeepSeek / 通义 / Ollama）
│   │   └── gateway.py       路由、超时、限流重试、降级链、token 统计
│   ├── tools/
│   │   ├── schemas.py       6 个 LLM 工具的 function schema + 写工具集合
│   │   ├── java_client.py   httpx 调 Java 内部接口（转发 token + X-Internal-Token）
│   │   └── executor.py      工具分发、错误归一化、中文摘要
│   ├── agent/
│   │   ├── state.py         AgentState（检查点）+ RunContext（请求级依赖）
│   │   ├── prompts.py       系统提示词 + Java 侧会话上下文注入
│   │   ├── events.py        SSE 事件模型 / 事件出口
│   │   ├── graph.py         LangGraph 状态机：agent → authorize → tools → agent
│   │   └── runner.py        把图执行过程编排成 SSE 事件流
│   ├── api/
│   │   ├── chat.py          /api/ai/chat、/chat/confirm、/health、/sessions
│   │   └── models.py        请求/响应模型
│   └── memory/store.py      会话历史 + 确认状态内存存储（TTL，预留 Redis）
├── tests/                   pytest（离线：mock 模型 + httpx.MockTransport）
├── scripts/dev_run.sh       无参数本地启动脚本
├── requirements.txt / Dockerfile / pytest.ini / conftest.py
```

---

## 3. 接口

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/ai/chat` | 对话，SSE 流式 |
| POST | `/api/ai/chat/confirm` | 写操作确认后恢复，返回同一套 SSE 事件 |
| GET | `/api/ai/health` | `{"status":"ok","llmProvider":"mock","version":"w1"}` |
| GET | `/api/ai/sessions/{sessionId}` | 会话历史（调试用，最近 20 条） |

请求头：`Authorization: Bearer <用户 token>`（必填）、`Content-Type: application/json`。

### 3.1 SSE 事件（契约 §4.1）

```text
event: meta               {"sessionId":"s-1","traceId":"t-1"}
event: token              {"text":"《三体》"}
event: tool_call          {"callId":"c1","name":"search_books","args":{"keyword":"三体"}}
event: tool_result        {"callId":"c1","name":"search_books","ok":true,"summary":"找到 1 本图书，当前可借 0 本","data":[...]}
event: confirm_required   {"confirmationId":"cf-1","tool":"reserve_book","args":{"bookId":1},"summary":"《三体》暂无可借库存，是否为你预约排队？"}
event: done               {"sessionId":"s-1","usage":{"promptTokens":812,"completionTokens":96},"latencyMs":1840}
event: error              {"code":"LLM_TIMEOUT","message":"模型响应超时，已尝试切换备用模型。"}
```

每个流**一定**以 `done` / `error` / `confirm_required` 之一结束：

- `confirm_required` 表示暂停（不是错误），前端弹确认卡片；此时**不会**再有 `done`；
- 调用 `/chat/confirm` 后继续执行并最终以 `done`（或 `error`）结束。

### 3.2 写操作确认（契约 §6）

```text
用户输入 → agent（LLM 决策）
             ├─ 无 tool_calls          → END
             ├─ 只有只读工具            → tools → agent
             └─ 含写工具（借/还/预约）  → authorize → interrupt 暂停，返回 confirm_required
                                          ├─ approved=true  → tools 执行 → agent → done
                                          └─ approved=false → 回灌「用户已取消」→ agent → done
```

实现细节：

- LangGraph `interrupt()` 前无副作用（恢复时节点会重放）；`confirmationId` 由
  `sessionId + step + 工具参数` 哈希得到，保证重放稳定、可幂等；
- 同一次模型回复里的多个写工具**合并为一次确认**（`confirm_required.tools` 数组，
  单个写操作时额外透出扁平的 `tool` / `args` 字段兼容契约示例）；
- 确认记录 `confirmationId -> 工具调用` 存在内存 store，**TTL 300s、一次性消费**，
  重复点击确认不会重复执行写操作；
- 上一笔写操作未确认时再发新消息，会以 `error: CONFIRMATION_PENDING` 拒绝，
  避免 LangGraph 把新输入误当成 interrupt 的恢复值。

---

## 4. 错误码（契约 §5）

| code | 含义 | 处理 |
|---|---|---|
| `LLM_TIMEOUT` | 主模型超时 | 无 token 输出时自动降级到 fallback 链 |
| `LLM_AUTH_ERROR` | Key 无效 / 未配置 | 报错并提示配置 `.env` 的哪个环境变量 |
| `LLM_RATE_LIMIT` | 模型限流 | 退避 0.2s 重试 1 次，仍失败再降级 |
| `TOOL_NOT_FOUND` | 模型调用了不存在的工具 | 错误回灌模型自我纠正，最多 3 轮 |
| `TOOL_UPSTREAM_ERROR` | Java 内部接口异常（5xx / 超时 / 业务失败） | 错误回灌模型，对话不中断 |
| `UPSTREAM_UNAUTHENTICATED` | 用户 token 失效（Java 返回 401） | 终止流，前端重新登录 |
| `AGENT_MAX_STEPS` | 超过最大循环步数（默认 8） | 强制结束 |

本服务额外扩展的错误码（契约表未列出，前端可按未知错误展示）：

| code | 含义 |
|---|---|
| `LLM_UPSTREAM_ERROR` | 模型侧非超时/非鉴权的网络或 5xx 异常 |
| `CONFIRMATION_PENDING` | 上一笔写操作等待确认中，拒绝新输入 |
| `CONFIRMATION_INVALID` | 确认 ID 不存在 / 已过期 / 已消费 / 会话不匹配 |
| `INTERNAL_ERROR` | 未预期异常兜底 |

关于 401：SSE 开始前的会话上下文预取若发现 401，直接返回 **HTTP 401 JSON**
（`{"code":401,"message":"..."}`），即“把 401 透传给前端”。

---

## 5. HTTP 层安全与超时

- `java_client` 统一使用 `httpx.AsyncClient`，连接超时 3s / 读超时 10s（可配置），
  会话上下文预取使用更短的 3s 超时；失败降级为“无上下文模式”，对话继续；
- 模型调用整体 `asyncio.wait_for` 超时（`LLM_TIMEOUT_SECONDS`，默认 60s）；
- 每次请求生成 `traceId`，日志为单行 JSON，含 `traceId / sessionId / tool / durationMs / code`，
  可直接接 Langfuse（W4）。

---

## 6. Agent 状态与内存存储

- LangGraph `InMemorySaver`，`thread_id = sessionId`，支持多轮对话与 interrupt 恢复；
- 会话历史与确认状态在 `app/memory/store.py`，均为进程内 + TTL 实现；
- **W1 限制**：只适合单实例部署。W2 需要把 checkpointer 与会话存储一起换成 Redis，
  代码已预留 `SessionStore` / `ConfirmationStore` 接口与 Redis 占位实现。

### Python 3.10 兼容说明

LangGraph 1.x 的 `interrupt()` / `get_stream_writer()` 依赖 `var_child_runnable_config`
contextvar 在 asyncio 任务间传播，而 Python 3.10 的 `asyncio.create_task` 不支持
`context=` 参数，二者在 3.10 下会抛 `Called get_config outside of a runnable context`。
`app/agent/graph.py` 的 `_with_config_context` 装饰器在节点入口显式 set/reset 该
contextvar，使官方 API 在 3.10 上行为与 3.11+ 一致（已用真实进程冒烟验证）。

---

## 7. Mock 模型（离线确定性测试）

`app/llm/mock.py` 继承 LangChain `BaseChatModel`，用规则把用户输入映射为固定行为：

| 用户输入 | 模型行为 |
|---|---|
| 「帮我找三体」 | `search_books(keyword=三体)` → 汇总结果 |
| 「帮我预约三体」 | 先 `search_books` 补 bookId，无库存再 `reserve_book`（触发确认） |
| 「帮我找三体，如果没货就预约」 | 检索 → 无库存 → 预约（两轮工具调用 + 一次确认） |
| 「我要还书」 | `get_my_borrows` → 归还第一条（触发确认） |
| 「我的借阅 / 有没有逾期」 | `get_my_borrows` |
| 「你好」 | 固定开场白（无工具调用） |

输出文案按固定分片长度流式产出，测试可断言事件序列而不受模型随机性影响。
mock 模型**只用于测试与演示**，真实模型走 `LLM_PROVIDER=deepseek|qwen|ollama`。

---

## 8. 测试

```bash
cd ai-service
.venv/bin/python -m pytest -q          # 91 passed（离线，不依赖网络与 API Key）
```

测试策略：

- `tests/test_mock_llm.py`：mock 意图识别 / 流式分片 / 确定性；
- `tests/test_java_client.py`：请求头透传、业务错误/401/5xx/超时分类，
  并覆盖现网实测差异（`myReservation` 缺省、`data` 为 null、`size` 上限 20）；
- `tests/test_executor.py`：6 个工具的中文摘要与错误码归一化；
- `tests/test_gateway.py`：缺 Key 中文报错、超时降级、部分输出不重复降级、限流重试；
- `tests/test_graph_flow.py`：只读流程、写操作确认/拒绝、多写合并、最大步数、错误码；
- `tests/test_sse_chat.py`：HTTP 层 SSE 协议、确认流程、Java 不可用/5xx 优雅降级；
- `tests/test_store.py` / `test_config.py` / `test_prompts.py`：存储 TTL、配置、提示词。

---

## 9. 已验证的现网差异（Java 实测）

1. `GET /api/ai/internal/books/{id}` 的 `myReservation` 为 `null` 时字段被 Jackson
   **直接省略**（不是 `null`），客户端一律用 `.get("myReservation")` 判空；
2. 在借记录的 `fineAmount` 当前恒为 `0.0` / `fineStatus=NONE`（罚金只在归还时结算），
   因此提示词禁止对在借图书报罚金金额，只报逾期天数；
3. `context` 接口的规则数值（如 `maxBorrowCount=5`、`borrowDays=30`）以接口返回为准，
   提示词由返回数据拼装，不硬编码。

---

## 10. 下一步（W2+）

- Redis checkpointer + 会话存储，支持多实例；
- 真实模型联调（DeepSeek / 通义），补充 token 计费与限流熔断；
- Langfuse 全链路追踪（`LANGFUSE_ENABLED`，日志字段已对齐）；
- W3：RAG（pgvector）与推荐、Text2SQL（W1 明确不做）。

> 安全提醒：`GET /api/ai/sessions/{id}` 为调试端点，生产环境应加内网限制或鉴权；
> `AI_INTERNAL_TOKEN` 生产环境务必替换默认值。
