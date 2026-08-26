from __future__ import annotations

from typing import Any

from repopilot.tools.definitions import PUBLIC_TOOL_BY_NAME, provider_tool_schemas


# Backward-compatible provider view, generated from the canonical catalog.
TOOL_SCHEMAS: list[dict[str, Any]] = provider_tool_schemas()


def validate_tool_arguments(name: str, arguments: dict[str, Any]) -> str | None:
    definition = PUBLIC_TOOL_BY_NAME.get(name)
    if definition is None:
        return f"unknown tool: {name}"
    parameters = definition.input_schema
    properties = parameters.get("properties", {})
    for required in parameters.get("required", []):
        if required not in arguments:
            return f"missing required property: {required}"
    if parameters.get("additionalProperties") is False:
        unknown = sorted(set(arguments) - set(properties))
        if unknown:
            return f"unknown property: {unknown[0]}"
    for key, value in arguments.items():
        property_schema = properties.get(key)
        if property_schema is None:
            continue
        expected = property_schema.get("type")
        valid = {
            "string": isinstance(value, str),
            "boolean": isinstance(value, bool),
            "integer": isinstance(value, int) and not isinstance(value, bool),
        }.get(expected, True)
        if not valid:
            article = "an" if expected == "integer" else "a"
            return f"{key} must be {article} {expected}"
        minimum = property_schema.get("minimum")
        if minimum is not None and value < minimum:
            return f"{key} must be at least {minimum}"
    return None
