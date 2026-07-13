"""Private corpus practice storage and execution boundary."""

from typing import Any

from .scenario_service import PracticeScenarioError, PracticeScenarioService

__all__ = [
    "PracticeRegistryAuthenticationError",
    "PracticeRegistryNotFound",
    "PracticeRegistryStore",
    "PracticeScenarioError",
    "PracticeScenarioService",
]


def __getattr__(name: str) -> Any:
    """Load the registry backend only when that optional surface is requested.

    The high-level scenario tools can run against an opaque runner manifest and
    must remain importable with older ``python-hwpx`` installations that do not
    yet provide the private registry package.
    """

    if name in {
        "PracticeRegistryAuthenticationError",
        "PracticeRegistryNotFound",
        "PracticeRegistryStore",
    }:
        from . import store

        return getattr(store, name)
    raise AttributeError(name)
