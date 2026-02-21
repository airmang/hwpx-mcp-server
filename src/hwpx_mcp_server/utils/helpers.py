import os

MAX_CHARS = int(os.environ.get("HWPX_MCP_MAX_CHARS", "10000"))


def resolve_path(filename: str) -> str:
    if os.path.isabs(filename):
        return filename
    return os.path.abspath(filename)


def truncate_response(text: str, max_chars: int = None) -> dict:
    if max_chars is None:
        max_chars = MAX_CHARS
    total = len(text)
    if total <= max_chars:
        return {"text": text, "total_chars": total, "truncated": False}
    return {
        "text": text[:max_chars],
        "total_chars": total,
        "truncated": True,
    }
