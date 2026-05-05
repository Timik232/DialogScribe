<script lang="ts">
	import { fetchApi } from '$lib/services/api.ts';
	import { authStore } from '$lib/stores/auth';
	import { onMount } from 'svelte';

	interface Template {
		slug: string;
		name: string;
		emoji: string;
		system_prompt: string;
		user_prompt_template: string;
		is_custom: boolean;
	}

	let templates: Template[] = $state([]);
	let loading = $state(true);
	let error = $state('');

	// Form state
	let showForm = $state(false);
	let editingSlug: string | null = $state(null);
	let formName = $state('');
	let formEmoji = $state('');
	let formSystemPrompt = $state('');
	let formUserPrompt = $state('');
	let formError = $state('');
	let formSaving = $state(false);

	// Delete confirmation
	let confirmDelete: { slug: string; name: string } | null = $state(null);
	let deleting = $state(false);

	let viewingTemplate: Template | null = $state(null);

	let importResult: string | null = $state(null);
	let fileInputEl: HTMLInputElement | null = $state(null);

	async function loadTemplates() {
		loading = true;
		error = '';
		try {
			templates = await fetchApi<Template[]>('GET', '/api/templates');
		} catch (e: any) {
			error = e.message || 'Не удалось загрузить шаблоны';
		} finally {
			loading = false;
		}
	}

	function openCreate() {
		editingSlug = null;
		formName = '';
		formEmoji = '';
		formSystemPrompt = '';
		formUserPrompt = '';
		formError = '';
		showForm = true;
	}

	function openEdit(t: Template) {
		editingSlug = t.slug;
		formName = t.name;
		formEmoji = t.emoji;
		formSystemPrompt = t.system_prompt;
		formUserPrompt = t.user_prompt_template;
		formError = '';
		showForm = true;
	}

	function cancelForm() {
		showForm = false;
		editingSlug = null;
		formError = '';
	}

	async function saveTemplate() {
		if (!formName.trim() || !formSystemPrompt.trim()) {
			formError = 'Название и системный промпт обязательны';
			return;
		}
		formSaving = true;
		formError = '';
		try {
			const body = {
				name: formName.trim(),
				emoji: formEmoji.trim(),
				system_prompt: formSystemPrompt.trim(),
				user_prompt_template: formUserPrompt.trim()
			};
			if (editingSlug) {
				await fetchApi('PUT', `/api/templates/${editingSlug}`, {
					headers: { 'Content-Type': 'application/json' },
					body: JSON.stringify(body)
				});
			} else {
				await fetchApi('POST', '/api/templates', {
					headers: { 'Content-Type': 'application/json' },
					body: JSON.stringify(body)
				});
			}
			showForm = false;
			editingSlug = null;
			await loadTemplates();
		} catch (e: any) {
			formError = e.message || 'Ошибка сохранения';
		} finally {
			formSaving = false;
		}
	}

	function askDelete(t: Template) {
		confirmDelete = { slug: t.slug, name: t.name };
	}

	function cancelDelete() {
		confirmDelete = null;
	}

	async function doDelete() {
		if (!confirmDelete) return;
		deleting = true;
		try {
			await fetchApi('DELETE', `/api/templates/${confirmDelete.slug}`);
			confirmDelete = null;
			await loadTemplates();
		} catch (e: any) {
			error = e.message || 'Ошибка удаления';
			confirmDelete = null;
		} finally {
			deleting = false;
		}
	}

	function truncate(s: string, lines: number = 2): string {
		if (!s) return '';
		const parts = s.split('\n');
		if (parts.length <= lines) return s;
		return parts.slice(0, lines).join('\n') + '…';
	}

	async function exportTemplates(): Promise<void> {
		try {
			const data = await fetchApi<{ version: number; templates: any[] }>('POST', '/api/templates/export');
			const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
			const url = URL.createObjectURL(blob);
			const a = document.createElement('a');
			a.href = url;
			a.download = 'dialogscribe-templates.json';
			document.body.appendChild(a);
			a.click();
			document.body.removeChild(a);
			URL.revokeObjectURL(url);
		} catch (e: any) {
			error = e?.message || 'Ошибка экспорта';
		}
	}

	async function exportSingle(slug: string): Promise<void> {
		try {
			let token = '';
			authStore.subscribe((s) => (token = s.accessToken))();

			const headers: Record<string, string> = {};
			if (token) headers['Authorization'] = `Bearer ${token}`;

			let res = await fetch(`/api/templates/export/${slug}`, {
				method: 'POST',
				headers,
				credentials: 'include'
			});

			if (res.status === 401 && token) {
				const newToken = await authStore.refresh();
				if (newToken) {
					headers['Authorization'] = `Bearer ${newToken}`;
					res = await fetch(`/api/templates/export/${slug}`, {
						method: 'POST',
						headers,
						credentials: 'include'
					});
				}
			}

			if (!res.ok) {
				const text = await res.text().catch(() => '');
				throw new Error(`Ошибка экспорта: ${res.status} ${text}`);
			}

			const blob = await res.blob();
			const url = URL.createObjectURL(blob);
			const a = document.createElement('a');
			a.href = url;
			a.download = `dialogscribe-template-${slug}.json`;
			document.body.appendChild(a);
			a.click();
			document.body.removeChild(a);
			URL.revokeObjectURL(url);
		} catch (e: any) {
			error = e?.message || 'Ошибка экспорта шаблона';
		}
	}

	async function handleImportFile(event: Event): Promise<void> {
		const input = event.target as HTMLInputElement;
		const file = input.files?.[0];
		if (!file) return;

		try {
			const text = await file.text();
			const parsed = JSON.parse(text);
			const report = await fetchApi<{ imported: number; skipped: number; errors: string[] }>('POST', '/api/templates/import', {
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({ templates_data: parsed })
			});
			importResult = `Импортировано: ${report.imported}, пропущено: ${report.skipped}`;
			setTimeout(() => { importResult = null; }, 5000);
			await loadTemplates();
		} catch (e: any) {
			if (e instanceof SyntaxError) {
				error = 'Невалидный JSON файл';
			} else {
				error = e?.message || 'Ошибка импорта';
			}
		}
		input.value = '';
	}

	onMount(loadTemplates);
</script>

<div class="page">
	<div class="page-header">
		<h1>Шаблоны</h1>
		<div class="header-actions">
			<button class="btn btn-secondary" onclick={exportTemplates}>Экспорт</button>
			<button class="btn btn-secondary" onclick={() => fileInputEl?.click()}>Импорт</button>
			<input bind:this={fileInputEl} type="file" accept=".json" style="display:none" onchange={handleImportFile} />
			<button class="btn btn-primary" onclick={openCreate}>Создать шаблон</button>
		</div>
	</div>

	{#if error}
		<div class="error-banner">{error}</div>
	{/if}

	{#if importResult}
		<div class="success-banner">{importResult}</div>
	{/if}

	{#if loading}
		<div class="empty">Загрузка…</div>
	{:else if templates.length === 0}
		<div class="empty">Шаблоны не найдены</div>
	{:else}
		<div class="grid">
			{#each templates as t (t.slug)}
				<div class="card template-card" onclick={() => viewingTemplate = t}>
					<div class="card-head">
						<span class="card-title">
							{#if t.emoji}<span class="card-emoji">{t.emoji}</span>{/if}
							{t.name}
						</span>
						<div class="card-badges">
							{#if !t.is_custom}
								<span class="badge badge-system">Системный</span>
							{:else}
								<span class="badge badge-custom">Пользовательский</span>
							{/if}
						</div>
					</div>

					<div class="card-body">
						<div class="prompt-preview">
							<span class="prompt-label">Системный промпт</span>
							<p>{truncate(t.system_prompt)}</p>
						</div>
						{#if t.user_prompt_template}
							<div class="prompt-preview">
								<span class="prompt-label">Пользовательский промпт</span>
								<p>{truncate(t.user_prompt_template)}</p>
							</div>
						{/if}
					</div>

					{#if t.is_custom}
						<div class="card-actions">
							<button class="btn-icon" title="Экспорт" onclick={(e) => { e.stopPropagation(); exportSingle(t.slug); }}>
								<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" x2="12" y1="15" y2="3"/></svg>
							</button>
							<button class="btn-icon" title="Редактировать" onclick={(e) => { e.stopPropagation(); openEdit(t); }}>
								<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17 3a2.85 2.85 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z"/><path d="m15 5 4 4"/></svg>
							</button>
							<button class="btn-icon btn-icon-danger" title="Удалить" onclick={(e) => { e.stopPropagation(); askDelete(t); }}>
								<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18"/><path d="M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6"/><path d="M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2"/><line x1="10" x2="10" y1="11" y2="17"/><line x1="14" x2="14" y1="11" y2="17"/></svg>
							</button>
						</div>
					{/if}
				</div>
			{/each}
		</div>
	{/if}

	<!-- Create / Edit form overlay -->
	{#if showForm}
		<div class="overlay" role="dialog" aria-modal="true">
			<div class="form-card">
				<h2>{editingSlug ? 'Редактировать шаблон' : 'Новый шаблон'}</h2>

				{#if formError}
					<div class="error-banner">{formError}</div>
				{/if}

				<label class="field">
					<span class="field-label">Название *</span>
					<input class="input" type="text" bind:value={formName} placeholder="Название шаблона" />
				</label>

				<label class="field">
					<span class="field-label">Эмодзи</span>
					<input class="input input-emoji" type="text" bind:value={formEmoji} placeholder="📝" maxlength="2" />
				</label>

				<label class="field">
					<span class="field-label">Системный промпт *</span>
					<textarea class="input textarea" bind:value={formSystemPrompt} rows="5" placeholder="Вы — помощник…"></textarea>
				</label>

				<label class="field">
					<span class="field-label">Пользовательский промпт</span>
					<textarea class="input textarea" bind:value={formUserPrompt} rows="3" placeholder={'Перескажите текст: {text}'}></textarea>
					<span class="field-hint">Используйте <code>{'{text}'}</code> для вставки текста</span>
				</label>

				<div class="form-actions">
					<button class="btn btn-secondary" onclick={cancelForm} disabled={formSaving}>Отмена</button>
					<button class="btn btn-primary" onclick={saveTemplate} disabled={formSaving}>
						{formSaving ? 'Сохранение…' : 'Сохранить'}
					</button>
				</div>
			</div>
		</div>
	{/if}

	<!-- Delete confirmation overlay -->
	{#if confirmDelete}
		<div class="overlay" role="dialog" aria-modal="true">
			<div class="confirm-card">
				<p>Удалить шаблон «{confirmDelete.name}»?</p>
				<div class="form-actions">
					<button class="btn btn-secondary" onclick={cancelDelete} disabled={deleting}>Отмена</button>
					<button class="btn btn-danger" onclick={doDelete} disabled={deleting}>
						{deleting ? 'Удаление…' : 'Удалить'}
					</button>
				</div>
			</div>
		</div>
	{/if}

	<!-- Template detail overlay -->
	{#if viewingTemplate}
		<div class="overlay" role="dialog" aria-modal="true">
			<div class="detail-card">
				<h2>
					{#if viewingTemplate.emoji}<span class="card-emoji">{viewingTemplate.emoji}</span>{/if}
					{viewingTemplate.name}
				</h2>

				<div class="detail-section">
					<span class="prompt-label">Системный промпт</span>
					<p class="detail-text">{viewingTemplate.system_prompt}</p>
				</div>

				{#if viewingTemplate.user_prompt_template}
					<div class="detail-section">
						<span class="prompt-label">Пользовательский промпт</span>
						<p class="detail-text">{viewingTemplate.user_prompt_template}</p>
					</div>
				{/if}

				<div class="form-actions">
					{#if viewingTemplate.is_custom}
						<button class="btn btn-secondary" onclick={() => { const v = viewingTemplate; viewingTemplate = null; openEdit(v!); }}>Редактировать</button>
						<button class="btn btn-danger" onclick={() => { const v = viewingTemplate; viewingTemplate = null; askDelete(v!); }}>Удалить</button>
						<button class="btn btn-secondary" onclick={() => exportSingle(viewingTemplate!.slug)}>Экспорт</button>
					{/if}
					<button class="btn btn-primary" onclick={() => viewingTemplate = null}>Закрыть</button>
				</div>
			</div>
		</div>
	{/if}
</div>

<style>
	/* Page layout */
	.page {
		max-width: 56rem;
		margin: 0 auto;
		padding: 2rem 1.5rem;
	}

	.page-header {
		display: flex;
		align-items: center;
		justify-content: space-between;
		margin-bottom: 1.5rem;
	}

	.page-header h1 {
		font-size: 1.5rem;
		font-weight: 600;
		margin: 0;
	}

	.header-actions {
		display: flex;
		gap: 0.5rem;
		align-items: center;
	}

	.success-banner {
		background-color: rgba(33, 160, 56, 0.1);
		border: 1px solid rgba(33, 160, 56, 0.25);
		color: var(--color-cta);
		border-radius: var(--radius-sm);
		padding: 0.75rem 1rem;
		font-size: 0.875rem;
		margin-bottom: 1rem;
	}

	/* Grid */
	.grid {
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(16rem, 1fr));
		gap: 1rem;
	}

	/* Template card */
	.template-card {
		cursor: pointer;
		display: flex;
		flex-direction: column;
		gap: 0.75rem;
		transition: box-shadow 0.15s;
	}

	.template-card:hover {
		box-shadow: 0 2px 8px rgba(0, 0, 0, 0.08);
	}

	.card-head {
		display: flex;
		align-items: flex-start;
		justify-content: space-between;
		gap: 0.5rem;
	}

	.card-title {
		font-weight: 600;
		font-size: 0.9375rem;
		display: flex;
		align-items: center;
		gap: 0.375rem;
		line-height: 1.3;
	}

	.card-emoji {
		font-size: 1.125rem;
		flex-shrink: 0;
	}

	.card-badges {
		display: flex;
		gap: 0.25rem;
		flex-shrink: 0;
	}

	.badge-system {
		background: var(--color-bg);
		color: var(--color-muted);
		border: 1px solid var(--color-border);
	}

	.badge-custom {
		background: rgba(33, 160, 56, 0.1);
		color: var(--color-cta);
		border: 1px solid rgba(33, 160, 56, 0.25);
	}

	.card-body {
		display: flex;
		flex-direction: column;
		gap: 0.5rem;
		flex: 1;
	}

	.prompt-preview {
		display: flex;
		flex-direction: column;
		gap: 0.125rem;
	}

	.prompt-label {
		font-size: 0.6875rem;
		font-weight: 600;
		text-transform: uppercase;
		letter-spacing: 0.04em;
		color: var(--color-muted);
	}

	.prompt-preview p {
		margin: 0;
		font-size: 0.8125rem;
		color: var(--color-text);
		opacity: 0.8;
		line-height: 1.45;
		display: -webkit-box;
		-webkit-line-clamp: 2;
		-webkit-box-orient: vertical;
		overflow: hidden;
	}

	/* Card actions */
	.card-actions {
		display: flex;
		gap: 0.375rem;
		border-top: 1px solid var(--color-border);
		padding-top: 0.625rem;
		margin-top: auto;
	}

	.btn-icon {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		width: 2rem;
		height: 2rem;
		border: none;
		border-radius: var(--radius-sm);
		background: transparent;
		color: var(--color-muted);
		cursor: pointer;
		transition: background-color 0.15s, color 0.15s;
	}

	.btn-icon:hover {
		background: var(--color-bg);
		color: var(--color-text);
	}

	.btn-icon-danger:hover {
		background: var(--color-error-bg);
		color: var(--color-error-text);
	}

	/* Empty / loading */
	.empty {
		text-align: center;
		padding: 3rem 1rem;
		color: var(--color-muted);
		font-size: 0.9375rem;
	}

	/* Overlay */
	.overlay {
		position: fixed;
		inset: 0;
		z-index: 50;
		display: flex;
		align-items: center;
		justify-content: center;
		background: rgba(0, 0, 0, 0.4);
		backdrop-filter: blur(2px);
	}

	/* Form card */
	.form-card,
	.confirm-card {
		background: var(--color-card);
		border-radius: var(--radius);
		padding: 1.5rem;
		width: min(28rem, 92vw);
		max-height: 90vh;
		overflow-y: auto;
		box-shadow: 0 8px 32px rgba(0, 0, 0, 0.18);
	}

	.form-card h2,
	.detail-card h2 {
		margin: 0 0 1.25rem;
		font-size: 1.125rem;
		font-weight: 600;
	}

	/* Detail card */
	.detail-card {
		background: var(--color-card);
		border-radius: var(--radius);
		padding: 1.5rem;
		width: min(40rem, 92vw);
		max-height: 90vh;
		overflow-y: auto;
		box-shadow: 0 8px 32px rgba(0, 0, 0, 0.18);
	}

	.detail-card h2 {
		display: flex;
		align-items: center;
		gap: 0.375rem;
	}

	.detail-section {
		margin-bottom: 1rem;
	}

	.detail-text {
		white-space: pre-wrap;
		font-size: 0.875rem;
		line-height: 1.6;
		color: var(--color-text);
		margin: 0.25rem 0 0;
		background: var(--color-bg);
		padding: 0.75rem;
		border-radius: calc(var(--radius) - 2px);
	}

	/* Field */
	.field {
		display: flex;
		flex-direction: column;
		gap: 0.25rem;
		margin-bottom: 1rem;
	}

	.field-label {
		font-size: 0.8125rem;
		font-weight: 500;
		color: var(--color-text);
	}

	.field-hint {
		font-size: 0.75rem;
		color: var(--color-muted);
	}

	.field-hint code {
		background: var(--color-bg);
		padding: 0.0625rem 0.25rem;
		border-radius: 3px;
		font-size: 0.75rem;
	}

	.textarea {
		resize: vertical;
		min-height: 3.5rem;
		line-height: 1.5;
	}

	.input-emoji {
		width: 4rem;
		text-align: center;
	}

	/* Form actions */
	.form-actions {
		display: flex;
		justify-content: flex-end;
		gap: 0.5rem;
		margin-top: 1rem;
	}

	/* Danger button */
	.btn-danger {
		background-color: var(--color-error-text);
		color: #fff;
	}

	.btn-danger:hover:not(:disabled) {
		opacity: 0.9;
	}

	/* Confirm card */
	.confirm-card p {
		font-size: 0.9375rem;
		margin: 0 0 1rem;
		line-height: 1.5;
	}
</style>
