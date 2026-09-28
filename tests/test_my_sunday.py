import asyncio
import importlib
from datetime import date

import pytest


TG = importlib.import_module("vera_bot")
MAX = importlib.import_module("vera_max_bot")
SUNDAY = date(2026, 10, 4)
GOSPEL_URL = "https://azbyka.ru/biblia/?Mk.8:34-9:1"
SOURCE_URL = "https://azbyka.ru/days/2026-10-04"

HTML_WITH_GOSPEL = """
<meta name="description" content="2026 - Неделя">
<div id="liturgiya">Лит.</div>
<a class="bibref" href="https://azbyka.ru/biblia/?Mk.8:34-9:1">Мк.8:34–9:1</a>
"""
HTML_WITHOUT_GOSPEL = '<div id="liturgiya">Лит.</div><p>Без ссылки</p>'
HTML_WITHOUT_LITURGY_MARKER = '<p>Евангелие без литургии</p>'


class _AiohttpResponse:
    status = 200

    async def text(self):
        return self.html

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None


class _AiohttpSession:
    def __init__(self, html):
        self.response = _AiohttpResponse()
        self.response.html = html

    def get(self, *args, **kwargs):
        return self.response

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None


class _AiohttpClientSession:
    html = HTML_WITH_GOSPEL

    def __new__(cls, *args, **kwargs):
        return _AiohttpSession(cls.html)


class _HttpxResponse:
    status_code = 200
    text = HTML_WITH_GOSPEL


class _HttpxClient:
    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def get(self, *args, **kwargs):
        return _HttpxResponse()


def _run(coro):
    return asyncio.run(coro)


@pytest.mark.parametrize("module", [TG, MAX])
def test_nearest_sunday_and_liturgy_parser(module, monkeypatch):
    assert module.nearest_sunday(date(2026, 9, 28)) == SUNDAY

    if module is TG:
        monkeypatch.setattr(module.aiohttp, "ClientSession", _AiohttpClientSession)
    else:
        monkeypatch.setattr(module.httpx, "AsyncClient", _HttpxClient)

    reading = _run(module.fetch_sunday_gospel_reading(SUNDAY))
    assert reading["gospel_ref"] == "Мк.8:34–9:1"
    assert reading["gospel_url"] == GOSPEL_URL
    assert reading["gospel_source"] == SOURCE_URL

    for missing_html in (HTML_WITHOUT_GOSPEL, HTML_WITHOUT_LITURGY_MARKER):
        if module is TG:
            _AiohttpClientSession.html = missing_html
        else:
            _HttpxResponse.text = missing_html
        assert _run(module.fetch_sunday_gospel_reading(SUNDAY)) is None


@pytest.mark.parametrize("module", [TG, MAX])
def test_sunday_cache_fetches_and_generates_once(tmp_path, monkeypatch, module):
    monkeypatch.setattr(module, "DB_PATH", str(tmp_path / f"{module.__name__}.db"))
    module.init_db()
    counts = {"fetch": 0, "generate": 0}
    reading = {"gospel_ref": "Мк.8:34–9:1", "gospel_url": GOSPEL_URL,
               "gospel_source": SOURCE_URL, "week_title": "Неделя"}
    content = {"short_explanation": "short", "weekly_thought": "thought",
               "small_step": "step", "prayer_note": "prayer"}

    async def fetch(_sunday):
        counts["fetch"] += 1
        return reading

    async def generate(_sunday, _reading):
        counts["generate"] += 1
        return content

    monkeypatch.setattr(module, "fetch_sunday_gospel_reading", fetch)
    monkeypatch.setattr(module, "generate_sunday_companion_explanation", generate)
    first = _run(module.get_sunday_companion(SUNDAY))
    second = _run(module.get_sunday_companion(SUNDAY))

    assert first == second
    assert counts == {"fetch": 1, "generate": 1}


@pytest.mark.parametrize("module", [TG, MAX])
def test_sunday_generation_failure_keeps_confirmed_reading_and_uses_fallback(tmp_path, monkeypatch, module):
    monkeypatch.setattr(module, "DB_PATH", str(tmp_path / f"{module.__name__}.db"))
    module.init_db()
    reading = {"gospel_ref": "Мк.8:34–9:1", "gospel_url": GOSPEL_URL,
               "gospel_source": SOURCE_URL, "week_title": "Неделя"}

    async def fetch(_sunday):
        return reading

    async def fail(_sunday, _reading):
        raise RuntimeError("AI unavailable")

    monkeypatch.setattr(module, "fetch_sunday_gospel_reading", fetch)
    monkeypatch.setattr(module, "generate_sunday_companion_explanation", fail)
    result = _run(module.get_sunday_companion(SUNDAY))

    assert result["gospel_ref"] == reading["gospel_ref"]
    assert result["gospel_url"] == reading["gospel_url"]
    assert result["gospel_source"] == reading["gospel_source"]
    assert result["short_explanation"] == module.SUNDAY_COMPANION_FALLBACK["short_explanation"]
    assert result["prayer_note"] == module.SUNDAY_COMPANION_FALLBACK["prayer_note"]


def _telegram_callbacks(markup):
    return {button.callback_data for row in markup.inline_keyboard for button in row if button.callback_data}


def test_telegram_sunday_menus_expose_required_callbacks():
    callbacks = _telegram_callbacks(TG.my_sunday_menu(True))
    assert {"my_sunday_gospel", "my_sunday_prepare", "my_sunday_loved_ones",
            "my_sunday_situation", "main_menu"} <= callbacks


def test_max_sunday_buttons_expose_required_callbacks():
    callbacks = {button["payload"] for row in MAX.my_sunday_buttons(True) for button in row}
    assert {"my_sunday_gospel", "my_sunday_prepare", "my_sunday_loved_ones",
            "my_sunday_situation", "main_menu"} <= callbacks


def test_situation_reflection_delegates_to_existing_telegram_flow(monkeypatch):
    called = []

    async def situation(callback):
        called.append(callback)

    class Callback:
        data = "my_sunday_reflect:situation"

        class User:
            id = 1

        from_user = User()

        async def answer(self, *args):
            pass

    monkeypatch.setattr(TG, "track_funnel_event", lambda *args, **kwargs: None)
    monkeypatch.setattr(TG, "cb_my_sunday_situation", situation)
    callback = Callback()
    _run(TG.cb_my_sunday_reflect(callback))
    assert called == [callback]


def test_situation_callback_delegates_to_existing_telegram_review(monkeypatch):
    called = []

    async def situation_review(callback):
        called.append(callback)

    class Callback:
        class User:
            id = 1

        from_user = User()

    monkeypatch.setattr(TG, "track_funnel_event", lambda *args, **kwargs: None)
    monkeypatch.setattr(TG, "cb_situation_review", situation_review)
    callback = Callback()
    _run(TG.cb_my_sunday_situation(callback))
    assert called == [callback]


def test_situation_reflection_delegates_to_existing_max_flow(monkeypatch):
    delegated = []

    async def delegation(chat_id, user_id, payload, first_name=""):
        delegated.append((chat_id, user_id, payload, first_name))

    original = MAX.handle_callback
    monkeypatch.setattr(MAX, "track_funnel_event", lambda *args, **kwargs: None)
    monkeypatch.setattr(MAX, "handle_callback", delegation)
    _run(original(10, 20, "my_sunday_reflect:situation", "Vera"))
    assert delegated == [(10, 20, "my_sunday_situation", "Vera")]


def test_situation_callback_delegates_to_existing_max_review(monkeypatch):
    delegated = []

    async def delegation(chat_id, user_id, payload, first_name=""):
        delegated.append((chat_id, user_id, payload, first_name))

    original = MAX.handle_callback
    monkeypatch.setattr(MAX, "track_funnel_event", lambda *args, **kwargs: None)
    monkeypatch.setattr(MAX, "handle_callback", delegation)
    _run(original(10, 20, "my_sunday_situation", "Vera"))
    assert delegated == [(10, 20, "situation_review", "Vera")]
