# DialogScribe

[DialogScribe](https://github.com/yaruslove/DialogScribe.git)  
based on GigaAm App wrapper

Микробиблиотека для транскрипции аудио и видео файлов на базе [GigaAM](https://github.com/salute-developers/GigaAM) с опциональной диаризацией спикеров через pyannote.

## Возможности

- **Транскрипция аудио и видео** любой длительности
- **Диаризация спикеров** через pyannote или гибридный подход
- **Множество форматов вывода**: TXT, JSON, SRT, VTT
- **Пакетная обработка** нескольких файлов
- **CLI интерфейс** для командной строки
- **Простой Python API** для интеграции в приложения

## Установка

### Требования

- Python ≥ 3.10
- [FFmpeg](https://ffmpeg.org/) установлен и доступен в PATH
- CUDA (опционально, для ускорения на GPU)

### Установка пакета

```bash
# 1. Клонировать репозиторий
git clone https://github.com/yaruslove/DialogScribe.git
cd https://github.com/yaruslove/DialogScribe


# 2. Установить GigaAM
git clone https://github.com/salute-developers/GigaAM.git
pip install -e ./GigaAM

# 3. Установить зависимости
pip install -r requirements.txt

# 4. (Опционально) Установить зависимости для диаризации
pip install -r requirements-diarization.txt

# 5. Установить пакет
pip install -e .
```

## Setup VENV
python -m venv venv
source venv/bin/activate  # Linux/Mac
или venv\Scripts\activate  # Windows

### Настройка для диаризации

Для использования диаризации спикеров необходим HuggingFace токен и доступ к моделям:

1. **Создайте токен на [HuggingFace](https://huggingface.co/settings/tokens)**
   - Нажмите "New token"
   - Выберите права "Read"
   - Скопируйте токен

2. **Примите условия использования моделей:**
   - [pyannote/segmentation-3.0](https://huggingface.co/pyannote/segmentation-3.0) - нажмите "Agree and access repository"
   - [pyannote/speaker-diarization-3.1](https://huggingface.co/pyannote/speaker-diarization-3.1) - нажмите "Agree and access repository"
   - [pyannote/speaker-diarization](https://huggingface.co/pyannote/speaker-diarization) - нажмите "Agree and access repository" (опционально, fallback)
   - [pyannote/speaker-diarization-community-1](https://huggingface.co/pyannote/speaker-diarization-community-1)


3. **Установите переменную окружения:**

```bash
# Способ 1: экспорт в консоли
export HF_TOKEN=your_actual_token_here

# Способ 2: создать .env файл
cp .env.example .env
# Затем отредактировать .env и вставить ваш токен
```

4. **Проверьте настройку:**

```bash
python setup_diarization.py
```

Этот скрипт проверит доступ к моделям и поможет настроить диаризацию.

## Быстрый старт

### Python API

```python
from gigaam_transcriber import GigaAMTranscriber

# Создание транскрибера
transcriber = GigaAMTranscriber()

# Простая транскрипция
result = transcriber.transcribe("audio.wav")
print(result.text)

# С диаризацией спикеров
result = transcriber.transcribe(
    "meeting.mp4",
    diarization="pyannote",
    num_speakers=3
)

# Вывод по сегментам
for seg in result.segments:
    print(f"[{seg.start:.1f}-{seg.end:.1f}] {seg.speaker}: {seg.text}")

# Сохранение в файл
result.save("transcript.json", format="json")
result.save("subtitles.srt")
```

### CLI

```bash
# Простая транскрипция
gigaam-transcribe audio.wav

# С диаризацией
gigaam-transcribe meeting.mp4 -d pyannote --speakers 3 -o meeting.txt

# Вывод в JSON
gigaam-transcribe interview.mp3 -d pyannote -f json -o interview.json

# Субтитры SRT
gigaam-transcribe video.mp4 -f srt -o subtitles.srt

# Пакетная обработка
gigaam-batch *.mp3 -o transcripts/ -d pyannote
```

## API Reference

### GigaAMTranscriber

Основной класс для транскрипции.

```python
from gigaam_transcriber import GigaAMTranscriber

transcriber = GigaAMTranscriber(
    model_name="v3_e2e_rnnt",  # Модель GigaAM
    device="auto",             # "auto", "cuda", "cpu"
    hf_token=None,            # HuggingFace токен (или из HF_TOKEN)
    verbose=False             # Подробный вывод
)
```

#### Методы

##### transcribe()

```python
result = transcriber.transcribe(
    input_path,                    # Путь к файлу
    output_path=None,              # Путь для сохранения
    diarization="none",            # "none", "pyannote", "hybrid"
    num_speakers=None,             # Количество спикеров
    output_format="txt",           # "txt", "json", "srt", "vtt"
    merge_same_speaker=True,       # Объединять сегменты одного спикера
    min_segment_gap=0.5           # Gap для объединения (сек)
)
```

##### audio2text() / video2text()

```python
# Транскрипция аудио
result = transcriber.audio2text("audio.wav", diarization="pyannote")

# Транскрипция видео
result = transcriber.video2text("video.mp4", diarization="pyannote")
```

##### transcribe_batch()

```python
results = transcriber.transcribe_batch(
    ["file1.mp3", "file2.mp4"],
    output_dir="transcripts/",
    diarization="pyannote"
)
```

### TranscriptionResult

Результат транскрипции.

```python
result.text         # Полный текст
result.segments     # Список сегментов
result.duration     # Длительность (сек)
result.language     # Язык
result.model_name   # Модель

# Форматирование
result.to_txt()     # Текст с таймкодами
result.to_json()    # JSON
result.to_srt()     # Субтитры SRT
result.to_vtt()     # Субтитры VTT

# Сохранение
result.save("output.txt")
result.save("output.json", format="json")

# Утилиты
result.get_speakers()              # Список спикеров
result.filter_by_speaker("Спикер №1")  # Фильтрация
```

### TranscriptionSegment

Сегмент транскрипции.

```python
segment.text       # Текст сегмента
segment.start      # Начало (сек)
segment.end        # Конец (сек)
segment.speaker    # Спикер
segment.duration   # Длительность
```

## Модели GigaAM

| Модель | Описание | Рекомендация |
|--------|----------|--------------|
| `v3_e2e_rnnt` | С пунктуацией и нормализацией (RNNT) | **Рекомендуется** |
| `v3_e2e_ctc` | С пунктуацией и нормализацией (CTC) | Альтернатива |
| `v3_rnnt` | Без пунктуации (RNNT) | - |
| `v3_ctc` | Без пунктуации (CTC) | - |

## Форматы вывода

### TXT (с диаризацией)
```
[00:00:00 - 00:17:41] Спикер №1: текст...
[00:18:98 - 00:39:26] Спикер №2: текст...
```

### JSON
```json
{
  "metadata": {
    "source": "meeting.mp4",
    "duration": 3600.5,
    "speakers_count": 3
  },
  "segments": [...],
  "full_text": "..."
}
```

### SRT
```
1
00:00:00,000 --> 00:00:17,410
[Спикер №1] текст...
```

### VTT
```
WEBVTT

00:00:00.000 --> 00:00:17.410
[Спикер №1] текст...
```

## Режимы диаризации

- **none** — без диаризации, только транскрипция
- **pyannote** — полная диаризация через pyannote/speaker-diarization-3.1
- **hybrid** — гибридный подход: VAD + эмбеддинги + кластеризация (легче, но менее точно)

## Примеры

### Транскрипция совещания

```python
from gigaam_transcriber import GigaAMTranscriber

with GigaAMTranscriber() as transcriber:
    result = transcriber.transcribe(
        "meeting.mp4",
        diarization="pyannote",
        num_speakers=4,
        output_format="json"
    )
    
    # Статистика по спикерам
    for speaker in result.get_speakers():
        filtered = result.filter_by_speaker(speaker)
        duration = sum(s.duration for s in filtered.segments)
        print(f"{speaker}: {duration:.0f} сек")
    
    result.save("meeting_transcript.json")
```

### Создание субтитров для видео

```python
from gigaam_transcriber import GigaAMTranscriber

transcriber = GigaAMTranscriber()
result = transcriber.transcribe("video.mp4")

# SRT субтитры
result.save("subtitles.srt")

# WebVTT субтитры
result.save("subtitles.vtt")
```

### Пакетная обработка

```python
from gigaam_transcriber import GigaAMTranscriber
from pathlib import Path

transcriber = GigaAMTranscriber()

audio_files = list(Path("recordings").glob("*.mp3"))

def progress(current, total, filename):
    print(f"Processing {current}/{total}: {filename}")

results = transcriber.transcribe_batch(
    audio_files,
    output_dir="transcripts",
    diarization="pyannote",
    progress_callback=progress
)

print(f"Processed {len(results)} files")
```

## Структура проекта

```
gigaam_transcriber/
├── __init__.py           # Публичный API
├── transcriber.py        # Основной класс GigaAMTranscriber
├── audio_processor.py    # Обработка аудио/видео
├── diarization.py        # Диаризация спикеров
├── segment_merger.py     # Сшивка сегментов
├── formatters.py         # Форматирование вывода
├── data_models.py        # Структуры данных
├── exceptions.py         # Исключения
├── utils.py              # Утилиты
└── cli.py                # CLI интерфейс
```

## Тестирование

```bash
# Запуск всех тестов
pytest tests/ -v

# Тесты без модели (быстрые)
pytest tests/ -v -m "not requires_model"

# Тесты с моделью (требуют GPU)
pytest tests/ -v -m requires_model
```

## Лицензия

MIT License

## Ссылки

- [GigaAM GitHub](https://github.com/salute-developers/GigaAM)
- [GigaAM paper (arXiv)](https://arxiv.org/abs/2506.01192)
- [pyannote speaker-diarization](https://huggingface.co/pyannote/speaker-diarization-3.1)
