import copy
from typing import Any, Dict


class DynamicSchemaRewriter:
    """
    Dynamically rewrites MCP tool schemas and OpenAI function definitions
    to require cryptographic sibling hashes (_ctx_hash_<prop>) for string properties,
    guaranteeing the LLM can echo back the cipher context statelessly.
    """

    @classmethod
    def rewrite(cls, schema: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(schema, dict):
            return schema

        # Guarantee caller input immutability
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

        return schema
