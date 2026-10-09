# syntax=docker/dockerfile:1

# ---- 阶段 1：构建前端 ----
FROM node:22-alpine AS frontend
WORKDIR /build
COPY webui/package.json webui/package-lock.json ./
RUN npm ci
COPY webui/ ./
RUN npm run build

# ---- 阶段 2：运行时 ----
FROM python:3.12-slim
WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# 核心层 + Web 后端依赖
COPY requirements.txt ./requirements.txt
COPY web/requirements.txt ./web-requirements.txt
RUN pip install --no-cache-dir -r requirements.txt -r web-requirements.txt

# 应用代码与前端构建产物（路径与 web/app/config.py 的 REPO_ROOT 对齐）
COPY core ./core
COPY web ./web
COPY --from=frontend /build/dist ./webui/dist

EXPOSE 8000

CMD ["uvicorn", "web.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
