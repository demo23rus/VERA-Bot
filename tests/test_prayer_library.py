import os

os.environ.setdefault("TELEGRAM_OWNER_ID", "1")
os.environ.setdefault("MAX_TOKEN", "test-token")
os.environ.setdefault("OPENAI_KEY", "test-openai-key")
os.environ.setdefault("ANTHROPIC_KEY", "test-anthropic-key")

import vera_bot
import vera_max_bot


def tg_callbacks(markup):
    return [
        button.callback_data
        for row in markup.inline_keyboard
        for button in row
        if button.callback_data
    ]


def max_payloads(rows):
    return [
        button.get("payload")
        for row in rows
        for button in row
        if button.get("type") == "callback"
    ]


def test_main_prayer_menu_exposes_more_prayers_and_akathists_on_both_platforms():
    assert {"more_prayers", "akathists"} <= set(tg_callbacks(vera_bot.prayers_menu()))
    assert {"more_prayers", "akathists"} <= set(max_payloads(vera_max_bot.prayers_buttons()))


def test_more_prayers_callbacks_resolve_to_real_prayer_entries():
    tg_more = [x for x in tg_callbacks(vera_bot.more_prayers_menu()) if x.startswith("prayer_")]
    max_more = [x for x in max_payloads(vera_max_bot.more_prayers_buttons()) if x.startswith("prayer_")]

    assert len(tg_more) == 8
    assert len(max_more) == 8

    for callback in tg_more:
        key = callback.removeprefix("prayer_")
        assert key in vera_bot.PRAYERS
        assert vera_bot.PRAYERS[key]["text"].strip()

    for callback in max_more:
        assert callback in vera_max_bot.PRAYER_TEXTS
        title, text = vera_max_bot.PRAYER_TEXTS[callback]
        assert title.strip()
        assert text.strip()


def test_expected_new_prayers_are_available():
    tg_expected = {
        "heavenly_king",
        "trisagion",
        "worthy",
        "guardian_angel",
        "otche",
        "bogorodice",
        "jesus",
        "symbol",
    }
    max_expected = {
        "prayer_heavenly_king",
        "prayer_trisagion",
        "prayer_worthy",
        "prayer_guardian_angel",
        "prayer_after_meal",
        "prayer_zdravie",
        "prayer_doroga",
        "prayer_o_detyah",
    }
    assert tg_expected <= set(vera_bot.PRAYERS)
    assert max_expected <= set(vera_max_bot.PRAYER_TEXTS)


def test_akathist_catalog_is_same_on_both_platforms_and_uses_verified_source_links():
    assert vera_bot.AKATHISTS == vera_max_bot.AKATHISTS
    assert len(vera_bot.AKATHISTS) == 6

    for key, (title, url) in vera_bot.AKATHISTS.items():
        assert key
        assert title.startswith(("☦️", "🌸", "🕯️", "🌺", "💛"))
        assert url.startswith("https://azbyka.ru/molitvoslov/")
        assert url.endswith(".html")


def test_akathist_menus_point_only_to_known_items():
    tg_items = [x for x in tg_callbacks(vera_bot.akathists_menu()) if x.startswith("akathist_")]
    max_items = [x for x in max_payloads(vera_max_bot.akathists_buttons()) if x.startswith("akathist_")]

    assert len(tg_items) == len(vera_bot.AKATHISTS)
    assert len(max_items) == len(vera_max_bot.AKATHISTS)

    for callback in tg_items:
        assert callback.removeprefix("akathist_") in vera_bot.AKATHISTS
    for callback in max_items:
        assert callback.removeprefix("akathist_") in vera_max_bot.AKATHISTS
