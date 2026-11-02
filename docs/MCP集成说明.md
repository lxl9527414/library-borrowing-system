# MCP 集成说明（W4-4）

> 把项目的 7 个能力暴露成标准 **MCP（Model Context Protocol）** 工具，让任意支持 MCP 的客户端
> （Claude Desktop、IDE、自研 Agent）都能直接调用这套图书馆能力。
>
> 意义：证明工具层不是绑死在某个框架上，而是**可被标准协议消费的能力**。

---

## 1. 实现

`ai-service/scripts/mcp_server.py`，基于官方 SDK `mcp>=2.0`（`mcp.server.mcpserver.MCPServer`）。

| MCP 工具 | 对应 Agent 工具 | 说明 |
|---|---|---|
| `search_knowledge(query, top_k, source_types)` | `search_knowledge` | 检索知识库，返回带 `citation_no` 的片段，回答时应引用 `[n]` |
| `search_books(keyword, category, size)` | `search_books` | 馆藏检索（库存） |
| `get_book_detail(book_id)` | `get_book_detail` | 图书详情 + 预约队列 |
| `get_my_borrows()` | `get_my_borrows` | 当前用户在借记录 |
| `borrow_book(book_id, confirm)` | `borrow_book` | **写操作**，`confirm=False` 只返回预览 |
| `return_book(borrow_id, confirm)` | `return_book` | **写操作**，同上 |
| `reserve_book(book_id, confirm)` | `reserve_book` | **写操作**，同上 |

**人在回路的 MCP 版本**：MCP 客户端没有浏览器确认卡，所以写工具用 `confirm` 参数实现两段式——
先 `confirm=false` 返回预览（包含要执行的 id），用户同意后客户端再以 `confirm=true` 调用。

---

## 2. 鉴权模型（和 Web 端一致的原则）

- MCP 客户端没有用户登录态，服务用环境变量 **`MCP_USER_TOKEN`** 提供"调用者身份"，原样转发给 Java 内部接口
  （`Authorization: Bearer <token>` + `X-Internal-Token`）；
- 服务端仍按 token 解析用户，**LLM/客户端无法指定 userId**；
- 未设置 token 时：`search_knowledge` 仍可用（不依赖用户身份），其它工具返回清晰错误。

---

## 3. 运行

```bash
# 1) 自检（不需要任何客户端）：列出 7 个工具 + 调一次知识检索
ai-service/.venv/bin/python ai-service/scripts/mcp_server.py --selftest

# 2) 以 stdio 方式运行（给 Claude Desktop / IDE 用）
MCP_USER_TOKEN=<读者token> ai-service/.venv/bin/python ai-service/scripts/mcp_server.py
```

Claude Desktop 配置（`~/Library/Application Support/Claude/claude_desktop_config.json`）：

```json
{
  "mcpServers": {
    "library-ai": {
      "command": "/绝对路径/ai-service/.venv/bin/python",
      "args": ["/绝对路径/ai-service/scripts/mcp_server.py"],
      "env": {
        "MCP_USER_TOKEN": "从 /api/auth/login 获取的读者 token",
        "JAVA_API_BASE": "http://127.0.0.1:18080",
        "AI_INTERNAL_TOKEN": "dev-internal-token"
      }
    }
  }
}
```

token 获取：

```bash
curl -s -X POST http://127.0.0.1:18080/api/auth/login \
  -H 'Content-Type: application/json' -d '{"username":"reader","password":"<密码>"}' \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["data"]["token"])'
```

---

## 4. 已验证范围（如实）

| 项 | 状态 |
|---|---|
| 工具注册（7 个，名称/描述正确） | ✅ `--selftest` 实测 |
| `search_knowledge` 真实检索（命中规则块，score 0.87） | ✅ 实测 |
| 写工具的 `confirm` 两段式语义 | ✅ 代码路径（未接真实客户端点按） |
| 与 Claude Desktop / 官方 Inspector 联调 | ⚠️ **未验证**（本机没有 MCP 客户端；协议由官方 SDK 实现） |
| SSE / streamable-http transport | ✅ SDK 支持（`--transport` 参数），未做联调 |

---

## 5. 与 Web 端的关系

- **同一套工具语义**：Web 端 Agent 与 MCP 端共享 `app/tools/schemas.py` 的定义思路（名称/参数/读写属性一致）；
- **同一套业务安全边界**：都通过 Java 内部接口、都按 token 识别身份、写操作都需要"再确认"；
- 区别只在**确认载体**：Web 用确认卡片，MCP 用 `confirm` 参数。

> 面试表述："我把工具层做成了 MCP Server，7 个能力可以被任何 Agent 客户端复用；
> 写操作用 confirm 两段式保持人在回路。协议用官方 SDK 实现，我验证了工具注册和知识检索；
> 没接真实客户端联调，这点我会如实说明。"
