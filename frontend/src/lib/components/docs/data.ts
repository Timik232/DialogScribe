export interface ApiParam {
	name: string;
	type: string;
	required: boolean;
	default?: string;
	description: string;
}

export interface ApiEndpoint {
	method: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE' | 'WS';
	path: string;
	tag: string;
	description: string;
	parameters: ApiParam[];
	curlExample: string;
	jsExample: string;
	successResponse: { status: number; body: string };
	errorResponses?: { status: number; description: string; body: string }[];
}

export interface FaqItem {
	question: string;
	answer: string;
}

export const sidebarSections = [
	{ id: 'overview', label: 'Обзор' },
	{ id: 'quickstart', label: 'Быстрый старт' },
	{ id: 'transcription', label: 'Транскрипция' },
	{ id: 'analysis', label: 'Анализ' },
	{ id: 'autoflow', label: 'Autoflow' },
	{ id: 'export', label: 'Экспорт' },
	{ id: 'templates', label: 'Шаблоны' },
	{ id: 'api-reference', label: 'API Reference' },
	{ id: 'faq', label: 'FAQ' }
];

export const endpoints: ApiEndpoint[] = [
	// === Transcription ===
	{
		method: 'POST',
		path: '/api/transcribe',
		tag: 'transcription',
		description: 'Транскрипция загруженного аудио/видео файла с опциональной диаризацией спикеров.',
		parameters: [
			{ name: 'file', type: 'UploadFile', required: true, description: 'Аудио/видео файл (wav, mp3, flac, ogg, mp4, mkv, avi, webm)' },
			{ name: 'diarization_mode', type: 'string', required: false, default: 'none', description: 'Режим диаризации: none, pyannote, hybrid' },
			{ name: 'language', type: 'string', required: false, default: 'ru', description: 'Язык транскрипции (ru, en)' },
			{ name: 'denoise', type: 'boolean', required: false, default: 'false', description: 'Включить шумоподавление' }
		],
		curlExample: `curl -X POST http://localhost:8000/api/transcribe \\
  -H "Authorization: Bearer YOUR_TOKEN" \\
  -F "file=@meeting.mp3" \\
  -F "diarization_mode=pyannote" \\
  -F "language=ru" \\
  -F "denoise=true"`,
		jsExample: `const formData = new FormData();
formData.append('file', fileInput.files[0]);
formData.append('diarization_mode', 'pyannote');
formData.append('language', 'ru');
formData.append('denoise', 'true');

const response = await fetch('/api/transcribe', {
  method: 'POST',
  headers: { 'Authorization': 'Bearer YOUR_TOKEN' },
  body: formData
});
const data = await response.json();`,
		successResponse: {
			status: 200,
			body: `{
  "segments": [
    {
      "start": 0.0,
      "end": 5.42,
      "speaker": "Спикер №1",
      "text": "Добрый день, коллеги."
    }
  ],
  "duration": 125.7,
  "text": "Добрый день, коллеги. Сегодня обсудим...",
  "language": "ru"
}`
		},
		errorResponses: [
			{ status: 413, description: 'Файл слишком большой', body: '{ "detail": "File too large. Maximum size: 1GB" }' },
			{ status: 502, description: 'Ошибка транскрипции', body: '{ "detail": "Transcription failed: unsupported codec" }' },
			{ status: 503, description: 'Сервис недоступен', body: '{ "detail": "Transcriber not ready" }' }
		]
	},
	{
		method: 'POST',
		path: '/api/transcribe/microphone',
		tag: 'transcription',
		description: 'Транскрипция аудио, записанного с микрофона.',
		parameters: [
			{ name: 'file', type: 'UploadFile', required: true, description: 'Аудио файл записи с микрофона' },
			{ name: 'diarization_mode', type: 'string', required: false, default: 'none', description: 'Режим диаризации: none, pyannote, hybrid' },
			{ name: 'language', type: 'string', required: false, default: 'ru', description: 'Язык транскрипции' },
			{ name: 'denoise', type: 'boolean', required: false, default: 'false', description: 'Включить шумоподавление' }
		],
		curlExample: `curl -X POST http://localhost:8000/api/transcribe/microphone \\
  -H "Authorization: Bearer YOUR_TOKEN" \\
  -F "file=@recording.wav" \\
  -F "diarization_mode=none" \\
  -F "language=ru"`,
		jsExample: `const formData = new FormData();
formData.append('file', audioBlob, 'recording.wav');
formData.append('diarization_mode', 'none');
formData.append('language', 'ru');

const response = await fetch('/api/transcribe/microphone', {
  method: 'POST',
  headers: { 'Authorization': 'Bearer YOUR_TOKEN' },
  body: formData
});
const data = await response.json();`,
		successResponse: {
			status: 200,
			body: `{
  "segments": [
    { "start": 0.0, "end": 3.21, "text": "Запись с микрофона." }
  ],
  "duration": 3.21,
  "text": "Запись с микрофона.",
  "language": "ru"
}`
		},
		errorResponses: [
			{ status: 413, description: 'Файл слишком большой', body: '{ "detail": "File too large" }' },
			{ status: 502, description: 'Ошибка транскрипции', body: '{ "detail": "Transcription failed" }' }
		]
	},

	// === Analysis ===
	{
		method: 'POST',
		path: '/api/summary',
		tag: 'analysis',
		description: 'Генерация краткого содержания текста транскрипции.',
		parameters: [
			{ name: 'text', type: 'string', required: true, description: 'Текст транскрипции для суммаризации' },
			{ name: 'model', type: 'string', required: false, description: 'Идентификатор LLM модели' },
			{ name: 'template_key', type: 'string', required: false, default: 'general', description: 'Ключ шаблона (general, meeting, interview, lecture)' }
		],
		curlExample: `curl -X POST http://localhost:8000/api/summary \\
  -H "Authorization: Bearer YOUR_TOKEN" \\
  -H "Content-Type: application/json" \\
  -d '{"text": "Текст транскрипции...", "template_key": "meeting"}'`,
		jsExample: `const response = await fetch('/api/summary', {
  method: 'POST',
  headers: {
    'Authorization': 'Bearer YOUR_TOKEN',
    'Content-Type': 'application/json'
  },
  body: JSON.stringify({
    text: 'Текст транскрипции...',
    template_key: 'meeting'
  })
});
const data = await response.json();`,
		successResponse: {
			status: 200,
			body: `{
  "summary_markdown": "## Краткое содержание\\n\\nОсновные тезисы...",
  "summary_html": "<h2>Краткое содержание</h2><p>Основные тезисы...</p>"
}`
		},
		errorResponses: [
			{ status: 502, description: 'Ошибка LLM', body: '{ "detail": "LLM request failed" }' }
		]
	},
	{
		method: 'POST',
		path: '/api/mindmap',
		tag: 'analysis',
		description: 'Генерация структуры майндмапа по тексту транскрипции.',
		parameters: [
			{ name: 'text', type: 'string', required: true, description: 'Текст транскрипции' },
			{ name: 'model', type: 'string', required: false, description: 'Идентификатор LLM модели' }
		],
		curlExample: `curl -X POST http://localhost:8000/api/mindmap \\
  -H "Authorization: Bearer YOUR_TOKEN" \\
  -H "Content-Type: application/json" \\
  -d '{"text": "Текст транскрипции..."}'`,
		jsExample: `const response = await fetch('/api/mindmap', {
  method: 'POST',
  headers: {
    'Authorization': 'Bearer YOUR_TOKEN',
    'Content-Type': 'application/json'
  },
  body: JSON.stringify({ text: 'Текст транскрипции...' })
});
const data = await response.json();`,
		successResponse: {
			status: 200,
			body: `{
  "mindmap_markdown": "# Тема\\n## Подтема 1\\n- Пункт 1\\n- Пункт 2",
  "mindmap_uid": "mm_a1b2c3",
  "mindmap_html": "<ul><li>Тема<ul><li>Подтема 1..."
}`
		},
		errorResponses: [
			{ status: 502, description: 'Ошибка LLM', body: '{ "detail": "LLM request failed" }' }
		]
	},
	{
		method: 'POST',
		path: '/api/insights',
		tag: 'analysis',
		description: 'Извлечение инсайтов:行动计划 и рекомендуемые шаги из текста.',
		parameters: [
			{ name: 'text', type: 'string', required: true, description: 'Текст транскрипции' },
			{ name: 'model', type: 'string', required: false, description: 'Идентификатор LLM модели' },
			{ name: 'include_action_items', type: 'boolean', required: false, default: 'true', description: 'Включить план действий' },
			{ name: 'include_suggested_steps', type: 'boolean', required: false, default: 'true', description: 'Включить рекомендуемые шаги' }
		],
		curlExample: `curl -X POST http://localhost:8000/api/insights \\
  -H "Authorization: Bearer YOUR_TOKEN" \\
  -H "Content-Type: application/json" \\
  -d '{"text": "Текст...", "include_action_items": true, "include_suggested_steps": true}'`,
		jsExample: `const response = await fetch('/api/insights', {
  method: 'POST',
  headers: {
    'Authorization': 'Bearer YOUR_TOKEN',
    'Content-Type': 'application/json'
  },
  body: JSON.stringify({
    text: 'Текст...',
    include_action_items: true,
    include_suggested_steps: true
  })
});
const data = await response.json();`,
		successResponse: {
			status: 200,
			body: `{
  "action_items": [
    "Подготовить отчёт до пятницы",
    "Назначить встречу с командой"
  ],
  "suggested_steps": [
    "Собрать данные из CRM",
    "Провести ревью процесса"
  ]
}`
		},
		errorResponses: [
			{ status: 502, description: 'Ошибка LLM', body: '{ "detail": "LLM request failed" }' }
		]
	},
	{
		method: 'POST',
		path: '/api/chat',
		tag: 'analysis',
		description: 'Чат с LLM по тексту транскрипции. Поддерживает мультитурный диалог.',
		parameters: [
			{ name: 'text', type: 'string', required: true, description: 'Текст транскрипции (контекст)' },
			{ name: 'model', type: 'string', required: false, description: 'Идентификатор LLM модели' },
			{ name: 'messages', type: 'array', required: true, description: 'Массив сообщений: [{role: "user", content: "..."}]' }
		],
		curlExample: `curl -X POST http://localhost:8000/api/chat \\
  -H "Authorization: Bearer YOUR_TOKEN" \\
  -H "Content-Type: application/json" \\
  -d '{
    "text": "Транскрипция встречи...",
    "messages": [{"role": "user", "content": "Какие решения были приняты?"}]
  }'`,
		jsExample: `const response = await fetch('/api/chat', {
  method: 'POST',
  headers: {
    'Authorization': 'Bearer YOUR_TOKEN',
    'Content-Type': 'application/json'
  },
  body: JSON.stringify({
    text: 'Транскрипция встречи...',
    messages: [{ role: 'user', content: 'Какие решения были приняты?' }]
  })
});
const data = await response.json();`,
		successResponse: {
			status: 200,
			body: `{
  "message": {
    "role": "assistant",
    "content": "На встрече были приняты следующие решения..."
  }
}`
		},
		errorResponses: [
			{ status: 502, description: 'Ошибка LLM', body: '{ "detail": "LLM request failed" }' }
		]
	},
	{
		method: 'GET',
		path: '/api/models',
		tag: 'analysis',
		description: 'Получить список доступных LLM моделей.',
		parameters: [],
		curlExample: `curl http://localhost:8000/api/models \\
  -H "Authorization: Bearer YOUR_TOKEN"`,
		jsExample: `const response = await fetch('/api/models', {
  headers: { 'Authorization': 'Bearer YOUR_TOKEN' }
});
const data = await response.json();`,
		successResponse: {
			status: 200,
			body: `{
  "models": [
    { "id": "gpt-4o", "name": "GPT-4o" },
    { "id": "gpt-4o-mini", "name": "GPT-4o Mini" }
  ]
}`
		},
		errorResponses: []
	},

	// === Export ===
	{
		method: 'POST',
		path: '/api/export',
		tag: 'export',
		description: 'Экспорт транскрипции в указанном формате. Возвращает файл для скачивания.',
		parameters: [
			{ name: 'data', type: 'object', required: true, description: 'Данные транскрипции (segments, text)' },
			{ name: 'format', type: 'string', required: true, description: 'Формат: json, srt, vtt, txt, docx, pdf' },
			{ name: 'filename', type: 'string', required: false, default: 'transcription', description: 'Имя файла без расширения' },
			{ name: 'speaker_names', type: 'object', required: false, description: 'Маппинг спикеров: {"Спикер №1": "Иван", "Спикер №2": "Мария"}' }
		],
		curlExample: `curl -X POST http://localhost:8000/api/export \\
  -H "Authorization: Bearer YOUR_TOKEN" \\
  -H "Content-Type: application/json" \\
  -d '{
    "data": {"segments": [...], "text": "..."},
    "format": "srt",
    "filename": "meeting",
    "speaker_names": {"Спикер №1": "Иван"}
  }' \\
  --output meeting.srt`,
		jsExample: `const response = await fetch('/api/export', {
  method: 'POST',
  headers: {
    'Authorization': 'Bearer YOUR_TOKEN',
    'Content-Type': 'application/json'
  },
  body: JSON.stringify({
    data: { segments: [...], text: '...' },
    format: 'srt',
    filename: 'meeting',
    speaker_names: { 'Спикер №1': 'Иван' }
  })
});
const blob = await response.blob();
const url = URL.createObjectURL(blob);
const a = document.createElement('a');
a.href = url;
a.download = 'meeting.srt';
a.click();`,
		successResponse: {
			status: 200,
			body: `// FileResponse — бинарный файл (Content-Disposition: attachment)`
		},
		errorResponses: [
			{ status: 422, description: 'Неподдерживаемый формат', body: '{ "detail": "Unsupported format: csv" }' }
		]
	},
	{
		method: 'POST',
		path: '/api/export-insights',
		tag: 'export',
		description: 'Экспорт инсайтов (план действий, решения, рекомендации) в текстовом формате.',
		parameters: [
			{ name: 'action_items', type: 'array', required: true, description: 'Список планов действий' },
			{ name: 'decisions', type: 'array', required: false, description: 'Список решений' },
			{ name: 'suggested_steps', type: 'array', required: true, description: 'Список рекомендуемых шагов' },
			{ name: 'format', type: 'string', required: false, default: 'txt', description: 'Формат экспорта' }
		],
		curlExample: `curl -X POST http://localhost:8000/api/export-insights \\
  -H "Authorization: Bearer YOUR_TOKEN" \\
  -H "Content-Type: application/json" \\
  -d '{
    "action_items": ["Подготовить отчёт"],
    "suggested_steps": ["Собрать данные"],
    "format": "txt"
  }' \\
  --output insights.txt`,
		jsExample: `const response = await fetch('/api/export-insights', {
  method: 'POST',
  headers: {
    'Authorization': 'Bearer YOUR_TOKEN',
    'Content-Type': 'application/json'
  },
  body: JSON.stringify({
    action_items: ['Подготовить отчёт'],
    suggested_steps: ['Собрать данные'],
    format: 'txt'
  })
});
const blob = await response.blob();
const url = URL.createObjectURL(blob);
const a = document.createElement('a');
a.href = url; a.download = 'insights.txt'; a.click();`,
		successResponse: {
			status: 200,
			body: `// FileResponse — текстовый файл`
		},
		errorResponses: []
	},

	// === Autoflow ===
	{
		method: 'WS',
		path: '/api/autoflow/ws?token=JWT',
		tag: 'autoflow',
		description: 'WebSocket-пайплайн для автоматической обработки: транскрипция → суммаризация → майндмап → инсайты.',
		parameters: [
			{ name: 'token', type: 'string', required: true, description: 'JWT токен (query-параметр)' },
			{ name: 'file_data', type: 'string', required: true, description: 'Файл в base64编码' },
			{ name: 'filename', type: 'string', required: true, description: 'Имя файла' },
			{ name: 'template_key', type: 'string', required: false, default: 'general', description: 'Ключ шаблона суммаризации' },
			{ name: 'diarization_mode', type: 'string', required: false, default: 'none', description: 'Режим диаризации' },
			{ name: 'include_summary', type: 'boolean', required: false, default: 'true', description: 'Включить суммаризацию' },
			{ name: 'include_mindmap', type: 'boolean', required: false, default: 'true', description: 'Включить майндмап' },
			{ name: 'include_insights', type: 'boolean', required: false, default: 'true', description: 'Включить инсайты' },
			{ name: 'model', type: 'string', required: false, description: 'LLM модель' },
			{ name: 'denoise', type: 'boolean', required: false, default: 'false', description: 'Шумоподавление' }
		],
		curlExample: `# WebSocket — используйте wscat или аналоги
wscat -c "ws://localhost:8000/api/autoflow/ws?token=YOUR_JWT_TOKEN" \\
  -x '{"file_data": "BASE64_DATA", "filename": "meeting.mp3", "include_summary": true, "include_mindmap": true, "include_insights": true}'`,
		jsExample: `const ws = new WebSocket(
  'ws://localhost:8000/api/autoflow/ws?token=YOUR_JWT_TOKEN'
);

ws.onopen = () => {
  ws.send(JSON.stringify({
    file_data: base64String,
    filename: 'meeting.mp3',
    template_key: 'general',
    diarization_mode: 'none',
    include_summary: true,
    include_mindmap: true,
    include_insights: true,
    denoise: false
  }));
};

ws.onmessage = (event) => {
  const msg = JSON.parse(event.data);
  console.log(msg.stage, msg.progress, msg.message);
};`,
		successResponse: {
			status: 200,
			body: `// Progress message:
{ "stage": "transcription", "progress": 45, "message": "Транскрибируем..." }

// Completion message:
{
  "stage": "complete",
  "result": {
    "transcription": { "segments": [...], "text": "..." },
    "summary": { "summary_markdown": "..." },
    "mindmap_html": "<ul>...</ul>",
    "mindmap_md": "# Тема...",
    "action_items": ["Пункт 1"],
    "suggested_steps": ["Шаг 1"],
    "errors": [],
    "stage_timings": { "transcription": 12.3, "summary": 3.1 }
  }
}`
		},
		errorResponses: [
			{ status: 4001, description: 'Ошибка аутентификации WS', body: '{ "error": "Invalid token" }' },
			{ status: 4002, description: 'Ошибка обработки', body: '{ "stage": "transcription", "error": "Failed to transcribe" }' }
		]
	},

	// === Templates ===
	{
		method: 'GET',
		path: '/api/templates',
		tag: 'templates',
		description: 'Получить список всех шаблонов суммаризации.',
		parameters: [],
		curlExample: `curl http://localhost:8000/api/templates \\
  -H "Authorization: Bearer YOUR_TOKEN"`,
		jsExample: `const response = await fetch('/api/templates', {
  headers: { 'Authorization': 'Bearer YOUR_TOKEN' }
});
const data = await response.json();`,
		successResponse: {
			status: 200,
			body: `{
  "templates": [
    {
      "slug": "general",
      "name": "Общий",
      "emoji": "📝",
      "system_prompt": "Сделай краткое содержание...",
      "user_prompt_template": "Текст: {text}"
    }
  ]
}`
		},
		errorResponses: []
	},
	{
		method: 'POST',
		path: '/api/templates',
		tag: 'templates',
		description: 'Создать новый шаблон суммаризации.',
		parameters: [
			{ name: 'name', type: 'string', required: true, description: 'Название шаблона' },
			{ name: 'emoji', type: 'string', required: true, description: 'Эмодзи шаблона' },
			{ name: 'system_prompt', type: 'string', required: true, description: 'Системный промпт для LLM' },
			{ name: 'user_prompt_template', type: 'string', required: true, description: 'Шаблон пользовательского промпта ({text} — плейсхолдер)' }
		],
		curlExample: `curl -X POST http://localhost:8000/api/templates \\
  -H "Authorization: Bearer YOUR_TOKEN" \\
  -H "Content-Type: application/json" \\
  -d '{
    "name": "Встреча",
    "emoji": "🤝",
    "system_prompt": "Суммаризируй встречу с указанием участников и решений",
    "user_prompt_template": "Транскрипция встречи: {text}"
  }'`,
		jsExample: `const response = await fetch('/api/templates', {
  method: 'POST',
  headers: {
    'Authorization': 'Bearer YOUR_TOKEN',
    'Content-Type': 'application/json'
  },
  body: JSON.stringify({
    name: 'Встреча',
    emoji: '🤝',
    system_prompt: 'Суммаризируй встречу с указанием участников и решений',
    user_prompt_template: 'Транскрипция встречи: {text}'
  })
});
const data = await response.json();`,
		successResponse: {
			status: 201,
			body: `{
  "slug": "vstrecha",
  "name": "Встреча",
  "emoji": "🤝",
  "system_prompt": "Суммаризируй встречу...",
  "user_prompt_template": "Транскрипция встречи: {text}"
}`
		},
		errorResponses: [
			{ status: 409, description: 'Шаблон уже существует', body: '{ "detail": "Template already exists" }' }
		]
	},
	{
		method: 'PUT',
		path: '/api/templates/{slug}',
		tag: 'templates',
		description: 'Обновить существующий шаблон по его slug.',
		parameters: [
			{ name: 'slug', type: 'string', required: true, description: 'Уникальный идентификатор шаблона (в URL)' },
			{ name: 'name', type: 'string', required: false, description: 'Новое название' },
			{ name: 'emoji', type: 'string', required: false, description: 'Новый эмодзи' },
			{ name: 'system_prompt', type: 'string', required: false, description: 'Новый системный промпт' },
			{ name: 'user_prompt_template', type: 'string', required: false, description: 'Новый шаблон промпта' }
		],
		curlExample: `curl -X PUT http://localhost:8000/api/templates/general \\
  -H "Authorization: Bearer YOUR_TOKEN" \\
  -H "Content-Type: application/json" \\
  -d '{"name": "Общий обновлённый", "system_prompt": "Новый промпт..."}'`,
		jsExample: `const response = await fetch('/api/templates/general', {
  method: 'PUT',
  headers: {
    'Authorization': 'Bearer YOUR_TOKEN',
    'Content-Type': 'application/json'
  },
  body: JSON.stringify({
    name: 'Общий обновлённый',
    system_prompt: 'Новый промпт...'
  })
});
const data = await response.json();`,
		successResponse: {
			status: 200,
			body: `{
  "slug": "general",
  "name": "Общий обновлённый",
  "emoji": "📝",
  "system_prompt": "Новый промпт...",
  "user_prompt_template": "Текст: {text}"
}`
		},
		errorResponses: [
			{ status: 404, description: 'Шаблон не найден', body: '{ "detail": "Template not found" }' }
		]
	},
	{
		method: 'DELETE',
		path: '/api/templates/{slug}',
		tag: 'templates',
		description: 'Удалить шаблон по его slug.',
		parameters: [
			{ name: 'slug', type: 'string', required: true, description: 'Уникальный идентификатор шаблона' }
		],
		curlExample: `curl -X DELETE http://localhost:8000/api/templates/my-template \\
  -H "Authorization: Bearer YOUR_TOKEN"`,
		jsExample: `const response = await fetch('/api/templates/my-template', {
  method: 'DELETE',
  headers: { 'Authorization': 'Bearer YOUR_TOKEN' }
});
const data = await response.json();`,
		successResponse: {
			status: 200,
			body: `{ "message": "Template deleted" }`
		},
		errorResponses: [
			{ status: 404, description: 'Шаблон не найден', body: '{ "detail": "Template not found" }' }
		]
	},
	{
		method: 'POST',
		path: '/api/templates/export',
		tag: 'templates',
		description: 'Экспорт всех шаблонов в JSON для резервного копирования.',
		parameters: [],
		curlExample: `curl -X POST http://localhost:8000/api/templates/export \\
  -H "Authorization: Bearer YOUR_TOKEN" \\
  --output templates.json`,
		jsExample: `const response = await fetch('/api/templates/export', {
  method: 'POST',
  headers: { 'Authorization': 'Bearer YOUR_TOKEN' }
});
const blob = await response.blob();
const url = URL.createObjectURL(blob);
const a = document.createElement('a');
a.href = url; a.download = 'templates.json'; a.click();`,
		successResponse: {
			status: 200,
			body: `// FileResponse — JSON файл со всеми шаблонами`
		},
		errorResponses: []
	},
	{
		method: 'POST',
		path: '/api/templates/import',
		tag: 'templates',
		description: 'Импорт шаблонов из JSON файла.',
		parameters: [
			{ name: 'templates_data', type: 'string', required: true, description: 'JSON-строка с массивом шаблонов' }
		],
		curlExample: `curl -X POST http://localhost:8000/api/templates/import \\
  -H "Authorization: Bearer YOUR_TOKEN" \\
  -H "Content-Type: application/json" \\
  -d '{"templates_data": "[{\\"name\\": \\"Мой шаблон\\", ...}]"}'`,
		jsExample: `const response = await fetch('/api/templates/import', {
  method: 'POST',
  headers: {
    'Authorization': 'Bearer YOUR_TOKEN',
    'Content-Type': 'application/json'
  },
  body: JSON.stringify({
    templates_data: JSON.stringify([{ name: 'Мой шаблон', ... }])
  })
});
const data = await response.json();`,
		successResponse: {
			status: 200,
			body: `{ "imported": 3, "skipped": 1 }`
		},
		errorResponses: [
			{ status: 422, description: 'Некорректные данные', body: '{ "detail": "Invalid templates data" }' }
		]
	},

	// === Auth ===
	{
		method: 'POST',
		path: '/api/auth/register',
		tag: 'auth',
		description: 'Регистрация нового пользователя.',
		parameters: [
			{ name: 'email', type: 'string', required: true, description: 'Email адрес' },
			{ name: 'username', type: 'string', required: true, description: 'Имя пользователя' },
			{ name: 'password', type: 'string', required: true, description: 'Пароль (минимум 8 символов)' }
		],
		curlExample: `curl -X POST http://localhost:8000/api/auth/register \\
  -H "Content-Type: application/json" \\
  -d '{"email": "user@example.com", "username": "ivan", "password": "secret123"}'`,
		jsExample: `const response = await fetch('/api/auth/register', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({
    email: 'user@example.com',
    username: 'ivan',
    password: 'secret123'
  })
});
const data = await response.json();`,
		successResponse: {
			status: 201,
			body: `{
  "user_id": 1,
  "username": "ivan",
  "email": "user@example.com"
}`
		},
		errorResponses: [
			{ status: 409, description: 'Пользователь уже существует', body: '{ "detail": "Username already registered" }' },
			{ status: 422, description: 'Ошибка валидации', body: '{ "detail": "Password must be at least 8 characters" }' }
		]
	},
	{
		method: 'POST',
		path: '/api/auth/login',
		tag: 'auth',
		description: 'Аутентификация пользователя. Возвращает JWT access_token и устанавливает refresh_token в cookie.',
		parameters: [
			{ name: 'login', type: 'string', required: true, description: 'Email или имя пользователя' },
			{ name: 'password', type: 'string', required: true, description: 'Пароль' }
		],
		curlExample: `curl -X POST http://localhost:8000/api/auth/login \\
  -H "Content-Type: application/json" \\
  -d '{"login": "ivan", "password": "secret123"}'`,
		jsExample: `const response = await fetch('/api/auth/login', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({
    login: 'ivan',
    password: 'secret123'
  })
});
const data = await response.json();
// data.access_token — используйте в заголовке Authorization`,
		successResponse: {
			status: 200,
			body: `{
  "access_token": "eyJhbGciOiJIUzI1NiIs...",
  "token_type": "bearer"
}`
		},
		errorResponses: [
			{ status: 401, description: 'Неверные учётные данные', body: '{ "detail": "Invalid credentials" }' }
		]
	},
	{
		method: 'POST',
		path: '/api/auth/refresh',
		tag: 'auth',
		description: 'Обновление JWT access_token с помощью refresh_token из cookie.',
		parameters: [
			{ name: 'refresh_token', type: 'cookie', required: true, description: 'Refresh token (HTTP-only cookie)' }
		],
		curlExample: `curl -X POST http://localhost:8000/api/auth/refresh \\
  -H "Cookie: refresh_token=YOUR_REFRESH_TOKEN"`,
		jsExample: `const response = await fetch('/api/auth/refresh', {
  method: 'POST',
  credentials: 'include'
});
const data = await response.json();`,
		successResponse: {
			status: 200,
			body: `{
  "access_token": "eyJhbGciOiJIUzI1NiIs...",
  "token_type": "bearer"
}`
		},
		errorResponses: [
			{ status: 401, description: 'Недействительный refresh token', body: '{ "detail": "Invalid refresh token" }' }
		]
	},
	{
		method: 'POST',
		path: '/api/auth/logout',
		tag: 'auth',
		description: 'Выход из системы. Удаляет refresh_token cookie.',
		parameters: [],
		curlExample: `curl -X POST http://localhost:8000/api/auth/logout \\
  -H "Authorization: Bearer YOUR_TOKEN"`,
		jsExample: `const response = await fetch('/api/auth/logout', {
  method: 'POST',
  headers: { 'Authorization': 'Bearer YOUR_TOKEN' },
  credentials: 'include'
});`,
		successResponse: {
			status: 200,
			body: `{ "message": "Logged out" }`
		},
		errorResponses: []
	},
	{
		method: 'GET',
		path: '/api/auth/me',
		tag: 'auth',
		description: 'Получить информацию о текущем пользователе.',
		parameters: [],
		curlExample: `curl http://localhost:8000/api/auth/me \\
  -H "Authorization: Bearer YOUR_TOKEN"`,
		jsExample: `const response = await fetch('/api/auth/me', {
  headers: { 'Authorization': 'Bearer YOUR_TOKEN' }
});
const data = await response.json();`,
		successResponse: {
			status: 200,
			body: `{
  "user_id": 1,
  "username": "ivan",
  "email": "user@example.com",
  "role": "user"
}`
		},
		errorResponses: [
			{ status: 401, description: 'Не авторизован', body: '{ "detail": "Not authenticated" }' }
		]
	},

	// === Usage ===
	{
		method: 'GET',
		path: '/api/usage/me',
		tag: 'usage',
		description: 'Получить статистику использования API текущим пользователем.',
		parameters: [
			{ name: 'period', type: 'string', required: false, default: 'monthly', description: 'Период: monthly, daily, all' }
		],
		curlExample: `curl "http://localhost:8000/api/usage/me?period=monthly" \\
  -H "Authorization: Bearer YOUR_TOKEN"`,
		jsExample: `const response = await fetch('/api/usage/me?period=monthly', {
  headers: { 'Authorization': 'Bearer YOUR_TOKEN' }
});
const data = await response.json();`,
		successResponse: {
			status: 200,
			body: `{
  "period": "monthly",
  "transcriptions": 42,
  "summaries": 28,
  "tokens_used": 150000
}`
		},
		errorResponses: [
			{ status: 401, description: 'Не авторизован', body: '{ "detail": "Not authenticated" }' }
		]
	},

	// === OpenAI-compatible ===
	{
		method: 'POST',
		path: '/v1/audio/transcriptions',
		tag: 'openai',
		description: 'OpenAI-совместимый эндпоинт для транскрипции. Совместим с Whisper API.',
		parameters: [
			{ name: 'file', type: 'UploadFile', required: true, description: 'Аудио файл' },
			{ name: 'model', type: 'string', required: false, default: 'whisper-1', description: 'Идентификатор модели' },
			{ name: 'language', type: 'string', required: false, description: 'Язык (ISO 639-1)' },
			{ name: 'response_format', type: 'string', required: false, default: 'json', description: 'Формат ответа: json, text, srt, vtt' }
		],
		curlExample: `curl -X POST http://localhost:8000/v1/audio/transcriptions \\
  -H "Authorization: Bearer YOUR_TOKEN" \\
  -F "file=@audio.mp3" \\
  -F "model=whisper-1" \\
  -F "language=ru" \\
  -F "response_format=json"`,
		jsExample: `const formData = new FormData();
formData.append('file', fileInput.files[0]);
formData.append('model', 'whisper-1');
formData.append('language', 'ru');

const response = await fetch('/v1/audio/transcriptions', {
  method: 'POST',
  headers: { 'Authorization': 'Bearer YOUR_TOKEN' },
  body: formData
});
const data = await response.json();`,
		successResponse: {
			status: 200,
			body: `{
  "text": "Транскрибированный текст..."
}`
		},
		errorResponses: [
			{ status: 413, description: 'Файл слишком большой', body: '{ "detail": "File too large" }' }
		]
	},

	// === System ===
	{
		method: 'GET',
		path: '/health',
		tag: 'system',
		description: 'Проверка работоспособности сервиса.',
		parameters: [],
		curlExample: `curl http://localhost:8000/health`,
		jsExample: `const response = await fetch('/health');
const data = await response.json();`,
		successResponse: {
			status: 200,
			body: `{
  "status": "ok",
  "transcriber": "ready"
}`
		},
		errorResponses: [
			{ status: 503, description: 'Сервис недоступен', body: '{ "status": "error", "transcriber": "not ready" }' }
		]
	}
];

export const faqItems: FaqItem[] = [
	{
		question: 'Какие форматы аудио и видео поддерживаются?',
		answer: 'Поддерживаются аудио форматы: WAV, MP3, FLAC, OGG, M4A, AAC, WMA, OPUS. Видео форматы: MP4, MKV, AVI, MOV, WebM, WMV, FLV, MPEG. Максимальный размер файла — 1 ГБ. Рекомендуется использовать WAV или FLAC для наилучшего качества распознавания.'
	},
	{
		question: 'Что такое диаризация и какие режимы доступны?',
		answer: 'Диаризация — это определение говорящего в аудиозаписи. Доступны три режима: <strong>none</strong> — без диаризации (только текст), <strong>pyannote</strong> — точная диаризация через pyannote/speaker-diarization-3.1 (рекомендуется), <strong>hybrid</strong> — лёгкий режим на основе VAD и кластеризации (быстрее, но менее точно).'
	},
	{
		question: 'Какие языки поддерживаются для транскрипции?',
		answer: 'Основной язык — русский (ru). Также поддерживается английский (en). Модель GigaAM оптимизирована для русского языка и обеспечивает наилучшее качество распознавания именно для него.'
	},
	{
		question: 'Как работает аутентификация через JWT?',
		answer: 'После регистрации и входа вы получаете <strong>access_token</strong> (JWT), который используется в заголовке <code>Authorization: Bearer TOKEN</code>. Access token имеет ограниченный срок действия. Refresh token хранится в HTTP-only cookie и автоматически обновляет access token через <code>/api/auth/refresh</code>.'
	},
	{
		question: 'Что такое Autoflow и как его использовать?',
		answer: 'Autoflow — это WebSocket-пайплайн для полной автоматической обработки файла за один запрос. Он последовательно выполняет: транскрипцию → генерацию саммари → создание майндмапа → извлечение инсайтов. Вы подключаетесь по WebSocket, отправляете файл и получаете прогресс-обновления и финальный результат.'
	},
	{
		question: 'Какие LLM модели доступны для анализа?',
		answer: 'Список доступных моделей можно получить через <code>GET /api/models</code>. Конкретный набор зависит от конфигурации сервера. Обычно доступны GPT-4o, GPT-4o Mini и другие модели. Вы можете указать модель через параметр <code>model</code> в запросах к эндпоинтам анализа.'
	},
	{
		question: 'Что означает ошибка 413?',
		answer: 'Ошибка <strong>413 Payload Too Large</strong> означает, что загружаемый файл превышает максимальный допустимый размер (1 ГБ). Попробуйте уменьшить размер файла или разбить его на части.'
	},
	{
		question: 'Что означает ошибка 502 и 503?',
		answer: '<strong>502 Bad Gateway</strong> — внутренняя ошибка обработки (например, неподдерживаемый кодек, ошибка модели). <strong>503 Service Unavailable</strong> — транскрайбер ещё не загружен или перегружен. Проверьте статус через <code>GET /health</code>.'
	},
	{
		question: 'Совместим ли API с OpenAI Whisper?',
		answer: 'Да, DialogScribe предоставляет эндпоинт <code>POST /v1/audio/transcriptions</code>, совместимый с OpenAI Whisper API. Он поддерживает те же параметры (file, model, language, response_format) и может использоваться как замена Whisper API в существующих интеграциях.'
	},
	{
		question: 'Как экспортировать транскрипцию в разные форматы?',
		answer: 'Используйте <code>POST /api/export</code> с параметром <code>format</code>. Доступные форматы: <strong>json</strong> (полная структура), <strong>srt</strong> (субтитры), <strong>vtt</strong> (WebVTT), <strong>txt</strong> (простой текст), <strong>docx</strong> (Word), <strong>pdf</strong>. Можно задать имена спикеров через параметр <code>speaker_names</code>.'
	}
];
