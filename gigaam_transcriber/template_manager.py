"""
Управление кастомными шаблонами саммари.

CRUD-операции с асинхронной PostgreSQL.
"""

import logging
import re
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gigaam_transcriber.models import Template

logger = logging.getLogger(__name__)

_CYRILLIC_MAP = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "yo",
    "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "shch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
    "ий": "iy",
}


def _slugify(text: str) -> str:
    lower = text.lower().strip()
    result = []
    for ch in lower:
        if ch in _CYRILLIC_MAP:
            result.append(_CYRILLIC_MAP[ch])
        elif ch.isalnum() or ch in "-_":
            result.append(ch)
        elif ch.isspace():
            result.append("-")
    slug = "".join(result).strip("-")
    slug = re.sub(r"-+", "-", slug)
    return slug or "untitled"


def _label_without_emoji(label: str) -> str:
    return re.sub(
        r"[\U0001F300-\U0001F9FF\U00002600-\U000027BF\U0000FE00-\U0000FE0F\U0001FA00-\U0001FA6F\U0001FA70-\U0001FAFF\u200D\ufe0f]",
        "", label,
    ).strip()


def _template_to_dict(t: Template) -> dict:
    """Convert Template ORM object to dict."""
    return {
        "id": t.id,
        "key": t.key,
        "label": t.label,
        "emoji": t.emoji or "",
        "system_prompt": t.system_prompt,
        "user_prompt_template": t.user_prompt_template or "",
        "created_at": t.created_at.isoformat() if t.created_at else None,
    }


class TemplateManager:
    """Async DB-backed template manager with per-user scoping."""

    @staticmethod
    async def list_templates(db: AsyncSession, user_id: str) -> list[dict]:
        """List all custom templates for a user."""
        result = await db.execute(
            select(Template).where(Template.user_id == user_id).order_by(Template.created_at)
        )
        templates = result.scalars().all()
        return [_template_to_dict(t) for t in templates]

    @staticmethod
    async def get_template(db: AsyncSession, user_id: str, key: str) -> dict | None:
        """Get a single template by user_id and key."""
        result = await db.execute(
            select(Template).where(Template.user_id == user_id, Template.key == key)
        )
        t = result.scalar_one_or_none()
        if t is None:
            return None
        return _template_to_dict(t)

    @staticmethod
    async def create_template(db: AsyncSession, user_id: str, label: str, system_prompt: str, emoji: str = "", user_prompt_template: str = "") -> dict:
        """Create a new custom template."""
        clean_label = label.strip()
        clean_prompt = system_prompt.strip()
        clean_emoji = emoji.strip()
        clean_user_prompt = user_prompt_template.strip()
        if not clean_label:
            raise ValueError("Название шаблона обязательно")
        if not clean_prompt:
            raise ValueError("Системный промпт обязателен")

        # Check for duplicate label
        plain_label = _label_without_emoji(clean_label)
        result = await db.execute(
            select(Template).where(Template.user_id == user_id)
        )
        existing = result.scalars().all()
        if any(_label_without_emoji(t.label).lower() == plain_label.lower() for t in existing):
            raise ValueError("Шаблон с таким названием уже существует")

        # Generate unique key
        slug = _slugify(plain_label)
        key = f"custom-{slug}"
        existing_keys = {t.key for t in existing}
        if key in existing_keys:
            suffix = 2
            while f"custom-{slug}-{suffix}" in existing_keys:
                suffix += 1
            key = f"custom-{slug}-{suffix}"

        template = Template(
            user_id=user_id,
            key=key,
            label=clean_label,
            emoji=clean_emoji,
            system_prompt=clean_prompt,
            user_prompt_template=clean_user_prompt,
        )
        db.add(template)
        await db.flush()
        return _template_to_dict(template)

    @staticmethod
    async def update_template(db: AsyncSession, user_id: str, key: str, label: str | None = None, system_prompt: str | None = None, emoji: str | None = None, user_prompt_template: str | None = None) -> dict:
        """Update an existing template."""
        result = await db.execute(
            select(Template).where(Template.user_id == user_id, Template.key == key)
        )
        template = result.scalar_one_or_none()
        if template is None:
            raise ValueError("Шаблон не найден")

        if label is not None:
            clean_label = label.strip()
            if not clean_label:
                raise ValueError("Название шаблона обязательно")
            # Check for duplicate label (excluding self)
            plain_label = _label_without_emoji(clean_label)
            all_result = await db.execute(select(Template).where(Template.user_id == user_id))
            for t in all_result.scalars().all():
                if t.key != key and _label_without_emoji(t.label).lower() == plain_label.lower():
                    raise ValueError("Шаблон с таким названием уже существует")
            template.label = clean_label

        if system_prompt is not None:
            clean_prompt = system_prompt.strip()
            if not clean_prompt:
                raise ValueError("Системный промпт обязателен")
            template.system_prompt = clean_prompt

        if emoji is not None:
            template.emoji = emoji.strip()
        if user_prompt_template is not None:
            template.user_prompt_template = user_prompt_template.strip()

        await db.flush()
        return _template_to_dict(template)

    @staticmethod
    async def delete_template(db: AsyncSession, user_id: str, key: str) -> None:
        """Delete a template by key."""
        result = await db.execute(
            select(Template).where(Template.user_id == user_id, Template.key == key)
        )
        template = result.scalar_one_or_none()
        if template is None:
            raise ValueError("Шаблон не найден")
        await db.delete(template)
        await db.flush()

    @staticmethod
    async def get_all_templates(db: AsyncSession, user_id: str, builtin_templates: dict) -> dict[str, dict[str, str]]:
        """Merge builtin templates with user's custom templates."""
        merged = {}
        for key, val in builtin_templates.items():
            merged[key] = {"label": val["label"], "system_prompt": val["system_prompt"]}
        result = await db.execute(
            select(Template).where(Template.user_id == user_id)
        )
        for t in result.scalars().all():
            merged[t.key] = {"label": t.label, "system_prompt": t.system_prompt}
        return merged

    @staticmethod
    async def get_template_choices(db: AsyncSession, user_id: str, builtin_templates: dict) -> list[tuple[str, str]]:
        """Get sorted template choices (label, key) for UI."""
        all_t = await TemplateManager.get_all_templates(db, user_id, builtin_templates)
        return sorted(
            [(t["label"], key) for key, t in all_t.items()],
            key=lambda x: x[0].lower(),
        )

    @staticmethod
    async def export_template(db: AsyncSession, user_id: str, key: str) -> dict:
        """Export a single template as portable dict."""
        result = await db.execute(
            select(Template).where(Template.user_id == user_id, Template.key == key)
        )
        t = result.scalar_one_or_none()
        if t is None:
            return None
        return {
            "version": 1,
            "app": "dialogscribe",
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "templates": [{
                "label": t.label,
                "emoji": t.emoji or "",
                "system_prompt": t.system_prompt,
                "user_prompt_template": t.user_prompt_template or "",
            }],
        }

    @staticmethod
    async def export_all_templates(db: AsyncSession, user_id: str) -> dict:
        """Export all user's custom templates."""
        result = await db.execute(
            select(Template).where(Template.user_id == user_id)
        )
        templates = result.scalars().all()
        exported = []
        for t in templates:
            exported.append({
                "label": t.label,
                "emoji": t.emoji or "",
                "system_prompt": t.system_prompt,
                "user_prompt_template": t.user_prompt_template or "",
            })
        return {
            "version": 1,
            "app": "dialogscribe",
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "templates": exported,
        }

    @staticmethod
    async def import_templates(db: AsyncSession, user_id: str, templates_data: dict) -> dict:
        """Import templates from portable dict. Returns import report."""
        errors: list[str] = []
        imported = 0
        skipped = 0

        if not isinstance(templates_data, dict) or "templates" not in templates_data:
            return {"imported": 0, "skipped": 0, "errors": ["Некорректный формат данных: отсутствует поле templates"]}

        items = templates_data["templates"]
        if not isinstance(items, list):
            return {"imported": 0, "skipped": 0, "errors": ["Поле templates должно быть массивом"]}

        for i, t in enumerate(items):
            if not isinstance(t, dict):
                errors.append(f"Шаблон #{i + 1}: не является объектом")
                continue

            label = (t.get("label") or t.get("name") or "").strip()
            system_prompt = (t.get("system_prompt") or "").strip()
            emoji = (t.get("emoji") or "").strip()
            user_prompt_template = (t.get("user_prompt_template") or "").strip()

            if not label:
                errors.append(f"Шаблон #{i + 1}: отсутствует название (label/name)")
                continue
            if not system_prompt:
                errors.append(f"Шаблон #{i + 1}: отсутствует системный промпт (system_prompt)")
                continue

            # Check for duplicate
            result = await db.execute(select(Template).where(Template.user_id == user_id))
            existing = result.scalars().all()
            plain_label = _label_without_emoji(label)
            if any(_label_without_emoji(e.label).lower() == plain_label.lower() for e in existing):
                skipped += 1
                continue

            try:
                await TemplateManager.create_template(db, user_id, label, system_prompt, emoji=emoji, user_prompt_template=user_prompt_template)
                imported += 1
            except ValueError:
                skipped += 1

        return {"imported": imported, "skipped": skipped, "errors": errors}
