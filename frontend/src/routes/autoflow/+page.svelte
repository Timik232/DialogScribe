<script lang="ts">
	import AudioUploader from '$lib/components/AudioUploader.svelte';
	import { fetchApi } from '$lib/services/api';

	interface Template {
		slug: string;
		name: string;
		emoji: string;
	}

	interface Segment {
		start: number;
		end: number;
		text: string;
		speaker?: string;
		confidence?: number;
	}

	interface Transcription {
		text: string;
		language: string;
		duration: number;
		segments: Segment[];
	}

	interface AutoflowResult {
		transcription?: Transcription;
		summary?: string;
		mindmap_html?: string;
		mindmap_md?: string;
		action_items?: { action_items?: Array<{ task: string; assignee?: string | null; deadline?: string | null; priority: string }>; decisions?: Array<{ decision: string; context: string }> };
		suggested_steps?: { suggested_steps?: Array<{ step: string; reason: string; category: string }> };
		errors: string[];
		stage_timings: Record<string, number>;
	}

	interface ProgressMessage {
		stage: 'transcribing' | 'summarizing' | 'mindmap' | 'insights' | 'processing' | 'complete' | 'error';
		progress?: number;
		message?: string;
		result?: AutoflowResult;
	}

	const SPEAKER_COLORS = [
		'#4285F4', '#EA4335', '#FBBC05', '#34A853',
		'#FF6D01', '#46BDC6', '#7B61FF', '#F538A0'
	];

	const STAGES = [
		{ key: 'transcribing', label: 'Транскрибация', icon: '🎤' },
		{ key: 'summarizing', label: 'Саммари', icon: '📝' },
		{ key: 'mindmap', label: 'Майндмэп', icon: '🗺️' },
		{ key: 'insights', label: 'Инсайты', icon: '📋' }
	] as const;

	let selectedFile: File | null = $state(null);
	let templateKey = $state('meeting');
	let diarizationMode = $state('none');
	let denoiseMode = $state('none');
	let includeInsights = $state(false);
	let templates: Template[] = $state([]);

	let loading = $state(false);
	let error = $state('');
	let disconnected = $state(false);

	let currentStage: string = $state('');
	let progressValue = $state(0);
	let progressMessage = $state('');

	let result: AutoflowResult | null = $state(null);
	let activeSection = $state<'transcription' | 'summary' | 'mindmap' | 'insights'>('transcription');
	let speakerNames: Record<string, string> = $state({});
	let editingSpeaker: string | null = $state(null);
	let editingSpeakerValue = $state('');

	$effect(() => {
		loadTemplates();
	});

	async function loadTemplates(): Promise<void> {
		try {
			templates = await fetchApi<Template[]>('GET', '/api/templates');
		} catch {
			templates = [];
		}
	}

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
		if (h > 0) return `${h}:${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`;
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

	function formatTiming(seconds: number): string {
		if (seconds < 60) return `${seconds.toFixed(1)} с`;
		return `${(seconds / 60).toFixed(1)} мин`;
	}

	function onFileSelected(file: File): void {
		selectedFile = file;
		error = '';
	}

	function fileToBase64(file: File): Promise<string> {
		return new Promise((resolve, reject) => {
			const reader = new FileReader();
			reader.onload = () => {
				const dataUrl = reader.result as string;
				resolve(dataUrl.split(',')[1]);
			};
			reader.onerror = reject;
			reader.readAsDataURL(file);
		});
	}

	async function startAutoflow(): Promise<void> {
		if (!selectedFile) return;

		loading = true;
		error = '';
		disconnected = false;
		result = null;
		currentStage = 'transcribing';
		progressValue = 0;
		progressMessage = 'Подключение...';

		try {
			const fileData = await fileToBase64(selectedFile);

			const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
			const wsUrl = `${wsProtocol}//${window.location.host}/api/autoflow/ws`;
			const ws = new WebSocket(wsUrl);

			ws.onopen = () => {
				ws.send(JSON.stringify({
					file_data: fileData,
					filename: selectedFile!.name,
					template_key: templateKey,
					diarization_mode: diarizationMode,
					denoise: denoiseMode,
					include_summary: true,
					include_mindmap: true,
					include_insights: includeInsights,
				}));
			};

			ws.onmessage = (event) => {
				const msg: ProgressMessage = JSON.parse(event.data);

				if (msg.stage === 'error') {
					error = msg.message || 'Неизвестная ошибка';
					loading = false;
					ws.close();
					return;
				}

				if (msg.stage === 'complete') {
					result = msg.result || null;
					loading = false;
					progressValue = 1;
					progressMessage = 'Готово!';
					ws.close();
					return;
				}

				currentStage = msg.stage;
				progressValue = msg.progress ?? 0;
				progressMessage = msg.message || '';
			};

			ws.onclose = (event) => {
				if (loading && !result) {
					if (event.code !== 1000) {
						disconnected = true;
					}
					loading = false;
				}
			};

			ws.onerror = () => {
				if (loading) {
					disconnected = true;
					loading = false;
				}
			};
		} catch (e: any) {
			error = e?.message || 'Ошибка подключения';
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

	async function downloadExport(format: string, data: any, filename: string): Promise<void> {
		try {
			const res = await fetch('/api/export', {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				credentials: 'include',
				body: JSON.stringify({
				data,
				format,
				filename,
				speaker_names: Object.keys(speakerNames).length > 0 ? speakerNames : undefined
			})
			});

			if (!res.ok) throw new Error('Экспорт не удался');

			const blob = await res.blob();
			const extMap: Record<string, string> = {
				txt: '.txt', json: '.json', srt: '.srt',
				vtt: '.vtt', docx: '.docx', pdf: '.pdf'
			};
			const fullName = `${filename}${extMap[format] || '.txt'}`;

			const url = URL.createObjectURL(blob);
			const a = document.createElement('a');
			a.href = url;
			a.download = fullName;
			document.body.appendChild(a);
			a.click();
			document.body.removeChild(a);
			URL.revokeObjectURL(url);
		} catch (e: any) {
			error = e?.message || 'Ошибка экспорта';
		}
	}

	async function downloadText(content: string, filename: string): Promise<void> {
		const blob = new Blob([content], { type: 'text/plain;charset=utf-8' });
		const url = URL.createObjectURL(blob);
		const a = document.createElement('a');
		a.href = url;
		a.download = filename;
		document.body.appendChild(a);
		a.click();
		document.body.removeChild(a);
		URL.revokeObjectURL(url);
	}
</script>

<div class="container page">
	<h1>Автопайплайн</h1>

	<section class="card upload-section">
		<h3>Аудио или видео файл</h3>
		<AudioUploader onfile={onFileSelected} />

		<div class="settings-row">
			<div class="field">
				<label for="template">Шаблон саммари</label>
				<select id="template" class="input" bind:value={templateKey}>
					{#each templates as t}
						<option value={t.slug}>{t.emoji} {t.name}</option>
					{/each}
					{#if templates.length === 0}
						<option value="meeting">📋 Встреча</option>
						<option value="lecture">📚 Лекция</option>
						<option value="interview">🎙️ Интервью</option>
						<option value="custom">✏️ Свой</option>
					{/if}
				</select>
			</div>
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
			<div class="field checkbox-field">
				<label class="checkbox-label">
					<input type="checkbox" bind:checked={includeInsights} />
					<span>📋 Извлечь инсайты</span>
				</label>
			</div>
		</div>

		<button
			class="btn btn-primary autoflow-btn"
			onclick={startAutoflow}
			disabled={!selectedFile || loading}
		>
			{#if loading}
				<span class="spinner"></span>
				Обработка…
			{:else}
				Запустить автопайплайн
			{/if}
		</button>
	</section>

	{#if error}
		<div class="error-banner">{error}</div>
	{/if}

	{#if disconnected}
		<div class="disconnect-banner">
			<span>⚠️ Соединение потеряно</span>
			<button class="btn btn-secondary" onclick={startAutoflow} disabled={!selectedFile}>
				Переподключить
			</button>
		</div>
	{/if}

	{#if loading}
		<section class="card progress-section">
			<div class="stages-row">
				{#each STAGES as stage, i}
					<div class="stage-item" class:active={currentStage === stage.key} class:done={progressValue > (i + 1) / 3}>
						<span class="stage-icon">{stage.icon}</span>
						<span class="stage-label">{stage.label}</span>
					</div>
					{#if i < STAGES.length - 1}
						<div class="stage-connector" class:filled={progressValue > (i + 1) / 3}></div>
					{/if}
				{/each}
			</div>

			<div class="progress-bar-track">
				<div class="progress-bar-fill" style="width: {progressValue * 100}%"></div>
			</div>

			{#if progressMessage}
				<p class="progress-message">{progressMessage}</p>
			{/if}
		</section>
	{/if}

	{#if result}
		<section class="card results-section">
			<div class="results-header">
				<h2>Результаты</h2>
				{#if result.stage_timings}
					<div class="info-bar">
						{#each Object.entries(result.stage_timings) as [key, val]}
							<span class="badge info-badge">{key}: {formatTiming(val)}</span>
						{/each}
					</div>
				{/if}
			</div>

			{#if result.errors.length > 0}
				<div class="error-banner">
					{#each result.errors as err}
						<div>{err}</div>
					{/each}
				</div>
			{/if}

			<div class="tabs">
				<button
					class="tab"
					class:active={activeSection === 'transcription'}
					onclick={() => (activeSection = 'transcription')}
					disabled={!result.transcription}
				>
					<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" class="btn-icon"><path stroke-linecap="round" stroke-linejoin="round" d="M12 18.75a6 6 0 006-6v-1.5m-6 7.5a6 6 0 01-6-6v-1.5m6 7.5v3.75m-3.75 0h7.5M12 15.75a3 3 0 01-3-3V4.5a3 3 0 116 0v8.25a3 3 0 01-3 3z"/></svg>Транскрипция
				</button>
				<button
					class="tab"
					class:active={activeSection === 'summary'}
					onclick={() => (activeSection = 'summary')}
					disabled={!result.summary}
				>
					<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" class="btn-icon"><path stroke-linecap="round" stroke-linejoin="round" d="M19.5 14.25v-2.625a3.375 3.375 0 00-3.375-3.375h-1.5A1.125 1.125 0 0113.5 7.125v-1.5a3.375 3.375 0 00-3.375-3.375H8.25m0 12.75h7.5m-7.5 3H12M10.5 2.25H5.625c-.621 0-1.125.504-1.125 1.125v17.25c0 .621.504 1.125 1.125 1.125h12.75c.621 0 1.125-.504 1.125-1.125V11.25a9 9 0 00-9-9z"/></svg>Саммари
				</button>
				<button
					class="tab"
					class:active={activeSection === 'mindmap'}
					onclick={() => (activeSection = 'mindmap')}
					disabled={!result.mindmap_html}
				>
					<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" class="btn-icon"><path stroke-linecap="round" stroke-linejoin="round" d="M9 6.75V15m6-6v8.25m.503 3.498l4.875-2.437c.381-.19.622-.58.622-1.006V4.82c0-.836-.88-1.38-1.628-1.006l-3.869 1.934c-.317.159-.69.159-1.006 0L9.503 3.252a1.125 1.125 0 00-1.006 0L3.622 5.689C3.24 5.88 3 6.27 3 6.695V19.18c0 .836.88 1.38 1.628 1.006l3.869-1.934c.317-.159.69-.159 1.006 0l4.994 2.497c.317.158.69.158 1.006 0z"/></svg>Майндмэп
				</button>
				<button
					class="tab"
					class:active={activeSection === 'insights'}
					onclick={() => (activeSection = 'insights')}
					disabled={!result.action_items && !result.suggested_steps}
				>
					<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" class="btn-icon"><path stroke-linecap="round" stroke-linejoin="round" d="M9 12h3.75M9 15h3.75M9 18h3.75m3 .75H18a2.25 2.25 0 002.25-2.25V6.108c0-1.135-.845-2.098-1.976-2.192a48.424 48.424 0 00-1.123-.08m-5.801 0c-.065.21-.1.433-.1.664 0 .414.336.75.75.75h4.5a.75.75 0 00.75-.75 2.25 2.25 0 00-.1-.664m-5.8 0A2.251 2.251 0 0113.5 2.25H15c1.012 0 1.867.668 2.15 1.586m-5.8 0c-.376.023-.75.05-1.124.08C9.095 4.01 8.25 4.973 8.25 6.108V8.25m0 0H4.875c-.621 0-1.125.504-1.125 1.125v11.25c0 .621.504 1.125 1.125 1.125h9.75c.621 0 1.125-.504 1.125-1.125V9.375c0-.621-.504-1.125-1.125-1.125H8.25zM6.75 12h.008v.008H6.75V12zm0 3h.008v.008H6.75V15zm0 3h.008v.008H6.75V18z"/></svg>Инсайты
				</button>
			</div>

			{#if activeSection === 'transcription' && result.transcription}
				{@const tr = result.transcription}
				<div class="section-content">
					<div class="info-bar" style="margin-bottom: 0.75rem;">
						<span class="badge info-badge">{formatDuration(tr.duration)}</span>
						<span class="badge info-badge">{tr.language}</span>
						<span class="badge info-badge">{tr.segments.length} сегментов</span>
						{#if (tr.text?.length ?? 0) > 30000}
							<span class="badge long-transcript-badge">📏 Длинная транскрипция — используется поэтапная обработка</span>
						{/if}
					</div>
					<div class="segments-list">
						{#each tr.segments as seg}
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
				</div>
			{/if}

			{#if activeSection === 'summary' && result.summary}
				<div class="section-content">
					<div class="summary-content">
						{@html result.summary.replace(/\n/g, '<br>')}
					</div>
				</div>
			{/if}

			{#if activeSection === 'mindmap' && result.mindmap_html}
				<div class="section-content">
					<div class="mindmap-wrapper">
						{@html result.mindmap_html}
					</div>
				</div>
			{/if}

			{#if activeSection === 'insights' && (result.action_items || result.suggested_steps)}
				<div class="section-content">
					{#if result.action_items?.action_items && result.action_items.action_items.length > 0}
						<div class="insights-block">
							<h4>✅ Задачи</h4>
							<ul class="insights-list">
								{#each result.action_items.action_items as item}
									<li>
										<span class="badge" style="background: {item.priority === 'high' ? '#dc262620' : item.priority === 'medium' ? '#d9770620' : '#9ca3af20'}; color: {item.priority === 'high' ? '#dc2626' : item.priority === 'medium' ? '#d97706' : '#9ca3af'}">{item.priority === 'high' ? 'Высокий' : item.priority === 'medium' ? 'Средний' : 'Низкий'}</span>
										<span>{item.task}</span>
										{#if item.assignee}<span class="badge info-badge">👤 {item.assignee}</span>{/if}
										{#if item.deadline}<span class="badge info-badge">📅 {item.deadline}</span>{/if}
									</li>
								{/each}
							</ul>
						</div>
					{/if}

					{#if result.action_items?.decisions && result.action_items.decisions.length > 0}
						<div class="insights-block">
							<h4>📌 Решения</h4>
							<ul class="insights-list">
								{#each result.action_items.decisions as d}
									<li>
										<strong>{d.decision}</strong>
										<span class="decision-ctx">{d.context}</span>
									</li>
								{/each}
							</ul>
						</div>
					{/if}

					{#if result.suggested_steps?.suggested_steps && result.suggested_steps.suggested_steps.length > 0}
						<div class="insights-block">
							<h4>💡 Рекомендуемые шаги</h4>
							<ol class="insights-list numbered">
								{#each result.suggested_steps.suggested_steps as step}
									<li>
										<span class="badge" style="background: {step.category === 'followup' ? '#2563eb20' : step.category === 'research' ? '#7c3aed20' : step.category === 'communication' ? '#16a34a20' : '#ea580c20'}; color: {step.category === 'followup' ? '#2563eb' : step.category === 'research' ? '#7c3aed' : step.category === 'communication' ? '#16a34a' : '#ea580c'}">{step.category === 'followup' ? 'Фоллоу-ап' : step.category === 'research' ? 'Исследование' : step.category === 'communication' ? 'Коммуникация' : 'Планирование'}</span>
										<strong>{step.step}</strong>
										<span class="step-reason">{step.reason}</span>
									</li>
								{/each}
							</ol>
						</div>
					{/if}
				</div>
			{/if}
		</section>

		<section class="card export-section">
			<h3>Экспорт</h3>

			{#if result.transcription}
				<div class="export-group">
					<span class="export-label">Транскрипция</span>
					<div class="export-buttons">
						<button class="btn btn-secondary" onclick={() => downloadExport('txt', result!.transcription, 'autoflow-transcription')}>TXT</button>
						<button class="btn btn-secondary" onclick={() => downloadExport('json', result!.transcription, 'autoflow-transcription')}>JSON</button>
						<button class="btn btn-secondary" onclick={() => downloadExport('srt', result!.transcription, 'autoflow-transcription')}>SRT</button>
					</div>
				</div>
			{/if}

			{#if result.summary}
				<div class="export-group">
					<span class="export-label">Саммари</span>
					<div class="export-buttons">
						<button class="btn btn-secondary" onclick={() => downloadText(result!.summary!, 'autoflow-summary.txt')}>TXT</button>
						<button class="btn btn-secondary" onclick={() => downloadExport('docx', { text: result!.summary }, 'autoflow-summary')}>DOCX</button>
						<button class="btn btn-secondary" onclick={() => downloadExport('pdf', { text: result!.summary }, 'autoflow-summary')}>PDF</button>
					</div>
				</div>
			{/if}

			{#if result.mindmap_md}
				<div class="export-group">
					<span class="export-label">Майндмэп</span>
					<div class="export-buttons">
						<button class="btn btn-secondary" onclick={() => downloadText(result!.mindmap_md!, 'autoflow-mindmap.md')}>Markdown</button>
					</div>
				</div>
			{/if}
		</section>
	{/if}
</div>

<style>
	.btn-icon {
		width: 18px;
		height: 18px;
		display: inline-block;
		vertical-align: middle;
		margin-right: 6px;
	}

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

	.autoflow-btn {
		align-self: flex-start;
		min-width: 220px;
		height: 42px;
		font-size: 0.9375rem;
		background-color: var(--color-cta);
		border-color: var(--color-cta);
	}

	.autoflow-btn:hover:not(:disabled) {
		filter: brightness(1.1);
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

	.disconnect-banner {
		display: flex;
		align-items: center;
		justify-content: space-between;
		background-color: var(--color-error-bg);
		border: 1px solid var(--color-error-border);
		color: var(--color-error-text);
		border-radius: var(--radius-sm);
		padding: 0.75rem 1rem;
		margin-bottom: 1rem;
		font-size: 0.875rem;
	}

	.progress-section {
		margin-bottom: 1rem;
	}

	.stages-row {
		display: flex;
		align-items: center;
		justify-content: center;
		gap: 0;
		margin-bottom: 1.25rem;
	}

	.stage-item {
		display: flex;
		flex-direction: column;
		align-items: center;
		gap: 0.375rem;
		padding: 0.5rem 0.75rem;
		border-radius: var(--radius-sm);
		opacity: 0.45;
		transition: opacity 0.3s, background-color 0.3s;
	}

	.stage-item.active {
		opacity: 1;
		background-color: rgba(33, 160, 56, 0.08);
	}

	.stage-item.done {
		opacity: 0.75;
	}

	.stage-icon {
		font-size: 1.5rem;
	}

	.stage-label {
		font-size: 0.8125rem;
		font-weight: 500;
		color: var(--color-text);
	}

	.stage-connector {
		width: 48px;
		height: 2px;
		background-color: var(--color-border);
		margin: 0 0.25rem;
		margin-bottom: 1.25rem;
		transition: background-color 0.3s;
	}

	.stage-connector.filled {
		background-color: var(--color-cta);
	}

	.progress-bar-track {
		width: 100%;
		height: 6px;
		background-color: var(--color-bg);
		border-radius: 3px;
		overflow: hidden;
		margin-bottom: 0.75rem;
	}

	.progress-bar-fill {
		height: 100%;
		background-color: var(--color-cta);
		border-radius: 3px;
		transition: width 0.3s ease;
	}

	.progress-message {
		text-align: center;
		font-size: 0.875rem;
		color: var(--color-muted);
		margin: 0;
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

	.long-transcript-badge {
		background-color: #eff6ff;
		color: #1d4ed8;
		border: 1px solid #bfdbfe;
	}

	.badge {
		display: inline-block;
		padding: 0.125rem 0.5rem;
		border-radius: 999px;
		font-size: 0.6875rem;
		font-weight: 500;
		white-space: nowrap;
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

	.tab:hover:not(:disabled) {
		color: var(--color-text);
	}

	.tab.active {
		color: var(--color-cta);
		border-bottom-color: var(--color-cta);
	}

	.tab:disabled {
		opacity: 0.4;
		cursor: not-allowed;
	}

	.section-content {
		min-height: 100px;
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

	.summary-content {
		background-color: var(--color-bg);
		border-radius: var(--radius-sm);
		padding: 1rem;
		font-size: 0.9375rem;
		line-height: 1.7;
		max-height: 500px;
		overflow-y: auto;
	}

	.mindmap-wrapper {
		background-color: var(--color-bg);
		border-radius: var(--radius-sm);
		padding: 1rem;
		max-height: 600px;
		overflow-y: auto;
	}

	.export-section {
		display: flex;
		flex-direction: column;
		gap: 1rem;
	}

	.export-section h3 {
		margin: 0;
	}

	.export-group {
		display: flex;
		align-items: center;
		gap: 1rem;
		flex-wrap: wrap;
	}

	.export-label {
		font-size: 0.875rem;
		font-weight: 500;
		color: var(--color-muted);
		min-width: 120px;
	}

	.export-buttons {
		display: flex;
		gap: 0.5rem;
		flex-wrap: wrap;
	}

	.error-banner {
		background-color: var(--color-error-bg);
		border: 1px solid var(--color-error-border);
		color: var(--color-error-text);
		border-radius: var(--radius-sm);
		padding: 0.75rem 1rem;
		font-size: 0.875rem;
		margin-bottom: 1rem;
	}

	.checkbox-field {
		display: flex;
		align-items: flex-end;
		min-width: auto;
	}

	.checkbox-label {
		display: flex;
		align-items: center;
		gap: 0.375rem;
		font-size: 0.875rem;
		cursor: pointer;
	}

	.insights-block {
		margin-bottom: 1.25rem;
	}

	.insights-block h4 {
		margin: 0 0 0.5rem;
		font-size: 0.875rem;
	}

	.insights-list {
		list-style: none;
		padding: 0;
		margin: 0;
	}

	.insights-list.numbered {
		list-style: decimal;
		padding-left: 1.5rem;
	}

	.insights-list li {
		padding: 0.5rem 0;
		border-bottom: 1px solid var(--color-border);
		display: flex;
		align-items: baseline;
		gap: 0.5rem;
		flex-wrap: wrap;
	}

	.insights-list li:last-child {
		border-bottom: none;
	}

	.decision-ctx {
		font-size: 0.8125rem;
		color: var(--color-muted);
		width: 100%;
	}

	.step-reason {
		font-size: 0.8125rem;
		color: var(--color-muted);
		width: 100%;
	}
</style>
