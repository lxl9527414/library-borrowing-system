# 公网 AI 服务部署说明（CentOS 7 / 2C1.76G）

> 记录 2026-11-02 在 `lxlssr.icu`（阿里云 ECS，CentOS 7.9）上完成 AI 服务部署的**真实过程与坑**。
> 照着本文可以从零复现；日常更新只需要 `./deploy/push-ai.sh`。

---

## 1. 部署形态

```
              Cloudflare
                  │
             nginx :80
        ┌─────────┴──────────┐
        │                    │
  /（SPA + /api/**）     /api/ai/**
        │                    │
  Spring Boot :18080    FastAPI :8000（127.0.0.1）
        │                    │
        ├──── MySQL :3306 ◄──┘（AI 不直连，走 18080 内部接口）
        └──── Redis :6379（会话 / 确认状态）
```

| 项 | 值 |
|---|---|
| 服务器 | 阿里云 ECS，CentOS 7.9，2 vCPU / 1,756 MB，glibc **2.17** |
| Python | 3.11.13（Miniconda 环境 `/opt/miniconda3/envs/ai`） |
| 代码目录 | `/opt/library/ai-service` |
| 配置 | `/opt/library/config/ai.env`（600） |
| systemd | `library-ai.service`（`MemoryLimit=600M` 保护 Java/MySQL） |
| 反代 | nginx `/api/ai/` → `127.0.0.1:8000`，关闭 `proxy_buffering`（SSE 必须） |
| 安全 | `/api/ai/internal/` 公网直接 404；后端内部接口还要求 `X-Internal-Token` + 用户 token |

---

## 2. 首次部署步骤（已在服务器执行过，供重装参考）

### 2.1 加 swap（1.76G 内存必需）

```bash
dd if=/dev/zero of=/swapfile bs=1M count=1024 status=none
chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
echo '/swapfile none swap sw 0 0' >> /etc/fstab
```

### 2.2 装 Python 3.11（**不要用最新 Miniconda**）

> 坑：`Miniconda3-py311_26.x` 要求 **glibc ≥ 2.28**，CentOS 7 只有 2.17 → 装完目录为空。
> 必须用 24.x 系列（本文用 `24.9.2-0`）。

```bash
curl -fsSL -o /tmp/miniconda.sh \
  https://mirrors.tuna.tsinghua.edu.cn/anaconda/miniconda/Miniconda3-py311_24.9.2-0-Linux-x86_64.sh
bash /tmp/miniconda.sh -b -p /opt/miniconda3
/opt/miniconda3/bin/conda create -y -p /opt/miniconda3/envs/ai python=3.11
```

### 2.3 装依赖（**两个版本必须锁**）

> 坑 1：`numpy>=2` 在 glibc 2.17 没有预编译 wheel，pip 会尝试源码编译并因缺 g++ 失败 → 锁 `numpy<2`（1.26.4）。
> 坑 2：`tiktoken>=0.9` 同样没有兼容 wheel，需要 Rust 编译 → 锁 `tiktoken==0.8.0`（有 manylinux2014 wheel）。

```bash
/opt/miniconda3/envs/ai/bin/pip install -q \
  -i https://mirrors.aliyun.com/pypi/simple/ --trusted-host mirrors.aliyun.com \
  "numpy>=1.26,<2" "tiktoken==0.8.0" \
  "fastapi>=0.115" "uvicorn[standard]>=0.30" "sse-starlette>=2.1" \
  "pydantic>=2.7" "pydantic-settings>=2.3" "python-dotenv>=1.0" \
  "langgraph>=1.0" "langgraph-checkpoint>=4.0" "langchain-core>=1.0" "langchain-openai>=1.0" \
  "openai>=1.40" "httpx>=0.27" "redis>=5.0" "pymysql>=1.1"
```

### 2.4 上传代码

```bash
./deploy/push-ai.sh --skip-restart      # 首次也用它上传
chown -R library:library /opt/library/ai-service
```

### 2.5 配置文件 `/opt/library/config/ai.env`（600，不要入库）

从 `deploy/ai.env.example` 复制并填入：
- `DEEPSEEK_API_KEY`（生产 Key）
- `DASHSCOPE_API_KEY`（向量检索查询时用）
- `AI_INTERNAL_TOKEN`（随机串，**必须与后端 `library.env` 同名变量一致**）
- `AI_REDIS_URL=redis://:<redis密码>@127.0.0.1:6379/1`
- 其余：`JAVA_API_BASE=http://127.0.0.1:18080`、`CORS_ORIGINS=https://lxlssr.icu`

### 2.6 systemd

```bash
install -m 0644 deploy/systemd/library-ai.service /etc/systemd/system/
systemctl daemon-reload && systemctl enable --now library-ai
```

### 2.7 后端加内部 token（重要）

```bash
TOKEN=$(grep '^AI_INTERNAL_TOKEN=' /opt/library/config/ai.env | cut -d= -f2-)
grep -q '^AI_INTERNAL_TOKEN=' /opt/library/config/library.env \
  || echo "AI_INTERNAL_TOKEN=${TOKEN}" >> /opt/library/config/library.env
systemctl restart library-server
```

> 不加这一行，内部接口只靠用户 token 保护（任何读者都能直接调），等于绕过"写操作确认"。

### 2.8 nginx

在 `/etc/nginx/conf.d/library.conf` 的 `location / {` 前加两段（完整配置见 `deploy/nginx/library.conf`）：

```nginx
location /api/ai/ {
    proxy_pass http://127.0.0.1:8000;
    proxy_http_version 1.1;
    proxy_buffering off;        # SSE 必须关，否则 token 攒着一起返回
    proxy_cache off;
    proxy_set_header Connection "";
    proxy_read_timeout 300s;
    add_header X-Accel-Buffering no always;
}
location /api/ai/internal/ { return 404; }
```

```bash
nginx -t && systemctl reload nginx
```

---

## 3. 日常更新

```bash
./deploy/push-ai.sh          # 打包 → 上传 → 重启 → 健康自检（含内部接口 404 校验）
```

---

## 4. 验证清单（部署后逐项）

```bash
# 1) 公网健康（应返回 provider=deepseek / sessionStore=redis）
curl -s https://lxlssr.icu/api/ai/health

# 2) 内部接口必须被 nginx 挡掉
curl -s -o /dev/null -w '%{http_code}\n' https://lxlssr.icu/api/ai/internal/context    # 期望 404

# 3) 后端内部接口的分层鉴权（服务器本机）
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:18080/api/ai/internal/context                        # 期望 403（缺内部 token）
curl -s -o /dev/null -w '%{http_code}\n' -H "X-Internal-Token: $TOKEN" http://127.0.0.1:18080/api/ai/internal/context  # 期望 401（缺用户 token）

# 4) 端到端对话（用读者账号，SSE 应依次出现 meta/token/tool_call/tool_result/done）
#    见 scripts/e2e_ai.py；把 --ai 换成 https://lxlssr.icu 即可
```

---

## 5. 回滚

| 场景 | 命令 |
|---|---|
| AI 服务代码回滚 | `ssh ... 'rm -rf /opt/library/ai-service && mv /opt/library/ai-service.previous /opt/library/ai-service && systemctl restart library-ai'` |
| 关闭 AI 服务 | `systemctl disable --now library-ai`（前端对话页会显示"AI 服务未连接"，业务不受影响） |
| 后端 jar 回滚 | `./deploy/push.sh --rollback` |

---

## 6. 已知限制（如实）

- **checkpointer 内存降级**：未安装 `langgraph-checkpoint-redis`（其依赖 RediSearch 模块，本机 Redis 不带）→ 会话历史在 Redis 正常，但"图执行中断恢复"跨重启不保留；
- **内存偏紧**：Python 常驻约 120–200 MB，已用 `MemoryLimit=600M` 兜底；高并发需要升配；
- **SSE 经过 Cloudflare**：实测正常；若以后出现"回复一次性出现"，先查 nginx `proxy_buffering` 与 Cloudflare 代理设置；
- **重排默认关闭**：评测显示向量单独效果最好（见 `docs/RAG评测报告.md`）。
