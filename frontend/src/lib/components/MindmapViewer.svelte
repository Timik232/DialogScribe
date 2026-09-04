<script lang="ts">
	import { onMount } from 'svelte';
	import { Transformer } from 'markmap-lib';
	import { Markmap } from 'markmap-view';

	let { markdown = '', theme = 'light' }: { markdown: string; theme?: 'light' | 'dark' } = $props();

	let svgEl: SVGSVGElement | undefined = $state();
	let mm: Markmap | null = $state(null);
	let transformer = $state(new Transformer());

	// ── Markmap rendering ──

	function buildMarkmapOptions() {
		return {
			color: theme === 'dark'
				? (() => {
						const palette = ['#4FC3F7', '#81C784', '#FFB74D', '#E57373', '#BA68C8', '#4DD0E1'];
						return (node: any) => palette[node.state?.depth % palette.length] ?? '#4FC3F7';
					})()
				: undefined,
		};
	}

	function renderData() {
		if (!mm || !markdown) return;
		const { root } = transformer.transform(markdown);
		mm.setData(root);
		mm.fit();
	}

	onMount(() => {
		if (!svgEl) return;
		mm = Markmap.create(svgEl, { duration: 300 });
		renderData();

		return () => {
			mm?.destroy();
			mm = null;
		};
	});

	$effect(() => {
		// Track markdown changes
		markdown;
		if (mm) renderData();
	});

	$effect(() => {
		// Track theme changes
		theme;
		if (mm) {
			mm.setOptions(buildMarkmapOptions());
			mm.setData(mm.getData());
			mm.fit();
		}
	});

	// ── Exports ──

	function downloadBlob(blob: Blob, filename: string) {
		const url = URL.createObjectURL(blob);
		const a = document.createElement('a');
		a.href = url;
		a.download = filename;
		a.click();
		URL.revokeObjectURL(url);
	}

	function exportPNG() {
		const svg = svgEl;
		if (!svg) return;

		const serializer = new XMLSerializer();
		const svgStr = serializer.serializeToString(svg);
		const svgBlob = new Blob([svgStr], { type: 'image/svg+xml;charset=utf-8' });
		const url = URL.createObjectURL(svgBlob);

		const img = new Image();
		img.onload = () => {
			const canvas = document.createElement('canvas');
			const scale = 2;
			canvas.width = img.naturalWidth * scale;
			canvas.height = img.naturalHeight * scale;
			const ctx = canvas.getContext('2d');
			if (!ctx) return;
			ctx.scale(scale, scale);
			ctx.drawImage(img, 0, 0);
			canvas.toBlob((blob) => {
				if (blob) downloadBlob(blob, 'mindmap.png');
			}, 'image/png');
			URL.revokeObjectURL(url);
		};
		img.src = url;
	}

	function exportSVG() {
		const svg = svgEl;
		if (!svg) return;
		const serializer = new XMLSerializer();
		const svgStr = serializer.serializeToString(svg);
		const blob = new Blob([svgStr], { type: 'image/svg+xml;charset=utf-8' });
		downloadBlob(blob, 'mindmap.svg');
	}

	function exportMarkdown() {
		if (!markdown) return;
		const blob = new Blob([markdown], { type: 'text/markdown;charset=utf-8' });
		downloadBlob(blob, 'mindmap.md');
	}
</script>

<div class="mindmap-viewer">
	{#if markdown}
		<div class="mindmap-toolbar">
			<button class="btn btn-secondary btn-sm" onclick={exportPNG}>
				<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
					<rect x="3" y="3" width="18" height="18" rx="2" ry="2"/>
					<circle cx="8.5" cy="8.5" r="1.5"/>
					<polyline points="21 15 16 10 5 21"/>
				</svg>
				Скачать PNG
			</button>
			<button class="btn btn-secondary btn-sm" onclick={exportSVG}>
				<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
					<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
					<polyline points="14 2 14 8 20 8"/>
				</svg>
				Скачать SVG
			</button>
			<button class="btn btn-secondary btn-sm" onclick={exportMarkdown}>
				<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
					<path d="M4 4h16v16H4z" fill="none"/>
					<path d="M7 15V9l2.5 3L12 9v6"/>
					<path d="M17 12l-2-2v4"/>
				</svg>
				Скачать Markdown
			</button>
		</div>
		<!-- svelte-ignore binding_property_non_reactive -->
		<svg bind:this={svgEl} class="mindmap-svg"></svg>
	{:else}
		<div class="mindmap-empty">
			<svg width="36" height="36" viewBox="0 0 24 24" fill="none" stroke="var(--color-muted)" stroke-width="1.5">
				<circle cx="12" cy="12" r="2"/>
				<path d="M12 2v4M12 18v4M4.93 4.93l2.83 2.83M16.24 16.24l2.83 2.83M2 12h4M18 12h4M4.93 19.07l2.83-2.83M16.24 7.76l2.83-2.83"/>
			</svg>
			<p>Нет данных для отображения</p>
		</div>
	{/if}
</div>

<style>
	.mindmap-viewer {
		background: var(--color-card);
		border: 1px solid var(--color-border);
		border-radius: var(--radius);
		overflow: hidden;
	}

	.mindmap-toolbar {
		display: flex;
		gap: 0.375rem;
		padding: 0.625rem 1rem;
		border-bottom: 1px solid var(--color-border);
		background: var(--color-card);
		flex-wrap: wrap;
	}

	.mindmap-toolbar .btn-sm {
		padding: 0.25rem 0.625rem;
		font-size: 0.75rem;
	}

	.mindmap-svg {
		width: 100%;
		min-height: 400px;
		display: block;
	}

	.mindmap-empty {
		display: flex;
		flex-direction: column;
		align-items: center;
		justify-content: center;
		min-height: 400px;
		gap: 0.75rem;
		color: var(--color-muted);
		font-size: 0.9375rem;
	}
</style>
