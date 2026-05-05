<script lang="ts">
	import MicSelector from './MicSelector.svelte';

	const SUPPORTED_EXTENSIONS = [
		'.wav', '.mp3', '.flac', '.ogg', '.m4a', '.aac', '.wma', '.opus',
		'.mp4', '.mkv', '.avi', '.mov', '.webm', '.wmv', '.flv', '.mpeg', '.mpg'
	];
	const MAX_SIZE_MB = 500;

	interface Props {
		onfile: (file: File) => void;
	}

	let { onfile }: Props = $props();

	let dropZone: HTMLDivElement;
	let fileInput: HTMLInputElement;
	let isDragging = $state(false);
	let selectedFile: File | null = $state(null);
	let fileDuration = $state<number | null>(null);
	let validationError = $state('');

	let micRecording = $state(false);
	let micDenied = $state(false);
	let mediaRecorder: MediaRecorder | null = $state(null);
	let chunks: Blob[] = [];
	let selectedMicId = $state("");

	function getExt(filename: string): string {
		const dot = filename.lastIndexOf('.');
		return dot === -1 ? '' : filename.slice(dot).toLowerCase();
	}

	function formatSize(bytes: number): string {
		if (bytes < 1024) return `${bytes} Б`;
		if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} КБ`;
		return `${(bytes / (1024 * 1024)).toFixed(1)} МБ`;
	}

	function formatDuration(seconds: number): string {
		const m = Math.floor(seconds / 60);
		const s = Math.floor(seconds % 60);
		return `${m}:${s.toString().padStart(2, '0')}`;
	}

	function validate(file: File): string {
		const ext = getExt(file.name);
		if (!SUPPORTED_EXTENSIONS.includes(ext)) {
			return `Неподдерживаемый формат: ${ext || 'без расширения'}`;
		}
		if (file.size > MAX_SIZE_MB * 1024 * 1024) {
			return `Файл слишком большой (макс. ${MAX_SIZE_MB} МБ)`;
		}
		return '';
	}

	function extractDuration(file: File): void {
		fileDuration = null;
		const url = URL.createObjectURL(file);
		const audio = new Audio();
		audio.addEventListener('loadedmetadata', () => {
			fileDuration = audio.duration;
			URL.revokeObjectURL(url);
		});
		audio.addEventListener('error', () => {
			URL.revokeObjectURL(url);
		});
		audio.src = url;
	}

	function handleFile(file: File): void {
		validationError = '';
		const err = validate(file);
		if (err) {
			validationError = err;
			selectedFile = null;
			fileDuration = null;
			return;
		}
		selectedFile = file;
		extractDuration(file);
		onfile(file);
	}

	function onDrop(e: DragEvent): void {
		e.preventDefault();
		isDragging = false;
		const file = e.dataTransfer?.files?.[0];
		if (file) handleFile(file);
	}

	function onDragOver(e: DragEvent): void {
		e.preventDefault();
		isDragging = true;
	}

	function onDragLeave(): void {
		isDragging = false;
	}

	function openFilePicker(): void {
		fileInput.click();
	}

	function onFileInput(e: Event): void {
		const target = e.target as HTMLInputElement;
		const file = target.files?.[0];
		if (file) handleFile(file);
	}

	async function toggleMic(): Promise<void> {
		if (micRecording) {
			mediaRecorder?.stop();
			micRecording = false;
			return;
		}

		try {
			const audioConstraints: MediaTrackConstraints = selectedMicId
				? { deviceId: { exact: selectedMicId } }
				: true as unknown as MediaTrackConstraints;
			const stream = await navigator.mediaDevices.getUserMedia({ audio: audioConstraints });
			chunks = [];
			const recorder = new MediaRecorder(stream);
			mediaRecorder = recorder;

			recorder.ondataavailable = (e) => {
				if (e.data.size > 0) chunks.push(e.data);
			};

			recorder.onstop = () => {
				stream.getTracks().forEach((t) => t.stop());
				const blob = new Blob(chunks, { type: 'audio/webm' });
				const file = new File([blob], 'recording.webm', { type: 'audio/webm' });
				selectedFile = file;
				fileDuration = null;
				validationError = '';
				onfile(file);
				chunks = [];
			};

			recorder.start();
			micRecording = true;
		} catch {
			micDenied = true;
		}
	}

	function clearFile(): void {
		selectedFile = null;
		fileDuration = null;
		validationError = '';
		if (fileInput) fileInput.value = '';
	}
</script>

<div class="uploader">
	<div
		class="drop-zone"
		class:dragging={isDragging}
		class:has-file={selectedFile && !validationError}
		bind:this={dropZone}
		ondrop={onDrop}
		ondragover={onDragOver}
		ondragleave={onDragLeave}
		onclick={openFilePicker}
		onkeydown={(e: KeyboardEvent) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openFilePicker(); } }}
		role="button"
		tabindex="0"
	>
		<input
			bind:this={fileInput}
			type="file"
			accept={SUPPORTED_EXTENSIONS.join(',')}
			onchange={onFileInput}
			class="sr-only"
		/>

		{#if selectedFile && !validationError}
			<div class="file-info">
				<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
					<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
					<polyline points="14 2 14 8 20 8"/>
				</svg>
				<div class="file-details">
					<span class="file-name">{selectedFile.name}</span>
					<span class="file-meta">
						{formatSize(selectedFile.size)}
						{#if fileDuration !== null}
							&middot; {formatDuration(fileDuration)}
						{/if}
					</span>
				</div>
				<button class="clear-btn" onclick={(e: Event) => { e.stopPropagation(); clearFile(); }} title="Убрать файл">
					<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
						<line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
					</svg>
				</button>
			</div>
		{:else}
			<div class="drop-prompt">
				<svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
					<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
					<polyline points="17 8 12 3 7 8"/>
					<line x1="12" y1="3" x2="12" y2="15"/>
				</svg>
				<span class="drop-text">Перетащите файл сюда или нажмите для выбора</span>
				<span class="drop-hint">Аудио: WAV, MP3, FLAC, OGG, M4A, AAC, WMA, OPUS<br>Видео: MP4, MKV, AVI, MOV, WEBM</span>
			</div>
		{/if}
	</div>

	{#if validationError}
		<div class="error-banner">{validationError}</div>
	{/if}

	<div class="mic-row">
		<MicSelector bind:selectedDeviceId={selectedMicId} disabled={micRecording || micDenied} />
		<button
			class="btn btn-secondary mic-btn"
			onclick={toggleMic}
			disabled={micDenied}
		>
			{#if micRecording}
				<span class="mic-dot recording"></span>
				Остановить запись
			{:else}
				<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
					<path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"/>
					<path d="M19 10v2a7 7 0 0 1-14 0v-2"/>
					<line x1="12" y1="19" x2="12" y2="23"/>
					<line x1="8" y1="23" x2="16" y2="23"/>
				</svg>
				Записать с микрофона
			{/if}
		</button>
		{#if micDenied}
			<span class="mic-denied">Доступ к микрофону запрещён</span>
		{/if}
	</div>
</div>

<style>
	.uploader {
		display: flex;
		flex-direction: column;
		gap: 0.75rem;
	}

	.drop-zone {
		border: 2px dashed var(--color-border);
		border-radius: var(--radius);
		padding: 2rem 1.5rem;
		text-align: center;
		cursor: pointer;
		transition: border-color 0.2s, background-color 0.2s;
		background-color: var(--color-card);
	}

	.drop-zone:hover,
	.drop-zone.dragging {
		border-color: var(--color-cta);
		background-color: rgba(33, 160, 56, 0.04);
	}

	.drop-zone.has-file {
		border-style: solid;
		border-color: var(--color-cta);
	}

	.sr-only {
		position: absolute;
		width: 1px;
		height: 1px;
		padding: 0;
		margin: -1px;
		overflow: hidden;
		clip: rect(0, 0, 0, 0);
		border: 0;
	}

	.drop-prompt {
		display: flex;
		flex-direction: column;
		align-items: center;
		gap: 0.5rem;
		color: var(--color-muted);
	}

	.drop-text {
		font-size: 0.9375rem;
		color: var(--color-text);
	}

	.drop-hint {
		font-size: 0.75rem;
		line-height: 1.5;
	}

	.file-info {
		display: flex;
		align-items: center;
		gap: 0.75rem;
		text-align: left;
	}

	.file-info svg {
		flex-shrink: 0;
		color: var(--color-cta);
	}

	.file-details {
		display: flex;
		flex-direction: column;
		gap: 0.125rem;
		min-width: 0;
	}

	.file-name {
		font-size: 0.9375rem;
		font-weight: 500;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	.file-meta {
		font-size: 0.75rem;
		color: var(--color-muted);
	}

	.clear-btn {
		margin-left: auto;
		flex-shrink: 0;
		background: none;
		border: none;
		cursor: pointer;
		color: var(--color-muted);
		padding: 0.25rem;
		border-radius: 4px;
		transition: color 0.15s, background-color 0.15s;
	}

	.clear-btn:hover {
		color: var(--color-error-text, #D32F2F);
		background-color: var(--color-error-bg, #FEF2F2);
	}

	.error-banner {
		background-color: var(--color-error-bg);
		border: 1px solid var(--color-error-border);
		color: var(--color-error-text);
		border-radius: var(--radius-sm);
		padding: 0.75rem 1rem;
		font-size: 0.875rem;
	}

	.mic-row {
		display: flex;
		align-items: center;
		gap: 0.75rem;
	}

	.mic-btn {
		display: inline-flex;
		align-items: center;
		gap: 0.5rem;
	}

	.mic-dot {
		width: 8px;
		height: 8px;
		border-radius: 50%;
		background-color: var(--color-error-text, #D32F2F);
	}

	.mic-dot.recording {
		animation: pulse-dot 1s ease-in-out infinite;
	}

	@keyframes pulse-dot {
		0%, 100% { opacity: 1; }
		50% { opacity: 0.3; }
	}

	.mic-denied {
		font-size: 0.8125rem;
		color: var(--color-error-text, #D32F2F);
	}
</style>
