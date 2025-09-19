"""Logging helpers for the HWPX MCP server."""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime
from typing import Any, Dict

_DEFAULT_LEVEL = "INFO"


class JsonFormatter(logging.Formatter):
    """Simple JSON-line formatter."""

    def format(self, record: logging.LogRecord) -> str:  # pragma: no cover - trivial
        payload: Dict[str, Any] = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "level": record.levelname,
            "message": record.getMessage(),
            "logger": record.name,
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        for name, value in record.__dict__.items():
            if name in payload:
                continue
            if name.startswith("_"):
                continue
            if name in {"args", "msg", "levelname", "levelno", "pathname", "lineno", "funcName", "created", "msecs", "relativeCreated", "thread", "threadName", "processName", "process"}:
                continue
            payload[name] = value
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(level_name: str | None = None) -> None:
    """Configure process-wide logging for structured output."""

    level_text = (level_name or os.getenv("LOG_LEVEL") or _DEFAULT_LEVEL).upper()
    try:
        level = getattr(logging, level_text)
    except AttributeError:
        level = logging.INFO

    root = logging.getLogger()
    root.setLevel(level)
    root.handlers[:] = []

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)

    logging.getLogger("hwpx_mcp_server").setLevel(level)
