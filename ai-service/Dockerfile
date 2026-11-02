# 云阁图书馆 AI 服务镜像（W1：Agent 主链路 + SSE + 写操作确认）
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    TZ=Asia/Shanghai

WORKDIR /app

# 依赖层单独缓存；国内网络走阿里云镜像
COPY requirements.txt ./
RUN pip install --no-cache-dir \
        -i https://mirrors.aliyun.com/pypi/simple/ \
        --trusted-host mirrors.aliyun.com \
        -r requirements.txt

COPY app ./app

EXPOSE 8000

# 与 docker-compose.yml 的健康检查保持一致（容器内无 curl，用标准库实现）
HEALTHCHECK --interval=10s --timeout=5s --start-period=15s --retries=10 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/ai/health')"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
