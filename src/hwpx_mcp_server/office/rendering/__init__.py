# SPDX-License-Identifier: Apache-2.0
"""Canonical Hancom rendering owner for the MCP application layer.

The package owns runtime discovery, renderer execution, serialized worker
policy, fixture orchestration, and visual-QA measurement.  ``python-hwpx``
supplies only renderer-neutral contracts and deterministic geometry helpers.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from hwpx.quality import SavePipeline
from hwpx.quality.rendering import EditMask, VisualReport

from .oracle import (
    MacHancomOracle,
    NullOracle,
    RenderBackend,
    RenderOracle,
    WindowsComOracle,
    resolve_oracle,
    visual_check,
)


class HancomRenderBackend:
    """Adapt a discovered Hancom render transport to the core check protocol."""

    def __init__(self, oracle: Any) -> None:
        self._oracle = oracle

    def available(self) -> bool:
        return bool(self._oracle is not None and self._oracle.available())

    def check(
        self,
        before_hwpx: str | None,
        after_hwpx: str,
        *,
        edit_mask: EditMask | None = None,
        diff_eps: float = 0.005,
        dpi: int = 150,
        work_dir: str | None = None,
        keep_artifacts: bool = False,
    ) -> VisualReport:
        return visual_check(
            before_hwpx,
            after_hwpx,
            oracle=self._oracle,
            edit_mask=edit_mask,
            diff_eps=diff_eps,
            dpi=dpi,
            work_dir=work_dir,
            keep_artifacts=keep_artifacts,
        )


def resolve_hancom_backend(*, dpi: int = 150, **_options: Any) -> HancomRenderBackend:
    """Discover Hancom at the application boundary and return a core backend."""

    return HancomRenderBackend(resolve_hancom_oracle(dpi=dpi))


def resolve_hancom_oracle(*, dpi: int = 150, **_options: Any) -> Any:
    """Return the canonical Hancom transport at the MCP boundary."""

    return resolve_oracle(dpi=dpi)


@contextmanager
def bind_document_rendering(document: Any) -> Iterator[None]:
    """Temporarily bind MCP-owned rendering to one core document save."""

    sentinel = object()
    previous = getattr(document, "_save_pipeline", sentinel)
    document._save_pipeline = SavePipeline(oracle_factory=resolve_hancom_backend)
    try:
        yield
    finally:
        if previous is sentinel:
            delattr(document, "_save_pipeline")
        else:
            document._save_pipeline = previous


__all__ = [
    "HancomRenderBackend",
    "MacHancomOracle",
    "NullOracle",
    "RenderBackend",
    "RenderOracle",
    "WindowsComOracle",
    "bind_document_rendering",
    "resolve_hancom_backend",
    "resolve_hancom_oracle",
    "resolve_oracle",
    "visual_check",
]
