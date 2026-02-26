"""Tests for specli.generator.path_rules — path transformation engine.

Covers:
- Auto-strip with common prefix detection
- No common prefix (different first segments)
- Single path (no stripping needed)
- ``keep`` segments re-insertion
- ``skip_segments`` removal
- Explicit ``strip_prefix`` overrides auto
- ``collapse`` mapping
- Precedence: collapse > strip_prefix > auto
- ``path_to_command_parts``
- Empty paths list
- Paths with only "/"
- Real-world paths (Corelia-style)
- Multiple path parameters
- ``auto_strip_prefix: false`` disabling
"""

from __future__ import annotations

import pytest

from specli.generator.path_rules import (
    _apply_keep,
    _apply_skip_segments,
    _strip_prefix,
    apply_path_rules,
    find_common_prefix,
    path_to_command_parts,
)
from specli.models import PathRulesConfig


# ------------------------------------------------------------------ #
# find_common_prefix
# ------------------------------------------------------------------ #


class TestFindCommonPrefix:
    """Test the common prefix detection algorithm."""

    def test_common_prefix_basic(self) -> None:
        paths = ["/api/v1/users", "/api/v1/tasks"]
        assert find_common_prefix(paths) == "/api/v1"

    def test_partial_common_prefix(self) -> None:
        paths = ["/api/v1/users", "/api/v2/tasks"]
        assert find_common_prefix(paths) == "/api"

    def test_no_common_prefix(self) -> None:
        paths = ["/users", "/tasks"]
        assert find_common_prefix(paths) == ""

    def test_single_path_no_prefix(self) -> None:
        """A single path has nothing to compare against -- no prefix."""
        assert find_common_prefix(["/api/v1/users"]) == ""

    def test_empty_list(self) -> None:
        assert find_common_prefix([]) == ""

    def test_identical_paths(self) -> None:
        """Identical paths share all segments, but the prefix is trimmed to
        avoid fully consuming any path."""
        paths = ["/api/v1/users", "/api/v1/users"]
        assert find_common_prefix(paths) == "/api/v1"

    def test_three_paths(self) -> None:
        paths = ["/api/v1/users", "/api/v1/tasks", "/api/v1/projects"]
        assert find_common_prefix(paths) == "/api/v1"

    def test_three_paths_partial(self) -> None:
        paths = ["/api/v1/users", "/api/v2/tasks", "/api/v3/projects"]
        assert find_common_prefix(paths) == "/api"

    def test_root_only_paths(self) -> None:
        """Paths that are just '/' have no non-empty segments."""
        paths = ["/", "/"]
        assert find_common_prefix(paths) == ""

    def test_one_root_path(self) -> None:
        paths = ["/", "/api/v1/users"]
        assert find_common_prefix(paths) == ""

    def test_does_not_strip_mid_segment(self) -> None:
        """'/api-v1/users' and '/api-v2/tasks' share no full segment."""
        paths = ["/api-v1/users", "/api-v2/tasks"]
        assert find_common_prefix(paths) == ""

    def test_deep_common_prefix(self) -> None:
        paths = ["/a/b/c/d/e/f", "/a/b/c/d/e/g"]
        assert find_common_prefix(paths) == "/a/b/c/d/e"

    def test_prefix_is_one_of_the_paths(self) -> None:
        """When a path equals the common prefix, the prefix is trimmed by
        one segment so that no path is fully consumed."""
        paths = ["/api/v1", "/api/v1/users", "/api/v1/tasks"]
        assert find_common_prefix(paths) == "/api"


# ------------------------------------------------------------------ #
# _strip_prefix
# ------------------------------------------------------------------ #


class TestStripPrefix:
    """Test prefix removal from individual paths."""

    def test_strip_basic(self) -> None:
        assert _strip_prefix("/api/v1/users", "/api/v1") == "/users"

    def test_strip_full_path(self) -> None:
        assert _strip_prefix("/api/v1", "/api/v1") == "/"

    def test_strip_empty_prefix(self) -> None:
        assert _strip_prefix("/users", "") == "/users"

    def test_strip_non_matching_prefix(self) -> None:
        """If the prefix doesn't match, path is returned unchanged."""
        assert _strip_prefix("/other/path", "/api/v1") == "/other/path"

    def test_strip_preserves_path_params(self) -> None:
        assert _strip_prefix("/api/v1/users/{id}", "/api/v1") == "/users/{id}"

    def test_strip_single_segment(self) -> None:
        assert _strip_prefix("/api/users", "/api") == "/users"

    def test_strip_root_path(self) -> None:
        assert _strip_prefix("/", "/api") == "/"


# ------------------------------------------------------------------ #
# _apply_keep
# ------------------------------------------------------------------ #


class TestApplyKeep:
    """Test re-insertion of kept segments."""

    def test_keep_one_segment(self) -> None:
        result = _apply_keep("/users", keep_segments=["v1"], stripped_segments=["api", "v1"])
        assert result == "/v1/users"

    def test_keep_multiple_segments(self) -> None:
        result = _apply_keep(
            "/users", keep_segments=["api", "v1"], stripped_segments=["api", "v1"]
        )
        assert result == "/api/v1/users"

    def test_keep_preserves_original_order(self) -> None:
        """Kept segments should appear in the order they appeared in the prefix."""
        result = _apply_keep(
            "/users", keep_segments=["v1", "api"], stripped_segments=["api", "v1"]
        )
        # "api" comes before "v1" in stripped_segments, so order is api, v1.
        assert result == "/api/v1/users"

    def test_keep_segment_not_in_stripped(self) -> None:
        """If the kept segment wasn't actually stripped, it's not re-inserted."""
        result = _apply_keep("/users", keep_segments=["v2"], stripped_segments=["api", "v1"])
        assert result == "/users"

    def test_keep_on_root_path(self) -> None:
        result = _apply_keep("/", keep_segments=["v1"], stripped_segments=["api", "v1"])
        assert result == "/v1"

    def test_keep_empty_list(self) -> None:
        result = _apply_keep("/users", keep_segments=[], stripped_segments=["api", "v1"])
        assert result == "/users"


# ------------------------------------------------------------------ #
# _apply_skip_segments
# ------------------------------------------------------------------ #


class TestApplySkipSegments:
    """Test segment removal."""

    def test_skip_one_segment(self) -> None:
        assert _apply_skip_segments("/api/users", ["api"]) == "/users"

    def test_skip_multiple_segments(self) -> None:
        assert _apply_skip_segments("/api/internal/users", ["api", "internal"]) == "/users"

    def test_skip_nonexistent_segment(self) -> None:
        assert _apply_skip_segments("/users/posts", ["api"]) == "/users/posts"

    def test_skip_all_segments(self) -> None:
        assert _apply_skip_segments("/api/v1", ["api", "v1"]) == "/"

    def test_skip_preserves_path_params(self) -> None:
        assert _apply_skip_segments("/api/users/{id}", ["api"]) == "/users/{id}"

    def test_skip_duplicate_segments(self) -> None:
        """If the same segment appears multiple times, all occurrences removed."""
        assert _apply_skip_segments("/api/users/api/tasks", ["api"]) == "/users/tasks"

    def test_skip_on_root(self) -> None:
        assert _apply_skip_segments("/", ["api"]) == "/"


# ------------------------------------------------------------------ #
# path_to_command_parts
# ------------------------------------------------------------------ #


class TestPathToCommandParts:
    """Test conversion of transformed paths to CLI command parts."""

    def test_simple_path(self) -> None:
        assert path_to_command_parts("/users") == ["users"]

    def test_nested_path(self) -> None:
        assert path_to_command_parts("/users/settings") == ["users", "settings"]

    def test_strips_single_param(self) -> None:
        assert path_to_command_parts("/users/{id}") == ["users"]

    def test_strips_param_between_segments(self) -> None:
        assert path_to_command_parts("/users/{id}/settings") == ["users", "settings"]

    def test_strips_multiple_params(self) -> None:
        assert path_to_command_parts("/users/{user_id}/posts/{post_id}") == ["users", "posts"]

    def test_root_path(self) -> None:
        assert path_to_command_parts("/") == []

    def test_only_params(self) -> None:
        assert path_to_command_parts("/{id}") == []

    def test_param_naming_variations(self) -> None:
        """Various param naming patterns should all be stripped."""
        assert path_to_command_parts("/repos/{owner}/{repo}/pulls") == ["repos", "pulls"]

    def test_no_leading_slash(self) -> None:
        """Even without a leading slash, segments parse correctly."""
        assert path_to_command_parts("users/{id}/settings") == ["users", "settings"]

    def test_hyphenated_segments(self) -> None:
        assert path_to_command_parts("/mcp-router/tools") == ["mcp-router", "tools"]


# ------------------------------------------------------------------ #
# apply_path_rules — auto_strip
# ------------------------------------------------------------------ #


class TestAutoStrip:
    """Test auto-strip with common prefix detection."""

    def test_auto_strip_basic(self) -> None:
        paths = ["/api/v1/users", "/api/v1/tasks"]
        rules = PathRulesConfig()
        result = apply_path_rules(paths, rules)
        assert result == {"/api/v1/users": "/users", "/api/v1/tasks": "/tasks"}

    def test_auto_strip_preserves_params(self) -> None:
        paths = ["/api/v1/users", "/api/v1/users/{id}"]
        rules = PathRulesConfig()
        result = apply_path_rules(paths, rules)
        assert result == {"/api/v1/users": "/users", "/api/v1/users/{id}": "/users/{id}"}

    def test_auto_strip_no_common_prefix(self) -> None:
        """When there's no common prefix, paths remain unchanged."""
        paths = ["/users", "/tasks"]
        rules = PathRulesConfig()
        result = apply_path_rules(paths, rules)
        assert result == {"/users": "/users", "/tasks": "/tasks"}

    def test_auto_strip_single_path(self) -> None:
        """A single path has no common prefix to strip."""
        paths = ["/api/v1/users"]
        rules = PathRulesConfig()
        result = apply_path_rules(paths, rules)
        assert result == {"/api/v1/users": "/api/v1/users"}

    def test_auto_strip_disabled(self) -> None:
        """When auto_strip_prefix is False, no stripping occurs."""
        paths = ["/api/v1/users", "/api/v1/tasks"]
        rules = PathRulesConfig(auto_strip_prefix=False)
        result = apply_path_rules(paths, rules)
        assert result == {"/api/v1/users": "/api/v1/users", "/api/v1/tasks": "/api/v1/tasks"}

    def test_auto_strip_deep_prefix(self) -> None:
        paths = ["/a/b/c/d/users", "/a/b/c/d/tasks"]
        rules = PathRulesConfig()
        result = apply_path_rules(paths, rules)
        assert result == {"/a/b/c/d/users": "/users", "/a/b/c/d/tasks": "/tasks"}


# ------------------------------------------------------------------ #
# apply_path_rules — keep
# ------------------------------------------------------------------ #


class TestKeepSegments:
    """Test that 'keep' re-inserts stripped segments."""

    def test_keep_version_segment(self) -> None:
        paths = ["/api/v1/users", "/api/v1/tasks"]
        rules = PathRulesConfig(keep=["v1"])
        result = apply_path_rules(paths, rules)
        assert result == {"/api/v1/users": "/v1/users", "/api/v1/tasks": "/v1/tasks"}

    def test_keep_multiple_segments(self) -> None:
        paths = ["/api/v1/users", "/api/v1/tasks"]
        rules = PathRulesConfig(keep=["api", "v1"])
        result = apply_path_rules(paths, rules)
        assert result == {
            "/api/v1/users": "/api/v1/users",
            "/api/v1/tasks": "/api/v1/tasks",
        }

    def test_keep_nonexistent_segment(self) -> None:
        """Keeping a segment that wasn't in the prefix is a no-op."""
        paths = ["/api/v1/users", "/api/v1/tasks"]
        rules = PathRulesConfig(keep=["v2"])
        result = apply_path_rules(paths, rules)
        assert result == {"/api/v1/users": "/users", "/api/v1/tasks": "/tasks"}

    def test_keep_without_auto_strip(self) -> None:
        """If auto_strip is off, there's nothing stripped, so keep has no effect."""
        paths = ["/api/v1/users", "/api/v1/tasks"]
        rules = PathRulesConfig(auto_strip_prefix=False, keep=["v1"])
        result = apply_path_rules(paths, rules)
        assert result == {"/api/v1/users": "/api/v1/users", "/api/v1/tasks": "/api/v1/tasks"}


# ------------------------------------------------------------------ #
# apply_path_rules — skip_segments
# ------------------------------------------------------------------ #


class TestSkipSegments:
    """Test that 'skip_segments' removes specific segments."""

    def test_skip_segment_after_strip(self) -> None:
        paths = ["/api/v1/internal/users", "/api/v1/internal/tasks"]
        rules = PathRulesConfig(skip_segments=["internal"])
        result = apply_path_rules(paths, rules)
        assert result == {
            "/api/v1/internal/users": "/users",
            "/api/v1/internal/tasks": "/tasks",
        }

    def test_skip_without_auto_strip(self) -> None:
        paths = ["/api/users", "/api/tasks"]
        rules = PathRulesConfig(auto_strip_prefix=False, skip_segments=["api"])
        result = apply_path_rules(paths, rules)
        assert result == {"/api/users": "/users", "/api/tasks": "/tasks"}

    def test_skip_nonexistent_segment(self) -> None:
        paths = ["/users", "/tasks"]
        rules = PathRulesConfig(auto_strip_prefix=False, skip_segments=["api"])
        result = apply_path_rules(paths, rules)
        assert result == {"/users": "/users", "/tasks": "/tasks"}


# ------------------------------------------------------------------ #
# apply_path_rules — strip_prefix
# ------------------------------------------------------------------ #


class TestExplicitStripPrefix:
    """Test explicit strip_prefix overrides auto-strip."""

    def test_explicit_prefix(self) -> None:
        paths = ["/api/v1/users", "/api/v1/tasks"]
        rules = PathRulesConfig(strip_prefix="/api")
        result = apply_path_rules(paths, rules)
        assert result == {"/api/v1/users": "/v1/users", "/api/v1/tasks": "/v1/tasks"}

    def test_explicit_overrides_auto(self) -> None:
        """Auto would strip /api/v1, but explicit strips only /api."""
        paths = ["/api/v1/users", "/api/v1/tasks"]
        # auto_strip is True by default, but strip_prefix should win.
        rules = PathRulesConfig(strip_prefix="/api")
        result = apply_path_rules(paths, rules)
        # With explicit /api stripped, v1 remains.
        assert result["/api/v1/users"] == "/v1/users"
        assert result["/api/v1/tasks"] == "/v1/tasks"

    def test_explicit_with_non_matching_path(self) -> None:
        """If a path doesn't start with the explicit prefix, it's unchanged."""
        paths = ["/api/v1/users", "/other/endpoint"]
        rules = PathRulesConfig(strip_prefix="/api/v1")
        result = apply_path_rules(paths, rules)
        assert result == {"/api/v1/users": "/users", "/other/endpoint": "/other/endpoint"}

    def test_explicit_strip_with_keep(self) -> None:
        """Keep works with explicit strip_prefix too."""
        paths = ["/api/v1/users", "/api/v1/tasks"]
        rules = PathRulesConfig(strip_prefix="/api/v1", keep=["v1"])
        result = apply_path_rules(paths, rules)
        assert result == {"/api/v1/users": "/v1/users", "/api/v1/tasks": "/v1/tasks"}


# ------------------------------------------------------------------ #
# apply_path_rules — collapse
# ------------------------------------------------------------------ #


class TestCollapse:
    """Test collapse mapping."""

    def test_collapse_basic(self) -> None:
        paths = ["/api/v1/users/{id}/settings", "/api/v1/users"]
        rules = PathRulesConfig(
            collapse={"/api/v1/users/{id}/settings": "user-settings"}
        )
        result = apply_path_rules(paths, rules)
        # Collapsed path has a leading slash added.
        assert result["/api/v1/users/{id}/settings"] == "/user-settings"
        # Non-collapsed path is auto-stripped normally.
        # Single non-collapsed path: /api/v1/users, the common prefix for
        # the auto-strip computation considers ALL paths including collapsed ones.
        # The auto prefix of all paths is /api/v1, but since the collapsed path
        # is excluded from further processing, the auto prefix is computed from
        # all paths.
        assert result["/api/v1/users"] == "/users"

    def test_collapse_with_leading_slash(self) -> None:
        """If the collapse value already has a leading slash, don't double it."""
        paths = ["/api/v1/health"]
        rules = PathRulesConfig(collapse={"/api/v1/health": "/status"})
        result = apply_path_rules(paths, rules)
        assert result["/api/v1/health"] == "/status"

    def test_collapse_overrides_all_rules(self) -> None:
        """Collapse takes precedence over strip_prefix and auto_strip."""
        paths = ["/api/v1/users", "/api/v1/tasks"]
        rules = PathRulesConfig(
            strip_prefix="/api",
            skip_segments=["v1"],
            collapse={"/api/v1/users": "people"},
        )
        result = apply_path_rules(paths, rules)
        # Collapsed path ignores all other rules.
        assert result["/api/v1/users"] == "/people"
        # Non-collapsed path uses strip_prefix + skip.
        assert result["/api/v1/tasks"] == "/tasks"


# ------------------------------------------------------------------ #
# apply_path_rules — precedence
# ------------------------------------------------------------------ #


class TestPrecedence:
    """Test the full precedence chain: collapse > strip_prefix > auto."""

    def test_collapse_beats_strip_prefix(self) -> None:
        paths = ["/api/v1/special"]
        rules = PathRulesConfig(
            strip_prefix="/api/v1",
            collapse={"/api/v1/special": "magic"},
        )
        result = apply_path_rules(paths, rules)
        assert result["/api/v1/special"] == "/magic"

    def test_strip_prefix_beats_auto(self) -> None:
        paths = ["/api/v1/users", "/api/v1/tasks"]
        # Auto would strip /api/v1, explicit strips /api only.
        rules = PathRulesConfig(strip_prefix="/api")
        result = apply_path_rules(paths, rules)
        assert result["/api/v1/users"] == "/v1/users"

    def test_all_rules_together(self) -> None:
        paths = [
            "/api/v1/internal/users",
            "/api/v1/internal/tasks",
            "/api/v1/health",
        ]
        rules = PathRulesConfig(
            strip_prefix="/api",
            keep=["v1"],
            skip_segments=["internal"],
            collapse={"/api/v1/health": "status"},
        )
        result = apply_path_rules(paths, rules)
        # Collapsed path.
        assert result["/api/v1/health"] == "/status"
        # Non-collapsed: strip /api, keep v1, skip internal.
        assert result["/api/v1/internal/users"] == "/v1/users"
        assert result["/api/v1/internal/tasks"] == "/v1/tasks"


# ------------------------------------------------------------------ #
# apply_path_rules — edge cases
# ------------------------------------------------------------------ #


class TestEdgeCases:
    """Test edge cases and boundary conditions."""

    def test_empty_paths_list(self) -> None:
        rules = PathRulesConfig()
        assert apply_path_rules([], rules) == {}

    def test_root_path_only(self) -> None:
        rules = PathRulesConfig()
        result = apply_path_rules(["/"], rules)
        assert result == {"/": "/"}

    def test_multiple_root_paths(self) -> None:
        rules = PathRulesConfig()
        result = apply_path_rules(["/", "/"], rules)
        # Duplicate keys collapse in dict, so only one entry.
        assert result == {"/": "/"}

    def test_paths_with_trailing_slash(self) -> None:
        """Trailing slashes produce an empty segment that is ignored."""
        paths = ["/api/v1/users/", "/api/v1/tasks/"]
        rules = PathRulesConfig()
        result = apply_path_rules(paths, rules)
        assert result["/api/v1/users/"] == "/users"
        assert result["/api/v1/tasks/"] == "/tasks"

    def test_multiple_path_parameters(self) -> None:
        """With two paths sharing /api/v1/users/{user_id}, the prefix is
        trimmed so the shorter path retains at least one segment."""
        paths = [
            "/api/v1/users/{user_id}/posts/{post_id}",
            "/api/v1/users/{user_id}/posts",
        ]
        rules = PathRulesConfig()
        result = apply_path_rules(paths, rules)
        # Common prefix: /api/v1/users/{user_id} (4 segments, shorter has 5)
        assert result["/api/v1/users/{user_id}/posts/{post_id}"] == "/posts/{post_id}"
        assert result["/api/v1/users/{user_id}/posts"] == "/posts"

    def test_default_rules_no_changes_needed(self) -> None:
        """Paths with no common prefix and default rules are unchanged."""
        paths = ["/users", "/tasks", "/health"]
        rules = PathRulesConfig()
        result = apply_path_rules(paths, rules)
        assert result == {"/users": "/users", "/tasks": "/tasks", "/health": "/health"}

    def test_path_parameter_not_collapsed(self) -> None:
        """Path parameters in the path are preserved (only path_to_command_parts strips them)."""
        paths = ["/api/users/{id}", "/api/tasks"]
        rules = PathRulesConfig()
        result = apply_path_rules(paths, rules)
        assert result["/api/users/{id}"] == "/users/{id}"

    def test_auto_strip_with_identical_paths(self) -> None:
        """When all paths are identical, the prefix is trimmed to avoid fully
        consuming them; each path retains its last segment."""
        paths = ["/api/v1/users", "/api/v1/users"]
        rules = PathRulesConfig()
        result = apply_path_rules(paths, rules)
        # Common prefix trimmed to /api/v1, leaving /users.
        assert result["/api/v1/users"] == "/users"


# ------------------------------------------------------------------ #
# apply_path_rules — real-world Corelia paths
# ------------------------------------------------------------------ #


class TestRealWorldPaths:
    """Test with complex real-world API paths like Corelia's."""

    def test_corelia_api_paths(self) -> None:
        paths = [
            "/api/mcp-router/tools",
            "/api/mcp-router/call-tool",
            "/api/admin/users/",
            "/api/admin/groups/",
            "/api/tasks/",
            "/api/tasks/{id}",
            "/api/mcp/health-check",
        ]
        rules = PathRulesConfig()
        result = apply_path_rules(paths, rules)
        assert result["/api/mcp-router/tools"] == "/mcp-router/tools"
        assert result["/api/admin/users/"] == "/admin/users"
        assert result["/api/tasks/"] == "/tasks"
        assert result["/api/tasks/{id}"] == "/tasks/{id}"
        assert result["/api/mcp/health-check"] == "/mcp/health-check"

    def test_corelia_paths_with_collapse(self) -> None:
        paths = [
            "/api/mcp-router/tools",
            "/api/mcp-router/call-tool",
            "/api/admin/users/",
        ]
        rules = PathRulesConfig(
            collapse={
                "/api/mcp-router/tools": "tools",
                "/api/mcp-router/call-tool": "call",
            }
        )
        result = apply_path_rules(paths, rules)
        assert result["/api/mcp-router/tools"] == "/tools"
        assert result["/api/mcp-router/call-tool"] == "/call"
        # Non-collapsed path is still auto-stripped.
        assert result["/api/admin/users/"] == "/admin/users"

    def test_github_style_paths(self) -> None:
        paths = [
            "/repos/{owner}/{repo}/pulls",
            "/repos/{owner}/{repo}/issues",
            "/repos/{owner}/{repo}/commits",
        ]
        rules = PathRulesConfig()
        result = apply_path_rules(paths, rules)
        # Common prefix: /repos/{owner}/{repo}
        assert result["/repos/{owner}/{repo}/pulls"] == "/pulls"
        assert result["/repos/{owner}/{repo}/issues"] == "/issues"
        assert result["/repos/{owner}/{repo}/commits"] == "/commits"

    def test_versioned_api_with_keep(self) -> None:
        paths = [
            "/api/v2/customers",
            "/api/v2/orders",
            "/api/v2/products",
        ]
        rules = PathRulesConfig(keep=["v2"])
        result = apply_path_rules(paths, rules)
        assert result["/api/v2/customers"] == "/v2/customers"
        assert result["/api/v2/orders"] == "/v2/orders"
        assert result["/api/v2/products"] == "/v2/products"

    def test_nested_resource_paths(self) -> None:
        """Deeply nested REST resources.  The common prefix is trimmed to
        /api/v1/orgs/{org_id} so the shortest path retains at least one
        segment."""
        paths = [
            "/api/v1/orgs/{org_id}/teams/{team_id}/members",
            "/api/v1/orgs/{org_id}/teams/{team_id}/repos",
            "/api/v1/orgs/{org_id}/teams",
        ]
        rules = PathRulesConfig()
        result = apply_path_rules(paths, rules)
        assert result["/api/v1/orgs/{org_id}/teams/{team_id}/members"] == "/teams/{team_id}/members"
        assert result["/api/v1/orgs/{org_id}/teams/{team_id}/repos"] == "/teams/{team_id}/repos"
        assert result["/api/v1/orgs/{org_id}/teams"] == "/teams"

    def test_mixed_versioned_endpoints(self) -> None:
        """When version segments differ, only shared prefix is stripped."""
        paths = [
            "/api/v1/users",
            "/api/v2/users",
        ]
        rules = PathRulesConfig()
        result = apply_path_rules(paths, rules)
        assert result["/api/v1/users"] == "/v1/users"
        assert result["/api/v2/users"] == "/v2/users"


# ------------------------------------------------------------------ #
# Integration: apply_path_rules + path_to_command_parts
# ------------------------------------------------------------------ #


class TestIntegration:
    """Test the full pipeline: apply rules then convert to command parts."""

    def test_full_pipeline(self) -> None:
        paths = ["/api/v1/users", "/api/v1/users/{id}", "/api/v1/users/{id}/settings"]
        rules = PathRulesConfig()
        transformed = apply_path_rules(paths, rules)

        parts = {orig: path_to_command_parts(t) for orig, t in transformed.items()}
        assert parts["/api/v1/users"] == ["users"]
        assert parts["/api/v1/users/{id}"] == ["users"]
        assert parts["/api/v1/users/{id}/settings"] == ["users", "settings"]

    def test_pipeline_with_collapse(self) -> None:
        paths = ["/api/v1/users/{id}/settings"]
        rules = PathRulesConfig(
            collapse={"/api/v1/users/{id}/settings": "user-settings"}
        )
        transformed = apply_path_rules(paths, rules)
        parts = path_to_command_parts(transformed["/api/v1/users/{id}/settings"])
        assert parts == ["user-settings"]

    def test_pipeline_with_all_rules(self) -> None:
        paths = [
            "/api/v2/internal/projects",
            "/api/v2/internal/projects/{id}",
            "/api/v2/health",
        ]
        rules = PathRulesConfig(
            strip_prefix="/api",
            keep=["v2"],
            skip_segments=["internal"],
            collapse={"/api/v2/health": "health-check"},
        )
        transformed = apply_path_rules(paths, rules)

        assert path_to_command_parts(transformed["/api/v2/internal/projects"]) == ["v2", "projects"]
        assert path_to_command_parts(transformed["/api/v2/internal/projects/{id}"]) == ["v2", "projects"]
        assert path_to_command_parts(transformed["/api/v2/health"]) == ["health-check"]
