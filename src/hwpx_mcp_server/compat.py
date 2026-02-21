"""외부 라이브러리 호환성 패치."""

from xml.etree import ElementTree as _ET
import builtins


def patch_python_hwpx() -> None:
    """python-hwpx의 ET 누락 버그를 보정합니다."""
    if not hasattr(builtins, "ET"):
        builtins.ET = _ET
