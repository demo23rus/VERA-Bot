#!/usr/bin/env python3
"""One-time, resumable owner announcement for «Моё воскресенье».

The command is deliberately dry-run by default.  It only records a successful
delivery after the platform send operation has completed successfully.
"""

import argparse
import asyncio
import inspect
import json
import os
import sqlite3
from typing import Any, Awaitable, Callable, Dict, Optional, Sequence, Tuple


MARKER = "my_sunday_announcement_2026_09_28"
EVENT_SENT = "broadcast_my_sunday_sent"
EVENT_FAILED = "broadcast_my_sunday_failed"
MESSAGE_TEXT = (
    "⛪ В «С верой» появилась новая функция — «Моё воскресенье»\n\n"
    "Теперь можно заранее посмотреть Евангелие ближайшего воскресенья, коротко понять смысл чтения, "
    "взять одну мысль на неделю и спокойно подготовиться к храму.\n\n"
    "Чтение берётся из церковного календаря, а объяснение написано простым человеческим языком.\n\n"
    "Нажмите кнопку ниже — функция уже доступна в главном меню."
)
BUTTON_TEXT = "⛪ Моё воскресенье"
BUTTON_PAYLOAD = "my_sunday"
TELEGRAM_DB_PATH = os.environ.get("TELEGRAM_DB_PATH", "/root/vera.db")
MAX_DB_PATH = os.environ.get("MAX_DB_PATH", "/root/vera_max.db")


def _connect(path: str) -> sqlite3.Connection:
    return sqlite3.connect(path, timeout=30)


def _sent_ids(conn: sqlite3.Connection, platform: str) -> set[int]:
    rows = conn.execute(
        "SELECT DISTINCT user_id FROM funnel_events "
        "WHERE platform=? AND event_name=? AND source=?",
        (platform, EVENT_SENT, MARKER),
    )
    return {int(row[0]) for row in rows}


def telegram_candidates(db_path: str) -> list[int]:
    with _connect(db_path) as conn:
        sent = _sent_ids(conn, "Telegram")
        return [int(row[0]) for row in conn.execute("SELECT user_id FROM users") if int(row[0]) not in sent]


def load_extra_max_map(path: Optional[str]) -> Dict[int, str]:
    if not path:
        return {}
    with open(path, encoding="utf-8") as handle:
        raw = json.load(handle)
    if not isinstance(raw, dict):
        raise ValueError("extra MAX map must be a JSON object")
    result: Dict[int, str] = {}
    for user_id, chat_id in raw.items():
        try:
            result[int(user_id)] = str(chat_id)
        except (TypeError, ValueError):
            continue
    return result


def max_candidates(db_path: str, extra_map: Optional[Dict[int, str]] = None) -> Tuple[list[Tuple[int, str]], Dict[str, int]]:
    """Return verified user/chat pairs and aggregate merge diagnostics."""
    extra_map = extra_map or {}
    with _connect(db_path) as conn:
        rows = [(int(user_id), str(chat_id) if chat_id is not None and str(chat_id) else None)
                for user_id, chat_id in conn.execute("SELECT user_id, max_chat_id FROM users")]
        sent = _sent_ids(conn, "MAX")

    current_users = {user_id: chat_id for user_id, chat_id in rows}
    diagnostics = {"unknown_external": 0, "conflicts": 0, "ambiguous": 0, "already_sent": 0}
    pairs: list[Tuple[int, str]] = []
    for user_id, external_chat_id in extra_map.items():
        if user_id not in current_users:
            diagnostics["unknown_external"] += 1
        elif current_users[user_id] and external_chat_id and current_users[user_id] != external_chat_id:
            diagnostics["conflicts"] += 1

    chosen: Dict[int, str] = {}
    for user_id, current_chat_id in rows:
        chat_id = current_chat_id or extra_map.get(user_id)
        if not chat_id:
            continue
        if user_id in sent:
            diagnostics["already_sent"] += 1
            continue
        chosen[user_id] = chat_id

    by_chat: Dict[str, list[int]] = {}
    for user_id, chat_id in chosen.items():
        by_chat.setdefault(chat_id, []).append(user_id)
    ambiguous_chats = {chat_id for chat_id, users in by_chat.items() if len(users) > 1}
    diagnostics["ambiguous"] = len(ambiguous_chats)
    for chat_id, users in by_chat.items():
        if chat_id not in ambiguous_chats:
            pairs.append((users[0], chat_id))
    return pairs, diagnostics


def _record(db_path: str, user_id: int, platform: str, event_name: str, value: str = "") -> None:
    with _connect(db_path) as conn:
        conn.execute(
            "INSERT INTO funnel_events (user_id,platform,event_name,source,target,value,metadata,created_at) "
            "VALUES (?,?,?,?,?,?,?,datetime('now'))",
            (user_id, platform, event_name, MARKER, "my_sunday_broadcast", value[:200], ""),
        )
        conn.commit()


def telegram_keyboard():
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=BUTTON_TEXT, callback_data=BUTTON_PAYLOAD)]])


def max_keyboard():
    from vera_max_bot import btn
    return [[btn(BUTTON_TEXT, BUTTON_PAYLOAD)]]


async def _maybe_close(bot: Any) -> None:
    session = getattr(bot, "session", None)
    close = getattr(session, "close", None)
    if close:
        result = close()
        if inspect.isawaitable(result):
            await result


async def run_campaign(platform: str = "all", extra_map: Optional[Dict[int, str]] = None,
                       telegram_db: str = TELEGRAM_DB_PATH, max_db: str = MAX_DB_PATH,
                       send: bool = False, limit: Optional[int] = None,
                       telegram_sender: Optional[Callable[..., Awaitable[Any]]] = None,
                       max_sender: Optional[Callable[..., Awaitable[Any]]] = None,
                       record_event: Optional[Callable[..., None]] = None) -> dict:
    record_event = record_event or _record_adapter
    summary = {"candidates": 0, "sent": 0, "failed": 0, "skipped": 0,
               "conflicts": 0, "ambiguous": 0, "unknown_external": 0}
    targets: list[Tuple[str, int, Optional[str], str]] = []
    if platform in ("telegram", "all"):
        targets.extend(("Telegram", user_id, None, telegram_db) for user_id in telegram_candidates(telegram_db))
    if platform in ("max", "all"):
        pairs, diagnostics = max_candidates(max_db, extra_map)
        for key in ("conflicts", "ambiguous", "unknown_external"):
            summary[key] = diagnostics[key]
        targets.extend(("MAX", user_id, chat_id, max_db) for user_id, chat_id in pairs)
    if limit is not None:
        targets = targets[:limit]
    summary["candidates"] = len(targets)
    if not send:
        summary["skipped"] = len(targets)
        return summary

    telegram_bot = None
    if telegram_sender is None and any(item[0] == "Telegram" for item in targets):
        from vera_bot import bot
        telegram_bot = bot
        telegram_sender = lambda user_id, **kwargs: bot.send_message(user_id, **kwargs)
    if max_sender is None and any(item[0] == "MAX" for item in targets):
        from vera_max_bot import send_proactive_max
        max_sender = send_proactive_max

    try:
        for item_platform, user_id, chat_id, db_path in targets:
            try:
                if item_platform == "Telegram":
                    await telegram_sender(user_id, text=MESSAGE_TEXT, reply_markup=telegram_keyboard())
                else:
                    if not await max_sender(chat_id, MESSAGE_TEXT, max_keyboard()):
                        raise RuntimeError("MAX send_proactive_max returned False")
                record_event(user_id, item_platform, EVENT_SENT, db_path)
                summary["sent"] += 1
            except Exception as exc:
                summary["failed"] += 1
                record_event(user_id, item_platform, EVENT_FAILED, db_path, str(exc))
            await asyncio.sleep(0.05 if item_platform == "Telegram" else 0.10)
    finally:
        if telegram_bot is not None:
            await _maybe_close(telegram_bot)
    return summary


def _record_adapter(user_id: int, platform: str, event_name: str, db_path: str, value: str = "") -> None:
    _record(db_path, user_id, platform, event_name, value)


def main(argv: Optional[Sequence[str]] = None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--platform", choices=("telegram", "max", "all"), default="all")
    parser.add_argument("--extra-max-map")
    parser.add_argument("--send", action="store_true")
    parser.add_argument("--limit", type=int)
    args = parser.parse_args(argv)
    result = asyncio.run(run_campaign(args.platform, load_extra_max_map(args.extra_max_map), send=args.send,
                                      limit=args.limit, record_event=_record_adapter))
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
