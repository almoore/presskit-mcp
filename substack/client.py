"""
Substack API client — built on python-substack (pip install python-substack).

python-substack handles:
  - Email/password auth (recommended) OR cookie-string auth
  - Proper ProseMirror document format (required for rich text)
  - Markdown-to-ProseMirror conversion
  - Publication management

Auth env vars (set at least one auth method):
  SUBSTACK_EMAIL           + SUBSTACK_PASSWORD   → email/password login
  SUBSTACK_COOKIES_STRING                         → raw cookie string fallback
  SUBSTACK_PUBLICATION_URL                        → e.g. https://myblog.substack.com

For read-only public content, no auth is required.

Rate limit: stay under ~1 req/sec to avoid blocks.
"""

import asyncio
import os
from typing import Any, Optional

import httpx

SUBSTACK_BASE = "https://substack.com"
DEFAULT_TIMEOUT = 20.0


# ── Lazy import guard ──────────────────────────────────────────────────────────

def _import_substack():
    """
    Import python-substack's Api and Post classes.

    Our local 'substack/' package shadows the installed 'python-substack'
    (which also installs as 'substack'). We temporarily manipulate sys.path
    to find the real package from site-packages.
    """
    import importlib
    import sys

    # Check if the real python-substack is already importable
    # (won't be if our local package shadows it)
    try:
        # Try importing from the installed package directly
        mod = importlib.import_module("substack")
        if hasattr(mod, "Api"):
            return mod.Api, mod.post.Post

        # Our local module was found — temporarily remove local paths
        original_path = sys.path[:]
        sys.path = [p for p in sys.path if not p.endswith("presskit-mcp") and p not in ("", ".")]

        # Force re-import from site-packages
        for key in list(sys.modules):
            if key == "substack" or key.startswith("substack."):
                del sys.modules[key]

        from substack import Api
        from substack.post import Post

        # Restore path and re-import our local module
        sys.path = original_path
        for key in list(sys.modules):
            if key == "substack" or key.startswith("substack."):
                del sys.modules[key]

        return Api, Post
    except (ImportError, AttributeError):
        raise ImportError(
            "python-substack is not installed. Run: pip install python-substack"
        )


# ── Env helpers ────────────────────────────────────────────────────────────────

def _publication_url() -> str:
    val = os.environ.get("SUBSTACK_PUBLICATION_URL", "").strip()
    if not val:
        raise ValueError(
            "SUBSTACK_PUBLICATION_URL is not set. "
            "Set it to your publication URL, e.g. https://myblog.substack.com"
        )
    return val


def _make_api(publication_url: Optional[str] = None) -> Any:
    """
    Build a python-substack Api instance.
    Prefers email/password; falls back to cookie string; falls back to no-auth.
    """
    Api, _ = _import_substack()

    pub_url = publication_url or os.environ.get("SUBSTACK_PUBLICATION_URL", "").strip()
    email = os.environ.get("SUBSTACK_EMAIL", "").strip()
    password = os.environ.get("SUBSTACK_PASSWORD", "").strip()
    cookies_string = os.environ.get("SUBSTACK_COOKIES_STRING", "").strip()

    if email and password:
        return Api(
            email=email,
            password=password,
            publication_url=pub_url or None,
        )
    elif cookies_string:
        return Api(
            cookies_string=cookies_string,
            publication_url=pub_url or None,
        )
    else:
        raise ValueError(
            "No Substack auth configured. Set either:\n"
            "  SUBSTACK_EMAIL + SUBSTACK_PASSWORD  (recommended)\n"
            "  SUBSTACK_COOKIES_STRING             (cookie fallback)"
        )


# ── Thread-executor wrapper ────────────────────────────────────────────────────

async def _run(fn, *args, **kwargs):
    """Run a synchronous python-substack call in a thread pool executor."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: fn(*args, **kwargs))


# ── Public read operations ─────────────────────────────────────────────────────

async def list_posts(
    subdomain: str,
    limit: int = 25,
    offset: int = 0,
    sort: str = "new",
) -> list[dict[str, Any]]:
    """List published posts for a publication via raw HTTP (no auth needed)."""
    async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
        r = await client.get(
            f"https://{subdomain}.substack.com/api/v1/posts",
            params={"limit": limit, "offset": offset, "sort": sort},
        )
        r.raise_for_status()
        return r.json()


async def get_post(subdomain: str, slug: str) -> dict[str, Any]:
    """Fetch a single post by slug. Public content requires no auth."""
    async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
        r = await client.get(
            f"https://{subdomain}.substack.com/api/v1/posts/{slug}"
        )
        r.raise_for_status()
        return r.json()


async def search_publications(
    query: str, page: int = 0, limit: int = 10
) -> list[dict[str, Any]]:
    """Search Substack publications by keyword. No auth required."""
    async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
        r = await client.get(
            f"{SUBSTACK_BASE}/api/v1/publication/search",
            params={
                "query": query,
                "skipExplanation": "false",
                "sort": "relevance",
                "page": page,
                "limit": limit,
            },
            headers={"User-Agent": "Mozilla/5.0"},
        )
        r.raise_for_status()
        data = r.json()
    if isinstance(data, dict):
        return data.get("publications", data.get("results", []))
    return data


# ── Auth-required operations ───────────────────────────────────────────────────

async def get_publication_info(publication_url: Optional[str] = None) -> dict[str, Any]:
    """Fetch metadata for the authenticated user's primary publication."""
    def _fetch():
        api = _make_api(publication_url)
        pub = api.get_user_primary_publication()
        return pub if isinstance(pub, dict) else vars(pub)

    return await _run(_fetch)


async def get_all_publications(publication_url: Optional[str] = None) -> list[dict[str, Any]]:
    """List all publications the authenticated user has access to."""
    def _fetch():
        api = _make_api(publication_url)
        pubs = api.get_user_publications()
        return [p if isinstance(p, dict) else vars(p) for p in (pubs or [])]

    return await _run(_fetch)


async def get_subscriber_count(publication_url: Optional[str] = None) -> dict[str, Any]:
    """
    Fetch subscriber statistics.
    Requires auth + admin access to the publication.
    """
    def _fetch():
        api = _make_api(publication_url)
        pub = api.get_user_primary_publication()
        pub_id = pub.get("id") if isinstance(pub, dict) else getattr(pub, "id", None)
        if not pub_id:
            raise ValueError("Could not determine publication ID from primary publication.")
        result = api.get_publication_subscribers(pub_id)
        return result if isinstance(result, dict) else {"data": result}

    return await _run(_fetch)


async def get_drafts(publication_url: Optional[str] = None) -> list[dict[str, Any]]:
    """List all current drafts for the authenticated user's publication."""
    def _fetch():
        api = _make_api(publication_url)
        drafts = api.get_drafts()
        return [d if isinstance(d, dict) else vars(d) for d in (drafts or [])]

    return await _run(_fetch)


async def create_draft(
    title: str,
    body_markdown: str,
    subtitle: Optional[str] = None,
    audience: str = "everyone",
    write_comment_permissions: str = "everyone",
    publication_url: Optional[str] = None,
    base_path: Optional[str] = None,
) -> dict[str, Any]:
    """
    Create a Substack draft using python-substack's Post object.

    Parses markdown into proper ProseMirror nodes: headings, paragraphs,
    blockquotes, horizontal rules, code blocks, and images. Local images
    are uploaded to Substack's CDN via api.get_image().

    audience: 'everyone' | 'only_paid' | 'founding' | 'only_free'
    write_comment_permissions: 'everyone' | 'only_paid' | 'none'
    base_path: directory for resolving relative image paths
    """
    import re

    Api, Post = _import_substack()

    def _create():
        api = _make_api(publication_url)
        user_id = api.get_user_id()

        post = Post(
            title=title,
            subtitle=subtitle or "",
            user_id=user_id,
            audience=audience,
            write_comment_permissions=write_comment_permissions,
        )

        lines = body_markdown.strip().split("\n")
        # Strip leading # title if it matches
        if lines and lines[0].startswith("# "):
            lines = lines[1:]

        image_re = re.compile(r"^!\[([^\]]*)\]\(([^)]+)\)\s*$")
        in_code_block = False
        code_lines: list[str] = []

        for line in lines:
            stripped = line.strip()

            # Code block handling
            if stripped.startswith("```"):
                if in_code_block:
                    post.code_block("\n".join(code_lines))
                    code_lines = []
                    in_code_block = False
                else:
                    in_code_block = True
                continue

            if in_code_block:
                code_lines.append(line)
                continue

            # Skip empty lines
            if not stripped:
                continue

            # Images
            img_match = image_re.match(stripped)
            if img_match:
                alt_text = img_match.group(1)
                img_src = img_match.group(2)

                # Resolve relative paths
                if not img_src.startswith(("http://", "https://")):
                    if base_path:
                        img_src = os.path.join(base_path, img_src)
                    if os.path.isfile(img_src):
                        try:
                            cdn_result = api.get_image(img_src)
                            cdn_url = cdn_result if isinstance(cdn_result, str) else cdn_result.get("url", str(cdn_result))
                            post.paragraph()
                            post.captioned_image(src=cdn_url, alt=alt_text)
                        except Exception:
                            post.paragraph(f"[Image: {alt_text}]")
                    else:
                        post.paragraph(f"[Image: {alt_text}] (file not found)")
                else:
                    post.paragraph()
                    post.captioned_image(src=img_src, alt=alt_text)
                continue

            # Headings
            if stripped.startswith("## "):
                post.heading(stripped[3:], level=2)
                continue
            if stripped.startswith("### "):
                post.heading(stripped[4:], level=3)
                continue

            # Horizontal rule
            if stripped in ("---", "***", "___"):
                post.horizontal_rule()
                continue

            # Blockquote
            if stripped.startswith("> "):
                post.blockquote(stripped[2:])
                continue

            # Regular paragraph
            post.paragraph(stripped)

        draft = api.post_draft(post.get_draft())
        return draft if isinstance(draft, dict) else vars(draft)

    return await _run(_create)


async def publish_post(
    post_id: int,
    send_email: bool = True,
    publication_url: Optional[str] = None,
) -> dict[str, Any]:
    """
    Publish an existing draft.

    send_email=True  → sends newsletter email to subscribers (default)
    send_email=False → publishes to web only, no email sent
    """
    def _publish():
        api = _make_api(publication_url)
        result = api.publish_draft(post_id, send_email=send_email)
        return result if isinstance(result, dict) else {"status": str(result)}

    return await _run(_publish)
