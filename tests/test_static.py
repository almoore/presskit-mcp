"""
Static / unit tests runnable with zero external dependencies.

Uses sys.modules patching to stub out httpx, mcp, and pydantic so the
source modules can be imported and their pure-Python logic exercised.

Run with:
    python3 -m unittest discover -s tests -v
"""

import ast
import importlib
import json
import os
import sys
import types
import unittest
from unittest.mock import MagicMock, patch


# ── Stub missing third-party packages before any source import ─────────────────

def _stub_modules():
    """
    Insert lightweight stubs into sys.modules so imports like
    `from mcp.server.fastmcp import FastMCP` don't crash.
    """
    stubs = {
        "httpx": MagicMock(),
        "httpx.HTTPStatusError": type("HTTPStatusError", (Exception,), {}),
        "mcp": MagicMock(),
        "mcp.server": MagicMock(),
        "mcp.server.fastmcp": MagicMock(),
        "pydantic": MagicMock(),
        # python-substack lives as bare 'substack' — stub it out
        # (our own substack/ package will shadow this on sys.path,
        # but _import_substack() does a lazy import; we test that failure path)
        "substack.post": MagicMock(),
    }

    # Make httpx.HTTPStatusError importable as a real exception class
    httpx_stub = types.ModuleType("httpx")
    httpx_stub.HTTPStatusError = type("HTTPStatusError", (Exception,), {
        "response": MagicMock(status_code=500, text="server error"),
    })
    httpx_stub.TimeoutException = type("TimeoutException", (Exception,), {})
    httpx_stub.AsyncClient = MagicMock()
    sys.modules["httpx"] = httpx_stub

    # Pydantic stubs: BaseModel, Field, ConfigDict
    pydantic_stub = types.ModuleType("pydantic")
    pydantic_stub.BaseModel = object
    pydantic_stub.Field = lambda *a, **kw: None
    pydantic_stub.ConfigDict = lambda **kw: {}
    sys.modules["pydantic"] = pydantic_stub

    # MCP stubs
    mcp_stub = types.ModuleType("mcp")
    mcp_server = types.ModuleType("mcp.server")
    mcp_fastmcp = types.ModuleType("mcp.server.fastmcp")
    mcp_fastmcp.FastMCP = MagicMock
    mcp_stub.server = mcp_server
    mcp_server.fastmcp = mcp_fastmcp
    sys.modules["mcp"] = mcp_stub
    sys.modules["mcp.server"] = mcp_server
    sys.modules["mcp.server.fastmcp"] = mcp_fastmcp


_stub_modules()

# Add repo root to path so `medium` and `substack` packages resolve
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import medium.client as med_client          # noqa: E402
import medium.tools as med_tools            # noqa: E402
import substack.client as ss_client         # noqa: E402


# ── Syntax tests ───────────────────────────────────────────────────────────────

class TestSyntax(unittest.TestCase):
    """All source files parse without SyntaxError."""

    FILES = [
        "server.py",
        "medium/__init__.py",
        "medium/client.py",
        "medium/tools.py",
        "substack/__init__.py",
        "substack/client.py",
        "substack/tools.py",
    ]

    def test_all_files_parse(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        for rel in self.FILES:
            path = os.path.join(root, rel)
            with self.subTest(file=rel):
                with open(path) as f:
                    src = f.read()
                try:
                    ast.parse(src)
                except SyntaxError as e:
                    self.fail(f"SyntaxError in {rel}: {e}")


# ── medium/client.py — pure function tests ────────────────────────────────────

class TestStripXssi(unittest.TestCase):

    def test_strips_standard_prefix(self):
        raw = "])}while(1);</x>" + '{"ok": true}'
        self.assertEqual(med_client._strip_xssi(raw), '{"ok": true}')

    def test_strips_alternate_prefix(self):
        raw = "])}while(1);<x>" + '{"ok": true}'
        self.assertEqual(med_client._strip_xssi(raw), '{"ok": true}')

    def test_strips_short_prefix(self):
        raw = "])}while(1);" + '{"ok": true}'
        self.assertEqual(med_client._strip_xssi(raw), '{"ok": true}')

    def test_passthrough_clean_json(self):
        raw = '{"key": "value"}'
        self.assertEqual(med_client._strip_xssi(raw), raw)

    def test_empty_string(self):
        self.assertEqual(med_client._strip_xssi(""), "")


class TestParseMediumJson(unittest.TestCase):

    def test_parses_clean_json(self):
        result = med_client._parse_medium_json('{"a": 1}')
        self.assertEqual(result, {"a": 1})

    def test_parses_xssi_wrapped_json(self):
        result = med_client._parse_medium_json('])}while(1);</x>{"b": 2}')
        self.assertEqual(result, {"b": 2})

    def test_raises_on_invalid_json(self):
        with self.assertRaises(json.JSONDecodeError):
            med_client._parse_medium_json("not json")


class TestMediumEnvHelpers(unittest.TestCase):

    def test_integration_token_raises_when_missing(self):
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("MEDIUM_INTEGRATION_TOKEN", None)
            with self.assertRaises(ValueError) as ctx:
                med_client._integration_token()
            self.assertIn("MEDIUM_INTEGRATION_TOKEN", str(ctx.exception))

    def test_integration_token_returns_value(self):
        with patch.dict(os.environ, {"MEDIUM_INTEGRATION_TOKEN": "tok123"}):
            self.assertEqual(med_client._integration_token(), "tok123")

    def test_session_cookie_raises_when_missing(self):
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("MEDIUM_SESSION_COOKIE", None)
            with self.assertRaises(ValueError) as ctx:
                med_client._session_cookie()
            self.assertIn("MEDIUM_SESSION_COOKIE", str(ctx.exception))

    def test_session_cookie_returns_value(self):
        with patch.dict(os.environ, {"MEDIUM_SESSION_COOKIE": "sid_abc"}):
            self.assertEqual(med_client._session_cookie(), "sid_abc")

    def test_rest_headers_contain_bearer(self):
        with patch.dict(os.environ, {"MEDIUM_INTEGRATION_TOKEN": "mytoken"}):
            headers = med_client._rest_headers()
            self.assertIn("Authorization", headers)
            self.assertEqual(headers["Authorization"], "Bearer mytoken")

    def test_session_headers_contain_sid_cookie(self):
        with patch.dict(os.environ, {"MEDIUM_SESSION_COOKIE": "mysid"}):
            headers = med_client._session_headers()
            self.assertIn("Cookie", headers)
            self.assertIn("sid=mysid", headers["Cookie"])


# ── medium/tools.py — error handler tests ─────────────────────────────────────

class TestMediumErrorHandler(unittest.TestCase):

    def _http_error(self, status_code, text="error body"):
        import httpx
        err = httpx.HTTPStatusError(f"HTTP {status_code}")
        err.response = MagicMock(status_code=status_code, text=text)
        return err

    def test_401_gives_auth_message(self):
        result = med_tools._handle_error(self._http_error(401), "test_ctx")
        self.assertIn("Authentication", result)

    def test_403_gives_permission_message(self):
        result = med_tools._handle_error(self._http_error(403), "test_ctx")
        self.assertIn("Permission", result)

    def test_404_gives_not_found_message(self):
        result = med_tools._handle_error(self._http_error(404), "test_ctx")
        self.assertIn("Not found", result)

    def test_429_gives_rate_limit_message(self):
        result = med_tools._handle_error(self._http_error(429), "test_ctx")
        self.assertIn("Rate limited", result)

    def test_value_error_surfaced(self):
        result = med_tools._handle_error(ValueError("bad input"), "test_ctx")
        self.assertIn("bad input", result)

    def test_timeout_message(self):
        import httpx
        result = med_tools._handle_error(httpx.TimeoutException("timed out"), "test_ctx")
        self.assertIn("timed out", result.lower())

    def test_context_included_in_output(self):
        result = med_tools._handle_error(ValueError("x"), "my_tool_name")
        self.assertIn("my_tool_name", result)


# ── substack/client.py — env helpers ──────────────────────────────────────────

class TestSubstackEnvHelpers(unittest.TestCase):

    def test_publication_url_raises_when_missing(self):
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("SUBSTACK_PUBLICATION_URL", None)
            with self.assertRaises(ValueError) as ctx:
                ss_client._publication_url()
            self.assertIn("SUBSTACK_PUBLICATION_URL", str(ctx.exception))

    def test_publication_url_returns_value(self):
        with patch.dict(os.environ, {"SUBSTACK_PUBLICATION_URL": "https://test.substack.com"}):
            self.assertEqual(ss_client._publication_url(), "https://test.substack.com")

    def test_make_api_raises_without_auth(self):
        env = {k: "" for k in ["SUBSTACK_EMAIL", "SUBSTACK_PASSWORD", "SUBSTACK_COOKIES_STRING"]}
        with patch.dict(os.environ, env):
            with self.assertRaises((ValueError, ImportError)):
                ss_client._make_api()


# ── Import graph test ─────────────────────────────────────────────────────────

class TestImportGraph(unittest.TestCase):
    """Verify no unexpected cross-package imports (e.g. medium importing substack)."""

    def _get_imports(self, filepath):
        with open(filepath) as f:
            tree = ast.parse(f.read())
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                imports.append(node.module)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append(alias.name)
        return imports

    def test_medium_does_not_import_substack(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        for fname in ["medium/client.py", "medium/tools.py"]:
            imports = self._get_imports(os.path.join(root, fname))
            for imp in imports:
                with self.subTest(file=fname, imp=imp):
                    self.assertFalse(
                        imp.startswith("substack"),
                        f"{fname} imports from substack: {imp}"
                    )

    def test_substack_does_not_import_medium(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        for fname in ["substack/client.py", "substack/tools.py"]:
            imports = self._get_imports(os.path.join(root, fname))
            for imp in imports:
                with self.subTest(file=fname, imp=imp):
                    self.assertFalse(
                        imp.startswith("medium"),
                        f"{fname} imports from medium: {imp}"
                    )


if __name__ == "__main__":
    unittest.main(verbosity=2)
