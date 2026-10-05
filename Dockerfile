# syntax=docker/dockerfile:1

# ---- 运行时镜像 -------------------------------------------------------------
FROM python:3.11-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# 非 root 运行
RUN useradd --create-home --uid 10001 appuser

COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY app ./app

USER appuser

EXPOSE 8000

# 容器内固定监听 8000；宿主机映射端口由 Compose 的 HOST_PORT 配置。
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=5 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3).status == 200 else 1)"

# ---- 一次性核验镜像（compose 服务 verify 使用） -----------------------------
FROM runtime AS verify

USER root
COPY requirements-dev.txt ./
RUN pip install -r requirements-dev.txt
COPY tests ./tests
COPY scripts ./scripts
USER appuser

CMD ["python", "scripts/verify.py"]
