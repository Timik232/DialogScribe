"""
Утилиты для оценки и управления контекстом LLM.

Единый модуль для оценки токенов, расчёта бюджетов контекста,
разбиения текста на чанки и поиска релевантных чанков.
"""

import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Модельные лимиты контекста
# ---------------------------------------------------------------------------

_MODEL_CONTEXT_LIMITS: dict[str, int] = {
    "gpt-4.1": 1_047_576,
    "gpt-4.1-mini": 1_047_576,
    "gpt-4.1-nano": 1_047_576,
    "gpt-4o": 128_000,
    "gpt-4o-mini": 128_000,
    "gpt-4-turbo": 128_000,
    "gpt-4": 8_192,
    "gpt-3.5-turbo": 16_385,
    "claude-3-opus": 200_000,
    "claude-3-sonnet": 200_000,
    "claude-3-haiku": 200_000,
    "claude-3.5-sonnet": 200_000,
}

_FALLBACK_CONTEXT_LIMIT = 128_000

# ---------------------------------------------------------------------------
# Русские стоп-слова для find_relevant_chunks
# ---------------------------------------------------------------------------

_RUSSIAN_STOPWORDS = frozenset({
    "и", "в", "на", "с", "что", "это", "как", "не", "но", "он",
    "она", "они", "мы", "вы", "тут", "где", "когда", "для", "по",
    "из", "за", "от", "до", "о", "а", "к", "у", "же", "ли", "бы",
    "это", "так", "да", "нет", "ещё", "еще", "тоже", "или",
    "только", "уже", "был", "была", "были", "будет", "будут",
    "может", "можно", "нужно", "надо", "вот", "все", "всё",
    "кто", "что", "какой", "какая", "какие", "чей", "чейто",
})

_ENGLISH_STOPWORDS = frozenset({
    "the", "a", "an", "is", "are", "was", "were", "be", "been",
    "being", "have", "has", "had", "do", "does", "did", "will",
    "would", "could", "should", "may", "might", "shall", "can",
    "this", "that", "these", "those", "it", "its", "i", "me",
    "my", "we", "our", "you", "your", "he", "him", "his", "she",
    "her", "they", "them", "their", "what", "which", "who",
    "when", "where", "how", "why", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "as", "into", "about", "but",
    "or", "and", "not", "no", "if", "then", "so", "than",
})


# ---------------------------------------------------------------------------
# Оценка токенов
# ---------------------------------------------------------------------------


def estimate_tokens(text: str) -> int:
    """Кириллица-осознанная оценка количества токенов.

    - Кириллические символы: 1 token ≈ 2 chars
    - Латинские символы: 1 token ≈ 4 chars
    - Прочие символы: 1 token ≈ 3 chars

    Returns:
        Оценочное количество токенов.
    """
    if not text:
        return 0

    cyrillic = 0
    latin = 0
    other = 0

    for c in text:
        if "\u0400" <= c <= "\u04ff":
            cyrillic += 1
        elif c.isascii() and c.isalpha():
            latin += 1
        else:
            other += 1

    # Weighted: cyrillic/2 + latin/4 + other/3
    total = cyrillic / 2 + latin / 4 + other / 3
    return int(total) if total > 0 else 0


# ---------------------------------------------------------------------------
# Лимит контекста модели
# ---------------------------------------------------------------------------


def get_model_context_limit(model: str) -> int:
    """Вернуть размер контекстного окна модели.

    Если модель неизвестна, вернуть консервативное значение 128000.
    """
    if not model:
        return _FALLBACK_CONTEXT_LIMIT

    # Normalize: strip version suffix after last colon/dot for partial matches
    model_lower = model.lower().strip()

    # Direct lookup
    if model_lower in _MODEL_CONTEXT_LIMITS:
        return _MODEL_CONTEXT_LIMITS[model_lower]

    # Prefix matching (e.g. "gpt-4o-mini-2024-07-18" → "gpt-4o-mini")
    for key, limit in _MODEL_CONTEXT_LIMITS.items():
        if model_lower.startswith(key):
            return limit

    return _FALLBACK_CONTEXT_LIMIT


# ---------------------------------------------------------------------------
# Расчёт бюджета контекста
# ---------------------------------------------------------------------------


def get_context_budget(
    model: str,
    system_prompt: str,
    text: str,
    history: Optional[list[dict]] = None,
) -> dict:
    """Рассчитать доступный контекстный бюджет.

    Args:
        model: Имя LLM-модели.
        system_prompt: Системный промпт.
        text: Основной текст (транскрипция).
        history: История сообщений чата (опционально).

    Returns:
        Dict с ключами:
        - total: полный лимит контекста модели
        - used_prompt: токены system_prompt
        - used_text: токены text
        - used_history: токены history
        - available: доступные токены
        - needs_compression: bool (True если used >= 50% от total)
    """
    total = get_model_context_limit(model)
    used_prompt = estimate_tokens(system_prompt)
    used_text = estimate_tokens(text)
    used_history = 0
    if history:
        for msg in history:
            used_history += estimate_tokens(msg.get("content", ""))

    used = used_prompt + used_text + used_history
    available = max(0, total - used)

    return {
        "total": total,
        "used_prompt": used_prompt,
        "used_text": used_text,
        "used_history": used_history,
        "available": available,
        "needs_compression": used >= total * 0.5,
    }


# ---------------------------------------------------------------------------
# Разбиение текста на чанки
# ---------------------------------------------------------------------------


def split_into_chunks(
    text: str,
    max_tokens: int = 3000,
    overlap_sentences: int = 2,
) -> list[str]:
    """Разбить текст на чанки по границам предложений с перекрытием.

    Args:
        text: Исходный текст.
        max_tokens: Максимум токенов на чанк.
        overlap_sentences: Количество предложений перекрытия.

    Returns:
        Список чанков. Если текст короткий — один элемент.
    """
    if not text or not text.strip():
        return [text] if text else []

    if estimate_tokens(text) <= max_tokens:
        return [text]

    # Split by sentence boundaries: `.`, `!`, `?`, `\n`
    sentences = re.split(r"(?<=[.!?])\s+|\n+", text)
    sentences = [s for s in sentences if s.strip()]

    if len(sentences) <= 1:
        # Fallback: split by words
        words = text.split()
        chunk_size = max(1, max_tokens * 2)  # ~2 chars/token for mixed
        chunks = []
        for i in range(0, len(words), chunk_size):
            chunks.append(" ".join(words[i : i + chunk_size]))
        return chunks if chunks else [text]

    chunks: list[str] = []
    current_chunk: list[str] = []
    current_tokens = 0

    for sentence in sentences:
        sent_tokens = estimate_tokens(sentence)
        if current_tokens + sent_tokens > max_tokens and current_chunk:
            chunks.append(" ".join(current_chunk))
            # Keep overlap sentences
            current_chunk = current_chunk[-overlap_sentences:] if overlap_sentences > 0 else []
            current_tokens = sum(estimate_tokens(s) for s in current_chunk)

        current_chunk.append(sentence)
        current_tokens += sent_tokens

    if current_chunk:
        chunks.append(" ".join(current_chunk))

    return chunks


# ---------------------------------------------------------------------------
# Поиск релевантных чанков
# ---------------------------------------------------------------------------


def _extract_keywords(text: str) -> set[str]:
    """Извлечь ключевые слова из текста.

    Латинские слова > 3 символов, кириллические > 2 символов,
    без стоп-слов.
    """
    words = re.findall(r"[a-zA-Z\u0400-\u04ff]+", text.lower())
    keywords = set()
    for w in words:
        is_cyrillic = any("\u0400" <= c <= "\u04ff" for c in w)
        if is_cyrillic:
            if len(w) > 2 and w not in _RUSSIAN_STOPWORDS:
                keywords.add(w)
        else:
            if len(w) > 3 and w not in _ENGLISH_STOPWORDS:
                keywords.add(w)
    return keywords


def find_relevant_chunks(
    chunks: list[str],
    query: str,
    max_chunks: int = 3,
) -> list[str]:
    """Найти чанки, наиболее релевантные запросу.

    Использует простой keyword matching: извлечь слова из query,
    найти чанки с наибольшим совпадением.

    Args:
        chunks: Список текстовых чанков.
        query: Поисковый запрос.
        max_chunks: Максимум возвращаемых чанков.

    Returns:
        Список релевантных чанков, отсортированных по релевантности.
    """
    if not chunks or not query or not query.strip():
        return chunks[:max_chunks]

    query_keywords = _extract_keywords(query)
    if not query_keywords:
        return chunks[:max_chunks]

    scored: list[tuple[int, str]] = []
    for chunk in chunks:
        chunk_keywords = _extract_keywords(chunk)
        overlap = len(query_keywords & chunk_keywords)
        if overlap > 0:
            scored.append((overlap, chunk))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [chunk for _, chunk in scored[:max_chunks]]
