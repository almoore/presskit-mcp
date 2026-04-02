#!/usr/bin/env python3
"""
presskit — CLI for publishing markdown to Medium and Substack.

Usage:
    presskit publish medium  --file article.md [--status draft] [--tags tag1,tag2]
    presskit publish substack --file article.md [--status draft] [--subtitle "..."]
    presskit publish both    --file article.md [--status draft]
    presskit list medium     [--username your_username] [--limit 10]
    presskit list substack   [--subdomain your_sub] [--limit 10]
    presskit drafts substack

Draft IDs are stored in frontmatter (medium_draft_id, substack_draft_id).
Re-running publish on the same file updates the existing draft instead of
creating a duplicate.
"""

import argparse
import asyncio
import json
import os
import re
import sys


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """
    Extract YAML-like frontmatter from markdown.
    Returns (metadata_dict, body_without_frontmatter).
    """
    if not text.startswith("---"):
        return {}, text

    end = text.find("\n---", 3)
    if end == -1:
        return {}, text

    frontmatter = text[3:end].strip()
    body = text[end + 4:].strip()

    meta = {}
    for line in frontmatter.split("\n"):
        line = line.strip()
        if ":" in line:
            key, _, value = line.partition(":")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            # Handle YAML arrays like [tag1, tag2]
            if value.startswith("[") and value.endswith("]"):
                value = [v.strip().strip('"').strip("'") for v in value[1:-1].split(",")]
            meta[key] = value

    return meta, body


def _extract_title(meta: dict, body: str) -> tuple[str, str]:
    """Get title from frontmatter or first # heading. Returns (title, body_without_title)."""
    title = meta.get("title", "")
    if title:
        # Strip title heading from body if it matches
        lines = body.split("\n")
        if lines and lines[0].startswith("# "):
            body = "\n".join(lines[1:]).strip()
        return title, body

    # Extract from first heading
    lines = body.split("\n")
    for i, line in enumerate(lines):
        if line.startswith("# "):
            title = line[2:].strip()
            body = "\n".join(lines[:i] + lines[i + 1:]).strip()
            return title, body

    return "Untitled", body


# ── Frontmatter ID management ────────────────────────────────────────────────


def _write_frontmatter_field(file_path: str, key: str, value: str):
    """Add or update a field in a markdown file's YAML frontmatter."""
    with open(file_path) as f:
        content = f.read()

    if not content.startswith("---"):
        return

    end = content.find("\n---", 3)
    if end == -1:
        return

    frontmatter = content[3:end]
    body = content[end:]

    # Check if key already exists
    lines = frontmatter.split("\n")
    found = False
    for i, line in enumerate(lines):
        if line.strip().startswith(f"{key}:"):
            lines[i] = f"{key}: {value}"
            found = True
            break

    if not found:
        lines.append(f"{key}: {value}")

    new_frontmatter = "\n".join(lines)
    with open(file_path, "w") as f:
        f.write(f"---{new_frontmatter}{body}")


# ── Medium update ─────────────────────────────────────────────────────────────


async def _update_medium_draft(post_id: str, title: str, body: str, tags: list | None, base_path: str | None):
    """Update an existing Medium draft by clearing and rewriting all content via delta OT."""
    import time
    import random
    from medium.client import (
        _graphql_headers, _parse_medium_json, _markdown_to_paragraphs,
        _random_hex, MEDIUM_WEB_BASE,
    )
    import httpx

    headers = _graphql_headers()
    cookie_str = headers["Cookie"]
    xsrf = headers.get("x-xsrf-token", "")

    def _make_delta_headers():
        return {
            "Cookie": cookie_str,
            "X-XSRF-Token": xsrf,
            "X-Client-Date": str(int(time.time() * 1000)),
            "X-Obvious-CID": "web",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": headers["User-Agent"],
            "Referer": f"{MEDIUM_WEB_BASE}/p/{post_id}/edit",
        }

    async def _fetch_state(client):
        """Fetch current delta state and return (base_rev, para_count)."""
        r = await client.get(
            f"{MEDIUM_WEB_BASE}/p/{post_id}/deltas?baseRev=-1",
            headers=_make_delta_headers(),
            follow_redirects=True,
        )
        r.raise_for_status()
        data = _parse_medium_json(r.text)
        entries = data.get("payload", {}).get("postDeltas", [])
        base_rev = max((e.get("rev", 0) for e in entries), default=0)

        para_count = 0
        for entry in entries:
            delta = entry.get("delta", {})
            if delta.get("type") == 1:
                para_count += 1
            elif delta.get("type") == 2:
                para_count -= 1
        return base_rev, para_count

    async def _post_deltas(client, deltas, base_rev):
        """Post a batch of deltas and return updated base_rev."""
        lock_id = str(random.randint(1000, 9999))
        payload = {"id": post_id, "deltas": deltas, "baseRev": base_rev}
        r = await client.post(
            f"{MEDIUM_WEB_BASE}/p/{post_id}/deltas?logLockId={lock_id}",
            headers=_make_delta_headers(),
            json=payload,
            follow_redirects=True,
        )
        if r.status_code >= 400:
            print(f"  Delta POST failed ({r.status_code}): {r.text[:500]}", file=sys.stderr)
            r.raise_for_status()
        resp = _parse_medium_json(r.text)
        return resp.get("payload", {}).get("value", {})

    async with httpx.AsyncClient(timeout=60.0) as client:
        # Step 1: Get current state — replay deltas to get paragraph names
        base_rev, para_count = await _fetch_state(client)
        print(f"  Current state: rev={base_rev}, paragraphs={para_count}")

        # Step 2: Get existing paragraph names by replaying deltas
        r = await client.get(
            f"{MEDIUM_WEB_BASE}/p/{post_id}/deltas?baseRev=-1",
            headers=_make_delta_headers(),
            follow_redirects=True,
        )
        r.raise_for_status()
        data = _parse_medium_json(r.text)
        entries = data.get("payload", {}).get("postDeltas", [])

        # Replay to get ordered list of existing paragraph names
        existing_names = []
        for entry in entries:
            delta = entry.get("delta", {})
            if delta.get("type") == 1:
                para = delta.get("paragraph", {})
                idx = delta.get("index", len(existing_names))
                existing_names.insert(idx, para.get("name", ""))
            elif delta.get("type") == 2:
                idx = delta.get("index", 0)
                if idx < len(existing_names):
                    existing_names.pop(idx)

        # Step 3: Build new content
        paragraphs = await _markdown_to_paragraphs(title, body, base_path, post_id)
        new_count = len(paragraphs)
        old_count = len(existing_names)
        print(f"  Rewriting: {old_count} existing → {new_count} new paragraphs")

        deltas = []

        # Update existing paragraphs in-place (reuse names)
        overlap = min(old_count, new_count)
        for i in range(overlap):
            para = paragraphs[i]
            is_image = para["type"] == 4
            update_para = {
                "name": existing_names[i],  # reuse existing name
                "type": para["type"],
                "text": para.get("text", ""),
                "markups": para.get("markups", []),
            }
            if is_image:
                update_para["layout"] = 1
                update_para["metadata"] = para.get("metadata", {})

            deltas.append({
                "type": 3, "index": i,
                "paragraph": update_para,
            })

        # If new content is longer: insert additional paragraphs
        for i in range(overlap, new_count):
            para = paragraphs[i]
            is_image = para["type"] == 4
            insert_para = {
                "name": para["name"],
                "type": para["type"],
                "text": "",
                "markups": [],
            }
            if is_image:
                insert_para["layout"] = 1
                insert_para["metadata"] = {}

            deltas.append({
                "type": 1, "index": i,
                "paragraph": insert_para,
                "isStartOfSection": False,
            })

            update_para = {
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
                    "type": 3, "index": i,
                    "paragraph": update_para,
                    "verifySameName": True,
                })

        # If new content is shorter: delete excess paragraphs (reverse order)
        for i in range(old_count - 1, new_count - 1, -1):
            deltas.append({"type": 2, "index": i})

        save_result = await _post_deltas(client, deltas, base_rev)
        base_rev = save_result.get("latestRev", base_rev + len(deltas))

        # Step 4: Update tags
        if tags:
            await client.post(
                f"{MEDIUM_WEB_BASE}/_/graphql",
                headers=headers,
                json={
                    "query": (
                        "mutation SetPostTags($targetPostId: ID!, $tagNames: [String!]!) { "
                        "setPostTags(targetPostId: $targetPostId, tagNames: $tagNames) { id title } }"
                    ),
                    "variables": {"targetPostId": post_id, "tagNames": tags[:5]},
                },
                follow_redirects=True,
            )

    return {
        "id": post_id,
        "title": save_result.get("title", title),
        "latestRev": save_result.get("latestRev"),
        "paragraphCount": len(paragraphs),
        "editUrl": f"{MEDIUM_WEB_BASE}/p/{post_id}/edit",
        "updated": True,
    }


# ── Commands ──────────────────────────────────────────────────────────────────


async def cmd_publish_medium(args):
    from medium.client import create_post_via_session

    with open(args.file) as f:
        raw = f.read()

    meta, body = _parse_frontmatter(raw)
    title, body = _extract_title(meta, body)
    tags = args.tags.split(",") if args.tags else meta.get("tags", None)
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",")]
    status = args.status or "draft"
    base_path = os.path.dirname(os.path.abspath(args.file))

    # Check for existing draft ID in frontmatter
    existing_id = meta.get("medium_draft_id", "").strip()

    if existing_id and not args.force:
        # Update existing draft
        print(f"Updating Medium draft {existing_id}: \"{title}\"")
        try:
            result = await _update_medium_draft(existing_id, title, body, tags, base_path)
            print(json.dumps(result, indent=2))
            print(f"\nUpdated: {result.get('editUrl', 'N/A')}")
            return
        except Exception as e:
            print(f"\nUpdate failed: {e}")
            print("Draft may be corrupted. Creating new draft instead...")
            # Fall through to create new draft

    # Create new draft
    print(f"Publishing to Medium: \"{title}\" ({status})")
    result = await create_post_via_session(
        title=title,
        content=body,
        publish_status=status,
        tags=tags,
        base_path=base_path,
    )
    print(json.dumps(result, indent=2))
    print(f"\nEdit: {result.get('editUrl', 'N/A')}")
    if result.get("mediumUrl"):
        print(f"URL:  {result['mediumUrl']}")

    # Write draft ID back to frontmatter
    post_id = result.get("id", "")
    if post_id:
        _write_frontmatter_field(args.file, "medium_draft_id", post_id)
        _write_frontmatter_field(args.file, "medium_edit_url", result.get("editUrl", ""))
        print(f"Saved medium_draft_id={post_id} to frontmatter")


async def cmd_publish_substack(args):
    from substack.client import create_draft, publish_post, update_draft

    with open(args.file) as f:
        raw = f.read()

    meta, body = _parse_frontmatter(raw)
    title, body = _extract_title(meta, body)
    subtitle = args.subtitle or meta.get("subtitle", "")
    status = args.status or "draft"
    base_path = os.path.dirname(os.path.abspath(args.file))

    # Check for existing draft ID in frontmatter
    existing_id = meta.get("substack_draft_id", "").strip()

    if existing_id and not args.force:
        # Update existing draft
        print(f"Updating Substack draft {existing_id}: \"{title}\"")
        result = await update_draft(
            draft_id=int(existing_id),
            title=title,
            body_markdown=body,
            subtitle=subtitle,
            base_path=base_path,
        )
        print(f"Updated: {json.dumps(result, indent=2, default=str)}")
        return

    # Create new draft
    print(f"Publishing to Substack: \"{title}\" ({status})")
    result = await create_draft(
        title=title,
        body_markdown=body,
        subtitle=subtitle,
        base_path=base_path,
    )
    draft_id = result.get("id")
    print(f"Draft created: {json.dumps(result, indent=2, default=str)}")

    # Write draft ID back to frontmatter
    if draft_id:
        _write_frontmatter_field(args.file, "substack_draft_id", str(draft_id))
        print(f"Saved substack_draft_id={draft_id} to frontmatter")

    if status == "public" and draft_id:
        send_email = not args.no_email
        print(f"Publishing draft {draft_id} (send_email={send_email})...")
        pub_result = await publish_post(draft_id, send_email=send_email)
        print(f"Published: {json.dumps(pub_result, indent=2, default=str)}")


async def cmd_publish_both(args):
    print("=== Medium ===")
    await cmd_publish_medium(args)
    print("\n=== Substack ===")
    await cmd_publish_substack(args)


async def cmd_list_medium(args):
    from medium.client import list_posts

    username = args.username
    if not username:
        print("Error: --username required for medium list", file=sys.stderr)
        sys.exit(1)

    posts = await list_posts(username, limit=args.limit)
    for p in posts:
        title = p.get("title", "Untitled")
        post_id = p.get("id", p.get("uniqueSlug", ""))
        print(f"  {post_id}  {title}")
    print(f"\n{len(posts)} posts")


async def cmd_list_substack(args):
    from substack.client import list_posts

    subdomain = args.subdomain
    if not subdomain:
        url = os.environ.get("SUBSTACK_PUBLICATION_URL", "")
        if url:
            subdomain = url.split("//")[-1].split(".")[0]
        else:
            print("Error: --subdomain required or set SUBSTACK_PUBLICATION_URL", file=sys.stderr)
            sys.exit(1)

    posts = await list_posts(subdomain, limit=args.limit)
    for p in posts:
        title = p.get("title", "Untitled")
        slug = p.get("slug", "")
        print(f"  {slug}  {title}")
    print(f"\n{len(posts)} posts")


async def cmd_drafts_substack(args):
    from substack.client import get_drafts

    drafts = await get_drafts()
    if not drafts:
        print("No drafts found.")
        return
    for d in drafts:
        title = d.get("title", d.get("draft_title", "Untitled"))
        draft_id = d.get("id", "")
        print(f"  {draft_id}  {title}")
    print(f"\n{len(drafts)} drafts")


# ── Argument parsing ──────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="presskit",
        description="Publish markdown to Medium and Substack.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # ── publish ───────────────────────────────────────────────────────────
    pub = sub.add_parser("publish", help="Publish a markdown file")
    pub_sub = pub.add_subparsers(dest="platform", required=True)

    for name in ("medium", "substack", "both"):
        p = pub_sub.add_parser(name, help=f"Publish to {name}")
        p.add_argument("--file", "-f", required=True, help="Markdown file to publish")
        p.add_argument("--status", "-s", default="draft", choices=["draft", "public", "unlisted"],
                        help="Publish status (default: draft)")
        p.add_argument("--tags", "-t", default=None, help="Comma-separated tags (Medium)")
        p.add_argument("--subtitle", default=None, help="Post subtitle (Substack)")
        p.add_argument("--no-email", action="store_true",
                        help="Publish to web only, no subscriber email (Substack)")
        p.add_argument("--force", action="store_true",
                        help="Create new draft even if one exists (ignore saved draft ID)")

    # ── list ──────────────────────────────────────────────────────────────
    lst = sub.add_parser("list", help="List published posts")
    lst_sub = lst.add_subparsers(dest="platform", required=True)

    med_list = lst_sub.add_parser("medium", help="List Medium posts")
    med_list.add_argument("--username", "-u", default=None, help="Medium username (without @)")
    med_list.add_argument("--limit", "-n", type=int, default=10)

    sub_list = lst_sub.add_parser("substack", help="List Substack posts")
    sub_list.add_argument("--subdomain", "-d", default=None, help="Substack subdomain")
    sub_list.add_argument("--limit", "-n", type=int, default=10)

    # ── drafts ────────────────────────────────────────────────────────────
    drafts = sub.add_parser("drafts", help="List drafts")
    drafts_sub = drafts.add_subparsers(dest="platform", required=True)
    drafts_sub.add_parser("substack", help="List Substack drafts")

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    dispatch = {
        ("publish", "medium"): cmd_publish_medium,
        ("publish", "substack"): cmd_publish_substack,
        ("publish", "both"): cmd_publish_both,
        ("list", "medium"): cmd_list_medium,
        ("list", "substack"): cmd_list_substack,
        ("drafts", "substack"): cmd_drafts_substack,
    }

    handler = dispatch.get((args.command, args.platform))
    if not handler:
        parser.print_help()
        sys.exit(1)

    asyncio.run(handler(args))


if __name__ == "__main__":
    main()
