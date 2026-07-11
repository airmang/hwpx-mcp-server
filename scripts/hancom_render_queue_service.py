#!/usr/bin/env python3
"""Run the private authenticated render queue endpoint on the licensed worker host."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from hwpx_mcp_server.workflow.render_queue import DurableRenderQueue
from hwpx_mcp_server.workflow.render_security import RenderSecurityPolicy
from hwpx_mcp_server.workflow.render_transport import serve_private_queue


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--cert", type=Path)
    parser.add_argument("--key", type=Path)
    parser.add_argument("--secret-env", default="HWPX_RENDER_QUEUE_SECRET")
    parser.add_argument("--allow-insecure-loopback", action="store_true", help="tests only")
    args = parser.parse_args()
    secret = os.environ.get(args.secret_env)
    if not secret:
        parser.error(f"secret environment variable {args.secret_env} is required")
    policy = RenderSecurityPolicy(sandbox_root=args.root.resolve() / "sandboxes")
    queue = DurableRenderQueue(args.root, secret=secret.encode(), policy=policy)
    server = serve_private_queue(
        queue, secret=secret.encode(), host=args.host, port=args.port,
        certfile=args.cert, keyfile=args.key,
        allow_insecure_loopback=args.allow_insecure_loopback,
    )
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
