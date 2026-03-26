"""
Medium MCP tools.

Tools registered here:
  - medium_get_current_user    Read   REST API  (MEDIUM_INTEGRATION_TOKEN)
  - medium_get_publications    Read   REST API  (MEDIUM_INTEGRATION_TOKEN)
  - medium_create_post         Write  REST API  (MEDIUM_INTEGRATION_TOKEN)
  - medium_list_posts          Read   Unofficial session (MEDIUM_SESSION_COOKIE)
  - medium_get_post_stats      Read   Unofficial session (MEDIUM_SESSION_COOKIE)
"""

import json
from typing import Optional

import httpx
from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field

from medium.client import (
    create_post,
    get_current_user,
    get_post_stats,
    get_publications,
    list_posts,
)


# ── Input models ───────────────────────────────────────────────────────────────


class MediumGetPublicationsInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    user_id: str = Field(
        ...,
        description=(
            "Medium user ID returned by medium_get_current_user. "
            "Example: '1234abcd5678ef'."
        ),
        min_length=1,
    )


class MediumCreatePostInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    user_id: str = Field(
        ...,
        description=(
            "Medium user ID returned by medium_get_current_user. "
            "Required even when posting to a publication."
        ),
        min_length=1,
    )
    title: str = Field(
        ...,
        description="Post title.",
        min_length=1,
        max_length=255,
    )
    content: str = Field(
        ...,
        description=(
            "Post body. Use Markdown (recommended) or HTML depending on content_format. "
            "For Markdown: supports **bold**, *italic*, ## headings, - bullets, > blockquotes. "
            "For HTML: use standard tags like <p>, <h2>, <strong>, <em>, <ul>, <blockquote>."
        ),
        min_length=1,
    )
    content_format: str = Field(
        default="markdown",
        description="Content format: 'markdown' (default) or 'html'.",
        pattern="^(markdown|html)$",
    )
    publish_status: str = Field(
        default="draft",
        description=(
            "Publication state: "
            "'draft' (default, saved but not published), "
            "'public' (published and visible to all), "
            "'unlisted' (published but not discoverable)."
        ),
        pattern="^(draft|public|unlisted)$",
    )
    tags: Optional[list[str]] = Field(
        default=None,
        description=(
            "Up to 5 topic tags. Examples: ['python', 'machine-learning', 'tutorial']. "
            "Medium enforces a 5-tag limit; extras are silently dropped."
        ),
        max_length=5,
    )
    canonical_url: Optional[str] = Field(
        default=None,
        description=(
            "Original URL if cross-posting from another source. "
            "Tells search engines the canonical source of the content."
        ),
    )
    publication_id: Optional[str] = Field(
        default=None,
        description=(
            "ID of a Medium publication to post under (from medium_get_publications). "
            "If not provided, the post is created under the user's personal profile."
        ),
    )
    notify_followers: bool = Field(
        default=False,
        description=(
            "Whether to notify followers when publishing. "
            "Only relevant when publish_status is 'public'. Default is False."
        ),
    )
    license: str = Field(
        default="all-rights-reserved",
        description=(
            "Content license. Options: 'all-rights-reserved' (default), "
            "'cc-40-by', 'cc-40-by-sa', 'cc-40-by-nd', 'cc-40-by-nc', "
            "'cc-40-by-nc-nd', 'cc-40-by-nc-sa', 'cc-40-zero', 'public-domain'."
        ),
        pattern=(
            "^(all-rights-reserved|cc-40-by|cc-40-by-sa|cc-40-by-nd|"
            "cc-40-by-nc|cc-40-by-nc-nd|cc-40-by-nc-sa|cc-40-zero|public-domain)$"
        ),
    )


class MediumListPostsInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    username: str = Field(
        ...,
        description=(
            "Medium username (without the @ symbol). "
            "Example: 'johndoe' for medium.com/@johndoe."
        ),
        min_length=1,
    )
    limit: int = Field(
        default=10,
        description="Maximum number of posts to return (1–25).",
        ge=1,
        le=25,
    )


class MediumGetPostStatsInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    post_id: str = Field(
        ...,
        description=(
            "Internal Medium post ID (alphanumeric hash). "
            "Find it in the post URL after /p/ or in the output of medium_list_posts. "
            "Example: 'a1b2c3d4e5f6'."
        ),
        min_length=1,
    )


# ── Error handler ──────────────────────────────────────────────────────────────


def _handle_error(e: Exception, context: str) -> str:
    if isinstance(e, httpx.HTTPStatusError):
        status = e.response.status_code
        if status == 401:
            return (
                f"Error ({context}): Authentication failed. "
                "For REST tools: check MEDIUM_INTEGRATION_TOKEN. "
                "For session tools: check MEDIUM_SESSION_COOKIE (may have expired)."
            )
        if status == 403:
            return (
                f"Error ({context}): Permission denied. "
                "You may not have write access to the target publication, "
                "or the post does not belong to your account."
            )
        if status == 404:
            return (
                f"Error ({context}): Not found. "
                "Verify the user_id, publication_id, username, or post_id."
            )
        if status == 429:
            return (
                f"Error ({context}): Rate limited by Medium. "
                "Wait a minute before retrying."
            )
        return f"Error ({context}): HTTP {status} — {e.response.text[:300]}"
    if isinstance(e, ValueError):
        return f"Error ({context}): {e}"
    if isinstance(e, httpx.TimeoutException):
        return f"Error ({context}): Request timed out. Retry shortly."
    return f"Error ({context}): {type(e).__name__}: {e}"


# ── Tool registration ──────────────────────────────────────────────────────────


def register_medium_tools(mcp: FastMCP) -> None:

    @mcp.tool(
        name="medium_get_current_user",
        annotations={
            "title": "Get Medium Current User",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def medium_get_current_user() -> str:
        """
        Fetch the authenticated Medium user's profile via the REST API.

        Use this first to get your user ID, which is required by
        medium_get_publications and medium_create_post.

        Auth: MEDIUM_INTEGRATION_TOKEN

        Returns:
            str: JSON with {id, username, name, url, imageUrl}.
        """
        try:
            user = await get_current_user()
            return json.dumps(user, indent=2, default=str)
        except Exception as e:
            return _handle_error(e, "medium_get_current_user")

    @mcp.tool(
        name="medium_get_publications",
        annotations={
            "title": "Get Medium Publications",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def medium_get_publications(params: MediumGetPublicationsInput) -> str:
        """
        List Medium publications the authenticated user can post to.

        Use medium_get_current_user first to retrieve your user_id.
        The publication IDs returned here can be passed as publication_id
        to medium_create_post to publish under a publication instead of your
        personal profile.

        Auth: MEDIUM_INTEGRATION_TOKEN

        Args:
            params (MediumGetPublicationsInput): user_id from medium_get_current_user.

        Returns:
            str: JSON array of publications with id, name, description, url, imageUrl.
        """
        try:
            pubs = await get_publications(params.user_id)
            return json.dumps(pubs, indent=2, default=str)
        except Exception as e:
            return _handle_error(e, "medium_get_publications")

    @mcp.tool(
        name="medium_create_post",
        annotations={
            "title": "Create Medium Post",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": True,
        },
    )
    async def medium_create_post(params: MediumCreatePostInput) -> str:
        """
        Create a new Medium post via the REST API.

        Supports Markdown or HTML content. Defaults to 'draft' so you can
        review in Medium's editor before publishing. Set publish_status='public'
        to publish immediately.

        To post under a publication, pass publication_id (from medium_get_publications).
        Without it, the post appears on the user's personal Medium profile.

        ⚠️ With publish_status='public' and notify_followers=True, this
        immediately notifies all followers. Prefer 'draft' first.

        Auth: MEDIUM_INTEGRATION_TOKEN

        Args:
            params (MediumCreatePostInput): title, content, content_format,
                publish_status, tags, canonical_url, publication_id,
                notify_followers, license.

        Returns:
            str: JSON with the created post including:
                {
                  "id": str,           <- internal Medium post ID
                  "title": str,
                  "url": str,          <- draft or published URL
                  "publishStatus": str,
                  "canonicalUrl": str
                }
        """
        try:
            post = await create_post(
                user_id=params.user_id,
                title=params.title,
                content=params.content,
                content_format=params.content_format,
                publish_status=params.publish_status,
                tags=params.tags,
                canonical_url=params.canonical_url,
                publication_id=params.publication_id,
                notify_followers=params.notify_followers,
                license=params.license,
            )
            return json.dumps(post, indent=2, default=str)
        except Exception as e:
            return _handle_error(e, "medium_create_post")

    @mcp.tool(
        name="medium_list_posts",
        annotations={
            "title": "List Medium Posts",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def medium_list_posts(params: MediumListPostsInput) -> str:
        """
        List published posts for a Medium user via the unofficial internal API.

        Returns post metadata including title, ID, slug, publish date,
        and clap count. The post 'id' field can be used with
        medium_get_post_stats to retrieve detailed stats.

        ⚠️ Uses an unofficial Medium endpoint (requires session cookie).
        May break if Medium changes their internal API.

        Auth: MEDIUM_SESSION_COOKIE

        Args:
            params (MediumListPostsInput): username (without @), limit (1–25).

        Returns:
            str: JSON array of post objects with id, title, canonicalUrl,
                 publishedAt, virtuals.totalClapCount.
        """
        try:
            posts = await list_posts(params.username, params.limit)
            return json.dumps(posts, indent=2, default=str)
        except Exception as e:
            return _handle_error(e, "medium_list_posts")

    @mcp.tool(
        name="medium_get_post_stats",
        annotations={
            "title": "Get Medium Post Stats",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def medium_get_post_stats(params: MediumGetPostStatsInput) -> str:
        """
        Fetch views, reads, and claps for a specific Medium post.

        Only the post's author can retrieve stats. The post_id is the
        alphanumeric hash found after /p/ in the post URL, or in the
        output of medium_list_posts.

        ⚠️ Uses an unofficial Medium endpoint (requires session cookie).
        Stats are only accessible for posts you authored.

        Auth: MEDIUM_SESSION_COOKIE

        Args:
            params (MediumGetPostStatsInput): post_id (internal Medium hash).

        Returns:
            str: JSON with views, reads, claps, and any other stats
                 available from Medium's internal API.
        """
        try:
            stats = await get_post_stats(params.post_id)
            return json.dumps(stats, indent=2, default=str)
        except Exception as e:
            return _handle_error(e, "medium_get_post_stats")
