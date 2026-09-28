import asyncio
import sqlite3

from ops import send_my_sunday_announcement as announcement


def make_db(path, users, events=()):
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE users (user_id INTEGER PRIMARY KEY, max_chat_id TEXT)")
    conn.execute("CREATE TABLE funnel_events (user_id INTEGER, platform TEXT, event_name TEXT, source TEXT, target TEXT, value TEXT, metadata TEXT, created_at TEXT)")
    conn.executemany("INSERT INTO users(user_id,max_chat_id) VALUES (?,?)", users)
    conn.executemany("INSERT INTO funnel_events(user_id,platform,event_name,source) VALUES (?,?,?,?)", events)
    conn.commit()
    conn.close()


def test_max_merge_current_wins_and_null_uses_external(tmp_path):
    db = tmp_path / "max.db"
    make_db(db, [(1, "current-1"), (2, None), (3, "current-3")])
    pairs, diagnostics = announcement.max_candidates(str(db), {1: "old-1", 2: "old-2", 99: "unknown"})
    assert set(pairs) == {(1, "current-1"), (2, "old-2"), (3, "current-3")}
    assert diagnostics["conflicts"] == 1
    assert diagnostics["unknown_external"] == 1


def test_duplicate_chat_ids_are_ambiguous_and_skipped(tmp_path):
    db = tmp_path / "max.db"
    make_db(db, [(1, "same"), (2, None), (3, "unique")])
    pairs, diagnostics = announcement.max_candidates(str(db), {2: "same"})
    assert pairs == [(3, "unique")]
    assert diagnostics["ambiguous"] == 1


def test_prior_sent_event_removes_candidate(tmp_path):
    db = tmp_path / "tg.db"
    make_db(db, [(1, None), (2, None)], [(1, "Telegram", announcement.EVENT_SENT, announcement.MARKER)])
    assert announcement.telegram_candidates(str(db)) == [2]


def test_dry_run_does_not_send_or_write_events(tmp_path):
    tg = tmp_path / "tg.db"
    mx = tmp_path / "max.db"
    make_db(tg, [(1, None)])
    make_db(mx, [(2, "chat-2")])
    calls = []

    async def should_not_send(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("dry run sent")

    before = sqlite3.connect(tg).execute("SELECT COUNT(*) FROM funnel_events").fetchone()[0]
    result = asyncio.run(announcement.run_campaign(
        "all", telegram_db=str(tg), max_db=str(mx),
        telegram_sender=should_not_send, max_sender=should_not_send,
    ))
    assert result["candidates"] == 2
    assert result["sent"] == 0
    assert result["failed"] == 0
    assert result["skipped"] == 2
    assert calls == []
    assert sqlite3.connect(tg).execute("SELECT COUNT(*) FROM funnel_events").fetchone()[0] == before


def test_success_and_failure_events_are_distinct_and_retryable(tmp_path):
    tg = tmp_path / "tg.db"
    make_db(tg, [(1, None), (2, None)])
    sent = []

    async def sender(user_id, **kwargs):
        sent.append(user_id)
        if user_id == 2:
            raise RuntimeError("blocked")

    result = asyncio.run(announcement.run_campaign("telegram", telegram_db=str(tg), send=True, telegram_sender=sender))
    assert result["sent"] == 1 and result["failed"] == 1
    conn = sqlite3.connect(tg)
    assert conn.execute("SELECT COUNT(*) FROM funnel_events WHERE event_name=?", (announcement.EVENT_SENT,)).fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM funnel_events WHERE event_name=?", (announcement.EVENT_FAILED,)).fetchone()[0] == 1
    assert announcement.telegram_candidates(str(tg)) == [2]


def test_constants_are_exact_and_safe():
    assert announcement.BUTTON_TEXT == "⛪ Моё воскресенье"
    assert announcement.BUTTON_PAYLOAD == "my_sunday"
    assert "донат" not in announcement.MESSAGE_TEXT.lower()
    assert "пожертв" not in announcement.MESSAGE_TEXT.lower()
    assert "открыть ассистент" not in announcement.MESSAGE_TEXT.lower()
