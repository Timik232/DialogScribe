<script lang="ts">
	interface Props {
		code: string;
		language: 'bash' | 'javascript';
	}

	let { code, language }: Props = $props();
	let copied = $state(false);

	function copyCode() {
		navigator.clipboard.writeText(code).then(() => {
			copied = true;
			setTimeout(() => copied = false, 2000);
		});
	}

	function highlightBash(src: string): string {
		return src
			.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
			.replace(/(#[^\s]*)/g, '<span class="hl-comment">$1</span>')
			.replace(/("(?:[^"\\]|\\.)*")/g, '<span class="hl-string">$1</span>')
			.replace(/('(?:[^'\\]|\\.)*')/g, '<span class="hl-string">$1</span>')
			.replace(/(\b(?:curl|echo|cat|mkdir|cd|export|source)\b)/g, '<span class="hl-keyword">$1</span>')
			.replace(/(\s(-{1,2}[\w-]+))/g, ' <span class="hl-flag">$1</span>')
			.replace(/(https?:\/\/[^\s"']+)/g, '<span class="hl-url">$1</span>');
	}

	function highlightJs(src: string): string {
		return src
			.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
			.replace(/(\/\/[^\n]*)/g, '<span class="hl-comment">$1</span>')
			.replace(/("(?:[^"\\]|\\.)*")/g, '<span class="hl-string">$1</span>')
			.replace(/('(?:[^'\\]|\\.)*')/g, '<span class="hl-string">$1</span>')
			.replace(/(`(?:[^`\\]|\\.)*`)/g, '<span class="hl-string">$1</span>')
			.replace(/(\b(?:const|let|var|function|return|await|async|new|if|else|try|catch|throw|import|from|export|default)\b)/g, '<span class="hl-keyword">$1</span>');
	}

	let highlighted = $derived(
		language === 'bash' ? highlightBash(code) : highlightJs(code)
	);
</script>

<div class="code-block">
	<div class="code-header">
		<span class="code-lang">{language === 'bash' ? 'cURL' : 'JavaScript'}</span>
		<button class="copy-btn" onclick={copyCode}>
			{#if copied}
				✓ Скопировано!
			{:else}
				Копировать
			{/if}
		</button>
	</div>
	<pre><code>{@html highlighted}</code></pre>
</div>

<style>
	.code-block {
		border-radius: var(--radius);
		overflow: hidden;
		margin: 0.75rem 0;
		border: 1px solid #2d2d3f;
	}

	.code-header {
		display: flex;
		justify-content: space-between;
		align-items: center;
		padding: 0.5rem 1rem;
		background-color: #16162a;
		border-bottom: 1px solid #2d2d3f;
	}

	.code-lang {
		color: #7c7c9a;
		font-size: 0.75rem;
		font-weight: 600;
		text-transform: uppercase;
		letter-spacing: 0.5px;
	}

	.copy-btn {
		background: none;
		border: 1px solid #3d3d5c;
		color: #9c9cb8;
		font-size: 0.75rem;
		padding: 0.25rem 0.625rem;
		border-radius: 4px;
		cursor: pointer;
		transition: color 0.15s, border-color 0.15s;
		font-family: var(--font-family);
	}

	.copy-btn:hover {
		color: #e0e0f0;
		border-color: #5c5c8a;
	}

	pre {
		background-color: #1e1e2e;
		margin: 0;
		padding: 1rem;
		overflow-x: auto;
		font-size: 0.8125rem;
		line-height: 1.6;
		tab-size: 2;
	}

	code {
		color: #cdd6f4;
		font-family: 'SF Mono', 'Fira Code', 'Cascadia Code', monospace;
		font-size: 0.8125rem;
	}

	:global(.hl-comment) { color: #6c7086; }
	:global(.hl-string) { color: #a6e3a1; }
	:global(.hl-keyword) { color: #cba6f7; }
	:global(.hl-flag) { color: #89b4fa; }
	:global(.hl-url) { color: #89dceb; text-decoration: underline; }
</style>
