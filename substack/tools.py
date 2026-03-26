"""
Substack MCP tools.

Tools registered here:
  - substack_get_publication_info    Read      python-substack Api
  - substack_get_all_publications    Read      python-substack Api
  - substack_list_posts              Read      Raw HTTP (public)
  - substack_get_post                Read      Raw HTTP (public)
  - substack_search_publications     Read      Raw HTTP (public)
  - substack_get_subscriber_count    Read      python-substack Api (admin)
  - substack_list_drafts             Read      python-substack Api
  - substack_create_draft            Write     python-substack Post object
  - substack_publish_post            Write     python-substack Api
"""

import json
from typing import Optional

import httpx
from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field

from substack.client import (
    create_draft,
    get_all_publications,
    get_drafts,
    get_post,
    get_publication_info,
    get_subscriber_count,
    list_posts,
    publish_post,
    search_publications,
)

# ── Input models ──────────────────────────────────────────────────────────────


class SubstackListPostsInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    subdomain: str = Field(
        ...,
        description=(
            "Publication subdomain (without .substack.com). "
            "Example: 'myblog' for myblog.substack.com."
        ),
        min_length=1,
    )
    limit: int = Field(
        default=25,
        description="Number of posts to return (1–50).",
        ge=1,
        le=50,
    )
    offset: int = Field(
        default=0,
        description="Pagination offset. Use multiples of `limit` to page through results.",
        ge=0,
    )
    sort: str = Field(
        default="new",
        description="Sort order: 'new' (most recent first) or 'top' (most popular).",
        pattern="^(new|top)$",
    )


class SubstackGetPostInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    subdomain: str = Field(
        ...,
        description="Publication subdomain.",
        min_length=1,
    )
    slug: str = Field(
        ...,
        description=(
            "Post slug from the URL. "
            "Example: from 'myblog.substack.com/p/my-post-title' use 'my-post-title'."
        ),
        min_length=1,
    )


class SubstackCreateDraftInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    title: str = Field(
        ...,
        description="Post title.",
        min_length=1,
        max_length=255,
    )
    body_markdown: str = Field(
        ...,
        description=(
            "Post body in Markdown. Separate paragraphs with blank lines. "
            "Supports **bold**, *italic*, ## headings, - bullet lists, > blockquotes. "
            "Converted to Substack's ProseMirror format by python-substack."
        ),
        min_length=1,
    )
    subtitle: Optional[str] = Field(
        default=None,
        description="Optional subtitle shown in email preview and post header.",
        max_length=500,
    )
    audience: str = Field(
        default="everyone",
        description=(
            "Who can read the post: 'everyone' | 'only_paid' | 'founding' | 'only_free'. "
            "Default is 'everyone'."
        ),
        pattern="^(everyone|only_paid|founding|only_free)$",
    )
    write_comment_permissions: str = Field(
        default="everyone",
        description="Who can comment: 'everyone' | 'only_paid' | 'none'.",
        pattern="^(everyone|only_paid|none)$",
    )
    publication_url: Optional[str] = Field(
        default=None,
        description=(
            "Override the default publication URL for this call. "
            "Example: 'https://myblog.substack.com'. "
            "Falls back to SUBSTACK_PUBLICATION_URL env var if not set."
        ),
    )


class SubstackPublishPostInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    post_id: int = Field(
        ...,
        description=(
            "Integer post ID returned by substack_create_draft. Example: 12345678."
        ),
        gt=0,
    )
    send_email: bool = Field(
        default=True,
        description=(
            "True (default) sends a newsletter email to subscribers. "
            "False publishes to web only — useful for testing."
        ),
    )
    publication_url: Optional[str] = Field(
        default=None,
        description="Override publication URL. Falls back to SUBSTACK_PUBLICATION_URL.",
    )


class SubstackSearchInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    query: str = Field(
        ...,
        description="Search keyword or phrase. Example: 'machine learning'.",
        min_length=1,
        max_length=200,
    )
    page: int = Field(default=0, description="Zero-based page index.", ge=0)
    limit: int = Field(default=10, description="Results per page (1–100).", ge=1, le=100)


class SubstackPublicationUrlInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    publication_url: Optional[str] = Field(
        default=None,
        description=(
            "Full publication URL. Example: 'https://myblog.substack.com'. "
            "Falls back to SUBSTACK_PUBLICATION_URL env var if not set."
        ),
    )


# ── Error handler ─────────────────────────────────────────────────────────────


def _handle_error(e: Exception, context: str) -> str:
    if isinstance(e, httpx.HTTPStatusError):
        status = e.response.status_code
        if status == 401:
            return (
                f"Error ({context}): Authentication failed. "
                "Check SUBSTACK_EMAIL/SUBSTACK_PASSWORD or SUBSTACK_COOKIES_STRING."
            )
        if status == 403:
            return (
                f"Error ({context}): Permission denied. "
                "You may not have write/admin access to this publication."
            )
        if status == 404:
            return f"Error ({context}): Not found. Verify the subdomain and slug/post_id."
        if status == 429:
            return f"Error ({context}): Rate limited. Wait a few seconds before retrying."
        return f"Error ({context}): HTTP {status} — {e.response.text[:300]}"
    if isinstance(e, ImportError):
        return f"Error ({context}): {e}"
    if isinstance(e, ValueError):
        return f"Error ({context}): {e}"
    if isinstance(e, httpx.TimeoutException):
        return f"Error ({context}): Request timed out. Retry shortly."
    return f"Error ({context}): {type(e).__name__}: {e}"


# ── Tool registration ─────────────────────────────────────────────────────────


def register_substack_tools(mcp: FastMCP) -> None:

    @mcp.tool(
        name="substack_get_publication_info",
        annotations={
            "title": "Get Substack Publication Info",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def substack_get_publication_info(params: SubstackPublicationUrlInput) -> str:
        """
        Fetch metadata for the authenticated user's primary Substack publication.

        Uses python-substack with SUBSTACK_EMAIL + SUBSTACK_PASSWORD (or
        SUBSTACK_COOKIES_STRING). Returns name, subdomain, description, author,
        and other publication metadata.

        Args:
            params (SubstackPublicationUrlInput): Optional publication_url override.

        Returns:
            str: JSON with publication metadata.
        """
        try:
            info = await get_publication_info(params.publication_url)
            return json.dumps(info, indent=2, default=str)
        except Exception as e:
            return _handle_error(e, "substack_get_publication_info")

    @mcp.tool(
        name="substack_get_all_publications",
        annotations={
            "title": "Get All Substack Publications",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def substack_get_all_publications(params: SubstackPublicationUrlInput) -> str:
        """
        List all Substack publications the authenticated user has access to.

        Useful when you manage multiple Substack publications and need to find
        the correct publication_url for other tools.

        Args:
            params (SubstackPublicationUrlInput): Optional publication_url override.

        Returns:
            str: JSON array of publication objects with id, name, subdomain, and URL.
        """
        try:
            pubs = await get_all_publications(params.publication_url)
            return json.dumps(pubs, indent=2, default=str)
        except Exception as e:
            return _handle_error(e, "substack_get_all_publications")

    @mcp.tool(
        name="substack_list_posts",
        annotations={
            "title": "List Substack Posts",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def substack_list_posts(params: SubstackListPostsInput) -> str:
        """
        List published posts for any Substack publication. No auth required for
        public content.

        Returns post metadata: title, slug, publish date, like count, comment
        count, paywall status. Use substack_get_post for full body content.

        Args:
            params (SubstackListPostsInput): subdomain, limit, offset, sort.

        Returns:
            str: JSON array of post objects including id, title, subtitle, slug,
                 post_date, reactions, comment_count, paywalled, canonical_url.
        """
        try:
            posts = await list_posts(
                params.subdomain, params.limit, params.offset, params.sort
            )
            return json.dumps(posts, indent=2, default=str)
        except Exception as e:
            return _handle_error(e, "substack_list_posts")

    @mcp.tool(
        name="substack_get_post",
        annotations={
            "title": "Get Substack Post",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def substack_get_post(params: SubstackGetPostInput) -> str:
        """
        Fetch full content and metadata for a single Substack post by slug.

        Paywalled posts return truncated content without valid auth cookies.
        Public posts return full body_html, title, subtitle, publish_date,
        reactions, and canonical_url.

        Args:
            params (SubstackGetPostInput): subdomain and post slug.

        Returns:
            str: JSON with the full post object.
        """
        try:
            post = await get_post(params.subdomain, params.slug)
            return json.dumps(post, indent=2, default=str)
        except Exception as e:
            return _handle_error(e, "substack_get_post")

    @mcp.tool(
        name="substack_search_publications",
        annotations={
            "title": "Search Substack Publications",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def substack_search_publications(params: SubstackSearchInput) -> str:
        """
        Search Substack for publications by keyword. No authentication required.

        Returns publication names, descriptions, subdomains, and subscriber
        counts where available.

        Args:
            params (SubstackSearchInput): query, page, limit.

        Returns:
            str: JSON array of matching publications with name, subdomain,
                 author_name, description, and subscriber_count.
        """
        try:
            results = await search_publications(params.query, params.page, params.limit)
            return json.dumps(results, indent=2, default=str)
        except Exception as e:
            return _handle_error(e, "substack_search_publications")

    @mcp.tool(
        name="substack_get_subscriber_count",
        annotations={
            "title": "Get Substack Subscriber Count",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def substack_get_subscriber_count(params: SubstackPublicationUrlInput) -> str:
        """
        Fetch subscriber statistics for your Substack publication.

        Requires authentication (SUBSTACK_EMAIL + SUBSTACK_PASSWORD or
        SUBSTACK_COOKIES_STRING) and admin/owner access to the publication.

        Args:
            params (SubstackPublicationUrlInput): Optional publication_url override.

        Returns:
            str: JSON with subscriber count breakdown (free, paid, total).
        """
        try:
            count = await get_subscriber_count(params.publication_url)
            return json.dumps(count, indent=2, default=str)
        except Exception as e:
            return _handle_error(e, "substack_get_subscriber_count")

    @mcp.tool(
        name="substack_list_drafts",
        annotations={
            "title": "List Substack Drafts",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def substack_list_drafts(params: SubstackPublicationUrlInput) -> str:
        """
        List all current drafts for the authenticated user's publication.

        Use this to find existing draft post IDs before calling
        substack_publish_post.

        Args:
            params (SubstackPublicationUrlInput): Optional publication_url override.

        Returns:
            str: JSON array of draft objects with id, title, subtitle, and
                 draft_created_at timestamp.
        """
        try:
            drafts = await get_drafts(params.publication_url)
            return json.dumps(drafts, indent=2, default=str)
        except Exception as e:
            return _handle_error(e, "substack_list_drafts")

    @mcp.tool(
        name="substack_create_draft",
        annotations={
            "title": "Create Substack Draft",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": True,
        },
    )
    async def substack_create_draft(params: SubstackCreateDraftInput) -> str:
        """
        Create a new Substack draft using python-substack's Post object.

        The body_markdown is converted to Substack's ProseMirror JSON format
        by python-substack — preserving bold, italic, headings, bullet lists,
        and blockquotes. This is the key improvement over raw HTTP approaches
        which produce plain text only.

        The draft is saved but NOT published. Call substack_publish_post with
        the returned post id to send it to subscribers.

        Args:
            params (SubstackCreateDraftInput): title, body_markdown, subtitle,
                audience, write_comment_permissions, optional publication_url.

        Returns:
            str: JSON with the created draft including:
                {
                  "id": int,        <- use this with substack_publish_post
                  "title": str,
                  "subtitle": str,
                  "draft_status": str
                }
        """
        try:
            draft = await create_draft(
                title=params.title,
                body_markdown=params.body_markdown,
                subtitle=params.subtitle,
                audience=params.audience,
                write_comment_permissions=params.write_comment_permissions,
                publication_url=params.publication_url,
            )
            return json.dumps(draft, indent=2, default=str)
        except Exception as e:
            return _handle_error(e, "substack_create_draft")

    @mcp.tool(
        name="substack_publish_post",
        annotations={
            "title": "Publish Substack Post",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": True,
        },
    )
    async def substack_publish_post(params: SubstackPublishPostInput) -> str:
        """
        Publish an existing Substack draft to subscribers.

        ⚠️ With send_email=True (default), this sends an email to all subscribers.
        Confirm the draft looks correct first using substack_list_drafts.
        Set send_email=False to publish to web only without sending an email.

        Use substack_create_draft first to create the draft and get the post_id.

        Args:
            params (SubstackPublishPostInput): post_id (int), send_email (bool),
                optional publication_url.

        Returns:
            str: JSON confirmation with published post status and canonical URL.
        """
        try:
            result = await publish_post(
                post_id=params.post_id,
                send_email=params.send_email,
                publication_url=params.publication_url,
            )
            return json.dumps(result, indent=2, default=str)
        except Exception as e:
            return _handle_error(e, "substack_publish_post")
