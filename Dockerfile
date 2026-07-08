# syntax=docker/dockerfile:1
FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HWPX_MCP_PROFILE=playmcp \
    HWPX_MCP_TRANSPORT=streamable-http \
    HWPX_MCP_HOST=0.0.0.0 \
    HWPX_MCP_PORT=8000 \
    HWPX_MCP_SANDBOX_ROOT=/tmp/hwpx-playmcp

WORKDIR /app

RUN python -m pip install --upgrade pip setuptools wheel

COPY pyproject.toml README.md LICENSE NOTICE ./
COPY src ./src

RUN pip install . \
    && mkdir -p "$HWPX_MCP_SANDBOX_ROOT" \
    && useradd --create-home --shell /usr/sbin/nologin appuser \
    && chown -R appuser:appuser /app "$HWPX_MCP_SANDBOX_ROOT"

USER appuser

EXPOSE 8000

CMD ["hwpx-mcp-server", "--transport", "streamable-http", "--host", "0.0.0.0", "--port", "8000"]
