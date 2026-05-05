import io
import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from gigaam_transcriber.auth import get_current_user
from gigaam_transcriber.database import get_db
from gigaam_transcriber.models import User
from gigaam_transcriber.summarizer import SUMMARY_TEMPLATES
from gigaam_transcriber.template_manager import TemplateManager
from routers._helpers import logger

router = APIRouter(prefix="/api", tags=["templates"])


class TemplateCreate(BaseModel):
    name: str
    emoji: str = ""
    system_prompt: str
    user_prompt_template: str = ""


class TemplateUpdate(BaseModel):
    name: str | None = None
    emoji: str | None = None
    system_prompt: str | None = None
    user_prompt_template: str | None = None


def _template_to_dict(key: str, label: str, system_prompt: str, is_custom: bool, emoji: str = "", user_prompt_template: str = "") -> dict[str, object]:
    return {
        "slug": key,
        "name": label,
        "emoji": emoji,
        "system_prompt": system_prompt,
        "user_prompt_template": user_prompt_template,
        "is_custom": is_custom,
    }


@router.get("/templates")
async def list_templates(_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = []
    for key, val in SUMMARY_TEMPLATES.items():
        result.append(
            _template_to_dict(key, val["label"], val["system_prompt"], is_custom=False)
        )
    custom = await TemplateManager.list_templates(db, _user.id)
    for t in custom:
        result.append(
            _template_to_dict(t["key"], t["label"], t["system_prompt"], is_custom=True,
                              emoji=t.get("emoji", ""), user_prompt_template=t.get("user_prompt_template", ""))
        )
    return result


@router.post("/templates", status_code=201)
async def create_template(body: TemplateCreate, _user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    try:
        created = await TemplateManager.create_template(db, _user.id, body.name, body.system_prompt,
                                                        emoji=body.emoji, user_prompt_template=body.user_prompt_template)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    await db.commit()
    return _template_to_dict(created["key"], created["label"], created["system_prompt"], is_custom=True,
                             emoji=created.get("emoji", ""), user_prompt_template=created.get("user_prompt_template", ""))


@router.put("/templates/{slug}")
async def update_template(slug: str, body: TemplateUpdate, _user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    if slug in SUMMARY_TEMPLATES:
        raise HTTPException(status_code=403, detail="Cannot modify built-in template")

    existing = await TemplateManager.get_template(db, _user.id, slug)
    if not existing:
        raise HTTPException(status_code=404, detail="Template not found")

    try:
        updated = await TemplateManager.update_template(db, _user.id, slug,
                                                        label=body.name, system_prompt=body.system_prompt,
                                                        emoji=body.emoji, user_prompt_template=body.user_prompt_template)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    await db.commit()
    return _template_to_dict(updated["key"], updated["label"], updated["system_prompt"], is_custom=True,
                             emoji=updated.get("emoji", ""), user_prompt_template=updated.get("user_prompt_template", ""))


@router.delete("/templates/{slug}")
async def delete_template(slug: str, _user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    if slug in SUMMARY_TEMPLATES:
        raise HTTPException(status_code=403, detail="Cannot delete built-in template")

    try:
        await TemplateManager.delete_template(db, _user.id, slug)
    except ValueError:
        raise HTTPException(status_code=404, detail="Template not found")
    await db.commit()
    return {"detail": "Template deleted"}


class ImportRequest(BaseModel):
    templates_data: dict[str, object]


@router.post("/templates/export")
async def export_templates(_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    data = await TemplateManager.export_all_templates(db, _user.id)
    return data


@router.post("/templates/export/{slug}")
async def export_single_template(slug: str, _user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    if slug in SUMMARY_TEMPLATES:
        raise HTTPException(status_code=403, detail="Cannot export built-in template")
    data = await TemplateManager.export_template(db, _user.id, slug)
    if data is None:
        raise HTTPException(status_code=404, detail="Template not found")
    filename = f"dialogscribe-template-{slug}.json"
    buf = io.BytesIO(json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8"))
    return StreamingResponse(buf, media_type="application/json",
                             headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.post("/templates/import")
async def import_templates(body: ImportRequest, _user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await TemplateManager.import_templates(db, _user.id, body.templates_data)
    await db.commit()
    return result
