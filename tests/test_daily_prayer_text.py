import asyncio
import os
import sqlite3
from types import SimpleNamespace

import pytest

os.environ.setdefault("TELEGRAM_OWNER_ID", "1")
os.environ.setdefault("MAX_TOKEN", "test-token")
os.environ.setdefault("OPENAI_KEY", "test-openai-key")
os.environ.setdefault("ANTHROPIC_KEY", "test-anthropic-key")

import vera_bot
import vera_max_bot


DIRTY_CASES = [
    "# Молитва дня - 29 сентября\n\n**Господи, укрепи меня.**\n\n**Аминь.**",
    "# Личное молитвенное обращение...\n\nГосподи, услышь меня.",
    "```markdown\n**Молитва дня**\n\nГосподи, помоги мне \"не ожесточиться\".\n```",
    "«__Молитва дня__\n\nГосподи, сохрани внутреннее \"слово\" во мне.\nАминь.»",
]


@pytest.mark.parametrize("module", [vera_bot, vera_max_bot])
@pytest.mark.parametrize("dirty", DIRTY_CASES)
def test_clean_daily_prayer_text_removes_generated_markup_and_titles(module, dirty):
    clean = module.clean_daily_prayer_text(dirty)

    assert not clean.startswith(("#", "**", "__", "`"))
    assert "Молитва дня" not in clean
    assert "Господи" in clean
    assert "\"не ожесточиться\"" in clean or "\"слово\"" in clean or dirty == DIRTY_CASES[0] or dirty == DIRTY_CASES[1]


@pytest.mark.parametrize("module_name", ["telegram", "max"])
def test_cached_prayer_is_cleaned_without_generator(monkeypatch, tmp_path, module_name):
    module = vera_bot if module_name == "telegram" else vera_max_bot
    db_path = tmp_path / f"{module_name}.db"
    monkeypatch.setattr(module, "DB_PATH", str(db_path))
    module.init_db()
    today = module.datetime.now().strftime("%Y-%m-%d")
    dirty = "# Молитва дня - 29 сентября\n\n**Господи, помоги мне.**"
    with sqlite3.connect(db_path) as conn:
        conn.execute("INSERT INTO daily_prayer_cache(date, prayer) VALUES (?, ?)", (today, dirty))
        conn.commit()

    async def generator_must_not_run(*args, **kwargs):
        raise AssertionError("cached prayer unexpectedly generated")

    if module_name == "telegram":
        monkeypatch.setattr(module, "claude_messages_create", generator_must_not_run)
        prayer = asyncio.run(module.get_prayer_of_day())
    else:
        monkeypatch.setattr(module.claude_client.messages, "create", generator_must_not_run)
        prayer = asyncio.run(module.get_prayer_of_day_max())

    assert prayer == "Господи, помоги мне."
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT prayer FROM daily_prayer_cache WHERE date=?", (today,)).fetchone()[0] == dirty


@pytest.mark.parametrize("module_name", ["telegram", "max"])
def test_morning_broadcast_has_only_clean_prayer_body(monkeypatch, module_name):
    module = vera_bot if module_name == "telegram" else vera_max_bot
    captured = []

    class FakeConnection:
        def cursor(self):
            return self

        def execute(self, *args):
            return self

        def fetchall(self):
            return [(7, "", "chat-7")] if module_name == "max" else [(7, "")]

        def close(self):
            pass

    monkeypatch.setattr(module, "db_connect", lambda: FakeConnection())
    monkeypatch.setattr(module, "date_ru", lambda *_: "29 сентября")
    monkeypatch.setattr(module, "get_todays_feast", lambda: "")
    dirty = "# Личное молитвенное обращение\n\n**Господи, дай мне мир.**"
    if module_name == "telegram":
        async def get_prayer():
            return dirty

        async def send_message(*args, **kwargs):
            captured.append((args, kwargs))

        monkeypatch.setattr(module, "get_prayer_of_day", get_prayer)
        monkeypatch.setattr(module, "bot", SimpleNamespace(send_message=send_message))
        asyncio.run(module.morning_broadcast())
    else:
        async def get_prayer_max():
            return dirty

        async def send_proactive(chat_id, text):
            captured.append((chat_id, text))
            return True

        monkeypatch.setattr(module, "get_prayer_of_day_max", get_prayer_max)
        monkeypatch.setattr(module, "send_proactive_max", send_proactive)
        asyncio.run(module.morning_broadcast_max())

    text = captured[0][0][1] if module_name == "telegram" else captured[0][1]
    assert "Православный помощник" not in text
    assert "Все молитвы" not in text
    assert "@id232007136009_1_bot" not in text
    assert "@Moya_Vera_bot" not in text
    assert "────────" not in text and "─────────────────" not in text
    assert text.count("Молитва дня") == 1
    assert "Господи, дай мне мир." in text
    assert "# Личное" not in text and "**" not in text
