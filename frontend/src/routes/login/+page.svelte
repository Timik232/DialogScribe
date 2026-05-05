<script lang="ts">
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import { page } from '$app/stores';
	import { authStore } from '$lib/stores/auth';

	let loginValue = $state('');
	let password = $state('');
	let error = $state('');
	let loading = $state(false);

	onMount(async () => {
		if ($authStore.isAuthenticated) {
			const returnUrl = $page.url.searchParams.get('return_url') || '/transcribe';
			goto(returnUrl);
		}
	});

	async function handleSubmit(e: Event) {
		e.preventDefault();
		error = '';
		loading = true;

		try {
			await authStore.login(loginValue, password);
			const returnUrl = $page.url.searchParams.get('return_url') || '/transcribe';
			goto(returnUrl);
		} catch (err: any) {
			const reason = err?.reason;
			if (reason === 'pending_approval') {
				error = 'Ваш аккаунт ожидает подтверждения администратором';
			} else if (reason === 'account_disabled') {
				error = 'Ваш аккаунт был отключён администратором';
			} else {
				error = 'Неверное имя пользователя или пароль';
			}
		} finally {
			loading = false;
		}
	}
</script>

<svelte:head>
	<title>Вход в систему</title>
</svelte:head>

<div class="top-bar"></div>

<div class="login-wrapper">
	<div class="login-card">
		<h1>Вход в систему</h1>
		<p class="subtitle">Авторизация для доступа к сервису</p>

		<form onsubmit={handleSubmit}>
			<div class="field">
				<span class="field-icon">
					<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>
				</span>
				<input
					type="text"
					bind:value={loginValue}
					placeholder="Email или имя пользователя"
					required
					autofocus
					disabled={loading}
				/>
			</div>

			<div class="field">
				<span class="field-icon">
					<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="11" width="18" height="11" rx="2" ry="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg>
				</span>
				<input
					type="password"
					bind:value={password}
					placeholder="Пароль"
					required
					disabled={loading}
				/>
			</div>

			<div class="forgot-link">
				<a href="/forgot-password">Забыли пароль?</a>
			</div>

			<button type="submit" class="btn" disabled={loading}>
				{loading ? 'Вход...' : 'Войти'}
			</button>
		</form>

		{#if error}
			<div class="error">{error}</div>
		{/if}

		<div class="register-link">
			Нет аккаунта? <a href="/register">Зарегистрироваться</a>
		</div>
	</div>
</div>

<div class="footer">
	<span class="footer-icon">
		<svg viewBox="0 0 24 24"><path d="M18 8h-1V6c0-2.76-2.24-5-5-5S7 3.24 7 6v2H6c-1.1 0-2 .9-2 2v10c0 1.1.9 2 2 2h12c1.1 0 2-.9 2-2V10c0-1.1-.9-2-2-2zm-6 9c-1.1 0-2-.9-2-2s.9-2 2-2 2 .9 2 2-.9 2-2 2zm3.1-9H8.9V6c0-1.71 1.39-3.1 3.1-3.1 1.71 0 3.1 1.39 3.1 3.1v2z"/></svg>
	</span>
	Защищённое соединение
</div>

<style>
	.top-bar {
		position: fixed;
		top: 0;
		left: 0;
		right: 0;
		height: 6px;
		background: var(--color-primary);
		z-index: 10;
	}

	.login-wrapper {
		display: flex;
		flex-direction: column;
		align-items: center;
		justify-content: center;
		min-height: 100vh;
		min-height: 100dvh;
		padding: 2rem 1rem 4rem;
		background: var(--color-bg);
		font-family: var(--font-family);
	}

	.login-card {
		background: var(--color-card);
		padding: 2.5rem 2.5rem 2rem;
		border-radius: var(--radius);
		box-shadow: 0 2px 12px rgba(0, 0, 0, 0.08);
		width: 100%;
		max-width: 380px;
		margin: 0 auto;
	}

	h1 {
		text-align: center;
		color: var(--color-primary);
		font-size: 1.5rem;
		font-weight: 600;
		margin-bottom: 0.25rem;
		letter-spacing: -0.01em;
	}

	.subtitle {
		text-align: center;
		color: var(--color-muted);
		margin-bottom: 2rem;
		font-size: 0.875rem;
	}

	.field {
		position: relative;
		margin-bottom: 1rem;
	}

	.field-icon {
		position: absolute;
		left: 12px;
		top: 50%;
		transform: translateY(-50%);
		color: var(--color-muted);
		pointer-events: none;
	}

	.field-icon :global(svg) {
		width: 18px;
		height: 18px;
	}

	input[type='text'],
	input[type='password'] {
		width: 100%;
		padding: 0.75rem 0.75rem 0.75rem 2.75rem;
		border: 1px solid var(--color-border);
		border-radius: var(--radius-sm);
		font-size: 0.9375rem;
		color: var(--color-text);
		background: var(--color-card);
		transition: border-color 0.15s, box-shadow 0.15s;
	}

	input[type='text']:focus,
	input[type='password']:focus {
		outline: none;
		border-color: var(--color-primary);
		box-shadow: 0 0 0 2px rgba(26, 35, 50, 0.1);
	}

	input::placeholder {
		color: #b0b0b0;
	}

	.btn {
		width: 100%;
		padding: 0.8rem;
		background: var(--color-cta);
		color: #fff;
		border: none;
		border-radius: var(--radius-sm);
		cursor: pointer;
		font-size: 1rem;
		font-weight: 600;
		margin-top: 0.75rem;
		transition: background 0.15s;
	}

	.btn:hover:not(:disabled) {
		background: var(--color-cta-hover);
	}

	.btn:active:not(:disabled) {
		background: var(--color-cta-active);
	}

	.btn:disabled {
		opacity: 0.7;
		cursor: not-allowed;
	}

	.error {
		background: var(--color-error-bg);
		border: 1px solid var(--color-error-border);
		color: var(--color-error-text);
		text-align: center;
		padding: 0.65rem 0.75rem;
		margin-top: 1rem;
		border-radius: var(--radius-sm);
		font-size: 0.875rem;
	}

	.forgot-link {
		text-align: right;
		margin-bottom: 0.5rem;
		font-size: 0.8125rem;
	}

	.forgot-link a {
		color: var(--color-muted);
		text-decoration: none;
		border-bottom: 1px dashed var(--color-muted);
	}

	.forgot-link a:hover {
		color: var(--color-primary);
		border-bottom-color: var(--color-primary);
	}

	.register-link {
		text-align: center;
		margin-top: 1.25rem;
		font-size: 0.8125rem;
		color: var(--color-muted);
	}

	.register-link a {
		color: var(--color-primary);
		text-decoration: none;
		border-bottom: 1px dashed var(--color-muted);
	}

	.register-link a:hover {
		color: var(--color-cta);
		border-bottom-color: var(--color-cta);
	}

	.footer {
		position: fixed;
		bottom: 0;
		left: 0;
		right: 0;
		text-align: center;
		padding: 1rem;
		color: var(--color-muted);
		font-size: 0.75rem;
		background: var(--color-bg);
	}

	.footer-icon {
		display: inline-block;
		vertical-align: middle;
		margin-right: 4px;
	}

	.footer-icon :global(svg) {
		width: 12px;
		height: 12px;
		fill: var(--color-cta);
	}

	@media (max-width: 480px) {
		.login-card {
			margin: 0 1rem;
			padding: 2rem 1.5rem 1.5rem;
		}
	}
</style>
