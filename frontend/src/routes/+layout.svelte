<script lang="ts">
	import AppShell from '$lib/components/AppShell.svelte';
	import { authStore } from '$lib/stores/auth';
	import { goto } from '$app/navigation';
	import { page } from '$app/stores';
	import { browser } from '$app/environment';

	let { children } = $props();

	const publicRoutes = ['/login', '/register', '/docs', '/forgot-password', '/reset-password'];
	const publicPrefixes = ['/share'];

	function isPublicRoute(path: string): boolean {
		return publicRoutes.includes(path) || publicPrefixes.some((p) => path.startsWith(p + '/') || path === p);
	}

	$effect(() => {
		if (!browser) return;
		const currentPath = $page.url.pathname;
		if (isPublicRoute(currentPath)) return;
		if ($authStore.loading) return;
		if (!$authStore.isAuthenticated) {
			const return_url = encodeURIComponent(currentPath + $page.url.search);
			goto(`/login?return_url=${return_url}`);
		}
	});
</script>

{#if $authStore.loading && !isPublicRoute($page.url.pathname)}
{:else if isPublicRoute($page.url.pathname)}
	{@render children()}
{:else}
	<AppShell>
		{@render children()}
	</AppShell>
{/if}
