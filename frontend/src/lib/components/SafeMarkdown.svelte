<script lang="ts">
	import { renderMarkdownSafe } from '$lib/services/safeHtml';

	let {
		markdown = '',
		breaks = true,
		class: cls = undefined
	}: { markdown?: string; breaks?: boolean; class?: string } = $props();

	// SECURITY: the only sanctioned {@html} sink in the app — input is
	// Markdown rendered by `marked` and sanitized by DOMPurify (see
	// $lib/services/safeHtml). Do not render untrusted strings via raw
	// {@html} anywhere else.
	let html = $derived(markdown ? renderMarkdownSafe(markdown, { breaks }) : '');
</script>

{#if html}
	<div class={cls}>{@html html}</div>
{/if}
