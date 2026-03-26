"""
publishing_mcp — FastMCP server for Medium and Substack publishing.

Exposes 14 tools total:
  - 5 Medium tools  (medium_*)
  - 9 Substack tools (substack_*)

Run directly:
    python server.py

Or via installed entry point (after `pip install -e .`):
    publishing-mcp

Add to Claude Desktop (claude_desktop_config.json):
    {
      "mcpServers": {
        "publishing": {
          "command": "python",
          "args": ["/absolute/path/to/publishing_mcp/server.py"],
          "env": {
            "MEDIUM_INTEGRATION_TOKEN": "...",
            "MEDIUM_SESSION_COOKIE":    "...",
            "SUBSTACK_EMAIL":           "...",
            "SUBSTACK_PASSWORD":        "...",
            "SUBSTACK_PUBLICATION_URL": "https://yourpub.substack.com"
          }
        }
      }
    }

Environment variables (set the ones you need — not all are required):
    MEDIUM_INTEGRATION_TOKEN   REST API token for Medium (existing holders only)
    MEDIUM_SESSION_COOKIE      'sid' cookie for Medium unofficial endpoints
    SUBSTACK_EMAIL             Substack login email (recommended auth method)
    SUBSTACK_PASSWORD          Substack login password
    SUBSTACK_PUBLICATION_URL   e.g. https://yourpub.substack.com
    SUBSTACK_COOKIES_STRING    Raw cookie string fallback (if no password)
"""

from mcp.server.fastmcp import FastMCP

from medium.tools import register_medium_tools
from substack.tools import register_substack_tools

mcp = FastMCP(
    name="publishing",
    instructions=(
        "MCP server for publishing content to Medium and Substack.\n\n"
        "MEDIUM tools:\n"
        "  - medium_get_current_user / medium_get_publications require MEDIUM_INTEGRATION_TOKEN.\n"
        "  - medium_create_post requires MEDIUM_INTEGRATION_TOKEN. Defaults to 'draft'.\n"
        "  - medium_list_posts / medium_get_post_stats require MEDIUM_SESSION_COOKIE.\n\n"
        "SUBSTACK tools:\n"
        "  - All write/auth tools require SUBSTACK_EMAIL + SUBSTACK_PASSWORD "
        "(or SUBSTACK_COOKIES_STRING) and SUBSTACK_PUBLICATION_URL.\n"
        "  - substack_list_posts, substack_get_post, substack_search_publications "
        "are public and require no auth.\n\n"
        "Typical cross-posting workflow:\n"
        "  1. medium_get_current_user → get your Medium user_id\n"
        "  2. medium_create_post(user_id=..., publish_status='draft') → create Medium draft\n"
        "  3. substack_create_draft(title=..., body_markdown=...) → create Substack draft\n"
        "  4. Review both drafts, then publish when ready\n"
    ),
)

register_medium_tools(mcp)
register_substack_tools(mcp)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
