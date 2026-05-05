<script lang="ts">
	import { page } from '$app/stores';
	import { get } from 'svelte/store';

	interface Segment {
		text: string;
		start: number;
		end: number;
		speaker?: string;
	}

	interface SharedTranscription {
		title: string;
		full_text: string;
		analysis_text?: string | null;
		segments_json: Segment[];
		duration: number;
		language: string;
		created_at: string;
	}

	let data: SharedTranscription | null = $state(null);
	let error = $state('');
	let loading = $state(true);

	$effect(() => {
		const shareId = get(page).params.id;
		if (shareId) {
			loadShared(shareId);
		}
	});

	async function loadShared(shareId: string) {
		loading = true;
		try {
			const res = await fetch(`/api/share/${shareId}`);
			if (!res.ok) {
				error = 'Эта расшифровка недоступна';
				return;
			}
			data = await res.json();
		} catch {
			error = 'Ошибка загрузки';
		} finally {
			loading = false;
		}
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

	function formatSegTime(s: number): string {
		const m = Math.floor(s / 60);
		const sec = Math.floor(s % 60);
		return `${m}:${sec.toString().padStart(2, '0')}`;
	}
</script>

<div class="share-page">
	<header class="share-header">
		<span class="logo">DialogScribe</span>
	</header>

	{#if loading}
		<div class="loading">Загрузка…</div>
	{:else if error}
		<div class="error-state">
			<p>{error}</p>
		</div>
	{:else if data}
		<div class="content">
			<h1 class="title">{data.title}</h1>

			<div class="meta">
				<span class="badge">{formatDuration(data.duration)}</span>
				<span class="badge">{data.language}</span>
				<span class="badge">{formatDate(data.created_at)}</span>
			</div>

			<div class="full-text">
				<pre>{data.full_text}</pre>
			</div>

			{#if data.analysis_text}
				<div class="analysis">
					<h3>Анализ</h3>
					<div class="analysis-content">{data.analysis_text}</div>
				</div>
			{/if}

			{#if data.segments_json && data.segments_json.length > 0}
				<div class="segments">
					<h3>Сегменты</h3>
					<div class="segments-list">
						{#each data.segments_json as seg}
							<div class="segment">
								<span class="seg-time">{formatSegTime(seg.start)}</span>
								<span class="seg-text">{seg.text}</span>
							</div>
						{/each}
					</div>
				</div>
			{/if}
		</div>
	{/if}
</div>

<style>
	.share-page {
		max-width: 48rem;
		margin: 0 auto;
		padding: 2rem 1.5rem;
		min-height: 100vh;
	}

	.share-header {
		display: flex;
		align-items: center;
		margin-bottom: 2rem;
	}

	.logo {
		font-size: 1.25rem;
		font-weight: 700;
	}

	.loading,
	.error-state {
		text-align: center;
		padding: 3rem 1rem;
		color: var(--color-muted);
	}

	.title {
		font-size: 1.5rem;
		font-weight: 600;
		margin: 0 0 1rem;
	}

	.meta {
		display: flex;
		gap: 0.5rem;
		margin-bottom: 1.5rem;
		flex-wrap: wrap;
	}

	.badge {
		font-size: 0.75rem;
		padding: 0.1875rem 0.5rem;
		border-radius: var(--radius-sm);
		background: var(--color-bg);
		color: var(--color-muted);
		border: 1px solid var(--color-border);
		white-space: nowrap;
	}

	.full-text {
		margin-bottom: 2rem;
	}

	.full-text pre {
		white-space: pre-wrap;
		word-wrap: break-word;
		font-family: inherit;
		font-size: 0.9375rem;
		line-height: 1.6;
		margin: 0;
		padding: 1rem;
		background: var(--color-bg);
		border: 1px solid var(--color-border);
		border-radius: var(--radius-sm);
	}

	.analysis {
		margin-top: 1.5rem;
		margin-bottom: 2rem;
	}

	.analysis h3 {
		font-size: 1rem;
		margin: 0 0 0.75rem;
	}

	.analysis-content {
		white-space: pre-wrap;
		word-wrap: break-word;
		padding: 1rem;
		border: 1px solid var(--color-border);
		border-radius: var(--radius-sm);
		max-height: 30rem;
		overflow-y: auto;
		font-size: 0.8125rem;
		line-height: 1.6;
	}

	.segments {
		margin-top: 1rem;
	}

	.segments h3 {
		font-size: 1rem;
		margin: 0 0 0.75rem;
	}

	.segments-list {
		border: 1px solid var(--color-border);
		border-radius: var(--radius-sm);
		padding: 0.5rem;
		max-height: 20rem;
		overflow-y: auto;
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

	.seg-text {
		flex: 1;
	}
</style>
