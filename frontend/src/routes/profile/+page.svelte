<script lang="ts">
	import { onMount } from 'svelte';
	import { authStore } from '$lib/stores/auth';
	import { fetchApi } from '$lib/services/api';
	import { goto } from '$app/navigation';

	interface UsageStat {
		event_type: string;
		total: number;
		count: number;
	}

	interface LimitInfo {
		limit_type: string;
		max_value: number;
		period: string;
		enabled: boolean;
		remaining: number | null;
	}

	let usageStats: UsageStat[] = $state([]);
	let limits: LimitInfo[] = $state([]);
	let loading = $state(true);
	let period = $state('monthly');

	async function loadUsage() {
		loading = true;
		try {
			const data = await fetchApi<{ period: string; stats: UsageStat[]; limits: LimitInfo[] }>('GET', `/api/usage/me?period=${period}`);
			usageStats = data.stats;
			limits = data.limits || [];
		} catch {
			usageStats = [];
			limits = [];
		} finally {
			loading = false;
		}
	}

	onMount(loadUsage);

	async function handleLogout() {
		await authStore.logout();
		goto('/login');
	}

	const eventTypeLabels: Record<string, string> = {
		transcription_minutes: 'Транскрипция (минуты)',
		llm_call: 'LLM вызовы',
		file_upload: 'Загрузки файлов',
	};
</script>

<svelte:head>
	<title>Профиль</title>
</svelte:head>

<div class="profile-page">
	<div class="profile-card">
		<h1>Профиль</h1>

		{#if $authStore.user}
			<div class="user-info">
				<div class="info-row">
					<span class="label">Имя пользователя:</span>
					<span class="value">{$authStore.user.username}</span>
				</div>
				<div class="info-row">
					<span class="label">Email:</span>
					<span class="value">{$authStore.user.email}</span>
				</div>
				<div class="info-row">
					<span class="label">Роль:</span>
					<span class="value">{($authStore.user.role === 'admin') ? 'Администратор' : 'Пользователь'}</span>
				</div>
			</div>
		{/if}

		<div class="usage-section">
			<div class="usage-header">
				<h2>Использование</h2>
				<select bind:value={period} onchange={loadUsage}>
					<option value="daily">За день</option>
					<option value="monthly">За месяц</option>
					<option value="all">Всё время</option>
				</select>
			</div>

			{#if loading}
				<p class="loading-text">Загрузка...</p>
			{:else if usageStats.length === 0}
				<p class="no-data">Нет данных об использовании</p>
			{:else}
				<div class="usage-table">
					{#each usageStats as stat}
						<div class="usage-row">
							<span class="usage-type">{eventTypeLabels[stat.event_type] || stat.event_type}</span>
							<span class="usage-value">{stat.total.toFixed(2)}</span>
							<span class="usage-count">{stat.count} вызовов</span>
						</div>
					{/each}
				</div>
			{/if}
		</div>

		{#if limits.length > 0}
			<div class="limits-section">
				<h2>Лимиты</h2>
				{#each limits as lim}
					{@const used = lim.remaining !== null ? lim.max_value - lim.remaining : 0}
					{@const pct = lim.max_value > 0 ? Math.min((used / lim.max_value) * 100, 100) : 0}
					<div class="limit-row">
						<div class="limit-header">
							<span class="limit-type">{eventTypeLabels[lim.limit_type] || lim.limit_type}</span>
							<span class="limit-remain">
								{#if lim.remaining !== null}
									{lim.remaining.toFixed(1)} / {lim.max_value}
								{:else}
									∞
								{/if}
							</span>
						</div>
						<div class="limit-bar-bg">
							<div
								class="limit-bar"
								class:limit-bar-warn={pct > 80}
								class:limit-bar-full={pct >= 100}
								style="width: {pct}%"
							></div>
						</div>
						<div class="limit-meta">
							{lim.period === 'daily' ? 'в день' : 'в месяц'}
							· {lim.enabled ? 'активен' : 'выключен'}
						</div>
					</div>
				{/each}
			</div>
		{/if}

		<button class="logout-btn" onclick={handleLogout}>Выйти</button>
	</div>
</div>

<style>
	.profile-page {
		padding: 2rem 1rem;
		max-width: 600px;
		margin: 0 auto;
	}

	.profile-card {
		background: var(--color-card);
		border-radius: var(--radius);
		padding: 2rem;
		box-shadow: 0 2px 12px rgba(0, 0, 0, 0.08);
	}

	h1 {
		color: var(--color-primary);
		font-size: 1.5rem;
		font-weight: 600;
		margin-bottom: 1.5rem;
	}

	h2 {
		font-size: 1.1rem;
		font-weight: 600;
		color: var(--color-text);
	}

	.user-info {
		margin-bottom: 2rem;
		padding-bottom: 1.5rem;
		border-bottom: 1px solid var(--color-border);
	}

	.info-row {
		display: flex;
		justify-content: space-between;
		padding: 0.5rem 0;
	}

	.label {
		color: var(--color-muted);
		font-size: 0.875rem;
	}

	.value {
		font-weight: 500;
		font-size: 0.875rem;
	}

	.usage-section {
		margin-bottom: 2rem;
	}

	.usage-header {
		display: flex;
		justify-content: space-between;
		align-items: center;
		margin-bottom: 1rem;
	}

	select {
		padding: 0.4rem 0.75rem;
		border: 1px solid var(--color-border);
		border-radius: var(--radius-sm);
		background: var(--color-card);
		color: var(--color-text);
		font-size: 0.8125rem;
	}

	.usage-table {
		display: flex;
		flex-direction: column;
		gap: 0.5rem;
	}

	.usage-row {
		display: flex;
		justify-content: space-between;
		align-items: center;
		padding: 0.75rem;
		background: var(--color-bg);
		border-radius: var(--radius-sm);
	}

	.usage-type {
		flex: 1;
		font-size: 0.875rem;
	}

	.usage-value {
		font-weight: 600;
		font-size: 0.875rem;
		margin: 0 1rem;
	}

	.usage-count {
		color: var(--color-muted);
		font-size: 0.75rem;
	}

	.loading-text, .no-data {
		text-align: center;
		color: var(--color-muted);
		font-size: 0.875rem;
		padding: 2rem 0;
	}

	.logout-btn {
		width: 100%;
		padding: 0.75rem;
		background: transparent;
		color: var(--color-error-text, #e53e3e);
		border: 1px solid var(--color-error-border, #fed7d7);
		border-radius: var(--radius-sm);
		cursor: pointer;
		font-size: 0.875rem;
		transition: background 0.15s;
	}

	.logout-btn:hover {
		background: var(--color-error-bg, #fff5f5);
	}

	.limits-section {
		margin-bottom: 2rem;
		padding-top: 1.5rem;
		border-top: 1px solid var(--color-border);
	}

	.limit-row {
		margin-bottom: 1rem;
	}

	.limit-header {
		display: flex;
		justify-content: space-between;
		margin-bottom: 0.35rem;
	}

	.limit-type {
		font-size: 0.875rem;
	}

	.limit-remain {
		font-size: 0.8125rem;
		color: var(--color-muted);
	}

	.limit-bar-bg {
		height: 8px;
		background: var(--color-bg);
		border-radius: 4px;
		overflow: hidden;
	}

	.limit-bar {
		height: 100%;
		background: var(--color-cta);
		border-radius: 4px;
		transition: width 0.3s;
	}

	.limit-bar-warn {
		background: #d69e2e;
	}

	.limit-bar-full {
		background: #e53e3e;
	}

	.limit-meta {
		font-size: 0.75rem;
		color: var(--color-muted);
		margin-top: 0.25rem;
	}
</style>
