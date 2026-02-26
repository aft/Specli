# Path Rules

Path rules control how API paths (e.g., `/api/v1/users/{id}`) are transformed into CLI command names (e.g., `users get <id>`). Without path rules, every segment of every path would become a nested sub-command, producing deep and awkward command hierarchies. The path rules engine strips common prefixes, removes noise segments, and collapses long paths into clean, usable commands.

## What Path Rules Solve

Consider an API with these paths:

```
/api/v1/users
/api/v1/users/{id}
/api/v1/users/{id}/settings
/api/v1/teams
/api/v1/teams/{id}
/api/v1/teams/{id}/members
```

Without path rules, the CLI commands would be:

```
specli api api v1 users list
specli api api v1 users get <id>
specli api api v1 users settings get <id>
specli api api v1 teams list
```

With the default `auto_strip_prefix: true`, the common prefix `/api/v1` is detected and stripped:

```
specli api users list
specli api users get <id>
specli api users settings get <id>
specli api teams list
```

## Configuration

Path rules are configured per profile in the `path_rules` section:

```json
{
  "name": "myapi",
  "spec": "https://api.example.com/openapi.json",
  "path_rules": {
    "auto_strip_prefix": true,
    "keep": [],
    "strip_prefix": null,
    "skip_segments": [],
    "collapse": {}
  }
}
```

## Auto-Strip Prefix

**Default: `true`**

When enabled, specli finds the longest common path prefix across all operations in the spec and strips it. This is the most useful default behavior and requires no manual configuration.

### How it works

1. Split all paths into segments: `/api/v1/users` becomes `["api", "v1", "users"]`
2. Walk segment-by-segment from the left, keeping only segments that are identical across all paths
3. Ensure no path is completely consumed by the prefix (at least one segment must remain)
4. Strip the computed prefix from all paths

### Examples

| Paths | Detected Prefix | Result |
|-------|----------------|--------|
| `/api/v1/users`, `/api/v1/tasks` | `/api/v1` | `/users`, `/tasks` |
| `/api/v1/users`, `/api/v2/tasks` | `/api` | `/v1/users`, `/v2/tasks` |
| `/users`, `/tasks` | _(none)_ | `/users`, `/tasks` |
| `/api/v1/users` _(single path)_ | _(none)_ | `/api/v1/users` |

A single path has no common prefix because there is nothing to compare against.

### Disabling auto-strip

If you want to keep the full path structure:

```json
{
  "path_rules": {
    "auto_strip_prefix": false
  }
}
```

## Keep

**Default: `[]`**

The `keep` list re-inserts specific segments that were removed during prefix stripping. This is useful when the auto-stripped prefix contains a segment you want to preserve for disambiguation.

### Example

API paths: `/api/v2/users`, `/api/v2/teams`

Auto-strip detects prefix `/api/v2`, producing `/users` and `/teams`.

If you want to keep `v2` for version clarity:

```json
{
  "path_rules": {
    "auto_strip_prefix": true,
    "keep": ["v2"]
  }
}
```

Result: `/v2/users`, `/v2/teams`

Commands:

```
specli api v2 users list
specli api v2 teams list
```

### Behavior details

- Only segments that were actually part of the stripped prefix are re-inserted
- Re-inserted segments are prepended in their original order within the prefix
- Segments in `keep` that were not stripped are ignored

## Strip Prefix

**Default: `null`**

An explicit prefix to strip. When set, this overrides `auto_strip_prefix` entirely. Use this when auto-detection does not produce the result you want.

```json
{
  "path_rules": {
    "strip_prefix": "/api/v1"
  }
}
```

This strips `/api/v1` from the beginning of every path. Paths that do not start with this prefix are left unchanged.

### Example

Paths: `/api/v1/users`, `/api/v1/teams`, `/health`

With `strip_prefix: "/api/v1"`:

| Original | Transformed |
|----------|-------------|
| `/api/v1/users` | `/users` |
| `/api/v1/teams` | `/teams` |
| `/health` | `/health` (no match, unchanged) |

## Skip Segments

**Default: `[]`**

The `skip_segments` list removes specific segments wherever they appear in any path, regardless of position. This is applied after prefix stripping.

### Example

Paths after stripping: `/users`, `/users/{id}/profile`, `/teams`, `/teams/{id}/profile`

To remove the `profile` segment:

```json
{
  "path_rules": {
    "skip_segments": ["profile"]
  }
}
```

Result:

| Original | Transformed |
|----------|-------------|
| `/users/{id}/profile` | `/users/{id}` |
| `/teams/{id}/profile` | `/teams/{id}` |
| `/users` | `/users` (no match, unchanged) |

### Common use cases

- Remove versioning segments that got through: `"skip_segments": ["v1", "v2"]`
- Remove internal namespacing: `"skip_segments": ["internal", "admin"]`
- Remove redundant nesting: `"skip_segments": ["api"]`

## Collapse

**Default: `{}`**

The `collapse` map overrides the entire path transformation for specific paths. It maps an original API path directly to a flat command name. Collapse takes the highest precedence -- it bypasses auto-strip, keep, skip, and strip_prefix entirely.

### Example

```json
{
  "path_rules": {
    "collapse": {
      "/api/v1/users/{userId}/notifications/preferences": "/user-notification-prefs"
    }
  }
}
```

The path `/api/v1/users/{userId}/notifications/preferences` becomes the command `user-notification-prefs` regardless of any other rules.

```
specli api user-notification-prefs get <userId>
```

### Use cases

- Flatten deeply nested resources into a single command
- Create custom aliases for frequently used endpoints
- Override paths where auto-detection produces poor results

## Precedence

Rules are applied in a fixed precedence order (highest wins):

```
1. collapse       -- if the exact path is in the collapse map, use the mapped value
2. strip_prefix   -- if set, strip the explicit prefix (overrides auto_strip)
3. auto_strip     -- if enabled and no strip_prefix, strip the detected common prefix
4. keep           -- re-insert kept segments after stripping
5. skip_segments  -- remove skip segments from the result
```

Within a single path transformation:

1. Check if the path is in `collapse` -- if yes, use the collapsed value and stop
2. Apply prefix stripping (explicit `strip_prefix` takes priority over `auto_strip_prefix`)
3. If `keep` segments were specified, re-insert any that were stripped
4. If `skip_segments` were specified, remove them from the result

## Path Parameters in Commands

Path parameters (segments wrapped in `{braces}`) are stripped from the command hierarchy and become positional arguments on the leaf command.

| Transformed Path | Command Parts | Positional Args |
|-----------------|---------------|-----------------|
| `/users/{id}` | `users` | `<id>` |
| `/users/{id}/settings` | `users settings` | `<id>` |
| `/teams/{teamId}/members/{memberId}` | `teams members` | `<teamId> <memberId>` |

This means the CLI command for `GET /users/{id}/settings` is:

```
specli api users settings get <id>
```

## Real-World Examples

### Stripe-like API

Paths: `/v1/customers`, `/v1/customers/{id}`, `/v1/charges`, `/v1/charges/{id}`, `/v1/refunds`

```json
{
  "path_rules": {
    "auto_strip_prefix": true
  }
}
```

Auto-strip detects prefix `/v1`. Result:

```
specli api customers list
specli api customers get cus_123
specli api charges create --body @charge.json
specli api refunds create --body @refund.json
```

### GitHub-like API

Paths: `/repos/{owner}/{repo}/issues`, `/repos/{owner}/{repo}/pulls`, `/users/{username}`

```json
{
  "path_rules": {
    "auto_strip_prefix": true
  }
}
```

Auto-strip does not strip `/repos` because `/users` does not share it. Commands are already clean:

```
specli api repos issues list <owner> <repo>
specli api repos pulls list <owner> <repo>
specli api users get <username>
```

### Deeply nested API with collapse

Paths include `/api/v3/organizations/{orgId}/workspaces/{wsId}/projects/{projId}/tasks`

```json
{
  "path_rules": {
    "strip_prefix": "/api/v3",
    "collapse": {
      "/api/v3/organizations/{orgId}/workspaces/{wsId}/projects/{projId}/tasks": "/org-tasks"
    }
  }
}
```

```
specli api org-tasks list <orgId> <wsId> <projId>
```

### Mixed versioning

Paths: `/api/v1/users`, `/api/v2/users`, `/api/v1/teams`

```json
{
  "path_rules": {
    "strip_prefix": "/api",
    "keep": ["v1", "v2"]
  }
}
```

Auto-strip would only strip `/api` (since v1 and v2 differ). Using explicit `strip_prefix` with `keep` preserves the version:

```
specli api v1 users list
specli api v2 users list
specli api v1 teams list
```
