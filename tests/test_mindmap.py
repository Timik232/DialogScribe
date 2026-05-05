"""Unit-тесты для mindmap.py — валидация, post-processing, рендеринг."""

import pytest

from gigaam_transcriber.mindmap import (
    validate_mindmap_markdown,
    postprocess_mindmap_markdown,
    render_mindmap_html,
    render_mindmap_fallback,
    _markdown_to_tree_html,
    _sanitize_markdown,
    MINDMAP_SYSTEM_PROMPT,
    get_stored_mindmap,
)


class TestValidateMindmapMarkdown:
    def test_valid_structure(self):
        md = "# Root\n## Branch 1\n### Leaf 1\n## Branch 2"
        assert validate_mindmap_markdown(md) is True

    def test_missing_h1(self):
        md = "## Branch 1\n### Leaf 1"
        assert validate_mindmap_markdown(md) is False

    def test_missing_h2(self):
        md = "# Root\nSome text without branches"
        assert validate_mindmap_markdown(md) is False

    def test_only_h1(self):
        md = "# Only root"
        assert validate_mindmap_markdown(md) is False

    def test_empty_string(self):
        assert validate_mindmap_markdown("") is False


class TestPostprocessMindmapMarkdown:
    def test_removes_code_blocks(self):
        md = "# Root\n## Branch\n```\ncode here\n```\n## Other"
        result = postprocess_mindmap_markdown(md)
        assert "```" not in result
        assert "code here" not in result

    def test_ensures_h1_at_start(self):
        md = "## Branch 1\n### Leaf 1\n## Branch 2"
        result = postprocess_mindmap_markdown(md)
        assert result.startswith("# ")

    def test_adds_h1_if_missing(self):
        md = "Just some text\n## Branch"
        result = postprocess_mindmap_markdown(md)
        assert "# " in result

    def test_removes_empty_headers(self):
        md = "# Root\n##\n### \n## Branch"
        result = postprocess_mindmap_markdown(md)
        lines = result.split("\n")
        for line in lines:
            if line.startswith("#"):
                assert len(line.strip()) > 2, f"Empty header: '{line}'"

    def test_preserves_valid_structure(self):
        md = "# Root\n## Branch 1\n- item 1\n## Branch 2\n- item 2"
        result = postprocess_mindmap_markdown(md)
        assert result == md


class TestRenderMindmapHtml:
    def test_returns_iframe(self):
        md = "# Root\n## Branch"
        result = render_mindmap_html(md, uid="test")
        assert '<iframe src="/mindmap/test"' in result

    def test_stores_html(self):
        md = "# Root\n## Branch"
        render_mindmap_html(md, uid="stored")
        stored = get_stored_mindmap("stored")
        assert stored is not None
        assert "markmap-container" in stored
        assert "/mindmap-static/js/markmap-lib.min.js" in stored
        assert "Root" in stored

    def test_unique_uid(self):
        md = "# Root"
        html1 = render_mindmap_html(md, uid="a")
        html2 = render_mindmap_html(md, uid="b")
        assert "/mindmap/a" in html1
        assert "/mindmap/b" in html2

    def test_stored_html_has_markmap_init(self):
        md = "# Root\n## Branch"
        render_mindmap_html(md, uid="init_test")
        stored = get_stored_mindmap("init_test")
        assert stored is not None
        assert "Markmap.create" in stored
        assert "Transformer" in stored
        assert "autoFit" in stored
        assert "/mindmap-static/js/markmap-lib.min.js" in stored
        assert "/mindmap-static/js/markmap-view.min.js" in stored
        assert "prefers-color-scheme" in stored
        assert "window.parent" not in stored
        assert "<!--MARKMAP_" not in stored
        assert "Интеллектуальная карта" in stored


class TestRenderMindmapFallback:
    def test_renders_tree(self):
        md = "# Root\n## Branch 1\n- item\n## Branch 2"
        html = render_mindmap_fallback(md)
        assert "текстовый режим" in html
        assert "Root" in html
        assert "Branch 1" in html

    def test_handles_h3(self):
        md = "# Root\n## Branch\n### Sub"
        html = render_mindmap_fallback(md)
        assert "Sub" in html


class TestMarkdownToTreeHtml:
    def test_h1_bold(self):
        html = _markdown_to_tree_html("# Title")
        assert "<strong" in html
        assert "Title" in html

    def test_h2_colored(self):
        html = _markdown_to_tree_html("## Branch")
        assert "margin-left:20px" in html
        assert "Branch" in html

    def test_list_items(self):
        html = _markdown_to_tree_html("- item text")
        assert "• item text" in html

    def test_empty_lines_skipped(self):
        html = _markdown_to_tree_html("# Root\n\n## Branch")
        assert html.count("<div") == 1  # H2 produces one div; H1 uses <strong>, not <div>


class TestSanitizeMarkdown:
    def test_removes_script_tags(self):
        md = '# Topic\n<script>alert("xss")</script>\n## Branch'
        result = _sanitize_markdown(md)
        assert "<script>" not in result
        assert "alert" not in result
        assert "## Branch" in result

    def test_removes_event_handlers(self):
        md = '# Topic\n## Branch\n<div onclick="evil()">text</div>'
        result = _sanitize_markdown(md)
        assert "onclick" not in result
        assert "evil" not in result

    def test_removes_iframe_tags(self):
        md = '# Topic\n<iframe src="evil.com"></iframe>\n## Branch'
        result = _sanitize_markdown(md)
        assert "<iframe" not in result
        assert "evil.com" not in result

    def test_preserves_normal_markdown(self):
        md = "# Root\n## Branch\n### Sub\n- item"
        result = _sanitize_markdown(md)
        assert result == md

    def test_removes_form_and_input(self):
        md = '# Topic\n<form action="evil"><input type="text"></form>\n## Branch'
        result = _sanitize_markdown(md)
        assert "<form" not in result
        assert "<input" not in result


class TestMindmapSystemPrompt:
    def test_prompt_not_empty(self):
        assert len(MINDMAP_SYSTEM_PROMPT) > 100

    def test_prompt_has_rules(self):
        assert "ПРАВИЛА" in MINDMAP_SYSTEM_PROMPT

    def test_prompt_has_example(self):
        assert "ПРИМЕР" in MINDMAP_SYSTEM_PROMPT

    def test_prompt_mentions_headers(self):
        assert "#" in MINDMAP_SYSTEM_PROMPT
        assert "##" in MINDMAP_SYSTEM_PROMPT


class TestTemplateBraces:
    def test_no_double_open_braces_in_output(self):
        """Rendered HTML MUST NOT contain '{{' (double open braces) from template."""
        md = "# Root\n## Branch 1\n### Leaf 1\n## Branch 2"
        render_mindmap_html(md, uid="brace_test_open")
        stored = get_stored_mindmap("brace_test_open")
        assert stored is not None
        # The markdown content itself shouldn't contain {{, so check the whole output
        assert "{{" not in stored

    def test_no_double_close_braces_in_output(self):
        """Rendered HTML MUST NOT contain '}}' (double close braces) from template."""
        md = "# Root\n## Branch 1\n### Leaf 1\n## Branch 2"
        render_mindmap_html(md, uid="brace_test_close")
        stored = get_stored_mindmap("brace_test_close")
        assert stored is not None
        assert "}}" not in stored

    def test_placeholder_replaced_in_output(self):
        """The <!--MARKDOWN_CONTENT--> placeholder must be replaced with actual content."""
        md = "# Root\n## Branch"
        render_mindmap_html(md, uid="placeholder_test")
        stored = get_stored_mindmap("placeholder_test")
        assert stored is not None
        assert "<!--MARKDOWN_CONTENT-->" not in stored
        assert "Root" in stored

    def test_single_brace_javascript_in_output(self):
        """JavaScript object literals must use single braces, not double."""
        md = "# Root\n## Branch"
        render_mindmap_html(md, uid="js_brace_test")
        stored = get_stored_mindmap("js_brace_test")
        assert stored is not None
        # Should find single-brace patterns like { autoFit: true }
        assert "autoFit: true" in stored
        assert "{{ autoFit:" not in stored

    def test_single_brace_css_in_output(self):
        """CSS rules must use single braces, not double."""
        md = "# Root\n## Branch"
        render_mindmap_html(md, uid="css_brace_test")
        stored = get_stored_mindmap("css_brace_test")
        assert stored is not None
        # Should find single-brace CSS like body {
        assert "body {" in stored
        # Should NOT find double-brace CSS
        assert "body {{" not in stored


class TestScriptEscaping:
    def test_script_tag_removed_from_content(self):
        md = "# Root\n## Branch\n### <script>alert(1)</script> test"
        render_mindmap_html(md, uid="script_escape_test")
        stored = get_stored_mindmap("script_escape_test")
        assert stored is not None
        # The markdown content area should not contain raw <script> from user input
        # (sanitizer strips it), but the template's own </script> closers are fine
        content_match = stored.split('id="markdown-source-script_escape_test">')
        if len(content_match) > 1:
            content_section = content_match[1].split("</script>")[0]
            assert "<script>" not in content_section


class TestGetStoredMindmap:
    def test_returns_none_for_unknown_uid(self):
        result = get_stored_mindmap("nonexistent_uid_xyz")
        assert result is None
