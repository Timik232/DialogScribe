<div class="hero">
	<h1>DialogScribe</h1>
	<p class="subtitle">Транскрипция аудио</p>
	<div class="actions">
		<a href="/transcribe" class="btn btn-primary">Начать транскрипцию</a>
		<a href="/analysis" class="btn btn-secondary">Анализ</a>
	</div>
</div>

<style>
	.hero {
		display: flex;
		flex-direction: column;
		align-items: center;
		justify-content: center;
		min-height: calc(100vh - var(--header-height));
		text-align: center;
		padding: 2rem;
	}
	h1 {
		font-size: 2.5rem;
		margin-bottom: 0.5rem;
	}
	.subtitle {
		color: var(--color-muted);
		font-size: 1.125rem;
		margin-bottom: 2rem;
	}
	.actions {
		display: flex;
		gap: 1rem;
	}
</style>
