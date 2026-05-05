<script lang="ts">
	import HintTypeBadge from './HintTypeBadge.svelte';
	import HintFeedbackButtons from './HintFeedbackButtons.svelte';

	type HintType = 'argumentative' | 'navigational' | 'tactical' | 'strategic' | 'warning' | 'analytical';
	type Priority = 'critical' | 'high' | 'medium' | 'low';

	interface Hint {
		hint_type: HintType;
		text: string;
		priority: Priority;
		hint_id?: string;
		rationale?: string;
	}

	let { hints = [], onhintfeedback }: { hints: Hint[]; onhintfeedback?: (hintId: string, rating: 'like' | 'dislike') => void } = $props();

	const priorityOrder: Record<Priority, number> = { critical: 0, high: 1, medium: 2, low: 3 };
	const priorityColors: Record<Priority, string> = { critical: '#b91c1c', high: '#D32F2F', medium: '#F9A825', low: '#21A038' };
	const priorityLabels: Record<Priority, string> = { critical: 'Критический', high: 'Высокий', medium: 'Средний', low: 'Низкий' };

	let sortedHints = $derived([...hints].sort((a, b) => priorityOrder[a.priority] - priorityOrder[b.priority]));

	const expandedRationales: Set<string> = new Set();

	function toggleRationale(key: string) {
		if (expandedRationales.has(key)) {
			expandedRationales.delete(key);
		} else {
			expandedRationales.add(key);
		}
	}

	function isExpanded(key: string): boolean {
		return expandedRationales.has(key);
	}
</script>

<div class="hint-panel">
	<div class="hint-panel__header">Подсказки</div>

	<div class="hint-panel__content">
		{#if hints.length === 0}
			<div class="hint-panel__empty">Нет подсказок</div>
		{:else}
			{#each sortedHints as hint, i (hint.text + i)}
				<div class="hint-card">
					<div class="hint-card__header">
						<HintTypeBadge type={hint.hint_type} />
						<span class="priority-badge" data-priority={hint.priority}>
							<span
								class="priority-badge__dot"
								style="background-color: {priorityColors[hint.priority]}"
							></span>
							{priorityLabels[hint.priority]}
						</span>
					</div>

					<p class="hint-card__text">{hint.text}</p>

					{#if hint.rationale}
						<div class="hint-card__rationale">
							<button
								class="rationale-toggle"
								type="button"
								onclick={() => toggleRationale(hint.text + i)}
							>
								{isExpanded(hint.text + i) ? '▲ Скрыть' : 'Почему?'}
							</button>
							{#if isExpanded(hint.text + i)}
								<p class="rationale-text">{hint.rationale}</p>
							{/if}
						</div>
					{/if}

					{#if hint.hint_id && onhintfeedback}
						<div class="hint-card__footer">
							<HintFeedbackButtons hintId={hint.hint_id} onfeedback={onhintfeedback} />
						</div>
					{/if}
				</div>
			{/each}
		{/if}
	</div>
</div>

<style>
	.hint-panel {
		height: 100%;
		display: flex;
		flex-direction: column;
		background-color: var(--color-card);
		border-left: 1px solid var(--color-border);
	}

	.hint-panel__header {
		font-size: 1rem;
		font-weight: 600;
		color: var(--color-text);
		padding: 16px;
		border-bottom: 1px solid var(--color-border);
		flex-shrink: 0;
	}

	.hint-panel__content {
		flex: 1;
		overflow-y: auto;
		max-height: calc(100vh - var(--header-height) - 53px);
		padding: 16px;
	}

	.hint-panel__empty {
		color: var(--color-muted);
		text-align: center;
		padding: 32px 0;
		font-size: 0.875rem;
	}

	.hint-card {
		background-color: var(--color-card);
		border: 1px solid var(--color-border);
		border-radius: var(--radius-sm);
		padding: 12px;
		margin-bottom: 8px;
		display: flex;
		flex-direction: column;
		gap: 8px;
		transition: background-color 0.15s ease;
	}

	.hint-card:hover {
		background-color: var(--color-bg);
	}

	.hint-card:last-child {
		margin-bottom: 0;
	}

	.hint-card__header {
		display: flex;
		justify-content: space-between;
		align-items: center;
		gap: 8px;
	}

	.hint-card__text {
		margin: 0;
		font-size: 0.875rem;
		color: var(--color-text);
		line-height: 1.5;
	}

	.hint-card__rationale {
		display: flex;
		flex-direction: column;
		gap: 4px;
	}

	.rationale-toggle {
		background: none;
		border: none;
		color: var(--color-muted);
		font-size: 0.75rem;
		cursor: pointer;
		padding: 0;
		text-align: left;
		text-decoration: underline;
		text-underline-offset: 2px;
	}

	.rationale-toggle:hover {
		color: var(--color-text);
	}

	.rationale-text {
		margin: 0;
		font-size: 0.8125rem;
		color: var(--color-muted);
		line-height: 1.5;
		padding: 4px 0;
	}

	.hint-card__footer {
		display: flex;
		justify-content: flex-end;
	}

	.priority-badge {
		display: inline-flex;
		align-items: center;
		gap: 6px;
		font-size: 0.75rem;
		color: var(--color-muted);
		flex-shrink: 0;
		white-space: nowrap;
	}

	.priority-badge__dot {
		width: 8px;
		height: 8px;
		border-radius: 50%;
		flex-shrink: 0;
	}
</style>
