"""
Medium API client.

Supports two access paths:

1. REST API (https://api.medium.com/v1) — official but frozen.
   Requires MEDIUM_INTEGRATION_TOKEN.
   Works for: get current user, get publications, create post.
   ⚠ Medium is no longer issuing new tokens — only existing holders can use this.

2. Unofficial session-based access — uses the browser `sid` cookie.
   Requires MEDIUM_SESSION_COOKIE.
   Works for: list published posts, get per-post stats.
   ⚠ Unofficial endpoints — undocumented and may change without notice.

Auth env vars:
  MEDIUM_INTEGRATION_TOKEN  — REST API Bearer token
  MEDIUM_SESSION_COOKIE     — 'sid' cookie value from browser DevTools
"""

import json
import os
from typing import Any, Optional

import httpx

MEDIUM_REST_BASE = "https://api.medium.com/v1"
MEDIUM_WEB_BASE = "https://medium.com"
DEFAULT_TIMEOUT = 20.0


# ── Auth helpers ───────────────────────────────────────────────────────────────

def _integration_token() -> str:
    token = os.environ.get("MEDIUM_INTEGRATION_TOKEN", "").strip()
    if not token:
        raise ValueError(
            "MEDIUM_INTEGRATION_TOKEN is not set. "
            "Add it to your environment: "
            "Medium Settings → Security and Apps → Integration tokens. "
            "Note: Medium is no longer issuing new tokens to new users."
        )
    return token


def _session_cookie() -> str:
    cookie = os.environ.get("MEDIUM_SESSION_COOKIE", "").strip()
    if not cookie:
        raise ValueError(
            "MEDIUM_SESSION_COOKIE is not set. "
            "Extract your 'sid' cookie from medium.com: "
            "Log in → DevTools → Application → Cookies → medium.com → copy the 'sid' value."
        )
    return cookie


def _rest_headers(token: Optional[str] = None) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token or _integration_token()}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Accept-Charset": "utf-8",
    }


def _session_headers(session_cookie: Optional[str] = None) -> dict[str, str]:
    sid = session_cookie or _session_cookie()
    return {
        "Cookie": f"sid={sid}",
        "Accept": "application/json",
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        ),
    }


def _strip_xssi(text: str) -> str:
    """
    Strip Medium's XSSI (cross-site script inclusion) prefix.

    Medium's unofficial JSON endpoints prepend a prefix like `])}while(1);</x>`
    to prevent the response from being parsed as JavaScript in a <script> tag.
    We must strip this before calling json.loads().
    """
    for prefix in ("])}while(1);</x>", "])}while(1);<x>", "])}while(1);"):
        if text.startswith(prefix):
            return text[len(prefix):]
    return text


def _parse_medium_json(text: str) -> Any:
    return json.loads(_strip_xssi(text))


# ── REST API operations ────────────────────────────────────────────────────────

async def get_current_user() -> dict[str, Any]:
    """
    Fetch the authenticated Medium user's profile via REST API.

    Returns: id, username, name, url, imageUrl.
    Auth: MEDIUM_INTEGRATION_TOKEN
    """
    async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
        r = await client.get(
            f"{MEDIUM_REST_BASE}/me",
            headers=_rest_headers(),
        )
        r.raise_for_status()
        data = r.json()
    return data.get("data", data)


async def get_publications(user_id: str) -> list[dict[str, Any]]:
    """
    List publications the authenticated user has publishing rights to.

    Returns: list of {id, name, description, url, imageUrl}.
    Auth: MEDIUM_INTEGRATION_TOKEN
    """
    async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
        r = await client.get(
            f"{MEDIUM_REST_BASE}/users/{user_id}/publications",
            headers=_rest_headers(),
        )
        r.raise_for_status()
        data = r.json()
    return data.get("data", [])


async def create_post(
    user_id: str,
    title: str,
    content: str,
    content_format: str = "markdown",
    publish_status: str = "draft",
    tags: Optional[list[str]] = None,
    canonical_url: Optional[str] = None,
    publication_id: Optional[str] = None,
    notify_followers: bool = False,
    license: str = "all-rights-reserved",
) -> dict[str, Any]:
    """
    Create a Medium post via the REST API.

    If publication_id is given, creates under that publication;
    otherwise creates under the user directly.

    content_format:  'markdown' | 'html'
    publish_status:  'draft' | 'public' | 'unlisted'
    license:         'all-rights-reserved' | 'cc-40-by' | 'cc-40-by-sa' |
                     'cc-40-by-nd' | 'cc-40-by-nc' | 'cc-40-by-nc-nd' |
                     'cc-40-by-nc-sa' | 'cc-40-zero' | 'public-domain'
    tags:            up to 5 tag strings (Medium's limit)

    Auth: MEDIUM_INTEGRATION_TOKEN
    """
    payload: dict[str, Any] = {
        "title": title,
        "contentFormat": content_format,
        "content": content,
        "publishStatus": publish_status,
        "license": license,
        "notifyFollowers": notify_followers,
    }
    if tags:
        payload["tags"] = tags[:5]  # Medium enforces a 5-tag limit
    if canonical_url:
        payload["canonicalUrl"] = canonical_url

    if publication_id:
        url = f"{MEDIUM_REST_BASE}/publications/{publication_id}/posts"
    else:
        url = f"{MEDIUM_REST_BASE}/users/{user_id}/posts"

    async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
        r = await client.post(url, headers=_rest_headers(), json=payload)
        r.raise_for_status()
        data = r.json()
    return data.get("data", data)


# ── Unofficial session-based write operations ─────────────────────────────────


def _full_cookie_string() -> str:
    """
    Build a full cookie string from the browser auth state file if available,
    falling back to just the sid cookie.

    Medium's GraphQL endpoint requires Cloudflare cookies (cf_clearance, etc.)
    in addition to the sid. The auth state file saved by playwright-cli
    contains all needed cookies.
    """
    auth_paths = [
        os.environ.get("MEDIUM_AUTH_STATE_FILE", ""),
        os.path.join(os.path.dirname(__file__), "..", "medium-auth.json"),
    ]
    for path in auth_paths:
        if path and os.path.isfile(path):
            with open(path) as f:
                state = json.load(f)
            medium_cookies = {
                c["name"]: c["value"]
                for c in state.get("cookies", [])
                if "medium.com" in c.get("domain", "")
            }
            if "sid" in medium_cookies:
                return "; ".join(f"{k}={v}" for k, v in medium_cookies.items())

    # Fallback: just the sid cookie
    return f"sid={_session_cookie()}"


def _graphql_headers() -> dict[str, str]:
    """Headers for Medium's GraphQL endpoint, using full cookie set."""
    cookie_str = _full_cookie_string()
    # Extract xsrf token from cookies if present
    xsrf = ""
    for part in cookie_str.split("; "):
        if part.startswith("xsrf="):
            xsrf = part[5:]
            break

    headers = {
        "Cookie": cookie_str,
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        ),
    }
    if xsrf:
        headers["x-xsrf-token"] = xsrf
    return headers


async def upload_image(
    image_path: str,
    post_id: Optional[str] = None,
) -> dict[str, Any]:
    """
    Upload an image to Medium's internal CDN.

    Endpoint: POST https://medium.com/_/upload?source=6
    Returns: {fileId, md5, mimeType, fileSize, fileName, imgWidth, imgHeight}

    The fileId (e.g. '1*abc123.png') is used as the data_id in image
    paragraph deltas to embed the image in a post.

    Auth: Full browser cookie set (MEDIUM_AUTH_STATE_FILE)
    """
    import mimetypes

    headers = _graphql_headers()
    upload_headers = {k: v for k, v in headers.items() if k != "Content-Type"}
    xsrf = headers.get("x-xsrf-token", "")
    if xsrf:
        upload_headers["X-XSRF-Token"] = xsrf
    upload_headers["X-Obvious-CID"] = "web"
    upload_headers["Accept"] = "application/json"

    mime_type = mimetypes.guess_type(image_path)[0] or "image/png"
    filename = os.path.basename(image_path)

    with open(image_path, "rb") as f:
        image_data = f.read()

    async with httpx.AsyncClient(timeout=60.0) as client:
        r = await client.post(
            f"{MEDIUM_WEB_BASE}/_/upload?source=6",
            headers=upload_headers,
            files={"uploadedFile": (filename, image_data, mime_type)},
            follow_redirects=True,
        )
        r.raise_for_status()
        data = _parse_medium_json(r.text)

    payload = data.get("payload", {}).get("value", {})
    if not payload.get("fileId"):
        raise ValueError(f"Image upload failed: {data}")
    return payload


def _random_hex(length: int = 4) -> str:
    """Generate a random hex string for paragraph/section names."""
    import random
    return "".join(random.choices("0123456789abcdef", k=length))


def _extract_markups(text: str) -> tuple[str, list[dict]]:
    """
    Extract bold, italic, and link markups from markdown-formatted text.

    Returns (plain_text, markups) where markups is a list of Medium markup objects:
      - type 1: bold  (**text**)
      - type 2: italic (*text*)
      - type 3: link  [text](url)

    Processes in order: links first, then bold, then italic, adjusting
    character offsets as markdown syntax is stripped.
    """
    import re

    markups: list[dict] = []
    # Process from inside out: links, bold, italic

    # Pass 1: Links [text](url)
    result = ""
    pos = 0
    for m in re.finditer(r"\[([^\]]+)\]\(([^)]+)\)", text):
        result += text[pos:m.start()]
        start = len(result)
        link_text = m.group(1)
        href = m.group(2)
        result += link_text
        end = len(result)
        markups.append({"type": 3, "start": start, "end": end, "href": href})
        pos = m.end()
    result += text[pos:]
    text = result

    # Pass 2: Bold **text**
    result = ""
    pos = 0
    for m in re.finditer(r"\*\*(.+?)\*\*", text):
        result += text[pos:m.start()]
        start = len(result)
        result += m.group(1)
        end = len(result)
        markups.append({"type": 1, "start": start, "end": end})
        pos = m.end()
    result += text[pos:]
    text = result

    # Pass 3: Italic *text* (not inside bold)
    result = ""
    pos = 0
    for m in re.finditer(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", text):
        result += text[pos:m.start()]
        start = len(result)
        result += m.group(1)
        end = len(result)
        markups.append({"type": 2, "start": start, "end": end})
        pos = m.end()
    result += text[pos:]
    text = result

    # Pass 4: Inline code `text`
    result = ""
    pos = 0
    for m in re.finditer(r"`([^`]+)`", text):
        result += text[pos:m.start()]
        start = len(result)
        result += m.group(1)
        end = len(result)
        markups.append({"type": 10, "start": start, "end": end})
        pos = m.end()
    result += text[pos:]
    text = result

    return text, markups


async def _markdown_to_paragraphs(
    title: str,
    content: str,
    base_path: Optional[str] = None,
    post_id: Optional[str] = None,
) -> list[dict[str, Any]]:
    """
    Convert markdown content to Medium's paragraph format.

    Supports: headings (##, ###), bold (**), italic (*), links [text](url),
    code blocks (```), blockquotes (>), images (![alt](path)), and plain paragraphs.

    If base_path is provided, relative image paths are resolved against it.
    Images are uploaded to Medium's CDN and inserted as type-4 paragraphs.
    """
    import re

    paragraphs: list[dict[str, Any]] = []

    # Title paragraph (type 3 = H3)
    paragraphs.append({
        "name": _random_hex(),
        "type": 3,
        "text": title,
        "markups": [],
    })

    # Strip the title from content if it starts with # Title
    lines = content.strip().split("\n")
    if lines and lines[0].lstrip().startswith("# "):
        lines = lines[1:]

    # Process content line by line, grouping into paragraphs
    in_code_block = False
    code_lines: list[str] = []
    image_re = re.compile(r"^!\[([^\]]*)\]\(([^)]+)\)\s*$")

    for line in lines:
        stripped = line.strip()

        # Code block handling
        if stripped.startswith("```"):
            if in_code_block:
                paragraphs.append({
                    "name": _random_hex(),
                    "type": 8,  # Code block
                    "text": "\n".join(code_lines),
                    "markups": [],
                })
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

        # Images: ![alt text](path/to/image.png)
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
                        img_info = await upload_image(img_src, post_id=post_id)
                        paragraphs.append({
                            "name": _random_hex(),
                            "type": 4,  # Image
                            "text": alt_text,
                            "markups": [],
                            "metadata": {
                                "id": img_info["fileId"],
                                "originalWidth": img_info.get("imgWidth", 0),
                                "originalHeight": img_info.get("imgHeight", 0),
                            },
                        })
                    except Exception as e:
                        # Image upload failed — insert as caption text
                        paragraphs.append({
                            "name": _random_hex(),
                            "type": 1,
                            "text": f"[Image: {alt_text}] (upload failed: {e})",
                            "markups": [],
                        })
                else:
                    paragraphs.append({
                        "name": _random_hex(),
                        "type": 1,
                        "text": f"[Image: {alt_text}] (file not found: {img_src})",
                        "markups": [],
                    })
            else:
                # URL-based image — Medium can sideload these
                paragraphs.append({
                    "name": _random_hex(),
                    "type": 4,
                    "text": alt_text,
                    "markups": [],
                    "iframe": {"mediaResourceId": img_src},
                })
            continue

        # Headings
        if stripped.startswith("## "):
            h_text, h_markups = _extract_markups(stripped[3:])
            paragraphs.append({
                "name": _random_hex(),
                "type": 3,
                "text": h_text,
                "markups": h_markups,
            })
            continue
        if stripped.startswith("### "):
            h_text, h_markups = _extract_markups(stripped[4:])
            paragraphs.append({
                "name": _random_hex(),
                "type": 3,
                "text": h_text,
                "markups": h_markups,
            })
            continue

        # Blockquotes
        if stripped.startswith("> "):
            bq_text, bq_markups = _extract_markups(stripped[2:])
            paragraphs.append({
                "name": _random_hex(),
                "type": 6,
                "text": bq_text,
                "markups": bq_markups,
            })
            continue

        # Horizontal rule / separator
        if stripped in ("---", "***", "___"):
            paragraphs.append({
                "name": _random_hex(),
                "type": 15,
                "text": "",
                "markups": [],
            })
            continue

        # Regular paragraph — extract inline formatting as Medium markups
        text, markups = _extract_markups(stripped)

        paragraphs.append({
            "name": _random_hex(),
            "type": 1,
            "text": text,
            "markups": markups,
        })

    return paragraphs


async def create_post_via_session(
    title: str,
    content: str,
    content_format: str = "markdown",
    publish_status: str = "draft",
    tags: Optional[list[str]] = None,
    canonical_url: Optional[str] = None,
    base_path: Optional[str] = None,
) -> dict[str, Any]:
    """
    Create a Medium post with full content using the session cookie.

    Uses Medium's internal delta-based OT save system:
    1. GraphQL createPost → empty draft
    2. Upload images to Medium CDN (if markdown references local files)
    3. POST /p/{id}/deltas → write title + body paragraphs + images
    4. GraphQL setPostTags → set tags
    5. GraphQL publishPost → publish (if requested)

    content_format:  'markdown' (default) — content is parsed into paragraphs
    publish_status:  'draft' (default) or 'public'
    tags:            up to 5 tag strings
    base_path:       directory to resolve relative image paths against

    Auth: MEDIUM_SESSION_COOKIE (plus browser auth state for Cloudflare cookies)
    """
    import random
    import time

    headers = _graphql_headers()
    cookie_str = headers["Cookie"]
    xsrf = headers.get("x-xsrf-token", "")

    async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
        # Step 1: Create empty draft via GraphQL
        r = await client.post(
            f"{MEDIUM_WEB_BASE}/_/graphql",
            headers=headers,
            json={
                "query": (
                    "mutation CreatePost($input: CreatePostInput!) { "
                    "createPost(input: $input) { id title mediumUrl } }"
                ),
                "variables": {"input": {}},
            },
            follow_redirects=True,
        )
        r.raise_for_status()
        data = r.json()

        errors = data.get("errors")
        if errors:
            raise ValueError(f"GraphQL error: {errors[0].get('message', errors)}")

        post = data.get("data", {}).get("createPost", {})
        post_id = post.get("id")
        if not post_id:
            raise ValueError("createPost returned no post ID")

        # Step 2: Write content via delta OT system (uploads images if present)
        paragraphs = await _markdown_to_paragraphs(title, content, base_path, post_id)
        lock_id = str(random.randint(1000, 9999))

        delta_headers = {
            "Cookie": cookie_str,
            "X-XSRF-Token": xsrf,
            "X-Client-Date": str(int(time.time() * 1000)),
            "X-Obvious-CID": "web",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": headers["User-Agent"],
            "Referer": f"{MEDIUM_WEB_BASE}/p/{post_id}/edit",
        }

        # Build deltas: section marker + insert all paragraphs
        deltas: list[dict[str, Any]] = []

        # Section marker (required for first save)
        deltas.append({
            "type": 8,
            "index": 0,
            "section": {
                "name": _random_hex(),
                "startIndex": 0,
            },
        })

        # Insert + update each paragraph
        for i, para in enumerate(paragraphs):
            is_image = para["type"] == 4

            # Build the insert paragraph (type 1 delta)
            insert_para: dict[str, Any] = {
                "name": para["name"],
                "type": para["type"],
                "text": "",
                "markups": [],
            }
            if is_image:
                insert_para["layout"] = 1
                insert_para["metadata"] = {}

            deltas.append({
                "type": 1,
                "index": i,
                "paragraph": insert_para,
                **({"isStartOfSection": False} if i > 0 else {}),
            })

            # Build the update paragraph (type 3 delta)
            update_para: dict[str, Any] = {
                "name": para["name"],
                "type": para["type"],
                "text": para.get("text", ""),
                "markups": para.get("markups", []),
            }
            if is_image:
                update_para["layout"] = 1
                update_para["metadata"] = para.get("metadata", {})

            if para.get("text") or para.get("metadata"):
                deltas.append({
                    "type": 3,
                    "index": i,
                    "paragraph": update_para,
                    "verifySameName": True,
                })

        delta_payload = {
            "id": post_id,
            "deltas": deltas,
            "baseRev": -1,
        }

        r2 = await client.post(
            f"{MEDIUM_WEB_BASE}/p/{post_id}/deltas?logLockId={lock_id}",
            headers=delta_headers,
            json=delta_payload,
            follow_redirects=True,
        )
        r2.raise_for_status()
        delta_response = _parse_medium_json(r2.text)
        save_result = delta_response.get("payload", {}).get("value", {})
        latest_rev = save_result.get("latestRev")

        post["title"] = save_result.get("title", title)
        post["latestRev"] = latest_rev
        post["paragraphCount"] = len(paragraphs)

        # Step 3: Set tags if provided
        if tags:
            tag_r = await client.post(
                f"{MEDIUM_WEB_BASE}/_/graphql",
                headers=headers,
                json={
                    "query": (
                        "mutation SetPostTags($targetPostId: ID!, $tagNames: [String!]!) { "
                        "setPostTags(targetPostId: $targetPostId, tagNames: $tagNames) { id title } }"
                    ),
                    "variables": {
                        "targetPostId": post_id,
                        "tagNames": tags[:5],
                    },
                },
                follow_redirects=True,
            )
            tag_data = tag_r.json()
            if tag_data.get("errors"):
                post["tag_warning"] = tag_data["errors"][0].get("message", "")

        # Step 4: Publish if requested
        if publish_status == "public":
            pub_r = await client.post(
                f"{MEDIUM_WEB_BASE}/_/graphql",
                headers=headers,
                json={
                    "query": (
                        "mutation PublishPost($postId: ID!) { "
                        "publishPost(postId: $postId) { id title mediumUrl } }"
                    ),
                    "variables": {"postId": post_id},
                },
                follow_redirects=True,
            )
            pub_data = pub_r.json()
            if pub_data.get("errors"):
                post["publish_error"] = pub_data["errors"][0].get("message", "")
            else:
                pub_post = pub_data.get("data", {}).get("publishPost", {})
                post["mediumUrl"] = pub_post.get("mediumUrl", post.get("mediumUrl"))
                post["publishStatus"] = "public"

        post["editUrl"] = f"{MEDIUM_WEB_BASE}/p/{post_id}/edit"

    return post


# ── Unofficial session-based read operations ──────────────────────────────────

async def list_posts(
    username: str,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """
    List published posts for a Medium user via the unofficial internal API.

    Requires the user to be logged in (MEDIUM_SESSION_COOKIE) to reliably
    retrieve their own posts, including member-only ones.

    Returns a list of post objects with title, id, slug, publishedAt,
    virtuals.totalClapCount, and canonicalUrl where available.

    Auth: MEDIUM_SESSION_COOKIE
    """
    async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
        r = await client.get(
            f"{MEDIUM_WEB_BASE}/@{username}/latest",
            headers=_session_headers(),
            params={"format": "json", "limit": limit},
            follow_redirects=True,
        )
        r.raise_for_status()
        data = _parse_medium_json(r.text)

    payload = data.get("payload", {})
    if not isinstance(payload, dict):
        return []

    # Posts live under payload.streamItems (type "postPreview") or
    # payload.references.Post (a dict keyed by post ID).
    post_refs: dict[str, Any] = payload.get("references", {}).get("Post", {})
    if post_refs:
        return list(post_refs.values())

    # Fallback: filter streamItems for postPreview type.
    stream_items = payload.get("streamItems", [])
    posts = []
    for item in stream_items:
        if item.get("itemType") == "postPreview":
            preview = item.get("postPreview", item)
            posts.append(preview)
    return posts


async def get_post_stats(post_id: str) -> dict[str, Any]:
    """
    Fetch view, read, and clap stats for a specific Medium post.

    Requires MEDIUM_SESSION_COOKIE and that the authenticated user
    is the author of the post (stats are only visible to the author).

    post_id: the internal Medium post ID (e.g. 'a1b2c3d4e5f6').
             Found in the post URL or returned by medium_list_posts.

    Auth: MEDIUM_SESSION_COOKIE
    """
    async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
        r = await client.get(
            f"{MEDIUM_WEB_BASE}/p/{post_id}/stats",
            headers=_session_headers(),
            params={"format": "json"},
            follow_redirects=True,
        )
        r.raise_for_status()
        data = _parse_medium_json(r.text)

    payload = data.get("payload", data)
    if isinstance(payload, dict):
        return payload.get("value", payload)
    return data
