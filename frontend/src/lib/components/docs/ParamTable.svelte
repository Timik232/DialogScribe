<script lang="ts">
	import type { ApiParam } from './data';

	interface Props {
		params: ApiParam[];
	}

	let { params }: Props = $props();
</script>

{#if params.length > 0}
	<div class="param-table-wrap">
		<table class="param-table">
			<thead>
				<tr>
					<th>Параметр</th>
					<th>Тип</th>
					<th>Обязательный</th>
					<th>По умолчанию</th>
					<th>Описание</th>
				</tr>
			</thead>
			<tbody>
				{#each params as param}
					<tr>
						<td><code class="param-name">{param.name}</code></td>
						<td><span class="param-type">{param.type}</span></td>
						<td>
							{#if param.required}
								<span class="required-badge">Да</span>
							{:else}
								<span class="optional">Нет</span>
							{/if}
						</td>
						<td>{param.default ?? '—'}</td>
						<td>{param.description}</td>
					</tr>
				{/each}
			</tbody>
		</table>
	</div>
{/if}

<style>
	.param-table-wrap {
		overflow-x: auto;
		margin: 0.75rem 0;
		border: 1px solid var(--color-border);
		border-radius: var(--radius);
	}

	.param-table {
		width: 100%;
		border-collapse: collapse;
		font-size: 0.8125rem;
	}

	.param-table th {
		text-align: left;
		padding: 0.625rem 0.75rem;
		background-color: var(--color-border);
		background-color: color-mix(in srgb, var(--color-border) 40%, transparent);
		font-weight: 600;
		font-size: 0.75rem;
		text-transform: uppercase;
		letter-spacing: 0.3px;
		color: var(--color-muted);
		border-bottom: 1px solid var(--color-border);
	}

	.param-table td {
		padding: 0.5rem 0.75rem;
		border-bottom: 1px solid var(--color-border);
		color: var(--color-text);
		vertical-align: top;
	}

	.param-table tbody tr:last-child td {
		border-bottom: none;
	}

	.param-name {
		background-color: color-mix(in srgb, var(--color-cta) 10%, transparent);
		color: var(--color-cta);
		padding: 0.125rem 0.375rem;
		border-radius: 3px;
		font-family: 'SF Mono', 'Fira Code', monospace;
		font-size: 0.8rem;
	}

	.param-type {
		color: var(--color-muted);
		font-style: italic;
		font-size: 0.8rem;
	}

	.required-badge {
		display: inline-block;
		background-color: #d32f2f;
		color: #fff;
		font-size: 0.6875rem;
		padding: 0.125rem 0.375rem;
		border-radius: 3px;
		font-weight: 600;
	}

	.optional {
		color: var(--color-muted);
		font-size: 0.8rem;
	}
</style>
