<script lang="ts">
	interface Segment {
		text: string;
		speaker: string;
		timestamp: number;
	}

	interface Props {
		segments: Segment[];
	}

	let { segments }: Props = $props();

	let userPercent = $derived.by(() => {
		if (segments.length < 3) return 0;
		const userCount = segments.filter((s) => s.speaker === 'user').length;
		const opponentCount = segments.filter((s) => s.speaker === 'opponent').length;
		const total = userCount + opponentCount;
		return total > 0 ? Math.round((userCount / total) * 100) : 0;
	});

	let opponentPercent = $derived(100 - userPercent);
</script>

{#if segments.length >= 3}
	<div class="stats-bar">
		<div class="stats-section stats-user" style="width: {userPercent}%">
			<span class="stats-label">Вы: {userPercent}%</span>
		</div>
		<div class="stats-section stats-opponent" style="width: {opponentPercent}%">
			<span class="stats-label">Оппонент: {opponentPercent}%</span>
		</div>
	</div>
{/if}

<style>
	.stats-bar {
		display: flex;
		height: 28px;
		margin-bottom: 0.75rem;
		overflow: hidden;
		flex-shrink: 0;
	}

	.stats-section {
		display: flex;
		align-items: center;
		justify-content: center;
		min-width: 0;
		overflow: hidden;
	}

	.stats-user {
		background-color: #4285f4;
	}

	.stats-opponent {
		background-color: #ea4335;
	}

	.stats-label {
		color: #ffffff;
		font-size: 12px;
		font-weight: 600;
		white-space: nowrap;
		line-height: 1;
	}
</style>
