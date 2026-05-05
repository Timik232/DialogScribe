<script lang="ts">
	import { authStore } from '$lib/stores/auth';
	import { fetchApi } from '$lib/services/api';
	import { LiveHintsClient } from '$lib/services/live-hints-api';
	import LiveAudioCapture from '$lib/components/LiveAudioCapture.svelte';
	import MeetingBriefForm from '$lib/components/MeetingBriefForm.svelte';
	import HintPanel from '$lib/components/HintPanel.svelte';
	import TranscriptView from '$lib/components/TranscriptView.svelte';
	import SpeakingStatsBar from '$lib/components/SpeakingStatsBar.svelte';

	interface Template {
		slug: string;
		label: string;
	}

	let templates: Template[] = $state([]);
	let selectedTemplate = $state('');
	let meetingBrief = $state({ goal: 'initial', offering: '', red_lines: '', known_objections: '' });
	let sessionActive = $state(false);
	let wsConnected = $state(false);
	let sessionStatus: 'idle' | 'connecting' | 'active' | 'error' | 'reconnecting' | 'connection_lost' = $state('idle');
	let transcriptSegments: Array<{ text: string; speaker: string; timestamp: number }> = $state([]);
	let hints: Array<{
		hint_type: 'argumentative' | 'navigational' | 'tactical' | 'strategic' | 'warning' | 'analytical';
		text: string;
		priority: 'high' | 'medium' | 'low';
		hint_id?: string;
		rationale?: string;
	}> = $state([]);
	let wsClient: LiveHintsClient | null = $state(null);
	let transcriptContainer: HTMLDivElement | undefined = $state();
	let errorMessage = $state('');
	let errorTimer: ReturnType<typeof setTimeout> | undefined;

	$effect(() => {
		loadTemplates();
	});

	async function loadTemplates(): Promise<void> {
		try {
			templates = await fetchApi<Template[]>('GET', '/api/live-hints/templates');
			if (templates.length > 0 && !selectedTemplate) {
				selectedTemplate = templates[0].slug;
			}
		} catch {
			templates = [];
		}
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
			sessionActive = false;
			wsConnected = false;
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
		try {
			const token = getToken();
			const client = new LiveHintsClient();

			client.onTranscript = (segment) => {
				transcriptSegments = [...transcriptSegments, segment];
			};

			client.onHints = (newHints) => {
				hints = [...hints, ...newHints];
			};

			client.onError = (error) => {
				console.error('Live hints error:', error);
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
			};

			client.onReconnecting = () => {
				sessionStatus = 'reconnecting';
			};

			client.onReconnectFailed = () => {
				sessionStatus = 'connection_lost';
				errorMessage = 'Соединение потеряно';
			};

			await client.connect(token);
			client.sendConfig(selectedTemplate, '');
			client.sendBriefUpdate(meetingBrief);

			wsClient = client;
			sessionActive = true;
		} catch (err) {
			console.error('Failed to start session:', err);
			sessionStatus = 'error';
		}
	}

	function stopSession(): void {
		if (wsClient) {
			wsClient.disconnect();
			wsClient = null;
		}
		sessionActive = false;
		wsConnected = false;
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

	function handleAudioChunk(audio_b64: string, source: 'mic' | 'tab'): void {
		if (wsClient) {
			wsClient.sendAudioChunk(audio_b64, source);
		}
	}

	function handleHintFeedback(hintId: string, rating: 'like' | 'dislike'): void {
		if (wsClient) {
			wsClient.sendHintFeedback(hintId, rating);
		}
	}

	function handleBriefSubmit(brief: { goal: string; offering: string; red_lines: string; known_objections: string }): void {
		meetingBrief = brief;
		if (wsClient && sessionActive) {
			wsClient.sendBriefUpdate(brief);
		}
	}
</script>

<div class="live-hints-page">
	<header class="session-header">
		<h1>Живые подсказки</h1>

		<div class="header-controls">
			<div class="field">
				<label for="template-select">Шаблон</label>
				<select id="template-select" class="input" bind:value={selectedTemplate} disabled={sessionActive}>
					{#if templates.length === 0}
						<option value="" disabled>Загрузка…</option>
					{:else}
						{#each templates as t}
							<option value={t.slug}>{t.label}</option>
						{/each}
					{/if}
				</select>
			</div>

			<div class="field context-field">
				<label>Контекст встречи</label>
				<MeetingBriefForm disabled={sessionActive} onsubmit={handleBriefSubmit} />
			</div>

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
			<h2 class="column-title">Транскрипция</h2>
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

		<section class="column hints-column">
			<h2 class="column-title">Подсказки</h2>
			<div class="column-body">
				{#if hints.length === 0}
					<div class="empty-state">
						<span class="empty-icon">💡</span>
						<p>Подсказки появятся во время активной сессии</p>
					</div>
				{:else}
					<HintPanel {hints} onhintfeedback={handleHintFeedback} />
				{/if}
			</div>
		</section>
	</div>
</div>

<style>
	.live-hints-page {
		min-height: calc(100vh - var(--header-height) - 2rem);
		padding: 1.5rem 1rem 3rem;
		display: flex;
		flex-direction: column;
		gap: 1rem;
	}

	/* ── Header ── */

	.session-header h1 {
		margin: 0 0 1rem;
	}

	.header-controls {
		display: flex;
		gap: 1rem;
		flex-wrap: wrap;
		align-items: flex-start;
		margin-bottom: 0.5rem;
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

	.context-field {
		flex: 2;
	}

	.session-actions {
		display: flex;
		align-items: flex-end;
		gap: 0.75rem;
		align-self: flex-start;
		padding-top: 1.375rem;
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

	/* ── Two-column layout ── */

	.session-content {
		display: grid;
		grid-template-columns: 1fr 400px;
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

	/* ── Responsive ── */

	@media (max-width: 768px) {
		.session-content {
			grid-template-columns: 1fr;
		}

		.header-controls {
			flex-direction: column;
		}

		.session-actions {
			padding-top: 0;
		}
	}
</style>
