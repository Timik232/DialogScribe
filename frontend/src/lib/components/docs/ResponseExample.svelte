<script lang="ts">
	interface Props {
		status: number;
		body: string;
		isError?: boolean;
	}

	let { status, body, isError = false }: Props = $props();
</script>

<div class="response-block">
	<div class="response-header">
		<span class="status-badge" class:status-success={!isError} class:status-error={isError}>
			{status}
		</span>
		<span class="response-label">{isError ? 'Ошибка' : 'Успешный ответ'}</span>
	</div>
	<pre><code>{body}</code></pre>
</div>

<style>
	.response-block {
		border-radius: var(--radius);
		overflow: hidden;
		margin: 0.75rem 0;
		border: 1px solid var(--color-border);
	}

	.response-header {
		display: flex;
		align-items: center;
		gap: 0.5rem;
		padding: 0.5rem 1rem;
		background-color: var(--color-border);
		background-color: color-mix(in srgb, var(--color-border) 40%, transparent);
		border-bottom: 1px solid var(--color-border);
	}

	.status-badge {
		display: inline-block;
		padding: 0.125rem 0.5rem;
		border-radius: 4px;
		font-size: 0.75rem;
		font-weight: 700;
		font-family: 'SF Mono', 'Fira Code', monospace;
	}

	.status-success {
		background-color: #21a038;
		color: #fff;
	}

	.status-error {
		background-color: #d32f2f;
		color: #fff;
	}

	.response-label {
		font-size: 0.8125rem;
		color: var(--color-muted);
	}

	pre {
		background-color: #1e1e2e;
		margin: 0;
		padding: 1rem;
		overflow-x: auto;
		font-size: 0.8125rem;
		line-height: 1.6;
	}

	code {
		color: #cdd6f4;
		font-family: 'SF Mono', 'Fira Code', 'Cascadia Code', monospace;
		font-size: 0.8125rem;
	}
</style>
