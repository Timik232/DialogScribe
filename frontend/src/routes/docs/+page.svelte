<script lang="ts">
	import Sidebar from '$lib/components/docs/Sidebar.svelte';
	import ApiEndpoint from '$lib/components/docs/ApiEndpoint.svelte';
	import CodeExample from '$lib/components/docs/CodeExample.svelte';
	import { endpoints, faqItems } from '$lib/components/docs/data';

	let expandedFaq = $state<number | null>(null);

	function toggleFaq(index: number) {
		expandedFaq = expandedFaq === index ? null : index;
	}

	const transcriptionEndpoints = endpoints.filter(e => e.tag === 'transcription');
	const analysisEndpoints = endpoints.filter(e => e.tag === 'analysis');
	const autoflowEndpoints = endpoints.filter(e => e.tag === 'autoflow');
	const exportEndpoints = endpoints.filter(e => e.tag === 'export');
	const templateEndpoints = endpoints.filter(e => e.tag === 'templates');
</script>

<svelte:head>
	<title>Документация — DialogScribe</title>
</svelte:head>

<div class="docs-layout">
	<Sidebar />
	<div class="docs-content">
		<section id="overview" class="docs-section">
			<h1>DialogScribe — Документация API</h1>
			<p class="lead">
				DialogScribe — сервис транскрипции аудио и видео на базе <strong>GigaAM</strong> с диаризацией спикеров,
				анализом текста, автоматическими пайплайнами и экспортом в различные форматы.
			</p>

			<div class="features-grid">
				<div class="feature-card">
					<span class="feature-icon">🎙️</span>
					<h3>Транскрипция</h3>
					<p>Распознавание речи на русском и английском языках с опциональной диаризацией спикеров через pyannote.</p>
				</div>
				<div class="feature-card">
					<span class="feature-icon">🧠</span>
					<h3>Анализ</h3>
					<p>Генерация саммари, майндмапов, инсайтов и чат с LLM по тексту транскрипции.</p>
				</div>
				<div class="feature-card">
					<span class="feature-icon">⚡</span>
					<h3>Autoflow</h3>
					<p>WebSocket-пайплайн для полной автоматической обработки: транскрипция → анализ → экспорт.</p>
				</div>
				<div class="feature-card">
					<span class="feature-icon">📄</span>
					<h3>Экспорт</h3>
					<p>Выгрузка результатов в TXT, JSON, SRT, VTT, DOCX и PDF с переименованием спикеров.</p>
				</div>
				<div class="feature-card">
					<span class="feature-icon">📋</span>
					<h3>Шаблоны</h3>
					<p>Настраиваемые шаблоны промптов для суммаризации с импортом и экспортом конфигураций.</p>
				</div>
			</div>
		</section>

		<section id="quickstart" class="docs-section">
			<h2>Быстрый старт</h2>

			<h3>1. Регистрация</h3>
			<CodeExample
				language="bash"
				code={`curl -X POST http://localhost:8000/api/auth/register \\
  -H "Content-Type: application/json" \\
  -d '{"email": "user@example.com", "username": "ivan", "password": "mypassword1'}'`}
			/>

			<h3>2. Получение токена</h3>
			<CodeExample
				language="bash"
				code={`curl -X POST http://localhost:8000/api/auth/login \\
  -H "Content-Type: application/json" \\
  -d '{"login": "ivan", "password": "mypassword1"}'`}
			/>
			<p class="hint">Сохраните <code>access_token</code> из ответа — он нужен для всех запросов.</p>

			<h3>3. Первая транскрипция</h3>
			<CodeExample
				language="bash"
				code={`curl -X POST http://localhost:8000/api/transcribe \\
  -H "Authorization: Bearer YOUR_ACCESS_TOKEN" \\
  -F "file=@meeting.mp3" \\
  -F "diarization_mode=pyannote" \\
  -F "language=ru"`}
			/>

			<h3>4. Анализ результата</h3>
			<CodeExample
				language="bash"
				code={`curl -X POST http://localhost:8000/api/summary \\
  -H "Authorization: Bearer YOUR_ACCESS_TOKEN" \\
  -H "Content-Type: application/json" \\
  -d '{"text": "ТЕКСТ_ТРАНСКРИПЦИИ", "template_key": "meeting"}'`}
			/>
		</section>

		<section id="transcription" class="docs-section">
			<h2>Транскрипция</h2>

			<h3>Поддерживаемые форматы</h3>
			<div class="format-list">
				<div class="format-group">
					<strong>Аудио:</strong> WAV, MP3, FLAC, OGG, M4A, AAC, WMA, OPUS
				</div>
				<div class="format-group">
					<strong>Видео:</strong> MP4, MKV, AVI, MOV, WebM, WMV, FLV, MPEG
				</div>
			</div>
			<p class="hint">Максимальный размер файла — 1 ГБ. Рекомендуется WAV или FLAC для наилучшего качества.</p>

			<h3>Режимы диаризации</h3>
			<div class="diarization-modes">
				<div class="mode-card">
					<strong>none</strong>
					<span>Только текст, без определения спикеров</span>
				</div>
				<div class="mode-card">
					<strong>pyannote</strong>
					<span>Точная диаризация через pyannote/speaker-diarization-3.1</span>
				</div>
				<div class="mode-card">
					<strong>hybrid</strong>
					<span>Лёгкий режим: VAD + кластеризация (быстрее)</span>
				</div>
			</div>

			<h3>Параметры шумоподавления</h3>
			<p>Параметр <code>denoise=true</code> включает предобработку аудио для удаления фонового шума. Рекомендуется для записей с высоким уровнем шума.</p>

			<h3>Эндпоинты</h3>
			{#each transcriptionEndpoints as endpoint}
				<ApiEndpoint {endpoint} />
			{/each}
		</section>

		<section id="analysis" class="docs-section">
			<h2>Анализ</h2>
			<p>Модуль анализа предоставляет инструменты для работы с текстом транскрипции через LLM-модели.</p>

			<div class="analysis-features">
				<div class="analysis-item">
					<h4>📝 Суммаризация</h4>
					<p>Генерация краткого содержания с использованием настраиваемых шаблонов (<code>template_key</code>: general, meeting, interview, lecture).</p>
				</div>
				<div class="analysis-item">
					<h4>🗺️ Майндмап</h4>
					<p>Автоматическое создание структуры майндмапа в формате Markdown и HTML для визуализации ключевых тем.</p>
				</div>
				<div class="analysis-item">
					<h4>💡 Инсайты</h4>
					<p>Извлечение плана действий (<code>action_items</code>) и рекомендуемых шагов (<code>suggested_steps</code>).</p>
				</div>
				<div class="analysis-item">
					<h4>💬 Чат</h4>
					<p>Мультитурный диалог с LLM по тексту транскрипции. Задавайте вопросы и получайте ответы с учётом контекста.</p>
				</div>
			</div>

			<h3>Эндпоинты</h3>
			{#each analysisEndpoints as endpoint}
				<ApiEndpoint {endpoint} />
			{/each}
		</section>

		<section id="autoflow" class="docs-section">
			<h2>Autoflow</h2>
			<p>Autoflow — это WebSocket-пайплайн для автоматической обработки файла за одно соединение.</p>

			<div class="autoflow-stages">
				<div class="stage">
					<span class="stage-num">1</span>
					<strong>Транскрипция</strong>
				</div>
				<div class="stage-arrow">→</div>
				<div class="stage">
					<span class="stage-num">2</span>
					<strong>Суммаризация</strong>
				</div>
				<div class="stage-arrow">→</div>
				<div class="stage">
					<span class="stage-num">3</span>
					<strong>Майндмап</strong>
				</div>
				<div class="stage-arrow">→</div>
				<div class="stage">
					<span class="stage-num">4</span>
					<strong>Инсайты</strong>
				</div>
			</div>

			<h3>Формат прогресса</h3>
			<p>На каждом этапе сервер отправляет сообщения с прогрессом:</p>
			<pre class="inline-code"><code>{`{ "stage": "transcription", "progress": 45, "message": "Транскрибируем..." }`}</code></pre>

			<h3>Формат завершения</h3>
			<p>По окончании отправляется сообщение со stage <code>"complete"</code> и полным результатом:</p>
			<pre class="inline-code"><code>{`{
  "stage": "complete",
  "result": {
    "transcription": { "segments": [...], "text": "..." },
    "summary": { "summary_markdown": "..." },
    "mindmap_html": "...",
    "mindmap_md": "...",
    "action_items": [...],
    "suggested_steps": [...],
    "errors": [],
    "stage_timings": { "transcription": 12.3, "summary": 3.1 }
  }
}`}</code></pre>

			<h3>Эндпоинт</h3>
			{#each autoflowEndpoints as endpoint}
				<ApiEndpoint {endpoint} />
			{/each}
		</section>

		<section id="export" class="docs-section">
			<h2>Экспорт</h2>
			<p>Экспорт результатов транскрипции и анализа в различные форматы.</p>

			<div class="format-grid">
				<div class="format-tag">TXT</div>
				<div class="format-tag">JSON</div>
				<div class="format-tag">SRT</div>
				<div class="format-tag">VTT</div>
				<div class="format-tag">DOCX</div>
				<div class="format-tag">PDF</div>
			</div>

			<h3>Переименование спикеров</h3>
			<p>Параметр <code>speaker_names</code> позволяет задать отображаемые имена для спикеров вместо «Спикер №1», «Спикер №2» и т.д.</p>

			<h3>Эндпоинты</h3>
			{#each exportEndpoints as endpoint}
				<ApiEndpoint {endpoint} />
			{/each}
		</section>

		<section id="templates" class="docs-section">
			<h2>Шаблоны</h2>
			<p>Шаблоны позволяют настраивать промпты для суммаризации под конкретные задачи.</p>

			<h3>Встроенные шаблоны</h3>
			<div class="built-in-templates">
				<div class="template-item">
					<span>📝</span> <strong>general</strong> — общее краткое содержание
				</div>
				<div class="template-item">
					<span>🤝</span> <strong>meeting</strong> — протокол встречи
				</div>
				<div class="template-item">
					<span>🎤</span> <strong>interview</strong> — структура интервью
				</div>
				<div class="template-item">
					<span>🎓</span> <strong>lecture</strong> — конспект лекции
				</div>
			</div>

			<h3>CRUD и импорт/экспорт</h3>
			<p>Создавайте, обновляйте и удаляйте шаблоны. Экспортируйте конфигурации для резервного копирования и импортируйте на других серверах.</p>

			<h3>Эндпоинты</h3>
			{#each templateEndpoints as endpoint}
				<ApiEndpoint {endpoint} />
			{/each}
		</section>

		<section id="api-reference" class="docs-section">
			<h2>API Reference</h2>
			<p>Полный список всех доступных эндпоинтов.</p>

			{#each endpoints as endpoint}
				<ApiEndpoint {endpoint} />
			{/each}
		</section>

		<section id="faq" class="docs-section">
			<h2>Часто задаваемые вопросы</h2>
			<div class="faq-list">
				{#each faqItems as item, index}
					<div class="faq-item">
						<button class="faq-question" onclick={() => toggleFaq(index)}>
							<span>{item.question}</span>
							<svg class="faq-chevron" class:rotated={expandedFaq === index} width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
								<polyline points="6 9 12 15 18 9"/>
							</svg>
						</button>
						{#if expandedFaq === index}
							<div class="faq-answer">
								{@html item.answer}
							</div>
						{/if}
					</div>
				{/each}
			</div>
		</section>
	</div>
</div>

<style>
	.docs-layout {
		display: flex;
		gap: 2rem;
		max-width: 1200px;
		margin: 0 auto;
		padding: 1.5rem 1rem;
	}

	.docs-content {
		flex: 1;
		min-width: 0;
	}

	.docs-section {
		margin-bottom: 3rem;
		padding-top: 1rem;
	}

	.docs-section h1 {
		font-size: 2rem;
		margin-bottom: 1rem;
	}

	.docs-section h2 {
		font-size: 1.5rem;
		margin-bottom: 0.75rem;
		padding-bottom: 0.5rem;
		border-bottom: 2px solid var(--color-border);
	}

	.docs-section h3 {
		margin-top: 1.5rem;
		margin-bottom: 0.5rem;
	}

	.docs-section p {
		margin-bottom: 0.75rem;
		line-height: 1.65;
		color: var(--color-text);
	}

	.lead {
		font-size: 1.0625rem;
		color: var(--color-muted);
		line-height: 1.7;
	}

	.hint {
		font-size: 0.875rem;
		color: var(--color-muted);
		padding: 0.5rem 0.75rem;
		background-color: color-mix(in srgb, var(--color-cta) 5%, transparent);
		border-left: 3px solid var(--color-cta);
		border-radius: 0 var(--radius-sm) var(--radius-sm) 0;
		margin: 0.5rem 0 1rem;
	}

	code {
		background-color: color-mix(in srgb, var(--color-border) 50%, transparent);
		padding: 0.125rem 0.375rem;
		border-radius: 3px;
		font-size: 0.875em;
		font-family: 'SF Mono', 'Fira Code', monospace;
	}

	.features-grid {
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
		gap: 1rem;
		margin-top: 1.5rem;
	}

	.feature-card {
		background-color: var(--color-card);
		border: 1px solid var(--color-border);
		border-radius: var(--radius);
		padding: 1.25rem;
	}

	.feature-icon {
		font-size: 1.5rem;
		display: block;
		margin-bottom: 0.5rem;
	}

	.feature-card h3 {
		margin: 0 0 0.375rem;
		font-size: 0.9375rem;
	}

	.feature-card p {
		font-size: 0.8125rem;
		color: var(--color-muted);
		margin: 0;
	}

	.format-list {
		display: flex;
		gap: 1.5rem;
		flex-wrap: wrap;
		margin: 0.5rem 0;
	}

	.format-group {
		font-size: 0.9375rem;
	}

	.format-group strong {
		margin-right: 0.25rem;
	}

	.diarization-modes {
		display: flex;
		flex-direction: column;
		gap: 0.5rem;
		margin: 0.75rem 0;
	}

	.mode-card {
		display: flex;
		align-items: baseline;
		gap: 0.75rem;
		padding: 0.625rem 1rem;
		background-color: var(--color-card);
		border: 1px solid var(--color-border);
		border-radius: var(--radius-sm);
	}

	.mode-card strong {
		font-family: 'SF Mono', 'Fira Code', monospace;
		color: var(--color-cta);
		min-width: 70px;
	}

	.mode-card span {
		color: var(--color-muted);
		font-size: 0.875rem;
	}

	.analysis-features {
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
		gap: 1rem;
		margin: 0.75rem 0 1.5rem;
	}

	.analysis-item {
		background-color: var(--color-card);
		border: 1px solid var(--color-border);
		border-radius: var(--radius);
		padding: 1rem;
	}

	.analysis-item h4 {
		margin: 0 0 0.375rem;
		font-size: 0.9375rem;
	}

	.analysis-item p {
		font-size: 0.8125rem;
		color: var(--color-muted);
		margin: 0;
	}

	.autoflow-stages {
		display: flex;
		align-items: center;
		gap: 0.5rem;
		flex-wrap: wrap;
		margin: 1rem 0;
		padding: 1rem;
		background-color: var(--color-card);
		border: 1px solid var(--color-border);
		border-radius: var(--radius);
	}

	.stage {
		display: flex;
		align-items: center;
		gap: 0.5rem;
		padding: 0.5rem 0.75rem;
		background-color: color-mix(in srgb, var(--color-cta) 10%, transparent);
		border-radius: var(--radius-sm);
		font-size: 0.875rem;
	}

	.stage-num {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		width: 22px;
		height: 22px;
		border-radius: 50%;
		background-color: var(--color-cta);
		color: #fff;
		font-size: 0.75rem;
		font-weight: 700;
	}

	.stage-arrow {
		color: var(--color-muted);
		font-size: 1.25rem;
	}

	.inline-code {
		background-color: #1e1e2e;
		border-radius: var(--radius);
		padding: 1rem;
		overflow-x: auto;
		font-size: 0.8125rem;
		line-height: 1.6;
		margin: 0.5rem 0;
	}

	.inline-code code {
		color: #cdd6f4;
		background: none;
		padding: 0;
		font-size: 0.8125rem;
		font-family: 'SF Mono', 'Fira Code', monospace;
	}

	.format-grid {
		display: flex;
		flex-wrap: wrap;
		gap: 0.5rem;
		margin: 0.75rem 0;
	}

	.format-tag {
		padding: 0.375rem 0.75rem;
		background-color: var(--color-card);
		border: 1px solid var(--color-border);
		border-radius: var(--radius-sm);
		font-size: 0.8125rem;
		font-weight: 600;
		font-family: 'SF Mono', 'Fira Code', monospace;
	}

	.built-in-templates {
		display: flex;
		flex-direction: column;
		gap: 0.375rem;
		margin: 0.75rem 0;
	}

	.template-item {
		display: flex;
		align-items: center;
		gap: 0.5rem;
		padding: 0.5rem 0.75rem;
		background-color: var(--color-card);
		border: 1px solid var(--color-border);
		border-radius: var(--radius-sm);
		font-size: 0.875rem;
	}

	.template-item strong {
		font-family: 'SF Mono', 'Fira Code', monospace;
		color: var(--color-cta);
	}

	.faq-list {
		display: flex;
		flex-direction: column;
		gap: 0.5rem;
	}

	.faq-item {
		background-color: var(--color-card);
		border: 1px solid var(--color-border);
		border-radius: var(--radius);
		overflow: hidden;
	}

	.faq-question {
		width: 100%;
		text-align: left;
		padding: 1rem 1.25rem;
		background: none;
		border: none;
		cursor: pointer;
		display: flex;
		justify-content: space-between;
		align-items: center;
		gap: 0.75rem;
		font-size: 0.9375rem;
		font-weight: 500;
		color: var(--color-text);
		font-family: var(--font-family);
		transition: background-color 0.15s;
	}

	.faq-question:hover {
		background-color: color-mix(in srgb, var(--color-border) 30%, transparent);
	}

	.faq-chevron {
		flex-shrink: 0;
		transition: transform 0.2s;
		color: var(--color-muted);
	}

	.faq-chevron.rotated {
		transform: rotate(180deg);
	}

	.faq-answer {
		padding: 0 1.25rem 1rem;
		font-size: 0.875rem;
		line-height: 1.65;
		color: var(--color-text);
	}

	.faq-answer :global(code) {
		background-color: color-mix(in srgb, var(--color-border) 50%, transparent);
		padding: 0.125rem 0.375rem;
		border-radius: 3px;
		font-size: 0.8125em;
		font-family: 'SF Mono', 'Fira Code', monospace;
	}

	.faq-answer :global(strong) {
		color: var(--color-primary);
	}

	@media (max-width: 768px) {
		.docs-layout {
			flex-direction: column;
			padding: 1rem 0.75rem;
		}

		.docs-section h1 { font-size: 1.5rem; }
		.docs-section h2 { font-size: 1.25rem; }

		.features-grid {
			grid-template-columns: 1fr;
		}

		.analysis-features {
			grid-template-columns: 1fr;
		}

		.autoflow-stages {
			flex-direction: column;
			align-items: stretch;
		}

		.stage-arrow {
			text-align: center;
			transform: rotate(90deg);
		}
	}
</style>
