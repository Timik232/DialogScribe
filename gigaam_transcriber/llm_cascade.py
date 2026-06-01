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
DEFAULT_CLASSIFIER_MODEL = os.getenv("LLM_CLASSIFIER_MODEL", "mistral-small-latest")
DEFAULT_ADVISOR_MODEL = os.getenv("LLM_ADVISOR_MODEL", "mistral-small-latest")


# ─── Layer 1: Classification ────────────────────────────────────


CLASSIFIER_SYSTEM_PROMPT = """Ты — классификатор фрагментов B2B-продажного диалога.
Определи, содержит ли фрагмент значимое для менеджера по продажам событие.

Категории:
- "neutral" — рутина, small talk, пустая фраза, обычное изложение без триггера
- "objection" — возражение: дорого, не подходит, подумаем, не готовы, не уверены, у других лучше
- "price_question" — обсуждение цены, бюджета, скидок, условий оплаты, ROI, сроков окупаемости
- "competitor_mention" — упоминание конкурента, альтернативы, сравнение с другим продуктом
- "buying_signal" — клиент спрашивает о деталях внедрения, сроках, демо, следующих шагах, договоре
- "need_expression" — клиент описывает свою проблему, боль, потребность, чего ему не хватает
- "clarification" — запрос уточнения, технический вопрос, сомнение в понимании

Классифицируй щедро: если есть хоть намёк на значимое событие — НЕ neutral.
Приветствия, прощания, «да-да», «понял» без контекста — neutral.

Ответь ТОЛЬКО JSON: {"classification": "<category>", "confidence": 0.0-1.0, "excerpt": "<фраза>"}"""


# ─── Layer 2: Advisory ──────────────────────────────────────────


ADVISOR_SYSTEM_PROMPT = """Ты — live-советник менеджера B2B-продаж. Менеджер сейчас на звонке с клиентом. Дай ОДНУ короткую, конкретную подсказку.

Типы:
- "tactical" — что СКАЗАТЬ или СПРОСИТЬ прямо сейчас (конкретная фраза или вопрос)
- "strategic" — куда ВЕСТИ разговор дальше (переход к теме, техника закрытия)
- "warning" — ОПАСНОСТЬ: необработанное возражение, уход клиента, конкурент, ошибка менеджера
- "analytical" — НАБЛЮДЕНИЕ: клиент проявил интерес, сменил тон, подал покупательский сигнал

Приоритеты:
- "critical" — клиент уходит, сделка под угрозой, нужно действовать СЕЙЧАС
- "high" — возражение, конкурент, покупательский сигнал — важно не упустить
- "medium" — полезный совет, можно учесть
- "low" — фоновое наблюдение

ФОРМАТ подсказки:
- 1-2 предложения максимум. Менеджер читает на ходу — ему некогда
- Для tactical: дай ГОТОВУЮ фразу в кавычках или конкретный вопрос
- Ссылайся на слова клиента из транскрипта

{feedback_bias}

Ответь ТОЛЬКО JSON:
{{"type": "<tactical|strategic|warning|analytical>", "text": "<подсказка>", "priority": "<critical|high|medium|low>", "rationale": "<почему>"}}"""


@dataclass
class ClassificationResult:
    """Result of Layer 1 classification."""
    classification: str
    confidence: float
    excerpt: str

    @property
    def is_significant(self) -> bool:
        """Whether this fragment should trigger Layer 2."""
        return self.classification != "neutral" and self.confidence >= 0.3


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
        self._classifier = self._build_client(classifier_config, DEFAULT_CLASSIFIER_MODEL)

        # Advisor client (strong model)
        self._advisor = self._build_client(advisor_config, DEFAULT_ADVISOR_MODEL)

    @staticmethod
    def _build_client(config: Optional[LLMClientConfig], default_model: str):
        """Build the live-advisor LLM client.

        The Live Advisor deliberately uses the OpenAI-compatible LLMClient
        (e.g. Mistral via LLM_BASE_URL/LLM_MODEL/LLM_API_KEY), NOT GigaChat —
        GigaChat produced poor live hints. Summary/insights/chat stay on the
        global provider via create_llm_client().
        """
        if config:
            return LLMClient(config)
        cfg = LLMClientConfig()
        if default_model:
            cfg.model = default_model
        return LLMClient(cfg)

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
