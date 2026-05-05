"""Meeting Brief models for structured meeting context."""

import enum
from typing import Optional

from pydantic import BaseModel, Field


class MeetingGoal(str, enum.Enum):
    """Goal of the meeting — used in Meeting Brief."""
    INITIAL = "initial"              # Первичная встреча
    PRICE_NEGOTIATION = "price"      # Переговоры о цене
    PRESENTATION = "presentation"    # Презентация
    DEAL_FINALIZATION = "finalization"  # Финализация сделки
    OTHER = "other"                  # Другое


class MeetingBrief(BaseModel):
    """Structured meeting context entered by user before/during session."""
    goal: Optional[MeetingGoal] = None
    offering: str = ""           # What we are offering to the client
    red_lines: str = ""          # Boundaries we will not cross
    known_objections: str = ""   # Known client objections or concerns

    def to_prompt_text(self) -> str:
        """Format brief as text for inclusion in LLM prompt."""
        parts = []
        if self.goal is not None:
            parts.append(f"Цель встречи: {self.goal.value}")
        if self.offering:
            parts.append(f"Наше предложение: {self.offering}")
        if self.red_lines:
            parts.append(f"Красные линии (не соглашаемся): {self.red_lines}")
        if self.known_objections:
            parts.append(f"Известные возражения клиента: {self.known_objections}")
        return "\n".join(parts)


class BriefUpdateMessage(BaseModel):
    """WebSocket message for updating meeting brief during session."""
    type: str = "brief_update"
    goal: Optional[str] = None
    offering: Optional[str] = None
    red_lines: Optional[str] = None
    known_objections: Optional[str] = None

    def apply_to(self, brief: MeetingBrief) -> MeetingBrief:
        """Merge updates into existing brief, only overwriting non-None fields."""
        if self.goal is not None:
            brief.goal = MeetingGoal(self.goal)
        if self.offering is not None:
            brief.offering = self.offering
        if self.red_lines is not None:
            brief.red_lines = self.red_lines
        if self.known_objections is not None:
            brief.known_objections = self.known_objections
        return brief
