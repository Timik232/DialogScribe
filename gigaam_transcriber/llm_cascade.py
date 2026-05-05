"""Two-level LLM Cascade for Live Advisor Agent.

Layer 1 (Classifier): Fast/cheap model classifies transcript fragment
  → neutral / objection / price_question / competitor_mention / clarification / commitment_signal / silence
Layer 2 (Advisor): Strong model generates structured hint ONLY for significant events.
"""

import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Optional

from gigaam_transcriber.hint_typology import Hint, HintType, HintPriority
from gigaam_transcriber.summarizer import LLMClient, LLMClientConfig

logger = logging.getLogger(__name__)


# Default model names (can be overridden via env vars)
DEFAULT_CLASSIFIER_MODEL = os.getenv("LLM_CLASSIFIER_MODEL", "gpt-4.1")
DEFAULT_ADVISOR_MODEL = os.getenv("LLM_ADVISOR_MODEL", "mistral/mistral-large-latest")


# ─── Layer 1: Classification ────────────────────────────────────


CLASSIFIER_SYSTEM_PROMPT = """Ты — классификатор фрагментов делового диалога.
Проанализируй фрагмент транскрипта и классифицируй его.

Категории:
- "neutral" — рутина, small talk, обычное обсуждение без значимых событий
- "objection" — возражение клиента (дорого, не подходит, подумаем, уже работаем с другими)
- "price_question" — вопрос или обсуждение цены, скидок, бюджета, условий оплаты
- "competitor_mention" — упоминание конкурента или альтернативного поставщика
- "clarification" — запрос уточнения от клиента (важно, но не возражение)
- "commitment_signal" — сигнал готовности к сделке (договорились, ок, согласен)
- "silence" — пауза в разговоре

ВАЖНО: Классифицируй как НЕ-neutral ТОЛЬКО если есть конкретное значимое событие.
Нейтральные обсуждения, приветствия, small talk — всегда "neutral".

Ответь ТОЛЬКО валидным JSON:
{"classification": "<category>", "confidence": 0.0-1.0, "excerpt": "<ключевая фраза>"}"""


# ─── Layer 2: Advisory ──────────────────────────────────────────


ADVISOR_SYSTEM_PROMPT = """Ты — ассистент менеджера B2B-продаж. На основе контекста встречи и фрагмента диалога дай подсказку.

Типы подсказок:
- "tactical" — немедленное действие (что сказать/спросить прямо сейчас)
- "strategic" — направление разговора (куда вести диалог)
- "warning" — предупреждение (риск, конкурент, возражение)
- "analytical" — аналитическое наблюдение (тенденция, сдвиг в обсуждении)

Приоритеты: "critical", "high", "medium", "low"

ПРАВИЛА:
- Не повторяй подсказки, которые уже давались (см. историю)
- Учитывай фазу переговоров и накопленные факты
- Не выдумывай данные — опирайся только на транскрипт и контекст
- Не давай юридических/финансовых гарантий
- Не рекомендуй манипулятивные техники

{feedback_bias}

Ответь ТОЛЬКО валидным JSON:
{"type": "<tactical|strategic|warning|analytical>", "text": "<подсказка>", "priority": "<critical|high|medium|low>", "rationale": "<почему эта подсказка>"}"""


@dataclass
class ClassificationResult:
    """Result of Layer 1 classification."""
    classification: str
    confidence: float
    excerpt: str

    @property
    def is_significant(self) -> bool:
        """Whether this fragment should trigger Layer 2."""
        return self.classification != "neutral" and self.confidence >= 0.5


class LLMCascade:
    """Two-level LLM cascade for hint generation.

    Layer 1: Classifies transcript fragment (cheap, fast)
    Layer 2: Generates hint (expensive, only when needed)
    """

    def __init__(
        self,
        classifier_config: Optional[LLMClientConfig] = None,
        advisor_config: Optional[LLMClientConfig] = None,
    ):
        # Classifier client (fast/cheap model)
        if classifier_config:
            self._classifier = LLMClient(classifier_config)
        else:
            cfg = LLMClientConfig()
            if DEFAULT_CLASSIFIER_MODEL:
                cfg.model = DEFAULT_CLASSIFIER_MODEL
            self._classifier = LLMClient(cfg)

        # Advisor client (strong model)
        if advisor_config:
            self._advisor = LLMClient(advisor_config)
        else:
            cfg = LLMClientConfig()
            if DEFAULT_ADVISOR_MODEL:
                cfg.model = DEFAULT_ADVISOR_MODEL
            self._advisor = LLMClient(cfg)

    def classify(self, fragment: str) -> Optional[ClassificationResult]:
        """Layer 1: Classify a transcript fragment.

        Returns ClassificationResult or None on error.
        """
        if not fragment or not fragment.strip():
            return None

        try:
            response = self._classifier.call(
                system_prompt=CLASSIFIER_SYSTEM_PROMPT,
                user_text=fragment,
                max_tokens=200,
            )
            return self._parse_classification(response)
        except Exception as e:
            logger.error(f"Layer 1 classification error: {e}")
            return None

    def advise(
        self,
        fragment: str,
        context_summary: str,
        feedback_bias: str = "",
    ) -> Optional[Hint]:
        """Layer 2: Generate a structured hint.

        Args:
            fragment: The classified transcript fragment
            context_summary: Accumulated context from SessionAccumulator
            feedback_bias: Feedback bias text from Accumulator

        Returns Hint or None on error.
        """
        if not fragment or not fragment.strip():
            return None

        system_prompt = ADVISOR_SYSTEM_PROMPT.format(
            feedback_bias=feedback_bias if feedback_bias else "Нет дополнительных указаний по feedback."
        )

        user_text = f"""Контекст встречи:
{context_summary}

Фрагмент диалога:
{fragment}

Дай одну конкретную подсказку."""

        try:
            response = self._advisor.call(
                system_prompt=system_prompt,
                user_text=user_text,
                max_tokens=500,
            )
            return self._parse_hint(response)
        except Exception as e:
            logger.error(f"Layer 2 advisory error: {e}")
            return None

    def run(
        self,
        fragment: str,
        context_summary: str = "",
        feedback_bias: str = "",
    ) -> Optional[Hint]:
        """Full cascade: classify → skip if neutral → advise if significant.

        This is the main entry point for the cascade.
        Returns Hint if significant event detected, None otherwise.
        """
        # Layer 1: Classify
        classification = self.classify(fragment)
        if not classification:
            logger.debug("Layer 1 returned None, skipping")
            return None

        logger.info(
            f"Layer 1: {classification.classification} "
            f"(confidence={classification.confidence:.2f}, "
            f"excerpt='{classification.excerpt[:50]}')"
        )

        if not classification.is_significant:
            logger.debug("Fragment classified as neutral, skipping Layer 2")
            return None

        # Layer 2: Generate hint
        hint = self.advise(fragment, context_summary, feedback_bias)
        if hint:
            logger.info(f"Layer 2 generated hint: [{hint.type.value}] {hint.text[:80]}")

        return hint

    @staticmethod
    def _parse_classification(response: str) -> Optional[ClassificationResult]:
        """Parse Layer 1 JSON response."""
        try:
            # Try direct JSON parse
            data = json.loads(response.strip())
        except json.JSONDecodeError:
            # Try extracting from markdown code block
            import re
            match = re.search(r"```(?:json)?\s*(.*?)```", response, re.DOTALL)
            if match:
                try:
                    data = json.loads(match.group(1).strip())
                except json.JSONDecodeError:
                    return None
            else:
                return None

        return ClassificationResult(
            classification=data.get("classification", "neutral"),
            confidence=float(data.get("confidence", 0.0)),
            excerpt=data.get("excerpt", ""),
        )

    @staticmethod
    def _parse_hint(response: str) -> Optional[Hint]:
        """Parse Layer 2 JSON response into Hint."""
        try:
            data = json.loads(response.strip())
        except json.JSONDecodeError:
            import re
            match = re.search(r"```(?:json)?\s*(.*?)```", response, re.DOTALL)
            if match:
                try:
                    data = json.loads(match.group(1).strip())
                except json.JSONDecodeError:
                    return None
            else:
                return None

        hint_type_str = data.get("type", "tactical")
        priority_str = data.get("priority", "medium")

        try:
            hint_type = HintType(hint_type_str)
        except ValueError:
            hint_type = HintType.TACTICAL

        try:
            priority = HintPriority(priority_str)
        except ValueError:
            priority = HintPriority.MEDIUM

        return Hint(
            type=hint_type,
            text=data.get("text", ""),
            priority=priority,
            rationale=data.get("rationale", ""),
        )
