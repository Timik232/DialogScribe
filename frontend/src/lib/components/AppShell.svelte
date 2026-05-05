<script lang="ts">
	import ThemeToggle from './ThemeToggle.svelte';
	import { authStore } from '$lib/stores/auth';
	import '$lib/styles/global.css';
	import '$lib/styles/components.css';

	interface Props {
		children: import('svelte').Snippet;
	}

	let { children }: Props = $props();
	let menuOpen = $state(false);
</script>

<header class="app-header">
	<div class="header-content">
		<a href="/" class="logo">
			<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="2">
				<path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"/>
				<path d="M19 10v2a7 7 0 0 1-14 0v-2"/>
				<line x1="12" y1="19" x2="12" y2="23"/>
				<line x1="8" y1="23" x2="16" y2="23"/>
			</svg>
			<span class="logo-text">DialogScribe</span>
		</a>
		<nav class="header-nav">
			<a href="/transcribe" class="nav-link">Транскрипция</a>
			<a href="/analysis" class="nav-link">Анализ</a>
			<a href="/templates" class="nav-link">Шаблоны</a>
			<a href="/transcriptions" class="nav-link">Расшифровки</a>
			<a href="/docs" class="nav-link">Документация</a>
			<a href="/live-hints" class="nav-link">Подсказки</a>
			{#if $authStore.user?.role === 'admin'}
				<a href="/admin" class="nav-link">Админ</a>
			{/if}
		</nav>
		<div class="header-actions">
			<ThemeToggle />
			<button class="hamburger" onclick={() => menuOpen = !menuOpen} aria-label="Меню">
				<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="2">
					<line x1="3" y1="6" x2="21" y2="6"/>
					<line x1="3" y1="12" x2="21" y2="12"/>
					<line x1="3" y1="18" x2="21" y2="18"/>
				</svg>
			</button>
		</div>
	</div>
</header>

{#if menuOpen}
	<div class="mobile-menu-overlay" role="button" tabindex="-1" onclick={() => menuOpen = false} onkeydown={() => menuOpen = false}></div>
	<nav class="mobile-nav">
		<a href="/transcribe" class="mobile-link" onclick={() => menuOpen = false}>Транскрипция</a>
		<a href="/analysis" class="mobile-link" onclick={() => menuOpen = false}>Анализ</a>
		<a href="/templates" class="mobile-link" onclick={() => menuOpen = false}>Шаблоны</a>
		<a href="/transcriptions" class="mobile-link" onclick={() => menuOpen = false}>Расшифровки</a>
		<a href="/docs" class="mobile-link" onclick={() => menuOpen = false}>Документация</a>
		<a href="/live-hints" class="mobile-link" onclick={() => menuOpen = false}>Подсказки</a>
		{#if $authStore.user?.role === 'admin'}
			<a href="/admin" class="mobile-link" onclick={() => menuOpen = false}>Админ</a>
		{/if}
	</nav>
{/if}

<main class="app-main">
	{@render children()}
</main>

<style>
	.app-header {
		background-color: #1A2332;
		height: var(--header-height);
		display: flex;
		align-items: center;
		position: sticky;
		top: 0;
		z-index: 100;
		border-bottom: 1px solid rgba(255, 255, 255, 0.1);
	}
	.header-content {
		width: 100%;
		max-width: 1200px;
		margin: 0 auto;
		padding: 0 1rem;
		display: flex;
		align-items: center;
		gap: 1.5rem;
	}
	.logo {
		display: flex;
		align-items: center;
		gap: 0.5rem;
		text-decoration: none;
	}
	.logo-text {
		color: #fff;
		font-size: 1.125rem;
		font-weight: 600;
	}
	.header-nav {
		display: flex;
		gap: 0.25rem;
		margin-left: 1rem;
	}
	.nav-link {
		color: rgba(255, 255, 255, 0.7);
		padding: 0.375rem 0.75rem;
		border-radius: var(--radius-sm);
		font-size: 0.875rem;
		transition: color 0.15s, background-color 0.15s;
	}
	.nav-link:hover {
		color: #fff;
		background-color: rgba(255, 255, 255, 0.1);
	}
	.header-actions {
		margin-left: auto;
		display: flex;
		align-items: center;
		gap: 0.5rem;
	}
	.hamburger {
		display: none;
		background: none;
		border: none;
		cursor: pointer;
		padding: 0.375rem;
		border-radius: var(--radius-sm);
		transition: background-color 0.15s;
	}
	.hamburger:hover {
		background-color: rgba(255, 255, 255, 0.1);
	}
	.mobile-menu-overlay {
		position: fixed;
		inset: 0;
		top: var(--header-height);
		background-color: rgba(0, 0, 0, 0.5);
		z-index: 99;
	}
	.mobile-nav {
		position: fixed;
		top: var(--header-height);
		left: 0;
		right: 0;
		background-color: #1A2332;
		border-bottom: 1px solid rgba(255, 255, 255, 0.1);
		z-index: 100;
		display: flex;
		flex-direction: column;
	}
	.mobile-link {
		color: rgba(255, 255, 255, 0.85);
		padding: 1rem 1.5rem;
		min-height: 44px;
		font-size: 1rem;
		border-bottom: 1px solid rgba(255, 255, 255, 0.06);
		transition: background-color 0.15s;
	}
	.mobile-link:hover {
		color: #fff;
		background-color: rgba(255, 255, 255, 0.08);
	}
	.app-main {
		min-height: calc(100vh - var(--header-height));
	}

	@media (max-width: 768px) {
		.header-nav {
			display: none;
		}
		.hamburger {
			display: flex;
		}
	}
</style>
