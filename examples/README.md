# Examples

## USPTO Data Set API

The `uspto-openapi.json` spec is the [USPTO Data Set API](https://developer.uspto.gov) — a public, no-auth API for searching US patent data. It has three endpoints covering metadata listing, field discovery, and Lucene-powered search.

### Generate the CLI

```bash
# 1. Initialize a profile from the spec
specli init --spec examples/uspto-openapi.json

# 2. Fix the base URL (the spec uses a server variable that needs resolving)
#    Edit ~/.config/specli/profiles/uspto-data-set-api.json and change:
#      "base_url": "{scheme}://developer.uspto.gov/ds-api"
#    to:
#      "base_url": "https://developer.uspto.gov/ds-api"

# 3. Build the CLI binary
specli build compile \
  -p uspto-data-set-api \
  -n uspto \
  --output ./dist

# The binary is at ./dist/uspto (about 19 MB)
```

### Build with extras

Generate skill documentation and an editable strings file alongside the binary:

```bash
specli build compile \
  -p uspto-data-set-api \
  -n uspto \
  --export-strings ./strings.json \
  --generate-skill ./skill \
  --output ./dist
```

Or skip the binary entirely and only produce the docs:

```bash
specli build compile \
  -p uspto-data-set-api \
  -n uspto \
  --export-strings ./strings.json \
  --generate-skill ./skill \
  --no-build
```

### Usage

```bash
# List all available datasets
./dist/uspto root list

# JSON output
./dist/uspto --json root list

# Get searchable fields for a dataset
./dist/uspto fields list oa_citations v1

# Search patent citation data (POST with form-encoded body)
./dist/uspto --json records create v1 oa_citations \
  -b '{"criteria": "*:*", "start": 0, "rows": 5}'

# Search with a Lucene query
./dist/uspto --json records create v1 oa_citations \
  -b '{"criteria": "Patent_PGPub:US*", "start": 0, "rows": 3}'

# Load body from a file
echo '{"criteria": "*:*", "rows": 10}' > query.json
./dist/uspto --json records create v1 oa_citations -b @query.json

# Dry run (shows the request without sending it)
./dist/uspto -n records create v1 oa_citations \
  -b '{"criteria": "*:*", "rows": 1}'

# Help for any command
./dist/uspto --help
./dist/uspto records create --help
```

### What this example demonstrates

- **Automatic content-type handling** — The `records` endpoint uses `application/x-www-form-urlencoded` in the spec. specli detects this and sends form-encoded bodies instead of JSON, matching what the API expects.
- **Path parameters** — `{dataset}` and `{version}` become positional CLI arguments.
- **Request body** — The `--body` / `-b` flag accepts JSON strings or `@filename` references.
- **No auth required** — The USPTO API is public, so no `specli auth login` step is needed.
- **Skill generation** — `--generate-skill` produces a SKILL.md with command reference, API docs, and auth setup guide.
- **String export** — `--export-strings` dumps every CLI-visible string to an editable JSON file for customization or translation.
