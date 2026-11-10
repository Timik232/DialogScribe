<script lang="ts">
	import { onMount } from 'svelte';
	import { Transformer } from 'markmap-lib';
	import { Markmap } from 'markmap-view';
	import { sanitizeInlineHtml } from '$lib/services/safeHtml';

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

	// markmap-view renders node.content via innerHTML, so every node text must
	// be sanitized before it reaches setData().
	type MarkmapNode = { content?: string; children?: MarkmapNode[] };

	function sanitizeTree(node: MarkmapNode): void {
		if (node.content) node.content = sanitizeInlineHtml(node.content);
		for (const child of node.children ?? []) sanitizeTree(child);
	}

	function renderData() {
		if (!mm || !markdown) return;
		const { root } = transformer.transform(markdown);
		sanitizeTree(root as MarkmapNode);
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

	// markmap renders node labels as HTML inside <foreignObject>, which taints the
	// rasterization canvas in Chromium (toBlob → null, the PNG button silently does
	// nothing) and is not rendered by many SVG consumers. Before any export, build
	// a portable copy of the live mindmap with native SVG <text> labels, explicit
	// width/height/viewBox and a background rect.
	type PortableSvg = { svgString: string; width: number; height: number };

	function buildPortableSvg(): PortableSvg | null {
		const svg = svgEl;
		if (!svg) return null;

		const ctm = svg.getScreenCTM();
		if (!ctm) return null;
		const inverse = ctm.inverse();
		const toUser = (x: number, y: number) => {
			const pt = svg.createSVGPoint();
			pt.x = x;
			pt.y = y;
			return pt.matrixTransform(inverse);
		};

		// Capture the browser's own line wrapping: walk every word of every label
		// as a DOM Range, group ranges by their rendered vertical position.
		// Grouping is per-label: two labels at the same height must stay separate.
		type LabelLine = { words: string[]; left: number; bottom: number; height: number };
		type Label = { lines: LabelLine[]; font: string; weight: string; color: string };
		const labels: Label[] = [];
		const range = document.createRange();
		for (const fo of Array.from(svg.querySelectorAll('foreignObject'))) {
			const labelDiv = (fo.querySelector('div > div') ?? fo.querySelector('div')) as HTMLElement | null;
			if (!labelDiv) continue;
			const divStyle = getComputedStyle(labelDiv);
			const linesByTop = new Map<number, LabelLine>();
			const walker = document.createTreeWalker(labelDiv, NodeFilter.SHOW_TEXT);
			let node: Node | null;
			while ((node = walker.nextNode())) {
				const text = node.nodeValue ?? '';
				const re = /\S+/g;
				let match: RegExpExecArray | null;
				while ((match = re.exec(text))) {
					range.setStart(node, match.index);
					range.setEnd(node, match.index + match[0].length);
					const rect = range.getBoundingClientRect();
					if (rect.width === 0 && rect.height === 0) continue;
					const key = Math.round(rect.top);
					let line = linesByTop.get(key);
					if (!line) {
						line = { words: [], left: rect.left, bottom: rect.bottom, height: rect.height };
						linesByTop.set(key, line);
					}
					line.words.push(match[0]);
					line.left = Math.min(line.left, rect.left);
					line.bottom = Math.max(line.bottom, rect.bottom);
					line.height = Math.max(line.height, rect.height);
				}
			}
			if (linesByTop.size > 0) {
				labels.push({
					lines: Array.from(linesByTop.values()),
					font: divStyle.fontFamily || 'sans-serif',
					weight: divStyle.fontWeight || '400',
					color: divStyle.color || '#1e293b'
				});
			}
		}
		range.detach();

		// Clone the live SVG, drop the foreignObject labels and append <text>
		// elements instead — placed at the SVG root in final user coordinates
		// (already includes every zoom/translate transform).
		const clone = svg.cloneNode(true) as SVGSVGElement;
		for (const fo of Array.from(clone.querySelectorAll('foreignObject'))) fo.remove();

		const ns = 'http://www.w3.org/2000/svg';
		let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
		for (const label of labels) {
			for (const line of label.lines) {
				const text = line.words.join(' ');
				if (!text) continue;
				const p1 = toUser(line.left, line.bottom);
				const p2 = toUser(line.left + 1, line.bottom - line.height);
				const fontPx = Math.hypot(p2.x - p1.x, p2.y - p1.y); // px→user scale at this point
				const baselineY = line.bottom - line.height * 0.22; // approx. baseline inside the line box
				const anchor = toUser(line.left, baselineY);
				const textEl = document.createElementNS(ns, 'text');
				textEl.setAttribute('x', String(anchor.x));
				textEl.setAttribute('y', String(anchor.y));
				// Colors must survive outside the app: no CSS vars, no inherit.
				textEl.setAttribute('fill', theme === 'dark' ? '#e2e8f0' : label.color);
				textEl.setAttribute('font-size', `${Math.max(1, fontPx).toFixed(2)}px`);
				textEl.setAttribute('font-family', label.font);
				textEl.setAttribute('font-weight', label.weight);
				textEl.textContent = text;
				clone.appendChild(textEl);
				minX = Math.min(minX, anchor.x);
				maxX = Math.max(maxX, anchor.x + text.length * fontPx * 0.7);
				minY = Math.min(minY, anchor.y - fontPx);
				maxY = Math.max(maxY, anchor.y);
			}
		}

		// Content bounds: getBBox covers paths/curves; union with text estimates.
		let bbox: DOMRect;
		try {
			bbox = svg.getBBox();
		} catch {
			bbox = new DOMRect(0, 0, 0, 0);
		}
		minX = Math.min(minX, bbox.x);
		minY = Math.min(minY, bbox.y);
		maxX = Math.max(maxX, bbox.x + bbox.width);
		maxY = Math.max(maxY, bbox.y + bbox.height);
		if (!Number.isFinite(minX) || !Number.isFinite(minY)) return null;

		const pad = 60;
		const x = Math.floor(minX - pad);
		const y = Math.floor(minY - pad);
		const width = Math.ceil(maxX - minX + pad * 2);
		const height = Math.ceil(maxY - minY + pad * 2);
		if (width <= 0 || height <= 0) return null;

		clone.setAttribute('viewBox', `${x} ${y} ${width} ${height}`);
		clone.setAttribute('width', String(width));
		clone.setAttribute('height', String(height));
		clone.removeAttribute('class');
		clone.removeAttribute('style');

		// Background first child (var(--color-card) is not resolvable inside a file).
		const bg = document.createElementNS(ns, 'rect');
		bg.setAttribute('x', String(x));
		bg.setAttribute('y', String(y));
		bg.setAttribute('width', String(width));
		bg.setAttribute('height', String(height));
		bg.setAttribute('fill', theme === 'dark' ? '#0f172a' : '#ffffff');
		clone.insertBefore(bg, clone.firstChild);

		const svgString = new XMLSerializer().serializeToString(clone);
		return { svgString, width, height };
	}

	function exportPNG() {
		const portable = buildPortableSvg();
		if (!portable) return;

		const { svgString, width, height } = portable;
		const url = URL.createObjectURL(new Blob([svgString], { type: 'image/svg+xml;charset=utf-8' }));
		const img = new Image();
		img.onload = () => {
			const scale = Math.min(2, 8192 / Math.max(width, height));
			const canvas = document.createElement('canvas');
			canvas.width = Math.round(width * scale);
			canvas.height = Math.round(height * scale);
			const ctx = canvas.getContext('2d');
			if (!ctx) {
				URL.revokeObjectURL(url);
				return;
			}
			ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
			URL.revokeObjectURL(url);
			canvas.toBlob((blob) => {
				if (blob) downloadBlob(blob, 'mindmap.png');
			}, 'image/png');
		};
		img.onerror = () => URL.revokeObjectURL(url);
		img.src = url;
	}

	function exportSVG() {
		const portable = buildPortableSvg();
		if (!portable) return;
		const blob = new Blob([portable.svgString], { type: 'image/svg+xml;charset=utf-8' });
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
