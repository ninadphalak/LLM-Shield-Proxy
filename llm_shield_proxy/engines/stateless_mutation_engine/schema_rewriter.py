import copy
from typing import Any, Dict


class DynamicSchemaRewriter:
    """
    Rewrites discovered JSON Schema objects to describe cryptographic sibling
    context fields (``_ctx_hash_<prop>``) for string properties.

    Marking a field as required does not compel a model or provider to echo it;
    callers must test the selected structured-output integration.
    """

    @classmethod
    def rewrite(cls, schema: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(schema, dict):
            return schema

        # Preserve caller input immutability.
        schema_copy = copy.deepcopy(schema)
        return cls._rewrite_internal(schema_copy)

    @classmethod
    def _rewrite_internal(cls, schema: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(schema, dict):
            return schema

        # Recursive traversal for nested object schemas
        if schema.get("type") == "object" and "properties" in schema:
            props = schema["properties"]
            new_props = {}
            injected_fields = []

            for k, v in props.items():
                # Process the child recursively first
                new_props[k] = cls._rewrite_internal(v)

                # If the property is a string, it might be redacted.
                # We inject the _ctx_hash sibling to allow stateless echoing.
                if isinstance(v, dict) and v.get("type") == "string":
                    ctx_key = f"_ctx_hash_{k}"
                    new_props[ctx_key] = {
                        "type": "string",
                        "description": f"Cryptographic context for {k}. Must be provided if {k} is redacted."
                    }
                    injected_fields.append(ctx_key)

            schema["properties"] = new_props

            # Augment the required array
            if injected_fields:
                if "required" not in schema:
                    schema["required"] = []
                # Ensure we only append if not already required
                for field in injected_fields:
                    if field not in schema["required"]:
                        schema["required"].append(field)

        elif schema.get("type") == "array" and "items" in schema:
            schema["items"] = cls._rewrite_internal(schema["items"])

        else:
            # Tool definitions are commonly nested inside a larger request rather
            # than passed as the root schema. Traverse containers until a JSON
            # Schema-shaped object is found.
            for key, value in list(schema.items()):
                if isinstance(value, dict):
                    schema[key] = cls._rewrite_internal(value)
                elif isinstance(value, list):
                    schema[key] = [
                        cls._rewrite_internal(item) if isinstance(item, dict) else item
                        for item in value
                    ]

        return schema
