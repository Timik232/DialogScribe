/**
 * Centralized safe HTML rendering for untrusted (LLM/user-derived) content.
 *
 * Every raw-HTML sink in the app MUST go through these helpers. They render
 * Markdown with `marked` and sanitize the result with `DOMPurify` using a
 * minimal allowlist: script/style/iframe/event-handlers/javascript:-URLs are
 * stripped entirely, links are restricted to http/https/mailto and forced to
 * open in a new tab with `rel="noopener noreferrer"`.
 *
 * The app is a pure SPA (`ssr = false`), so DOMPurify always has a DOM.
 */
import { marked } from 'marked';
import DOMPurify from 'dompurify';

/** Block-level allowlist for rendered Markdown documents. */
const BLOCK_TAGS = [
	'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
	'p', 'ul', 'ol', 'li',
	'strong', 'em', 'b', 'i',
	'code', 'pre', 'blockquote',
	'table', 'thead', 'tbody', 'tr', 'th', 'td',
	'a', 'br', 'hr',
];

/** Inline-only allowlist for fragments embedded inside other renderers. */
const INLINE_TAGS = ['strong', 'em', 'b', 'i', 'code', 'a', 'br', 'span', 'del', 's', 'sub', 'sup'];

const ALLOWED_ATTR = ['href', 'title', 'align', 'target', 'rel'];

/** Only these link schemes may survive sanitization. */
const SAFE_LINK_SCHEME = /^(?:https?:|mailto:)/i;

let hooksInstalled = false;

function ensureSanitizeHooks(): void {
	if (hooksInstalled) return;
	hooksInstalled = true;
	DOMPurify.addHook('afterSanitizeAttributes', (node) => {
		if (node.tagName !== 'A') return;
		const href = node.getAttribute('href');
		if (href !== null && !SAFE_LINK_SCHEME.test(href.trim())) {
			node.removeAttribute('href');
		}
		node.setAttribute('target', '_blank');
		node.setAttribute('rel', 'noopener noreferrer');
	});
}

/** Sanitize an HTML fragment with the block-level Markdown allowlist. */
export function sanitizeBlockHtml(html: string): string {
	ensureSanitizeHooks();
	return DOMPurify.sanitize(html ?? '', { ALLOWED_TAGS: BLOCK_TAGS, ALLOWED_ATTR });
}

/** Sanitize an inline HTML fragment (e.g. markmap node content). */
export function sanitizeInlineHtml(html: string): string {
	ensureSanitizeHooks();
	return DOMPurify.sanitize(html ?? '', { ALLOWED_TAGS: INLINE_TAGS, ALLOWED_ATTR });
}

/** Render Markdown to sanitized HTML. GFM tables on; `breaks` mirrors chat semantics. */
export function renderMarkdownSafe(markdown: string, opts: { breaks?: boolean } = {}): string {
	const raw = marked.parse(markdown ?? '', {
		gfm: true,
		breaks: opts.breaks ?? true,
		async: false,
	}) as string;
	return sanitizeBlockHtml(raw);
}
