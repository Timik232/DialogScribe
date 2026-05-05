<script lang="ts">
	type HintType = 'tactical' | 'strategic' | 'warning' | 'analytical' | 'argumentative' | 'navigational';

	let { type }: { type: HintType } = $props();

	const meta: Record<HintType, { icon: string; color: string; label: string }> = {
		tactical: { icon: '⚡', color: '#2196F3', label: 'Тактика' },
		strategic: { icon: '🧭', color: '#4CAF50', label: 'Стратегия' },
		warning: { icon: '⚠️', color: '#FF9800', label: 'Внимание' },
		analytical: { icon: '📊', color: '#9E9E9E', label: 'Аналитика' },
		argumentative: { icon: '💪', color: '#E91E63', label: 'Аргументация' },
		navigational: { icon: '🧭', color: '#2196F3', label: 'Навигация' }
	};

	let info = $derived(meta[type]);
</script>

<span class="hint-type-badge" style="--badge-color: {info.color}">
	<span class="hint-type-badge__icon">{info.icon}</span>
	<span class="hint-type-badge__label">{info.label}</span>
</span>

<style>
	.hint-type-badge {
		display: inline-flex;
		align-items: center;
		gap: 4px;
		font-size: 0.75rem;
		padding: 2px 8px;
		border-radius: 4px;
		border-left: 3px solid var(--badge-color);
		background: color-mix(in srgb, var(--badge-color) 8%, transparent);
		color: var(--badge-color);
		font-weight: 500;
		white-space: nowrap;
	}

	.hint-type-badge__icon {
		font-size: 0.8125rem;
		line-height: 1;
	}

	.hint-type-badge__label {
		line-height: 1.2;
	}
</style>
