# Python 最小必要知识（能读能改即可）

> 面向：Java 背景、Python 只会基础语法、但需要读懂/改动本项目 AI 服务的人。
> 原则：**不系统学 Python，只学这 10 个点**，全部绑定项目文件。

## 0. 学习路径（按顺序读）

```
main.py（入口，看 FastAPI 怎么起）
  → api/chat.py（路由 + SSE 流）
  → agent/runner.py（流式执行器）
  → agent/graph.py（状态机）
  → tools/executor.py（工具分发）
  → rag/service.py（检索）
```

## 1. 模块与包

```python
from app.agent.graph import init_graph      # 绝对导入（本项目统一用）
```
- `app/` 是包（有 `__init__.py`），`app.agent.graph` 是模块路径；
- 会看 `from ... import ...` 就够了，不必背相对导入。

**项目位置**：任意文件顶部。

## 2. 函数、类与方法

```python
class AgentStreamRunner:
    async def run(self, ctx: RunContext) -> None:   # self 是实例
```

- `def` / `class` / `async def` / `self` 的含义；
- 会读类型注解和默认参数即可。

**项目位置**：`app/agent/runner.py`、`app/rag/bm25.py`。

## 3. 类型注解（Reading only）

```python
def search(self, query: str, top_k: int = 5) -> list[dict]: ...
dict[str, Any] | None
```
- 主要作用是"看得懂签名"，不需要自己写复杂类型。

## 4. async / await（本项目最关键的语法）

```python
result = await client.get(...)               # 等待异步 IO
await asyncio.to_thread(self._embed, text)   # 把阻塞函数丢到线程池
async for chunk in stream: ...               # 异步迭代
```

**要点**：
- `async def` 函数必须 `await` 调用；
- 同步阻塞函数（如本地计算/网络 SDK）用 `asyncio.to_thread` 包一层，否则会卡住事件循环；
- 本项目里"调模型/调检索/调 Java 接口"全是 async。

**项目位置**：`app/llm/gateway.py`、`app/tools/executor.py`、`app/rag/service.py`。

## 5. pydantic（配置与数据校验）

```python
class Settings(BaseSettings):
    llm_provider: str = "deepseek"
    model_config = SettingsConfigDict(env_file=...)
```

- 用类字段声明配置，自动从环境变量/`.env` 读取；
- 请求体也是 pydantic 模型（自动校验 + 报错）。

**项目位置**：`app/config.py`、`app/tools/schemas.py`。

## 6. 装饰器（看得懂即可）

```python
@router.post("/chat")
async def chat(payload: ChatRequest, request: Request): ...
```

- `@router.post("/chat")` 把这个函数注册成 HTTP 接口；
- 本项目还有 `@property`、`@dataclass`，都是"给函数/类加行为"的语法糖。

**项目位置**：`app/api/chat.py`。

## 7. 异常处理与日志

```python
try:
    data = await client.get(...)
except JavaClientError as exc:
    logger.warning("工具调用失败", extra={"code": exc.code})
```

**要点**：能读懂 `try/except`、`raise`、`finally`；本项目日志是结构化 JSON（`extra={}` 传字段）。

**项目位置**：`app/obs/logging.py`、`app/tools/java_client.py`。

## 8. 虚拟环境与依赖

```bash
cd ai-service
.venv/bin/python -V                 # Python 3.10.9
.venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn app.main:app --port 8000 --reload
```

**要点**：`.venv` 是项目专用解释器；`requirements.txt` 是依赖清单；不要在系统 Python 里装。

## 9. pytest（会加一条断言就够）

```python
def test_search_returns_chunks(fake_rag):
    result = fake_rag.search("罚金")
    assert result, "应返回结果"
    assert "chunk_id" in result[0]
```

**常用命令**：
```bash
.venv/bin/python -m pytest -q              # 全部
.venv/bin/python -m pytest tests/test_rag_service.py -q   # 单文件
.venv/bin/python -m pytest -k "bm25" -q    # 按名字过滤
```

**项目位置**：`ai-service/tests/`（319 个）。

## 10. 读 traceback（调试第一步）

顺序：**最下面一行是错误类型与信息，往上找第一个属于你项目的文件行**。

```
app/tools/executor.py:123: KeyError: 'data'     ← 先看这里
```

本项目踩过的真实例子：`e2e_ai.py` 里 `KeyError: 'data'`，原因是内部 token 硬编码，轮换后 403 —— 错误信息在底层，根因在配置。

## 11. 面试话术（被问 Python 水平时）

> "我 Python 是能读能改的水平。这个 AI 服务是我主导设计的，Python 代码用 AI 辅助实现，我负责架构、Prompt、契约和验收；比如 `graph.py` 的状态流转、`executor.py` 的工具分发、评测口径我都能讲清楚，遇到 bug 能定位到文件改。"

## 12. 两周练习清单

- [ ] Day 1：读 `main.py`，说出启动时做了哪几件事；
- [ ] Day 2：给 `search_knowledge` 的 `top_k` 加一个上限校验，跑测试；
- [ ] Day 3：读 `api/chat.py`，画出 SSE 事件顺序；
- [ ] Day 4：改一条拒答文案，跑 `pytest` 看哪些断言受影响；
- [ ] Day 5：给 `tests/` 加一条新断言（比如检查 chunks 非空）；
- [ ] Day 6：读 `graph.py`，白板画出节点与边；
- [ ] Day 7：用 `--pdb` 调试一个失败的测试；
- [ ] Week 2：独立加一个"只读工具"（如查询借阅规则文本），走完 schema → executor → 测试。
