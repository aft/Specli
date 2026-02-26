# Skill Generation

specli can generate Claude Code skill files from any OpenAPI spec. A skill is a structured set of markdown files that Claude Code reads as context when a user invokes the skill, giving Claude detailed knowledge of the API's endpoints, parameters, and auth requirements.

## What Skills Are

In the Claude Code ecosystem, a skill is a directory containing:

- **SKILL.md** -- the main skill file with a YAML frontmatter block (name, description) followed by a quick start guide and grouped command reference
- **references/** -- additional context files that Claude Code loads alongside the skill

When a user invokes a skill, Claude Code reads these files and uses them as context for generating accurate API calls, writing integration code, or answering questions about the API.

## Generated File Structure

Running `specli generate-skill` produces three files:

```
<output-dir>/
  SKILL.md                      # Main skill file
  references/
    api-reference.md            # Full endpoint documentation
    auth-setup.md               # Authentication configuration guide
```

### SKILL.md

The main skill file contains:

- YAML frontmatter with `name` and `description` (derived from the API title)
- A quick start section showing how to install, initialize, and configure auth
- A grouped command reference organized by API resource (using tags or first path segment)

Example snippet:

```markdown
---
name: petstore
description: CLI for the Swagger Petstore
---

# Swagger Petstore - OpenAPI 3.0

CLI for the Swagger Petstore

## Quick Start

\```bash
pip install specli
specli init --spec https://petstore3.swagger.io/api/v3/openapi.json
specli auth login petstore
\```

## Available Commands

### Pet

- `specli pet update` -- Update an existing pet
- `specli pet create` -- Add a new pet to the store
- `specli pet list` -- Finds Pets by status
- `specli pet get <petId>` -- Find pet by ID
- `specli pet delete <petId>` -- Deletes a pet

### Store

- `specli store list` -- Returns pet inventories by status
- `specli store create` -- Place an order for a pet
- `specli store get <orderId>` -- Find purchase order by ID
- `specli store delete <orderId>` -- Delete purchase order by ID
```

### references/api-reference.md

A complete endpoint reference with one section per operation. Each section includes:

- HTTP method and path
- Summary and description
- Parameters table (name, location, type, required, description)
- Request body details (content types, description)
- Response status codes and descriptions

Example snippet:

```markdown
## GET /pet/{petId}

Find pet by ID

Returns a single pet

### Parameters

| Name | Location | Type | Required | Description |
|------|----------|------|----------|-------------|
| `petId` | path | integer | Yes | ID of pet to return |

### Responses

- **200**: successful operation
- **400**: Invalid ID supplied
- **404**: Pet not found
```

### references/auth-setup.md

An authentication configuration guide tailored to the spec's security schemes. For each scheme, it provides:

- The scheme type and details (header name, location, OAuth2 flows)
- A ready-to-run CLI command for setting up auth

Example for an API key scheme:

```markdown
## api_key (apiKey)

**Location**: header
**Parameter name**: api_key

\```bash
specli auth add petstore --type api_key --header api_key --source env:API_KEY_API_KEY
\```
```

## How to Generate a Skill

### Basic usage

```bash
specli generate-skill --output ./skills/myapi
```

This uses the active profile (from `./specli.json` or `--profile`).

### Specify a profile

```bash
specli generate-skill --profile myapi --output ./skills/myapi
```

### Full workflow

```bash
# 1. Initialize from a spec
specli init --spec https://api.example.com/openapi.json --name myapi

# 2. Generate the skill
specli generate-skill --profile myapi --output ./skills/myapi

# 3. Review the output
cat ./skills/myapi/SKILL.md
cat ./skills/myapi/references/api-reference.md
cat ./skills/myapi/references/auth-setup.md
```

### Output

```
Loading spec from profile: myapi
Generating skill to: ./skills/myapi
Skill generated at: skills/myapi
--> Review: cat skills/myapi/SKILL.md
```

## Customizing Output

### Operation grouping

Operations are grouped in SKILL.md by their first tag (from the spec's `tags` array). If an operation has no tags, it is grouped by its first non-parameter path segment. Group names are title-cased.

To change grouping, modify the tags in your OpenAPI spec.

### Command names

The generated command strings follow the same mapping used by the CLI:

| HTTP Method | Verb | Example |
|-------------|------|---------|
| GET (collection) | `list` | `specli users list` |
| GET (single) | `get` | `specli users get <id>` |
| POST | `create` | `specli users create` |
| PUT | `update` | `specli users update` |
| PATCH | `patch` | `specli users patch` |
| DELETE | `delete` | `specli users delete <id>` |

A GET endpoint is classified as a "collection" if its path ends with a static segment (e.g., `/pets`), or as a "single resource" if it ends with a path parameter (e.g., `/pets/{petId}`).

### Profile name in examples

The generated skill files use the profile name in example commands (e.g., `specli auth login myapi`). If you initialize with `--name production`, the examples will reference `production`.

## Template Reference

Skill files are rendered from Jinja2 templates located at:

```
src/specli/skill/templates/
  skill.md.j2          # Main SKILL.md template
  reference.md.j2      # API reference template
  auth_setup.md.j2     # Auth setup template
```

### Template variables

All three templates receive the same context dict:

| Variable | Type | Description |
|----------|------|-------------|
| `name` | string | Slugified API title (e.g., `"petstore"`) |
| `title` | string | Original API title (e.g., `"Swagger Petstore"`) |
| `description` | string | API description or `"CLI for the <title>"` |
| `spec_url` | string | Spec URL from the profile |
| `profile_name` | string | Profile name |
| `grouped_operations` | dict | Operations grouped by resource, with command strings |
| `operations` | list | All `APIOperation` instances |
| `security_schemes` | dict | Security scheme name to `SecurityScheme` instance |
| `servers` | list | `ServerInfo` instances |
| `info` | `APIInfo` | API metadata (title, version, contact, license) |

### Template filters

The templates use standard Jinja2 filters plus:

- `| upper` -- uppercases a string (used for HTTP methods in the reference)
- `| join(", ")` -- joins a list with a separator (used for content types)

### Overriding templates

To customize the generated output, you can fork the templates directory and modify the `.j2` files. The templates use `trim_blocks` and `lstrip_blocks` for clean output. Markdown in templates is not autoescaped (the `.md.j2` extension is excluded from autoescape).

## Integrating with Claude Code

Once generated, place the skill directory where Claude Code expects it:

```bash
# For a project-specific skill
cp -r ./skills/myapi .claude/skills/myapi

# Or reference it in your Claude Code configuration
```

Claude Code will discover the skill from the SKILL.md frontmatter and load the references automatically when the skill is invoked.
