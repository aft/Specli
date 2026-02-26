"""Tests for the AST-based source scanner."""

from __future__ import annotations

from pathlib import Path

import pytest

from specli.enrichment.scanner import RouteDoc, SourceScanner, _parse_param_docs


# ---------------------------------------------------------------------------
# Fixtures: Python source files to scan
# ---------------------------------------------------------------------------


SIMPLE_FASTAPI_SOURCE = '''\
"""Campaign management endpoints."""

from fastapi import APIRouter

router = APIRouter(prefix="/api/campaigns")


@router.get("/")
async def list_campaigns(skip: int = 0, limit: int = 100):
    """List all campaigns with pagination.

    Returns a paginated list of campaigns. Supports filtering by status
    and date range.

    Args:
        skip: Number of records to skip for pagination.
        limit: Maximum number of records to return.
    """
    pass


@router.post("/")
async def create_campaign(name: str, budget: float):
    """Create a new advertising campaign.

    Validates the campaign parameters and creates a new record in the database.
    Returns the created campaign with its assigned ID.

    Args:
        name: Human-readable campaign name.
        budget: Total budget in USD.
    """
    pass


@router.get("/{campaign_id}")
async def get_campaign(campaign_id: int):
    """Get campaign details by ID.

    Retrieves the full campaign record including metrics and status history.

    Args:
        campaign_id: Unique campaign identifier.
    """
    pass


@router.delete("/{campaign_id}")
async def delete_campaign(campaign_id: int):
    """Delete a campaign permanently.

    Args:
        campaign_id: Campaign to delete.
    """
    pass
'''


APP_DIRECT_SOURCE = '''\
"""Asset upload endpoints."""

from fastapi import FastAPI

app = FastAPI()


@app.post("/api/assets/upload")
async def upload_asset(file_name: str, content_type: str):
    """Upload an asset file to the system.

    Accepts multipart form data with the following fields:
    - file: The binary file content
    - metadata: Optional JSON metadata

    The file is validated for size (max 100MB) and content type.
    Supported types: image/png, image/jpeg, application/pdf.

    Args:
        file_name: Original filename with extension.
        content_type: MIME type of the uploaded file.
    """
    pass
'''


INCLUDE_ROUTER_SOURCE = '''\
"""Module with include_router pattern."""

from fastapi import APIRouter, FastAPI

app = FastAPI()
items_router = APIRouter()

app.include_router(items_router, prefix="/api/v2/items")


@items_router.get("/")
async def list_items():
    """List all items in the inventory."""
    pass


@items_router.get("/{item_id}")
async def get_item(item_id: int):
    """Get a single item by ID.

    Args:
        item_id: The item's unique database identifier.
    """
    pass
'''


PYDANTIC_MODEL_SOURCE = '''\
"""Orders API with Pydantic models."""

from fastapi import APIRouter
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/orders")


class OrderCreate(BaseModel):
    product_id: int = Field(description="ID of the product to order")
    quantity: int = Field(description="Number of units to order")
    notes: str = Field(default="", description="Optional order notes")


@router.post("/")
async def create_order(order: OrderCreate):
    """Create a new order."""
    pass
'''


NO_DOCSTRING_SOURCE = '''\
from fastapi import APIRouter

router = APIRouter(prefix="/api/health")

@router.get("/")
async def health_check():
    pass
'''


# ---------------------------------------------------------------------------
# Scanner tests
# ---------------------------------------------------------------------------


@pytest.fixture
def source_tree(tmp_path: Path) -> Path:
    """Create a source tree with multiple Python files."""
    (tmp_path / "campaigns.py").write_text(SIMPLE_FASTAPI_SOURCE)
    (tmp_path / "assets.py").write_text(APP_DIRECT_SOURCE)
    (tmp_path / "items.py").write_text(INCLUDE_ROUTER_SOURCE)
    (tmp_path / "orders.py").write_text(PYDANTIC_MODEL_SOURCE)
    (tmp_path / "health.py").write_text(NO_DOCSTRING_SOURCE)
    (tmp_path / "test_skip.py").write_text("# should be skipped")
    return tmp_path


class TestSourceScanner:

    def test_scan_finds_all_routes(self, source_tree: Path) -> None:
        scanner = SourceScanner()
        docs = scanner.scan(str(source_tree))
        # campaigns: 4, assets: 1, items: 2, orders: 1, health: 1 = 9
        assert len(docs) == 9

    def test_scan_with_exclude(self, source_tree: Path) -> None:
        scanner = SourceScanner()
        docs = scanner.scan(
            str(source_tree),
            exclude_patterns=["**/test_*"],
        )
        assert all("test_skip" not in d.source_file for d in docs)

    def test_router_prefix_resolution(self, source_tree: Path) -> None:
        scanner = SourceScanner()
        docs = scanner.scan(str(source_tree))

        campaign_docs = [d for d in docs if "/api/campaigns" in d.path]
        assert len(campaign_docs) == 4

        paths = {d.path for d in campaign_docs}
        assert "/api/campaigns/" in paths
        assert "/api/campaigns/{campaign_id}" in paths

    def test_include_router_prefix(self, source_tree: Path) -> None:
        scanner = SourceScanner()
        docs = scanner.scan(str(source_tree))

        item_docs = [d for d in docs if "/api/v2/items" in d.path]
        assert len(item_docs) == 2
        assert any(d.path == "/api/v2/items/" for d in item_docs)
        assert any(d.path == "/api/v2/items/{item_id}" for d in item_docs)

    def test_direct_app_decorator(self, source_tree: Path) -> None:
        scanner = SourceScanner()
        docs = scanner.scan(str(source_tree))

        asset_docs = [d for d in docs if "upload" in d.path]
        assert len(asset_docs) == 1
        doc = asset_docs[0]
        assert doc.method == "post"
        assert doc.path == "/api/assets/upload"

    def test_docstring_extraction(self, source_tree: Path) -> None:
        scanner = SourceScanner()
        docs = scanner.scan(str(source_tree))

        list_doc = next(
            d for d in docs
            if d.path == "/api/campaigns/" and d.method == "get"
        )
        assert list_doc.summary == "List all campaigns with pagination."
        assert "pagination" in list_doc.description
        assert list_doc.param_docs.get("skip") == "Number of records to skip for pagination."
        assert list_doc.param_docs.get("limit") == "Maximum number of records to return."

    def test_module_docstring(self, source_tree: Path) -> None:
        scanner = SourceScanner()
        docs = scanner.scan(str(source_tree))

        campaign_doc = next(d for d in docs if "/api/campaigns/" in d.path)
        assert campaign_doc.module_doc == "Campaign management endpoints."

    def test_pydantic_field_docs(self, source_tree: Path) -> None:
        scanner = SourceScanner()
        docs = scanner.scan(str(source_tree))

        order_doc = next(d for d in docs if "/api/orders/" in d.path)
        assert order_doc.param_docs.get("product_id") == "ID of the product to order"
        assert order_doc.param_docs.get("quantity") == "Number of units to order"

    def test_no_docstring_still_produces_route(self, source_tree: Path) -> None:
        scanner = SourceScanner()
        docs = scanner.scan(str(source_tree))

        health_doc = next(d for d in docs if "/api/health/" in d.path)
        assert health_doc.summary is None
        assert health_doc.description is None

    def test_nonexistent_dir_returns_empty(self) -> None:
        scanner = SourceScanner()
        docs = scanner.scan("/nonexistent/path/that/does/not/exist")
        assert docs == []

    def test_methods_are_lowercase(self, source_tree: Path) -> None:
        scanner = SourceScanner()
        docs = scanner.scan(str(source_tree))
        for doc in docs:
            assert doc.method == doc.method.lower()


class TestParseParamDocs:

    def test_google_style(self) -> None:
        docstring = """Do something.

        Args:
            name: The user's name.
            age: The user's age in years.
        """
        result = _parse_param_docs(docstring)
        assert result == {
            "name": "The user's name.",
            "age": "The user's age in years.",
        }

    def test_multiline_param(self) -> None:
        docstring = """Do something.

        Args:
            name: The user's name, which can be
                quite long and span multiple lines.
            age: Simple.
        """
        result = _parse_param_docs(docstring)
        assert "quite long" in result["name"]
        assert result["age"] == "Simple."

    def test_parameters_keyword(self) -> None:
        docstring = """Do something.

        Parameters:
            x: First value.
            y: Second value.
        """
        result = _parse_param_docs(docstring)
        assert result == {"x": "First value.", "y": "Second value."}

    def test_no_args_section(self) -> None:
        docstring = """Just a description with no args."""
        result = _parse_param_docs(docstring)
        assert result == {}

    def test_args_with_type_annotations(self) -> None:
        docstring = """Something.

        Args:
            name (str): The name.
            count (int): How many.
        """
        result = _parse_param_docs(docstring)
        assert result == {"name": "The name.", "count": "How many."}
