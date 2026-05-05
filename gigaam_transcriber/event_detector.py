"""Event Detector for Live Advisor Agent.

Detects events in the transcript stream that should trigger hint generation:
- Speech pauses (>2 seconds)
- Keyword matches (price, objection, competitor terms)
- Timer fallback (configurable interval)
- Event deduplication
"""

import re
import time
import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


# Default keyword lists for trigger detection
DEFAULT_PRICE_KEYWORDS = [
    r"цен[аеуы]", r"стоимость[ью]?", r"бюджет", r"скидк[аеиу]",
    r"дорого", r"дешев[аоы]", r"тариф", r"сумм[аеуы]",
    r"оплат[аеуы]", r"рассрочк[аеуы]", r"рубл[еяю]",
    r"\d+\s*(тыс|млн|миллион|тысяч)",
]

DEFAULT_OBJECTION_KEYWORDS = [
    r"подум[аеюы]", r"не подход[а-я]+", r"слишком", r"дорого",
    r"не уверен[а-я]*", r"сомнев[а-я]+", r"рискованн",
    r"не готовы", r"отлож[а-я]+", r"позже", r"потом",
    r"не устраив[а-я]+", r"выгоднее", r"у других (дешевле|лучше|быстрее)",
]

DEFAULT_COMPETITOR_KEYWORDS = [
    r"конкурент", r"у других", r"другой поставщик", r"альтернатив[аеуы]",
    r"ваш конкурент", r"другое предложение",
]


@dataclass
class TriggerEvent:
    """An event detected by the EventDetector that may trigger hint generation."""
    event_type: str  # "speech_pause", "keyword_match", "timer"
    transcript_fragment: str
    matched_keyword: Optional[str] = None
    keyword_category: Optional[str] = None  # "price", "objection", "competitor"
    timestamp: float = field(default_factory=time.time)


class EventDetector:
    """Detects events in transcript stream for triggering hint generation.

    Features:
    - Speech pause detection (>2 seconds silence after speech)
    - Keyword trigger detection (price/objection/competitor terms)
    - Timer fallback (configurable interval)
    - Event deduplication (within 3-second window)
    """

    def __init__(
        self,
        pause_threshold_seconds: float = 2.0,
        timer_interval_seconds: float = 10.0,
        custom_keywords: Optional[list[str]] = None,
    ):
        self.pause_threshold = pause_threshold_seconds
        self.timer_interval = timer_interval_seconds

        # Compile keyword regex patterns
        self._price_patterns = [re.compile(p, re.IGNORECASE) for p in DEFAULT_PRICE_KEYWORDS]
        self._objection_patterns = [re.compile(p, re.IGNORECASE) for p in DEFAULT_OBJECTION_KEYWORDS]
        self._competitor_patterns = [re.compile(p, re.IGNORECASE) for p in DEFAULT_COMPETITOR_KEYWORDS]
        self._custom_patterns = []
        if custom_keywords:
            self._custom_patterns = [re.compile(p, re.IGNORECASE) for p in custom_keywords]

        # State tracking
        self._last_speech_ts: float = 0.0
        self._last_event_ts: float = 0.0
        self._last_transcript_pos: int = 0
        self._last_timer_check_ts: float = time.time()
        self._speech_active: bool = False

    def process_transcript_chunk(self, text: str) -> list[TriggerEvent]:
        """Process a new transcript chunk and return any triggered events.

        This is the main entry point. It checks for keyword matches and
        updates speech tracking state.
        """
        events = []

        if not text or not text.strip():
            return events

        # Mark speech as active
        now = time.time()
        self._speech_active = True
        self._last_speech_ts = now

        # Check keywords (only on new content)
        keyword_event = self.check_keywords(text)
        if keyword_event:
            events.append(keyword_event)

        return events

    def check_pause(self, current_time: Optional[float] = None) -> Optional[TriggerEvent]:
        """Check if a speech pause has occurred (>threshold seconds since last speech).

        Should be called periodically (e.g., every 0.5s) from the main loop.
        """
        now = current_time or time.time()

        if not self._speech_active:
            return None

        silence_duration = now - self._last_speech_ts
        if silence_duration >= self.pause_threshold and self._last_speech_ts > 0:
            # Pause detected
            self._speech_active = False
            event = TriggerEvent(
                event_type="speech_pause",
                transcript_fragment=f"[pause {silence_duration:.1f}s]",
                timestamp=now,
            )
            return event

        return None

    def check_keywords(self, text: str) -> Optional[TriggerEvent]:
        """Check text for trigger keywords. Returns event if match found."""
        if not text or not text.strip():
            return None

        # Check price keywords
        for pattern in self._price_patterns:
            match = pattern.search(text)
            if match:
                return TriggerEvent(
                    event_type="keyword_match",
                    transcript_fragment=text,
                    matched_keyword=match.group(),
                    keyword_category="price",
                )

        # Check objection keywords
        for pattern in self._objection_patterns:
            match = pattern.search(text)
            if match:
                return TriggerEvent(
                    event_type="keyword_match",
                    transcript_fragment=text,
                    matched_keyword=match.group(),
                    keyword_category="objection",
                )

        # Check competitor keywords
        for pattern in self._competitor_patterns:
            match = pattern.search(text)
            if match:
                return TriggerEvent(
                    event_type="keyword_match",
                    transcript_fragment=text,
                    matched_keyword=match.group(),
                    keyword_category="competitor",
                )

        # Check custom keywords
        for pattern in self._custom_patterns:
            match = pattern.search(text)
            if match:
                return TriggerEvent(
                    event_type="keyword_match",
                    transcript_fragment=text,
                    matched_keyword=match.group(),
                    keyword_category="custom",
                )

        return None

    def should_trigger_timer(self, current_time: Optional[float] = None) -> bool:
        """Check if the timer fallback should trigger.

        Returns True if timer_interval has passed since last event or timer check.
        """
        now = current_time or time.time()

        if now - self._last_timer_check_ts >= self.timer_interval:
            self._last_timer_check_ts = now
            return True
        return False

    def should_trigger(self, event: TriggerEvent) -> bool:
        """Deduplication check: should this event actually trigger processing?

        Returns False if a similar event was processed within the last 3 seconds.
        """
        now = time.time()
        dedup_window = 3.0

        if now - self._last_event_ts < dedup_window:
            logger.debug(f"Deduplicating event {event.event_type} within {dedup_window}s window")
            return False

        return True

    def mark_event_processed(self) -> None:
        """Mark that an event was processed (for deduplication tracking)."""
        self._last_event_ts = time.time()

    def reset_timer(self) -> None:
        """Reset the timer (called when another event type fires)."""
        self._last_timer_check_ts = time.time()

    def create_timer_event(self, transcript_text: str) -> TriggerEvent:
        """Create a timer fallback event."""
        return TriggerEvent(
            event_type="timer",
            transcript_fragment=transcript_text,
            timestamp=time.time(),
        )
