<script lang="ts">
	import MicSelector from './MicSelector.svelte';

	let {
		onstart,
		onstop,
		onchunk
	}: {
		onstart?: () => void;
		onstop?: () => void;
		onchunk?: (audio: ArrayBuffer, source: 'mic' | 'tab') => void;
	} = $props();

	let micEnabled = $state(true);
	let tabAudioEnabled = $state(false);

	let recording = $state(false);
	let seconds = $state(0);
	let status = $state<'idle' | 'recording' | 'error'>('idle');
	let errorMessage = $state('');

	let micStream: MediaStream | null = null;
	let tabStream: MediaStream | null = null;
	let micRecorder: MediaRecorder | null = null;
	let tabRecorder: MediaRecorder | null = null;
	let timerInterval: ReturnType<typeof setInterval> | null = null;
	let micSendTimer: ReturnType<typeof setInterval> | null = null;
	let tabSendTimer: ReturnType<typeof setInterval> | null = null;
	let selectedMicId = $state("");

	function formatDuration(total: number): string {
		const h = Math.floor(total / 3600);
		const m = Math.floor((total % 3600) / 60);
		const s = Math.floor(total % 60);
		const mm = String(m).padStart(2, '0');
		const ss = String(s).padStart(2, '0');
		if (h > 0) {
			return `${String(h).padStart(2, '0')}:${mm}:${ss}`;
		}
		return `${mm}:${ss}`;
	}

	function createRecorder(
		stream: MediaStream,
		source: 'mic' | 'tab'
	): { recorder: MediaRecorder; sendTimer: ReturnType<typeof setInterval> } {
		let mimeType = 'audio/webm;codecs=opus';
		if (!MediaRecorder.isTypeSupported(mimeType)) {
			mimeType = 'audio/webm';
		}

		const recorder = new MediaRecorder(stream, { mimeType });

		let headerOnly: Blob | null = null;
		let pendingBlobs: Blob[] = [];
		// Последний кластер держим в резерве: следующий чанк включает его
		// ПЕРЕД новыми кластерами — перекрытие ~1.5-3с, чтобы слова на стыке
		// чанков не резались. Сервер вычитает дубль (_dedup_overlap).
		let lastCluster: Blob | null = null;

		// WebM Cluster ID (0x1F43B675) — граница, после которой в первом блобе
		// начинается аудио. Заголовок до неё (EBML/Segment/Tracks) нужен ffmpeg
		// для декодирования каждого следующего кластера.
		function splitHeader(buffer: ArrayBuffer): Blob {
			const bytes = new Uint8Array(buffer);
			for (let i = 0; i + 4 <= bytes.length; i++) {
				if (bytes[i] === 0x1f && bytes[i + 1] === 0x43 && bytes[i + 2] === 0xb6 && bytes[i + 3] === 0x75) {
					return new Blob([bytes.slice(0, i)], { type: mimeType });
				}
			}
			return new Blob([bytes], { type: mimeType });
		}

		function sendBlob(blob: Blob): void {
			if (blob.size < 100) return;
			blob.arrayBuffer().then((buffer) => {
				onchunk?.(buffer, source);
			});
		}

		function sendAccumulated(): void {
			if (pendingBlobs.length === 0 || !headerOnly) return;
			// Перекрытие: [header][последний кластер прошлого чанка][новые кластеры]
			const parts: Blob[] = [headerOnly];
			if (lastCluster) parts.push(lastCluster);
			parts.push(...pendingBlobs);
			lastCluster = pendingBlobs[pendingBlobs.length - 1];
			pendingBlobs = [];
			sendBlob(new Blob(parts, { type: mimeType }));
		}

		recorder.ondataavailable = (e: BlobEvent) => {
			if (e.data.size <= 0) return;
			if (!headerOnly) {
				// Первый блоб = заголовок + первые ~3с аудио. Отправляем его целиком
				// (транскрибируются один раз), а в headerOnly оставляем только
				// заголовок без первого кластера — иначе первые ~3с приклеивались
				// бы к каждому чанку и транскрибировались заново (дубли фраз).
				e.data.arrayBuffer().then((buffer) => {
					headerOnly = splitHeader(buffer);
				});
				sendBlob(e.data);
				return;
			}
			pendingBlobs.push(e.data);
			sendAccumulated();
		};

		recorder.onstop = () => {
			sendAccumulated();
			headerOnly = null;
			lastCluster = null;
			pendingBlobs = [];
		};

		// Страховочный сброс (обычно pending уже пуст — отправка идёт сразу
		// в ondataavailable, latency = 3с timeslice + ASR).
		const sendTimer = setInterval(sendAccumulated, 3_000);
		recorder.start(3000);
		return { recorder, sendTimer };
	}

	async function startRecording(): Promise<void> {
		errorMessage = '';
		status = 'idle';

		try {
			if (micEnabled) {
				const micConstraints: boolean | MediaTrackConstraints = selectedMicId
					? { deviceId: { exact: selectedMicId } }
					: true;
				micStream = await navigator.mediaDevices.getUserMedia({ audio: micConstraints });
				const mic = createRecorder(micStream, 'mic');
				micRecorder = mic.recorder;
				micSendTimer = mic.sendTimer;
			}

			if (tabAudioEnabled) {
				const rawStream = await navigator.mediaDevices.getDisplayMedia({
					audio: true,
					video: true  // required by browsers to show picker
				});
				// Extract audio tracks BEFORE stopping video tracks
				const audioTracks = rawStream.getAudioTracks();
				if (audioTracks.length === 0) {
					rawStream.getTracks().forEach(t => t.stop());
					throw new DOMException('No audio track captured', 'NotAllowedError');
				}
				tabStream = new MediaStream(audioTracks);
				rawStream.getVideoTracks().forEach(t => t.stop());
				const tab = createRecorder(tabStream, 'tab');
				tabRecorder = tab.recorder;
				tabSendTimer = tab.sendTimer;
			}

			if (!micEnabled && !tabAudioEnabled) {
				status = 'error';
				errorMessage = 'Включите хотя бы один источник аудио';
				return;
			}

			recording = true;
			status = 'recording';
			seconds = 0;
			timerInterval = setInterval(() => {
				seconds++;
			}, 1000);
			onstart?.();
		} catch (err: unknown) {
			cleanupStreams();
			status = 'error';
			if (err instanceof DOMException && err.message === 'No audio track captured') {
				errorMessage = 'Не удалось захватить звук устройства. Попробуйте выбрать вкладку.';
			} else if (err instanceof DOMException && err.name === 'NotAllowedError') {
				errorMessage = 'Разрешение на захват аудио отклонено';
			} else {
				errorMessage = 'Ошибка захвата аудио. Проверьте разрешения.';
			}
		}
	}

	function cleanupStreams(): void {
		if (micSendTimer) { clearInterval(micSendTimer); micSendTimer = null; }
		if (tabSendTimer) { clearInterval(tabSendTimer); tabSendTimer = null; }
		if (micRecorder && micRecorder.state !== 'inactive') {
			micRecorder.stop();
		}
		if (tabRecorder && tabRecorder.state !== 'inactive') {
			tabRecorder.stop();
		}
		micStream?.getTracks().forEach((t) => t.stop());
		tabStream?.getTracks().forEach((t) => t.stop());
		micStream = null;
		tabStream = null;
		micRecorder = null;
		tabRecorder = null;
	}

	function stopRecording(): void {
		cleanupStreams();

		if (timerInterval) {
			clearInterval(timerInterval);
			timerInterval = null;
		}
		seconds = 0;

		recording = false;
		status = 'idle';
		onstop?.();
	}
</script>

<div class="live-audio-capture">
	{#if !recording}
		<button class="btn-start" onclick={startRecording} type="button">
			<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
				<path d="M12 1a4 4 0 0 0-4 4v7a4 4 0 0 0 8 0V5a4 4 0 0 0-4-4z" />
				<path d="M19 10v2a7 7 0 0 1-14 0v-2" />
				<line x1="12" y1="19" x2="12" y2="23" />
				<line x1="8" y1="23" x2="16" y2="23" />
			</svg>
			Начать запись
		</button>
	{:else}
		<div class="recording-indicator">
			<span class="pulse-dot"></span>
			<span class="timer">{formatDuration(seconds)}</span>
			<button class="btn-stop" onclick={stopRecording} type="button">
				<svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor">
					<rect x="6" y="6" width="12" height="12" rx="1" />
				</svg>
				Стоп
			</button>
		</div>
	{/if}

	<div class="source-toggles">
		<label class="toggle" class:active={micEnabled}>
			<input type="checkbox" bind:checked={micEnabled} disabled={recording} />
			<span class="toggle-label">Микрофон</span>
		</label>
		<label class="toggle" class:active={tabAudioEnabled}>
			<input type="checkbox" bind:checked={tabAudioEnabled} disabled={recording} />
			<span class="toggle-label">Звук устройства</span>
		</label>
		{#if micEnabled}
			<MicSelector bind:selectedDeviceId={selectedMicId} disabled={recording} />
		{/if}
		{#if tabAudioEnabled && !recording}
			<small class="device-hint">В диалоге браузера выберите вкладку или экран для захвата звука</small>
		{/if}
	</div>

	{#if status === 'error'}
		<div class="error-message">
			{errorMessage || 'Ошибка захвата аудио. Проверьте разрешения.'}
		</div>
	{/if}
</div>

<style>
	.live-audio-capture {
		display: flex;
		flex-direction: column;
		gap: 12px;
	}

	/* ── Start button ── */
	.btn-start {
		display: inline-flex;
		align-items: center;
		gap: 8px;
		padding: 10px 20px;
		background: var(--color-cta);
		color: #fff;
		border: none;
		border-radius: var(--radius-sm);
		font-size: 15px;
		font-weight: 600;
		cursor: pointer;
		transition: background 0.15s ease;
	}

	.btn-start:hover {
		background: var(--color-cta-hover);
	}

	.btn-start:active {
		background: var(--color-cta-active);
	}

	/* ── Stop button ── */
	.btn-stop {
		display: inline-flex;
		align-items: center;
		gap: 6px;
		padding: 8px 16px;
		background: #EA4335;
		color: #fff;
		border: none;
		border-radius: var(--radius-sm);
		font-size: 14px;
		font-weight: 600;
		cursor: pointer;
		transition: opacity 0.15s ease;
	}

	.btn-stop:hover {
		opacity: 0.9;
	}

	.btn-stop:active {
		opacity: 0.8;
	}

	/* ── Recording indicator row ── */
	.recording-indicator {
		display: flex;
		align-items: center;
		gap: 12px;
	}

	/* ── Pulsing dot ── */
	.pulse-dot {
		display: inline-block;
		width: 12px;
		height: 12px;
		border-radius: 50%;
		background: #EA4335;
		animation: pulse 1.2s ease-in-out infinite;
	}

	@keyframes pulse {
		0%,
		100% {
			opacity: 1;
			transform: scale(1);
		}
		50% {
			opacity: 0.4;
			transform: scale(0.75);
		}
	}

	/* ── Timer ── */
	.timer {
		font-family: 'SF Mono', 'Cascadia Code', 'Fira Code', 'Consolas', monospace;
		font-size: 28px;
		font-weight: 700;
		letter-spacing: 0.04em;
		color: var(--color-text);
	}

	/* ── Source toggles ── */
	.source-toggles {
		display: flex;
		gap: 12px;
	}

	.toggle {
		position: relative;
		display: inline-flex;
		align-items: center;
		gap: 8px;
		padding: 6px 14px;
		background: var(--color-card);
		border: 1px solid var(--color-border);
		border-radius: var(--radius-sm);
		cursor: pointer;
		font-size: 13px;
		color: var(--color-muted);
		transition: border-color 0.15s ease, color 0.15s ease;
		user-select: none;
	}

	.toggle.active {
		border-color: var(--color-cta);
		color: var(--color-text);
	}

	.toggle input[type='checkbox'] {
		position: absolute;
		width: 1px;
		height: 1px;
		overflow: hidden;
		clip: rect(0 0 0 0);
		white-space: nowrap;
	}

	.toggle:hover {
		border-color: var(--color-cta);
	}

	.toggle input[type='checkbox']:disabled {
		opacity: 0.5;
	}
	.toggle:has(input:disabled) {
		opacity: 0.7;
		cursor: not-allowed;
	}

	.device-hint {
		font-size: 12px;
		color: var(--color-muted);
		margin-top: 2px;
	}

	/* ── Error message ── */
	.error-message {
		padding: 8px 12px;
		background: var(--color-error-bg);
		border: 1px solid var(--color-error-border);
		border-radius: var(--radius-sm);
		color: var(--color-error-text);
		font-size: 13px;
	}
</style>
