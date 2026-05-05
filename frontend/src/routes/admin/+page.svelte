<script lang="ts">
	import { onMount } from 'svelte';
	import { authStore } from '$lib/stores/auth';
	import { fetchApi } from '$lib/services/api';
	import { goto } from '$app/navigation';

	interface UsageSummary {
		event_type: string;
		total: number;
		count: number;
	}

	interface UserItem {
		id: string;
		email: string;
		username: string;
		role: string;
		is_active: boolean;
		approved_at: string | null;
		created_at: string | null;
		usage_summary: UsageSummary[];
	}

	interface GlobalStats {
		total_users: number;
		active_users: number;
		pending_users: number;
		total_usage_value: number;
		total_usage_count: number;
	}

	interface TimeseriesPoint {
		date: string;
		event_type: string;
		total: number;
		count: number;
	}

	let users: UserItem[] = $state([]);
	let stats: GlobalStats | null = $state(null);
	let timeseries: TimeseriesPoint[] = $state([]);
	let loading = $state(true);
	let error = $state('');
	let search = $state('');
	let statusFilter = $state('all');
	let limitModal = $state<{ userId: string; username: string; limitType: string; maxValue: number; period: string; enabled: boolean } | null>(null);

	onMount(async () => {
		// Wait for auth store to hydrate from localStorage/refresh
		while ($authStore.loading) {
			await new Promise((r) => setTimeout(r, 50));
		}
		if ($authStore.user?.role !== 'admin') {
			goto('/transcribe');
			return;
		}
		await Promise.all([loadUsers(), loadStats(), loadTimeseries()]);
		loading = false;
	});

	async function loadUsers() {
		error = '';
		try {
			const params = new URLSearchParams();
			if (search) params.set('search', search);
			if (statusFilter !== 'all') params.set('status', statusFilter);
			const qs = params.toString();
			const data = await fetchApi<{ users: UserItem[] }>('GET', `/api/admin/users${qs ? '?' + qs : ''}`);
			users = data.users;
		} catch (e: any) {
			error = e?.message || 'Failed to load users';
		}
	}

	async function loadStats() {
		try {
			stats = await fetchApi<GlobalStats>('GET', '/api/admin/stats/overview');
		} catch {}
	}

	async function loadTimeseries() {
		try {
			const data = await fetchApi<{ data: TimeseriesPoint[] }>('GET', '/api/admin/stats/timeseries?days=14');
			timeseries = data.data;
		} catch {}
	}

	async function toggleActive(user: UserItem) {
		try {
			await fetchApi('PATCH', `/api/admin/users/${user.id}`, {
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({ is_active: !user.is_active }),
			});
			await Promise.all([loadUsers(), loadStats()]);
		} catch (e: any) {
			error = e?.message || 'Failed to update user';
		}
	}

	async function approveUser(user: UserItem) {
		try {
			await fetchApi('PATCH', `/api/admin/users/${user.id}/approve`);
			await Promise.all([loadUsers(), loadStats()]);
		} catch (e: any) {
			error = e?.message || 'Failed to approve user';
		}
	}

	async function rejectUser(user: UserItem) {
		if (!confirm(`Отклонить пользователя ${user.username}?`)) return;
		try {
			await fetchApi('DELETE', `/api/admin/users/${user.id}`);
			await Promise.all([loadUsers(), loadStats()]);
		} catch (e: any) {
			error = e?.message || 'Failed to reject user';
		}
	}

	function openLimitModal(user: UserItem, limitType: string) {
		limitModal = {
			userId: user.id,
			username: user.username,
			limitType,
			maxValue: 100,
			period: 'monthly',
			enabled: true,
		};
	}

	async function saveLimit() {
		if (!limitModal) return;
		try {
			await fetchApi('PUT', `/api/admin/users/${limitModal.userId}/limits`, {
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({
					limit_type: limitModal.limitType,
					max_value: limitModal.maxValue,
					period: limitModal.period,
					enabled: limitModal.enabled,
				}),
			});
			limitModal = null;
			await loadUsers();
		} catch (e: any) {
			error = e?.message || 'Failed to save limit';
		}
	}

	const eventTypeLabels: Record<string, string> = {
		transcription_minutes: 'Транскрипция (мин)',
		llm_call: 'LLM вызовы',
		file_upload: 'Загрузки',
	};

	const isPending = (u: UserItem) => !u.is_active && !u.approved_at;

	const chartDates = $derived.by(() => {
		const dates: string[] = [];
		const now = new Date();
		for (let i = 13; i >= 0; i--) {
			const d = new Date(now);
			d.setDate(d.getDate() - i);
			dates.push(d.toISOString().slice(0, 10));
		}
		return dates;
	});

	const chartData = $derived.by(() => {
		const totals: Record<string, number> = {};
		for (const pt of timeseries) {
			totals[pt.date] = (totals[pt.date] || 0) + pt.total;
		}
		return chartDates.map((d) => totals[d] || 0);
	});

	const chartMax = $derived(Math.max(...chartData, 1));
</script>

<svelte:head>
	<title>Админ-панель</title>
</svelte:head>

<div class="admin-page">
	<h1>Админ-панель</h1>

	{#if error}
		<div class="error">{error}</div>
	{/if}

	{#if stats}
		<div class="dashboard">
			<div class="stat-card">
				<div class="stat-value">{stats.total_users}</div>
				<div class="stat-label">Всего пользователей</div>
			</div>
			<div class="stat-card">
				<div class="stat-value stat-green">{stats.active_users}</div>
				<div class="stat-label">Активные</div>
			</div>
			<div class="stat-card">
				<div class="stat-value stat-yellow">{stats.pending_users}</div>
				<div class="stat-label">Ожидают</div>
			</div>
			<div class="stat-card">
				<div class="stat-value">{stats.total_usage_count}</div>
				<div class="stat-label">Использований</div>
			</div>
		</div>
	{/if}

	{#if chartData.length > 0}
		<div class="chart-section">
			<h2>Использование за 14 дней</h2>
			<div class="chart">
				{#each chartDates as _, i}
					<div class="chart-bar-wrapper">
						<div class="chart-bar" style="height: {(chartData[i] / chartMax) * 100}%; background: var(--color-cta);"></div>
						<div class="chart-label">{chartDates[i].slice(5)}</div>
					</div>
				{/each}
			</div>
		</div>
	{/if}

	<div class="toolbar">
		<div class="filter-tabs">
			<button class="tab" class:active={statusFilter === 'all'} onclick={() => { statusFilter = 'all'; loadUsers(); }}>Все</button>
			<button class="tab" class:active={statusFilter === 'pending'} onclick={() => { statusFilter = 'pending'; loadUsers(); }}>Ожидают</button>
			<button class="tab" class:active={statusFilter === 'active'} onclick={() => { statusFilter = 'active'; loadUsers(); }}>Активные</button>
			<button class="tab" class:active={statusFilter === 'disabled'} onclick={() => { statusFilter = 'disabled'; loadUsers(); }}>Отключённые</button>
		</div>
		<div class="search-box">
			<input
				type="text"
				placeholder="Поиск по email/имени..."
				bind:value={search}
				oninput={() => loadUsers()}
			/>
		</div>
	</div>

	{#if loading}
		<p class="loading-text">Загрузка...</p>
	{:else}
		<div class="table-wrapper">
			<table>
				<thead>
					<tr>
						<th>Пользователь</th>
						<th>Email</th>
						<th>Роль</th>
						<th>Статус</th>
						<th>Дата регистрации</th>
						<th>Использование (месяц)</th>
						<th>Действия</th>
					</tr>
				</thead>
				<tbody>
					{#each users as user}
						<tr class:inactive={!user.is_active}>
							<td>{user.username}</td>
							<td>{user.email}</td>
							<td>
								<span class="badge" class:admin-badge={user.role === 'admin'}>
									{user.role}
								</span>
							</td>
							<td>
								{#if isPending(user)}
									<span class="status pending">Ожидает</span>
								{:else if user.is_active}
									<span class="status active">Активен</span>
								{:else}
									<span class="status disabled">Отключён</span>
								{/if}
							</td>
							<td class="date-cell">
								{user.created_at ? new Date(user.created_at).toLocaleDateString('ru-RU') : '—'}
							</td>
							<td>
								{#each user.usage_summary as stat}
									<div class="usage-item">
										{eventTypeLabels[stat.event_type] || stat.event_type}:
										<strong>{stat.total.toFixed(1)}</strong>
									</div>
								{/each}
								{#if user.usage_summary.length === 0}
									<span class="no-usage">—</span>
								{/if}
							</td>
							<td>
								<div class="actions">
									{#if isPending(user)}
										<button class="btn-sm btn-approve" onclick={() => approveUser(user)}>
											Подтвердить
										</button>
										<button class="btn-sm btn-danger" onclick={() => rejectUser(user)}>
											Отклонить
										</button>
									{:else if user.role !== 'admin'}
										<button
											class="btn-sm"
											class:btn-danger={user.is_active}
											onclick={() => toggleActive(user)}
										>
											{user.is_active ? 'Отключить' : 'Включить'}
										</button>
										<button class="btn-sm" onclick={() => openLimitModal(user, 'transcription_minutes')}>
											Лимиты
										</button>
									{/if}
								</div>
							</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
	{/if}
</div>

{#if limitModal}
	<div class="modal-overlay" onclick={() => (limitModal = null)}>
		<div class="modal" onclick={(e) => e.stopPropagation()}>
			<h2>Лимит для {limitModal.username}</h2>

			<div class="form-group">
				<label>Тип лимита</label>
				<select bind:value={limitModal.limitType}>
					<option value="transcription_minutes">Транскрипция (минуты)</option>
					<option value="llm_call">LLM вызовы</option>
					<option value="file_upload">Загрузки файлов</option>
				</select>
			</div>

			<div class="form-group">
				<label>Максимальное значение</label>
				<input type="number" bind:value={limitModal.maxValue} min="0" step="1" />
			</div>

			<div class="form-group">
				<label>Период</label>
				<select bind:value={limitModal.period}>
					<option value="daily">День</option>
					<option value="monthly">Месяц</option>
				</select>
			</div>

			<div class="form-group">
				<label>
					<input type="checkbox" bind:checked={limitModal.enabled} />
					Включён
				</label>
			</div>

			<div class="modal-actions">
				<button class="btn-secondary" onclick={() => (limitModal = null)}>Отмена</button>
				<button class="btn-primary" onclick={saveLimit}>Сохранить</button>
			</div>
		</div>
	</div>
{/if}

<style>
	.admin-page {
		padding: 2rem 1rem;
		max-width: 1200px;
		margin: 0 auto;
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
		margin-bottom: 1rem;
	}

	.dashboard {
		display: grid;
		grid-template-columns: repeat(4, 1fr);
		gap: 1rem;
		margin-bottom: 2rem;
	}

	.stat-card {
		background: var(--color-card);
		border-radius: var(--radius);
		padding: 1.25rem;
		text-align: center;
		box-shadow: 0 1px 6px rgba(0, 0, 0, 0.06);
	}

	.stat-value {
		font-size: 2rem;
		font-weight: 700;
		color: var(--color-text);
	}

	.stat-green { color: #38a169; }
	.stat-yellow { color: #d69e2e; }

	.stat-label {
		font-size: 0.8125rem;
		color: var(--color-muted);
		margin-top: 0.25rem;
	}

	.chart-section {
		background: var(--color-card);
		border-radius: var(--radius);
		padding: 1.5rem;
		margin-bottom: 2rem;
		box-shadow: 0 1px 6px rgba(0, 0, 0, 0.06);
	}

	.chart {
		display: flex;
		align-items: flex-end;
		gap: 4px;
		height: 120px;
	}

	.chart-bar-wrapper {
		flex: 1;
		display: flex;
		flex-direction: column;
		align-items: center;
		height: 100%;
		justify-content: flex-end;
	}

	.chart-bar {
		width: 100%;
		min-height: 2px;
		border-radius: 2px 2px 0 0;
		transition: height 0.3s;
	}

	.chart-label {
		font-size: 0.625rem;
		color: var(--color-muted);
		margin-top: 4px;
	}

	.toolbar {
		display: flex;
		gap: 1rem;
		align-items: center;
		margin-bottom: 1.5rem;
		flex-wrap: wrap;
	}

	.filter-tabs {
		display: flex;
		gap: 0;
	}

	.tab {
		padding: 0.4rem 0.75rem;
		font-size: 0.8125rem;
		border: 1px solid var(--color-border);
		background: var(--color-card);
		color: var(--color-muted);
		cursor: pointer;
	}

	.tab:first-child { border-radius: var(--radius-sm) 0 0 var(--radius-sm); }
	.tab:last-child { border-radius: 0 var(--radius-sm) var(--radius-sm) 0; }

	.tab.active {
		background: var(--color-cta);
		color: #fff;
		border-color: var(--color-cta);
	}

	.search-box input {
		padding: 0.4rem 0.75rem;
		border: 1px solid var(--color-border);
		border-radius: var(--radius-sm);
		font-size: 0.8125rem;
		background: var(--color-card);
		color: var(--color-text);
		min-width: 220px;
	}

	.table-wrapper {
		overflow-x: auto;
	}

	table {
		width: 100%;
		border-collapse: collapse;
		background: var(--color-card);
		border-radius: var(--radius);
		overflow: hidden;
	}

	th, td {
		padding: 0.75rem 1rem;
		text-align: left;
		border-bottom: 1px solid var(--color-border);
		font-size: 0.875rem;
	}

	th {
		background: var(--color-bg);
		font-weight: 600;
		color: var(--color-muted);
	}

	tr.inactive {
		opacity: 0.6;
	}

	.badge {
		padding: 0.2rem 0.5rem;
		border-radius: 4px;
		font-size: 0.75rem;
		background: var(--color-bg);
		color: var(--color-muted);
	}

	.admin-badge {
		background: var(--color-cta);
		color: #fff;
	}

	.status {
		font-size: 0.8125rem;
	}

	.status.active { color: #38a169; }
	.status.pending { color: #d69e2e; }
	.status.disabled { color: #e53e3e; }

	.date-cell {
		font-size: 0.8125rem;
		color: var(--color-muted);
		white-space: nowrap;
	}

	.usage-item {
		font-size: 0.75rem;
		color: var(--color-muted);
	}

	.no-usage {
		color: var(--color-muted);
		font-size: 0.75rem;
	}

	.actions {
		display: flex;
		gap: 0.5rem;
		flex-wrap: wrap;
	}

	.btn-sm {
		padding: 0.3rem 0.6rem;
		font-size: 0.75rem;
		border: 1px solid var(--color-border);
		border-radius: var(--radius-sm);
		background: var(--color-card);
		color: var(--color-text);
		cursor: pointer;
	}

	.btn-sm:hover {
		background: var(--color-bg);
	}

	.btn-approve {
		border-color: #c6f6d5;
		color: #38a169;
	}

	.btn-approve:hover {
		background: #f0fff4;
	}

	.btn-danger {
		border-color: #fed7d7;
		color: #e53e3e;
	}

	.btn-danger:hover {
		background: #fff5f5;
	}

	.error {
		background: var(--color-error-bg);
		border: 1px solid var(--color-error-border);
		color: var(--color-error-text);
		padding: 0.75rem;
		border-radius: var(--radius-sm);
		margin-bottom: 1rem;
		font-size: 0.875rem;
	}

	.loading-text {
		text-align: center;
		color: var(--color-muted);
		padding: 2rem 0;
	}

	.modal-overlay {
		position: fixed;
		inset: 0;
		background: rgba(0, 0, 0, 0.5);
		display: flex;
		align-items: center;
		justify-content: center;
		z-index: 100;
	}

	.modal {
		background: var(--color-card);
		padding: 2rem;
		border-radius: var(--radius);
		width: 100%;
		max-width: 400px;
	}

	.form-group {
		margin-bottom: 1rem;
	}

	.form-group label {
		display: block;
		font-size: 0.8125rem;
		color: var(--color-muted);
		margin-bottom: 0.25rem;
	}

	.form-group input[type='number'],
	.form-group select {
		width: 100%;
		padding: 0.5rem;
		border: 1px solid var(--color-border);
		border-radius: var(--radius-sm);
		background: var(--color-card);
		color: var(--color-text);
		font-size: 0.875rem;
	}

	.form-group input[type='checkbox'] {
		margin-right: 0.5rem;
	}

	.modal-actions {
		display: flex;
		gap: 0.75rem;
		justify-content: flex-end;
		margin-top: 1.5rem;
	}

	.btn-primary {
		padding: 0.5rem 1rem;
		background: var(--color-cta);
		color: #fff;
		border: none;
		border-radius: var(--radius-sm);
		cursor: pointer;
		font-size: 0.875rem;
	}

	.btn-secondary {
		padding: 0.5rem 1rem;
		background: transparent;
		border: 1px solid var(--color-border);
		border-radius: var(--radius-sm);
		cursor: pointer;
		font-size: 0.875rem;
	}

	@media (max-width: 768px) {
		.dashboard {
			grid-template-columns: repeat(2, 1fr);
		}
		.toolbar {
			flex-direction: column;
			align-items: stretch;
		}
		.search-box input {
			min-width: unset;
			width: 100%;
		}
	}
</style>
