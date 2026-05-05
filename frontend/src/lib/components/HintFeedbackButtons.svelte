<script lang="ts">
	let { hintId, onfeedback }: { hintId: string; onfeedback?: (hintId: string, rating: 'like' | 'dislike') => void } = $props();

	let feedback: 'like' | 'dislike' | null = $state(null);

	function handleFeedback(rating: 'like' | 'dislike') {
		feedback = rating;
		onfeedback?.(hintId, rating);
	}
</script>

<div class="hint-feedback">
	<button
		class="hint-feedback__btn"
		class:hint-feedback__btn--active={feedback === 'like'}
		class:hint-feedback__btn--disabled={feedback === 'dislike'}
		onclick={() => handleFeedback('like')}
		title="Полезно"
		type="button"
	>
		👍
	</button>
	<button
		class="hint-feedback__btn"
		class:hint-feedback__btn--active={feedback === 'dislike'}
		class:hint-feedback__btn--disabled={feedback === 'like'}
		onclick={() => handleFeedback('dislike')}
		title="Не полезно"
		type="button"
	>
		👎
	</button>
</div>

<style>
	.hint-feedback {
		display: inline-flex;
		gap: 4px;
	}

	.hint-feedback__btn {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		width: 24px;
		height: 24px;
		border: 1px solid var(--color-border);
		border-radius: 4px;
		background: transparent;
		cursor: pointer;
		font-size: 0.75rem;
		padding: 0;
		transition: background-color 0.15s, border-color 0.15s;
	}

	.hint-feedback__btn:hover:not(:disabled) {
		background: var(--color-bg);
		border-color: var(--color-muted);
	}

	.hint-feedback__btn--active {
		background: rgba(33, 150, 243, 0.12);
		border-color: #2196F3;
	}

	.hint-feedback__btn--disabled {
		opacity: 0.4;
		cursor: default;
	}
</style>
