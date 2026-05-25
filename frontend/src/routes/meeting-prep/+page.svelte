<script lang="ts">
	import { fetchApi } from '$lib/services/api';
	import { marked } from 'marked';
	import { Document, Packer, Paragraph, TextRun, HeadingLevel, Table, TableRow, TableCell, WidthType, BorderStyle, AlignmentType } from 'docx';
	import { saveAs } from 'file-saver';

	let models: Array<{ id: string; name: string }> = $state([]);
	let selectedModel = $state('');

	let companyData = $state('');
	let catalogData = $state('');

	let generating = $state(false);
	let errorMsg = $state('');

	let resultMarkdown = $state('');
	let resultModel = $state('');
	let resultId = $state('');
	let resultHtml = $derived(resultMarkdown ? (marked.parse(resultMarkdown, { breaks: true }) as string) : '');

	let llmAvailable = $state(true);
	let llmChecked = $state(false);

	let canGenerate = $derived(
		companyData.trim().length > 0 &&
		catalogData.trim().length > 0 &&
		llmAvailable &&
		!generating
	);

	$effect(() => {
		loadModels();
	});

	async function loadModels() {
		try {
			const data = await fetchApi<{ models: Array<{ id: string; name: string }> }>('GET', '/api/models');
			models = data.models ?? [];
			if (models.length > 0 && !selectedModel) {
				selectedModel = models[0].id;
			}
			llmAvailable = true;
		} catch (e: any) {
			if (e?.message?.includes('503')) {
				llmAvailable = false;
			}
		} finally {
			llmChecked = true;
		}
	}

	async function generate() {
		errorMsg = '';
		resultMarkdown = '';
		resultModel = '';
		resultId = '';
		generating = true;

		try {
			const result = await fetchApi<{ id: string; markdown: string; model: string }>('POST', '/api/meeting-prep', {
				body: JSON.stringify({
					company_data: companyData,
					catalog_data: catalogData,
					model: selectedModel || undefined,
				}),
				headers: { 'Content-Type': 'application/json' },
			});
			resultId = result.id;
			resultMarkdown = result.markdown;
			resultModel = result.model;
		} catch (e: any) {
			errorMsg = e?.message ?? 'Ошибка генерации плана';
		} finally {
			generating = false;
		}
	}

	function downloadBlob(blob: Blob, filename: string): void {
		const url = URL.createObjectURL(blob);
		const a = document.createElement('a');
		a.href = url;
		a.download = filename;
		a.click();
		URL.revokeObjectURL(url);
	}

	function exportTxt(): void {
		if (!resultMarkdown) return;
		const blob = new Blob([resultMarkdown], { type: 'text/plain; charset=utf-8' });
		downloadBlob(blob, 'meeting-prep-plan.txt');
	}

	function exportHtml(): void {
		if (!resultMarkdown) return;
		const html = `<!DOCTYPE html><html><head><meta charset="utf-8"><title>Подготовка к встрече</title><style>body{font-family:Arial,sans-serif;max-width:800px;margin:2em auto;padding:0 1em;line-height:1.7;color:#222}h1{font-size:1.4em}h2{font-size:1.2em;margin-top:1.5em}h3{font-size:1.05em}table{border-collapse:collapse;width:100%}th,td{border:1px solid #ccc;padding:6px 10px;text-align:left}th{background:#f5f5f5}</style></head><body>${resultHtml}</body></html>`;
		const blob = new Blob([html], { type: 'text/html; charset=utf-8' });
		downloadBlob(blob, 'meeting-prep-plan.html');
	}

	async function exportDocx(): Promise<void> {
		if (!resultMarkdown) return;

		const lines = resultMarkdown.split('\n');
		const children: (Paragraph | Table)[] = [];
		let i = 0;

		while (i < lines.length) {
			const line = lines[i];

			if (line.startsWith('|')) {
				const tableLines: string[] = [];
				while (i < lines.length && lines[i].startsWith('|')) {
					tableLines.push(lines[i]);
					i++;
				}
				const rows = tableLines
					.filter((l) => !l.match(/^\|[\s\-:|]+\|$/))
					.map((l) => l.split('|').slice(1, -1).map((c) => c.trim()));
				if (rows.length > 0) {
					const tableRows = rows.map(
						(cells, idx) =>
							new TableRow({
								tableHeader: idx === 0,
								children: cells.map(
									(cell) =>
										new TableCell({
											width: { size: Math.floor(100 / cells.length), type: WidthType.PERCENTAGE },
											children: [
												new Paragraph({
													children: [new TextRun({ text: cell, bold: idx === 0, size: 22, font: 'Arial' })],
													spacing: { after: 40 },
												}),
											],
										})
								),
							})
					);
					children.push(
						new Table({
							rows: tableRows,
							width: { size: 100, type: WidthType.PERCENTAGE },
						})
					);
				}
				continue;
			}

			if (line.startsWith('### ')) {
				children.push(new Paragraph({ text: line.slice(4), heading: HeadingLevel.HEADING_3, spacing: { before: 200, after: 100 } }));
			} else if (line.startsWith('## ')) {
				children.push(new Paragraph({ text: line.slice(3), heading: HeadingLevel.HEADING_2, spacing: { before: 240, after: 120 } }));
			} else if (line.startsWith('# ')) {
				children.push(new Paragraph({ text: line.slice(2), heading: HeadingLevel.HEADING_1, spacing: { before: 300, after: 160 } }));
			} else if (line.startsWith('- ') || line.startsWith('* ')) {
				const text = line.slice(2).replace(/\*\*(.+?)\*\*/g, '$1').replace(/\*(.+?)\*/g, '$1');
				children.push(new Paragraph({ children: [new TextRun({ text: `• ${text}`, size: 22, font: 'Arial' })], spacing: { after: 40 }, indent: { left: 360 } }));
			} else if (/^\d+\.\s/.test(line)) {
				const text = line.replace(/^\d+\.\s/, '').replace(/\*\*(.+?)\*\*/g, '$1').replace(/\*(.+?)\*/g, '$1');
				children.push(new Paragraph({ children: [new TextRun({ text, size: 22, font: 'Arial' })], spacing: { after: 40 }, indent: { left: 360 } }));
			} else if (line.trim()) {
				const parts = line.split(/(\*\*.+?\*\*|\*.+?\*)/g);
				const runs = parts
					.filter((p) => p)
					.map((part) => {
						if (part.startsWith('**') && part.endsWith('**')) return new TextRun({ text: part.slice(2, -2), bold: true, size: 22, font: 'Arial' });
						if (part.startsWith('*') && part.endsWith('*')) return new TextRun({ text: part.slice(1, -1), italics: true, size: 22, font: 'Arial' });
						return new TextRun({ text: part, size: 22, font: 'Arial' });
					});
				children.push(new Paragraph({ children: runs, spacing: { after: 80 } }));
			}
			i++;
		}

		const doc = new Document({
			sections: [{ children }],
		});
		const blob = await Packer.toBlob(doc);
		saveAs(blob, 'meeting-prep-plan.docx');
	}
</script>

<div class="meeting-prep-page">
	{#if !llmChecked}
		<div class="loading-screen">
			<div class="spinner"></div>
			<p>Загрузка…</p>
		</div>
	{:else if !llmAvailable}
		<div class="container">
			<div class="degradation-banner">
				<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
					<path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/>
					<line x1="12" y1="9" x2="12" y2="13"/>
					<line x1="12" y1="17" x2="12.01" y2="17"/>
				</svg>
				<span>LLM не настроен. Функция подготовки к встрече недоступна.</span>
			</div>
		</div>
	{:else}
		<div class="container">
			<div class="page-header">
				<h1>Подготовка к встрече</h1>
				<p class="page-desc">Генерация плана подготовки к встрече с компанией на основе данных о компании и каталога продуктов</p>
			</div>

			{#if errorMsg}
				<div class="error-banner">{errorMsg}</div>
			{/if}

			<div class="input-grid">
				<div class="card input-card">
					<h3>Данные о компании</h3>
					<p class="input-hint">Вставьте выписку, CRM-данные, результаты веб-поиска о компании</p>
					<textarea
						class="input textarea"
						placeholder="ИНН, ОГРН, адрес, контакты, новости, сайт, вакансии..."
						bind:value={companyData}
						rows="14"
						disabled={generating}
					></textarea>
					<span class="char-count">{companyData.length.toLocaleString()} символов</span>
				</div>

				<div class="card input-card">
					<h3>Каталог продуктов</h3>
					<p class="input-hint">Вставьте описание продуктов и услуг вашей компании</p>
					<textarea
						class="input textarea"
						placeholder="Список продуктов, услуг, ценовые категории..."
						bind:value={catalogData}
						rows="14"
						disabled={generating}
					></textarea>
					<span class="char-count">{catalogData.length.toLocaleString()} символов</span>
				</div>
			</div>

			<div class="settings-bar card">
				<div class="setting-group">
					<label for="model-select">Модель</label>
					<select id="model-select" class="input" bind:value={selectedModel} disabled={generating}>
						{#each models as m}
							<option value={m.id}>{m.name}</option>
						{/each}
					</select>
				</div>

				<div class="action-group">
					<button class="btn btn-primary generate-btn" onclick={generate} disabled={!canGenerate}>
						{#if generating}
							<span class="btn-spinner"></span>
							Генерация…
						{:else}
							<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" class="btn-icon"><path stroke-linecap="round" stroke-linejoin="round" d="M3.75 13.5l10.5-11.25L12 10.5h8.25L9.75 21.75 12 13.5H3.75z"/></svg>
							Сгенерировать план
						{/if}
					</button>
				</div>
			</div>

			{#if resultMarkdown}
				<div class="card result-card">
					<div class="result-header">
						<h3>План подготовки</h3>
						<div class="result-meta">
							<span class="badge badge-model">{resultModel}</span>
							<div class="export-buttons">
								<button class="btn btn-secondary btn-sm" onclick={exportTxt}>TXT</button>
								<button class="btn btn-secondary btn-sm" onclick={exportDocx}>DOCX</button>
								<button class="btn btn-secondary btn-sm" onclick={exportHtml}>HTML</button>
							</div>
						</div>
					</div>
					<div class="result-content">
						{@html resultHtml}
					</div>
				</div>
			{/if}
		</div>
	{/if}
</div>

<style>
	.meeting-prep-page {
		padding: 1.5rem 0;
	}

	.loading-screen {
		display: flex;
		flex-direction: column;
		align-items: center;
		justify-content: center;
		min-height: calc(100vh - var(--header-height));
		gap: 1rem;
		color: var(--color-muted);
	}

	.spinner {
		width: 32px;
		height: 32px;
		border: 3px solid var(--color-border);
		border-top-color: var(--color-cta);
		border-radius: 50%;
		animation: spin 0.6s linear infinite;
	}

	@keyframes spin {
		to { transform: rotate(360deg); }
	}

	.container {
		max-width: 1000px;
		margin: 0 auto;
		padding: 0 1rem;
		display: flex;
		flex-direction: column;
		gap: 1rem;
	}

	.degradation-banner {
		display: flex;
		align-items: center;
		gap: 0.75rem;
		margin-top: 3rem;
		padding: 1rem 1.25rem;
		background: var(--color-error-bg);
		border: 1px solid var(--color-error-border);
		border-radius: var(--radius);
		color: var(--color-error-text);
		font-size: 0.9375rem;
		font-weight: 500;
	}

	.page-header {
		margin-bottom: 0.25rem;
	}

	.page-desc {
		color: var(--color-muted);
		font-size: 0.875rem;
		margin-top: 0.25rem;
	}

	.input-grid {
		display: grid;
		grid-template-columns: 1fr 1fr;
		gap: 1rem;
	}

	.input-card h3 {
		margin: 0 0 0.25rem;
		font-size: 0.9375rem;
	}

	.input-hint {
		margin: 0 0 0.5rem;
		font-size: 0.8125rem;
		color: var(--color-muted);
	}

	.textarea {
		width: 100%;
		resize: vertical;
		min-height: 200px;
		font-family: inherit;
		font-size: 0.8125rem;
		line-height: 1.6;
	}

	.char-count {
		display: block;
		text-align: right;
		font-size: 0.75rem;
		color: var(--color-muted);
		margin-top: 0.25rem;
	}

	.settings-bar {
		display: flex;
		gap: 1rem;
		flex-wrap: wrap;
		align-items: flex-end;
	}

	.setting-group {
		display: flex;
		flex-direction: column;
		gap: 0.375rem;
		flex: 1;
		min-width: 200px;
	}

	.setting-group label {
		font-size: 0.8125rem;
		font-weight: 500;
		color: var(--color-muted);
	}

	.setting-group select {
		cursor: pointer;
	}

	.action-group {
		display: flex;
		align-items: flex-end;
		padding-top: 1.375rem;
	}

	.generate-btn {
		min-width: 200px;
		height: 42px;
		font-size: 0.9375rem;
		background-color: var(--color-cta);
		border-color: var(--color-cta);
		white-space: nowrap;
	}

	.generate-btn:hover:not(:disabled) {
		filter: brightness(1.1);
	}

	.btn-spinner {
		width: 14px;
		height: 14px;
		border: 2px solid rgba(255, 255, 255, 0.3);
		border-top-color: #fff;
		border-radius: 50%;
		animation: spin 0.6s linear infinite;
		display: inline-block;
		margin-right: 0.5rem;
	}

	.btn-icon {
		width: 18px;
		height: 18px;
		display: inline-block;
		vertical-align: middle;
		margin-right: 6px;
	}

	.error-banner {
		padding: 0.625rem 1rem;
		background: var(--color-error-bg);
		color: var(--color-error-text);
		border: 1px solid var(--color-error-border);
		border-radius: var(--radius);
		font-size: 0.875rem;
		line-height: 1.5;
	}

	.result-card {
		animation: fade-in 0.3s ease;
	}

	@keyframes fade-in {
		from { opacity: 0; transform: translateY(8px); }
		to { opacity: 1; transform: translateY(0); }
	}

	.result-header {
		display: flex;
		align-items: center;
		justify-content: space-between;
		margin-bottom: 1rem;
	}

	.result-header h3 {
		margin: 0;
		font-size: 0.9375rem;
	}

	.result-meta {
		display: flex;
		gap: 0.5rem;
		align-items: center;
	}

	.export-buttons {
		display: flex;
		gap: 0.375rem;
	}

	.badge {
		display: inline-block;
		padding: 0.125rem 0.5rem;
		border-radius: 999px;
		font-size: 0.6875rem;
		font-weight: 500;
		white-space: nowrap;
	}

	.badge-model {
		background: color-mix(in srgb, var(--color-cta) 12%, var(--color-bg));
		color: var(--color-cta);
		border: 1px solid color-mix(in srgb, var(--color-cta) 30%, transparent);
	}

	.result-content {
		font-size: 0.875rem;
		line-height: 1.7;
		color: var(--color-text);
	}

	.result-content :global(h1),
	.result-content :global(h2),
	.result-content :global(h3) {
		margin-top: 1rem;
		margin-bottom: 0.5rem;
	}

	.result-content :global(h1) {
		font-size: 1.25rem;
	}

	.result-content :global(h2) {
		font-size: 1.0625rem;
	}

	.result-content :global(h3) {
		font-size: 0.9375rem;
	}

	.result-content :global(ul),
	.result-content :global(ol) {
		margin-left: 1.25rem;
		margin-bottom: 0.5rem;
	}

	.result-content :global(p) {
		margin-bottom: 0.5rem;
	}

	.result-content :global(table) {
		width: 100%;
		border-collapse: collapse;
		margin: 0.75rem 0;
		font-size: 0.8125rem;
	}

	.result-content :global(th),
	.result-content :global(td) {
		border: 1px solid var(--color-border);
		padding: 0.375rem 0.625rem;
		text-align: left;
	}

	.result-content :global(th) {
		background: var(--color-bg);
		font-weight: 600;
	}

	@media (max-width: 768px) {
		.input-grid {
			grid-template-columns: 1fr;
		}

		.settings-bar {
			flex-direction: column;
		}

		.action-group {
			padding-top: 0;
		}
	}
</style>
