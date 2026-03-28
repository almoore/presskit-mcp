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

Reads markdown files and publishes them via the same client functions
used by the MCP server — no MCP protocol involved.
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

    print(f"Publishing to Medium: \"{title}\" ({status})")
    result = await create_post_via_session(
        title=title,
        content=body,
        publish_status=status,
        tags=tags,
    )
    print(json.dumps(result, indent=2))
    print(f"\nEdit: {result.get('editUrl', 'N/A')}")
    if result.get("mediumUrl"):
        print(f"URL:  {result['mediumUrl']}")


async def cmd_publish_substack(args):
    from substack.client import create_draft, publish_post

    with open(args.file) as f:
        raw = f.read()

    meta, body = _parse_frontmatter(raw)
    title, body = _extract_title(meta, body)
    subtitle = args.subtitle or meta.get("subtitle", "")
    status = args.status or "draft"

    print(f"Publishing to Substack: \"{title}\" ({status})")
    result = await create_draft(
        title=title,
        body_markdown=body,
        subtitle=subtitle,
    )
    draft_id = result.get("id")
    print(f"Draft created: {json.dumps(result, indent=2, default=str)}")

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
