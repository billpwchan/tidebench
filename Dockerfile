FROM node:24-bookworm-slim AS web
WORKDIR /build/frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
COPY examples/ /build/examples/
RUN npm run build

FROM ghcr.io/astral-sh/uv:0.12.21 AS uv
FROM python:3.13-slim-bookworm AS runtime
COPY --from=uv /uv /uvx /bin/
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY backend ./backend
RUN uv sync --frozen --no-dev
COPY --from=web /build/frontend/dist ./frontend/dist
RUN groupadd --gid 10001 tidebench && useradd --uid 10001 --gid tidebench --no-create-home tidebench \
    && mkdir -p /app/data && chown -R tidebench:tidebench /app/data
USER tidebench
ENV PATH="/app/.venv/bin:$PATH" TIDEBENCH_DATA_DIR="/app/data" TIDEBENCH_BIND="0.0.0.0" TIDEBENCH_PORT="8000"
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3)"
CMD ["python", "-m", "tidebench"]
