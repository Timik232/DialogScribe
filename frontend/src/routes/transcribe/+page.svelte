<script lang="ts">
	import AudioUploader from '$lib/components/AudioUploader.svelte';
	import { fetchApi } from '$lib/services/api';
	import {
		transcriptionStore,
		type TranscriptionResult,
		type TranscriptionSegment
	} from '$lib/stores/transcription';
	import { authStore } from '$lib/stores/auth';
	import { goto } from '$app/navigation';
	import { onMount } from 'svelte';
	import { get } from 'svelte/store';

	const SPEAKER_COLORS = [
		'#4285F4', '#EA4335', '#FBBC05', '#34A853',
		'#FF6D01', '#46BDC6', '#7B61FF', '#F538A0'
	];

	const EXPORT_FORMATS = [
		{ id: 'txt', label: 'TXT' },
		{ id: 'json', label: 'JSON' },
		{ id: 'srt', label: 'SRT' },
		{ id: 'vtt', label: 'VTT' },
		{ id: 'docx', label: 'DOCX' },
		{ id: 'pdf', label: 'PDF' }
	];

	let selectedFile: File | null = $state(null);
	let diarizationMode = $state('none');
	let language = $state('');
	let denoiseMode = $state('none');
	let loading = $state(false);
	let error = $state('');
	let result: TranscriptionResult | null = $state(null);
	let activeTab = $state<'segments' | 'text'>('segments');
	let copySuccess = $state(false);
	let speakerNames: Record<string, string> = $state({});
	let editingSpeaker: string | null = $state(null);
	let editingSpeakerValue = $state('');
	let saving = $state(false);
	let saved = $state(false);

	onMount(() => {
		if (result === null) {
			const stored = get(transcriptionStore);
			if (stored) result = stored;
		}
	});

	function speakerColor(speaker: string | undefined): string {
		if (!speaker) return 'var(--color-muted)';
		const match = speaker.match(/\d+/);
		const idx = match ? parseInt(match[0], 10) - 1 : 0;
		return SPEAKER_COLORS[((idx % SPEAKER_COLORS.length) + SPEAKER_COLORS.length) % SPEAKER_COLORS.length];
	}

	function formatTimestamp(seconds: number): string {
		const h = Math.floor(seconds / 3600);
		const m = Math.floor((seconds % 3600) / 60);
		const s = Math.floor(seconds % 60);
		if (h > 0) {
			return `${h}:${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`;
		}
		return `${m}:${s.toString().padStart(2, '0')}`;
	}

	function formatDuration(seconds: number): string {
		const h = Math.floor(seconds / 3600);
		const m = Math.floor((seconds % 3600) / 60);
		const s = Math.floor(seconds % 60);
		if (h > 0) return `${h} ч ${m} мин ${s} с`;
		if (m > 0) return `${m} мин ${s} с`;
		return `${s} с`;
	}

	function onFileSelected(file: File): void {
		selectedFile = file;
		error = '';
	}

	async function transcribe(): Promise<void> {
		if (!selectedFile) return;
		loading = true;
		error = '';
		result = null;

		try {
			const formData = new FormData();
			formData.append('file', selectedFile);
			formData.append('diarization_mode', diarizationMode);
			formData.append('denoise', denoiseMode);
			if (language.trim()) {
				formData.append('language', language.trim());
			}

			const endpoint = '/api/transcribe';
			const data = await fetchApi<TranscriptionResult>('POST', endpoint, {
				body: formData
			});

			result = data;
			transcriptionStore.set(data);
		} catch (e: any) {
			error = e?.message || 'Ошибка транскрипции';
		} finally {
			loading = false;
		}
	}

	function startSpeakerEdit(speaker: string): void {
		editingSpeaker = speaker;
		editingSpeakerValue = speakerNames[speaker] || '';
		setTimeout(() => {
			const input = document.querySelector('.speaker-input') as HTMLInputElement;
			if (input) { input.focus(); input.select(); }
		}, 0);
	}

	function saveSpeakerName(speaker: string): void {
		if (!editingSpeaker) return;
		if (editingSpeakerValue.trim()) {
			speakerNames[speaker] = editingSpeakerValue.trim();
		} else {
			delete speakerNames[speaker];
		}
		editingSpeaker = null;
		editingSpeakerValue = '';
	}

	function cancelSpeakerEdit(): void {
		editingSpeaker = null;
		editingSpeakerValue = '';
	}

	async function downloadExport(format: string): Promise<void> {
		if (!result) return;
		try {
			const res = await fetch('/api/export', {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				credentials: 'include',
				body: JSON.stringify({
					data: result,
					format,
					filename: 'transcription',
					speaker_names: Object.keys(speakerNames).length > 0 ? speakerNames : undefined
				})
			});

			if (!res.ok) throw new Error('Экспорт не удался');

			const blob = await res.blob();
			const extMap: Record<string, string> = {
				txt: '.txt', json: '.json', srt: '.srt',
				vtt: '.vtt', docx: '.docx', pdf: '.pdf'
			};
			const filename = `transcription${extMap[format] || '.txt'}`;

			const url = URL.createObjectURL(blob);
			const a = document.createElement('a');
			a.href = url;
			a.download = filename;
			document.body.appendChild(a);
			a.click();
			document.body.removeChild(a);
			URL.revokeObjectURL(url);
		} catch (e: any) {
			error = e?.message || 'Ошибка экспорта';
		}
	}

	function sendToAnalysis(): void {
		if (!result) return;
		let textToSend = result.text;
		for (const [original, name] of Object.entries(speakerNames)) {
			textToSend = textToSend.replaceAll(original, name);
		}
		transcriptionStore.set({ ...result, text: textToSend, speaker_names: speakerNames });
		goto('/analysis');
	}

	async function copyText(): Promise<void> {
		if (!result) return;
		try {
			await navigator.clipboard.writeText(result.text);
			copySuccess = true;
			setTimeout(() => { copySuccess = false; }, 2000);
		} catch {
			/* fallback: select textarea */
		}
	}

	function generateDefaultTitle(r: TranscriptionResult): string {
		const now = new Date();
		const pad = (n: number) => String(n).padStart(2, '0');
		const dateStr = `${pad(now.getDate())}.${pad(now.getMonth() + 1)}.${now.getFullYear()} ${pad(now.getHours())}:${pad(now.getMinutes())}`;
		const totalSec = Math.floor(r.duration || 0);
		const mins = Math.floor(totalSec / 60);
		const secs = totalSec % 60;
		const durStr = mins > 0 ? `${mins}м ${secs}с` : `${secs}с`;
		return `${dateStr} — ${durStr}`;
	}

	async function saveTranscription(): Promise<void> {
		if (!result || saving || saved) return;
		saving = true;
		try {
			await fetchApi('POST', '/api/saved-transcriptions', {
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({
					title: generateDefaultTitle(result),
					full_text: result.text,
					segments: result.segments || [],
					speaker_names: speakerNames || {},
					duration: result.duration || 0,
					language: result.language || 'ru'
				})
			});
			saved = true;
		} catch (e) {
			console.error('Failed to save transcription:', e);
		} finally {
			saving = false;
		}
	}
</script>

<div class="container page">
	<h1>Транскрипция</h1>

	<section class="card upload-section">
		<h3>Аудио или видео файл</h3>
		<AudioUploader onfile={onFileSelected} />

		<div class="settings-row">
			<div class="field">
				<label for="diarization">Диаризация</label>
				<select id="diarization" class="input" bind:value={diarizationMode}>
					<option value="none">Нет</option>
					<option value="simple">Простая</option>
					<option value="advanced">Расширенная</option>
				</select>
			</div>
			<div class="field">
				<label for="denoise">Шумоподавление</label>
				<select id="denoise" class="input" bind:value={denoiseMode}>
					<option value="none">Нет</option>
					<option value="light">Лёгкое</option>
					<option value="medium">Среднее</option>
				</select>
			</div>
			<div class="field">
				<label for="language">Язык</label>
				<input
					id="language"
					class="input"
					type="text"
					placeholder="ru (по умолчанию)"
					bind:value={language}
				/>
			</div>
		</div>

		<button
			class="btn btn-primary transcribe-btn"
			onclick={transcribe}
			disabled={!selectedFile || loading}
		>
			{#if loading}
				<span class="spinner"></span>
				Транскрибация…
			{:else}
				Транскрибировать
			{/if}
		</button>
	</section>

	{#if error}
		<div class="error-banner">{error}</div>
	{/if}

	{#if result}
		<section class="card results-section">
			<div class="results-header">
				<h2>Результат</h2>
				<div class="info-bar">
					<span class="badge info-badge">{formatDuration(result.duration)}</span>
					<span class="badge info-badge">{result.language}</span>
					<span class="badge info-badge">{result.segments.length} сегментов</span>
				</div>
			</div>

			{#if $authStore?.isAuthenticated}
				<div style="margin: 0.75rem 0;">
					{#if saved}
						<button class="btn" disabled style="opacity: 0.6; cursor: default;">✓ Сохранено</button>
					{:else}
						<button class="btn btn-primary" onclick={saveTranscription} disabled={saving}>
							{saving ? 'Сохранение...' : '💾 Сохранить'}
						</button>
					{/if}
				</div>
			{/if}

			<div class="tabs">
				<button
					class="tab"
					class:active={activeTab === 'segments'}
					onclick={() => (activeTab = 'segments')}
				>
					Сегменты
				</button>
				<button
					class="tab"
					class:active={activeTab === 'text'}
					onclick={() => (activeTab = 'text')}
				>
					Полный текст
				</button>
			</div>

			{#if activeTab === 'segments'}
				<div class="segments-list">
					{#each result.segments as seg, i}
						<div class="segment">
							<span class="segment-time">
								[{formatTimestamp(seg.start)} – {formatTimestamp(seg.end)}]
							</span>
							{#if seg.speaker}
								{#if editingSpeaker === seg.speaker}
									<input
										class="speaker-input"
										type="text"
										bind:value={editingSpeakerValue}
										placeholder={seg.speaker}
										onkeydown={(e) => {
											if (e.key === 'Enter') saveSpeakerName(seg.speaker);
											if (e.key === 'Escape') cancelSpeakerEdit();
										}}
										onblur={() => saveSpeakerName(seg.speaker)}
									/>
								{:else}
									<span
										class="speaker-badge clickable"
										style="color: {speakerColor(seg.speaker)}; border-color: {speakerColor(seg.speaker)}"
										onclick={() => startSpeakerEdit(seg.speaker)}
										title="Нажмите чтобы переименовать"
									>
										{speakerNames[seg.speaker] || seg.speaker}
									</span>
								{/if}
							{/if}
							<span class="segment-text">{seg.text}</span>
						</div>
					{/each}
				</div>
			{:else}
				<div class="fulltext-wrapper">
					<pre class="fulltext">{result.text}</pre>
					<button class="copy-btn" onclick={copyText} title="Копировать">
						{#if copySuccess}
							<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
								<polyline points="20 6 9 17 4 12"/>
							</svg>
						{:else}
							<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
								<rect x="9" y="9" width="13" height="13" rx="2" ry="2"/>
								<path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>
							</svg>
						{/if}
					</button>
				</div>
			{/if}
		</section>

		<section class="card actions-section">
			<h3>Скачать</h3>
			<div class="download-row">
				{#each EXPORT_FORMATS as fmt}
					<button class="btn btn-secondary" onclick={() => downloadExport(fmt.id)}>
						{fmt.label}
					</button>
				{/each}
			</div>

			<button class="btn btn-primary send-btn" onclick={sendToAnalysis}>
				Отправить на анализ
			</button>
		</section>
	{/if}
</div>

<style>
	.page {
		padding: 1.5rem 1rem 3rem;
	}

	.page h1 {
		margin-bottom: 1.25rem;
	}

	.upload-section {
		display: flex;
		flex-direction: column;
		gap: 1rem;
		margin-bottom: 1rem;
	}

	.settings-row {
		display: flex;
		gap: 1rem;
		flex-wrap: wrap;
	}

	.field {
		display: flex;
		flex-direction: column;
		gap: 0.375rem;
		flex: 1;
		min-width: 180px;
	}

	.field label {
		font-size: 0.8125rem;
		font-weight: 500;
		color: var(--color-muted);
	}

	.transcribe-btn {
		align-self: flex-start;
		min-width: 180px;
		height: 42px;
		font-size: 0.9375rem;
	}

	.spinner {
		width: 16px;
		height: 16px;
		border: 2px solid rgba(255, 255, 255, 0.3);
		border-top-color: #fff;
		border-radius: 50%;
		animation: spin 0.6s linear infinite;
	}

	@keyframes spin {
		to { transform: rotate(360deg); }
	}

	.results-section {
		margin-bottom: 1rem;
	}

	.results-header {
		display: flex;
		align-items: center;
		justify-content: space-between;
		flex-wrap: wrap;
		gap: 0.75rem;
		margin-bottom: 1rem;
	}

	.results-header h2 {
		margin: 0;
	}

	.info-bar {
		display: flex;
		gap: 0.5rem;
		flex-wrap: wrap;
	}

	.info-badge {
		background-color: var(--color-bg);
		color: var(--color-text);
		border: 1px solid var(--color-border);
	}

	.tabs {
		display: flex;
		gap: 0;
		border-bottom: 1px solid var(--color-border);
		margin-bottom: 1rem;
	}

	.tab {
		padding: 0.5rem 1rem;
		background: none;
		border: none;
		border-bottom: 2px solid transparent;
		font-size: 0.875rem;
		font-weight: 500;
		color: var(--color-muted);
		cursor: pointer;
		transition: color 0.15s, border-color 0.15s;
		font-family: var(--font-family);
	}

	.tab:hover {
		color: var(--color-text);
	}

	.tab.active {
		color: var(--color-cta);
		border-bottom-color: var(--color-cta);
	}

	.segments-list {
		display: flex;
		flex-direction: column;
		gap: 0.5rem;
	}

	.segment {
		display: flex;
		align-items: baseline;
		gap: 0.5rem;
		padding: 0.5rem 0.75rem;
		border-radius: var(--radius-sm);
		background-color: var(--color-bg);
		line-height: 1.5;
	}

	.segment-time {
		font-size: 0.8125rem;
		color: var(--color-muted);
		white-space: nowrap;
		font-variant-numeric: tabular-nums;
		flex-shrink: 0;
	}

	.speaker-badge {
		font-size: 0.75rem;
		font-weight: 600;
		padding: 0.0625rem 0.5rem;
		border: 1px solid;
		border-radius: 9999px;
		white-space: nowrap;
		flex-shrink: 0;
	}

	.speaker-badge.clickable {
		cursor: pointer;
		transition: opacity 0.15s;
	}

	.speaker-badge.clickable:hover {
		opacity: 0.75;
	}

	.speaker-input {
		font-size: 0.75rem;
		font-weight: 600;
		padding: 0.0625rem 0.375rem;
		border: 1px solid var(--color-cta);
		border-radius: 9999px;
		outline: none;
		width: 80px;
		background: var(--color-card);
	}

	.segment-text {
		font-size: 0.9375rem;
	}

	.fulltext-wrapper {
		position: relative;
	}

	.fulltext {
		background-color: var(--color-bg);
		border-radius: var(--radius-sm);
		padding: 1rem;
		padding-right: 2.5rem;
		font-size: 0.9375rem;
		line-height: 1.6;
		white-space: pre-wrap;
		word-break: break-word;
		max-height: 400px;
		overflow-y: auto;
		font-family: var(--font-family);
		margin: 0;
	}

	.copy-btn {
		position: absolute;
		top: 0.5rem;
		right: 0.5rem;
		background: var(--color-card);
		border: 1px solid var(--color-border);
		border-radius: var(--radius-sm);
		padding: 0.375rem;
		cursor: pointer;
		color: var(--color-muted);
		transition: color 0.15s, border-color 0.15s;
	}

	.copy-btn:hover {
		color: var(--color-cta);
		border-color: var(--color-cta);
	}

	.actions-section {
		display: flex;
		flex-direction: column;
		gap: 1rem;
	}

	.actions-section h3 {
		margin: 0;
	}

	.download-row {
		display: flex;
		gap: 0.5rem;
		flex-wrap: wrap;
	}

	.send-btn {
		align-self: flex-start;
		margin-top: 0.25rem;
	}
</style>
