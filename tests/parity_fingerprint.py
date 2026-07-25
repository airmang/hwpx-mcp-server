# SPDX-License-Identifier: Apache-2.0
"""Structural fingerprints of a module's public surface.

``fingerprint(module)`` captures the *shape* of a module's public API — the
names it exports, whether each is a function, class, dataclass, or simple
constant, and (for callables) their signatures — as a plain, sorted,
JSON-serialisable ``dict``. It does not capture behaviour: calling a function
twice with the same arguments is not part of the fingerprint, only the fact
that the function exists and what it is called with.

This exists so that "does the MCP owner still match core's public shape" can
be checked after core's copy is deleted: freeze core's fingerprint once
(``scripts/freeze_parity_fingerprints.py``) while the module still exists,
commit the JSON, and compare the live MCP module's fingerprint against the
frozen record forever after.

Deliberately excluded from scope: return values, side effects, raised
exceptions, non-dataclass ``__init__`` signatures (a plain class's
constructor is not part of its fingerprint — only its dataclass fields are,
when it is a dataclass), and anything requiring the module to be *called*
rather than merely *introspected*.
"""

from __future__ import annotations

import dataclasses
import inspect
from types import ModuleType
from typing import Any

__all__ = ["fingerprint"]

_SIMPLE_CONSTANT_TYPES = (str, int, float, bool)


def fingerprint(module: ModuleType) -> dict[str, Any]:
    """Return a canonical, deterministic, JSON-serialisable shape of ``module``.

    Keyed by public top-level name (no leading underscore), sorted. Each
    entry records a ``kind`` of ``"function"``, ``"class"``, ``"dataclass"``,
    or ``"constant"`` and kind-specific detail (see module docstring).

    Names re-exported from *other* modules are included exactly as declared
    in ``module.__all__`` when present — an explicit ``__all__`` is the
    module's own curated public contract, and aggregating re-exports from
    submodules into a package ``__init__`` is normal, not leakage. When a
    module has no ``__all__``, public names are instead discovered by
    scanning its namespace and keeping only names whose value's
    ``__module__`` equals this module's ``__name__`` (functions/classes) or
    that are simple/JSON-safe constants — so a bare ``import re`` or
    ``from dataclasses import dataclass`` sitting in the namespace does not
    leak another module's surface in.
    """

    names = _public_names(module)
    return {name: _describe(getattr(module, name)) for name in names}


def _public_names(module: ModuleType) -> list[str]:
    declared = getattr(module, "__all__", None)
    if declared is not None:
        return sorted(declared)
    members = vars(module)
    return sorted(
        name
        for name in members
        if not name.startswith("_") and _is_owned_member(members[name], module)
    )


def _is_owned_member(value: Any, module: ModuleType) -> bool:
    if _is_constant(value) or _is_dataclass_instance(value):
        return True
    owner = getattr(value, "__module__", None)
    return owner == module.__name__


def _is_dataclass_instance(value: Any) -> bool:
    """A module-level constant that is an *instance* of a dataclass (e.g. a
    default policy object), as opposed to the dataclass type itself.

    ``isinstance(value, type)`` in ``_describe`` already routes the type
    itself to ``_describe_class``; this catches the separate case of a bound
    instance sitting at module level as a constant.
    """

    return dataclasses.is_dataclass(value) and not isinstance(value, type)


def _describe(value: Any) -> dict[str, Any]:
    if isinstance(value, type):
        return _describe_class(value)
    if _is_constant(value) or _is_dataclass_instance(value):
        return {"kind": "constant", "value": _canonicalize(value)}
    # Functions, bound/unbound methods, staticmethod/classmethod descriptors
    # accessed off a module (already unwrapped by attribute lookup), and any
    # other callable land here as "function".
    return {"kind": "function", "signature": _signature_of(value)}


def _describe_class(cls: type) -> dict[str, Any]:
    is_dataclass = dataclasses.is_dataclass(cls)
    entry: dict[str, Any] = {
        "kind": "dataclass" if is_dataclass else "class",
        "bases": sorted(base.__name__ for base in cls.__bases__ if base is not object),
        "methods": _public_methods(cls),
    }
    if is_dataclass:
        entry["fields"] = _dataclass_fields(cls)
    return entry


def _public_methods(cls: type) -> dict[str, str]:
    members = vars(cls)
    methods: dict[str, str] = {}
    for name in members:
        if name.startswith("_"):
            continue
        value = members[name]
        if isinstance(value, property):
            methods[name] = _property_signature(value)
            continue
        # getattr (rather than raw __dict__ access) makes attribute lookup
        # unwrap staticmethod/classmethod descriptors into plain callables
        # so inspect.signature reports the caller-visible signature.
        resolved = getattr(cls, name)
        if inspect.isroutine(resolved) or callable(resolved):
            methods[name] = _signature_of(resolved)
    return dict(sorted(methods.items()))


def _dataclass_fields(cls: type) -> dict[str, dict[str, Any]]:
    fields: dict[str, dict[str, Any]] = {}
    for field in dataclasses.fields(cls):
        if field.name.startswith("_"):
            continue  # internal bookkeeping field, not part of the public shape
        has_default = (
            field.default is not dataclasses.MISSING
            or field.default_factory is not dataclasses.MISSING  # type: ignore[misc]
        )
        fields[field.name] = {
            "type": str(field.type),
            "hasDefault": has_default,
        }
    return dict(sorted(fields.items()))


def _property_signature(value: property) -> str:
    getter = value.fget
    if getter is None:
        return "property"
    try:
        return "property" + str(inspect.signature(getter))
    except (TypeError, ValueError):
        return "property"


def _signature_of(value: Any) -> str:
    try:
        return str(inspect.signature(value))
    except (TypeError, ValueError):
        return "<signature unavailable>"


def _is_constant(value: Any) -> bool:
    if value is None or isinstance(value, _SIMPLE_CONSTANT_TYPES):
        return True
    # list/tuple/dict are always representable: _canonicalize degrades safely
    return isinstance(value, (list, tuple, dict))


def _canonicalize(value: Any) -> Any:
    """Reduce ``value`` to a JSON-safe, deterministic shape.

    Simple scalars pass through. Containers recurse. Anything else
    (typically a callable sitting inside a constant tuple/list/dict, e.g. a
    registry of detector functions) becomes a stable descriptive marker
    rather than being silently dropped, so a rename or signature change
    still shows up as a fingerprint diff.
    """

    if value is None or isinstance(value, _SIMPLE_CONSTANT_TYPES):
        return value
    if isinstance(value, (list, tuple)):
        return [_canonicalize(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _canonicalize(val) for key, val in sorted(value.items(), key=lambda kv: str(kv[0]))}
    if _is_dataclass_instance(value):
        return {
            "$dataclass": type(value).__name__,
            "fields": {
                field.name: _canonicalize(getattr(value, field.name))
                for field in sorted(dataclasses.fields(value), key=lambda f: f.name)
            },
        }
    if callable(value):
        name = getattr(value, "__name__", type(value).__name__)
        return f"<callable {name}{_signature_of(value)}>"
    return f"<{type(value).__name__}>"
