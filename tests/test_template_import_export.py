import json
import pytest
import pytest_asyncio

from gigaam_transcriber.template_manager import TemplateManager
from gigaam_transcriber.database import engine, async_session_factory, Base


@pytest_asyncio.fixture
async def db_session():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_session_factory() as session:
        yield session
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


USER_ID = "test-user-import-export"


class TestExportImportCycle:
    @pytest.mark.asyncio
    async def test_full_cycle(self, db_session):
        await TemplateManager.create_template(
            db_session, USER_ID, "Meeting", "You summarize meetings",
            emoji="📝", user_prompt_template="Summarize: {{text}}",
        )
        await TemplateManager.create_template(
            db_session, USER_ID, "Legal", "You are a lawyer", emoji="⚖️",
        )

        exported = await TemplateManager.export_all_templates(db_session, USER_ID)
        assert exported["version"] == 1
        assert exported["app"] == "dialogscribe"
        assert exported["exported_at"]
        assert len(exported["templates"]) == 2

        for t in exported["templates"]:
            assert "id" not in t
            assert "key" not in t

        json_str = json.dumps(exported)
        parsed = json.loads(json_str)

        other_user = "test-user-import-2"
        report = await TemplateManager.import_templates(db_session, other_user, parsed)
        assert report["imported"] == 2
        assert report["skipped"] == 0
        assert report["errors"] == []

        loaded = await TemplateManager.list_templates(db_session, other_user)
        labels = {t["label"] for t in loaded}
        assert "Meeting" in labels
        assert "Legal" in labels

    @pytest.mark.asyncio
    async def test_exported_json_structure(self, db_session):
        await TemplateManager.create_template(
            db_session, USER_ID, "Test", "prompt",
            emoji="📝", user_prompt_template="upt",
        )

        exported = await TemplateManager.export_all_templates(db_session, USER_ID)
        t = exported["templates"][0]
        assert set(t.keys()) == {"label", "emoji", "system_prompt", "user_prompt_template"}
        assert t["label"] == "Test"
        assert t["system_prompt"] == "prompt"
        assert t["emoji"] == "📝"
        assert t["user_prompt_template"] == "upt"

    @pytest.mark.asyncio
    async def test_import_into_existing(self, db_session):
        await TemplateManager.create_template(db_session, USER_ID, "Existing", "old")
        data = {
            "templates": [
                {"label": "Existing", "system_prompt": "new"},
                {"label": "New", "system_prompt": "brand new"},
            ],
        }
        report = await TemplateManager.import_templates(db_session, USER_ID, data)
        assert report["imported"] == 1
        assert report["skipped"] == 1

    @pytest.mark.asyncio
    async def test_single_template_export(self, db_session):
        created = await TemplateManager.create_template(
            db_session, USER_ID, "Solo", "solo prompt", emoji="🎯",
        )
        result = await TemplateManager.export_template(db_session, USER_ID, created["key"])
        assert result is not None
        assert result["version"] == 1
        assert len(result["templates"]) == 1
        assert result["templates"][0]["label"] == "Solo"
        assert "id" not in result["templates"][0]
        assert "key" not in result["templates"][0]

    @pytest.mark.asyncio
    async def test_round_trip_preserves_data(self, db_session):
        await TemplateManager.create_template(
            db_session, USER_ID, "RT", "rt prompt",
            emoji="🔄", user_prompt_template="rt: {{text}}",
        )
        exported = await TemplateManager.export_all_templates(db_session, USER_ID)
        await TemplateManager.delete_template(
            db_session, USER_ID,
            (await TemplateManager.list_templates(db_session, USER_ID))[0]["key"],
        )

        other_user = "test-user-rt"
        report = await TemplateManager.import_templates(db_session, other_user, exported)
        assert report["imported"] == 1

        loaded = await TemplateManager.list_templates(db_session, other_user)
        assert len(loaded) == 1
        t = loaded[0]
        assert t["label"] == "RT"
        assert t["system_prompt"] == "rt prompt"
        assert t["emoji"] == "🔄"
        assert t["user_prompt_template"] == "rt: {{text}}"
