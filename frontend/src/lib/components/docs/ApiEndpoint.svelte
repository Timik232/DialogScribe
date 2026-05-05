<script lang="ts">
	import type { ApiEndpoint } from './data';
	import ParamTable from './ParamTable.svelte';
	import CodeExample from './CodeExample.svelte';
	import ResponseExample from './ResponseExample.svelte';

	interface Props {
		endpoint: ApiEndpoint;
	}

	let { endpoint }: Props = $props();

	const methodColors: Record<string, string> = {
		GET: '#21A038',
		POST: '#2B7DE9',
		PUT: '#E69500',
		PATCH: '#D4A017',
		DELETE: '#D32F2F',
		WS: '#7B61FF'
	};
</script>

<div class="endpoint-card">
	<div class="endpoint-header">
		<span class="method-badge" style="background-color: {methodColors[endpoint.method]}">
			{endpoint.method}
		</span>
		<code class="endpoint-path">{endpoint.path}</code>
	</div>
	<p class="endpoint-desc">{endpoint.description}</p>

	{#if endpoint.parameters.length > 0}
		<h4 class="section-subtitle">Параметры</h4>
		<ParamTable params={endpoint.parameters} />
	{/if}

	<h4 class="section-subtitle">Пример запроса (cURL)</h4>
	<CodeExample code={endpoint.curlExample} language="bash" />

	<h4 class="section-subtitle">Пример запроса (JavaScript)</h4>
	<CodeExample code={endpoint.jsExample} language="javascript" />

	<h4 class="section-subtitle">Ответ {endpoint.successResponse.status}</h4>
	<ResponseExample
		status={endpoint.successResponse.status}
		body={endpoint.successResponse.body}
	/>

	{#if endpoint.errorResponses && endpoint.errorResponses.length > 0}
		<h4 class="section-subtitle">Ошибки</h4>
		{#each endpoint.errorResponses as err}
			<ResponseExample
				status={err.status}
				body={err.body}
				isError={true}
			/>
		{/each}
	{/if}
</div>

<style>
	.endpoint-card {
		background-color: var(--color-card);
		border: 1px solid var(--color-border);
		border-radius: var(--radius);
		padding: 1.5rem;
		margin-bottom: 1.5rem;
	}

	.endpoint-header {
		display: flex;
		align-items: center;
		gap: 0.75rem;
		flex-wrap: wrap;
		margin-bottom: 0.75rem;
	}

	.method-badge {
		display: inline-block;
		padding: 0.25rem 0.625rem;
		border-radius: 4px;
		color: #fff;
		font-size: 0.75rem;
		font-weight: 700;
		font-family: 'SF Mono', 'Fira Code', monospace;
		letter-spacing: 0.5px;
		flex-shrink: 0;
	}

	.endpoint-path {
		font-family: 'SF Mono', 'Fira Code', 'Cascadia Code', monospace;
		font-size: 0.9375rem;
		color: var(--color-text);
		background-color: color-mix(in srgb, var(--color-border) 30%, transparent);
		padding: 0.25rem 0.5rem;
		border-radius: 4px;
		word-break: break-all;
	}

	.endpoint-desc {
		color: var(--color-muted);
		font-size: 0.9375rem;
		margin-bottom: 1rem;
		line-height: 1.5;
	}

	.section-subtitle {
		font-size: 0.8125rem;
		font-weight: 600;
		color: var(--color-muted);
		margin: 1.25rem 0 0.5rem;
		text-transform: uppercase;
		letter-spacing: 0.3px;
	}

	.section-subtitle:first-of-type {
		margin-top: 0.5rem;
	}
</style>
