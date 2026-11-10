"""Source-level tripwire tests for the mindmap export UI.

Regression background (2026-11-10): the "Скачать PNG" button silently did
nothing because markmap-view renders node labels as HTML inside
<foreignObject> — serializing the live SVG taints the rasterization canvas
in Chromium (toBlob → null). The fix routes every export through
buildPortableSvg(), which clones the SVG, drops foreignObject elements and
appends native <text> labels (PR #40).

These tests intentionally assert on the component SOURCE (string-level
invariants) because the real bug only reproduces in a browser with layout
(jsdom/happy-dom cannot provide Range rects or getScreenCTM). If one of
these fails, someone changed the export path — re-verify the export in a
real browser before "fixing" the test.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
COMPONENT = REPO_ROOT / "frontend" / "src" / "lib" / "components" / "MindmapViewer.svelte"


def component_source() -> str:
    assert COMPONENT.exists(), f"missing component source: {COMPONENT}"
    return COMPONENT.read_text(encoding="utf-8")


class TestExportButtonsPresent:
    """The three download buttons must stay in the UI."""

    def test_button_labels(self):
        src = component_source()
        for label in ("Скачать PNG", "Скачать SVG", "Скачать Markdown"):
            assert label in src, f"export button missing from UI: {label!r}"


class TestExportPathsUsePortableSvg:
    """Every raster/vector export must go through the portable clone.

    Serializing the live SVG (with foreignObject labels) reintroduces the
    tainted-canvas bug: PNG dies silently, SVG downloads an unportable file.
    """

    def test_build_portable_svg_exists(self):
        src = component_source()
        assert "function buildPortableSvg" in src, "buildPortableSvg() was removed"

    def test_png_and_svg_call_build_portable_svg(self):
        src = component_source()
        assert src.count("buildPortableSvg()") >= 3, (
            "exportPNG/exportSVG must call buildPortableSvg() "
            "(one in each export path plus the definition check)"
        )

    def test_live_svg_never_serialized_directly(self):
        """XMLSerializer may only run on the portable clone, never on the
        live svg element (foreignObject inside → tainted canvas / HTML-in-SVG)."""
        src = component_source()
        assert "serializeToString(clone)" in src, (
            "serializeToString must target the portable clone"
        )
        assert "serializeToString(svg" not in src, (
            "serializeToString() on the live svg reintroduces the tainted-canvas bug"
        )


class TestPortableSvgSanitization:
    """The portable clone must drop foreignObject and be self-describing."""

    def test_foreign_object_removed_from_clone(self):
        src = component_source()
        assert "clone.querySelectorAll('foreignObject')" in src, (
            "the clone must strip <foreignObject> elements"
        )

    def test_clone_has_explicit_dimensions(self):
        src = component_source()
        for attr in ("viewBox", "width", "height"):
            assert f"setAttribute('{attr}'" in src, (
                f"portable SVG must set explicit {attr} (unportable/unsized file otherwise)"
            )

    def test_download_filenames(self):
        src = component_source()
        for name in ("mindmap.png", "mindmap.svg", "mindmap.md"):
            assert name in src, f"download filename literal missing: {name!r}"


class TestPngRasterization:
    """PNG export must keep the blob-URL lifecycle that avoids leaks and
    races: revoke only after drawImage, and revoke on img.onerror too."""

    def test_revoke_after_draw_image(self):
        src = component_source()
        assert "URL.revokeObjectURL(url);" in src

    def test_blob_null_guard_uses_download_only(self):
        src = component_source()
        assert "if (blob) downloadBlob(blob, 'mindmap.png')" in src, (
            "PNG must only download a non-null blob (toBlob null = tainted canvas; "
            "fail loudly in the browser instead of silently corrupting downloads)"
        )
