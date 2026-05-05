#!/usr/bin/env python3
"""
CLI интерфейс для GigaAM Transcriber.

Использование:
    gigaam-transcribe audio.wav
    gigaam-transcribe meeting.mp4 -d pyannote --speakers 3 -o meeting.txt
    gigaam-transcribe interview.mp3 -d pyannote -f json -o interview.json
    gigaam-transcribe video.mp4 -f srt -o subtitles.srt
"""

import os
import importlib
import sys
import threading
import time
import unicodedata
from pathlib import Path

click = importlib.import_module("click")


def normalize_path(path: str) -> Path:
    """
    Нормализация пути для обхода проблем с Unicode (NFD vs NFC).

    На некоторых файловых системах (особенно macOS HFS+) символы типа "й"
    могут храниться в decomposed форме (и + combining breve).
    """
    # Нормализуем в NFC форму
    normalized = unicodedata.normalize("NFC", str(path))
    p = Path(normalized)

    if p.exists():
        return p

    # Если файл не найден, пробуем найти через glob в родительской директории
    parent = p.parent
    name = p.name

    if parent.exists():
        # Ищем файл с похожим именем
        for f in parent.iterdir():
            # Нормализуем имя файла и сравниваем
            if unicodedata.normalize("NFC", f.name) == name:
                return f
            # Или сравниваем в NFD форме
            if unicodedata.normalize("NFD", f.name) == unicodedata.normalize("NFD", name):
                return f

    return p  # Возвращаем оригинальный путь, Click сам выдаст ошибку


# Добавляем путь к пакету
sys.path.insert(0, str(Path(__file__).parent.parent))

from gigaam_transcriber import (
    GigaAMTranscriber,
    TranscriberError,
    __version__,
)


def print_banner():
    """Вывод баннера."""
    click.echo(
        click.style(
            """
╔═══════════════════════════════════════════════════════════════╗
║           GigaAM Transcriber v{}                       ║
║   Транскрипция аудио/видео на базе Mistral Voxtral       ║
╚═══════════════════════════════════════════════════════════════╝
""".format(__version__),
            fg="cyan",
        )
    )


class UnicodePathType(click.Path):
    """Click Path type с нормализацией Unicode."""

    def convert(self, value, param, ctx):
        # Сначала нормализуем путь
        normalized_path = normalize_path(value)
        if normalized_path.exists():
            return str(normalized_path)
        # Если не нашли, пробуем стандартный способ
        return super().convert(value, param, ctx)


@click.command()
@click.argument("input_file", type=UnicodePathType(exists=True))
@click.option("-o", "--output", type=click.Path(), help="Путь к выходному файлу")
@click.option(
    "-d",
    "--diarize",
    type=click.Choice(["none", "pyannote", "hybrid"]),
    default="none",
    help="Режим диаризации спикеров",
)
@click.option("--speakers", type=int, default=None, help="Ожидаемое количество спикеров")
@click.option("--min-speakers", type=int, default=None, help="Минимальное количество спикеров")
@click.option("--max-speakers", type=int, default=None, help="Максимальное количество спикеров")
@click.option(
    "-f",
    "--format",
    "output_format",
    type=click.Choice(["txt", "json", "srt", "vtt"]),
    default="txt",
    help="Формат вывода (по умолчанию: txt)",
)
@click.option("--no-merge", is_flag=True, help="Не объединять сегменты одного спикера")
@click.option(
    "--gap", type=float, default=0.5, help="Максимальный gap для объединения сегментов (секунды)"
)
@click.option(
    "--api-key",
    default=lambda: os.getenv("MISTRAL_API_KEY", ""),
    help="API ключ Mistral (по умолчанию из MISTRAL_API_KEY)",
)
@click.option("--asr-url", default="https://api.mistral.ai", help="Базовый URL ASR API")
@click.option(
    "--chunk-threshold",
    type=float,
    default=1800.0,
    help="Min duration in seconds to trigger chunking. 0 to disable.",
)
@click.option("--chunk-duration", type=float, default=300.0, help="Chunk duration in seconds")
@click.option("-v", "--verbose", is_flag=True, help="Подробный вывод")
@click.option("--version", is_flag=True, help="Показать версию")
@click.option("--quiet", "-q", is_flag=True, help="Тихий режим - только результат")
def main(
    input_file,
    output,
    diarize,
    speakers,
    min_speakers,
    max_speakers,
    output_format,
    no_merge,
    gap,
    api_key,
    asr_url,
    chunk_threshold,
    chunk_duration,
    verbose,
    version,
    quiet,
):
    """
    GigaAM Audio/Video Transcriber

    Транскрибирует аудио и видео файлы с использованием Mistral Voxtral API.

    Примеры:

    \b
      # Простая транскрипция
      gigaam-transcribe audio.wav

    \b
      # С диаризацией спикеров
      gigaam-transcribe meeting.mp4 -d pyannote --speakers 3

    \b
      # Сохранение в JSON
      gigaam-transcribe interview.mp3 -f json -o interview.json

    \b
      # Создание субтитров
      gigaam-transcribe video.mp4 -f srt -o subtitles.srt
    """
    if version:
        click.echo(f"gigaam-transcribe версия {__version__}")
        return

    if not quiet:
        print_banner()

    # Проверка HF_TOKEN для диаризации
    if diarize != "none" and not os.getenv("HF_TOKEN"):
        click.echo(
            click.style(
                "⚠️  Предупреждение: HF_TOKEN не установлен. "
                "Диаризация требует токен HuggingFace.\n"
                "   Установите: export HF_TOKEN=<ваш токен>\n",
                fg="yellow",
            )
        )

    try:
        if not quiet:
            click.echo(f"📁 Файл: {input_file}")
            click.echo("🤖 ASR: Mistral Voxtral")
            click.echo(f"🌐 ASR URL: {asr_url}")
            click.echo(f"🎤 Диаризация: {diarize}")
            click.echo("")

        # Создание транскрибера
        with GigaAMTranscriber(
            api_key=api_key,
            asr_url=asr_url,
            verbose=verbose,
            chunk_threshold=chunk_threshold,
            chunk_duration=chunk_duration,
        ) as transcriber:
            if not quiet:
                click.echo("🔄 Отправка аудио в Mistral ASR...")

            # Транскрипция с индикатором прогресса

            if not quiet:
                # Показываем анимированный прогресс-бар во время транскрипции
                class AnimatedProgress:
                    def __init__(self):
                        self._running = False
                        self._thread = None
                        self._progress = 0
                        self._direction = 1
                        self._chars = ["|", "/", "-", "\\"]
                        self._char_idx = 0

                    def start(self):
                        self._running = True
                        self._thread = threading.Thread(target=self._animate)
                        self._thread.daemon = True
                        self._thread.start()

                    def stop(self):
                        self._running = False
                        if self._thread and self._thread.is_alive():
                            self._thread.join()
                        # Завершаем бар на 100%
                        bar_width = 40
                        filled = bar_width
                        bar = "=" * filled
                        click.echo(f"\rТранскрипция  [{bar}]  100%")
                        click.echo()  # Новая строка

                    def _animate(self):
                        """Анимация прогресс-бара."""
                        bar_width = 40
                        while self._running:
                            # Обновляем прогресс
                            self._progress += self._direction * 2
                            if self._progress >= 95:
                                self._direction = -1
                            elif self._progress <= 10:
                                self._direction = 1

                            # Создаем бар
                            filled = int(self._progress * bar_width / 100)
                            bar = "=" * filled

                            # Анимированный символ в конце
                            char = self._chars[self._char_idx % len(self._chars)]
                            self._char_idx += 1

                            # Выводим
                            click.echo(
                                f"\rТранскрипция  [{bar}{char}] {self._progress:3d}%", nl=False
                            )
                            time.sleep(0.3)

                progress = AnimatedProgress()
                progress.start()
                try:
                    result = transcriber.transcribe(
                        input_file,
                        output_path=output,
                        diarization=diarize,
                        num_speakers=speakers,
                        min_speakers=min_speakers,
                        max_speakers=max_speakers,
                        output_format=output_format,
                        merge_same_speaker=not no_merge,
                        min_segment_gap=gap,
                    )
                finally:
                    progress.stop()
            else:
                # Тихий режим
                result = transcriber.transcribe(
                    input_file,
                    output_path=output,
                    diarization=diarize,
                    num_speakers=speakers,
                    min_speakers=min_speakers,
                    max_speakers=max_speakers,
                    output_format=output_format,
                    merge_same_speaker=not no_merge,
                    min_segment_gap=gap,
                )

        # Вывод результата
        # Вывод результата
        if not quiet:
            click.echo("")
            click.echo(click.style("✅ Транскрипция завершена!", fg="green"))
            click.echo(f"⏱️  Время обработки: {result.processing_time:.1f} сек")
            click.echo(f"📊 Длительность аудио: {format_duration(result.duration)}")
            click.echo(f"📝 Сегментов: {len(result.segments)}")

            if result.get_speakers():
                click.echo(f"👥 Спикеры: {', '.join(result.get_speakers())}")

            if output:
                click.echo(f"💾 Сохранено в: {output}")

            click.echo("")
            click.echo(click.style("─" * 60, fg="cyan"))
            click.echo("")

        # Вывод транскрипции
        if output_format == "txt":
            click.echo(result.to_txt())
        elif output_format == "json":
            click.echo(result.to_json())
        elif output_format == "srt":
            click.echo(result.to_srt())
        elif output_format == "vtt":
            click.echo(result.to_vtt())

    except TranscriberError as e:
        click.echo(click.style(f"❌ Ошибка: {e}", fg="red"), err=True)
        sys.exit(1)
    except KeyboardInterrupt:
        click.echo(click.style("\n⚠️  Прервано пользователем", fg="yellow"))
        sys.exit(130)
    except Exception as e:
        click.echo(click.style(f"❌ Неожиданная ошибка: {e}", fg="red"), err=True)
        if verbose:
            import traceback

            traceback.print_exc()
        sys.exit(1)


class DummyProgressBar:
    """Заглушка для прогресс-бара в тихом режиме."""

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def update(self, n):
        pass


def format_duration(seconds: float) -> str:
    """Форматирование длительности."""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)

    if hours > 0:
        return f"{hours}ч {minutes}м {secs}с"
    elif minutes > 0:
        return f"{minutes}м {secs}с"
    else:
        return f"{secs}с"


@click.command()
@click.argument("input_files", nargs=-1, type=click.Path(exists=True))
@click.option("-o", "--output-dir", type=click.Path(), help="Директория для результатов")
@click.option("-d", "--diarize", type=click.Choice(["none", "pyannote", "hybrid"]), default="none")
@click.option(
    "-f",
    "--format",
    "output_format",
    type=click.Choice(["txt", "json", "srt", "vtt"]),
    default="txt",
)
@click.option(
    "--api-key",
    default=lambda: os.getenv("MISTRAL_API_KEY", ""),
    help="API ключ Mistral (по умолчанию из MISTRAL_API_KEY)",
)
@click.option("--asr-url", default="https://api.mistral.ai", help="Базовый URL ASR API")
@click.option("-v", "--verbose", is_flag=True)
def batch(input_files, output_dir, diarize, output_format, api_key, asr_url, verbose):
    """
    Пакетная транскрипция нескольких файлов.

    Пример:
        gigaam-batch *.mp3 -o transcripts/ -d pyannote
    """
    if not input_files:
        click.echo("Не указаны файлы для обработки", err=True)
        return

    print_banner()
    click.echo(f"📁 Файлов для обработки: {len(input_files)}")

    if output_dir:
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

    with GigaAMTranscriber(api_key=api_key, asr_url=asr_url, verbose=verbose) as transcriber:

        def progress_callback(current, total, filename):
            click.echo(f"[{current + 1}/{total}] {filename}")

        results = transcriber.transcribe_batch(
            list(input_files),
            output_dir=output_dir,
            diarization=diarize,
            output_format=output_format,
            progress_callback=progress_callback,
        )

    # Статистика
    successful = sum(1 for r in results if r.text)
    failed = len(results) - successful

    click.echo("")
    click.echo(click.style(f"✅ Успешно: {successful}", fg="green"))
    if failed:
        click.echo(click.style(f"❌ Ошибок: {failed}", fg="red"))


# Entry point для batch команды
def batch_main():
    getattr(batch, "main")(standalone_mode=True)


if __name__ == "__main__":
    getattr(main, "main")(standalone_mode=True)
