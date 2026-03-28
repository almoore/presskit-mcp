# presskit-mcp

A Python FastMCP server and CLI for publishing to Medium and Substack.

> **Stability warning**: Both integrations rely on unofficial/deprecated access paths.
> Medium's REST API is frozen (no new tokens issued). Both platforms' internal
> endpoints are undocumented and may change without notice.

---

## Install

```bash
pip install -e .
```

This installs two commands:
- `presskit` — CLI for direct publishing from the terminal
- `publishing-mcp` — MCP server for use with Claude Desktop/Claude Code

---

## CLI Usage (`presskit`)

### Publish a markdown file

```bash
# Publish to Medium as a draft
presskit publish medium --file docs/drafts/my-article.md

# Publish to Substack as a draft
presskit publish substack --file docs/drafts/my-article.md

# Publish to both platforms at once
presskit publish both --file docs/drafts/my-article.md

# Publish live (not draft)
presskit publish medium --file article.md --status public --tags "python,automation"

# Substack: publish to web only (no subscriber email)
presskit publish substack --file article.md --status public --no-email
```

The CLI reads YAML frontmatter from the markdown file:

```yaml
---
title: "My Article Title"
subtitle: "Optional subtitle for Substack"
tags: [python, automation, infrastructure]
---

# My Article Title

Article body here...
```

If no frontmatter `title` is present, the first `# Heading` is used.

### List posts

```bash
# List your Medium posts
presskit list medium --username alexander.g.moore1

# List Substack posts
presskit list substack --subdomain alexgmoore

# List Substack drafts
presskit drafts substack
```

### CLI options reference

```
presskit publish <platform> [options]
  --file, -f       Markdown file to publish (required)
  --status, -s     draft | public | unlisted (default: draft)
  --tags, -t       Comma-separated tags, e.g. "python,devops" (Medium)
  --subtitle       Post subtitle (Substack)
  --no-email       Publish to web only, skip subscriber email (Substack)

presskit list <platform> [options]
  --username, -u   Medium username without @ (Medium)
  --subdomain, -d  Substack subdomain (Substack)
  --limit, -n      Max results (default: 10)

presskit drafts substack
```

---

## MCP Server Usage

### Run the server

```bash
# Direct
python server.py

# Or via installed entry point
publishing-mcp
```

### Add to Claude Code (project-level)

Create `.claude/mcp.json` in your project:

```json
{
  "mcpServers": {
    "publishing": {
      "command": "python3",
      "args": ["/absolute/path/to/presskit-mcp/server.py"],
      "env": {
        "MEDIUM_SESSION_COOKIE": "your_sid_cookie",
        "MEDIUM_AUTH_STATE_FILE": "/path/to/medium-auth.json",
        "SUBSTACK_EMAIL": "you@example.com",
        "SUBSTACK_PASSWORD": "your_substack_password",
        "SUBSTACK_PUBLICATION_URL": "https://yourpub.substack.com"
      }
    }
  }
}
```

### Add to Claude Desktop

Edit `~/Library/Application Support/Claude/claude_desktop_config.json` (Mac):

```json
{
  "mcpServers": {
    "publishing": {
      "command": "python",
      "args": ["/absolute/path/to/presskit-mcp/server.py"],
      "env": {
        "MEDIUM_SESSION_COOKIE": "your_sid",
        "MEDIUM_AUTH_STATE_FILE": "/path/to/medium-auth.json",
        "SUBSTACK_EMAIL": "you@example.com",
        "SUBSTACK_PASSWORD": "your_password",
        "SUBSTACK_PUBLICATION_URL": "https://yourpub.substack.com"
      }
    }
  }
}
```

Restart Claude Desktop/Code after adding the config.

---

## FastMCP Development & Testing

presskit-mcp is built on [FastMCP](https://github.com/jlowin/fastmcp). Here's how to work with it during development.

### Inspect tools interactively

The MCP Inspector opens a browser UI where you can call any tool, see inputs/outputs, and debug:

```bash
# Using the mcp CLI (installed with mcp[cli])
mcp dev server.py
```

This starts the server and opens an interactive inspector at `http://localhost:5173`. You can:
- Browse all 15 registered tools
- Fill in parameters and execute them
- See JSON responses in real-time
- Test error handling

### Run the server with stdio transport (default)

```bash
# FastMCP defaults to stdio transport (what Claude Desktop/Code expects)
python server.py
```

### Run with SSE transport (for remote/HTTP access)

```bash
# Start as an HTTP server on port 8000
mcp run server.py --transport sse --port 8000
```

Then connect from any MCP client using `http://localhost:8000/sse`.

### Call a tool directly via `mcp call`

```bash
# One-shot tool invocation without starting a persistent server
echo '{"username": "alexander.g.moore1", "limit": 5}' | \
  mcp call server.py medium_list_posts
```

### List all registered tools

```bash
mcp tools server.py
```

### Environment variables for testing

```bash
# Medium (session-based — no integration token needed)
export MEDIUM_SESSION_COOKIE="your_sid_value"
export MEDIUM_AUTH_STATE_FILE="/path/to/medium-auth.json"

# Medium (REST API — only if you have an existing token)
export MEDIUM_INTEGRATION_TOKEN="your_token"

# Substack
export SUBSTACK_EMAIL="you@example.com"
export SUBSTACK_PASSWORD="your_password"
export SUBSTACK_PUBLICATION_URL="https://yourpub.substack.com"
```

### Running tests

```bash
python3 -m unittest discover -s tests -v
```

Tests use `sys.modules` patching to stub third-party deps — no external services needed.

---

## Tools Reference

### Medium (6 tools)

| Tool | Auth | Method |
|---|---|---|
| `medium_get_current_user` | Integration token | REST API |
| `medium_get_publications` | Integration token | REST API |
| `medium_create_post` | Integration token | REST API |
| `medium_create_post_session` | Session cookie | GraphQL + Delta OT |
| `medium_list_posts` | Session cookie | Unofficial GraphQL |
| `medium_get_post_stats` | Session cookie | Unofficial GraphQL |

### Substack (9 tools)

| Tool | Auth | Method |
|---|---|---|
| `substack_get_publication_info` | Email/password or cookie | python-substack |
| `substack_get_all_publications` | Email/password or cookie | python-substack |
| `substack_list_posts` | None (public) | Raw HTTP |
| `substack_get_post` | None (public) | Raw HTTP |
| `substack_search_publications` | None (public) | Raw HTTP |
| `substack_get_subscriber_count` | Email/password or cookie | python-substack |
| `substack_list_drafts` | Email/password or cookie | python-substack |
| `substack_create_draft` | Email/password or cookie | python-substack |
| `substack_publish_post` | Email/password or cookie | python-substack |

---

## Credentials

### Medium session cookie

Medium no longer issues API tokens. The session-based tools use browser cookies:

1. Log in to medium.com in a browser
2. Extract the `sid` cookie from DevTools → Application → Cookies
3. For full functionality, save the complete browser state with `playwright-cli state-save medium-auth.json` and set `MEDIUM_AUTH_STATE_FILE`

### Substack

Use your Substack email and password directly. If your account uses magic links only:
1. Sign out of Substack
2. Click "Sign in with password"
3. Click "Set a new password"

---

## Architecture

```
presskit-mcp/
├── server.py          # FastMCP server entry point (15 tools)
├── cli.py             # CLI entry point (presskit command)
├── medium/
│   ├── client.py      # REST API + GraphQL + Delta OT HTTP layer
│   └── tools.py       # 6 MCP tools with Pydantic input models
├── substack/
│   ├── client.py      # python-substack wrapper + raw HTTP
│   └── tools.py       # 9 MCP tools with Pydantic input models
├── tests/
│   └── test_static.py # Unit tests (no external deps needed)
├── pyproject.toml     # pip installable, entry points
└── config.yaml        # Credential reference (not loaded by code)
```

### How Medium session publishing works

Medium's web editor saves content via a delta-based Operational Transform system — not GraphQL or REST. The `medium_create_post_session` tool replicates this:

1. **GraphQL `createPost`** → creates an empty draft, returns `post_id`
2. **`POST /p/{post_id}/deltas`** → writes title + paragraphs as OT deltas
3. **GraphQL `setPostTags`** → sets up to 5 tags
4. **GraphQL `publishPost`** → publishes (optional)

This was discovered by decompiling Medium's Android APK and capturing network traffic from the web editor.

---

## Known limitations

- **Medium session cookies expire** — you'll need to refresh `medium-auth.json` periodically (re-login via browser)
- **Medium REST API** — No new integration tokens. Only existing holders can use `medium_create_post`
- **Medium inline formatting** — Bold/italic markup positions in the delta API need accurate character offsets; the current markdown parser sends plain text paragraphs
- **Substack** — No official API. All endpoints are reverse-engineered. Keep requests under 1/sec
- **Images** — Neither platform supports programmatic inline image upload via these tools yet
