# syntax=docker/dockerfile:1
FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HWPX_AUTOMATION_PROFILE=playmcp \
    HWPX_AUTOMATION_TRANSPORT=streamable-http \
    HWPX_AUTOMATION_HOST=0.0.0.0 \
    HWPX_AUTOMATION_PORT=8000 \
    HWPX_AUTOMATION_WORKSPACE_ROOTS='["/tmp/hwpx-playmcp"]'

WORKDIR /app

RUN python -m pip install --upgrade pip setuptools wheel

COPY pyproject.toml README.md LICENSE NOTICE ./
COPY src ./src

RUN pip install ".[mcp,http]" \
    && mkdir -p /tmp/hwpx-playmcp \
    && useradd --create-home --shell /usr/sbin/nologin appuser \
    && chown -R appuser:appuser /app /tmp/hwpx-playmcp

USER appuser

EXPOSE 8000

CMD ["hwpx-automation-mcp", "--transport", "streamable-http", "--host", "0.0.0.0", "--port", "8000"]
