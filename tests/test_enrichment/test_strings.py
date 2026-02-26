"""Tests for the strings export/import system."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from specli.enrichment.strings import (
    export_strings,
    export_strings_to_file,
    import_strings,
    import_strings_from_file,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_spec() -> dict:
    """A raw OpenAPI spec with various text fields."""
    return {
        "openapi": "3.0.3",
        "info": {
            "title": "Fradken",
            "version": "1.0.0",
            "description": "Ad Generation Campaign Manager",
        },
        "tags": [
            {"name": "campaigns", "description": "Campaign operations."},
            {"name": "assets"},
        ],
        "paths": {
            "/api/campaigns": {
                "get": {
                    "operationId": "list_campaigns",
                    "summary": "List all campaigns.",
                    "description": "Returns paginated campaigns.",
                    "tags": ["campaigns"],
                    "parameters": [
                        {
                            "name": "skip",
                            "in": "query",
                            "description": "Records to skip.",
                            "schema": {"type": "integer"},
                        },
                        {
                            "name": "limit",
                            "in": "query",
                            "description": "Max results.",
                            "schema": {"type": "integer"},
                        },
                    ],
                },
                "post": {
                    "operationId": "create_campaign",
                    "summary": "Create a campaign.",
                    "description": "",
                    "tags": ["campaigns"],
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "name": {
                                            "type": "string",
                                            "description": "Campaign name.",
                                        },
                                        "budget": {
                                            "type": "number",
                                            "description": "",
                                        },
                                    },
                                },
                            }
                        }
                    },
                },
            },
            "/api/assets/{asset_id}": {
                "get": {
                    "operationId": "get_asset",
                    "summary": "Get asset.",
                    "description": "Retrieve an asset by ID.",
                    "tags": ["assets"],
                    "parameters": [
                        {
                            "name": "asset_id",
                            "in": "path",
                            "required": True,
                            "description": "Asset ID.",
                            "schema": {"type": "string"},
                        }
                    ],
                },
            },
        },
    }


# ---------------------------------------------------------------------------
# Export tests
# ---------------------------------------------------------------------------


class TestExportStrings:

    def test_exports_info(self, sample_spec: dict) -> None:
        result = export_strings(sample_spec)
        assert result["info"]["title"] == "Fradken"
        assert result["info"]["description"] == "Ad Generation Campaign Manager"

    def test_exports_tags(self, sample_spec: dict) -> None:
        result = export_strings(sample_spec)
        assert result["tags"]["campaigns"] == "Campaign operations."
        assert result["tags"]["assets"] == ""

    def test_exports_operations(self, sample_spec: dict) -> None:
        result = export_strings(sample_spec)
        ops = result["operations"]
        assert "GET /api/campaigns" in ops
        assert "POST /api/campaigns" in ops
        assert "GET /api/assets/{asset_id}" in ops

    def test_exports_operation_fields(self, sample_spec: dict) -> None:
        result = export_strings(sample_spec)
        op = result["operations"]["GET /api/campaigns"]
        assert op["summary"] == "List all campaigns."
        assert op["description"] == "Returns paginated campaigns."
        assert op["parameters"]["skip"] == "Records to skip."
        assert op["parameters"]["limit"] == "Max results."

    def test_exports_request_body_properties(self, sample_spec: dict) -> None:
        result = export_strings(sample_spec)
        op = result["operations"]["POST /api/campaigns"]
        assert op["parameters"]["name"] == "Campaign name."
        assert op["parameters"]["budget"] == ""

    def test_export_to_file(self, sample_spec: dict, tmp_path: Path) -> None:
        out = tmp_path / "strings.json"
        count = export_strings_to_file(sample_spec, str(out))
        assert count == 3  # 3 operations
        assert out.exists()

        data = json.loads(out.read_text())
        assert "info" in data
        assert "tags" in data
        assert "operations" in data

    def test_export_preserves_order(self, sample_spec: dict) -> None:
        result = export_strings(sample_spec)
        keys = list(result["operations"].keys())
        # Sorted by path
        assert keys[0].endswith("/api/assets/{asset_id}")
        assert keys[1].startswith("GET /api/campaigns")
        assert keys[2].startswith("POST /api/campaigns")


# ---------------------------------------------------------------------------
# Import tests
# ---------------------------------------------------------------------------


class TestImportStrings:

    def test_import_overrides_summary(self, sample_spec: dict) -> None:
        strings = {
            "operations": {
                "GET /api/campaigns": {
                    "summary": "Fetch campaigns for LLM processing.",
                }
            }
        }
        import_strings(sample_spec, strings)
        op = sample_spec["paths"]["/api/campaigns"]["get"]
        assert op["summary"] == "Fetch campaigns for LLM processing."

    def test_import_overrides_description(self, sample_spec: dict) -> None:
        strings = {
            "operations": {
                "GET /api/campaigns": {
                    "description": "New detailed description for AI agents.",
                }
            }
        }
        import_strings(sample_spec, strings)
        op = sample_spec["paths"]["/api/campaigns"]["get"]
        assert op["description"] == "New detailed description for AI agents."

    def test_import_overrides_param_description(self, sample_spec: dict) -> None:
        strings = {
            "operations": {
                "GET /api/campaigns": {
                    "parameters": {
                        "skip": "Offset for pagination (0-based).",
                    }
                }
            }
        }
        import_strings(sample_spec, strings)
        params = sample_spec["paths"]["/api/campaigns"]["get"]["parameters"]
        skip = next(p for p in params if p["name"] == "skip")
        assert skip["description"] == "Offset for pagination (0-based)."

    def test_import_overrides_request_body_property(self, sample_spec: dict) -> None:
        strings = {
            "operations": {
                "POST /api/campaigns": {
                    "parameters": {
                        "budget": "Campaign budget in USD (min 1.00).",
                    }
                }
            }
        }
        import_strings(sample_spec, strings)
        schema = (
            sample_spec["paths"]["/api/campaigns"]["post"]
            ["requestBody"]["content"]["application/json"]["schema"]
        )
        assert schema["properties"]["budget"]["description"] == "Campaign budget in USD (min 1.00)."

    def test_import_overrides_info(self, sample_spec: dict) -> None:
        strings = {
            "info": {
                "title": "Fradken Ad Manager",
                "description": "AI-powered ad generation CLI.",
            }
        }
        import_strings(sample_spec, strings)
        assert sample_spec["info"]["title"] == "Fradken Ad Manager"
        assert sample_spec["info"]["description"] == "AI-powered ad generation CLI."

    def test_import_overrides_tag_description(self, sample_spec: dict) -> None:
        strings = {
            "tags": {
                "assets": "Digital asset management and uploads.",
            }
        }
        import_strings(sample_spec, strings)
        tags = sample_spec["tags"]
        asset_tag = next(t for t in tags if t["name"] == "assets")
        assert asset_tag["description"] == "Digital asset management and uploads."

    def test_import_adds_new_tag(self, sample_spec: dict) -> None:
        strings = {
            "tags": {
                "templates": "Creatomate template management.",
            }
        }
        import_strings(sample_spec, strings)
        tag_names = [t["name"] for t in sample_spec["tags"]]
        assert "templates" in tag_names

    def test_import_skips_empty_values(self, sample_spec: dict) -> None:
        original_summary = sample_spec["paths"]["/api/campaigns"]["get"]["summary"]
        strings = {
            "operations": {
                "GET /api/campaigns": {
                    "summary": "",
                    "description": "",
                }
            }
        }
        import_strings(sample_spec, strings)
        assert sample_spec["paths"]["/api/campaigns"]["get"]["summary"] == original_summary

    def test_import_ignores_nonexistent_paths(self, sample_spec: dict) -> None:
        original = copy.deepcopy(sample_spec)
        strings = {
            "operations": {
                "GET /api/nonexistent": {
                    "summary": "Should be ignored.",
                }
            }
        }
        import_strings(sample_spec, strings)
        # Paths should be unchanged.
        assert sample_spec["paths"] == original["paths"]

    def test_empty_strings_dict_is_noop(self, sample_spec: dict) -> None:
        original = copy.deepcopy(sample_spec)
        import_strings(sample_spec, {})
        assert sample_spec == original


# ---------------------------------------------------------------------------
# Round-trip tests
# ---------------------------------------------------------------------------


class TestRoundTrip:

    def test_export_then_import_is_idempotent(self, sample_spec: dict) -> None:
        """Exporting and re-importing without edits produces the same spec."""
        before = copy.deepcopy(sample_spec)
        exported = export_strings(sample_spec)
        import_strings(sample_spec, exported)
        # Summaries, descriptions, params should match.
        for path, methods in sample_spec["paths"].items():
            for method, op in methods.items():
                if not isinstance(op, dict):
                    continue
                before_op = before["paths"][path][method]
                assert op.get("summary") == before_op.get("summary")
                assert op.get("description") == before_op.get("description")

    def test_file_round_trip(self, sample_spec: dict, tmp_path: Path) -> None:
        """Export to file, import from file."""
        out = tmp_path / "strings.json"
        export_strings_to_file(sample_spec, str(out))

        # Modify the exported file.
        data = json.loads(out.read_text())
        data["operations"]["GET /api/campaigns"]["summary"] = "Modified summary."
        out.write_text(json.dumps(data))

        count = import_strings_from_file(sample_spec, str(out))
        assert count == 3
        assert sample_spec["paths"]["/api/campaigns"]["get"]["summary"] == "Modified summary."
