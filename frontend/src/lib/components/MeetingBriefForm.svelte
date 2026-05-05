<script lang="ts">
	interface MeetingBrief {
		goal: string;
		offering: string;
		red_lines: string;
		known_objections: string;
	}

	interface Props {
		disabled?: boolean;
		onsubmit?: (brief: MeetingBrief) => void;
	}

	let { disabled = false, onsubmit }: Props = $props();

	let goal = $state('');
	let offering = $state('');
	let red_lines = $state('');
	let known_objections = $state('');

	let goalTouched = $state(false);

	let goalInvalid = $derived(goalTouched && !goal);

	const goalOptions: { value: string; label: string }[] = [
		{ value: 'initial', label: 'Первичная встреча' },
		{ value: 'price', label: 'Переговоры о цене' },
		{ value: 'presentation', label: 'Презентация' },
		{ value: 'finalization', label: 'Финализация сделки' },
		{ value: 'other', label: 'Другое' },
	];

	function handleBlur(): void {
		goalTouched = true;
		onsubmit?.({ goal, offering, red_lines, known_objections });
	}
</script>

<div class="brief-form">
	<label class="field">
		<span class="label">Цель встречи *</span>
		<select
			class="input"
			class:invalid={goalInvalid}
			bind:value={goal}
			{disabled}
			onblur={handleBlur}
		>
			<option value="" disabled>Выберите цель…</option>
			{#each goalOptions as opt}
				<option value={opt.value}>{opt.label}</option>
			{/each}
		</select>
	</label>

	<label class="field">
		<span class="label">Предложение</span>
		<textarea
			class="input"
			placeholder="Что предлагаем клиенту"
			bind:value={offering}
			{disabled}
			onblur={handleBlur}
			rows="2"
		></textarea>
	</label>

	<label class="field">
		<span class="label">Красные линии</span>
		<textarea
			class="input"
			placeholder="На что не соглашаемся"
			bind:value={red_lines}
			{disabled}
			onblur={handleBlur}
			rows="2"
		></textarea>
	</label>

	<label class="field">
		<span class="label">Возражения</span>
		<textarea
			class="input"
			placeholder="Известные возражения клиента"
			bind:value={known_objections}
			{disabled}
			onblur={handleBlur}
			rows="2"
		></textarea>
	</label>
</div>

<style>
	.brief-form {
		display: flex;
		flex-direction: column;
		gap: 0.5rem;
	}

	.field {
		display: flex;
		flex-direction: column;
		gap: 0.25rem;
	}

	.label {
		font-size: 0.75rem;
		font-weight: 500;
		color: var(--color-muted);
	}

	.input {
		width: 100%;
		padding: 0.375rem 0.5rem;
		font-size: 0.8125rem;
		line-height: 1.4;
		color: var(--color-text);
		background: var(--color-bg);
		border: 1px solid var(--color-border);
		border-radius: var(--radius-sm);
		outline: none;
		transition: border-color 0.15s;
		font-family: inherit;
		resize: vertical;
		box-sizing: border-box;
	}

	.input:focus {
		border-color: var(--color-cta);
	}

	.input:disabled {
		opacity: 0.55;
		cursor: not-allowed;
	}

	.input.invalid {
		border-color: #e53e3e;
	}

	textarea.input {
		min-height: 2.5rem;
	}
</style>
