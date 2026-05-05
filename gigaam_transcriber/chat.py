"""
Chat with transcript: Q&A over transcription text with citation support.

LLM-модуль для ответов на вопросы по транскрипции с цитированием.
Поддерживает длинные транскрипции через сжатие контекста.
"""

import hashlib
import logging
from typing import Optional

from gigaam_transcriber.summarizer import LLMClient
from gigaam_transcriber.context_utils import (
    estimate_tokens,
    find_relevant_chunks,
    get_context_budget,
    get_model_context_limit,
    split_into_chunks,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

CHAT_SYSTEM_PROMPT = """\
You are a helpful assistant that answers questions based on a transcript of an audio/video recording.

RULES:
1. Answer ONLY based on the provided transcript. Do not make up information.
2. When referencing something from the transcript, cite it using the format: [Speaker, MM:SS] «exact quote»
3. If the information is not in the transcript, say so honestly.
4. Respond in the same language the user asks in.
5. Be concise but thorough.
"""

CHUNK_SUMMARY_PROMPT = """\
Создай краткое саммари этого фрагмента транскрипции. Включи:
- Основные обсуждаемые темы
- Ключевые имена и роли
- Важные решения и факты
- Временные метки если есть

Ответ на русском, 3-5 предложений."""

# ---------------------------------------------------------------------------
# Chunk summary cache
# ---------------------------------------------------------------------------

_chunk_summary_cache: dict[str, list[dict]] = {}


def _get_cache_key(text: str) -> str:
    return hashlib.md5(text.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Compressed context helpers
# ---------------------------------------------------------------------------


def _create_chunk_summaries(
    text: str,
    llm_client: LLMClient,
    chunk_size: int = 3000,
) -> list[dict]:
    """Split transcript into chunks and summarize each."""
    chunks = split_into_chunks(text, max_tokens=chunk_size)
    summaries: list[dict] = []

    for i, chunk in enumerate(chunks):
        logger.debug("Summarizing chunk %d/%d", i + 1, len(chunks))
        try:
            summary = llm_client.call(CHUNK_SUMMARY_PROMPT, chunk, max_tokens=512)
            summaries.append({
                "index": i,
                "summary": summary.strip(),
                "chunk_text": chunk,
            })
        except Exception as e:
            logger.warning("Failed to summarize chunk %d: %s", i + 1, e)
            summaries.append({
                "index": i,
                "summary": chunk[:200] + "..." if len(chunk) > 200 else chunk,
                "chunk_text": chunk,
            })

    return summaries


def _build_compressed_context(
    chunk_summaries: list[dict],
    query: str,
    max_tokens: int = 30000,
) -> str:
    """Build compressed context from chunk summaries + relevant chunks."""
    parts: list[str] = []
    used_tokens = 0

    overview_header = "СЖАТЫЙ КОНТЕКСТ ТРАНСКРИПЦИИ:\n\n"
    overview_parts: list[str] = []
    for cs in chunk_summaries:
        line = f"[Часть {cs['index'] + 1}] {cs['summary']}"
        line_tokens = estimate_tokens(line)
        if used_tokens + line_tokens > max_tokens // 2:
            break
        overview_parts.append(line)
        used_tokens += line_tokens

    parts.append(overview_header + "\n".join(overview_parts))

    if query:
        chunk_texts = [cs["chunk_text"] for cs in chunk_summaries]
        relevant = find_relevant_chunks(chunk_texts, query, max_chunks=3)
        if relevant:
            parts.append("\n\nРЕЛЕВАНТНЫЕ ФРАГМЕНТЫ:\n")
            for j, r in enumerate(relevant):
                r_tokens = estimate_tokens(r)
                if used_tokens + r_tokens > max_tokens:
                    break
                parts.append(f"\n--- Фрагмент {j + 1} ---\n{r}\n")
                used_tokens += r_tokens

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Main chat function
# ---------------------------------------------------------------------------


def chat_with_transcript(
    text: str,
    messages: list[dict],
    model: Optional[str] = None,
    llm_client: Optional[LLMClient] = None,
) -> dict:
    """Send a chat question about a transcript to the LLM.

    For long transcripts, uses compressed context (chunk summaries + relevant chunks).
    """
    if llm_client is None:
        llm_client = LLMClient()

    if model and model != llm_client.config.model:
        llm_client.update_config(
            llm_client.config.base_url,
            llm_client.config.api_key,
            model,
        )

    effective_model = llm_client.config.model
    history = messages[:-1] if messages else []
    latest_message = messages[-1] if messages else None

    budget = get_context_budget(effective_model, CHAT_SYSTEM_PROMPT, text, history)

    if budget["needs_compression"]:
        transcript_content = _get_compressed_transcript(text, latest_message, llm_client, budget)
    else:
        transcript_content = f"TRANSCRIPT:\n{text}"

    truncated_history = _truncate_history(transcript_content, history, effective_model)

    api_messages = [
        {"role": "system", "content": CHAT_SYSTEM_PROMPT},
        {"role": "system", "content": transcript_content},
    ]
    api_messages.extend(truncated_history)
    if latest_message:
        api_messages.append(latest_message)

    try:
        client = llm_client._get_client()
        response = client.chat.completions.create(
            model=effective_model,
            messages=api_messages,
            max_tokens=4096,
        )
        answer = response.choices[0].message.content.strip()
        return {"answer": answer}
    except Exception as exc:
        logger.error("Chat with transcript failed: %s", exc)
        raise


def _get_compressed_transcript(
    text: str,
    latest_message: Optional[dict],
    llm_client: LLMClient,
    budget: dict,
) -> str:
    """Get or create compressed transcript context."""
    cache_key = _get_cache_key(text)
    if cache_key not in _chunk_summary_cache:
        logger.info("Creating chunk summaries for long transcript (cache miss)")
        _chunk_summary_cache[cache_key] = _create_chunk_summaries(text, llm_client)

    summaries = _chunk_summary_cache[cache_key]
    query = latest_message.get("content", "") if latest_message else ""
    max_ctx = min(budget["total"] // 2, 60000)

    return _build_compressed_context(summaries, query, max_tokens=max_ctx)


def _truncate_history(
    transcript_content: str,
    messages: list[dict],
    model: str,
) -> list[dict]:
    """Truncate conversation history to fit within token budget."""
    max_context = get_model_context_limit(model)
    transcript_tokens = estimate_tokens(transcript_content)
    system_tokens = estimate_tokens(CHAT_SYSTEM_PROMPT)
    budget = max_context - transcript_tokens - system_tokens - 4096  # reserve for output

    if budget <= 0:
        return []

    kept: list[dict] = []
    used = 0
    for msg in reversed(messages):
        msg_tokens = estimate_tokens(msg.get("content", ""))
        if used + msg_tokens > budget or len(kept) >= 10:
            break
        kept.insert(0, msg)
        used += msg_tokens

    return kept
