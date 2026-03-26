# publishing_mcp — Fix Roadmap

Testing method: static analysis + Python AST inspection + import simulation
(PyPI is network-blocked in the build sandbox; runtime tests require credentials)

---

## 🔴 P0 — Blocker (server cannot run at all)

### 1. Package naming collision with `python-substack`

**File:** `substack/client.py` → `_import_substack()`

**What happens:** Our project has a `substack/` directory. Python registers this
as the `substack` package on `sys.path`. When `_import_substack()` runs:

```python
from substack import Api      # ← resolves to OUR substack/__init__.py
from substack.post import Post
```

…Python finds our empty `substack/__init__.py` instead of the installed
`python-substack` library. Result: `ImportError: cannot import name 'Api'`.
Reproduced in sandbox with a direct import simulation.

**Fix:** Wrap all source code in a top-level `publishing_mcp/` namespace package
so our internal packages are `publishing_mcp.substack` and `publishing_mcp.medium`,
not bare `substack` / `medium`.

```
publishing_mcp/
    __init__.py
    substack/
        __init__.py
        client.py       ← _import_substack() now works; bare `from substack import Api`
        tools.py        ← update imports to `from publishing_mcp.substack.client import ...`
    medium/
        __init__.py
        client.py
        tools.py        ← update imports to `from publishing_mcp.medium.client import ...`
server.py               ← update imports to `from publishing_mcp.*.tools import ...`
```

Also update `pyproject.toml`:
```toml
[tool.hatch.build.targets.wheel]
packages = ["publishing_mcp"]

[project.scripts]
publishing-mcp = "server:main"   # server.py stays at root — no change needed
```

---

## 🟠 P1 — Correctness (wrong behavior once running)

### 2. README examples use wrong parameter names

**File:** `README.md`, "Example workflows" section

| README shows | Actual parameter |
|---|---|
| `substack_create_draft(subdomain="myblog", ..., body_html="<p>...</p>")` | No `subdomain`; field is `body_markdown`, not `body_html` |
| `substack_publish_post(subdomain="myblog", post_id=12345678)` | No `subdomain` parameter |

These examples will produce validation errors at runtime. Fix by updating the
README examples to match `SubstackCreateDraftInput` and `SubstackPublishPostInput`.

### 3. README incorrectly labels Medium endpoints as "GraphQL"

**File:** `README.md` tools table; also referenced in "Known limitations"

The `medium_list_posts` and `medium_get_post_stats` implementations use:
- `GET /@{username}/latest?format=json`
- `GET /p/{post_id}/stats?format=json`

These are informal JSON endpoints, not GraphQL. The README table says
"Unofficial GraphQL" which is misleading. Fix the table labels to
"Unofficial JSON API".

### 4. `pyproject.toml` Python version constraint too high

**File:** `pyproject.toml`

```toml
requires-python = ">=3.11"   # ← actual code uses only 3.9+ features
```

The codebase uses `list[X]` / `dict[K, V]` generics in annotations (PEP 585,
Python 3.9+) and `X | Y` union syntax (PEP 604, Python 3.10+). No 3.11-specific
features were found. Change to `>=3.10`.

### 5. Verify `python-substack` API method names

**File:** `substack/client.py`

The following `python-substack` method calls have not been verified against the
library source (v0.1.x). They need a live check:

| Call | Verify |
|---|---|
| `api.get_user_id()` | Does this method exist? |
| `api.get_user_primary_publication()` | Return type: dict or object? |
| `api.get_user_publications()` | Return type: list of dict or objects? |
| `api.get_publication_subscribers(pub_id)` | Parameter: int or str? |
| `api.get_drafts()` | Method name correct? |
| `api.post_draft(post)` | Accepts a `Post` object? Returns dict? |
| `api.publish_draft(post_id, send_email=True)` | `send_email` kwarg supported? |
| `Post(title, subtitle, user_id, audience, write_comment_permissions)` | Correct constructor? |
| `post.add({"type": "paragraph", "content": para})` | Correct `add()` signature? |

**Action:** Clone `python-substack` and diff method signatures before first run.

---

## 🟡 P2 — Reliability (works but fragile)

### 6. Medium `list_posts` unofficial endpoint may be dead

**File:** `medium/client.py` → `list_posts()`

`GET https://medium.com/@{username}/latest?format=json` is a legacy Medium
endpoint that has been partially or fully disabled at various points. Medium
no longer officially supports `?format=json` on profile pages.

**Options to investigate (in order of reliability):**
1. Medium RSS feed: `https://medium.com/feed/@{username}` — publicly available,
   returns Atom XML, no auth required. Parse with `feedparser` or stdlib `xml`.
2. Medium GraphQL `/_/graphql` with `PostsQuery` — requires session cookie,
   schema undocumented.
3. Keep current approach but add fallback to RSS if JSON parse fails.

### 7. Medium `get_post_stats` endpoint reliability unknown

**File:** `medium/client.py` → `get_post_stats()`

`GET https://medium.com/p/{post_id}/stats?format=json` has not been confirmed
working. Medium may require additional CSRF headers or cookie fields beyond `sid`.

**Action:** Test with a real session cookie. If blocked, investigate whether
Medium's internal stats endpoint requires `uid` cookie in addition to `sid`.

### 8. No retry / backoff logic

**Files:** `medium/client.py`, `substack/client.py`

Both clients make single-shot HTTP requests with a 20s timeout. Transient
errors (network blips, Medium/Substack 5xx) will surface as hard failures.

**Fix:** Add httpx retry logic or a simple exponential backoff wrapper for
non-4xx errors.

---

## 🟢 P3 — Polish (nice to have)

### 9. Delete orphan root-level files

`client.py` and `tools.py` at the project root are inert copies (nothing imports
them). They'll confuse anyone reading the repo. Delete after the P0 restructure.

### 10. Clean up nested `mnt/` directory

`presskit-mcp/mnt/user-data/outputs/publishing_mcp/substack/` is a leftover
artifact from a prior session. Delete it.

### 11. Add `.env.example`

No template file exists to help users configure credentials. Add:

```bash
# .env.example
MEDIUM_INTEGRATION_TOKEN=
MEDIUM_SESSION_COOKIE=
SUBSTACK_EMAIL=
SUBSTACK_PASSWORD=
SUBSTACK_PUBLICATION_URL=https://yourpub.substack.com
SUBSTACK_COOKIES_STRING=
```

And wire up `python-dotenv` in `server.py`:

```python
from dotenv import load_dotenv
load_dotenv()
```

### 12. Add structured logging

Neither client has logging. Add `logging.getLogger(__name__)` calls at
DEBUG level for request URLs and at WARNING for retries, so users can
diagnose credential/network issues without modifying code.

---

## Summary

| Priority | Count | Effort |
|---|---|---|
| P0 Blocker | 1 | ~1 hour (restructure + import updates) |
| P1 Correctness | 4 | ~30 min (README + pyproject + API verification) |
| P2 Reliability | 3 | ~2–4 hours (endpoint testing + retry logic) |
| P3 Polish | 4 | ~30 min |

**Recommended next step:** Fix P0 first (restructure into `publishing_mcp/`),
then verify python-substack method names against the library source (#5),
then do a live end-to-end test with real credentials before addressing P2.
