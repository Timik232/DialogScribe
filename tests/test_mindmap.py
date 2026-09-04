"""Unit-тесты для mindmap.py — валидация, post-processing, fallback-рендер.

Серверный executable-HTML рендер (render_mindmap_html, _mindmap_store,
/mindmap/{uid}) удалён; остался только Markdown-генератор и текстовый
fallback-рендер.
"""

from gigaam_transcriber.mindmap import (
    validate_mindmap_markdown,
    postprocess_mindmap_markdown,
    render_mindmap_fallback,
    _markdown_to_tree_html,
    _sanitize_markdown,
    MINDMAP_SYSTEM_PROMPT,
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


