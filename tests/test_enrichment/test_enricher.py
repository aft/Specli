"""Tests for the spec enricher."""

from __future__ import annotations

import copy

import pytest

from specli.enrichment.enricher import (
    _is_thin,
    _normalise_path,
    enrich_raw_spec,
)
from specli.enrichment.scanner import RouteDoc


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def minimal_spec() -> dict:
    """A minimal OpenAPI spec with thin descriptions."""
    return {
        "openapi": "3.0.3",
        "info": {"title": "Test API", "version": "1.0.0"},
        "paths": {
            "/api/campaigns": {
                "get": {
                    "operationId": "list_campaigns",
                    "summary": "List Campaigns",
                    "description": "",
                    "parameters": [
                        {
                            "name": "skip",
                            "in": "query",
                            "schema": {"type": "integer"},
                        },
                        {
                            "name": "limit",
                            "in": "query",
                            "description": "Max results",
                            "schema": {"type": "integer"},
                        },
                    ],
                    "tags": ["campaigns"],
                },
                "post": {
                    "operationId": "create_campaign",
                    "summary": "Create Campaign",
                    "description": "",
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "name": {"type": "string"},
                                        "budget": {"type": "number"},
                                    },
                                },
                            }
                        }
                    },
                    "tags": ["campaigns"],
                },
            },
            "/api/campaigns/{campaign_id}": {
                "get": {
                    "operationId": "get_campaign",
                    "summary": "This is a sufficiently long and detailed summary already",
                    "description": "Detailed existing description that should be preserved.",
                    "parameters": [
                        {
                            "name": "campaign_id",
                            "in": "path",
                            "required": True,
                            "description": "Campaign ID",
                            "schema": {"type": "integer"},
                        }
                    ],
                    "tags": ["campaigns"],
                },
            },
        },
        "tags": [
            {"name": "campaigns"},
        ],
    }


@pytest.fixture
def route_docs() -> list[RouteDoc]:
    """Route docs extracted from source."""
    return [
        RouteDoc(
            method="get",
            path="/api/campaigns",
            summary="List all campaigns with pagination.",
            description=(
                "List all campaigns with pagination.\n\n"
                "Returns a paginated list of campaigns. Supports filtering by status."
            ),
            param_docs={
                "skip": "Number of records to skip for pagination.",
                "limit": "Maximum number of records to return.",
            },
            module_doc="Campaign management endpoints.",
            source_file="/src/campaigns.py",
        ),
        RouteDoc(
            method="post",
            path="/api/campaigns",
            summary="Create a new advertising campaign.",
            description=(
                "Create a new advertising campaign.\n\n"
                "Validates the campaign parameters and creates a new record."
            ),
            param_docs={
                "name": "Human-readable campaign name.",
                "budget": "Total budget in USD.",
            },
            module_doc="Campaign management endpoints.",
            source_file="/src/campaigns.py",
        ),
        RouteDoc(
            method="get",
            path="/api/campaigns/{campaign_id}",
            summary="Get campaign details by ID.",
            description=(
                "Get campaign details by ID.\n\n"
                "Retrieves the full campaign record."
            ),
            param_docs={"campaign_id": "Unique campaign identifier."},
            module_doc="Campaign management endpoints.",
            source_file="/src/campaigns.py",
        ),
    ]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestEnrichRawSpec:

    def test_enriches_thin_summary(
        self, minimal_spec: dict, route_docs: list[RouteDoc]
    ) -> None:
        enrich_raw_spec(minimal_spec, route_docs)
        op = minimal_spec["paths"]["/api/campaigns"]["get"]
        assert op["summary"] == "List all campaigns with pagination."

    def test_enriches_empty_description(
        self, minimal_spec: dict, route_docs: list[RouteDoc]
    ) -> None:
        enrich_raw_spec(minimal_spec, route_docs)
        op = minimal_spec["paths"]["/api/campaigns"]["get"]
        assert "paginated list" in op["description"]

    def test_does_not_overwrite_good_summary(
        self, minimal_spec: dict, route_docs: list[RouteDoc]
    ) -> None:
        enrich_raw_spec(minimal_spec, route_docs)
        op = minimal_spec["paths"]["/api/campaigns/{campaign_id}"]["get"]
        assert op["summary"] == "This is a sufficiently long and detailed summary already"

    def test_does_not_overwrite_longer_description(
        self, minimal_spec: dict, route_docs: list[RouteDoc]
    ) -> None:
        """When spec description is longer than source, keep spec."""
        # Make spec description longer than source.
        spec = copy.deepcopy(minimal_spec)
        spec["paths"]["/api/campaigns/{campaign_id}"]["get"]["description"] = "A" * 500
        enrich_raw_spec(spec, route_docs)
        assert spec["paths"]["/api/campaigns/{campaign_id}"]["get"]["description"] == "A" * 500

    def test_enriches_parameter_descriptions(
        self, minimal_spec: dict, route_docs: list[RouteDoc]
    ) -> None:
        enrich_raw_spec(minimal_spec, route_docs)
        params = minimal_spec["paths"]["/api/campaigns"]["get"]["parameters"]
        skip_param = next(p for p in params if p["name"] == "skip")
        assert skip_param["description"] == "Number of records to skip for pagination."

    def test_does_not_overwrite_existing_param_description(
        self, minimal_spec: dict, route_docs: list[RouteDoc]
    ) -> None:
        enrich_raw_spec(minimal_spec, route_docs)
        params = minimal_spec["paths"]["/api/campaigns"]["get"]["parameters"]
        limit_param = next(p for p in params if p["name"] == "limit")
        # "Max results" was already there; should NOT be overwritten.
        assert limit_param["description"] == "Max results"

    def test_enriches_request_body_properties(
        self, minimal_spec: dict, route_docs: list[RouteDoc]
    ) -> None:
        enrich_raw_spec(minimal_spec, route_docs)
        schema = (
            minimal_spec["paths"]["/api/campaigns"]["post"]
            ["requestBody"]["content"]["application/json"]["schema"]
        )
        assert schema["properties"]["name"]["description"] == "Human-readable campaign name."
        assert schema["properties"]["budget"]["description"] == "Total budget in USD."

    def test_enriches_tag_descriptions(
        self, minimal_spec: dict, route_docs: list[RouteDoc]
    ) -> None:
        enrich_raw_spec(minimal_spec, route_docs)
        tags = minimal_spec["tags"]
        campaigns_tag = next(t for t in tags if t["name"] == "campaigns")
        assert campaigns_tag["description"] == "Campaign management endpoints."

    def test_no_route_docs_is_noop(self, minimal_spec: dict) -> None:
        original = copy.deepcopy(minimal_spec)
        enrich_raw_spec(minimal_spec, [])
        assert minimal_spec == original


class TestNormalisePath:

    def test_strips_trailing_slash(self) -> None:
        assert _normalise_path("/api/foo/") == "/api/foo"

    def test_normalises_param_names(self) -> None:
        assert _normalise_path("/api/{item_id}") == "/api/{_}"
        assert _normalise_path("/api/{itemId}") == "/api/{_}"

    def test_root_path(self) -> None:
        assert _normalise_path("/") == "/"


class TestIsThin:

    def test_none_is_thin(self) -> None:
        assert _is_thin(None, None) is True

    def test_empty_is_thin(self) -> None:
        assert _is_thin("", None) is True

    def test_short_is_thin(self) -> None:
        assert _is_thin("List Items", None) is True

    def test_matches_operation_id(self) -> None:
        assert _is_thin("List Campaigns", "list_campaigns") is True

    def test_good_summary_is_not_thin(self) -> None:
        assert _is_thin("Retrieve all active campaigns with pagination support.", None) is False

    def test_long_but_matching_op_id(self) -> None:
        # If it matches operation_id exactly, it's thin regardless of length.
        assert _is_thin("Upload Asset To Storage", "upload_asset_to_storage") is True
