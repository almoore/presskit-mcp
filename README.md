# publishing_mcp

A Python FastMCP server exposing Medium and Substack as MCP tools.

> ⚠️ **Stability warning**: Both integrations rely on unofficial/deprecated access paths.
> Medium's REST API is frozen (no new tokens issued). Both platforms' GraphQL/cookie
> endpoints are undocumented and may change without notice.

---

## Tools

### Medium (5 tools)

| Tool | Auth needed | Access path |
|---|---|---|
| `medium_get_current_user` | `MEDIUM_INTEGRATION_TOKEN` | REST API |
| `medium_get_publications` | `MEDIUM_INTEGRATION_TOKEN` | REST API |
| `medium_create_post` | `MEDIUM_INTEGRATION_TOKEN` | REST API |
| `medium_list_posts` | `MEDIUM_SESSION_COOKIE` | Unofficial GraphQL |
| `medium_get_post_stats` | `MEDIUM_SESSION_COOKIE` | Unofficial GraphQL |

### Substack (9 tools)

Built on [python-substack](https://github.com/ma2za/python-substack) — uses proper
ProseMirror document format for rich text (bold, italic, headings, lists). Raw HTTP
approaches produce plain text only.

| Tool | Auth needed | Access path |
|---|---|---|
| `substack_get_publication_info` | Email/password or cookie | python-substack |
| `substack_get_all_publications` | Email/password or cookie | python-substack |
| `substack_list_posts` | None (public) | Raw HTTP |
| `substack_get_post` | None (public) | Raw HTTP |
| `substack_search_publications` | None (public) | Raw HTTP |
| `substack_get_subscriber_count` | Email/password or cookie (admin) | python-substack |
| `substack_list_drafts` | Email/password or cookie | python-substack |
| `substack_create_draft` | Email/password or cookie | python-substack Post object |
| `substack_publish_post` | Email/password or cookie | python-substack |

---

## Setup

### 1. Install

```bash
pip install mcp[cli] httpx pydantic python-substack python-dotenv
# or
pip install -e .
```

### 2. Get credentials

**Medium integration token** (REST API — for existing token holders only):
- Medium Settings → Security and Apps → Integration tokens

**Medium session cookie** (unofficial GraphQL):
- Log in to medium.com → DevTools → Application → Cookies → `sid`

**Substack email + password** (recommended):
- Use your Substack login credentials directly
- If your account has no password: Sign out → "Sign in with password" → "Set a new password"

**Substack cookie string** (fallback if no password):
- Log in to substack.com → DevTools → Network → any request → copy the `Cookie` header value

### 3. Set environment variables

```bash
export MEDIUM_INTEGRATION_TOKEN="your_token"
export MEDIUM_SESSION_COOKIE="your_medium_sid"
export SUBSTACK_EMAIL="you@example.com"
export SUBSTACK_PASSWORD="your_substack_password"
export SUBSTACK_PUBLICATION_URL="https://yourpub.substack.com"
```

### 4. Run

```bash
python server.py
```

### 5. Add to Claude Desktop

In `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "publishing": {
      "command": "python",
      "args": ["/absolute/path/to/publishing_mcp/server.py"],
      "env": {
        "MEDIUM_INTEGRATION_TOKEN": "your_token",
        "MEDIUM_SESSION_COOKIE": "your_sid",
        "SUBSTACK_EMAIL": "you@example.com",
        "SUBSTACK_PASSWORD": "your_password",
        "SUBSTACK_PUBLICATION_URL": "https://yourpub.substack.com"
      }
    }
  }
}
```

---

## Example workflows

### Cross-post a markdown article to Medium as a draft
```
1. medium_get_current_user  → get user_id
2. medium_create_post(user_id=..., title="...", content="...", publish_status="draft")
```

### Check your latest Substack post performance
```
1. substack_list_posts(subdomain="myblog", limit=5, sort="new")
2. substack_get_post(subdomain="myblog", slug="my-latest-post")
```

### Create and publish a Substack post
```
1. substack_create_draft(subdomain="myblog", title="...", body_html="<p>...</p>")
   → returns { "id": 12345678, ... }
2. substack_publish_post(subdomain="myblog", post_id=12345678)   ⚠️ sends emails
```

---

## Known limitations

- **Medium REST API**: No new integration tokens. Only 3 endpoints work: get user, get publications, create post. Cannot update existing posts or retrieve post lists.
- **Medium GraphQL**: Pagination limited to ~25 posts per request. Schema is undocumented and can change.
- **Substack**: No official API. All endpoints are reverse-engineered internal APIs. The `connect.sid` cookie approach works but is against the spirit (and possibly letter) of Substack's ToS — use responsibly and only for your own content.
- **Rate limits**: Both platforms will throttle aggressive requests. Keep Substack calls under 1 req/sec.
