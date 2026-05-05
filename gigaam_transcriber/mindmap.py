"""
Генерация mind map из транскрипций.

LLM генерирует иерархический Markdown, который рендерится
в интерактивную mind map через Markmap.js.
HTML отдаётся через отдельный FastAPI эндпоинт /mindmap/{uid},
чтобы обойти DOMPurify-санитизацию Gradio.
"""

import html
import logging
import json
import re
import uuid
from gigaam_transcriber.summarizer import LLMClient
from gigaam_transcriber.context_utils import (
    get_context_budget,
    split_into_chunks,
)

logger = logging.getLogger(__name__)

_mindmap_store: dict[str, str] = {}


def get_stored_mindmap(uid: str) -> str | None:
    return _mindmap_store.get(uid)


# ---------------------------------------------------------------------------
# System prompt для генерации mind map
# ---------------------------------------------------------------------------

MINDMAP_SYSTEM_PROMPT = """\
Ты — эксперт по структурированию информации. Проанализируй транскрипцию \
и создай иерархическую структуру mind map в формате Markdown.

ПРАВИЛА:
1. Используй ТОЛЬКО заголовки (# , ## , ### ) для иерархии — это важно для рендеринга
2. Корневой узел — один заголовок # (тема/название)
3. Основные ветви — заголовки ##
4. Подветви — заголовки ###
5. Конкретные детали — маркированные списки под заголовками
6. НЕ используй нумерованные списки, таблицы или блоки кода
7. Каждый узел должен быть кратким (до 10 слов)
8. Структура должна быть сбалансированной (3-7 основных ветвей)

ПРИМЕР:
# Тема встречи
## Участники
### Иванов
- предложил план
### Петров
- согласовал бюджет
## Решения
### Бюджет утверждён
- 500 000 рублей
## Задачи
### Разработка
- дедлайн: 15 марта
- ответственный: Иванов

Генерируй ТОЛЬКО Markdown-структуру без пояснений. Всё на русском языке."""

MINDMAP_REDUCE_PROMPT = """\
Перед тобой несколько фрагментов mind map (Markmap Markdown), созданных из \
разных частей одной транскрипции. Объедини их в один целостный Markmap \
Markdown без повторов и противоречий.

ПРАВИЛА:
1. Один корневой заголовок # (общая тема)
2. 3-7 основных ветвей ##
3. Подветви ### и детали списками
4. Убери дубликаты, сохрани уникальные детали
5. Генерируй ТОЛЬКО Markdown без пояснений"""

# ---------------------------------------------------------------------------
# Валидация и post-processing
# ---------------------------------------------------------------------------


def validate_mindmap_markdown(md_text: str) -> bool:
    """
    Проверить, что Markdown подходит для mind map.

    Returns:
        True если структура корректна
    """
    lines = md_text.strip().split("\n")
    has_h1 = any(line.startswith("# ") for line in lines)
    has_h2 = any(line.startswith("## ") for line in lines)
    return has_h1 and has_h2


def postprocess_mindmap_markdown(md_text: str) -> str:
    """
    Post-processing Markdown для mind map.

    - Убирает блоки кода
    - Убирает таблицы
    - Гарантирует наличие корневого H1
    - Исправляет пропуски в иерархии
    """
    lines = md_text.strip().split("\n")
    processed: list[str] = []
    in_code_block = False

    for line in lines:
        if line.strip().startswith("```"):
            in_code_block = not in_code_block
            continue
        if in_code_block:
            continue

        # Пропускаем таблицы
        if "|" in line and re.match(r"^[\s|:-]+$", line):
            continue

        # Пропускаем пустые заголовки
        if re.match(r"^#{1,4}\s*$", line):
            continue

        processed.append(line)

    # Гарантируем H1 в начале
    if processed and not processed[0].startswith("# "):
        processed.insert(0, "# Тема транскрипции")

    if not any(l.startswith("# ") for l in processed):
        processed.insert(0, "# Тема транскрипции")

    return "\n".join(processed)


def _markdown_to_tree_html(md_text: str) -> str:
    """Конвертировать Markdown в HTML-дерево для fallback-режима."""
    lines = md_text.strip().split("\n")
    html_parts: list[str] = []

    for line in lines:
        if not line.strip():
            continue
        if line.startswith("# "):
            html_parts.append(f'<strong style="font-size:1.2em;">{html.escape(line[2:])}</strong>')
        elif line.startswith("## "):
            html_parts.append(
                f'<div style="margin-left:20px; margin-top:8px;"><span style="color:#1f77b4; font-weight:600;">{html.escape(line[3:])}</span>'
            )
        elif line.startswith("### "):
            html_parts.append(
                f'<div style="margin-left:40px; margin-top:4px;"><span style="color:#2ca02c; font-weight:600;">{html.escape(line[4:])}</span>'
            )
        elif line.startswith("- "):
            html_parts.append(f'<div style="margin-left:60px;">• {html.escape(line[2:])}</div>')
        else:
            html_parts.append(f'<div style="margin-left:20px;">{html.escape(line)}</div>')

    return "\n".join(html_parts)


# ---------------------------------------------------------------------------
# Санитизация Markdown
# ---------------------------------------------------------------------------


def _sanitize_markdown(md_text: str) -> str:
    """Sanitize Markdown to prevent XSS in embedded HTML."""
    text = re.sub(r"<script[^>]*>.*?</script>", "", md_text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'\s+on\w+\s*=\s*["\'][^"\']*["\']', "", text, flags=re.IGNORECASE)
    for tag in ("iframe", "object", "embed", "form", "input"):
        text = re.sub(rf"<{tag}[^>]*>.*?</{tag}>", "", text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(rf"<{tag}[^>]*/?\s*>", "", text, flags=re.IGNORECASE)
    return text



# ---------------------------------------------------------------------------
# Локализация
# ---------------------------------------------------------------------------

TRANSLATIONS = {
    "ru-RU": {
        "ui_title": "🧠 Интеллектуальная карта",
        "ui_download_png": "PNG",
        "ui_download_svg": "SVG",
        "ui_download_md": "Markdown",
        "ui_zoom_out": "−",
        "ui_zoom_reset": "Сброс",
        "ui_zoom_in": "+",
        "ui_depth_select": "Уровень",
        "ui_depth_all": "Все",
        "ui_depth_2": "Уровень 2",
        "ui_depth_3": "Уровень 3",
        "ui_fullscreen": "Полный экран",
        "ui_theme": "Тема",
        "ui_footer": "<b>Работает на</b> <a href='https://markmap.js.org/' target='_blank' rel='noopener noreferrer'>Markmap</a>",
        "html_error_missing_content": "⚠️ Не удалось загрузить карту: отсутствует содержимое.",
        "html_error_load_failed": "⚠️ Ошибка загрузки ресурсов. Попробуйте позже.",
        "js_done": "Готово",
        "js_failed": "Ошибка",
        "js_generating": "Генерация...",
        "js_filename": "mindmap.png",
        "md_image_alt": "🧠 Интеллектуальная карта",
    },
}

# ---------------------------------------------------------------------------
# Rich HTML-шаблон для Markmap.js (assembled from root mindmap.py)
# ---------------------------------------------------------------------------

RICH_MINDMAP_TEMPLATE = """\
<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<style>
:root {
    --primary-color: #1e88e5;
    --secondary-color: #43a047;
    --background-color: #f4f6f8;
    --card-bg-color: #ffffff;
    --text-color: #000000;
    --link-color: #546e7a;
    --node-stroke-color: #90a4ae;
    --muted-text-color: #546e7a;
    --border-color: #e0e0e0;
    --header-gradient: linear-gradient(135deg, var(--secondary-color), var(--primary-color));
    --shadow: 0 10px 20px rgba(0, 0, 0, 0.06);
    --border-radius: 12px;
    --font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
}
.theme-dark {
    --primary-color: #64b5f6;
    --secondary-color: #81c784;
    --background-color: #111827;
    --card-bg-color: #1f2937;
    --text-color: #ffffff;
    --link-color: #cbd5e1;
    --node-stroke-color: #94a3b8;
    --muted-text-color: #9ca3af;
    --border-color: #374151;
    --header-gradient: linear-gradient(135deg, #0ea5e9, #22c55e);
    --shadow: 0 10px 20px rgba(0, 0, 0, 0.3);
}
html, body {
    margin: 0;
    padding: 0;
    width: 100vw;
    height: 100vh;
    background: var(--card-bg-color);
    overflow: hidden;
}
.mindmap-container-wrapper {
    font-family: var(--font-family);
    line-height: 1.6;
    color: var(--text-color);
    margin: 0;
    padding: 0;
    -webkit-font-smoothing: antialiased;
    -moz-osx-font-smoothing: grayscale;
    display: flex;
    flex-direction: column;
    background: var(--card-bg-color);
    width: 100vw;
    height: 100vh;
    box-sizing: border-box;
    overflow: hidden;
    border: none;
    border-radius: 0;
    box-shadow: none;
}
.header {
    background: var(--card-bg-color);
    color: var(--text-color);
    padding: 12px 16px;
    display: flex;
    flex-direction: column;
    gap: 12px;
    flex-shrink: 0;
    border-bottom: 1px solid var(--border-color);
    z-index: 10;
}
.header-top {
    display: flex;
    align-items: center;
    gap: 12px;
}
.header h1 {
    margin: 0;
    font-size: 1.2em;
    font-weight: 600;
    letter-spacing: 0.5px;
    display: flex;
    align-items: center;
    gap: 8px;
}
.header-credits {
    font-size: 0.8em;
    color: var(--muted-text-color);
    opacity: 0.8;
    white-space: nowrap;
}
.header-credits a {
    color: var(--primary-color);
    text-decoration: none;
    border-bottom: 1px dotted var(--link-color);
}
.content-area {
    padding: 0;
    flex: 1 1 0;
    background: var(--card-bg-color);
    position: relative;
    overflow: hidden;
    width: 100%;
    min-height: 0;
    /* Height will be computed dynamically by JS below */
}
.markmap-container {
    position: absolute;
    top: 0;
    left: 0;
    right: 0;
    bottom: 0;
    background-color: var(--card-bg-color);
}
.markmap-container svg {
    width: 100%;
    height: 100%;
    display: block;
}
.markmap-container svg text {
    fill: var(--text-color) !important;
    font-family: var(--font-family);
}
.markmap-container svg foreignObject,
.markmap-container svg .markmap-foreign,
.markmap-container svg .markmap-foreign div {
    color: var(--text-color) !important;
    font-family: var(--font-family);
}
.markmap-container svg .markmap-link {
    stroke: var(--link-color) !important;
    stroke-opacity: 0.6;
}
.theme-dark .markmap-node circle {
    fill: var(--card-bg-color) !important;
}
.markmap-container svg .markmap-node circle,
.markmap-container svg .markmap-node rect {
    stroke: var(--node-stroke-color) !important;
}
.control-rows {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 12px;
    margin-left: auto; /* Push controls to the right */
}
.btn-group {
    display: inline-flex;
    gap: 4px;
    align-items: center;
    border: 1px solid var(--border-color);
    border-radius: 6px;
    padding: 2px;
    background: var(--background-color);
}
.control-btn {
    background-color: transparent;
    color: var(--text-color);
    border: none;
    padding: 4px 10px;
    border-radius: 4px;
    font-size: 0.85em;
    font-weight: 500;
    cursor: pointer;
    transition: all 0.2s ease;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    height: 28px;
    box-sizing: border-box;
    opacity: 0.8;
}
.control-btn:hover {
    background-color: var(--card-bg-color);
    opacity: 1;
    box-shadow: 0 1px 3px rgba(0,0,0,0.1);
}
.control-btn:active {
    transform: translateY(1px);
}
.control-btn.primary { 
    background-color: var(--primary-color);
    color: white;
    opacity: 1;
}
.control-btn.primary:hover {
    box-shadow: 0 2px 5px rgba(30,136,229,0.3);
}

select.control-btn {
    appearance: none;
    padding-right: 28px;
    background-image: url("data:image/svg+xml;charset=US-ASCII,%3Csvg%20xmlns%3D%22http%3A%2F%2Fwww.w3.org%2F2000%2Fsvg%22%20width%3D%22292.4%22%20height%3D%22292.4%22%3E%3Cpath%20fill%3D%22%23FFFFFF%22%20d%3D%22M287%2069.4a17.6%2017.6%200%200%200-13-5.4H18.4c-5%200-9.3%201.8-12.9%205.4A17.6%2017.6%200%200%200%200%2082.2c0%205%201.8%209.3%205.4%2012.9l128%20127.9c3.6%203.6%207.8%205.4%2012.8%205.4s9.2-1.8%2012.8-5.4L287%2095c3.5-3.5%205.4-7.8%205.4-12.8%200-5-1.9-9.2-5.5-12.8z%22%2F%3E%3C%2Fsvg%3E");
    background-repeat: no-repeat;
    background-position: right 8px center;
    background-size: 10px;
}
.control-btn option {
    background-color: var(--card-bg-color);
    color: var(--text-color);
}
.error-message {
    color: #c62828;
    background-color: #ffcdd2;
    border: 1px solid #ef9a9a;
    padding: 14px;
    border-radius: 8px;
    font-weight: 500;
    font-size: 1em;
    margin: 10px;
}

/* Mobile Responsive Adjustments */
@media screen and (max-width: 768px) {
    .mindmap-container-wrapper {
        min-height: 400px;
        height: 80vh;
    }
    .header {
        flex-direction: column;
        gap: 10px;
    }
    .btn-group {
        padding: 2px;
    }
    .control-btn {
        padding: 4px 6px;
        font-size: 0.75em;
        height: 28px;
    }
    select.control-btn {
        padding-right: 20px;
        background-position: right 4px center;
    }
}
</style>
</head>
<body>
<div class="mindmap-container-wrapper">
    <div class="header">
        <div class="header-top">
            <h1><!--MARKMAP_UI_TITLE--></h1>
            <div class="header-credits">
                <span><!--MARKMAP_UI_FOOTER--></span>
            </div>
            <div class="control-rows">
                <div class="btn-group">
                    <button id="download-png-btn-<!--MARKMAP_UNIQUE_ID-->" class="control-btn primary" title="<!--MARKMAP_UI_DOWNLOAD_PNG-->">PNG</button>
                    <button id="download-svg-btn-<!--MARKMAP_UNIQUE_ID-->" class="control-btn" title="<!--MARKMAP_UI_DOWNLOAD_SVG-->">SVG</button>
                    <button id="download-md-btn-<!--MARKMAP_UNIQUE_ID-->" class="control-btn" title="<!--MARKMAP_UI_DOWNLOAD_MD-->">MD</button>
                </div>
                <div class="btn-group">
                    <button id="zoom-out-btn-<!--MARKMAP_UNIQUE_ID-->" class="control-btn" title="<!--MARKMAP_UI_ZOOM_OUT-->">－</button>
                    <button id="zoom-reset-btn-<!--MARKMAP_UNIQUE_ID-->" class="control-btn" title="<!--MARKMAP_UI_ZOOM_RESET-->">↺</button>
                    <button id="zoom-in-btn-<!--MARKMAP_UNIQUE_ID-->" class="control-btn" title="<!--MARKMAP_UI_ZOOM_IN-->">＋</button>
                </div>
                <div class="btn-group">
                    <select id="depth-select-<!--MARKMAP_UNIQUE_ID-->" class="control-btn" title="<!--MARKMAP_UI_DEPTH_SELECT-->">
                        <option value="0" selected><!--MARKMAP_UI_DEPTH_ALL--></option>
                        <option value="2"><!--MARKMAP_UI_DEPTH_2--></option>
                        <option value="3"><!--MARKMAP_UI_DEPTH_3--></option>
                    </select>
                    <button id="fullscreen-btn-<!--MARKMAP_UNIQUE_ID-->" class="control-btn" title="<!--MARKMAP_UI_FULLSCREEN-->">⛶</button>
                    <button id="theme-toggle-btn-<!--MARKMAP_UNIQUE_ID-->" class="control-btn" title="<!--MARKMAP_UI_THEME-->">◑</button>
                </div>
            </div>
        </div>
    </div>
    <div class="content-area">
        <div class="markmap-container" id="markmap-container-<!--MARKMAP_UNIQUE_ID-->"></div>
    </div>
</div>

<script type="text/template" id="markdown-source-<!--MARKMAP_UNIQUE_ID-->"><!--MARKMAP_MARKDOWN--></script>

<script>
  (function() {
    const uniqueId = "<!--MARKMAP_UNIQUE_ID-->";
    const i18n = <!--MARKMAP_I18N_JSON-->;

    const loadScriptOnce = (src, checkFn) => {
        if (checkFn()) return Promise.resolve();
        return new Promise((resolve, reject) => {
            const existing = document.querySelector(`script[data-src="${src}"]`);
            if (existing) {
                existing.addEventListener('load', () => resolve());
                existing.addEventListener('error', () => reject(new Error('Loading failed: ' + src)));
                return;
            }
            const script = document.createElement('script');
            script.src = src;
            script.async = true;
            script.dataset.src = src;
            script.onload = () => resolve();
            script.onerror = () => reject(new Error('Loading failed: ' + src));
            document.head.appendChild(script);
        });
    };

    const ensureMarkmapReady = () =>
        loadScriptOnce('/mindmap-static/js/d3.min.js', () => window.d3)
            .then(() => loadScriptOnce('/mindmap-static/js/markmap-lib.min.js', () => window.markmap && window.markmap.Transformer))
            .then(() => loadScriptOnce('/mindmap-static/js/markmap-view.min.js', () => window.markmap && window.markmap.Markmap));

    const parseColorLuma = (colorStr) => {
        if (!colorStr) return null;
        // hex #rrggbb or rrggbb
        let m = colorStr.match(/^#?([0-9a-f]{6})$/i);
        if (m) {
            const hex = m[1];
            const r = parseInt(hex.slice(0, 2), 16);
            const g = parseInt(hex.slice(2, 4), 16);
            const b = parseInt(hex.slice(4, 6), 16);
            return (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255;
        }
        // rgb(r, g, b) or rgba(r, g, b, a)
        m = colorStr.match(/rgba?\\s*\\(\\s*(\\d+)\\s*,\\s*(\\d+)\\s*,\\s*(\\d+)/i);
        if (m) {
            const r = parseInt(m[1], 10);
            const g = parseInt(m[2], 10);
            const b = parseInt(m[3], 10);
            return (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255;
        }
        return null;
    };

        const setTheme = (wrapperEl, explicitTheme) => {
            const prefersDark = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
            const chosen = explicitTheme || (prefersDark ? 'dark' : 'light');
        wrapperEl.classList.toggle('theme-dark', chosen === 'dark');
        return chosen;
    };

    const renderMindmap = () => {
        const containerEl = document.getElementById('markmap-container-' + uniqueId);
        if (!containerEl || containerEl.dataset.markmapRendered) return;

        const sourceEl = document.getElementById('markdown-source-' + uniqueId);
        if (!sourceEl) return;

        const markdownContent = sourceEl.textContent.trim();
        if (!markdownContent) {
            containerEl.innerHTML = '<div class="error-message">' + i18n.html_error_missing_content + '</div>';
            return;
        }

        ensureMarkmapReady().then(() => {
            const svgEl = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
            svgEl.style.width = '100%';
            svgEl.style.height = '100%';
            containerEl.innerHTML = '';
            containerEl.appendChild(svgEl);

            const { Transformer, Markmap } = window.markmap;
            const transformer = new Transformer();
            const { root } = transformer.transform(markdownContent);

            const containerWidth = containerEl.clientWidth || window.innerWidth;
            const containerHeight = containerEl.clientHeight || window.innerHeight;
            const isPortrait = containerHeight >= containerWidth * 0.8;

            const style = (id) => `
                ${id} text, ${id} foreignObject { font-size: 16px; }
                ${id} foreignObject { line-height: 1.6; }
                ${id} foreignObject div { padding: 2px 0; }
                ${id} foreignObject h1 { font-size: 24px; font-weight: 700; margin: 0 0 6px 0; border-bottom: 2px solid currentColor; padding-bottom: 4px; display: inline-block; }
                ${id} foreignObject h2 { font-size: 18px; font-weight: 600; margin: 0 0 4px 0; }
                ${id} foreignObject strong { font-weight: 700; }
                ${id} foreignObject p { margin: 2px 0; }
            `;
            
            let responsiveMaxWidth;
            let dynamicSpacingVertical = 5;
            let dynamicSpacingHorizontal = 80;

            if (isPortrait) {
                // Old Version / Mobile: Force early text wrap to explode height and tighten width
                responsiveMaxWidth = Math.max(140, Math.floor(containerWidth * 0.35)); 
                dynamicSpacingVertical = 20; // Explicitly spread out branches vertically
                dynamicSpacingHorizontal = 60;
            } else {
                // New Version (Direct Chat): Generous width to utilize massive horizontal space
                responsiveMaxWidth = Math.max(220, Math.floor(containerWidth * 0.35)); 
                dynamicSpacingVertical = 12;
                dynamicSpacingHorizontal = 60; // Tighter horizontal gaps so the chart doesn't get too wide to scale up
            }

            const options = {
                autoFit: true,
                style: style,
                initialExpandLevel: 3,
                zoom: true,
                pan: true,
                fitRatio: 0.95, // Maximize scale to make text bigger
                maxWidth: responsiveMaxWidth,
                spacingVertical: dynamicSpacingVertical,
                spacingHorizontal: dynamicSpacingHorizontal,
                colorFreezeLevel: 2
            };

            const markmapInstance = Markmap.create(svgEl, options, root);
            
            // Extra tick: force fit to make sure bounding box centers
            setTimeout(() => {
                markmapInstance.fit();
            }, 100);

            // Dynamically refit if the user drags to resize the sidebar/iframe
            const resizeObserver = new ResizeObserver(entries => {
                for (let entry of entries) {
                    if (entry.contentRect.width > 0 && entry.contentRect.height > 0) {
                        requestAnimationFrame(() => markmapInstance.fit());
                    }
                }
            });
            resizeObserver.observe(containerEl);

            window.markmapInstance = markmapInstance; // Expose for external triggers
            containerEl.dataset.markmapRendered = 'true';

            setupControls({
                containerEl,
                svgEl,
                markmapInstance,
                root,
                isPortrait
            });

        }).catch((error) => {
            console.error('Markmap loading error:', error);
            containerEl.innerHTML = '<div class="error-message">' + i18n.html_error_load_failed + '</div>';
        });
    };

    // Dynamically fix layout: measure header height and set content-area height precisely
    const adjustLayout = () => {
        const wrapper = document.querySelector('.mindmap-container-wrapper');
        const header = document.querySelector('.header');
        const contentArea = document.querySelector('.content-area');
        if (!wrapper || !header || !contentArea) return;
        const headerH = header.getBoundingClientRect().height;
        const totalH = wrapper.getBoundingClientRect().height;
        const contentH = Math.max(totalH - headerH, 200);
        contentArea.style.height = contentH + 'px';
    };

    // Run once after DOM is ready, then on any resize
    adjustLayout();
    window.addEventListener('resize', () => {
        adjustLayout();
        if (window.markmapInstance) {
            requestAnimationFrame(() => window.markmapInstance.fit());
        }
    });

    const setupControls = ({ containerEl, svgEl, markmapInstance, root, isPortrait }) => {
        const downloadSvgBtn = document.getElementById('download-svg-btn-' + uniqueId);
        const downloadPngBtn = document.getElementById('download-png-btn-' + uniqueId);
        const downloadMdBtn = document.getElementById('download-md-btn-' + uniqueId);
        const zoomInBtn = document.getElementById('zoom-in-btn-' + uniqueId);
        const zoomOutBtn = document.getElementById('zoom-out-btn-' + uniqueId);
        const zoomResetBtn = document.getElementById('zoom-reset-btn-' + uniqueId);
        const depthSelect = document.getElementById('depth-select-' + uniqueId);
        const fullscreenBtn = document.getElementById('fullscreen-btn-' + uniqueId);
        const themeToggleBtn = document.getElementById('theme-toggle-btn-' + uniqueId);

        if (depthSelect) {
            depthSelect.value = "3";
        }

        const wrapper = containerEl.closest('.mindmap-container-wrapper');
        let currentTheme = setTheme(wrapper);

        const showFeedback = (button, textOk = i18n.js_done, textFail = i18n.js_failed) => {
            if (!button) return;
            const buttonText = button.querySelector('.btn-text') || button;
            const originalText = buttonText.textContent;
            button.disabled = true;
            buttonText.textContent = textOk;
            button.classList.add('copied');
            setTimeout(() => {
                buttonText.textContent = originalText;
                button.disabled = false;
                button.classList.remove('copied');
            }, 1800);
        };

        const handleDownloadSVG = () => {
            const svg = containerEl.querySelector('svg');
            if (!svg) return;
            // Inline styles before export
            const clonedSvg = svg.cloneNode(true);
            const style = document.createElement('style');
            style.textContent = `
                text { font-family: sans-serif; fill: ${currentTheme === 'dark' ? '#ffffff' : '#000000'}; }
                foreignObject, .markmap-foreign, .markmap-foreign div { color: ${currentTheme === 'dark' ? '#ffffff' : '#000000'}; font-family: sans-serif; font-size: 14px; }
                h1 { font-size: 22px; font-weight: 700; margin: 0; }
                h2 { font-size: 18px; font-weight: 600; margin: 0; }
                strong { font-weight: 700; }
                .markmap-link { stroke: ${currentTheme === 'dark' ? '#cbd5e1' : '#546e7a'}; }
                .markmap-node circle, .markmap-node rect { stroke: ${currentTheme === 'dark' ? '#94a3b8' : '#94a3b8'}; }
            `;
            clonedSvg.prepend(style);
            const svgData = new XMLSerializer().serializeToString(clonedSvg);
                const blob = new Blob([svgData], {type: 'image/svg+xml'});
                const url = URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url; a.download = 'mindmap.svg';
                document.body.appendChild(a);
                a.click();
                document.body.removeChild(a);
                URL.revokeObjectURL(url);
                showFeedback(downloadSvgBtn);
        };

        const handleDownloadMD = () => {
            const markdownContent = document.getElementById('markdown-source-' + uniqueId)?.textContent || '';
            if (!markdownContent) return;
                const blob = new Blob([markdownContent], {type: 'text/markdown'});
                const url = URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url; a.download = 'mindmap.md';
                document.body.appendChild(a);
                a.click();
                document.body.removeChild(a);
                URL.revokeObjectURL(url);
                showFeedback(downloadMdBtn);
        };

        const handleDownloadPNG = () => {
            const btn = downloadPngBtn;
            const btnTextEl = btn.querySelector('.btn-text') || btn;
            const originalText = btnTextEl.textContent;
            btnTextEl.textContent = i18n.js_generating;
            btn.disabled = true;

            const svg = containerEl.querySelector('svg');
            if (!svg) {
                btnTextEl.textContent = originalText;
                btn.disabled = false;
                showFeedback(btn, i18n.js_failed, i18n.js_failed);
                return;
            }

            try {
                // Clone SVG and inline styles
                const clonedSvg = svg.cloneNode(true);
                clonedSvg.setAttribute('xmlns', 'http://www.w3.org/2000/svg');
                clonedSvg.setAttribute('xmlns:xlink', 'http://www.w3.org/1999/xlink');
                
                const rect = svg.getBoundingClientRect();
                const width = rect.width || 800;
                const height = rect.height || 600;
                clonedSvg.setAttribute('width', width);
                clonedSvg.setAttribute('height', height);

                // Remove foreignObject (HTML content) and replace with text
                const foreignObjects = clonedSvg.querySelectorAll('foreignObject');
                foreignObjects.forEach(fo => {
                    const text = fo.textContent || '';
                    const g = document.createElementNS('http://www.w3.org/2000/svg', 'g');
                    const textEl = document.createElementNS('http://www.w3.org/2000/svg', 'text');
                    textEl.setAttribute('x', fo.getAttribute('x') || '0');
                    textEl.setAttribute('y', (parseFloat(fo.getAttribute('y') || '0') + 14).toString());
                    textEl.setAttribute('fill', currentTheme === 'dark' ? '#ffffff' : '#000000');
                    textEl.setAttribute('font-family', 'sans-serif');
                    textEl.setAttribute('font-size', '14');
                    textEl.textContent = text.trim();
                    g.appendChild(textEl);
                    fo.parentNode.replaceChild(g, fo);
                });

                // Inline styles
                const style = document.createElementNS('http://www.w3.org/2000/svg', 'style');
                style.textContent = `
                    text { font-family: sans-serif; font-size: 14px; fill: ${currentTheme === 'dark' ? '#ffffff' : '#000000'}; }
                    .markmap-link { fill: none; stroke: ${currentTheme === 'dark' ? '#cbd5e1' : '#546e7a'}; stroke-width: 2; }
                    .markmap-node circle { stroke: ${currentTheme === 'dark' ? '#94a3b8' : '#94a3b8'}; stroke-width: 2; }
                `;
                clonedSvg.insertBefore(style, clonedSvg.firstChild);

                // Add background rect
                const bgRect = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
                bgRect.setAttribute('width', '100%');
                bgRect.setAttribute('height', '100%');
                bgRect.setAttribute('fill', currentTheme === 'dark' ? '#1f2937' : '#ffffff');
                clonedSvg.insertBefore(bgRect, clonedSvg.firstChild);

                const svgData = new XMLSerializer().serializeToString(clonedSvg);
                const svgBase64 = btoa(unescape(encodeURIComponent(svgData)));
                const dataUrl = 'data:image/svg+xml;base64,' + svgBase64;

                const img = new Image();
                img.onload = () => {
                    const canvas = document.createElement('canvas');
                    const scale = 9;
                    canvas.width = width * scale;
                    canvas.height = height * scale;
                    const ctx = canvas.getContext('2d');
                    ctx.scale(scale, scale);
                    ctx.fillStyle = currentTheme === 'dark' ? '#1f2937' : '#ffffff';
                    ctx.fillRect(0, 0, width, height);
                    ctx.drawImage(img, 0, 0, width, height);

                    canvas.toBlob((blob) => {
                        if (!blob) {
                            btnTextEl.textContent = originalText;
                            btn.disabled = false;
                            showFeedback(btn, i18n.js_failed, i18n.js_failed);
                            return;
                        }
                        
                        // Use non-bubbling MouseEvent to avoid router interception
                        const a = document.createElement('a');
                        a.download = i18n.js_filename;
                        a.href = URL.createObjectURL(blob);
                        a.style.display = 'none';
                        document.body.appendChild(a);
                        
                        const evt = new MouseEvent('click', {
                            view: window,
                            bubbles: false,
                            cancelable: false
                        });
                        a.dispatchEvent(evt);
                        
                        setTimeout(() => {
                            document.body.removeChild(a);
                            URL.revokeObjectURL(a.href);
                        }, 100);

                        btnTextEl.textContent = originalText;
                        btn.disabled = false;
                        showFeedback(btn);
                    }, 'image/png');
                };
                
                img.onerror = (e) => {
                    console.error('PNG image load error:', e);
                    btnTextEl.textContent = originalText;
                    btn.disabled = false;
                    showFeedback(btn, i18n.js_failed, i18n.js_failed);
                };
                
                img.src = dataUrl;
            } catch (err) {
                console.error('PNG export error:', err);
                btnTextEl.textContent = originalText;
                btn.disabled = false;
                showFeedback(btn, i18n.js_failed, i18n.js_failed);
            }
        };

        const handleZoom = (direction) => {
            if (direction === 'reset') {
                markmapInstance.fit();
                return;
            }
            // Simple zoom simulation if d3 zoom instance is not accessible
            // Markmap uses d3-zoom, so we can try to select the svg and transition
            const svg = d3.select(svgEl);
            // We can't easily access the internal zoom behavior object created by markmap
            // So we rely on fit() for reset, and maybe just let user scroll/pinch for zoom
            // Or we can try to rescale if supported
            if (markmapInstance.rescale) {
                const scale = direction === 'in' ? 1.25 : 0.8;
                markmapInstance.rescale(scale);
            } else {
                // Fallback: just fit, as manual transform manipulation conflicts with d3
                // Or we could try to find the zoom behavior attached to the node
                // const zoom = d3.zoomTransform(svgEl);
                // But we need the zoom behavior function to call scaleBy
            }
        };

        const setExpandLevel = (levelStr) => {
            const level = parseInt(levelStr, 10);
            const expandLevel = level === 0 ? Infinity : level;

            // Recursively set fold state on cloned tree nodes
            const applyFold = (node, currentDepth) => {
                if (!node) return;
                if (!node.payload) node.payload = {};
                if (expandLevel === Infinity) {
                    // Expand ALL: clear all fold flags
                    node.payload.fold = 0;
                } else {
                    // Fold any node deeper than the target level
                    node.payload.fold = currentDepth >= expandLevel ? 1 : 0;
                }
                if (node.children) {
                    node.children.forEach(child => applyFold(child, currentDepth + 1));
                }
            };

            const cleanRoot = JSON.parse(JSON.stringify(root));
            applyFold(cleanRoot, 0);

            markmapInstance.setOptions({ initialExpandLevel: expandLevel });
            markmapInstance.setData(cleanRoot);
            setTimeout(() => markmapInstance.fit(), 50);
        };

        const handleDepthChange = (e) => {
            setExpandLevel(e.target.value);
        };

        const handleFullscreen = () => {
            const el = wrapper || containerEl;
            if (!document.fullscreenElement) {
                el.requestFullscreen().then(() => {
                    if (depthSelect) depthSelect.value = "0";
                    setExpandLevel("0");
                }).catch(err => {
                    console.error('Fullscreen failed:', err);
                    // Fallback to container if wrapper fails
                    containerEl.requestFullscreen().then(() => {
                        if (depthSelect) depthSelect.value = "0";
                        setExpandLevel("0");
                    });
                });
            } else {
                document.exitFullscreen();
            }
        };
        
        document.addEventListener('fullscreenchange', () => {
            const isFs = !!document.fullscreenElement;
            if (isFs && (document.fullscreenElement === containerEl || document.fullscreenElement === wrapper)) {
                setTimeout(() => markmapInstance.fit(), 300);
            } else if (!isFs) {
                // Revert to default depth when exiting fullscreen
                const defaultLevel = "3";
                if (depthSelect) depthSelect.value = defaultLevel;
                setExpandLevel(defaultLevel);
            }
        });

        const handleThemeToggle = () => {
            currentTheme = currentTheme === 'dark' ? 'light' : 'dark';
            setTheme(wrapper, currentTheme);
        };

        downloadSvgBtn?.addEventListener('click', (e) => { e.stopPropagation(); handleDownloadSVG(); });
        downloadMdBtn?.addEventListener('click', (e) => { e.stopPropagation(); handleDownloadMD(); });
        downloadPngBtn?.addEventListener('click', (e) => { e.stopPropagation(); handleDownloadPNG(); });
        zoomInBtn?.addEventListener('click', (e) => { e.stopPropagation(); handleZoom('in'); });
        zoomOutBtn?.addEventListener('click', (e) => { e.stopPropagation(); handleZoom('out'); });
        zoomResetBtn?.addEventListener('click', (e) => { e.stopPropagation(); handleZoom('reset'); });
        depthSelect?.addEventListener('change', (e) => { e.stopPropagation(); handleDepthChange(e); });
        fullscreenBtn?.addEventListener('click', (e) => { e.stopPropagation(); handleFullscreen(); });
        themeToggleBtn?.addEventListener('click', (e) => { e.stopPropagation(); handleThemeToggle(); });
    };

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', renderMindmap);
    } else {
        renderMindmap();
    }
  })();
</script>
</body>
</html>"""


# Упрощённый fallback без CDN
_FALLBACK_ONLY_TEMPLATE = """\
<div style="font-family: Arial, sans-serif; padding: 16px; border: 1px solid #e0e0e0; border-radius: 8px; background: #fafafa;">
<h3 style="margin-top:0;">🧠 Mind Map (текстовый режим)</h3>
<p style="color:#666; font-size:0.9em;">Markmap.js не загружен. Отображается текстовая иерархия.</p>
<hr style="border:none;border-top:1px solid #eee;">
<div style="white-space:pre-wrap; font-size:0.95em; line-height:1.5;">{tree_html}</div>
</div>"""


# ---------------------------------------------------------------------------
# Генерация mind map
# ---------------------------------------------------------------------------


def generate_mindmap_markdown(
    transcription_text: str,
    llm_client: LLMClient,
) -> str:
    """Сгенерировать Markdown для mind map из транскрипции.

    Uses map-reduce for long texts that exceed 50% of the model's context.
    """
    logger.info("Mindmap: calling LLM (text length=%d chars)", len(transcription_text))

    model = llm_client.config.model
    budget = get_context_budget(model, MINDMAP_SYSTEM_PROMPT, transcription_text)

    if budget["needs_compression"]:
        raw_md = _generate_mindmap_map_reduce(transcription_text, llm_client, budget)
    else:
        raw_md = llm_client.call(
            MINDMAP_SYSTEM_PROMPT,
            transcription_text,
            max_tokens=4096,
        )

    logger.info(
        "Mindmap: LLM returned %d chars, first 200 chars: %s",
        len(raw_md),
        raw_md[:200].replace("\n", "\\n"),
    )

    processed = postprocess_mindmap_markdown(raw_md)

    logger.info(
        "Mindmap: post-processed %d chars, first 200 chars: %s",
        len(processed),
        processed[:200].replace("\n", "\\n"),
    )

    valid = validate_mindmap_markdown(processed)
    if not valid:
        logger.warning(
            "Mindmap: validation FAILED (has_h1=%s, has_h2=%s), using fallback structure",
            any(l.startswith("# ") for l in processed.split("\n")),
            any(l.startswith("## ") for l in processed.split("\n")),
        )
        processed = (
            "# Тема транскрипции\n"
            "## Ключевые моменты\n"
            "- Не удалось построить полную структуру\n"
            "## Основное содержание\n"
        ) + processed
    else:
        logger.info("Mindmap: validation passed")

    h1_count = sum(
        1 for l in processed.split("\n") if l.startswith("# ") and not l.startswith("## ")
    )
    h2_count = sum(
        1 for l in processed.split("\n") if l.startswith("## ") and not l.startswith("### ")
    )
    h3_count = sum(1 for l in processed.split("\n") if l.startswith("### "))
    bullet_count = sum(1 for l in processed.split("\n") if l.startswith("- "))
    logger.info(
        "Mindmap: structure summary — H1=%d, H2=%d, H3=%d, bullets=%d",
        h1_count,
        h2_count,
        h3_count,
        bullet_count,
    )

    return processed


def _generate_mindmap_map_reduce(text: str, llm_client: LLMClient, budget: dict) -> str:
    """Map-reduce mindmap generation for long texts."""
    chunk_max_tokens = min(3000, budget["total"] // 4)
    chunks = split_into_chunks(text, max_tokens=chunk_max_tokens)
    logger.info("Map-reduce mindmap: %d chunks", len(chunks))

    subtrees: list[str] = []
    for i, chunk in enumerate(chunks):
        logger.debug("Generating subtree from chunk %d/%d", i + 1, len(chunks))
        try:
            subtree = llm_client.call(MINDMAP_SYSTEM_PROMPT, chunk, max_tokens=4096)
            subtrees.append(subtree)
        except Exception as e:
            logger.warning("Failed to generate subtree from chunk %d: %s", i + 1, e)

    if not subtrees:
        return "# Тема транскрипции\n## Ошибка обработки\n- Не удалось сгенерировать структуру"

    combined = "\n\n---\n\n".join(subtrees)
    try:
        return llm_client.call(MINDMAP_REDUCE_PROMPT, combined, max_tokens=4096)
    except Exception as e:
        logger.error("Reduce step failed for mindmap: %s", e)
        return subtrees[0]



def render_mindmap_html(md_text: str, uid: str | None = None) -> str:
    """
    Рендерить mind map: сохранить HTML в in-memory store и вернуть iframe.

    HTML отдаётся через FastAPI /mindmap/{uid}, минуя DOMPurify Gradio.
    """
    if uid is None:
        uid = uuid.uuid4().hex[:12]

    sanitized_md = _sanitize_markdown(md_text)
    logger.info("Mindmap: sanitized markdown %d chars for uid=%s", len(sanitized_md), uid)

    i18n = TRANSLATIONS.get("ru-RU", {})

    escaped_md = sanitized_md.replace("</script>", "<\\/script>")

    full_html = RICH_MINDMAP_TEMPLATE
    full_html = full_html.replace("<!--MARKMAP_UNIQUE_ID-->", uid)
    full_html = full_html.replace("<!--MARKMAP_MARKDOWN-->", escaped_md)
    full_html = full_html.replace("<!--MARKMAP_I18N_JSON-->", json.dumps(i18n, ensure_ascii=False))
    for key, value in i18n.items():
        placeholder = f"<!--MARKMAP_{key.upper()}-->"
        full_html = full_html.replace(placeholder, value)

    _mindmap_store[uid] = full_html
    logger.info("Mindmap: stored HTML for uid=%s, size=%d bytes", uid, len(full_html))

    return f'<iframe src="/mindmap/{uid}" style="width:100%; height:650px; border:none; border-radius:8px;"></iframe>'

def render_mindmap_fallback(md_text: str) -> str:
    """
    Рендерить mind map в fallback HTML (без Markmap.js).

    Args:
        md_text: Иерархический Markdown

    Returns:
        HTML-строка для gr.HTML()
    """
    tree_html = _markdown_to_tree_html(md_text)
    return _FALLBACK_ONLY_TEMPLATE.format(tree_html=tree_html)
