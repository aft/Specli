# Getting Started

This guide walks you through installing specli, initializing a profile from an API spec, and making your first API call.

## Installation

### pip (into an existing environment)

```bash
pip install specli
```

### pipx (isolated install, recommended for CLI tools)

```bash
pipx install specli
```

### From source (development)

```bash
git clone https://github.com/CoreliaOS/specli.git
cd specli
pip install -e ".[dev]"
```

Verify the installation:

```bash
specli --version
```

## Step 1: Initialize from a Spec

Point `specli init` at any OpenAPI 3.0 or 3.1 spec. The spec can be a URL, a local file, or piped through stdin.

### From a URL

```bash
specli init --spec https://petstore3.swagger.io/api/v3/openapi.json
```

### From a local file

```bash
specli init --spec ./openapi.yaml --name myapi
```

### From stdin

```bash
curl -s https://api.example.com/openapi.json | specli init --spec -
```

The `init` command does the following:

1. Fetches and validates the spec (OpenAPI 3.0.x or 3.1.x)
2. Detects the API title, version, base URL, and security schemes
3. Creates a named profile in `~/.config/specli/profiles/<name>.json`
4. Writes a project-local config file `./specli.json` that sets the default profile
5. Auto-detects the longest common path prefix for clean CLI command names

The output tells you what was detected:

```
Fetching spec from: https://petstore3.swagger.io/api/v3/openapi.json
Validated: Swagger Petstore - OpenAPI 3.0 v1.0.11 (OpenAPI 3.0.2)
Detected 2 security scheme(s): petstore_auth, api_key
--> Set up auth: specli auth login swagger-petstore---openapi-3-0
Profile "swagger-petstore---openapi-3-0" created.
--> Inspect API: specli inspect paths --profile swagger-petstore---openapi-3-0
--> View auth: specli inspect auth --profile swagger-petstore---openapi-3-0
```

## Step 2: Explore the API

Use the `inspect` commands to learn what the API offers before making calls.

### List all endpoints

```bash
specli inspect paths
```

This prints a table of every operation in the spec:

```
Method  Path                      Summary              Deprecated
GET     /pet/findByStatus         Finds Pets by status
GET     /pet/findByTags           Finds Pets by tags
GET     /pet/{petId}              Find pet by ID
POST    /pet                      Add a new pet
PUT     /pet                      Update an existing pet
DELETE  /pet/{petId}              Deletes a pet
...
```

### View API metadata

```bash
specli inspect info
```

### View schemas

```bash
specli inspect schemas
```

### View security schemes

```bash
specli inspect auth
```

## Step 3: Configure Authentication

Most APIs require authentication. The `auth` commands help you set it up.

### Interactive setup

The `login` command reads the spec's security schemes and prompts you interactively:

```bash
specli auth login myapi
```

It walks through:
1. Listing available auth schemes from the spec
2. Auto-selecting (or letting you choose) a scheme
3. Prompting for the credential source

### Non-interactive setup

Use `auth add` for scripting or CI:

```bash
# API key from an environment variable
specli auth add myapi --type api_key --header X-API-Key --source env:MY_API_KEY

# Bearer token from a file
specli auth add myapi --type bearer --source file:~/.secrets/token.txt

# HTTP Basic with prompt
specli auth add myapi --type basic --source prompt
```

### Test your auth

```bash
specli auth test myapi
```

## Step 4: Make API Calls

Once initialized and authenticated, the `api` sub-command group contains all generated commands. The command hierarchy mirrors the API's resource structure.

### List resources (GET collection)

```bash
specli api pet find-by-status --status available
```

### Get a single resource (GET with path parameter)

```bash
specli api pet get 42
```

Path parameters become positional arguments. Query parameters become `--option` flags.

### Create a resource (POST)

```bash
specli api pet create --body '{"name": "Rex", "status": "available"}'
```

### Create from a file

Use the `@filename` syntax to read the request body from a file:

```bash
specli api pet create --body @new-pet.json
```

### Update a resource (PUT)

```bash
specli api pet update --body '{"id": 42, "name": "Rex", "status": "sold"}'
```

### Delete a resource (DELETE)

```bash
specli api pet delete 42
```

## Step 5: Try JSON Output for Scripting

By default, specli uses Rich formatting when your terminal is interactive and plain text when piped. You can force JSON output with the `--json` flag:

```bash
# Pretty-print JSON in the terminal
specli --json api pet get 42

# Pipe to jq for filtering
specli --json api pet find-by-status --status available | jq '.[].name'

# Save to a file
specli --json api pet get 42 -o pet.json
```

Other output options:

```bash
# Plain text (tab-separated, good for awk/cut)
specli --plain api pet find-by-status --status available

# Suppress diagnostic messages (only data on stdout)
specli --quiet --json api pet find-by-status --status available

# Debug mode (shows HTTP details on stderr)
specli --verbose api pet get 42
```

## Step 6: Dry-Run Mode

Preview what specli would send without making a real HTTP request:

```bash
specli --dry-run api pet create --body '{"name": "Rex"}'
```

Output (on stderr):

```
[dry-run] POST https://petstore3.swagger.io/api/v3/pet
  Header: Content-Type: application/json
  Body (JSON): {
    "name": "Rex"
  }
```

This is useful for debugging auth headers, verifying parameter mapping, and building CI scripts.

## Next Steps

- [Configuration](configuration.md) -- config files, profiles, precedence, environment variables
- [Authentication](auth.md) -- auth plugins, credential sources, security
- [Path Rules](path-rules.md) -- customize how API paths map to CLI commands
- [Plugins](plugins.md) -- extend specli with custom hooks
- [Skill Generation](skill-generation.md) -- generate Claude Code skill files
