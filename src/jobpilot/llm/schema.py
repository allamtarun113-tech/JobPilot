"""Adapt domain JSON schemas to OpenAI's strict structured-output subset."""

from typing import Any

from pydantic import BaseModel


def strict_schema(output: type[BaseModel]) -> dict[str, Any]:
    """Turn a Pydantic model's JSON schema into OpenAI's strict form.

    Strict mode needs every object closed (no extra keys) with every
    property required, and doesn't support defaults, discriminators or
    ``oneOf``. Fields marked ``x-derived`` are filled by code, so they
    are left out.

    Args:
        output: The Pydantic model the model must return.

    Returns:
        The adapted JSON schema.
    """
    schema = output.model_json_schema()
    if output.__name__ == "JobChatDecision":
        # The domain keeps proposals as dictionaries; the wire format enumerates every supported key.
        schema["properties"]["proposal"] = {
            "type": "object",
            "properties": {
                key: {"anyOf": [{"type": kind}, {"type": "null"}]}
                for key, kind in {
                    "item_id": "integer",
                    "text": "string",
                    "field": "string",
                    "value": "string",
                    "action": "string",
                    "kind": "string",
                    "until": "string",
                }.items()
            },
        }

    for name, prop in list(schema.get("properties", {}).items()):
        if prop.get("x-derived"):  # filled by code after validation, never asked of the model
            del schema["properties"][name]

    def close(node: Any) -> None:
        """Recursively close every object node and drop unsupported keys."""
        if isinstance(node, list):
            for child in node:
                close(child)
        elif isinstance(node, dict):
            node.pop("default", None)
            node.pop("discriminator", None)
            if "oneOf" in node:
                node["anyOf"] = node.pop("oneOf")
            if node.get("type") == "object":
                if "properties" not in node:
                    raise ValueError("Unbounded object is unsupported in structured outputs")
                node["additionalProperties"] = False
                node["required"] = list(node["properties"])
            for child in node.values():
                close(child)

    close(schema)
    return schema
