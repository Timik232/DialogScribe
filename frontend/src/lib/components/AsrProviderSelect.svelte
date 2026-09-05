<script lang="ts">
	import { onMount } from 'svelte';
	import { fetchApi } from '$lib/services/api';

	let { id = 'asr-provider', selectClass = '' }: { id?: string; selectClass?: string } = $props();

	let provider = $state('litellm');
	let loading = $state(true);
	let feedback = $state<{ text: string; type: 'success' | 'error' } | null>(null);

	onMount(async () => {
		try {
			const data = await fetchApi<{ provider: string }>('GET', '/api/settings/asr-provider');
			provider = data.provider || 'litellm';
		} catch {
			provider = 'litellm';
		} finally {
			loading = false;
		}
	});

	async function save() {
		feedback = null;
		try {
			await fetchApi('PUT', '/api/settings/asr-provider', {
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({ provider })
			});
			feedback = { text: 'Сохранено', type: 'success' };
		} catch (e) {
			feedback = { text: e instanceof Error ? e.message : 'Ошибка сохранения', type: 'error' };
		}
		setTimeout(() => (feedback = null), 3000);
	}
</script>

<select {id} class={selectClass} bind:value={provider} onchange={save} disabled={loading}>
	<option value="litellm">GigaAM</option>
	<option value="mistral">Mistral</option>
</select>
{#if feedback}
	<span class="asr-feedback" class:success={feedback.type === 'success'} class:error={feedback.type === 'error'}>
		{feedback.text}
	</span>
{/if}

<style>
	.asr-feedback {
		font-size: 0.85rem;
		color: var(--color-muted);
	}
	.asr-feedback.success {
		color: var(--color-success, #34a853);
	}
	.asr-feedback.error {
		color: var(--color-danger, #ea4335);
	}
</style>
