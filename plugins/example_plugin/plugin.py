"""Example plugin that logs requests to stderr."""

from __future__ import annotations

import sys
from typing import Any

from specli.models import GlobalConfig
from specli.plugins.base import Plugin


class ExamplePlugin(Plugin):
    """Logs request and response information to stderr."""

    def __init__(self) -> None:
        self._initialized = False

    @property
    def name(self) -> str:
        return "example"

    @property
    def version(self) -> str:
        return "0.1.0"

    @property
    def description(self) -> str:
        return "Example plugin that logs request/response info"

    def on_init(self, config: GlobalConfig) -> None:
        self._initialized = True

    def on_pre_request(
        self, method: str, url: str, headers: dict[str, str], params: dict[str, Any]
    ) -> dict[str, Any]:
        print(f"[example] {method} {url}", file=sys.stderr)
        return {"headers": headers, "params": params}

    def on_post_response(
        self, status_code: int, headers: dict[str, str], body: Any
    ) -> Any:
        print(f"[example] Response: {status_code}", file=sys.stderr)
        return body

    def cleanup(self) -> None:
        self._initialized = False
