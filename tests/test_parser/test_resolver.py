"""Tests for specli.parser.resolver."""

from __future__ import annotations

import pytest

from specli.exceptions import SpecParseError
from specli.parser.resolver import _deep_resolve, _resolve_ref, resolve_refs


# ---------------------------------------------------------------------------
# resolve_refs (top-level)
# ---------------------------------------------------------------------------


class TestResolveRefs:
    """Test the top-level resolve_refs function."""

    def test_resolves_simple_ref(self) -> None:
        spec = {
            "paths": {
                "/pets": {
                    "get": {
                        "responses": {
                            "200": {
                                "content": {
                                    "application/json": {
                                        "schema": {"$ref": "#/components/schemas/Pet"}
                                    }
                                }
                            }
                        }
                    }
                }
            },
            "components": {
                "schemas": {
                    "Pet": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                        },
                    }
                }
            },
        }

        resolved = resolve_refs(spec)

        # The $ref should be replaced with the actual schema
        schema = resolved["paths"]["/pets"]["get"]["responses"]["200"]["content"][
            "application/json"
        ]["schema"]
        assert schema == {"type": "object", "properties": {"name": {"type": "string"}}}

    def test_does_not_mutate_original(self) -> None:
        spec = {
            "paths": {
                "/test": {
                    "get": {
                        "responses": {
                            "200": {
                                "schema": {"$ref": "#/components/schemas/Item"}
                            }
                        }
                    }
                }
            },
            "components": {"schemas": {"Item": {"type": "string"}}},
        }

        original_ref = spec["paths"]["/test"]["get"]["responses"]["200"]["schema"]["$ref"]
        resolve_refs(spec)
        # Original should still have the $ref
        assert spec["paths"]["/test"]["get"]["responses"]["200"]["schema"]["$ref"] == original_ref

    def test_no_refs_passthrough(self) -> None:
        spec = {
            "openapi": "3.0.3",
            "info": {"title": "Test", "version": "1.0"},
            "paths": {},
        }
        resolved = resolve_refs(spec)
        assert resolved == spec

    def test_resolves_nested_refs(self) -> None:
        spec = {
            "paths": {
                "/items": {
                    "get": {
                        "responses": {
                            "200": {
                                "content": {
                                    "application/json": {
                                        "schema": {"$ref": "#/components/schemas/ItemList"}
                                    }
                                }
                            }
                        }
                    }
                }
            },
            "components": {
                "schemas": {
                    "ItemList": {
                        "type": "array",
                        "items": {"$ref": "#/components/schemas/Item"},
                    },
                    "Item": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "integer"},
                            "name": {"type": "string"},
                        },
                    },
                }
            },
        }

        resolved = resolve_refs(spec)
        schema = resolved["paths"]["/items"]["get"]["responses"]["200"]["content"][
            "application/json"
        ]["schema"]

        assert schema["type"] == "array"
        assert schema["items"]["type"] == "object"
        assert "id" in schema["items"]["properties"]

    def test_handles_circular_refs(self) -> None:
        spec = {
            "components": {
                "schemas": {
                    "TreeNode": {
                        "type": "object",
                        "properties": {
                            "value": {"type": "string"},
                            "children": {
                                "type": "array",
                                "items": {"$ref": "#/components/schemas/TreeNode"},
                            },
                        },
                    }
                }
            }
        }

        # Should not raise or infinite loop
        resolved = resolve_refs(spec)

        # The first level should be resolved
        tree = resolved["components"]["schemas"]["TreeNode"]
        assert tree["type"] == "object"
        assert tree["properties"]["value"]["type"] == "string"

        # The $ref in items resolves to the TreeNode object (one level deep)
        children_items = tree["properties"]["children"]["items"]
        assert children_items["type"] == "object"

        # But the nested circular ref inside THAT level stays unresolved
        nested_items = children_items["properties"]["children"]["items"]
        assert "$ref" in nested_items

    def test_resolves_parameter_ref(self) -> None:
        spec = {
            "paths": {
                "/items": {
                    "get": {
                        "parameters": [
                            {"$ref": "#/components/parameters/LimitParam"}
                        ]
                    }
                }
            },
            "components": {
                "parameters": {
                    "LimitParam": {
                        "name": "limit",
                        "in": "query",
                        "schema": {"type": "integer"},
                    }
                }
            },
        }

        resolved = resolve_refs(spec)
        param = resolved["paths"]["/items"]["get"]["parameters"][0]
        assert param["name"] == "limit"
        assert param["in"] == "query"

    def test_resolves_request_body_ref(self) -> None:
        spec = {
            "paths": {
                "/items": {
                    "post": {
                        "requestBody": {
                            "$ref": "#/components/requestBodies/ItemBody"
                        }
                    }
                }
            },
            "components": {
                "requestBodies": {
                    "ItemBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {"type": "object"}
                            }
                        },
                    }
                }
            },
        }

        resolved = resolve_refs(spec)
        body = resolved["paths"]["/items"]["post"]["requestBody"]
        assert body["required"] is True
        assert "application/json" in body["content"]


# ---------------------------------------------------------------------------
# _resolve_ref
# ---------------------------------------------------------------------------


class TestResolveRef:
    """Test single $ref resolution."""

    def test_resolves_schema_ref(self) -> None:
        root = {
            "components": {
                "schemas": {
                    "Pet": {"type": "object", "properties": {"name": {"type": "string"}}}
                }
            }
        }
        result = _resolve_ref("#/components/schemas/Pet", root)
        assert result["type"] == "object"
        assert result["properties"]["name"]["type"] == "string"

    def test_resolves_deeply_nested_ref(self) -> None:
        root = {
            "a": {
                "b": {
                    "c": {
                        "d": "found"
                    }
                }
            }
        }
        result = _resolve_ref("#/a/b/c/d", root)
        assert result == "found"

    def test_missing_key_raises(self) -> None:
        root = {"components": {"schemas": {}}}
        with pytest.raises(SpecParseError, match="Cannot resolve.*not found"):
            _resolve_ref("#/components/schemas/Missing", root)

    def test_external_ref_raises(self) -> None:
        root: dict = {}
        with pytest.raises(SpecParseError, match="External.*not supported"):
            _resolve_ref("./other-file.json#/schemas/Pet", root)

    def test_json_pointer_escaping(self) -> None:
        """Test RFC 6901 JSON Pointer escaping: ~0 = ~, ~1 = /."""
        root = {
            "components": {
                "schemas": {
                    "a/b": {"type": "found-slash"},
                    "c~d": {"type": "found-tilde"},
                }
            }
        }
        # '/' in key is encoded as '~1'
        result = _resolve_ref("#/components/schemas/a~1b", root)
        assert result["type"] == "found-slash"

        # '~' in key is encoded as '~0'
        result = _resolve_ref("#/components/schemas/c~0d", root)
        assert result["type"] == "found-tilde"


# ---------------------------------------------------------------------------
# _deep_resolve
# ---------------------------------------------------------------------------


class TestDeepResolve:
    """Test recursive resolution."""

    def test_scalars_passthrough(self) -> None:
        root: dict = {}
        assert _deep_resolve("hello", root) == "hello"
        assert _deep_resolve(42, root) == 42
        assert _deep_resolve(None, root) is None
        assert _deep_resolve(True, root) is True

    def test_list_passthrough(self) -> None:
        root: dict = {}
        result = _deep_resolve([1, "two", None], root)
        assert result == [1, "two", None]

    def test_dict_without_ref(self) -> None:
        root: dict = {}
        obj = {"key": "value", "nested": {"a": 1}}
        result = _deep_resolve(obj, root)
        assert result == obj

    def test_resolves_ref_in_dict(self) -> None:
        root = {"definitions": {"Str": {"type": "string"}}}
        obj = {"schema": {"$ref": "#/definitions/Str"}}
        result = _deep_resolve(obj, root)
        assert result["schema"] == {"type": "string"}

    def test_resolves_ref_in_list(self) -> None:
        root = {"definitions": {"Num": {"type": "integer"}}}
        obj = [{"$ref": "#/definitions/Num"}, {"type": "string"}]
        result = _deep_resolve(obj, root)
        assert result[0] == {"type": "integer"}
        assert result[1] == {"type": "string"}

    def test_circular_ref_terminates(self) -> None:
        root = {
            "defs": {
                "Self": {
                    "type": "object",
                    "child": {"$ref": "#/defs/Self"},
                }
            }
        }
        # Should terminate without raising
        result = _deep_resolve(root, root)
        # First level resolved: child is the Self object itself
        assert result["defs"]["Self"]["type"] == "object"
        child = result["defs"]["Self"]["child"]
        assert child["type"] == "object"
        # The nested circular ref inside the resolved child stays unresolved
        nested_child = child["child"]
        assert "$ref" in nested_child

    def test_parallel_branches_same_ref(self) -> None:
        """Two separate references to the same schema should both resolve."""
        root = {
            "definitions": {"T": {"type": "string"}},
            "a": {"$ref": "#/definitions/T"},
            "b": {"$ref": "#/definitions/T"},
        }
        result = _deep_resolve(root, root)
        assert result["a"] == {"type": "string"}
        assert result["b"] == {"type": "string"}
