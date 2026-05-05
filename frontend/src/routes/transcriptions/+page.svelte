<script lang="ts">
	import { fetchApi } from '$lib/services/api';
	import { onMount } from 'svelte';

	interface TranscriptionListItem {
		id: string;
		title: string;
		duration: number;
		language: string;
		created_at: string;
		share_id: string | null;
	}

	interface TranscriptionDetail {
		id: string;
		title: string;
		full_text: string;
		analysis_text: string | null;
		segments_json: Array<{ text: string; start: number; end: number; speaker?: string }>;
		speaker_names: Record<string, string>;
		duration: number;
		language: string;
		share_id: string | null;
		created_at: string;
		updated_at: string;
	}

	let transcriptions: TranscriptionListItem[] = $state([]);
	let loading = $state(true);
	let searchQuery = $state('');
	let searchTimeout: ReturnType<typeof setTimeout> | null = null;

	let selectedId: string | null = $state(null);
	let selectedDetail: TranscriptionDetail | null = $state(null);
	let editTitle = $state('');
	let editText = $state('');
	let saving = $state(false);
	let deleting = $state(false);
	let sharing = $state(false);
	let analyzing = $state(false);
	let toast = $state('');
	let analysisPolling = $state(false);
	let analysisPollTimer: ReturnType<typeof setInterval> | null = null;

	onMount(() => {
		loadTranscriptions();
	});

	$effect(() => {
		return () => {
			stopAnalysisPolling();
		};
	});

	async function loadTranscriptions(query?: string) {
		loading = true;
		try {
			const path = query
				? `/api/saved-transcriptions?q=${encodeURIComponent(query)}`
				: '/api/saved-transcriptions';
			const data = await fetchApi<TranscriptionListItem[]>('GET', path);
			transcriptions = Array.isArray(data) ? data : [];
		} catch (e) {
			console.error('Failed to load transcriptions:', e);
			transcriptions = [];
		} finally {
			loading = false;
		}
	}

	function onSearchInput() {
		if (searchTimeout) clearTimeout(searchTimeout);
		searchTimeout = setTimeout(() => {
			loadTranscriptions(searchQuery || undefined);
		}, 300);
	}

	function formatDuration(seconds: number): string {
		if (!seconds) return '0:00';
		const m = Math.floor(seconds / 60);
		const s = Math.floor(seconds % 60);
		return `${m}:${s.toString().padStart(2, '0')}`;
	}

	function formatDate(dateStr: string): string {
		const d = new Date(dateStr);
		const pad = (n: number) => String(n).padStart(2, '0');
		return `${pad(d.getDate())}.${pad(d.getMonth() + 1)}.${d.getFullYear()} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
	}

	async function openDetail(id: string) {
		selectedId = id;
		stopAnalysisPolling();
		try {
			const data = await fetchApi<TranscriptionDetail>('GET', `/api/saved-transcriptions/${id}`);
			selectedDetail = data;
			editTitle = data.title;
			editText = data.full_text;
			if (!data.analysis_text && data.full_text) {
				startAnalysisPolling();
			}
		} catch (e) {
			console.error('Failed to load detail:', e);
			selectedId = null;
		}
	}

	function closeDetail() {
		stopAnalysisPolling();
		selectedId = null;
		selectedDetail = null;
	}

	async function saveChanges() {
		if (!selectedDetail || saving) return;
		saving = true;
		try {
			await fetchApi('PUT', `/api/saved-transcriptions/${selectedDetail.id}`, {
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({ title: editTitle, full_text: editText })
			});
			selectedDetail.title = editTitle;
			selectedDetail.full_text = editText;
			showToast('Сохранено');
			const idx = transcriptions.findIndex((t) => t.id === selectedDetail!.id);
			if (idx !== -1) transcriptions[idx].title = editTitle;
		} catch (e) {
			console.error('Failed to save:', e);
		} finally {
			saving = false;
		}
	}

	async function deleteTranscription() {
		if (!selectedDetail || deleting) return;
		if (!confirm('Удалить расшифровку?')) return;
		deleting = true;
		try {
			await fetchApi('DELETE', `/api/saved-transcriptions/${selectedDetail.id}`);
			closeDetail();
			await loadTranscriptions(searchQuery || undefined);
		} catch (e) {
			console.error('Failed to delete:', e);
		} finally {
			deleting = false;
		}
	}

		async function toggleShare() {
		if (!selectedDetail || sharing) return;
		sharing = true;
		try {
			if (selectedDetail.share_id) {
				await fetchApi('DELETE', `/api/saved-transcriptions/${selectedDetail.id}/share`);
				selectedDetail.share_id = null;
				showToast('Доступ отменён');
			} else {
				const data = await fetchApi<{ share_id: string }>('POST', `/api/saved-transcriptions/${selectedDetail.id}/share`);
				selectedDetail.share_id = data.share_id;
				const url = `${window.location.origin}/share/${data.share_id}`;
				await navigator.clipboard.writeText(url);
				showToast('Ссылка скопирована!');
			}
		} catch (e) {
			console.error('Failed to toggle share:', e);
		} finally {
			sharing = false;
		}
	}

	async function runAnalysis() {
		stopAnalysisPolling();
		if (!selectedDetail || analyzing) return;
		if (selectedDetail.analysis_text) {
			if (!confirm('Запустить анализ заново? Текущий результат будет заменён.')) return;
		}
		analyzing = true;
		try {
			const data = await fetchApi<{ analysis_text: string }>('POST', `/api/saved-transcriptions/${selectedDetail.id}/analyze`);
			selectedDetail.analysis_text = data.analysis_text;
			showToast('Анализ готов');
		} catch (e) {
			console.error('Failed to analyze:', e);
			showToast('Ошибка анализа');
		} finally {
			analyzing = false;
		}
	}

	function startAnalysisPolling() {
		if (analysisPollTimer) clearInterval(analysisPollTimer);
		analysisPolling = true;
		let attempts = 0;
		analysisPollTimer = setInterval(async () => {
			attempts++;
			if (attempts > 20) {
				stopAnalysisPolling();
				return;
			}
			try {
				const data = await fetchApi<TranscriptionDetail>('GET', `/api/saved-transcriptions/${selectedId}`);
				if (data.analysis_text) {
					selectedDetail = data;
					stopAnalysisPolling();
				}
			} catch (e) {
				console.error('Polling failed:', e);
			}
		}, 3000);
	}

	function stopAnalysisPolling() {
		if (analysisPollTimer) {
			clearInterval(analysisPollTimer);
			analysisPollTimer = null;
		}
		analysisPolling = false;
	}

	function showToast(msg: string) {
		toast = msg;
		setTimeout(() => {
			toast = '';
		}, 3000);
	}

	function formatSegTime(s: number): string {
		const m = Math.floor(s / 60);
		const sec = Math.floor(s % 60);
		return `${m}:${sec.toString().padStart(2, '0')}`;
	}
</script>

<div class="page">
	<h1>Мои расшифровки</h1>

	<div class="search-bar">
		<input
			type="text"
			class="input"
			placeholder="Поиск расшифровок..."
			bind:value={searchQuery}
			oninput={onSearchInput}
		/>
	</div>

	{#if loading}
		<div class="empty">Загрузка…</div>
	{:else if transcriptions.length === 0}
		<div class="empty-state">
			{#if searchQuery}
				<p>Ничего не найдено</p>
			{:else}
				<p>Нет сохранённых расшифровок</p>
				<a href="/transcribe" class="btn btn-primary">Создать расшифровку</a>
			{/if}
		</div>
	{:else}
		<div class="grid">
			{#each transcriptions as item (item.id)}
				<div class="card transcription-card" onclick={() => openDetail(item.id)} role="button" tabindex="0">
					<h3 class="card-title">{item.title}</h3>
					<div class="card-meta">
						<span class="badge">{formatDate(item.created_at)}</span>
						<span class="badge">{formatDuration(item.duration)}</span>
						<span class="badge">{item.language}</span>
					</div>
				</div>
			{/each}
		</div>
	{/if}

	{#if selectedId}
		{#if selectedDetail}
		<div class="overlay" onclick={closeDetail}>
			<div class="form-card" onclick={(e) => e.stopPropagation()}>
				<div class="overlay-header">
					<h2>Расшифровка</h2>
					<button class="close-btn" onclick={closeDetail}>✕</button>
				</div>

				<div class="field">
					<label>Название</label>
					<input type="text" class="input" bind:value={editTitle} />
				</div>

				<div class="field">
					<label>Текст</label>
					<textarea class="input" bind:value={editText} rows="15" style="resize: vertical;"></textarea>
				</div>

				{#if selectedDetail.analysis_text}
					<div class="analysis-section">
						<label>Анализ</label>
						<div class="analysis-content">{selectedDetail.analysis_text}</div>
					</div>
				{/if}

				{#if analysisPolling}
					<div class="analysis-section" style="opacity: 0.7;">
						<label>Анализ ⏳</label>
						<div class="analysis-content" style="color: var(--color-muted);">Анализ выполняется, подождите...</div>
					</div>
				{/if}

				{#if selectedDetail.segments_json && selectedDetail.segments_json.length > 0}
					<div class="segments-section">
						<h4>Сегменты</h4>
						<div class="segments-list">
							{#each selectedDetail.segments_json as seg}
								<div class="segment">
									<span class="seg-time">{formatSegTime(seg.start)}</span>
									{#if seg.speaker}
										<span class="seg-speaker">{selectedDetail.speaker_names?.[seg.speaker] || seg.speaker}</span>
									{/if}
									<span class="seg-text">{seg.text}</span>
								</div>
							{/each}
						</div>
					</div>
				{/if}

				<div class="overlay-actions">
					<button class="btn btn-primary" onclick={saveChanges} disabled={saving}>
						{saving ? 'Сохранение...' : 'Сохранить'}
					</button>

					<button class="btn" onclick={runAnalysis} disabled={analyzing || !selectedDetail?.full_text}>
						{analyzing ? 'Анализирую...' : '🧠 Анализировать'}
					</button>

					{#if selectedDetail.share_id}
						<div class="share-info">
							<input
								type="text"
								class="input"
								value="{`${window.location.origin}/share/${selectedDetail.share_id}`}"
								readonly
								style="flex:1; font-size: 0.85rem;"
							/>
							<button
								class="btn"
								onclick={async () => {
									await navigator.clipboard.writeText(`${window.location.origin}/share/${selectedDetail.share_id}`);
									showToast('Ссылка скопирована!');
								}}
							>
								📋
							</button>
							<button class="btn" onclick={toggleShare} disabled={sharing}>Отменить доступ</button>
						</div>
					{:else}
						<button class="btn" onclick={toggleShare} disabled={sharing}>
							{sharing ? '...' : '🔗 Поделиться'}
						</button>
					{/if}

					<button class="btn btn-danger" onclick={deleteTranscription} disabled={deleting}>
						{deleting ? 'Удаление...' : '🗑 Удалить'}
					</button>

					<button class="btn" onclick={closeDetail}>Закрыть</button>
				</div>
			</div>
		</div>
		{:else}
			<div class="overlay" onclick={closeDetail}>
				<div class="form-card" onclick={(e) => e.stopPropagation()}>
					<div class="overlay-header">
						<h2>Загрузка…</h2>
						<button class="close-btn" onclick={closeDetail}>✕</button>
					</div>
					<div class="empty">Загрузка данных расшифровки…</div>
				</div>
			</div>
		{/if}
	{/if}

	{#if toast}
		<div class="toast">{toast}</div>
	{/if}
</div>

<style>
	/* Page layout */
	.page {
		max-width: 56rem;
		margin: 0 auto;
		padding: 2rem 1.5rem;
	}

	.page h1 {
		font-size: 1.5rem;
		font-weight: 600;
		margin: 0 0 1rem;
	}

	/* Search */
	.search-bar {
		margin-bottom: 1rem;
	}

	/* Grid */
	.grid {
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(16rem, 1fr));
		gap: 1rem;
	}

	/* Card */
	.transcription-card {
		cursor: pointer;
		display: flex;
		flex-direction: column;
		gap: 0.5rem;
		transition: box-shadow 0.15s;
	}

	.transcription-card:hover {
		box-shadow: 0 2px 8px rgba(0, 0, 0, 0.08);
	}

	.card-title {
		font-weight: 600;
		font-size: 0.9375rem;
		margin: 0;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	.card-meta {
		display: flex;
		gap: 0.5rem;
		flex-wrap: wrap;
	}

	/* Badge */
	.badge {
		font-size: 0.75rem;
		padding: 0.1875rem 0.5rem;
		border-radius: var(--radius-sm);
		background: var(--color-bg);
		color: var(--color-muted);
		border: 1px solid var(--color-border);
		white-space: nowrap;
	}

	/* Empty */
	.empty {
		text-align: center;
		padding: 3rem 1rem;
		color: var(--color-muted);
		font-size: 0.9375rem;
	}

	.empty-state {
		text-align: center;
		padding: 3rem 1rem;
		display: flex;
		flex-direction: column;
		align-items: center;
		gap: 1rem;
	}

	.empty-state p {
		color: var(--color-muted);
		font-size: 0.9375rem;
		margin: 0;
	}

	/* Overlay */
	.overlay {
		position: fixed;
		inset: 0;
		background: rgba(0, 0, 0, 0.5);
		display: flex;
		align-items: center;
		justify-content: center;
		z-index: 1000;
		padding: 1rem;
	}

	.form-card {
		background: var(--color-card, var(--color-bg));
		border-radius: var(--radius-md, var(--radius-sm));
		padding: 1.5rem;
		max-width: 48rem;
		width: 100%;
		max-height: 90vh;
		overflow-y: auto;
		display: flex;
		flex-direction: column;
		gap: 1rem;
	}

	.overlay-header {
		display: flex;
		justify-content: space-between;
		align-items: center;
	}

	.overlay-header h2 {
		margin: 0;
		font-size: 1.25rem;
	}

	.close-btn {
		background: none;
		border: none;
		font-size: 1.25rem;
		cursor: pointer;
		color: var(--color-muted);
		padding: 0.25rem;
	}

	.field {
		display: flex;
		flex-direction: column;
		gap: 0.25rem;
	}

	.field label {
		font-size: 0.8125rem;
		color: var(--color-muted);
		font-weight: 500;
	}

	/* Segments */
	.segments-section {
		margin-top: 0.5rem;
	}

	.segments-section h4 {
		margin: 0 0 0.5rem;
		font-size: 0.875rem;
	}

	.segments-list {
		max-height: 15rem;
		overflow-y: auto;
		border: 1px solid var(--color-border);
		border-radius: var(--radius-sm);
		padding: 0.5rem;
	}

	/* Analysis */
	.analysis-section {
		margin-top: 0.5rem;
		display: flex;
		flex-direction: column;
		gap: 0.25rem;
	}

	.analysis-section label {
		font-size: 0.8125rem;
		color: var(--color-muted);
		font-weight: 500;
	}

	.analysis-content {
		white-space: pre-wrap;
		word-wrap: break-word;
		max-height: 20rem;
		overflow-y: auto;
		padding: 1rem;
		border: 1px solid var(--color-border);
		border-radius: var(--radius-sm);
		font-size: 0.8125rem;
		line-height: 1.6;
	}

	.segment {
		display: flex;
		gap: 0.5rem;
		padding: 0.25rem 0;
		font-size: 0.8125rem;
		border-bottom: 1px solid var(--color-border);
	}

	.segment:last-child {
		border-bottom: none;
	}

	.seg-time {
		color: var(--color-muted);
		min-width: 3.5rem;
		font-family: monospace;
	}

	.seg-speaker {
		background: var(--color-bg);
		border: 1px solid var(--color-border);
		padding: 0 0.375rem;
		border-radius: var(--radius-sm);
		font-size: 0.75rem;
		white-space: nowrap;
	}

	.seg-text {
		flex: 1;
	}

	/* Actions */
	.overlay-actions {
		display: flex;
		gap: 0.5rem;
		flex-wrap: wrap;
		align-items: center;
		margin-top: 0.5rem;
	}

	.share-info {
		display: flex;
		gap: 0.5rem;
		align-items: center;
		flex: 1 1 100%;
	}

	.btn-danger {
		background: #dc3545;
		color: white;
		border-color: #dc3545;
	}

	.btn-danger:hover:not(:disabled) {
		background: #c82333;
	}

	/* Toast */
	.toast {
		position: fixed;
		bottom: 2rem;
		left: 50%;
		transform: translateX(-50%);
		background: var(--color-text);
		color: var(--color-bg);
		padding: 0.5rem 1.25rem;
		border-radius: var(--radius-md, var(--radius-sm));
		font-size: 0.875rem;
		z-index: 2000;
	}
</style>
