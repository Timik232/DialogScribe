<script lang="ts">
	import { transcriptionStore } from "$lib/stores/transcription";
	import { fetchApi } from "$lib/services/api";

	// ── State ──

	let models: Array<{ id: string; name: string }> = $state([]);
	let templates: Array<{ slug: string; name: string; emoji: string; is_custom: boolean }> = $state([]);
	let selectedModel = $state("");
	let selectedTemplate = $state("general");

	let llmAvailable = $state(true);
	let llmChecked = $state(false);

	let summaryLoading = $state(false);
	let summaryHtml = $state("");
	let summaryMarkdown = $state("");

	let mindmapLoading = $state(false);
	let mindmapMarkdown = $state("");
	let mindmapHtml = $state("");

	let insightsLoading = $state(false);
	let allLoading = $state(false);
	let insightsData = $state<{
		action_items?: Array<{ task: string; assignee?: string | null; deadline?: string | null; priority: string; checked?: boolean }>;
		decisions?: Array<{ decision: string; context: string }>;
		suggested_steps?: Array<{ step: string; reason: string; category: string }>;
		parse_error?: boolean;
		raw?: string;
	} | null>(null);

	let errorMsg = $state("");

	// ── Chat state ──

	let chatOpen = $state(false);
	let chatMessages = $state<Array<{ role: string; content: string }>>([]);
	let chatInput = $state("");
	let chatLoading = $state(false);
	let chatFirstQuestion = $state(true);

	const suggestedQuestions = [
		"Какие ключевые решения были приняты?",
		"Кто за что отвечает?",
		"Какие есть договорённости по срокам?",
		"Кратко опишите основные темы обсуждения",
	];

	// ── Derived ──

	let hasTranscription = $derived(!!$transcriptionStore?.text);
	let canGenerate = $derived(hasTranscription && llmAvailable && !summaryLoading && !mindmapLoading && !insightsLoading && !allLoading);

	let builtInTemplates = $derived(templates.filter((t) => !t.is_custom));
	let customTemplates = $derived(templates.filter((t) => t.is_custom));

	// ── Init ──

	$effect(() => {
		loadModels();
		loadTemplates();
	});

	async function loadModels() {
		try {
			const data = await fetchApi<{ models: Array<{ id: string; name: string }> }>("GET", "/api/models");
			models = data.models ?? [];
			if (models.length > 0 && !selectedModel) {
				selectedModel = models[0].id;
			}
			llmAvailable = true;
		} catch (e: any) {
			if (e?.message?.includes("503")) {
				llmAvailable = false;
			}
		} finally {
			llmChecked = true;
		}
	}

	async function loadTemplates() {
		try {
			const data = await fetchApi<Array<{ slug: string; name: string; emoji: string; is_custom: boolean }>>("GET", "/api/templates");
			templates = data ?? [];
		} catch {
			// non-critical — templates are optional
		}
	}

	// ── Actions ──

	async function generateSummary() {
		if (!$transcriptionStore) return;
		errorMsg = "";
		summaryLoading = true;
		summaryHtml = "";
		summaryMarkdown = "";

		try {
			const result = await fetchApi<{ summary_markdown: string; summary_html: string }>("POST", "/api/summary", {
				body: JSON.stringify({
					text: $transcriptionStore.text,
					model: selectedModel || undefined,
					template_key: selectedTemplate || undefined,
				}),
				headers: { "Content-Type": "application/json" },
			});
			summaryMarkdown = result.summary_markdown ?? "";
			summaryHtml = result.summary_html ?? "";
		} catch (e: any) {
			errorMsg = e?.message ?? "Ошибка генерации саммари";
		} finally {
			summaryLoading = false;
		}
	}

	async function generateMindmap() {
		if (!$transcriptionStore) return;
		errorMsg = "";
		mindmapLoading = true;
		mindmapMarkdown = "";
		mindmapHtml = "";

		try {
			const result = await fetchApi<{ mindmap_markdown: string; mindmap_uid: string; mindmap_html: string }>("POST", "/api/mindmap", {
				body: JSON.stringify({
					text: $transcriptionStore.text,
					model: selectedModel || undefined,
				}),
				headers: { "Content-Type": "application/json" },
			});
			mindmapMarkdown = result.mindmap_markdown ?? "";
			mindmapHtml = result.mindmap_html ?? "";
		} catch (e: any) {
			errorMsg = e?.message ?? "Ошибка генерации майндмэпа";
		} finally {
			mindmapLoading = false;
		}
	}

	async function exportSummary(format: string) {
		if (!summaryMarkdown) return;
		try {
			const res = await fetch("/api/export", {
				method: "POST",
				credentials: "include",
				headers: { "Content-Type": "application/json" },
				body: JSON.stringify({
					data: {
						text: summaryMarkdown,
						segments: [],
						duration: 0,
						language: "ru",
					},
					format,
					filename: "summary",
				}),
			});
			if (!res.ok) throw new Error(`Export failed: ${res.status}`);
			const blob = await res.blob();
			const ext = format === "docx" ? ".docx" : format === "pdf" ? ".pdf" : ".txt";
			const url = URL.createObjectURL(blob);
			const a = document.createElement("a");
			a.href = url;
			a.download = `summary${ext}`;
			a.click();
			URL.revokeObjectURL(url);
		} catch (e: any) {
			errorMsg = e?.message ?? "Ошибка экспорта";
		}
	}

	async function generateAll() {
		if (!$transcriptionStore) return;
		errorMsg = "";
		allLoading = true;
		summaryLoading = true;
		mindmapLoading = true;
		insightsLoading = true;

		// Summary
		try {
			const result = await fetchApi<{ summary_markdown: string; summary_html: string }>("POST", "/api/summary", {
				body: JSON.stringify({
					text: $transcriptionStore.text,
					model: selectedModel || undefined,
					template_key: selectedTemplate || undefined,
				}),
				headers: { "Content-Type": "application/json" },
			});
			summaryMarkdown = result.summary_markdown ?? "";
			summaryHtml = result.summary_html ?? "";
		} catch (e: any) {
			errorMsg = e?.message ?? "Ошибка генерации саммари";
		} finally {
			summaryLoading = false;
		}

		// Mindmap (skip if summary failed)
		if (!errorMsg) {
			try {
				const result = await fetchApi<{ mindmap_markdown: string; mindmap_uid: string; mindmap_html: string }>("POST", "/api/mindmap", {
					body: JSON.stringify({
						text: $transcriptionStore.text,
						model: selectedModel || undefined,
					}),
					headers: { "Content-Type": "application/json" },
				});
				mindmapMarkdown = result.mindmap_markdown ?? "";
				mindmapHtml = result.mindmap_html ?? "";
			} catch (e: any) {
				errorMsg = e?.message ?? "Ошибка генерации майндмэпа";
			} finally {
				mindmapLoading = false;
			}
		} else {
			mindmapLoading = false;
		}

		// Insights (skip if previous failed)
		if (!errorMsg) {
			try {
				const result = await fetchApi<{
					action_items?: Array<{ task: string; assignee?: string | null; deadline?: string | null; priority: string }>;
					decisions?: Array<{ decision: string; context: string }>;
					suggested_steps?: Array<{ step: string; reason: string; category: string }>;
					parse_error?: boolean;
					raw?: string;
				}>("POST", "/api/insights", {
					body: JSON.stringify({
						text: $transcriptionStore.text,
						model: selectedModel || undefined,
						include_action_items: true,
						include_suggested_steps: true,
					}),
					headers: { "Content-Type": "application/json" },
				});
				insightsData = result;
			} catch (e: any) {
				errorMsg = e?.message ?? "Ошибка извлечения инсайтов";
			} finally {
				insightsLoading = false;
			}
		} else {
			insightsLoading = false;
		}

		allLoading = false;
	}

	async function generateInsights() {
		if (!$transcriptionStore) return;
		errorMsg = "";
		insightsLoading = true;
		insightsData = null;

		try {
			const result = await fetchApi<{
				action_items?: Array<{ task: string; assignee?: string | null; deadline?: string | null; priority: string }>;
				decisions?: Array<{ decision: string; context: string }>;
				suggested_steps?: Array<{ step: string; reason: string; category: string }>;
				parse_error?: boolean;
				raw?: string;
			}>("POST", "/api/insights", {
				body: JSON.stringify({
					text: $transcriptionStore.text,
					model: selectedModel || undefined,
					include_action_items: true,
					include_suggested_steps: true,
				}),
				headers: { "Content-Type": "application/json" },
			});
			insightsData = result;
		} catch (e: any) {
			errorMsg = e?.message ?? "Ошибка извлечения инсайтов";
		} finally {
			insightsLoading = false;
		}
	}

	function toggleActionItem(index: number) {
		if (!insightsData?.action_items) return;
		insightsData.action_items[index].checked = !insightsData.action_items[index].checked;
		insightsData = { ...insightsData, action_items: [...insightsData.action_items!] };
	}

	function priorityColor(priority: string): string {
		if (priority === "high") return "#dc2626";
		if (priority === "medium") return "#d97706";
		return "#9ca3af";
	}

	function priorityLabel(priority: string): string {
		if (priority === "high") return "Высокий";
		if (priority === "medium") return "Средний";
		return "Низкий";
	}

	function categoryColor(category: string): string {
		if (category === "followup") return "#2563eb";
		if (category === "research") return "#7c3aed";
		if (category === "communication") return "#16a34a";
		return "#ea580c";
	}

	function categoryLabel(category: string): string {
		if (category === "followup") return "Фоллоу-ап";
		if (category === "research") return "Исследование";
		if (category === "communication") return "Коммуникация";
		return "Планирование";
	}

	async function exportInsights(format: string) {
		if (!insightsData) return;
		try {
			const res = await fetch("/api/export-insights", {
				method: "POST",
				credentials: "include",
				headers: { "Content-Type": "application/json" },
				body: JSON.stringify({
					action_items: insightsData.action_items ?? [],
					decisions: insightsData.decisions ?? [],
					suggested_steps: insightsData.suggested_steps ?? [],
					format,
				}),
			});
			if (!res.ok) throw new Error(`Export failed: ${res.status}`);
			const blob = await res.blob();
			const ext = format === "docx" ? ".docx" : ".txt";
			const url = URL.createObjectURL(blob);
			const a = document.createElement("a");
			a.href = url;
			a.download = `insights${ext}`;
			a.click();
			URL.revokeObjectURL(url);
		} catch (e: any) {
			errorMsg = e?.message ?? "Ошибка экспорта";
		}
	}

	async function sendChatMessage(content?: string) {
		const msg = content ?? chatInput.trim();
		if (!msg || !$transcriptionStore) return;

		chatMessages = [...chatMessages, { role: "user", content: msg }];
		chatInput = "";
		chatLoading = true;

		try {
			const result = await fetchApi<{ answer: string }>("POST", "/api/chat", {
				body: JSON.stringify({
					text: $transcriptionStore.text,
					model: selectedModel || undefined,
					messages: chatMessages,
				}),
				headers: { "Content-Type": "application/json" },
			});
			chatMessages = [...chatMessages, { role: "assistant", content: result.answer }];
		} catch (e: any) {
			chatMessages = [...chatMessages, { role: "assistant", content: `❌ Ошибка: ${e?.message ?? "Не удалось получить ответ"}` }];
		} finally {
			chatLoading = false;
			chatFirstQuestion = false;
		}
	}

	function handleChatKeydown(e: KeyboardEvent) {
		if (e.key === "Enter" && !e.shiftKey) {
			e.preventDefault();
			sendChatMessage();
		}
	}

	function clearChat() {
		chatMessages = [];
		chatInput = "";
	}
</script>

<div class="analysis-page">
	{#if !llmChecked}
		<div class="loading-screen">
			<div class="spinner"></div>
			<p>Загрузка…</p>
		</div>
	{:else if !llmAvailable}
		<div class="container">
			<div class="degradation-banner">
				<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
					<path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/>
					<line x1="12" y1="9" x2="12" y2="13"/>
					<line x1="12" y1="17" x2="12.01" y2="17"/>
				</svg>
				<span>LLM не настроен. Функция анализа недоступна.</span>
			</div>
		</div>
	{:else if !hasTranscription}
		<div class="empty-state">
			<svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="var(--color-muted)" stroke-width="1.5">
				<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
				<polyline points="14 2 14 8 20 8"/>
				<line x1="16" y1="13" x2="8" y2="13"/>
				<line x1="16" y1="17" x2="8" y2="17"/>
				<polyline points="10 9 9 9 8 9"/>
			</svg>
			<h2>Сначала выполните транскрипцию</h2>
			<p class="empty-hint">Загрузите аудио или видео файл на странице транскрипции, чтобы получить текст для анализа.</p>
			<a href="/transcribe" class="btn btn-primary">Перейти к транскрипции</a>
		</div>
	{:else}
		<div class="container" class:container-shifted={chatOpen}>
			<div class="page-header">
				<h1>Анализ</h1>
				<p class="page-desc">Генерация саммари и майндмэпа на основе транскрипции</p>
			</div>

			{#if errorMsg}
				<div class="error-banner">{errorMsg}</div>
			{/if}

			<div class="settings-bar card">
				<div class="setting-group">
					<label for="model-select">Модель</label>
					<select id="model-select" class="input" bind:value={selectedModel}>
						{#each models as m}
							<option value={m.id}>{m.name}</option>
						{/each}
					</select>
				</div>
				<div class="setting-group">
					<label for="template-select">Шаблон</label>
					<select id="template-select" class="input" bind:value={selectedTemplate}>
						{#if builtInTemplates.length > 0}
							<optgroup label="Встроенные">
								{#each builtInTemplates as t}
									<option value={t.slug}>{t.name}</option>
								{/each}
							</optgroup>
						{/if}
						{#if customTemplates.length > 0}
							<optgroup label="Пользовательские">
								{#each customTemplates as t}
									<option value={t.slug}>{t.name}</option>
								{/each}
							</optgroup>
						{/if}
					</select>
				</div>
			</div>

			<div class="card transcription-preview">
				<h3>Текст транскрипции</h3>
				<div class="preview-text">
					{$transcriptionStore?.text ?? ""}
				</div>
			</div>

			<div class="action-row">
				<button class="btn btn-accent" onclick={generateAll} disabled={!canGenerate || allLoading}>
					{#if allLoading}
						<span class="btn-spinner"></span>
						Генерация…
					{:else}
						<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" class="btn-icon"><path stroke-linecap="round" stroke-linejoin="round" d="M3.75 13.5l10.5-11.25L12 10.5h8.25L9.75 21.75 12 13.5H3.75z"/></svg>Сделать всё
					{/if}
				</button>
				<button class="btn btn-primary" onclick={generateSummary} disabled={!canGenerate || summaryLoading}>
					{#if summaryLoading}
						<span class="btn-spinner"></span>
						Генерация…
					{:else}
						Сгенерировать саммари
					{/if}
				</button>
				<button class="btn btn-primary" onclick={generateMindmap} disabled={!canGenerate || mindmapLoading}>
					{#if mindmapLoading}
						<span class="btn-spinner"></span>
						Генерация…
					{:else}
						Сгенерировать майндмэп
					{/if}
				</button>
				<button class="btn btn-primary" onclick={generateInsights} disabled={!canGenerate || insightsLoading}>
					{#if insightsLoading}
						<span class="btn-spinner"></span>
						Извлечение…
					{:else}
						<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" class="btn-icon"><path stroke-linecap="round" stroke-linejoin="round" d="M9 12h3.75M9 15h3.75M9 18h3.75m3 .75H18a2.25 2.25 0 002.25-2.25V6.108c0-1.135-.845-2.098-1.976-2.192a48.424 48.424 0 00-1.123-.08m-5.801 0c-.065.21-.1.433-.1.664 0 .414.336.75.75.75h4.5a.75.75 0 00.75-.75 2.25 2.25 0 00-.1-.664m-5.8 0A2.251 2.251 0 0113.5 2.25H15c1.012 0 1.867.668 2.15 1.586m-5.8 0c-.376.023-.75.05-1.124.08C9.095 4.01 8.25 4.973 8.25 6.108V8.25m0 0H4.875c-.621 0-1.125.504-1.125 1.125v11.25c0 .621.504 1.125 1.125 1.125h9.75c.621 0 1.125-.504 1.125-1.125V9.375c0-.621-.504-1.125-1.125-1.125H8.25zM6.75 12h.008v.008H6.75V12zm0 3h.008v.008H6.75V15zm0 3h.008v.008H6.75V18z"/></svg>Извлечь инсайты
					{/if}
				</button>
			</div>

			{#if summaryHtml || summaryMarkdown}
				<div class="card result-card">
					<div class="result-header">
						<h3>Саммари</h3>
						<div class="export-buttons">
							<button class="btn btn-secondary btn-sm" onclick={() => exportSummary("txt")}>TXT</button>
							<button class="btn btn-secondary btn-sm" onclick={() => exportSummary("docx")}>DOCX</button>
							<button class="btn btn-secondary btn-sm" onclick={() => exportSummary("pdf")}>PDF</button>
						</div>
					</div>
					{#if summaryHtml}
						<div class="summary-content">{@html summaryHtml}</div>
					{:else}
						<pre class="summary-content plain">{summaryMarkdown}</pre>
					{/if}
				</div>
			{/if}

			{#if mindmapHtml}
				<div class="card result-card">
					<div class="result-header">
						<h3>Майндмэп</h3>
					</div>
					<div class="mindmap-wrapper">
						{@html mindmapHtml}
					</div>
				</div>
			{/if}

			{#if insightsData}
				<div class="card result-card">
					<div class="result-header">
						<h3>📋 Задачи и следующие шаги</h3>
						<div class="export-buttons">
							<button class="btn btn-secondary btn-sm" onclick={() => exportInsights("txt")}>TXT</button>
							<button class="btn btn-secondary btn-sm" onclick={() => exportInsights("docx")}>DOCX</button>
						</div>
					</div>

					{#if insightsData.parse_error}
						<div class="parse-warning">
							⚠️ Не удалось разобрать ответ LLM. Отображается сырой текст:
							<pre class="raw-text">{insightsData.raw ?? ''}</pre>
						</div>
					{/if}

					{#if insightsData.action_items && insightsData.action_items.length > 0}
						<div class="insights-section">
							<h4>✅ Задачи</h4>
							<ul class="action-items-list">
								{#each insightsData.action_items as item, i}
									<li class:checked={item.checked}>
										<label class="action-item">
											<input type="checkbox" checked={item.checked} onchange={() => toggleActionItem(i)} />
											<span class="item-text">{item.task}</span>
											<span class="badge" style="background: {priorityColor(item.priority)}20; color: {priorityColor(item.priority)}">{priorityLabel(item.priority)}</span>
											{#if item.assignee}
												<span class="badge badge-muted">👤 {item.assignee}</span>
											{/if}
											{#if item.deadline}
												<span class="badge badge-muted">📅 {item.deadline}</span>
											{/if}
										</label>
									</li>
								{/each}
							</ul>
						</div>
					{/if}

					{#if insightsData.decisions && insightsData.decisions.length > 0}
						<div class="insights-section">
							<h4>📌 Решения</h4>
							<ul class="decisions-list">
								{#each insightsData.decisions as d}
									<li>
										<strong>{d.decision}</strong>
										<span class="decision-context">{d.context}</span>
									</li>
								{/each}
							</ul>
						</div>
					{/if}

					{#if insightsData.suggested_steps && insightsData.suggested_steps.length > 0}
						<div class="insights-section">
							<h4>💡 Рекомендуемые шаги</h4>
							<ol class="steps-list">
								{#each insightsData.suggested_steps as step}
									<li>
										<span class="badge" style="background: {categoryColor(step.category)}20; color: {categoryColor(step.category)}">{categoryLabel(step.category)}</span>
										<strong>{step.step}</strong>
										<span class="step-reason">{step.reason}</span>
									</li>
								{/each}
							</ol>
						</div>
					{/if}

					{#if (!insightsData.action_items || insightsData.action_items.length === 0) && (!insightsData.decisions || insightsData.decisions.length === 0) && (!insightsData.suggested_steps || insightsData.suggested_steps.length === 0) && !insightsData.parse_error}
						<p class="empty-insights">Инсайты не найдены.</p>
					{/if}
				</div>
			{/if}
		</div>

		{#if !chatOpen}
		<button class="chat-toggle-btn" onclick={() => chatOpen = true} title="Чат с транскриптом">
			<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" class="btn-icon"><path stroke-linecap="round" stroke-linejoin="round" d="M20.25 8.511c.884.284 1.5 1.128 1.5 2.097v4.286c0 1.136-.847 2.1-1.98 2.193-.34.027-.68.052-1.02.072v3.091l-3-3c-1.354 0-2.694-.055-4.02-.163a2.115 2.115 0 01-.825-.242m9.345-8.334a2.126 2.126 0 00-.476-.095 48.64 48.64 0 00-8.048 0c-1.131.094-1.976 1.057-1.976 2.192v4.286c0 .837.46 1.58 1.155 1.951m9.345-8.334V6.637c0-1.621-1.152-3.026-2.76-3.235A48.455 48.455 0 0011.25 3c-2.115 0-4.198.137-6.24.402-1.608.209-2.76 1.614-2.76 3.235v6.226c0 1.621 1.152 3.026 2.76 3.235.577.075 1.157.14 1.74.194V21l4.155-4.155"/></svg>
		</button>
		{/if}

		{#if chatOpen}
			<aside class="chat-sidebar">
				<div class="chat-header">
					<span class="chat-title"><svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" class="btn-icon"><path stroke-linecap="round" stroke-linejoin="round" d="M20.25 8.511c.884.284 1.5 1.128 1.5 2.097v4.286c0 1.136-.847 2.1-1.98 2.193-.34.027-.68.052-1.02.072v3.091l-3-3c-1.354 0-2.694-.055-4.02-.163a2.115 2.115 0 01-.825-.242m9.345-8.334a2.126 2.126 0 00-.476-.095 48.64 48.64 0 00-8.048 0c-1.131.094-1.976 1.057-1.976 2.192v4.286c0 .837.46 1.58 1.155 1.951m9.345-8.334V6.637c0-1.621-1.152-3.026-2.76-3.235A48.455 48.455 0 0011.25 3c-2.115 0-4.198.137-6.24.402-1.608.209-2.76 1.614-2.76 3.235v6.226c0 1.621 1.152 3.026 2.76 3.235.577.075 1.157.14 1.74.194V21l4.155-4.155"/></svg>Чат с транскриптом</span>
					<div class="chat-header-actions">
						<button class="btn btn-secondary btn-sm" onclick={clearChat}>Очистить</button>
						<button class="chat-close-btn" onclick={() => chatOpen = false}>✕</button>
					</div>
				</div>

				<div class="chat-messages">
					{#if chatMessages.length === 0}
						<div class="chat-suggestions">
							<p class="chat-suggestions-label">Попробуйте спросить:</p>
							{#each suggestedQuestions as q}
								<button class="chat-suggestion-btn" onclick={() => sendChatMessage(q)}>{q}</button>
							{/each}
						</div>
					{:else}
						{#each chatMessages as msg}
							<div class="chat-bubble" class:chat-bubble-user={msg.role === "user"} class:chat-bubble-assistant={msg.role === "assistant"}>
								{#if msg.role === "assistant"}
									<div class="chat-bubble-content">{@html msg.content}</div>
								{:else}
									<div class="chat-bubble-content">{msg.content}</div>
								{/if}
							</div>
						{/each}
						{#if chatLoading}
							<div class="chat-bubble chat-bubble-assistant">
								<div class="chat-bubble-content chat-thinking">{chatFirstQuestion && ($transcriptionStore?.text?.length ?? 0) > 30000 ? 'Подготовка контекста…' : 'Думает…'}</div>
							</div>
						{/if}
					{/if}
				</div>

				<div class="chat-input-area">
					<textarea
						class="chat-input"
						placeholder="Задайте вопрос о транскрипте…"
						bind:value={chatInput}
						onkeydown={handleChatKeydown}
						rows="1"
						disabled={chatLoading}
					></textarea>
					<button class="chat-send-btn" onclick={() => sendChatMessage()} disabled={chatLoading || !chatInput.trim()}>
						➤
					</button>
				</div>
			</aside>
		{/if}
	{/if}
</div>

<style>
	.btn-icon {
		width: 18px;
		height: 18px;
		display: inline-block;
		vertical-align: middle;
		margin-right: 6px;
	}

	.analysis-page {
		padding: 1.5rem 0;
	}

	.loading-screen {
		display: flex;
		flex-direction: column;
		align-items: center;
		justify-content: center;
		min-height: calc(100vh - var(--header-height));
		gap: 1rem;
		color: var(--color-muted);
	}

	.spinner {
		width: 32px;
		height: 32px;
		border: 3px solid var(--color-border);
		border-top-color: var(--color-cta);
		border-radius: 50%;
		animation: spin 0.6s linear infinite;
	}

	@keyframes spin {
		to { transform: rotate(360deg); }
	}

	.container {
		max-width: 900px;
		margin: 0 auto;
		padding: 0 1rem;
		display: flex;
		flex-direction: column;
		gap: 1rem;
	}

	/* Degradation banner */

	.degradation-banner {
		display: flex;
		align-items: center;
		gap: 0.75rem;
		margin-top: 3rem;
		padding: 1rem 1.25rem;
		background: var(--color-error-bg);
		border: 1px solid var(--color-error-border);
		border-radius: var(--radius);
		color: var(--color-error-text);
		font-size: 0.9375rem;
		font-weight: 500;
	}

	/* Empty state */

	.empty-state {
		display: flex;
		flex-direction: column;
		align-items: center;
		justify-content: center;
		min-height: calc(100vh - var(--header-height));
		gap: 0.75rem;
		text-align: center;
		padding: 2rem;
	}

	.empty-state h2 {
		color: var(--color-text);
		font-size: 1.25rem;
	}

	.empty-hint {
		color: var(--color-muted);
		font-size: 0.875rem;
		max-width: 400px;
		margin-bottom: 0.5rem;
	}

	/* Page header */

	.page-header {
		margin-bottom: 0.25rem;
	}

	.page-desc {
		color: var(--color-muted);
		font-size: 0.875rem;
		margin-top: 0.25rem;
	}

	/* Settings bar */

	.settings-bar {
		display: flex;
		gap: 1rem;
		flex-wrap: wrap;
	}

	.setting-group {
		display: flex;
		flex-direction: column;
		gap: 0.375rem;
		flex: 1;
		min-width: 200px;
	}

	.setting-group label {
		font-size: 0.8125rem;
		font-weight: 500;
		color: var(--color-muted);
	}

	.setting-group select {
		cursor: pointer;
	}

	/* Transcription preview */

	.transcription-preview h3 {
		margin-bottom: 0.75rem;
		font-size: 0.9375rem;
	}

	.preview-text {
		max-height: 160px;
		overflow-y: auto;
		font-size: 0.8125rem;
		color: var(--color-muted);
		line-height: 1.6;
		white-space: pre-wrap;
		word-break: break-word;
	}

	/* Action row */

	.action-row {
		display: flex;
		gap: 0.75rem;
		flex-wrap: wrap;
	}

	.btn-spinner {
		width: 14px;
		height: 14px;
		border: 2px solid rgba(255, 255, 255, 0.3);
		border-top-color: #fff;
		border-radius: 50%;
		animation: spin 0.6s linear infinite;
		display: inline-block;
	}

	/* Result cards */

	.result-card {
		animation: fade-in 0.3s ease;
	}

	@keyframes fade-in {
		from { opacity: 0; transform: translateY(8px); }
		to { opacity: 1; transform: translateY(0); }
	}

	.result-header {
		display: flex;
		align-items: center;
		justify-content: space-between;
		margin-bottom: 1rem;
	}

	.result-header h3 {
		margin: 0;
		font-size: 0.9375rem;
	}

	.export-buttons {
		display: flex;
		gap: 0.375rem;
	}

	.btn-sm {
		padding: 0.25rem 0.625rem;
		font-size: 0.75rem;
	}

	.summary-content {
		font-size: 0.875rem;
		line-height: 1.7;
		color: var(--color-text);
	}

	.summary-content :global(h1),
	.summary-content :global(h2),
	.summary-content :global(h3) {
		margin-top: 1rem;
		margin-bottom: 0.5rem;
	}

	.summary-content :global(ul),
	.summary-content :global(ol) {
		margin-left: 1.25rem;
		margin-bottom: 0.5rem;
	}

	.summary-content :global(p) {
		margin-bottom: 0.5rem;
	}

	.summary-content.plain {
		white-space: pre-wrap;
		word-break: break-word;
		background: var(--color-bg);
		padding: 1rem;
		border-radius: var(--radius-sm);
		font-family: var(--font-family);
	}

	.mindmap-wrapper {
		background-color: var(--color-bg);
		border-radius: var(--radius-sm);
		padding: 1rem;
		max-height: 600px;
		overflow-y: auto;
	}

	.badge-muted {
		background: var(--color-bg);
		color: var(--color-muted);
		font-size: 0.6875rem;
		border: 1px solid var(--color-border);
	}

	.badge {
		display: inline-block;
		padding: 0.125rem 0.5rem;
		border-radius: 999px;
		font-size: 0.6875rem;
		font-weight: 500;
		white-space: nowrap;
	}

	.insights-section {
		margin-bottom: 1.25rem;
	}

	.insights-section h4 {
		margin: 0 0 0.5rem;
		font-size: 0.875rem;
	}

	.action-items-list {
		list-style: none;
		padding: 0;
		margin: 0;
	}

	.action-items-list li {
		padding: 0.5rem 0;
		border-bottom: 1px solid var(--color-border);
	}

	.action-items-list li:last-child {
		border-bottom: none;
	}

	.action-items-list li.checked .item-text {
		text-decoration: line-through;
		opacity: 0.5;
	}

	.action-item {
		display: flex;
		align-items: center;
		gap: 0.5rem;
		flex-wrap: wrap;
		cursor: pointer;
	}

	.action-item input[type="checkbox"] {
		flex-shrink: 0;
	}

	.item-text {
		flex: 1;
		font-size: 0.875rem;
	}

	.decisions-list {
		list-style: none;
		padding: 0;
		margin: 0;
	}

	.decisions-list li {
		padding: 0.5rem 0;
		border-bottom: 1px solid var(--color-border);
		display: flex;
		flex-direction: column;
		gap: 0.25rem;
	}

	.decisions-list li:last-child {
		border-bottom: none;
	}

	.decision-context {
		font-size: 0.8125rem;
		color: var(--color-muted);
	}

	.steps-list {
		padding-left: 1.5rem;
		margin: 0;
	}

	.steps-list li {
		padding: 0.5rem 0;
		border-bottom: 1px solid var(--color-border);
		display: flex;
		flex-direction: column;
		gap: 0.25rem;
	}

	.steps-list li:last-child {
		border-bottom: none;
	}

	.step-reason {
		font-size: 0.8125rem;
		color: var(--color-muted);
	}

	.empty-insights {
		color: var(--color-muted);
		font-size: 0.875rem;
		text-align: center;
		padding: 1rem 0;
	}

	.parse-warning {
		padding: 0.75rem 1rem;
		background: var(--color-error-bg, #fef2f2);
		border: 1px solid var(--color-error-border, #fecaca);
		border-radius: var(--radius-sm);
		margin-bottom: 1rem;
		font-size: 0.8125rem;
		color: var(--color-error-text, #991b1b);
	}

	.raw-text {
		margin-top: 0.5rem;
		white-space: pre-wrap;
		word-break: break-word;
		font-size: 0.8125rem;
	}

	.btn-accent {
		background: linear-gradient(135deg, #6366f1, #8b5cf6);
		color: #fff;
		border: none;
		font-weight: 600;
	}

	.btn-accent:hover:not(:disabled) {
		background: linear-gradient(135deg, #4f46e5, #7c3aed);
	}

	.btn-accent:disabled {
		opacity: 0.5;
		cursor: not-allowed;
	}

	/* ── Chat ── */

	.chat-toggle-btn {
		position: fixed;
		bottom: 2rem;
		right: 2rem;
		width: 52px;
		height: 52px;
		border-radius: 50%;
		background: linear-gradient(135deg, #6366f1, #8b5cf6);
		color: #fff;
		border: none;
		font-size: 1.5rem;
		cursor: pointer;
		box-shadow: 0 4px 14px rgba(99, 102, 241, 0.4);
		z-index: 99;
		transition: transform 0.15s ease, box-shadow 0.15s ease;
	}

	.chat-toggle-btn:hover {
		transform: scale(1.08);
		box-shadow: 0 6px 20px rgba(99, 102, 241, 0.5);
	}

	.container-shifted {
		margin-right: 400px;
	}

	.chat-sidebar {
		position: fixed;
		right: 0;
		top: var(--header-height);
		width: 400px;
		height: calc(100vh - var(--header-height));
		background: var(--color-surface);
		border-left: 1px solid var(--color-border);
		display: flex;
		flex-direction: column;
		z-index: 100;
	}

	.chat-header {
		display: flex;
		align-items: center;
		justify-content: space-between;
		padding: 0.75rem 1rem;
		border-bottom: 1px solid var(--color-border);
		flex-shrink: 0;
	}

	.chat-title {
		font-weight: 600;
		font-size: 0.9375rem;
	}

	.chat-header-actions {
		display: flex;
		align-items: center;
		gap: 0.5rem;
	}

	.chat-close-btn {
		background: none;
		border: none;
		font-size: 1.125rem;
		cursor: pointer;
		color: var(--color-muted);
		padding: 0.25rem;
		line-height: 1;
	}

	.chat-close-btn:hover {
		color: var(--color-text);
	}

	.chat-messages {
		flex: 1;
		overflow-y: auto;
		padding: 1rem;
		display: flex;
		flex-direction: column;
		gap: 0.75rem;
	}

	.chat-suggestions {
		display: flex;
		flex-direction: column;
		gap: 0.5rem;
		padding-top: 2rem;
	}

	.chat-suggestions-label {
		font-size: 0.8125rem;
		color: var(--color-muted);
		margin-bottom: 0.25rem;
	}

	.chat-suggestion-btn {
		text-align: left;
		background: var(--color-bg);
		border: 1px solid var(--color-border);
		border-radius: var(--radius);
		padding: 0.625rem 0.875rem;
		cursor: pointer;
		font-size: 0.8125rem;
		color: var(--color-text);
		transition: border-color 0.15s ease, background 0.15s ease;
	}

	.chat-suggestion-btn:hover {
		border-color: var(--color-cta);
		background: color-mix(in srgb, var(--color-cta) 8%, var(--color-bg));
	}

	.chat-bubble {
		max-width: 85%;
		padding: 0.625rem 0.875rem;
		border-radius: var(--radius);
		font-size: 0.8125rem;
		line-height: 1.6;
		word-break: break-word;
	}

	.chat-bubble-user {
		align-self: flex-end;
		background: var(--color-cta);
		color: #fff;
		border-bottom-right-radius: 4px;
	}

	.chat-bubble-assistant {
		align-self: flex-start;
		background: var(--color-bg);
		border: 1px solid var(--color-border);
		border-bottom-left-radius: 4px;
	}

	.chat-bubble-content :global(p) {
		margin: 0 0 0.5rem;
	}

	.chat-bubble-content :global(p:last-child) {
		margin-bottom: 0;
	}

	.chat-bubble-content :global(code) {
		background: var(--color-surface);
		padding: 0.125rem 0.375rem;
		border-radius: 3px;
		font-size: 0.75rem;
	}

	.chat-thinking {
		color: var(--color-muted);
		font-style: italic;
	}

	.chat-input-area {
		display: flex;
		align-items: flex-end;
		gap: 0.5rem;
		padding: 0.75rem 1rem;
		border-top: 1px solid var(--color-border);
		flex-shrink: 0;
	}

	.chat-input {
		flex: 1;
		resize: none;
		border: 1px solid var(--color-border);
		border-radius: var(--radius);
		padding: 0.5rem 0.75rem;
		font-size: 0.8125rem;
		font-family: inherit;
		line-height: 1.5;
		background: var(--color-bg);
		color: var(--color-text);
		max-height: 120px;
		overflow-y: auto;
	}

	.chat-input:focus {
		outline: none;
		border-color: var(--color-cta);
	}

	.chat-send-btn {
		flex-shrink: 0;
		width: 36px;
		height: 36px;
		border-radius: var(--radius);
		background: var(--color-cta);
		color: #fff;
		border: none;
		cursor: pointer;
		font-size: 1rem;
		display: flex;
		align-items: center;
		justify-content: center;
		transition: opacity 0.15s ease;
	}

	.chat-send-btn:disabled {
		opacity: 0.4;
		cursor: not-allowed;
	}

	.chat-send-btn:hover:not(:disabled) {
		opacity: 0.85;
	}

	@media (max-width: 768px) {
		.chat-sidebar {
			width: 100%;
		}

		.container-shifted {
			margin-right: 0;
		}
	}
</style>
