import pytest
import pytest_asyncio

from gigaam_transcriber.template_manager import (
    TemplateManager,
    _slugify,
    _label_without_emoji,
)
from gigaam_transcriber.database import engine, async_session_factory, Base

BUILTIN = {
    "meeting": {"label": "📝 Встреча", "system_prompt": "meeting prompt"},
    "general": {"label": "📄 Общий", "system_prompt": "general prompt"},
}


@pytest_asyncio.fixture
async def db_session():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_session_factory() as session:
        yield session
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest.fixture
def user_id() -> str:
    return "test-user-1"


@pytest.fixture
def other_user_id() -> str:
    return "test-user-2"



class TestSlugify:
    def test_cyrillic(self):
        assert _slugify("Юридический") == "yuridicheskiy"

    def test_emoji_stripped(self):
        assert _slugify("⚖️ Юридический") == "yuridicheskiy"

    def test_spaces_to_dashes(self):
        assert _slugify("My Template") == "my-template"

    def test_empty(self):
        assert _slugify("") == "untitled"


class TestLabelWithoutEmoji:
    def test_removes_emoji(self):
        assert _label_without_emoji("⚖️ Юридический") == "Юридический"

    def test_no_emoji(self):
        assert _label_without_emoji("General") == "General"



class TestCreateTemplate:
    @pytest.mark.asyncio
    async def test_basic(self, db_session, user_id):
        t = await TemplateManager.create_template(
            db_session, user_id, "📝 Legal", "You are a lawyer"
        )
        assert t["label"] == "📝 Legal"
        assert t["key"] == "custom-legal"
        assert t["id"]
        assert t["system_prompt"] == "You are a lawyer"

    @pytest.mark.asyncio
    async def test_with_all_fields(self, db_session, user_id):
        t = await TemplateManager.create_template(
            db_session, user_id,
            label="🔬 Research",
            system_prompt="Analyze research",
            emoji="🔬",
            user_prompt_template="Analyze: {{text}}",
        )
        assert t["emoji"] == "🔬"
        assert t["user_prompt_template"] == "Analyze: {{text}}"

    @pytest.mark.asyncio
    async def test_empty_label(self, db_session, user_id):
        with pytest.raises(ValueError, match="Название"):
            await TemplateManager.create_template(db_session, user_id, "", "prompt")

    @pytest.mark.asyncio
    async def test_empty_prompt(self, db_session, user_id):
        with pytest.raises(ValueError, match="промпт"):
            await TemplateManager.create_template(db_session, user_id, "Label", "")

    @pytest.mark.asyncio
    async def test_duplicate_label(self, db_session, user_id):
        await TemplateManager.create_template(db_session, user_id, "Test", "prompt1")
        with pytest.raises(ValueError, match="уже существует"):
            await TemplateManager.create_template(db_session, user_id, "Test", "prompt2")

    @pytest.mark.asyncio
    async def test_duplicate_label_different_emoji(self, db_session, user_id):
        await TemplateManager.create_template(db_session, user_id, "📝 Test", "prompt1")
        with pytest.raises(ValueError, match="уже существует"):
            await TemplateManager.create_template(db_session, user_id, "🔧 Test", "prompt2")

    @pytest.mark.asyncio
    async def test_key_collision(self, db_session, user_id):
        await TemplateManager.create_template(db_session, user_id, "My Template", "p1")
        t2 = await TemplateManager.create_template(db_session, user_id, "My-Template", "p2")
        assert t2["key"] == "custom-my-template-2"


class TestListTemplates:
    @pytest.mark.asyncio
    async def test_empty(self, db_session, user_id):
        result = await TemplateManager.list_templates(db_session, user_id)
        assert result == []

    @pytest.mark.asyncio
    async def test_returns_created(self, db_session, user_id):
        await TemplateManager.create_template(db_session, user_id, "A", "p1")
        await TemplateManager.create_template(db_session, user_id, "B", "p2")
        result = await TemplateManager.list_templates(db_session, user_id)
        assert len(result) == 2
        labels = {t["label"] for t in result}
        assert labels == {"A", "B"}


class TestGetTemplate:
    @pytest.mark.asyncio
    async def test_existing(self, db_session, user_id):
        created = await TemplateManager.create_template(db_session, user_id, "Legal", "prompt")
        fetched = await TemplateManager.get_template(db_session, user_id, created["key"])
        assert fetched is not None
        assert fetched["label"] == "Legal"

    @pytest.mark.asyncio
    async def test_nonexistent(self, db_session, user_id):
        result = await TemplateManager.get_template(db_session, user_id, "custom-nope")
        assert result is None


class TestUpdateTemplate:
    @pytest.mark.asyncio
    async def test_basic(self, db_session, user_id):
        created = await TemplateManager.create_template(db_session, user_id, "Old", "old prompt")
        updated = await TemplateManager.update_template(
            db_session, user_id, created["key"],
            label="New", system_prompt="new prompt",
        )
        assert updated["label"] == "New"
        assert updated["system_prompt"] == "new prompt"

    @pytest.mark.asyncio
    async def test_not_found(self, db_session, user_id):
        with pytest.raises(ValueError, match="не найден"):
            await TemplateManager.update_template(
                db_session, user_id, "custom-nonexistent",
                label="L", system_prompt="P",
            )

    @pytest.mark.asyncio
    async def test_empty_label(self, db_session, user_id):
        created = await TemplateManager.create_template(db_session, user_id, "L", "P")
        with pytest.raises(ValueError, match="Название"):
            await TemplateManager.update_template(
                db_session, user_id, created["key"],
                label="", system_prompt="P",
            )

    @pytest.mark.asyncio
    async def test_duplicate_label(self, db_session, user_id):
        await TemplateManager.create_template(db_session, user_id, "A", "p1")
        t2 = await TemplateManager.create_template(db_session, user_id, "B", "p2")
        with pytest.raises(ValueError, match="уже существует"):
            await TemplateManager.update_template(
                db_session, user_id, t2["key"],
                label="A", system_prompt="p3",
            )


class TestDeleteTemplate:
    @pytest.mark.asyncio
    async def test_basic(self, db_session, user_id):
        created = await TemplateManager.create_template(db_session, user_id, "Del", "p")
        await TemplateManager.delete_template(db_session, user_id, created["key"])
        result = await TemplateManager.list_templates(db_session, user_id)
        assert result == []

    @pytest.mark.asyncio
    async def test_not_found(self, db_session, user_id):
        with pytest.raises(ValueError, match="не найден"):
            await TemplateManager.delete_template(db_session, user_id, "custom-nonexistent")


class TestGetAllTemplates:
    @pytest.mark.asyncio
    async def test_merges_builtins_and_custom(self, db_session, user_id):
        await TemplateManager.create_template(db_session, user_id, "Custom", "custom prompt")
        all_t = await TemplateManager.get_all_templates(db_session, user_id, BUILTIN)
        assert "meeting" in all_t
        assert "general" in all_t
        custom_keys = [k for k in all_t if k.startswith("custom-")]
        assert len(custom_keys) == 1

    @pytest.mark.asyncio
    async def test_only_builtins(self, db_session, user_id):
        all_t = await TemplateManager.get_all_templates(db_session, user_id, BUILTIN)
        assert set(all_t.keys()) == set(BUILTIN.keys())


class TestGetTemplateChoices:
    @pytest.mark.asyncio
    async def test_choices_sorted(self, db_session, user_id):
        await TemplateManager.create_template(db_session, user_id, "ААА", "p")
        choices = await TemplateManager.get_template_choices(db_session, user_id, BUILTIN)
        labels = [c[0] for c in choices]
        assert labels == sorted(labels, key=lambda x: x.lower())


class TestPerUserIsolation:
    @pytest.mark.asyncio
    async def test_user_cannot_see_others_templates(self, db_session, user_id, other_user_id):
        await TemplateManager.create_template(db_session, user_id, "User1 Template", "p1")
        result = await TemplateManager.list_templates(db_session, other_user_id)
        assert result == []

    @pytest.mark.asyncio
    async def test_user_cannot_get_others_template(self, db_session, user_id, other_user_id):
        created = await TemplateManager.create_template(db_session, user_id, "Secret", "p")
        result = await TemplateManager.get_template(db_session, other_user_id, created["key"])
        assert result is None

    @pytest.mark.asyncio
    async def test_user_cannot_update_others_template(self, db_session, user_id, other_user_id):
        created = await TemplateManager.create_template(db_session, user_id, "Owned", "p")
        with pytest.raises(ValueError, match="не найден"):
            await TemplateManager.update_template(
                db_session, other_user_id, created["key"],
                label="Hacked", system_prompt="hacked",
            )

    @pytest.mark.asyncio
    async def test_user_cannot_delete_others_template(self, db_session, user_id, other_user_id):
        created = await TemplateManager.create_template(db_session, user_id, "Owned", "p")
        with pytest.raises(ValueError, match="не найден"):
            await TemplateManager.delete_template(
                db_session, other_user_id, created["key"],
            )

    @pytest.mark.asyncio
    async def test_same_label_different_users(self, db_session, user_id, other_user_id):
        t1 = await TemplateManager.create_template(db_session, user_id, "Shared Label", "p1")
        t2 = await TemplateManager.create_template(db_session, other_user_id, "Shared Label", "p2")
        assert t1["key"] == t2["key"]
        u1 = await TemplateManager.list_templates(db_session, user_id)
        u2 = await TemplateManager.list_templates(db_session, other_user_id)
        assert len(u1) == 1
        assert len(u2) == 1
        assert u1[0]["system_prompt"] == "p1"
        assert u2[0]["system_prompt"] == "p2"


class TestExportTemplates:
    @pytest.mark.asyncio
    async def test_export_empty(self, db_session, user_id):
        result = await TemplateManager.export_all_templates(db_session, user_id)
        assert result["version"] == 1
        assert result["app"] == "dialogscribe"
        assert result["exported_at"]
        assert result["templates"] == []

    @pytest.mark.asyncio
    async def test_export_with_templates(self, db_session, user_id):
        await TemplateManager.create_template(
            db_session, user_id, "📝 Test", "prompt1",
            emoji="📝", user_prompt_template="upt",
        )
        await TemplateManager.create_template(db_session, user_id, "Another", "prompt2")

        result = await TemplateManager.export_all_templates(db_session, user_id)
        assert len(result["templates"]) == 2

        for t in result["templates"]:
            assert "id" not in t
            assert "key" not in t
            assert "label" in t
            assert "system_prompt" in t

    @pytest.mark.asyncio
    async def test_export_strips_id_and_key(self, db_session, user_id):
        await TemplateManager.create_template(db_session, user_id, "Legal", "You are a lawyer")
        exported = await TemplateManager.export_all_templates(db_session, user_id)
        t = exported["templates"][0]
        assert t["label"] == "Legal"
        assert t["system_prompt"] == "You are a lawyer"
        assert "id" not in t
        assert "key" not in t

    @pytest.mark.asyncio
    async def test_export_single(self, db_session, user_id):
        created = await TemplateManager.create_template(
            db_session, user_id, "Single", "prompt", emoji="📌",
        )
        result = await TemplateManager.export_template(db_session, user_id, created["key"])
        assert result is not None
        assert result["version"] == 1
        assert len(result["templates"]) == 1
        assert result["templates"][0]["label"] == "Single"

    @pytest.mark.asyncio
    async def test_export_single_not_found(self, db_session, user_id):
        result = await TemplateManager.export_template(db_session, user_id, "custom-nope")
        assert result is None


class TestImportTemplates:
    @pytest.mark.asyncio
    async def test_import_valid(self, db_session, user_id):
        data = {
            "version": 1,
            "templates": [
                {"label": "Imported", "system_prompt": "test prompt", "emoji": "📝"},
            ],
        }
        report = await TemplateManager.import_templates(db_session, user_id, data)
        assert report["imported"] == 1
        assert report["skipped"] == 0
        assert report["errors"] == []
        loaded = await TemplateManager.list_templates(db_session, user_id)
        assert len(loaded) == 1

    @pytest.mark.asyncio
    async def test_import_skips_duplicates(self, db_session, user_id):
        await TemplateManager.create_template(db_session, user_id, "Existing", "old prompt")
        data = {
            "templates": [
                {"label": "Existing", "system_prompt": "new prompt"},
            ],
        }
        report = await TemplateManager.import_templates(db_session, user_id, data)
        assert report["imported"] == 0
        assert report["skipped"] == 1

    @pytest.mark.asyncio
    async def test_import_invalid_fields(self, db_session, user_id):
        data = {
            "templates": [
                {"label": "", "system_prompt": "prompt"},
                {"label": "NoPrompt", "system_prompt": ""},
            ],
        }
        report = await TemplateManager.import_templates(db_session, user_id, data)
        assert report["imported"] == 0
        assert len(report["errors"]) == 2

    @pytest.mark.asyncio
    async def test_import_empty_array(self, db_session, user_id):
        report = await TemplateManager.import_templates(db_session, user_id, {"templates": []})
        assert report["imported"] == 0
        assert report["skipped"] == 0

    @pytest.mark.asyncio
    async def test_import_malformed_input(self, db_session, user_id):
        report = await TemplateManager.import_templates(db_session, user_id, {})
        assert len(report["errors"]) == 1

        report = await TemplateManager.import_templates(db_session, user_id, {"templates": "not a list"})
        assert len(report["errors"]) == 1

    @pytest.mark.asyncio
    async def test_import_accepts_name_as_label(self, db_session, user_id):
        data = {
            "templates": [
                {"name": "Via Name", "system_prompt": "prompt"},
            ],
        }
        report = await TemplateManager.import_templates(db_session, user_id, data)
        assert report["imported"] == 1

    @pytest.mark.asyncio
    async def test_import_mixed_valid_invalid(self, db_session, user_id):
        data = {
            "templates": [
                {"label": "Good", "system_prompt": "prompt"},
                {"label": "", "system_prompt": "prompt"},
                {"label": "Also Good", "system_prompt": "prompt2"},
            ],
        }
        report = await TemplateManager.import_templates(db_session, user_id, data)
        assert report["imported"] == 2
        assert len(report["errors"]) == 1
