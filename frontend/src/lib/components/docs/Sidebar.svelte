<script lang="ts">
	import { sidebarSections } from './data';

	let activeSection = $state('overview');
	let mobileOpen = $state(false);

	$effect(() => {
		const observer = new IntersectionObserver(
			(entries) => {
				for (const entry of entries) {
					if (entry.isIntersecting) {
						activeSection = entry.target.id;
					}
				}
			},
			{ rootMargin: '-80px 0px -60% 0px', threshold: 0.1 }
		);

		for (const section of sidebarSections) {
			const el = document.getElementById(section.id);
			if (el) observer.observe(el);
		}

		return () => observer.disconnect();
	});

	function scrollTo(id: string) {
		const el = document.getElementById(id);
		if (el) {
			el.scrollIntoView({ behavior: 'smooth', block: 'start' });
			activeSection = id;
			mobileOpen = false;
		}
	}
</script>

{#if mobileOpen}
	<div class="sidebar-overlay" onclick={() => mobileOpen = false} role="button" tabindex="-1"></div>
{/if}

<button class="sidebar-toggle" onclick={() => mobileOpen = !mobileOpen} aria-label="Навигация по документации">
	<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
		<line x1="3" y1="6" x2="21" y2="6"/>
		<line x1="3" y1="12" x2="21" y2="12"/>
		<line x1="3" y1="18" x2="21" y2="18"/>
	</svg>
</button>

<aside class="sidebar" class:sidebar-open={mobileOpen}>
	<nav class="sidebar-nav">
		{#each sidebarSections as section}
			<button
				class="sidebar-link"
				class:active={activeSection === section.id}
				onclick={() => scrollTo(section.id)}
			>
				{section.label}
			</button>
		{/each}
	</nav>
</aside>

<style>
	.sidebar-toggle {
		display: none;
		position: fixed;
		bottom: 1.5rem;
		right: 1.5rem;
		z-index: 50;
		width: 44px;
		height: 44px;
		border-radius: 50%;
		background-color: var(--color-cta);
		color: #fff;
		border: none;
		cursor: pointer;
		align-items: center;
		justify-content: center;
		box-shadow: 0 2px 8px rgba(0, 0, 0, 0.2);
	}

	.sidebar-overlay {
		display: none;
		position: fixed;
		inset: 0;
		z-index: 39;
		background-color: rgba(0, 0, 0, 0.4);
	}

	.sidebar {
		position: sticky;
		top: calc(var(--header-height) + 1rem);
		width: 260px;
		min-width: 260px;
		max-height: calc(100vh - var(--header-height) - 2rem);
		overflow-y: auto;
		padding: 1rem 0;
	}

	.sidebar-nav {
		display: flex;
		flex-direction: column;
		gap: 2px;
	}

	.sidebar-link {
		display: block;
		width: 100%;
		text-align: left;
		padding: 0.5rem 1rem;
		border: none;
		background: none;
		color: var(--color-muted);
		font-size: 0.875rem;
		cursor: pointer;
		border-radius: var(--radius-sm);
		transition: color 0.15s, background-color 0.15s;
		font-family: var(--font-family);
	}

	.sidebar-link:hover {
		color: var(--color-text);
		background-color: var(--color-border);
		opacity: 0.5;
	}

	.sidebar-link.active {
		color: var(--color-cta);
		background-color: var(--color-cta);
		background-color: color-mix(in srgb, var(--color-cta) 10%, transparent);
		font-weight: 600;
	}

	@media (max-width: 768px) {
		.sidebar-toggle {
			display: flex;
		}

		.sidebar-overlay {
			display: block;
		}

		.sidebar {
			position: fixed;
			top: var(--header-height);
			left: 0;
			bottom: 0;
			width: 280px;
			min-width: unset;
			max-height: unset;
			background-color: var(--color-card);
			border-right: 1px solid var(--color-border);
			z-index: 40;
			transform: translateX(-100%);
			transition: transform 0.25s ease;
			padding: 1.5rem 0.5rem;
		}

		.sidebar-open {
			transform: translateX(0);
		}
	}
</style>
