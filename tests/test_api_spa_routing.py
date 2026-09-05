"""Routing tests for SPA containment, mindmap 410 tombstone, and docs gating.

Security contract:
- spa_fallback must never serve a file that resolves outside the frontend
  build directory (path traversal via encoded ``..%2f`` segments).
- /mindmap/{uid} must return a static 410 tombstone BEFORE the SPA catch-all
  can convert it into an HTML response.
- /docs, /redoc, /openapi.json must be disabled unless ENVIRONMENT=development.
"""

import base64
import hashlib
import importlib
import json
import os

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from tests.conftest import setup_auth_override


def _csp_hash(script_body: str) -> str:
    digest = hashlib.sha256(script_body.encode("utf-8")).digest()
    return "sha256-" + base64.b64encode(digest).decode("ascii")


def _script_src_of(csp: str) -> str:
    return next(d.strip() for d in csp.split(";") if d.strip().startswith("script-src"))


@pytest.fixture
def fresh_csp_cache(monkeypatch):
    monkeypatch.setattr("api._csp_cache", None)


@pytest.fixture
def client():
    from api import app

    setup_auth_override(app)
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def fake_build(monkeypatch, tmp_path):
    """Point the SPA fallback at a temp build dir with a marker index + asset."""
    (tmp_path / "index.html").write_text("<html>SPA_INDEX_MARKER</html>", encoding="utf-8")
    (tmp_path / "asset.txt").write_text("SPA_ASSET_MARKER", encoding="utf-8")
    monkeypatch.setattr("api._BUILD_DIR", tmp_path)
    monkeypatch.setattr("api._SPA_INDEX", tmp_path / "index.html")
    return tmp_path


@pytest.fixture
def no_build(monkeypatch, tmp_path):
    """Point the SPA fallback at an empty dir (CI-like: no frontend build)."""
    empty = tmp_path / "no-build"
    empty.mkdir()
    monkeypatch.setattr("api._BUILD_DIR", empty)
    monkeypatch.setattr("api._SPA_INDEX", empty / "index.html")
    return empty


class TestSpaContainment:
    def test_index_served_for_unknown_client_route(self, client, fake_build):
        resp = client.get("/some/client/route")
        assert resp.status_code == 200
        assert "SPA_INDEX_MARKER" in resp.text

    def test_real_asset_served(self, client, fake_build):
        resp = client.get("/asset.txt")
        assert resp.status_code == 200
        assert resp.text == "SPA_ASSET_MARKER"

    def test_encoded_traversal_to_api_py_404(self, client):
        resp = client.get("/..%2fapi.py")
        assert resp.status_code == 404
        assert "import uvicorn" not in resp.text

    def test_encoded_double_traversal_to_existing_file_404(self, client):
        resp = client.get("/..%2f..%2fapi.py")
        assert resp.status_code == 404
        assert "import uvicorn" not in resp.text

    def test_encoded_traversal_to_etc_passwd_404(self, client):
        resp = client.get("/..%2f..%2f..%2fetc%2fpasswd")
        assert resp.status_code == 404
        assert "root:" not in resp.text

    def test_fully_encoded_dots_traversal_404(self, client):
        resp = client.get("/%2e%2e%2fapi.py")
        assert resp.status_code == 404
        assert "import uvicorn" not in resp.text

    def test_traversal_inside_build_still_404_without_build(self, client, no_build):
        resp = client.get("/..%2fapi.py")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_absolute_path_direct_handler_404(self):
        from api import spa_fallback

        with pytest.raises(HTTPException) as exc:
            await spa_fallback("/etc/passwd")
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_dotdot_direct_handler_404(self):
        from api import spa_fallback

        with pytest.raises(HTTPException) as exc:
            await spa_fallback("../../etc/passwd")
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_in_build_path_direct_handler_serves_index(self, fake_build):
        from api import spa_fallback

        resp = await spa_fallback("anything/here")
        assert resp.status_code == 200
        assert "SPA_INDEX_MARKER" in resp.body.decode()


class TestMindmapTombstone:
    def test_mindmap_returns_410(self, client):
        resp = client.get("/mindmap/autoflow")
        assert resp.status_code == 410
        assert resp.json() == {"detail": "Mindmap HTML endpoints are retired"}

    def test_mindmap_uid_variant_410(self, client):
        resp = client.get("/mindmap/some-old-uid-123")
        assert resp.status_code == 410

    def test_mindmap_not_swallowed_by_spa_catchall(self, client, fake_build):
        """Route-order proof: the tombstone wins even when the SPA index exists."""
        resp = client.get("/mindmap/autoflow")
        assert resp.status_code == 410
        assert "SPA_INDEX_MARKER" not in resp.text
        assert resp.headers["content-type"].startswith("application/json")

        control = client.get("/some-other-page")
        assert control.status_code == 200
        assert "SPA_INDEX_MARKER" in control.text

    def test_mindmap_static_mount_removed(self, client, fake_build):
        """The mount is gone; the path falls through to the SPA shell, never JS."""
        resp = client.get("/mindmap-static/js/d3.min.js")
        assert resp.status_code == 200
        assert "SPA_INDEX_MARKER" in resp.text
        assert "javascript" not in resp.headers.get("content-type", "")
        assert not any(
            getattr(route, "path", "") == "/mindmap-static" for route in client.app.routes
        )


class TestSecurityHeaders:
    """CSP + nosniff hardening must be present on SPA and API responses."""

    def test_spa_index_has_security_headers(self, client, fake_build):
        resp = client.get("/")
        assert resp.status_code == 200
        csp = resp.headers["content-security-policy"]
        assert "script-src 'self'" in csp
        assert "object-src 'none'" in csp
        assert "frame-ancestors 'none'" in csp
        assert "base-uri 'self'" in csp
        assert "connect-src 'self' ws: wss:" in csp
        assert resp.headers["x-content-type-options"] == "nosniff"

    def test_client_route_fallback_has_security_headers(self, client, fake_build):
        resp = client.get("/analysis")
        assert resp.status_code == 200
        assert "script-src 'self'" in resp.headers["content-security-policy"]
        assert resp.headers["x-content-type-options"] == "nosniff"

    def test_api_json_response_has_security_headers(self, client):
        resp = client.get("/mindmap/anything")
        assert resp.status_code == 410
        assert "script-src 'self'" in resp.headers["content-security-policy"]
        assert resp.headers["x-content-type-options"] == "nosniff"

    def test_csp_forbids_inline_scripts(self, client, fake_build, fresh_csp_cache):
        csp = client.get("/").headers["content-security-policy"]
        script_src = next(
            directive for directive in csp.split(";") if directive.strip().startswith("script-src")
        )
        assert "unsafe-inline" not in script_src
        assert "unsafe-eval" not in script_src

    def test_csp_includes_hash_of_inline_bootstrap_script(self, client, fake_build, fresh_csp_cache):
        body = "const bootstrapMarker = 1;"
        (fake_build / "index.html").write_text(
            f"<html><script>{body}</script></html>", encoding="utf-8"
        )
        (fake_build / "csp-hashes.json").write_text(json.dumps([_csp_hash(body)]), encoding="utf-8")

        csp = client.get("/").headers["content-security-policy"]

        assert f"'{_csp_hash(body)}'" in _script_src_of(csp)
        assert "unsafe-inline" not in _script_src_of(csp)

    def test_csp_reflects_regenerated_hashes_via_mtime_cache(
        self, client, fake_build, fresh_csp_cache
    ):
        hashes_file = fake_build / "csp-hashes.json"
        first, second = _csp_hash("window.__sveltekit_first = 1;"), _csp_hash(
            "window.__sveltekit_second = 2;"
        )

        hashes_file.write_text(json.dumps([first]), encoding="utf-8")
        os.utime(hashes_file, ns=(10**9, 10**9))
        assert f"'{first}'" in _script_src_of(client.get("/").headers["content-security-policy"])

        hashes_file.write_text(json.dumps([second]), encoding="utf-8")
        os.utime(hashes_file, ns=(2 * 10**9, 2 * 10**9))
        script_src = _script_src_of(client.get("/").headers["content-security-policy"])
        assert f"'{second}'" in script_src
        assert f"'{first}'" not in script_src

    def test_csp_dev_fallback_unsafe_inline_without_hashes(
        self, client, no_build, fresh_csp_cache, monkeypatch
    ):
        monkeypatch.setenv("ENVIRONMENT", "development")
        script_src = _script_src_of(client.get("/").headers["content-security-policy"])
        assert "'self' 'unsafe-inline'" in script_src

    def test_csp_malformed_hashes_fail_closed(self, client, fake_build, fresh_csp_cache):
        payload = ["sha256-shortcut; script-src 'unsafe-inline'", 123, None]
        (fake_build / "csp-hashes.json").write_text(json.dumps(payload), encoding="utf-8")

        script_src = _script_src_of(client.get("/").headers["content-security-policy"])

        assert script_src == "script-src 'self'"
        assert "unsafe-inline" not in script_src
        assert "shortcut" not in script_src


class TestDocsGating:
    def test_docs_disabled_by_default(self, client, no_build):
        for path in ("/docs", "/redoc", "/openapi.json"):
            resp = client.get(path)
            assert resp.status_code == 404, f"{path} should be disabled in production"

    def test_docs_enabled_in_development(self, monkeypatch, no_build):
        import api as api_module

        saved_env = os.environ.get("ENVIRONMENT")
        monkeypatch.setenv("ENVIRONMENT", "development")
        try:
            reloaded = importlib.reload(api_module)
            client = TestClient(reloaded.app)
            assert client.get("/docs").status_code == 200
            assert client.get("/redoc").status_code == 200
            schema = client.get("/openapi.json")
            assert schema.status_code == 200
            assert "openapi" in schema.json()
        finally:
            if saved_env is None:
                os.environ.pop("ENVIRONMENT", None)
            else:
                os.environ["ENVIRONMENT"] = saved_env
            importlib.reload(api_module)

    def test_is_development_mode_fail_safe(self, monkeypatch):
        import api as api_module

        for value in (None, "production", "PRODUCTION", "prod ", "developmen", ""):
            if value is None:
                monkeypatch.delenv("ENVIRONMENT", raising=False)
            else:
                monkeypatch.setenv("ENVIRONMENT", value)
            assert api_module.is_development_mode() is False, (
                f"ENVIRONMENT={value!r} must not enable development mode"
            )
        monkeypatch.setenv("ENVIRONMENT", "development")
        assert api_module.is_development_mode() is True
        monkeypatch.setenv("ENVIRONMENT", " Development ")
        assert api_module.is_development_mode() is True
