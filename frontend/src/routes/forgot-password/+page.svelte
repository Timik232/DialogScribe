<script lang="ts">
	import { forgotPassword } from '$lib/services/auth';

	let email = $state('');
	let error = $state('');
	let loading = $state(false);
	let success = $state(false);

	async function handleSubmit(e: Event) {
		e.preventDefault();
		error = '';
		loading = true;

		try {
			await forgotPassword(email);
			success = true;
		} catch (e: any) {
			error = e?.message || 'Ошибка отправки письма';
		} finally {
			loading = false;
		}
	}
</script>

<svelte:head>
	<title>Сброс пароля</title>
</svelte:head>

<div class="top-bar"></div>

<div class="login-wrapper">
	<div class="login-card">
		<h1>Сброс пароля</h1>
		<p class="subtitle">Введите email для восстановления доступа</p>

		{#if success}
			<div class="success-box">
				<div class="success-icon">✓</div>
				<p class="success-text">Письмо отправлено</p>
				<p class="success-sub">Если аккаунт с таким email существует, мы отправили ссылку для сброса пароля</p>
				<a href="/login" class="btn" style="display:inline-block; text-align:center; text-decoration:none; margin-top:1rem;">Вернуться к входу</a>
			</div>
		{:else}
			<form onsubmit={handleSubmit}>
				<div class="field">
					<span class="field-icon">
						<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 4h16c1.1 0 2 .9 2 2v12c0 1.1-.9 2-2 2H4c-1.1 0-2-.9-2-2V6c0-1.1.9-2 2-2z"/><polyline points="22,6 12,13 2,6"/></svg>
					</span>
					<input
						type="email"
						bind:value={email}
						placeholder="Email"
						required
						autofocus
						disabled={loading}
					/>
				</div>

				<button type="submit" class="btn" disabled={loading}>
					{loading ? 'Отправка...' : 'Отправить ссылку'}
				</button>
			</form>

			{#if error}
				<div class="error">{error}</div>
			{/if}
		{/if}

		<div class="back-link">
			<a href="/login">Вернуться к входу</a>
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

	input[type='email'] {
		width: 100%;
		padding: 0.75rem 0.75rem 0.75rem 2.75rem;
		border: 1px solid var(--color-border);
		border-radius: var(--radius-sm);
		font-size: 0.9375rem;
		color: var(--color-text);
		background: var(--color-card);
		transition: border-color 0.15s, box-shadow 0.15s;
	}

	input[type='email']:focus {
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

	.back-link {
		text-align: center;
		margin-top: 1.25rem;
		font-size: 0.8125rem;
		color: var(--color-muted);
	}

	.back-link a {
		color: var(--color-primary);
		text-decoration: none;
		border-bottom: 1px dashed var(--color-muted);
	}

	.back-link a:hover {
		color: var(--color-cta);
		border-bottom-color: var(--color-cta);
	}

	.success-box {
		text-align: center;
		padding: 1.5rem 0;
	}

	.success-icon {
		width: 48px;
		height: 48px;
		line-height: 48px;
		border-radius: 50%;
		background: #22c55e;
		color: #fff;
		font-size: 1.5rem;
		margin: 0 auto 1rem;
	}

	.success-text {
		font-weight: 600;
		color: var(--color-text);
		font-size: 1.1rem;
	}

	.success-sub {
		color: var(--color-muted);
		font-size: 0.875rem;
		margin-top: 0.5rem;
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
