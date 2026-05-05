"""Session Accumulator — cumulative state for Live Advisor Agent."""

from dataclasses import dataclass, field
from typing import Optional
from difflib import SequenceMatcher

from gigaam_transcriber.hint_typology import Hint, HintType, HintPriority
from gigaam_transcriber.meeting_brief_models import MeetingBrief


@dataclass
class Fact:
    """A fact extracted from the conversation."""
    text: str
    category: str  # entity, decision, commitment, question
    timestamp: float


@dataclass
class HintRecord:
    """Record of a generated hint including user feedback."""
    hint_id: str
    type: HintType
    text: str
    priority: HintPriority
    rationale: str
    timestamp: float
    feedback: Optional[str] = None  # "like" or "dislike"


@dataclass
class SessionAccumulator:
    """Cumulative state of the current meeting session."""
    meeting_brief: MeetingBrief = field(default_factory=MeetingBrief)
    facts: list[Fact] = field(default_factory=list)
    hint_history: list[HintRecord] = field(default_factory=list)
    negotiation_phase: str = "introduction"  # introduction/exploration/negotiation/closing/stalled
    key_topics: list[str] = field(default_factory=list)
    objections_raised: list[str] = field(default_factory=list)
    prices_mentioned: list[str] = field(default_factory=list)
    last_hint_ts: float = 0.0

    # Feedback tracking for biasing
    _liked_types: list[str] = field(default_factory=list)
    _disliked_types: list[str] = field(default_factory=list)

    def add_fact(self, text: str, category: str, timestamp: float) -> None:
        """Add a fact if not duplicate."""
        for f in self.facts:
            if f.text == text and f.category == category:
                return
        self.facts.append(Fact(text=text, category=category, timestamp=timestamp))

    def extract_facts_from_text(self, text: str, timestamp: float) -> None:
        """Extract facts from transcript text using regex patterns."""
        import re

        # Extract price/budget mentions
        price_patterns = [
            r"(?:цена|стоимость|бюджет|сумм[ае]|тариф)\s[^.]*?(?:\d[\d\s]*(?:руб|тыс|млн|USD|EUR|€|\$)[^. ]*)",
            r"\d[\d\s]*(?:руб|тыс|млн|USD|EUR|€|\$)[^. ]*(?:\s*(?:в год|в месяц|за|на))?",
        ]
        for p in price_patterns:
            for match in re.finditer(p, text, re.IGNORECASE):
                self.prices_mentioned.append(match.group().strip())
                self.add_fact(match.group().strip(), "entity", timestamp)

        # Extract decisions/commitments
        decision_patterns = [
            r"(?:договорились|согласн[а-я]*|решили|окей|хорошо|ок|договор)[^.]*",
        ]
        for p in decision_patterns:
            for match in re.finditer(p, text, re.IGNORECASE):
                self.add_fact(match.group().strip(), "decision", timestamp)

        # Extract questions
        if "?" in text:
            sentences = text.split(".")
            for s in sentences:
                if "?" in s.strip():
                    self.add_fact(s.strip(), "question", timestamp)

    def add_hint(self, hint: Hint) -> None:
        """Record a generated hint."""
        record = HintRecord(
            hint_id=hint.hint_id,
            type=hint.type,
            text=hint.text,
            priority=hint.priority,
            rationale=hint.rationale,
            timestamp=hint.timestamp,
        )
        self.hint_history.append(record)
        self.last_hint_ts = hint.timestamp

    def record_feedback(self, hint_id: str, rating: str) -> bool:
        """Record user feedback for a hint. Returns True if hint found."""
        for record in self.hint_history:
            if record.hint_id == hint_id:
                record.feedback = rating
                if rating == "like" and record.type.value not in self._liked_types:
                    self._liked_types.append(record.type.value)
                elif rating == "dislike" and record.type.value not in self._disliked_types:
                    self._disliked_types.append(record.type.value)
                return True
        return False

    def update_phase(self, new_phase: str) -> None:
        """Update negotiation phase."""
        valid = {"introduction", "exploration", "negotiation", "closing", "stalled"}
        if new_phase in valid:
            self.negotiation_phase = new_phase

    def update_phase_from_text(self, text: str) -> None:
        """Update negotiation phase based on conversational signals."""
        import re
        text_lower = text.lower()

        phase_signals = {
            "closing": [r"договорились", r"подпиш[а-я]+", r"договор", r"оформ[а-я]+", r"заверш[а-я]+"],
            "negotiation": [r"цен[аеу]", r"стоимость", r"скидк[ае]", r"бюджет", r"тариф", r"срок[а-я]*", r"услови[а-я]"],
            "exploration": [r"расскаж[а-я]+", r"как\s", r"какой\s", r"что\s", r"какие\s", r"сколько\s", r"предлож[а-я]+"],
            "stalled": [r"не\s*готов[а-я]*", r"подум[а-я]+", r"позже", r"не\s*подход", r"отказ[а-я]*"],
        }

        # Don't regress to earlier phases
        phase_priority = {"introduction": 0, "exploration": 1, "negotiation": 2, "closing": 3, "stalled": 4}
        current_priority = phase_priority.get(self.negotiation_phase, 0)

        for phase, patterns in phase_signals.items():
            if phase_priority.get(phase, 0) <= current_priority and phase != "stalled":
                continue  # Don't regress
            for p in patterns:
                if re.search(p, text_lower):
                    self.update_phase(phase)
                    return

    def add_topic(self, topic: str) -> None:
        """Add a topic to the key topics list if not already present."""
        if topic and topic.lower() not in [t.lower() for t in self.key_topics]:
            self.key_topics.append(topic)

    def update_brief(self, brief: MeetingBrief) -> None:
        """Replace meeting brief."""
        self.meeting_brief = brief

    def get_hint_history(self, limit: int = 5) -> list[HintRecord]:
        """Get most recent hint records."""
        return self.hint_history[-limit:]

    def check_duplicate_hint(self, hint_text: str, window_seconds: float = 180.0) -> bool:
        """Check if a similar hint was generated within the time window (default 3 min).
        Uses simple text similarity (>80% overlap)."""
        import time
        now = time.time()
        for record in reversed(self.hint_history):
            if now - record.timestamp > window_seconds:
                break
            ratio = SequenceMatcher(None, hint_text.lower(), record.text.lower()).ratio()
            if ratio > 0.8:
                return True
        return False

    def get_feedback_bias_text(self) -> str:
        """Generate bias guidance text for Layer 2 prompt based on feedback."""
        parts = []
        if self._liked_types:
            parts.append(f"User found helpful: {', '.join(self._liked_types)} hints. Generate more of these.")
        if self._disliked_types:
            parts.append(f"User did not find helpful: {', '.join(self._disliked_types)} hints. Avoid generating these.")
        return " ".join(parts) if parts else ""

    def get_context_summary(self) -> str:
        """Generate a text summary of accumulated context for LLM prompt."""
        parts = [f"Phase: {self.negotiation_phase}"]
        if self.meeting_brief.offering or self.meeting_brief.red_lines:
            parts.append(f"Meeting Brief:\n{self.meeting_brief.to_prompt_text()}")
        if self.key_topics:
            parts.append(f"Topics discussed: {', '.join(self.key_topics)}")
        if self.objections_raised:
            parts.append(f"Objections raised: {'; '.join(self.objections_raised)}")
        if self.prices_mentioned:
            parts.append(f"Prices/terms mentioned: {'; '.join(self.prices_mentioned)}")
        recent_hints = self.get_hint_history(3)
        if recent_hints:
            hint_texts = [f"- [{h.type.value}] {h.text}" for h in recent_hints]
            parts.append("Recent hints given:\n" + "\n".join(hint_texts))
        bias = self.get_feedback_bias_text()
        if bias:
            parts.append(f"Feedback bias: {bias}")
        return "\n\n".join(parts)
