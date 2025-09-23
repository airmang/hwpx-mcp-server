"""Schema sanitizer ensuring draft-07 safe output."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple


class SchemaSanitizer:
    """Normalize JSON schema produced by pydantic models."""

    _DROP_KEYS = {
        "$schema",
        "$id",
        "title",
        "description",
        "examples",
        "default",
    }
    _FORBIDDEN_KEYS = {"$defs", "definitions", "if", "then", "else"}
    _FORBIDDEN_PREFIXES = ("dependent", "unevaluated")

    def __init__(self, schema: Dict[str, Any]):
        if not isinstance(schema, dict):
            raise TypeError("schema must be a mapping")
        self._raw_schema = schema
        self._ref_cache: Dict[str, Dict[str, Any]] = {}
        self._resolving: set[str] = set()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def sanitize(self) -> Dict[str, Any]:
        sanitized, _ = self._sanitize_schema(self._raw_schema)
        if not isinstance(sanitized, dict):
            raise TypeError("sanitized schema must be a mapping")
        sanitized.pop("$defs", None)
        sanitized.pop("definitions", None)
        sanitized = self._ensure_object_shape(sanitized)
        return sanitized

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _sanitize_schema(self, node: Any) -> Tuple[Any, bool]:
        if isinstance(node, dict):
            working = dict(node)
            optional = False

            for key in list(working.keys()):
                if key in self._DROP_KEYS or key in self._FORBIDDEN_KEYS:
                    working.pop(key, None)
                elif key.startswith(self._FORBIDDEN_PREFIXES):
                    working.pop(key, None)

            # allOf merging keeps flat schema objects
            if "allOf" in working:
                merged: Dict[str, Any] = {}
                parts = working.pop("allOf")
                if isinstance(parts, list):
                    for part in parts:
                        sanitized_part, part_optional = self._sanitize_schema(part)
                        optional = optional or part_optional
                        if isinstance(sanitized_part, dict):
                            merged = self._merge_schema_dicts(merged, sanitized_part)
                working = self._merge_schema_dicts(merged, working)

            # Handle anyOf/oneOf optional unions
            for union_key in ("anyOf", "oneOf"):
                if union_key in working:
                    options = working.pop(union_key)
                    sanitized_options: List[Any] = []
                    found_null = False
                    if isinstance(options, list):
                        for option in options:
                            sanitized_option, opt_optional = self._sanitize_schema(option)
                            optional = optional or opt_optional
                            if self._is_null_schema(sanitized_option):
                                found_null = True
                            else:
                                sanitized_options.append(sanitized_option)
                    if not sanitized_options:
                        working.pop("type", None)
                    elif found_null and len(sanitized_options) == 1:
                        base_schema = sanitized_options[0]
                        if isinstance(base_schema, dict):
                            working = self._merge_schema_dicts(working, base_schema)
                        else:
                            working = base_schema
                        optional = True
                    elif len(sanitized_options) == 1:
                        base_schema = sanitized_options[0]
                        if isinstance(base_schema, dict):
                            working = self._merge_schema_dicts(working, base_schema)
                        else:
                            working = base_schema
                    else:
                        collapsed_type = self._collapse_simple_types(sanitized_options)
                        if collapsed_type is not None:
                            working["type"] = collapsed_type
                        else:
                            raise ValueError("complex schema unions are not supported")

            if "$ref" in working:
                ref = working.pop("$ref")
                resolved = self._resolve_ref(ref)
                working = self._merge_schema_dicts(resolved, working)

            type_value = working.get("type")
            if isinstance(type_value, list):
                cleaned = [item for item in type_value if item != "null"]
                if len(cleaned) != len(type_value):
                    optional = True
                if not cleaned:
                    working.pop("type", None)
                elif len(cleaned) == 1:
                    working["type"] = cleaned[0]
                else:
                    raise ValueError("multiple primitive types are not supported")

            if "const" in working:
                const_value = working.pop("const")
                if "enum" not in working:
                    working["enum"] = [const_value]

            optional_props: set[str] = set()
            properties_value = working.get("properties")
            if isinstance(properties_value, dict):
                sanitized_properties: Dict[str, Any] = {}
                for prop_name in sorted(properties_value.keys()):
                    prop_schema = properties_value[prop_name]
                    sanitized_prop, prop_optional = self._sanitize_schema(prop_schema)
                    optional_props.update({prop_name} if prop_optional else set())
                    sanitized_properties[prop_name] = sanitized_prop
                working["properties"] = sanitized_properties
            else:
                working.pop("properties", None)

            required_value = working.get("required")
            required_items: List[str] = []
            if isinstance(required_value, list):
                seen: set[str] = set()
                for item in required_value:
                    if not isinstance(item, str):
                        continue
                    if item in optional_props or item in seen:
                        continue
                    seen.add(item)
                    required_items.append(item)
            required_items = sorted(required_items)
            if required_items:
                working["required"] = required_items
            else:
                working.pop("required", None)

            for key, value in list(working.items()):
                if key in {"properties", "required"}:
                    continue
                sanitized_value, child_optional = self._sanitize_schema(value)
                optional = optional or child_optional
                working[key] = sanitized_value

            if self._is_object_schema(working):
                working = self._ensure_object_shape(working)
            else:
                working.pop("required", None)

            return working, optional

        if isinstance(node, list):
            sanitized_items: List[Any] = []
            for item in node:
                sanitized_item, _ = self._sanitize_schema(item)
                sanitized_items.append(sanitized_item)
            return sanitized_items, False

        return node, False

    def _collapse_simple_types(self, options: List[Any]) -> Optional[str]:
        simple_types: List[str] = []
        for option in options:
            if not isinstance(option, dict):
                return None
            type_value = option.get("type")
            if not isinstance(type_value, str):
                return None
            if len(option) > 1:
                return None
            simple_types.append(type_value)
        if not simple_types:
            return None
        for preferred in ("string", "number", "integer", "boolean"):
            if preferred in simple_types:
                return preferred
        return simple_types[0]

    def _merge_schema_dicts(self, base: Dict[str, Any], overlay: Any) -> Dict[str, Any]:
        if not isinstance(overlay, dict):
            return self._clone(base)
        result = self._clone(base)
        for key, value in overlay.items():
            if key == "properties" and isinstance(value, dict):
                existing = result.get("properties")
                if isinstance(existing, dict):
                    merged = existing.copy()
                    merged.update(value)
                    result["properties"] = merged
                else:
                    result["properties"] = self._clone(value)
            elif key == "required" and isinstance(value, list):
                existing_required = result.get("required")
                merged_required = []
                if isinstance(existing_required, list):
                    merged_required.extend(existing_required)
                for item in value:
                    if isinstance(item, str) and item not in merged_required:
                        merged_required.append(item)
                result["required"] = merged_required
            else:
                result[key] = self._clone(value)
        return result

    def _resolve_ref(self, ref: Any) -> Dict[str, Any]:
        if not isinstance(ref, str):
            raise TypeError("schema reference must be a string")
        cached = self._ref_cache.get(ref)
        if cached is not None:
            return self._clone(cached)
        if ref in self._resolving:
            raise ValueError(f"circular schema reference detected for {ref}")
        target = self._resolve_pointer(self._raw_schema, ref)
        self._resolving.add(ref)
        sanitized_target, _ = self._sanitize_schema(target)
        self._resolving.remove(ref)
        if not isinstance(sanitized_target, dict):
            raise TypeError("referenced schema must resolve to a mapping")
        self._ref_cache[ref] = sanitized_target
        return self._clone(sanitized_target)

    def _resolve_pointer(self, schema: Any, pointer: str) -> Any:
        if pointer == "#":
            return schema
        if not pointer.startswith("#/"):
            raise ValueError(f"unsupported schema reference: {pointer}")
        parts = pointer[2:].split("/")
        current = schema
        for raw_part in parts:
            part = raw_part.replace("~1", "/").replace("~0", "~")
            if isinstance(current, dict):
                if part not in current:
                    raise KeyError(f"cannot resolve pointer {pointer}")
                current = current[part]
            elif isinstance(current, list):
                index = int(part)
                current = current[index]
            else:
                raise KeyError(f"cannot resolve pointer {pointer}")
        return current

    def _ensure_object_shape(self, schema: Dict[str, Any]) -> Dict[str, Any]:
        result = dict(schema)
        result["type"] = "object"
        props = result.get("properties")
        if not isinstance(props, dict):
            props = {}
        ordered_props = {key: props[key] for key in sorted(props.keys())}
        result["properties"] = ordered_props
        required = result.get("required")
        if isinstance(required, list):
            filtered = [item for item in required if isinstance(item, str)]
        else:
            filtered = []
        result["required"] = sorted(dict.fromkeys(filtered))
        result["additionalProperties"] = False
        return result

    def _clone(self, value: Any) -> Any:
        if isinstance(value, dict):
            return {k: self._clone(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self._clone(item) for item in value]
        return value

    @staticmethod
    def _is_null_schema(schema: Any) -> bool:
        return isinstance(schema, dict) and schema.get("type") == "null" and len(schema) == 1

    @staticmethod
    def _is_object_schema(schema: Dict[str, Any]) -> bool:
        if not isinstance(schema, dict):
            return False
        if schema.get("type") == "object":
            return True
        if "properties" in schema:
            return True
        if "required" in schema:
            return True
        if "additionalProperties" in schema:
            return True
        return False
