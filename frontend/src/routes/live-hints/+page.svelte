<script lang="ts">
	import { authStore } from '$lib/stores/auth';
	import { fetchApi } from '$lib/services/api';
	import { LiveHintsClient } from '$lib/services/live-hints-api';
	import LiveAudioCapture from '$lib/components/LiveAudioCapture.svelte';
	import TranscriptView from '$lib/components/TranscriptView.svelte';
	import SpeakingStatsBar from '$lib/components/SpeakingStatsBar.svelte';

	interface TranscriptSegment {
		text: string;
		speaker: string;
		timestamp: number;
	}

	let transcriptSegments: TranscriptSegment[] = $state([]);
	let sessionActive = $state(false);
	let wsConnected = $state(false);
	let sessionStatus: 'idle' | 'connecting' | 'active' | 'error' | 'reconnecting' | 'connection_lost' = $state('idle');
	let wsClient: LiveHintsClient | null = $state(null);
	let transcriptContainer: HTMLDivElement | undefined = $state();
	let errorMessage = $state('');
	let errorTimer: ReturnType<typeof setTimeout> | undefined;
	let backpressure = $state({ dropped: 0, invalid: 0, recv: 0, process: 0, recvCap: 0, processCap: 0 });
	let copyState = $state<'idle' | 'ok'>('idle');
	let saveState = $state<'idle' | 'saving' | 'saved' | 'error'>('idle');
	let recordingPaused = $state(false);
	let sessionSeconds = $state(0);
	let sessionTimer: ReturnType<typeof setInterval> | undefined;

	const backpressureActive = $derived(
		backpressure.dropped > 0 ||
			backpressure.invalid > 0 ||
			(backpressure.recvCap > 0 && backpressure.recv / backpressure.recvCap >= 0.8) ||
			(backpressure.processCap > 0 && backpressure.process / backpressure.processCap >= 0.8)
	);

	const totalWords = $derived(
		transcriptSegments.reduce((acc, s) => acc + s.text.trim().split(/\s+/).filter(Boolean).length, 0)
	);
	const wordsPerMinute = $derived(
		sessionSeconds > 0 ? Math.round((totalWords / sessionSeconds) * 60) : 0
	);

	function formatSessionDuration(total: number): string {
		const h = Math.floor(total / 3600);
		const m = Math.floor((total % 3600) / 60);
		const s = total % 60;
		const mm = String(m).padStart(2, '0');
		const ss = String(s).padStart(2, '0');
		return h > 0 ? `${h}:${mm}:${ss}` : `${mm}:${ss}`;
	}

	function fullTranscriptText(): string {
		return transcriptSegments.map((s) => `[${s.speaker}] ${s.text}`).join('\n');
	}

	async function copyTranscript(): Promise<void> {
		const text = fullTranscriptText();
		if (!text) return;
		try {
			await navigator.clipboard.writeText(text);
			copyState = 'ok';
			setTimeout(() => (copyState = 'idle'), 2000);
		} catch {
			/* clipboard unavailable */
		}
	}

	async function saveTranscript(): Promise<void> {
		if (saveState === 'saving' || transcriptSegments.length === 0) return;
		saveState = 'saving';
		try {
			await fetchApi('POST', '/api/saved-transcriptions', {
				title: `Live ${new Date().toLocaleString('ru-RU')}`,
				full_text: transcriptSegments.map((s) => `${s.speaker}: ${s.text}`).join('\n'),
				segments: transcriptSegments,
				speaker_names: {},
				duration: sessionSeconds,
				language: 'ru'
			});
			saveState = 'saved';
			setTimeout(() => (saveState = 'idle'), 3000);
		} catch (e) {
			console.error('Failed to save transcript:', e);
			saveState = 'error';
			setTimeout(() => (saveState = 'idle'), 3000);
		}
	}

	function togglePause(): void {
		recordingPaused = !recordingPaused;
	}

	$effect(() => {
		transcriptSegments;
		if (transcriptContainer) {
			const list = transcriptContainer.querySelector('.transcript-list');
			if (list) {
				list.scrollTop = list.scrollHeight;
			}
		}
	});

	$effect(() => {
		return () => {
			if (wsClient) {
				wsClient.disconnect();
				wsClient = null;
			}
			if (sessionTimer) clearInterval(sessionTimer);
			sessionActive = false;
			wsConnected = false;
			recordingPaused = false;
			if (errorTimer) clearTimeout(errorTimer);
		};
	});

	function getToken(): string {
		let token = '';
		authStore.subscribe((s) => (token = s.accessToken))();
		return token;
	}

	async function startSession(): Promise<void> {
		sessionStatus = 'connecting';
		errorMessage = '';
		try {
			const token = getToken();
			const client = new LiveHintsClient();

			client.onTranscript = (segment) => {
				transcriptSegments = [...transcriptSegments, segment as TranscriptSegment];
			};

			client.onError = (error) => {
				console.error('Live mode error:', error);
				errorMessage = String(error.message || 'Произошла ошибка');
				sessionStatus = 'error';
				if (errorTimer) clearTimeout(errorTimer);
				errorTimer = setTimeout(() => {
					errorMessage = '';
				}, 5000);
			};

			client.onStatus = (data) => {
				const status = data.status;
				if (status === 'ready') {
					sessionStatus = 'active';
					wsConnected = true;
				}
				const queues = data.queues as { recv?: number; process?: number } | undefined;
				if (queues) {
					const caps = (data.caps ?? {}) as { recv_queue?: number; process_queue?: number };
					backpressure = {
						dropped: Number(data.dropped ?? 0),
						invalid: Number(data.invalid_chunks ?? 0),
						recv: Number(queues.recv ?? 0),
						process: Number(queues.process ?? 0),
						recvCap: Number(caps.recv_queue ?? 0),
						processCap: Number(caps.process_queue ?? 0)
					};
				}
			};

			client.onReconnecting = () => {
				sessionStatus = 'reconnecting';
			};

			client.onReconnectFailed = () => {
				sessionStatus = 'connection_lost';
				errorMessage = 'Соединение потеряно';
			};

			await client.connect(token);
			// Live-режим: транскрипция без генерации подсказок
			client.sendConfig('meeting', '', false);

			wsClient = client;
			sessionActive = true;
			recordingPaused = false;
			sessionSeconds = 0;
			sessionTimer = setInterval(() => {
				if (!recordingPaused) sessionSeconds++;
			}, 1000);
		} catch (err) {
			console.error('Failed to start session:', err);
			sessionStatus = 'error';
			errorMessage = 'Не удалось подключиться';
		}
	}

	function stopSession(): void {
		if (wsClient) {
			wsClient.disconnect();
			wsClient = null;
		}
		if (sessionTimer) {
			clearInterval(sessionTimer);
			sessionTimer = undefined;
		}
		sessionActive = false;
		wsConnected = false;
		recordingPaused = false;
		sessionStatus = 'idle';
		errorMessage = '';
		if (errorTimer) clearTimeout(errorTimer);
	}

	function toggleSession(): void {
		if (sessionActive) {
			stopSession();
		} else {
			startSession();
		}
	}

	function handleAudioChunk(audio: ArrayBuffer, source: 'mic' | 'tab'): void {
		if (wsClient && !recordingPaused) {
			wsClient.sendAudioChunk(audio, source);
		}
	}

	function handleKeydown(e: KeyboardEvent): void {
		if (e.target instanceof HTMLElement && ['INPUT', 'TEXTAREA', 'SELECT'].includes(e.target.tagName)) return;
		if (e.code === 'Space' && sessionActive) {
			e.preventDefault();
			togglePause();
		} else if (e.code === 'Escape' && sessionActive && !recordingPaused) {
			e.preventDefault();
			stopSession();
		}
	}

	$effect(() => {
		window.addEventListener('keydown', handleKeydown);
		return () => window.removeEventListener('keydown', handleKeydown);
	});
</script>

<div class="live-mode-page">
	<header class="session-header">
		<h1>Live Режим</h1>
		<p class="page-subtitle">Транскрипция в реальном времени — микрофон или звук устройства, без выбора режима</p>

		<div class="header-controls">
			<div class="session-actions">
				<span class="status-badge" class:connected={wsConnected} class:error={sessionStatus === 'error'} class:reconnecting={sessionStatus === 'reconnecting'} class:connection-lost={sessionStatus === 'connection_lost'}>
					{#if sessionStatus === 'idle'}
						Ожидание
					{:else if sessionStatus === 'connecting'}
						Подключение…
					{:else if sessionStatus === 'active'}
						Активно
					{:else if sessionStatus === 'reconnecting'}
						Переподключение…
					{:else if sessionStatus === 'connection_lost'}
						Соединение потеряно
					{:else}
						Ошибка
					{/if}
				</span>
				{#if backpressureActive}
					<span
						class="status-badge bp-badge"
						title={`Очереди: ${backpressure.recv}/${backpressure.recvCap} · обработка: ${backpressure.process}/${backpressure.processCap} · отброшено чанков: ${backpressure.dropped} · некорректных: ${backpressure.invalid}`}
					>
						⚠ {backpressure.dropped > 0 ? `отброшено: ${backpressure.dropped}` : 'очередь на пределе'}
					</span>
				{/if}
				{#if sessionActive}
					<span class="status-badge" class:paused={recordingPaused}>⏱ {formatSessionDuration(sessionSeconds)}</span>
					<span class="status-badge">💬 {transcriptSegments.length}</span>
					<span class="status-badge">📝 {totalWords} слов · {wordsPerMinute} сл/мин</span>
					<button class="btn btn-secondary btn-pause" onclick={togglePause} title="Space">
						{recordingPaused ? '▶ Продолжить' : '⏸ Пауза'}
					</button>
				{/if}
				<button
					class="btn btn-primary session-btn"
					onclick={toggleSession}
					disabled={sessionStatus === 'connecting'}
				>
					{#if sessionActive}
						Остановить
					{:else}
						Начать сессию
					{/if}
				</button>
			</div>
		</div>
	</header>

	{#if errorMessage}
		<div class="error-banner">{errorMessage}</div>
	{/if}

	{#if sessionActive}
		<LiveAudioCapture onchunk={handleAudioChunk} />
	{/if}

	<div class="session-content">
		<section class="column transcript-column">
			<div class="column-head">
				<h2 class="column-title">Транскрипция</h2>
				{#if transcriptSegments.length > 0}
					<div class="transcript-actions">
						<button class="btn btn-secondary btn-sm" onclick={copyTranscript} disabled={copyState === 'ok'}>
							{copyState === 'ok' ? '✓ Скопировано' : '⧉ Копировать'}
						</button>
						<button
							class="btn btn-secondary btn-sm"
							onclick={saveTranscript}
							disabled={saveState === 'saving' || saveState === 'saved'}
							title="Сохранить текст в Расшифровки (аудио не сохраняется)"
						>
							{saveState === 'saving' ? 'Сохранение…' : saveState === 'saved' ? '✓ Сохранено' : saveState === 'error' ? '✗ Ошибка' : '💾 В Расшифровки'}
						</button>
					</div>
				{/if}
			</div>
			<SpeakingStatsBar segments={transcriptSegments} />
			<div class="column-body" bind:this={transcriptContainer}>
				{#if transcriptSegments.length === 0}
					<div class="empty-state">
						<span class="empty-icon">🎤</span>
						<p>Начните запись, чтобы увидеть транскрипцию</p>
					</div>
				{:else}
					<TranscriptView segments={transcriptSegments} />
				{/if}
			</div>
		</section>
	</div>
</div>

<style>
	.live-mode-page {
		min-height: calc(100vh - var(--header-height) - 2rem);
		padding: 1.5rem 1rem 3rem;
		display: flex;
		flex-direction: column;
		gap: 1rem;
	}

	/* ── Header ── */

	.session-header h1 {
		margin: 0 0 0.25rem;
	}

	.page-subtitle {
		margin: 0 0 1rem;
		font-size: 0.875rem;
		color: var(--color-muted);
	}

	.header-controls {
		display: flex;
		gap: 1rem;
		flex-wrap: wrap;
		align-items: flex-start;
		margin-bottom: 0.5rem;
	}

	.session-actions {
		display: flex;
		align-items: center;
		gap: 0.75rem;
		flex-wrap: wrap;
	}

	.status-badge {
		display: inline-flex;
		align-items: center;
		padding: 0.375rem 0.75rem;
		border-radius: 999px;
		font-size: 0.75rem;
		font-weight: 600;
		background: var(--color-bg);
		color: var(--color-muted);
		border: 1px solid var(--color-border);
		white-space: nowrap;
	}

	.status-badge.connected {
		background: rgba(33, 160, 56, 0.1);
		color: var(--color-cta);
		border-color: var(--color-cta);
	}

	.status-badge.error {
		background: var(--color-error-bg);
		color: var(--color-error-text);
		border-color: var(--color-error-border);
	}

	.status-badge.reconnecting {
		background: rgba(234, 179, 8, 0.1);
		color: #b45309;
		border-color: #f59e0b;
	}

	.status-badge.connection-lost {
		background: var(--color-error-bg);
		color: var(--color-error-text);
		border-color: var(--color-error-border);
	}

	.bp-badge {
		background: rgba(234, 179, 8, 0.1);
		color: #b45309;
		border-color: #f59e0b;
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

	.session-btn {
		min-width: 160px;
		height: 42px;
		font-size: 0.9375rem;
		background-color: var(--color-cta);
		border-color: var(--color-cta);
		white-space: nowrap;
	}

	.session-btn:hover:not(:disabled) {
		filter: brightness(1.1);
	}

	/* ── Single-column layout ── */

	.session-content {
		display: flex;
		flex-direction: column;
		gap: 1rem;
		flex: 1;
		min-height: 0;
	}

	.column {
		display: flex;
		flex-direction: column;
		background: var(--color-card);
		border-radius: var(--radius);
		padding: 1rem;
		border: 1px solid var(--color-border);
		overflow: hidden;
	}

	.column-title {
		margin: 0 0 0.75rem;
		font-size: 1rem;
		font-weight: 600;
		color: var(--color-text);
		flex-shrink: 0;
	}

	.column-head {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 0.75rem;
		flex-wrap: wrap;
		margin-bottom: 0.75rem;
	}

	.column-head .column-title {
		margin: 0;
	}

	.transcript-actions {
		display: flex;
		gap: 0.5rem;
	}

	.btn-pause {
		height: 42px;
		white-space: nowrap;
	}

	.status-badge.paused {
		opacity: 0.6;
	}

	.column-body {
		flex: 1;
		overflow-y: auto;
		min-height: 0;
	}

	/* ── Empty state ── */

	.empty-state {
		display: flex;
		flex-direction: column;
		align-items: center;
		justify-content: center;
		height: 100%;
		min-height: 200px;
		color: var(--color-muted);
		text-align: center;
		gap: 0.75rem;
	}

	.empty-icon {
		font-size: 2.5rem;
		opacity: 0.6;
	}

	.empty-state p {
		margin: 0;
		font-size: 0.875rem;
		max-width: 260px;
		line-height: 1.5;
	}
</style>
