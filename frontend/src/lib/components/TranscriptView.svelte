<script lang="ts">
	import type { Snippet } from 'svelte';

	interface Segment {
		text: string;
		speaker: 'user' | 'opponent';
		timestamp: number;
	}

	interface Props {
		segments?: Segment[];
		children?: Snippet;
	}

	let { segments = [], children }: Props = $props();

	const speakerLabels: Record<string, string> = {
		user: 'Вы',
		opponent: 'Оппонент'
	};

	const speakerColors: Record<string, string> = {
		user: '#4285F4',
		opponent: '#EA4335'
	};

	function formatTime(seconds: number): string {
		const mins = Math.floor(seconds / 60);
		const secs = Math.floor(seconds % 60);
		return `${String(mins).padStart(2, '0')}:${String(secs).padStart(2, '0')}`;
	}
</script>

<div class="transcript-view">
	{#if segments.length === 0}
		<div class="empty-state">
			<span class="empty-icon">🎙️</span>
			<p>Транскрипция появится здесь</p>
		</div>
	{:else}
		<ul class="transcript-list">
			{#each segments as segment}
				<li class="transcript-line">
					<span
						class="speaker-badge"
						style="background-color: {speakerColors[segment.speaker]}"
					>
						{speakerLabels[segment.speaker]}
					</span>
					<span class="transcript-text">{segment.text}</span>
					<span class="transcript-time">{formatTime(segment.timestamp)}</span>
				</li>
			{/each}
		</ul>
	{/if}

	{#if children}
		{@render children()}
	{/if}
</div>

<style>
	.transcript-view {
		height: 100%;
		display: flex;
		flex-direction: column;
		background-color: var(--color-card);
		border-radius: var(--radius);
		border: 1px solid var(--color-border);
		overflow: hidden;
	}

	.transcript-list {
		list-style: none;
		margin: 0;
		padding: 0;
		flex: 1;
		overflow-y: auto;
	}

	.transcript-line {
		display: flex;
		align-items: flex-start;
		gap: 10px;
		padding: 10px 16px;
		border-bottom: 1px solid var(--color-border);
	}

	.transcript-line:last-child {
		border-bottom: none;
	}

	.speaker-badge {
		flex-shrink: 0;
		display: inline-flex;
		align-items: center;
		justify-content: center;
		padding: 2px 10px;
		border-radius: 999px;
		color: #ffffff;
		font-size: 12px;
		font-weight: 600;
		white-space: nowrap;
		line-height: 1.5;
	}

	.transcript-text {
		flex: 1;
		color: var(--color-text);
		font-size: 14px;
		line-height: 1.5;
		word-break: break-word;
	}

	.transcript-time {
		flex-shrink: 0;
		color: var(--color-muted);
		font-size: 12px;
		white-space: nowrap;
		padding-top: 2px;
	}

	.empty-state {
		flex: 1;
		display: flex;
		flex-direction: column;
		align-items: center;
		justify-content: center;
		gap: 8px;
		color: var(--color-muted);
	}

	.empty-icon {
		font-size: 32px;
		opacity: 0.6;
	}

	.empty-state p {
		margin: 0;
		font-size: 14px;
	}
</style>
