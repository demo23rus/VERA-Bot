import random
import re

import json
from pathlib import Path
from contextlib import suppress
MONTHS_RU = {
    1: "января", 2: "февраля", 3: "марта", 4: "апреля",
    5: "мая", 6: "июня", 7: "июля", 8: "августа",
    9: "сентября", 10: "октября", 11: "ноября", 12: "декабря"
}

def date_ru(fmt="full"):
    from datetime import datetime as _dt
    now = _dt.now()
    month = MONTHS_RU[now.month]
    if fmt == "full":
        return f"{now.day} {month} {now.year}"
    elif fmt == "short":
        return f"{now.day} {month}"
    return f"{now.day}.{now.month:02d}"

import asyncio
import sqlite3
import logging
import aiohttp
import os
from datetime import datetime, timedelta, date
from openai import AsyncOpenAI
import anthropic
from aiogram import Bot, Dispatcher, F, BaseMiddleware
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove,
)
from aiogram.filters import Command
from aiogram.fsm.storage.memory import MemoryStorage
from yookassa import Configuration, Payment
import gspread
from google.oauth2.service_account import Credentials
import uuid

# ========== ЗАГРУЗКА КЛЮЧЕЙ ==========
def load_env(path="/root/.env_vera"):
    env = {}
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line and "=" in line and not line.startswith("#"):
                    k, v = line.split("=", 1)
                    env[k.strip()] = v.strip()
    except Exception as e:
        logging.warning(f"Не удалось загрузить {path}: {e}")
    return env

_env = load_env()

def _config_bool(name: str, default: bool = False) -> bool:
    raw = _env.get(name)
    if raw is None:
        raw = os.environ.get(name)
    if raw is None or str(raw).strip() == "":
        return bool(default)
    return str(raw).strip().lower() not in {"0", "false", "no", "off"}

def _config_float(name: str, default: float, minimum: float = 1.0, maximum: float = 300.0) -> float:
    raw = _env.get(name)
    if raw is None:
        raw = os.environ.get(name)
    try:
        value = float(raw) if raw not in (None, "") else float(default)
    except (TypeError, ValueError):
        value = float(default)
    return max(minimum, min(maximum, value))

# ========== КОНФИГ ==========
BOT_TOKEN         = _env.get("TELEGRAM_BOT_TOKEN") or os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHANNEL_ID        = _env.get("TELEGRAM_CHANNEL_ID") or os.environ.get("TELEGRAM_CHANNEL_ID", "@SvyatoyPut")
BOT_USERNAME      = _env.get("TELEGRAM_BOT_USERNAME") or os.environ.get("TELEGRAM_BOT_USERNAME", "Moya_Vera_bot")
BOT_URL           = f"https://t.me/{BOT_USERNAME}"
OPENAI_KEY        = _env.get("OPENAI_KEY") or os.environ.get("OPENAI_KEY", "")
ANTHROPIC_KEY     = _env.get("ANTHROPIC_KEY") or os.environ.get("ANTHROPIC_KEY", "")
CHANNEL_POST_TIMEOUT_SECONDS = _config_float("CHANNEL_POST_TIMEOUT_SECONDS", 75.0, 30.0, 120.0)

def require_int_config(name: str) -> int:
    raw_value = _env.get(name) or os.environ.get(name)
    if raw_value is None or not str(raw_value).strip():
        raise RuntimeError(
            f"Обязательная переменная {name} не задана. "
            f"Добавьте {name}=ВАШ_ID в /root/.env_vera и перезапустите бота."
        )
    try:
        value = int(str(raw_value).strip())
    except ValueError as exc:
        raise RuntimeError(
            f"Переменная {name} должна содержать только числовой Telegram ID, "
            f"получено: {raw_value!r}."
        ) from exc
    if value <= 0:
        raise RuntimeError(f"Переменная {name} должна быть положительным Telegram ID.")
    return value

OWNER_ID          = require_int_config("TELEGRAM_OWNER_ID")
CREDENTIALS_FILE  = _env.get("GOOGLE_CREDENTIALS_FILE") or os.environ.get("GOOGLE_CREDENTIALS_FILE", "/root/google_credentials.json")
SPREADSHEET_ID    = _env.get("VERA_SPREADSHEET_ID") or os.environ.get("VERA_SPREADSHEET_ID", "1PE7CaFuWOe_eygQqIoMAmUdJBtATbIaNfZR4cvarPCA")

# Визуалы канала временно полностью отключены.

def select_channel_visual(*args, **kwargs):
    return None

def build_channel_image_prompt(*args, **kwargs):
    return ""

# CTA канала. Третий элемент — уникальный источник для аналитики воронки.
CHANNEL_CTA = {
    "morning": ("🙏 Откройте молитву дня в помощнике.", "🙏 Открыть молитвы", "ch_morning"),
    "quote": ("❓ Хотите разобраться глубже? Спросите помощника.", "❓ Задать вопрос", "ch_quote"),
    "saint": ("👼 Найдите святого и возможные дни его памяти.", "👼 Найти святого", "ch_saint"),
    "guidance": ("❓ Расскажите помощнику, что сейчас волнует.", "❓ Обратиться к помощнику", "ch_guidance"),
    "practical": ("⛪ Откройте пошаговую памятку в помощнике.", "⛪ Открыть памятку", "ch_practical"),
    "story": ("👼 Найдите святого по имени и дням памяти.", "👼 Найти святого", "ch_story"),
    "evening": ("🌙 Откройте вечернюю молитву.", "🌙 Вечерняя молитва", "ch_evening"),
    "qa": ("✍️ Задайте помощнику свой вопрос.", "✍️ Задать вопрос", "ch_qa"),
    "life": ("👼 Узнайте о святом и своём дне ангела.", "👼 Найти святого", "ch_life"),
    "film": ("📚 Откройте православную библиотеку.", "📚 Открыть библиотеку", "ch_film"),
    "gospel": ("📖 Откройте Евангельская мысль.", "📖 Евангельская мысль", "ch_gospel"),
    "photo": ("📸 Отправьте фото иконы помощнику.", "📸 Узнать икону", "ch_photo"),
    "church": ("🗺️ Найдите ближайший храм.", "🗺️ Найти храм", "ch_church"),
    "showcase_prayer": ("🙏 Выберите молитву по своей ситуации.", "🙏 Выбрать молитву", "ch_showcase_prayer"),
    "showcase_photo": ("📸 Отправьте фото иконы для определения образа.", "📸 Определить икону", "ch_showcase_photo"),
    "showcase_angel": ("👼 Найдите возможные дни памяти покровителя.", "👼 Узнать день ангела", "ch_showcase_angel"),
    "showcase_confession": ("📿 Откройте спокойную подготовку к исповеди.", "📿 Подготовиться", "ch_showcase_confession"),
    "interactive": ("💬 Выберите тему следующей полезной публикации.", "💬 Выбрать тему", "ch_interactive"),
    "community": ("❓ Есть похожая ситуация? Задайте свой вопрос помощнику.", "❓ Задать свой вопрос", "ch_community"),
}

# Куда направлять человека после перехода из канала.
CHANNEL_ROUTES = {
    "ch_morning": "prayers",
    "ch_quote": "ask_question",
    "ch_saint": "saints",
    "ch_guidance": "ask_question",
    "ch_practical": "sacraments",
    "ch_story": "saints",
    "ch_evening": "prayer_evening_ru",
    "ch_qa": "ask_question",
    "ch_life": "saints",
    "ch_film": "library",
    "ch_gospel": "daily_gospel",
    "ch_photo": "photo_icon",
    "ch_church": "find_church",
    "ch_profile": "profile",
    "ch_calendar": "calendar",
    "ch_showcase_prayer": "prayers",
    "ch_showcase_photo": "photo_icon",
    "ch_showcase_angel": "saints",
    "ch_showcase_confession": "sacr_ispoved",
    "ch_interactive": "interactive_menu",
    "ch_community": "ask_question",
}

CHANNEL_CTA_B_LABELS = {
    "morning": "🙏 Начать день с молитвы",
    "quote": "❓ Разобрать свою ситуацию",
    "saint": "👼 Узнать своего покровителя",
    "guidance": "🕊️ Получить бережный ответ",
    "practical": "⛪ Посмотреть пошагово",
    "story": "👼 Найти святого по имени",
    "evening": "🌙 Завершить день с молитвой",
    "qa": "✍️ Спросить помощника",
    "life": "📖 Узнать больше о святом",
    "film": "📚 Выбрать материал",
    "gospel": "📖 Прочитать сегодня",
    "photo": "📸 Отправить фото иконы",
    "church": "🗺️ Найти храм рядом",
    "showcase_prayer": "🙏 Найти свою молитву",
    "showcase_photo": "📸 Определить образ",
    "showcase_confession": "📿 Подготовиться спокойно",
    "interactive": "💬 Выбрать следующую тему",
}


def save_post_source(post_key: str, source: str, variant: str):
    try:
        conn = _funnel_conn()
        conn.execute("UPDATE channel_posts SET source=?,variant=? WHERE post_key=?", (source, variant, post_key))
        conn.commit(); conn.close()
    except Exception as e:
        logging.error(f"Post source save error: {e}")


def channel_button(cta_key: str, source_override: str = "") -> InlineKeyboardMarkup:
    _footer, label, source = CHANNEL_CTA.get(cta_key, CHANNEL_CTA["guidance"])
    source = source_override or source
    if source_override.endswith("b"):
        label = CHANNEL_CTA_B_LABELS.get(cta_key, label)
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
        text=label,
        url=f"{BOT_URL}?start={source}",
    )]])


def add_channel_cta(text: str, cta_key: str) -> str:
    footer, _label, _source = CHANNEL_CTA.get(cta_key, CHANNEL_CTA["guidance"])
    return f"{text.rstrip()}\n\n─────────────────\n{footer}"



async def generate_channel_image_bytes(*args, **kwargs):
    return None

async def download_channel_image(*args, **kwargs):
    return None

async def send_channel_post(
    text: str,
    cta_key: str,
    with_photo: bool = False,
    msk_now: datetime = None,
    photo_urls=None,
    visual_title: str = "",
    source_override: str = "",
    generation_prompt: str = "",
    cache_key: str = "",
    prefer_generated: bool = False,
    show_visual_title: bool = False,
):
    """Публикует текстовый пост и сохраняет CTA-кнопку воронки."""
    base_text = clean_channel_markup(text)
    final_text = add_channel_cta(base_text, cta_key)
    reply_markup = channel_button(cta_key, source_override)
    try:
        posted = await bot.send_message(
            CHANNEL_ID,
            final_text[:4096],
            reply_markup=reply_markup,
            disable_web_page_preview=True,
        )
        logging.info(f"Канал ТГ: текст+CTA отправлены, message_id={posted.message_id}")
        return str(posted.message_id)
    except Exception as e:
        logging.error(f"Канал ТГ: публикация не удалась: {e}")
        return ""

logging.basicConfig(level=logging.INFO)
logging.info(f"OPENAI_KEY: {'configured' if OPENAI_KEY else 'missing'}")
logging.info(f"ANTHROPIC_KEY: {'configured' if ANTHROPIC_KEY else 'missing'}")

# Лимиты
FREE_AI_REQUESTS  = 10
FREE_PHOTO        = 3

def validate_core_config():
    missing = [
        name for name, value in (
            ("TELEGRAM_BOT_TOKEN", BOT_TOKEN),
            ("OPENAI_KEY", OPENAI_KEY),
            ("ANTHROPIC_KEY", ANTHROPIC_KEY),
        ) if not str(value or "").strip()
    ]
    if missing:
        raise RuntimeError(
            "Не заданы обязательные параметры в /root/.env_vera: " + ", ".join(missing)
        )


validate_core_config()

# ЮКасса
YOOKASSA_SHOP_ID  = _env.get("YOOKASSA_SHOP_ID") or os.environ.get("YOOKASSA_SHOP_ID", "")
YOOKASSA_SECRET   = _env.get("YOOKASSA_SECRET") or os.environ.get("YOOKASSA_SECRET", "")
if YOOKASSA_SHOP_ID and YOOKASSA_SECRET:
    Configuration.account_id = YOOKASSA_SHOP_ID
    Configuration.secret_key = YOOKASSA_SECRET
else:
    logging.warning("ЮКасса Telegram не настроена — пожертвования временно недоступны")

# ========== КЛИЕНТЫ AI ==========
openai_client = AsyncOpenAI(api_key=OPENAI_KEY)
claude_client = anthropic.Anthropic(api_key=ANTHROPIC_KEY, timeout=45.0)

async def claude_messages_create(**kwargs):
    """Не блокирует event loop во время синхронного запроса Anthropic."""
    return await asyncio.to_thread(claude_client.messages.create, **kwargs)

# ========== БОТ ==========
# Бот с увеличенным таймаутом сессии (против Request timeout при постинге)
try:
    from aiogram.client.session.aiohttp import AiohttpSession
    _session = AiohttpSession(timeout=60)
    bot = Bot(token=BOT_TOKEN, session=_session)
except Exception as _e:
    logging.error(f"Не удалось задать таймаут сессии, использую стандартный: {_e}")
    bot = Bot(token=BOT_TOKEN)
dp  = Dispatcher(storage=MemoryStorage())

# Защита от одновременной публикации планировщиком и восстановлением после перезапуска.
CHANNEL_PUBLISH_LOCK = asyncio.Lock()


def db_connect():
    """SQLite connection tuned for two concurrently running bot processes."""
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def create_database_backup(prefix: str = "vera_tg") -> str:
    """Creates a consistent SQLite backup and keeps the latest 14 copies."""
    backup_dir = Path(BACKUP_DIR)
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    target = backup_dir / f"{prefix}_{stamp}.db"
    tmp = target.with_suffix(".tmp")
    source_conn = sqlite3.connect(DB_PATH, timeout=30)
    dest_conn = sqlite3.connect(str(tmp))
    try:
        source_conn.backup(dest_conn)
    finally:
        dest_conn.close()
        source_conn.close()
    tmp.replace(target)
    backups = sorted(backup_dir.glob(f"{prefix}_*.db"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in backups[14:]:
        with suppress(Exception):
            old.unlink()
    set_app_setting("last_backup_path", str(target))
    set_app_setting("last_backup_at", datetime.now().isoformat())
    return str(target)


def backup_status_text(prefix: str = "vera_tg") -> str:
    backup_dir = Path(BACKUP_DIR)
    files = sorted(backup_dir.glob(f"{prefix}_*.db"), key=lambda p: p.stat().st_mtime, reverse=True) if backup_dir.exists() else []
    if not files:
        return "Резервных копий пока нет."
    latest = files[0]
    return f"Последняя копия: {latest.name}\nРазмер: {latest.stat().st_size // 1024} КБ\nВсего сохранено: {len(files)}"


async def database_backup_loop(prefix: str = "vera_tg"):
    await asyncio.sleep(105)
    while True:
        try:
            await asyncio.to_thread(create_database_backup, prefix)
            logging.info("Резервная копия базы Telegram создана")
        except Exception as e:
            logging.error(f"Ошибка резервного копирования Telegram: {e}")
            record_critical_error("backup_tg", e)
        await asyncio.sleep(24 * 3600)


def get_app_setting(key: str, default: str = "") -> str:
    try:
        conn = db_connect()
        row = conn.execute("SELECT value FROM app_settings WHERE key=?", (key,)).fetchone()
        conn.close()
        return str(row[0]) if row else default
    except Exception:
        return default


def set_app_setting(key: str, value) -> None:
    try:
        conn = db_connect()
        conn.execute(
            "INSERT OR REPLACE INTO app_settings(key,value,updated_at) VALUES (?,?,?)",
            (key, str(value), datetime.now().isoformat()),
        )
        conn.commit(); conn.close()
    except Exception as e:
        logging.error(f"app_settings write error: {e}")


def record_critical_error(component: str, error) -> None:
    try:
        conn = db_connect()
        conn.execute(
            "INSERT INTO critical_errors(component,error_text,created_at) VALUES (?,?,?)",
            (component[:80], str(error)[:1500], datetime.now().isoformat()),
        )
        conn.commit(); conn.close()
    except Exception:
        pass


def touch_user_session(user_id: int, platform: str, source: str = "", target: str = "") -> None:
    """Counts real return sessions, not only /start events."""
    try:
        now = datetime.now()
        conn = db_connect()
        row = conn.execute(
            "SELECT id,last_event_at FROM user_sessions WHERE user_id=? AND platform=? ORDER BY id DESC LIMIT 1",
            (int(user_id), platform),
        ).fetchone()
        new_session = True
        if row and row[1]:
            try:
                new_session = (now - datetime.fromisoformat(row[1])).total_seconds() >= 6 * 3600
            except Exception:
                pass
        if new_session:
            conn.execute(
                "INSERT INTO user_sessions(user_id,platform,started_at,last_event_at,source,target) VALUES (?,?,?,?,?,?)",
                (int(user_id), platform, now.isoformat(), now.isoformat(), source or "", target or ""),
            )
        else:
            conn.execute(
                "UPDATE user_sessions SET last_event_at=?,source=CASE WHEN ?<>'' THEN ? ELSE source END,target=CASE WHEN ?<>'' THEN ? ELSE target END WHERE id=?",
                (now.isoformat(), source, source, target, target, row[0]),
            )
        conn.commit(); conn.close()
    except Exception as e:
        logging.error(f"session tracking error: {e}")

# ========== БАЗА ДАННЫХ ==========
DB_PATH = _env.get("TELEGRAM_DB_PATH") or os.environ.get("TELEGRAM_DB_PATH", "/root/vera.db")
BACKUP_DIR = _env.get("VERA_BACKUP_DIR") or os.environ.get("VERA_BACKUP_DIR", "/root/vera_backups")

def init_db():
    conn = db_connect()
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS users (
        user_id       INTEGER PRIMARY KEY,
        username      TEXT    DEFAULT '',
        first_name    TEXT    DEFAULT '',
        church_name   TEXT    DEFAULT '',
        birth_date    TEXT    DEFAULT '',
        angel_day     TEXT    DEFAULT '',
        remind_days   INTEGER DEFAULT 3,
        step          TEXT    DEFAULT '',
        onboarded     INTEGER DEFAULT 0,
        registered_at TEXT    DEFAULT '',
        notifications INTEGER DEFAULT 0
    )""")
    # Добавляем колонку если её ещё нет (для существующих баз)
    try:
        c.execute("ALTER TABLE users ADD COLUMN notifications INTEGER DEFAULT 0")
        conn.commit()
    except Exception:
        pass
    # Таблица кеша молитвы дня
    c.execute("""CREATE TABLE IF NOT EXISTS daily_prayer_cache (
        date TEXT PRIMARY KEY,
        prayer TEXT
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS limits (
        user_id       INTEGER PRIMARY KEY,
        ai_requests   INTEGER DEFAULT 0,
        photo_requests INTEGER DEFAULT 0,
        last_reset    TEXT    DEFAULT ''
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS subscriptions (
        user_id  INTEGER PRIMARY KEY,
        plan     TEXT    DEFAULT '',
        sub_end  TEXT    DEFAULT ''
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS pending_payments (
        payment_id TEXT PRIMARY KEY,
        user_id    INTEGER,
        plan       TEXT,
        created_at TEXT
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS donation_payments (
        payment_id TEXT PRIMARY KEY,
        user_id INTEGER NOT NULL,
        chat_id INTEGER NOT NULL,
        username TEXT DEFAULT '',
        first_name TEXT DEFAULT '',
        amount INTEGER NOT NULL,
        platform TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending',
        user_notified INTEGER NOT NULL DEFAULT 0,
        owner_notified INTEGER NOT NULL DEFAULT 0,
        sheet_recorded INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        paid_at TEXT DEFAULT ''
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS favorites (
        id      INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        title   TEXT,
        content TEXT,
        saved_at TEXT
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS channel_posts (
        post_key TEXT PRIMARY KEY,
        post_date TEXT NOT NULL,
        slot TEXT NOT NULL,
        rubric TEXT NOT NULL,
        topic TEXT DEFAULT '',
        content TEXT DEFAULT '',
        status TEXT NOT NULL DEFAULT 'pending',
        created_at TEXT NOT NULL
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS channel_clicks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        source TEXT NOT NULL,
        target TEXT NOT NULL,
        clicked_at TEXT NOT NULL
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS user_reviews (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        chat_id INTEGER NOT NULL,
        username TEXT DEFAULT '',
        first_name TEXT DEFAULT '',
        review_text TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'new',
        owner_reply TEXT DEFAULT '',
        created_at TEXT NOT NULL,
        replied_at TEXT DEFAULT '',
        handled_by TEXT DEFAULT ''
    )""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_channel_clicks_source ON channel_clicks(source)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_channel_posts_date ON channel_posts(post_date)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_user_reviews_status ON user_reviews(status)")
    # Premium funnel V3: аналитика, активация, удержание и рефералы.
    c.execute("""CREATE TABLE IF NOT EXISTS funnel_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        platform TEXT NOT NULL,
        event_name TEXT NOT NULL,
        source TEXT DEFAULT '',
        target TEXT DEFAULT '',
        value TEXT DEFAULT '',
        metadata TEXT DEFAULT '',
        created_at TEXT NOT NULL
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS user_funnel_state (
        user_id INTEGER NOT NULL,
        platform TEXT NOT NULL,
        first_source TEXT DEFAULT '',
        first_target TEXT DEFAULT '',
        first_seen_at TEXT NOT NULL,
        last_seen_at TEXT NOT NULL,
        visit_count INTEGER NOT NULL DEFAULT 0,
        useful_actions INTEGER NOT NULL DEFAULT 0,
        activated_at TEXT DEFAULT '',
        profile_completed INTEGER NOT NULL DEFAULT 0,
        notifications_enabled INTEGER NOT NULL DEFAULT 0,
        review_left INTEGER NOT NULL DEFAULT 0,
        donation_made INTEGER NOT NULL DEFAULT 0,
        referral_code TEXT DEFAULT '',
        referred_by INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (user_id, platform)
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS nurture_journeys (
        user_id INTEGER NOT NULL,
        platform TEXT NOT NULL,
        track TEXT NOT NULL,
        day_index INTEGER NOT NULL DEFAULT 0,
        active INTEGER NOT NULL DEFAULT 1,
        next_send_at TEXT NOT NULL,
        last_sent_at TEXT DEFAULT '',
        created_at TEXT NOT NULL,
        PRIMARY KEY (user_id, platform)
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS referrals (
        platform TEXT NOT NULL,
        referrer_id INTEGER NOT NULL,
        referred_user_id INTEGER NOT NULL,
        status TEXT NOT NULL DEFAULT 'started',
        created_at TEXT NOT NULL,
        activated_at TEXT DEFAULT '',
        reward_sent INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (platform, referred_user_id)
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS post_experiments (
        source TEXT PRIMARY KEY,
        platform TEXT NOT NULL,
        post_key TEXT NOT NULL,
        cta_key TEXT NOT NULL,
        variant TEXT NOT NULL,
        created_at TEXT NOT NULL
    )""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_funnel_events_date ON funnel_events(created_at)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_funnel_events_source ON funnel_events(source)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_referrals_referrer ON referrals(referrer_id)")
    for migration in (
        "ALTER TABLE user_reviews ADD COLUMN publish_consent INTEGER DEFAULT 0",
        "ALTER TABLE user_reviews ADD COLUMN public_approved INTEGER DEFAULT 0",
        "ALTER TABLE channel_posts ADD COLUMN source TEXT DEFAULT ''",
        "ALTER TABLE channel_posts ADD COLUMN variant TEXT DEFAULT ''",
    ):
        try:
            c.execute(migration)
        except Exception:
            pass

    # V4 reliability, consent, attribution and operations tables.
    c.execute("""CREATE TABLE IF NOT EXISTS app_settings (
        key TEXT PRIMARY KEY, value TEXT DEFAULT '', updated_at TEXT NOT NULL
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS schema_migrations (
        version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS user_sessions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL, platform TEXT NOT NULL,
        started_at TEXT NOT NULL, last_event_at TEXT NOT NULL,
        source TEXT DEFAULT '', target TEXT DEFAULT ''
    )""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_sessions_user ON user_sessions(user_id,platform,last_event_at)")
    c.execute("""CREATE TABLE IF NOT EXISTS topic_votes (
        platform TEXT NOT NULL, week_key TEXT NOT NULL, user_id INTEGER NOT NULL,
        topic TEXT NOT NULL, updated_at TEXT NOT NULL,
        PRIMARY KEY(platform,week_key,user_id)
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS critical_errors (
        id INTEGER PRIMARY KEY AUTOINCREMENT, component TEXT NOT NULL,
        error_text TEXT NOT NULL, created_at TEXT NOT NULL
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS processed_updates (
        update_id TEXT PRIMARY KEY, update_type TEXT DEFAULT '', received_at TEXT NOT NULL
    )""")
    for migration in (
        "ALTER TABLE user_funnel_state ADD COLUMN last_source TEXT DEFAULT ''",
        "ALTER TABLE user_funnel_state ADD COLUMN last_target TEXT DEFAULT ''",
        "ALTER TABLE user_funnel_state ADD COLUMN last_source_at TEXT DEFAULT ''",
        "ALTER TABLE user_funnel_state ADD COLUMN last_session_at TEXT DEFAULT ''",
        "ALTER TABLE user_reviews ADD COLUMN published_at TEXT DEFAULT ''",
        "ALTER TABLE channel_posts ADD COLUMN message_id TEXT DEFAULT ''",
        "ALTER TABLE donation_payments ADD COLUMN checked_at TEXT DEFAULT ''",
        "ALTER TABLE donation_payments ADD COLUMN expires_at TEXT DEFAULT ''",
        "ALTER TABLE donation_payments ADD COLUMN last_error TEXT DEFAULT ''",
    ):
        try:
            c.execute(migration)
        except Exception:
            pass
    c.execute("INSERT OR IGNORE INTO schema_migrations(version,applied_at) VALUES (4,?)", (datetime.now().isoformat(),))

    conn.commit()
    conn.close()

# ========== PREMIUM FUNNEL V4 ==========
FUNNEL_USEFUL_CALLBACKS = {
    "prayer_of_day", "prayer_for_me", "prayer_morning_ru", "prayer_evening_ru",
    "saints", "saint_search", "daily_gospel", "ask_question", "photo_icon",
    "photo_church", "find_church", "sacr_ispoved", "sacr_prichaschenie",
    "library", "favorites", "profile_patron_prayer",
}

FUNNEL_TRACK_BY_TARGET = {
    "prayers": "prayer", "prayer_evening_ru": "prayer", "saints": "saint",
    "ask_question": "support", "photo_icon": "icon", "photo_church": "icon",
    "sacr_ispoved": "confession", "sacraments": "confession",
    "daily_gospel": "gospel", "library": "gospel", "find_church": "church",
}

NURTURE_DAY_OFFSETS = (1, 2, 3, 5, 7)
NURTURE_MESSAGES = {
    "prayer": [
        ("🙏 День 1. Выберите одну короткую молитву и прочитайте её без спешки. Постоянство важнее объёма.", "prayers"),
        ("🌅 День 2. Попробуйте начать утро с благодарности за три простые вещи.", "prayer_of_day"),
        ("🕯️ День 3. Когда трудно подобрать слова, скажите Богу честно, что сейчас происходит в сердце.", "prayer_for_me"),
        ("⭐ День 5. Сохраните одну молитву в избранное, чтобы она была рядом в нужный момент.", "favorites"),
        ("🌙 День 7. Завершите неделю спокойной вечерней молитвой и отметьте, что изменилось внутри.", "prayer_evening_ru"),
    ],
    "support": [
        ("🕊️ День 1. Сформулируйте один вопрос, который действительно не даёт покоя. Один честный вопрос лучше десяти общих.", "ask_question"),
        ("📖 День 2. Откройте Евангельская мысль и выберите одну мысль, которую можно применить сегодня.", "daily_gospel"),
        ("🙏 День 3. Добавьте к размышлению короткую молитву своими словами.", "prayer_for_me"),
        ("⛪ День 5. Посмотрите практическую памятку о храме или Таинствах.", "sacraments"),
        ("💬 День 7. Расскажите, что оказалось полезным, — это помогает улучшать проект.", "review"),
    ],
    "saint": [
        ("👼 День 1. Найдите святого по своему имени и посмотрите возможные дни памяти.", "saints"),
        ("📅 День 2. Заполните профиль, чтобы помощник мог напоминать о дне ангела.", "profile"),
        ("🙏 День 3. Откройте молитву небесному покровителю.", "profile_patron_prayer"),
        ("📖 День 5. Прочитайте одну историю святого и выберите практический урок для себя.", "saints"),
        ("🕊️ День 7. Поздравьте близкого с именинами или поделитесь с ним помощником.", "invite_friend"),
    ],
    "confession": [
        ("📿 День 1. Спокойно прочитайте памятку об исповеди — без требования вспомнить всё сразу.", "sacr_ispoved"),
        ("📝 День 2. Запишите несколько конкретных поступков, о которых болит совесть, без оправданий и обвинений других.", "sacr_ispoved"),
        ("🙏 День 3. Прочитайте короткую покаянную молитву.", "prayer_pokayanny_kanon"),
        ("⛪ День 5. Уточните расписание исповеди в выбранном храме.", "find_church"),
        ("🕊️ День 7. При личных вопросах подготовки обязательно поговорите со священником своего прихода.", "sacr_ispoved"),
    ],
    "icon": [
        ("📸 День 1. Подготовьте чёткую фотографию иконы целиком, без бликов и сильного наклона.", "photo_icon"),
        ("👼 День 2. После определения образа откройте раздел святых и узнайте дни памяти.", "saints"),
        ("🙏 День 3. Найдите молитву святому или обратитесь своими словами.", "prayers"),
        ("📚 День 5. Прочитайте проверенный материал о символике православных икон.", "library"),
        ("🤝 День 7. Поделитесь функцией с близким, у которого есть неизвестная семейная икона.", "invite_friend"),
    ],
    "gospel": [
        ("📖 День 1. Прочитайте сегодняшний отрывок медленно два раза.", "daily_gospel"),
        ("🕯️ День 2. Выберите одну фразу и подумайте, где она касается вашей жизни.", "daily_gospel"),
        ("🙏 День 3. Завершите чтение короткой молитвой своими словами.", "prayer_for_me"),
        ("📚 День 5. Откройте библиотеку и выберите один материал для спокойного чтения.", "library"),
        ("💬 День 7. Задайте вопрос о том, что осталось непонятным.", "ask_question"),
    ],
    "church": [
        ("⛪ День 1. Найдите ближайший храм и посмотрите расписание на официальной странице прихода.", "find_church"),
        ("🕯️ День 2. Прочитайте короткую памятку о поведении в храме.", "sacraments"),
        ("📖 День 3. Откройте Евангельская мысль перед посещением службы.", "daily_gospel"),
        ("🙏 День 5. Сохраните молитву, которую хотите прочитать в храме.", "prayers"),
        ("🕊️ День 7. Выберите один следующий шаг: служба, беседа со священником или исповедь.", "sacr_ispoved"),
    ],
}


def _funnel_conn():
    return sqlite3.connect(DB_PATH, timeout=15)


def base_channel_source(payload: str) -> str:
    return (payload or "").split("__", 1)[0]


def make_post_source(platform_code: str, msk_now: datetime, hour: int, cta_key: str, variant: str) -> str:
    base = CHANNEL_CTA.get(cta_key, CHANNEL_CTA.get("guidance", ("", "", "ch_guidance")))[2]
    return f"{base}__{platform_code}{msk_now:%y%m%d}{hour:02d}{variant}"


def record_post_experiment(source: str, platform: str, post_key: str, cta_key: str, variant: str):
    try:
        conn = _funnel_conn()
        conn.execute(
            "INSERT OR REPLACE INTO post_experiments (source,platform,post_key,cta_key,variant,created_at) VALUES (?,?,?,?,?,?)",
            (source, platform, post_key, cta_key, variant, datetime.now().isoformat()),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logging.error(f"Funnel experiment write error: {e}")


def track_funnel_event(user_id: int, platform: str, event_name: str, source: str = "", target: str = "", value: str = "", metadata: str = ""):
    try:
        now = datetime.now().isoformat()
        conn = _funnel_conn()
        conn.execute(
            "INSERT INTO funnel_events (user_id,platform,event_name,source,target,value,metadata,created_at) VALUES (?,?,?,?,?,?,?,?)",
            (int(user_id), platform, event_name, source or "", target or "", str(value or ""), str(metadata or "")[:1000], now),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logging.error(f"Funnel event error: {e}")


def touch_funnel_user(user_id: int, platform: str, source: str = "", target: str = "", increment_visit: bool = True):
    now = datetime.now(); referral_code = f"ref_{int(user_id)}"
    try:
        touch_user_session(user_id, platform, source, target)
        conn = db_connect(); row = conn.execute("SELECT visit_count,first_source,first_target,first_seen_at FROM user_funnel_state WHERE user_id=? AND platform=?", (int(user_id), platform)).fetchone()
        if row:
            conn.execute("""UPDATE user_funnel_state SET last_seen_at=?,visit_count=visit_count+?,last_source=CASE WHEN ?<>'' THEN ? ELSE last_source END,last_target=CASE WHEN ?<>'' THEN ? ELSE last_target END,last_source_at=CASE WHEN ?<>'' THEN ? ELSE last_source_at END WHERE user_id=? AND platform=?""", (now.isoformat(), 1 if increment_visit else 0, source, source, target, target, source, now.isoformat(), int(user_id), platform))
            if increment_visit and row[3]:
                age_days = (now - datetime.fromisoformat(row[3])).days
                for threshold in (1,3,7):
                    if age_days >= threshold and not conn.execute("SELECT 1 FROM funnel_events WHERE user_id=? AND platform=? AND event_name=? LIMIT 1", (int(user_id),platform,f"return_d{threshold}")).fetchone():
                        conn.execute("INSERT INTO funnel_events(user_id,platform,event_name,created_at) VALUES (?,?,?,?)", (int(user_id),platform,f"return_d{threshold}",now.isoformat()))
        else:
            conn.execute("""INSERT INTO user_funnel_state(user_id,platform,first_source,first_target,first_seen_at,last_seen_at,visit_count,referral_code,last_source,last_target,last_source_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)""", (int(user_id),platform,source or "",target or "",now.isoformat(),now.isoformat(),1 if increment_visit else 0,referral_code,source or "",target or "",now.isoformat() if source else ""))
        profile = conn.execute("SELECT church_name,birth_date,notifications FROM users WHERE user_id=?", (int(user_id),)).fetchone()
        if profile: conn.execute("UPDATE user_funnel_state SET profile_completed=?,notifications_enabled=? WHERE user_id=? AND platform=?", (1 if (profile[0] or profile[1]) else 0,int(profile[2] or 0),int(user_id),platform))
        conn.commit(); conn.close()
    except Exception as e:
        logging.error(f"Funnel touch error: {e}")



def set_funnel_flag(user_id: int, platform: str, field: str, value: int = 1):
    allowed = {"profile_completed", "notifications_enabled", "review_left", "donation_made"}
    if field not in allowed:
        return
    touch_funnel_user(user_id, platform, increment_visit=False)
    try:
        conn = _funnel_conn()
        conn.execute(
            f"UPDATE user_funnel_state SET {field}=? WHERE user_id=? AND platform=?",
            (int(value), int(user_id), platform),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logging.error(f"Funnel flag error: {e}")


def register_referral(platform: str, referrer_id: int, referred_user_id: int) -> bool:
    if not referrer_id or int(referrer_id) == int(referred_user_id):
        return False
    try:
        conn = _funnel_conn()
        conn.execute(
            "INSERT OR IGNORE INTO referrals (platform,referrer_id,referred_user_id,status,created_at) VALUES (?,?,?,'started',?)",
            (platform, int(referrer_id), int(referred_user_id), datetime.now().isoformat()),
        )
        conn.execute(
            "UPDATE user_funnel_state SET referred_by=? WHERE user_id=? AND platform=?",
            (int(referrer_id), int(referred_user_id), platform),
        )
        conn.commit()
        conn.close()
        track_funnel_event(referred_user_id, platform, "referral_started", source=f"ref_{referrer_id}")
        return True
    except Exception as e:
        logging.error(f"Referral register error: {e}")
        return False


def mark_useful_action(user_id: int, platform: str, action: str, source: str = "") -> int:
    touch_funnel_user(user_id, platform, source, action, increment_visit=False); now = datetime.now(); referrer_id = 0
    try:
        conn = db_connect(); row = conn.execute("SELECT useful_actions,activated_at,first_source,last_source,last_source_at FROM user_funnel_state WHERE user_id=? AND platform=?", (int(user_id),platform)).fetchone()
        if not source and row:
            source = row[2] or ""
            if row[3] and row[4]:
                try:
                    if (now-datetime.fromisoformat(row[4])).total_seconds() <= 7*86400: source = row[3]
                except Exception: pass
        first_activation = bool(row and int(row[0] or 0)==0)
        conn.execute("UPDATE user_funnel_state SET useful_actions=useful_actions+1,activated_at=CASE WHEN activated_at='' THEN ? ELSE activated_at END,last_seen_at=? WHERE user_id=? AND platform=?", (now.isoformat(),now.isoformat(),int(user_id),platform))
        if first_activation:
            ref = conn.execute("SELECT referrer_id,status FROM referrals WHERE platform=? AND referred_user_id=?", (platform,int(user_id))).fetchone()
            if ref and ref[1] != "activated": referrer_id=int(ref[0]); conn.execute("UPDATE referrals SET status='activated',activated_at=? WHERE platform=? AND referred_user_id=?", (now.isoformat(),platform,int(user_id)))
        conn.commit(); conn.close(); track_funnel_event(user_id,platform,"useful_action",source=source,target=action)
        if first_activation: track_funnel_event(user_id,platform,"activated",source=source,target=action)
        return referrer_id
    except Exception as e:
        logging.error(f"Useful action error: {e}"); return 0



def should_send_activation_prompt(user_id: int, platform: str) -> bool:
    try:
        conn = _funnel_conn()
        exists = conn.execute(
            "SELECT 1 FROM funnel_events WHERE user_id=? AND platform=? AND event_name='activation_prompt_sent' LIMIT 1",
            (int(user_id), platform),
        ).fetchone()
        conn.close()
        if exists:
            return False
        track_funnel_event(user_id, platform, "activation_prompt_sent")
        return True
    except Exception:
        return False


def start_nurture_journey(user_id: int, platform: str, track: str):
    if track not in NURTURE_MESSAGES:
        track = "support"
    now = datetime.now()
    next_send = now + timedelta(days=1)
    try:
        conn = _funnel_conn()
        conn.execute(
            """INSERT OR REPLACE INTO nurture_journeys
               (user_id,platform,track,day_index,active,next_send_at,last_sent_at,created_at)
               VALUES (?,?,?,0,1,?,'',?)""",
            (int(user_id), platform, track, next_send.isoformat(), now.isoformat()),
        )
        conn.commit()
        conn.close()
        track_funnel_event(user_id, platform, "nurture_started", target=track)
    except Exception as e:
        logging.error(f"Nurture start error: {e}")


def stop_nurture_journey(user_id: int, platform: str):
    try:
        conn = _funnel_conn()
        conn.execute(
            "UPDATE nurture_journeys SET active=0 WHERE user_id=? AND platform=?",
            (int(user_id), platform),
        )
        conn.commit()
        conn.close()
        track_funnel_event(user_id, platform, "nurture_stopped")
    except Exception as e:
        logging.error(f"Nurture stop error: {e}")


def due_nurture_rows(platform: str):
    try:
        conn = _funnel_conn()
        rows = conn.execute(
            """SELECT user_id,track,day_index FROM nurture_journeys
               WHERE platform=? AND active=1 AND next_send_at<=? ORDER BY next_send_at LIMIT 50""",
            (platform, datetime.now().isoformat()),
        ).fetchall()
        conn.close()
        return rows
    except Exception as e:
        logging.error(f"Nurture read error: {e}")
        return []


def advance_nurture(user_id: int, platform: str, day_index: int):
    next_index = int(day_index) + 1
    now = datetime.now()
    try:
        conn = _funnel_conn()
        if next_index >= len(NURTURE_DAY_OFFSETS):
            conn.execute(
                "UPDATE nurture_journeys SET active=0,last_sent_at=? WHERE user_id=? AND platform=?",
                (now.isoformat(), int(user_id), platform),
            )
        else:
            previous_day = NURTURE_DAY_OFFSETS[int(day_index)]
            next_day = NURTURE_DAY_OFFSETS[next_index]
            next_send = now + timedelta(days=max(1, next_day - previous_day))
            conn.execute(
                "UPDATE nurture_journeys SET day_index=?,next_send_at=?,last_sent_at=? WHERE user_id=? AND platform=?",
                (next_index, next_send.isoformat(), now.isoformat(), int(user_id), platform),
            )
        conn.commit()
        conn.close()
    except Exception as e:
        logging.error(f"Nurture advance error: {e}")


def funnel_report_text(platform: str, days: int = 7) -> str:
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()
    try:
        conn = _funnel_conn()
        clicks = conn.execute(
            "SELECT COUNT(*),COUNT(DISTINCT user_id) FROM funnel_events WHERE platform=? AND event_name='channel_click' AND created_at>=?",
            (platform, cutoff),
        ).fetchone() or (0, 0)
        activated = conn.execute(
            "SELECT COUNT(*) FROM user_funnel_state WHERE platform=? AND activated_at>=?",
            (platform, cutoff),
        ).fetchone()[0]
        returning = conn.execute(
            "SELECT COUNT(*) FROM user_funnel_state WHERE platform=? AND visit_count>=2 AND last_seen_at>=?",
            (platform, cutoff),
        ).fetchone()[0]
        return_d1 = conn.execute("SELECT COUNT(DISTINCT user_id) FROM funnel_events WHERE platform=? AND event_name='return_d1' AND created_at>=?", (platform, cutoff)).fetchone()[0]
        return_d3 = conn.execute("SELECT COUNT(DISTINCT user_id) FROM funnel_events WHERE platform=? AND event_name='return_d3' AND created_at>=?", (platform, cutoff)).fetchone()[0]
        return_d7 = conn.execute("SELECT COUNT(DISTINCT user_id) FROM funnel_events WHERE platform=? AND event_name='return_d7' AND created_at>=?", (platform, cutoff)).fetchone()[0]
        profiles = conn.execute(
            "SELECT COUNT(*) FROM user_funnel_state WHERE platform=? AND profile_completed=1 AND last_seen_at>=?",
            (platform, cutoff),
        ).fetchone()[0]
        notifications = conn.execute(
            "SELECT COUNT(*) FROM user_funnel_state WHERE platform=? AND notifications_enabled=1 AND last_seen_at>=?",
            (platform, cutoff),
        ).fetchone()[0]
        reviews = conn.execute(
            "SELECT COUNT(*) FROM user_funnel_state WHERE platform=? AND review_left=1 AND last_seen_at>=?",
            (platform, cutoff),
        ).fetchone()[0]
        donations = conn.execute(
            "SELECT COUNT(*) FROM user_funnel_state WHERE platform=? AND donation_made=1 AND last_seen_at>=?",
            (platform, cutoff),
        ).fetchone()[0]
        referrals = conn.execute(
            "SELECT COUNT(*) FROM referrals WHERE platform=? AND status='activated' AND activated_at>=?",
            (platform, cutoff),
        ).fetchone()[0]
        nurture = conn.execute(
            "SELECT COUNT(*) FROM nurture_journeys WHERE platform=? AND active=1",
            (platform,),
        ).fetchone()[0]
        top = conn.execute(
            """SELECT CASE WHEN instr(source,'__')>0 THEN substr(source,1,instr(source,'__')-1) ELSE source END AS base_source,
                      COUNT(*),COUNT(DISTINCT user_id)
               FROM funnel_events WHERE platform=? AND event_name='channel_click' AND created_at>=?
               GROUP BY base_source ORDER BY COUNT(*) DESC LIMIT 8""",
            (platform, cutoff),
        ).fetchall()
        variants = conn.execute(
            """SELECT p.cta_key,p.variant,
                      SUM(CASE WHEN e.event_name='channel_click' THEN 1 ELSE 0 END) AS clicks,
                      SUM(CASE WHEN e.event_name='activated' THEN 1 ELSE 0 END) AS activations
               FROM post_experiments p
               LEFT JOIN funnel_events e ON e.source=p.source AND e.platform=p.platform AND e.created_at>=?
               WHERE p.platform=?
               GROUP BY p.cta_key,p.variant
               HAVING clicks>0
               ORDER BY clicks DESC LIMIT 10""",
            (cutoff, platform),
        ).fetchall()
        conn.close()
        unique_clicks = int(clicks[1] or 0)
        activation_rate = (activated / unique_clicks * 100) if unique_clicks else 0
        return_rate = (returning / max(activated, 1) * 100) if activated else 0
        top_text = "\n".join(f"• {src}: {count} переходов / {users} чел." for src, count, users in top) or "• Данных пока недостаточно"
        variant_lines = []
        for cta_key, variant, v_clicks, v_activations in variants:
            rate = (int(v_activations or 0) / int(v_clicks or 1) * 100) if v_clicks else 0
            variant_lines.append(f"• {cta_key} {variant.upper()}: {int(v_clicks or 0)} → {int(v_activations or 0)} ({rate:.1f}%)")
        variant_text = "\n".join(variant_lines) or "• Данных пока недостаточно"
        return (
            f"📊 Воронка «С верой» — {platform}, {days} дней\n\n"
            f"Переходы из канала: {int(clicks[0] or 0)}\n"
            f"Уникальные пользователи: {unique_clicks}\n"
            f"Первое полезное действие: {activated} ({activation_rate:.1f}%)\n"
            f"Вернулись повторно: {returning} ({return_rate:.1f}%)\n"
            f"Возврат D1 / D3 / D7: {return_d1} / {return_d3} / {return_d7}\n"
            f"Заполнили профиль: {profiles}\n"
            f"Включили уведомления: {notifications}\n"
            f"Активные 7-дневные серии: {nurture}\n"
            f"Оставили отзыв: {reviews}\n"
            f"Успешные рекомендации: {referrals}\n"
            f"Сделали пожертвование: {donations}\n\n"
            f"Лучшие рубрики:\n{top_text}\n\n"
            f"A/B: переход → первое действие:\n{variant_text}"
        )
    except Exception as e:
        logging.error(f"Funnel report error: {e}")
        return f"⚠️ Не удалось построить отчёт: {e}"


def referral_reward_text() -> str:
    return (
        "🎁 Молитвенная подборка за близких\n\n"
        "Господи, сохрани моих родных и близких. Даруй им здравие, мир, мудрость и защиту от всякого зла. "
        "Помоги нам быть терпеливыми друг к другу, прощать и поддерживать в трудные дни. "
        "Укрепи нашу семью в любви и вере. Аминь."
    )



def has_referral_reward(user_id: int, platform: str) -> bool:
    try:
        conn = _funnel_conn()
        row = conn.execute(
            "SELECT 1 FROM referrals WHERE platform=? AND referrer_id=? AND status='activated' LIMIT 1",
            (platform, int(user_id)),
        ).fetchone()
        conn.close()
        return bool(row)
    except Exception:
        return False


def record_topic_vote(user_id: int, platform: str, topic: str):
    week_key = datetime.now().strftime("%G-W%V")
    conn = db_connect(); conn.execute("INSERT OR REPLACE INTO topic_votes(platform,week_key,user_id,topic,updated_at) VALUES (?,?,?,?,?)", (platform,week_key,int(user_id),topic,datetime.now().isoformat())); conn.commit(); conn.close()
    track_funnel_event(user_id,platform,"interactive_vote",value=topic)

def top_interactive_topic(platform: str) -> str:
    try:
        week_key=datetime.now().strftime("%G-W%V"); conn=db_connect(); row=conn.execute("SELECT topic,COUNT(*) FROM topic_votes WHERE platform=? AND week_key=? GROUP BY topic ORDER BY COUNT(*) DESC,topic LIMIT 1", (platform,week_key)).fetchone(); conn.close(); return row[0] if row else ""
    except Exception as e:
        logging.error(f"Interactive vote read error: {e}"); return ""



def interactive_topic_prompt(topic: str) -> str:
    return {
        "prayer": "По выбору читателей подробно и понятно расскажи, как начать регулярную домашнюю молитву без перегруза и чувства вины. Дай три реалистичных шага.",
        "confession": "По выбору читателей дай бережную памятку о первой исповеди: как подготовиться, чего не бояться и что уточнить у священника.",
        "saint": "По выбору читателей объясни, как искать святого по имени и понимать день ангела, не обещая точность без церковного календаря и разговора со священником.",
        "support": "По выбору читателей разберись с тревогой и унынием: дай три бережных духовных и бытовых шага и напомни, когда нужна профессиональная помощь.",
    }.get(topic, "Ответь на самый частый практический вопрос начинающего о вере и предложи три понятных шага.")


def weekly_report_due(platform: str) -> bool:
    week_key = datetime.now().strftime("%G-W%V")
    try:
        conn = _funnel_conn()
        row = conn.execute(
            "SELECT 1 FROM funnel_events WHERE user_id=? AND platform=? AND event_name='weekly_report_sent' AND value=? LIMIT 1",
            (int(OWNER_ID), platform, week_key),
        ).fetchone()
        conn.close()
        return not bool(row)
    except Exception:
        return False


def mark_weekly_report_sent(platform: str):
    track_funnel_event(OWNER_ID, platform, "weekly_report_sent", value=datetime.now().strftime("%G-W%V"))

def latest_public_review_excerpt() -> str:
    try:
        conn=db_connect(); row=conn.execute("SELECT id,review_text FROM user_reviews WHERE publish_consent=1 AND public_approved=1 AND COALESCE(published_at,'')='' ORDER BY id LIMIT 1").fetchone()
        if not row: conn.close(); return ""
        conn.execute("UPDATE user_reviews SET published_at=? WHERE id=?", (datetime.now().isoformat(),int(row[0]))); conn.commit(); conn.close(); return (row[1] or "").strip()[:700]
    except Exception as e:
        logging.error(f"Public review read error: {e}"); return ""


def get_user(user_id, username="", first_name=""):
    conn = db_connect()
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO users (user_id, username, first_name, registered_at, notifications) VALUES (?,?,?,?,0)",
              (user_id, username, first_name, datetime.now().isoformat()))
    c.execute("INSERT OR IGNORE INTO limits (user_id, last_reset) VALUES (?,?)",
              (user_id, datetime.now().date().isoformat()))
    conn.commit()
    c.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
    row = c.fetchone()
    conn.close()
    if row:
        return {
            "user_id": row[0], "username": row[1], "first_name": row[2],
            "church_name": row[3], "birth_date": row[4], "angel_day": row[5],
            "remind_days": row[6], "step": row[7], "onboarded": row[8],
            "notifications": row[10] if len(row) > 10 else 0
        }
    return {}

def set_step(user_id, step):
    conn = db_connect()
    c = conn.cursor()
    c.execute("UPDATE users SET step=? WHERE user_id=?", (step, user_id))
    conn.commit()
    conn.close()

def set_onboarded(user_id):
    conn = db_connect()
    c = conn.cursor()
    c.execute("UPDATE users SET onboarded=1 WHERE user_id=?", (user_id,))
    conn.commit()
    conn.close()

def save_profile(user_id, church_name, birth_date, angel_day):
    conn = db_connect()
    c = conn.cursor()
    c.execute("UPDATE users SET church_name=?, birth_date=?, angel_day=?, onboarded=1 WHERE user_id=?",
              (church_name, birth_date, angel_day, user_id))
    conn.commit()
    conn.close()
    set_funnel_flag(user_id, "Telegram", "profile_completed", 1)

def get_subscription(user_id):
    conn = db_connect()
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO subscriptions (user_id) VALUES (?)", (user_id,))
    conn.commit()
    c.execute("SELECT plan, sub_end FROM subscriptions WHERE user_id=?", (user_id,))
    row = c.fetchone()
    conn.close()
    if row and row[0]:
        if row[1] and datetime.fromisoformat(row[1]) > datetime.now():
            return row[0], row[1]
    return "", ""

def get_limits(user_id):
    conn = db_connect()
    c = conn.cursor()
    c.execute("SELECT ai_requests, photo_requests, last_reset FROM limits WHERE user_id=?", (user_id,))
    row = c.fetchone()
    if row:
        last_reset = row[2] or ""
        today = datetime.now().date().isoformat()
        if last_reset != today:
            c.execute("UPDATE limits SET ai_requests=0, photo_requests=0, last_reset=? WHERE user_id=?",
                      (today, user_id))
            conn.commit()
            conn.close()
            return {"ai_requests": 0, "photo_requests": 0}
        conn.close()
        return {"ai_requests": row[0], "photo_requests": row[1]}
    conn.close()
    return {"ai_requests": 0, "photo_requests": 0}

def increment_limit(user_id, field):
    conn = db_connect()
    c = conn.cursor()
    c.execute(f"UPDATE limits SET {field}={field}+1 WHERE user_id=?", (user_id,))
    conn.commit()
    conn.close()

def save_favorite(user_id, title, content):
    conn = db_connect()
    c = conn.cursor()
    c.execute("INSERT INTO favorites (user_id, title, content, saved_at) VALUES (?,?,?,?)",
              (user_id, title, content, datetime.now().isoformat()))
    conn.commit()
    conn.close()

def get_favorites(user_id):
    conn = db_connect()
    c = conn.cursor()
    c.execute("SELECT id, title, saved_at FROM favorites WHERE user_id=? ORDER BY saved_at DESC LIMIT 20", (user_id,))
    rows = c.fetchall()
    conn.close()
    return rows


def create_review_record(user_id, chat_id, username, first_name, review_text):
    conn = db_connect()
    c = conn.cursor()
    c.execute(
        """INSERT INTO user_reviews
           (user_id, chat_id, username, first_name, review_text, status, created_at)
           VALUES (?,?,?,?,?,'new',?)""",
        (user_id, chat_id, username or "", first_name or "", review_text.strip(), datetime.now().isoformat()),
    )
    review_id = c.lastrowid
    conn.commit()
    conn.close()
    return review_id


def get_review_record(review_id):
    conn = db_connect()
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM user_reviews WHERE id=?", (int(review_id),)).fetchone()
    conn.close()
    return dict(row) if row else None


def update_review_record(review_id, status, owner_reply="", handled_by="Владелец"):
    replied_at = datetime.now().strftime("%d.%m.%Y %H:%M")
    conn = db_connect()
    conn.execute(
        """UPDATE user_reviews
           SET status=?, owner_reply=?, replied_at=?, handled_by=?
           WHERE id=?""",
        (status, owner_reply or "", replied_at, handled_by, int(review_id)),
    )
    conn.commit()
    conn.close()
    return replied_at


def record_channel_click(user_id, source, target):
    try:
        conn = db_connect()
        conn.execute(
            "INSERT INTO channel_clicks (user_id,source,target,clicked_at) VALUES (?,?,?,?)",
            (user_id, source, target, datetime.now().isoformat()),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logging.error(f"Не удалось записать переход из канала: {e}")

# ========== GOOGLE SHEETS ==========
def get_sheet():
    try:
        scopes = ["https://www.googleapis.com/auth/spreadsheets"]
        creds  = Credentials.from_service_account_file(CREDENTIALS_FILE, scopes=scopes)
        client = gspread.authorize(creds)
        sp     = client.open_by_key(SPREADSHEET_ID)
        try:
            sheet = sp.worksheet("ВераТГ")
        except Exception:
            sheet = sp.add_worksheet(title="ВераТГ", rows=1000, cols=10)
            sheet.insert_row(["ID","Username","Имя","Церковное имя","Дата рождения","День ангела","Тариф","Дата регистрации","Запросов AI","Последняя активность","Отзывов","Пожертвований"], 1)
        return sheet
    except Exception as e:
        logging.error(f"Google Sheets ошибка: {e}")
        return None

def sheets_add_user(user_id, username, first_name):
    try:
        sheet = get_sheet()
        if not sheet:
            return
        col = sheet.col_values(1)
        if str(user_id) in col:
            return
        sheet.append_row([
            str(user_id),
            f"@{username}" if username else "—",
            first_name or "—",
            "—", "—", "—", "Бесплатный",
            datetime.now().strftime("%d.%m.%Y %H:%M"),
            "0",
            datetime.now().strftime("%d.%m.%Y %H:%M"),
            "0",
            "0"
        ])
    except Exception as e:
        logging.error(f"Sheets add_user: {e}")

def sheets_update_activity(user_id):
    try:
        sheet = get_sheet()
        if not sheet:
            return
        col = sheet.col_values(1)
        if str(user_id) in col:
            row = col.index(str(user_id)) + 1
            lim = get_limits(user_id)
            sheet.update_cell(row, 9,  str(lim["ai_requests"]))
            sheet.update_cell(row, 10, datetime.now().strftime("%d.%m.%Y %H:%M"))
    except Exception as e:
        logging.error(f"Sheets update_activity: {e}")

def sheets_update_profile(user_id, church_name, birth_date, angel_day):
    try:
        sheet = get_sheet()
        if not sheet:
            return
        col = sheet.col_values(1)
        if str(user_id) in col:
            row = col.index(str(user_id)) + 1
            sheet.update_cell(row, 4, church_name or "—")
            sheet.update_cell(row, 5, birth_date  or "—")
            sheet.update_cell(row, 6, angel_day   or "—")
    except Exception as e:
        logging.error(f"Sheets update_profile: {e}")

# ========== ПРАВОСЛАВНЫЙ КАЛЕНДАРЬ ==========
# Великие праздники (фиксированные)
FIXED_FEASTS = {
    "14.01": "Обрезание Господне, память свт. Василия Великого",
    "07.01": "Рождество Христово ☀️",
    "19.01": "Богоявление (Крещение Господне) 💧",
    "15.02": "Сретение Господне",
    "07.04": "Благовещение Пресвятой Богородицы",
    "21.05": "Апостола и евангелиста Иоанна Богослова",
    "22.05": "Перенесение мощей святителя Николая Чудотворца",
    "07.07": "Рождество Иоанна Предтечи",
    "12.07": "Апостолов Петра и Павла",
    "19.08": "Преображение Господне ✨",
    "28.08": "Успение Пресвятой Богородицы",
    "11.09": "Усекновение главы Иоанна Предтечи",
    "21.09": "Рождество Пресвятой Богородицы",
    "27.09": "Воздвижение Честного Животворящего Креста Господня ✝️",
    "14.10": "Покров Пресвятой Богородицы 🕊️",
    "04.11": "Казанской иконы Пресвятой Богородицы",
    "19.12": "Святителя Николая Чудотворца 🌟",
    "04.12": "Введение во храм Пресвятой Богородицы",
}

# Посты
FASTS = {
    "Великий пост": "Период подготовки к Пасхе. Конкретную меру пищевого поста, особенно при заболеваниях, беременности, возрасте или тяжёлой работе, следует согласовать со священником и врачом.",
    "Петров пост": "Начинается после Недели всех святых и завершается перед праздником апостолов Петра и Павла. Устав и послабления лучше уточнять по календарю своего прихода.",
    "Успенский пост": "Проходит 14–27 августа по гражданскому календарю. Это время молитвы, покаяния и милосердия; конкретную пищевую меру уточняйте в своём приходе.",
    "Рождественский пост": "Проходит 28 ноября – 6 января. Правила питания различаются по дням и обстоятельствам человека; бот не назначает индивидуальную меру поста.",
    "Среда и пятница": "Традиционные постные дни. Исключения зависят от церковного календаря, состояния здоровья и благословения духовника.",
}

PASTORAL_DISCLAIMER = "ℹ️ Это общая справочная памятка. Порядок подготовки и приходскую практику уточняйте у священника своего храма."

PASCHA_GUIDE_TEXT = (
    "🥚 ПАСХА — ВОСКРЕСЕНИЕ ХРИСТОВО\n\n"
    "Пасха — главный праздник церковного года, свидетельство победы жизни над смертью. "
    "Дата праздника меняется ежегодно.\n\n"
    "Великий пост — время молитвы, покаяния, милосердия и подготовки к встрече Пасхи. "
    "Единая строгая схема питания подходит не всем: меру поста уточняют с учётом здоровья и благословения священника.\n\n"
    "Расписание исповеди, Причастия, освящения пасхальной пищи и ночной службы различается по храмам. "
    "Перед поездкой проверьте расписание своего прихода.\n\n"
    "Пасхальное приветствие: «Христос Воскресе!» — «Воистину Воскресе!»"
)

THEOPHANY_GUIDE_TEXT = (
    "💧 КРЕЩЕНИЕ ГОСПОДНЕ — БОГОЯВЛЕНИЕ\n\n"
    "19 января Церковь вспоминает Крещение Иисуса Христа в Иордане. В храмах совершается Великое освящение воды; "
    "точное время нужно уточнить в расписании конкретного прихода.\n\n"
    "Святую воду хранят благоговейно и употребляют с молитвой. При возникновении практических вопросов лучше обратиться в свой храм.\n\n"
    "Купание в проруби — народная традиция, а не обязательный церковный обряд и не замена покаянию. "
    "Не рискуйте здоровьем: при любых сомнениях откажитесь от купания и проконсультируйтесь с врачом."
)


# Святые по именам (для дня ангела)
SAINTS_BY_NAME = {'абрам': [('22.10', 'прп. Авраамия Ростовского')],
 'авраам': [('22.10', 'прп. Авраамия Ростовского'), ('09.10', 'прп. Авраамия Затворника')],
 'агафья': [('18.02', 'мц. Агафии Панормской')],
 'агния': [('21.01', 'мц. Агнии Римской')],
 'адриан': [('26.08', 'мч. Адриана и Наталии')],
 'александр': [('06.06', 'мч. Александра'),
               ('12.06', 'блгв. кн. Александра Невского'),
               ('12.09', 'блгв. кн. Александра Невского'),
               ('23.11', 'блгв. кн. Александра Невского')],
 'алексей': [('30.03', 'прп. Алексия, человека Божия'), ('25.04', 'сщмч. Алексия'), ('20.09', 'блгв. кн. Алексия')],
 'алла': [('26.03', 'мц. Аллы Готфской')],
 'амвросий': [('20.12', 'свт. Амвросия Медиоланского'), ('10.10', 'прп. Амвросия Оптинского')],
 'анастасия': [('04.01', 'мц. Анастасии Римляныни'), ('22.12', 'вмц. Анастасии Узорешительницы')],
 'анатолий': [('23.07', 'прп. Анатолия Оптинского'), ('15.08', 'мч. Анатолия')],
 'андрей': [('13.12', 'ап. Андрея Первозванного')],
 'анна': [('03.02', 'прп. Анны'), ('22.07', 'равноап. Марии Магдалины'), ('07.08', 'прп. Анны')],
 'антон': [('17.01', 'прп. Антония Великого'), ('23.07', 'прп. Антония Печерского')],
 'антоний': [('17.01', 'прп. Антония Великого'), ('23.07', 'прп. Антония Печерского')],
 'антонина': [('01.03', 'мц. Антонины'), ('10.06', 'мц. Антонины')],
 'аркадий': [('26.02', 'прп. Аркадия Новоторжского')],
 'арсений': [('08.05', 'свт. Арсения Великого'), ('24.07', 'прп. Арсения Коневского')],
 'артемий': [('02.11', 'мч. Артемия Антиохийского')],
 'артём': [('20.10', 'ап. Артемы'), ('02.11', 'мч. Артемия')],
 'борис': [('06.08', 'блгв. кн. Бориса и Глеба'), ('24.07', 'блгв. кн. Бориса')],
 'вадим': [('22.04', 'прмч. Вадима Персидского')],
 'валентин': [('12.08', 'мч. Валентина'), ('19.07', 'мч. Валентина Доростольского')],
 'валентина': [('10.02', 'мц. Валентины'), ('07.08', 'мц. Валентины')],
 'валерий': [('07.03', 'мч. Валерия'), ('20.11', 'мч. Валерия')],
 'валерия': [('07.06', 'мц. Валерии')],
 'варвара': [('17.12', 'вмц. Варвары Илиопольской')],
 'варлаам': [('19.11', 'прп. Варлаама Хутынского')],
 'василий': [('14.01', 'свт. Василия Великого'), ('13.03', 'мч. Василия'), ('04.04', 'прп. Василия')],
 'василиса': [('15.01', 'мц. Василисы'), ('04.04', 'мц. Василисы')],
 'вениамин': [('13.08', 'сщмч. Вениамина Петроградского')],
 'вера': [('30.09', 'мц. Веры, Надежды, Любови и матери их Софии')],
 'виктор': [('11.11', 'мч. Виктора'), ('05.03', 'мч. Виктора')],
 'виктория': [('23.12', 'мц. Виктории'), ('11.11', 'мц. Виктории')],
 'виталий': [('04.05', 'мч. Виталия Медиоланского')],
 'владимир': [('28.07', 'равноап. кн. Владимира')],
 'вячеслав': [('04.03', 'блгв. кн. Вячеслава Чешского')],
 'гавриил': [('26.07', 'арх. Гавриила'), ('08.04', 'арх. Гавриила')],
 'галина': [('29.03', 'мц. Галины')],
 'геннадий': [('17.12', 'свт. Геннадия Новгородского')],
 'георгий': [('06.05', 'вмч. Георгия Победоносца'), ('26.11', 'освящение храма вмч. Георгия')],
 'герасим': [('17.03', 'прп. Герасима Иорданского')],
 'глеб': [('06.08', 'блгв. кн. Бориса и Глеба'), ('05.09', 'блгв. кн. Глеба')],
 'григорий': [('12.01', 'свт. Григория Нисского'), ('25.01', 'свт. Григория Богослова')],
 'давид': [('01.03', 'прп. Давида'), ('06.03', 'прп. Давида Солунского')],
 'даниил': [('17.12', 'прп. Даниила Столпника'), ('23.12', 'блгв. кн. Даниила Московского')],
 'дарья': [('01.04', 'мц. Дарии')],
 'денис': [('16.10', 'сщмч. Дионисия Ареопагита')],
 'дима': [('08.11', 'вмч. Димитрия Солунского'), ('01.06', 'блгв. кн. Димитрия Донского')],
 'дионисий': [('16.10', 'сщмч. Дионисия Ареопагита')],
 'дмитрий': [('08.11', 'вмч. Димитрия Солунского'), ('01.06', 'блгв. кн. Димитрия Донского')],
 'домна': [('14.01', 'мц. Домны Никомидийской')],
 'евгений': [('26.12', 'мч. Евгения'), ('20.11', 'мч. Евгения Мелитинского')],
 'евгения': [('24.12', 'прмц. Евгении')],
 'евдокия': [('14.03', 'прмц. Евдокии'), ('04.08', 'прав. Евдокии')],
 'екатерина': [('07.12', 'вмц. Екатерины')],
 'елена': [('03.06', 'равноап. царицы Елены'), ('24.07', 'равноап. Елены')],
 'елизавета': [('05.09', 'прмц. Елисаветы Феодоровны'), ('18.09', 'прмц. Елисаветы')],
 'ефим': [('20.01', 'прп. Евфимия Великого')],
 'ефрем': [('10.02', 'прп. Ефрема Сирина')],
 'зинаида': [('23.10', 'мц. Зинаиды')],
 'зиновий': [('13.11', 'мч. Зиновия и Зиновии')],
 'зоя': [('13.02', 'мц. Зои Вифлеемской'), ('02.05', 'мц. Зои')],
 'иван': [('20.01', 'Собор Иоанна Предтечи'),
          ('07.07', 'Рождество Иоанна Предтечи'),
          ('11.09', 'Усекновение главы Иоанна Предтечи')],
 'илия': [('02.08', 'прор. Илии Фесвитянина')],
 'илья': [('02.08', 'прор. Илии Фесвитянина')],
 'иннокентий': [('26.11', 'свт. Иннокентия Иркутского'), ('06.10', 'свт. Иннокентия Московского')],
 'иоанн': [('20.01', 'Собор Иоанна Предтечи'),
           ('07.07', 'Рождество Иоанна Предтечи'),
           ('11.09', 'Усекновение главы Иоанна Предтечи')],
 'иосиф': [('19.09', 'прав. Иосифа Прекрасного'), ('11.04', 'прп. Иосифа Волоцкого')],
 'ирина': [('29.04', 'мц. Ирины'), ('18.05', 'мц. Ирины')],
 'капитолина': [('27.10', 'мц. Капитолины')],
 'кирилл': [('27.02', 'равноап. Кирилла, учителя Словенского')],
 'клавдия': [('20.03', 'мц. Клавдии')],
 'климент': [('25.11', 'сщмч. Климента Римского')],
 'константин': [('03.06', 'равноап. царя Константина')],
 'кристина': [('24.07', 'вмц. Христины')],
 'ксения': [('06.02', 'блж. Ксении Петербургской'), ('24.01', 'мц. Ксении')],
 'кузьма': [('14.07', 'бессрр. Космы и Дамиана'), ('14.11', 'бессрр. Космы и Дамиана')],
 'лариса': [('08.04', 'мц. Ларисы')],
 'лев': [('05.03', 'свт. Льва Катанского'), ('18.02', 'свт. Льва Великого')],
 'леонид': [('16.04', 'мч. Леонида')],
 'лидия': [('05.04', 'мц. Лидии')],
 'лука': [('31.10', 'ап. Луки'), ('11.06', 'свт. Луки Крымского')],
 'любовь': [('30.09', 'мц. Веры, Надежды, Любови')],
 'людмила': [('29.09', 'мц. кн. Людмилы Чешской')],
 'макар': [('19.01', 'прп. Макария Великого')],
 'макарий': [('19.01', 'прп. Макария Великого')],
 'максим': [('13.08', 'прп. Максима Исповедника'), ('11.11', 'блж. Максима Московского')],
 'маргарита': [('30.07', 'вмц. Марины (Маргариты)')],
 'марина': [('30.07', 'вмц. Марины')],
 'мария': [('22.07', 'равноап. Марии Магдалины'), ('17.09', 'мц. Марии'), ('26.01', 'прп. Марии')],
 'марк': [('25.04', 'ап. Марка')],
 'марфа': [('04.07', 'прп. Марфы')],
 'матрона': [('02.05', 'блж. Матроны Московской'), ('09.08', 'мц. Матроны')],
 'мефодий': [('11.05', 'равноап. Мефодия, учителя Словенского')],
 'митрофан': [('23.11', 'свт. Митрофана Воронежского')],
 'михаил': [('21.11', 'Собор Архистратига Михаила'), ('12.07', 'ап. Михаила')],
 'моисей': [('04.09', 'прп. Моисея Угрина')],
 'надежда': [('30.09', 'мц. Надежды')],
 'наталья': [('08.09', 'мц. Наталии'), ('26.08', 'мц. Наталии')],
 'никита': [('15.09', 'вмч. Никиты Готфского')],
 'николай': [('22.05', 'свт. Николая, архиеп. Мирликийского'), ('19.12', 'свт. Николая Чудотворца')],
 'нина': [('27.01', 'равноап. Нины, просветительницы Грузии')],
 'нонна': [('05.08', 'прав. Нонны')],
 'оксана': [('06.10', 'прп. Ксанфиппы'), ('24.01', 'мц. Ксении')],
 'олег': [('03.10', 'блгв. кн. Олега Брянского')],
 'ольга': [('24.07', 'равноап. кн. Ольги')],
 'павел': [('12.07', 'ап. Петра и Павла'), ('03.02', 'прп. Павла')],
 'петр': [('12.07', 'ап. Петра и Павла'), ('04.07', 'блгв. кн. Петра')],
 'платон': [('18.11', 'мч. Платона Анкирского')],
 'прохор': [('09.04', 'прп. Прохора Лебедника'), ('28.01', 'прп. Прохора Печерского')],
 'пётр': [('12.07', 'ап. Петра и Павла'), ('04.07', 'блгв. кн. Петра')],
 'раиса': [('05.09', 'мц. Раисы Александрийской')],
 'роман': [('01.10', 'прп. Романа Сладкопевца'), ('08.08', 'мч. Романа')],
 'светлана': [('26.02', 'мц. Фотины (Светланы)')],
 'семён': [('03.02', 'прп. Симеона Богоприимца'), ('14.09', 'прп. Симеона Столпника')],
 'серафима': [('29.07', 'прмц. Серафимы')],
 'сергей': [('08.10', 'прп. Сергия Радонежского'), ('20.09', 'мч. Сергия')],
 'сергий': [('08.10', 'прп. Сергия Радонежского')],
 'софия': [('30.09', 'мц. Софии'), ('17.09', 'мц. Веры, Надежды, Любови и матери их Софии')],
 'степан': [('09.01', 'архидиак. Стефана первомученика')],
 'стефан': [('09.01', 'архидиак. Стефана первомученика')],
 'тамара': [('01.05', 'блгв. царицы Тамары Грузинской')],
 'татьяна': [('25.01', 'мц. Татианы')],
 'тимофей': [('04.02', 'ап. Тимофея')],
 'тимур': [('02.06', 'прп. Тимофея')],
 'тихон': [('09.10', 'свт. Тихона Задонского'), ('29.06', 'свт. Тихона Амафунтского')],
 'трофим': [('19.09', 'мч. Трофима')],
 'ульяна': [('15.01', 'мц. Иулиании Никомидийской')],
 'федор': [('08.03', 'вмч. Феодора Тирона')],
 'феодор': [('08.03', 'вмч. Феодора Тирона'), ('09.06', 'прп. Феодора Освященного')],
 'феодосий': [('11.01', 'прп. Феодосия Великого'), ('03.05', 'прп. Феодосия Печерского')],
 'филипп': [('27.11', 'ап. Филиппа'), ('22.01', 'свт. Филиппа Московского')],
 'фома': [('19.10', 'ап. Фомы')],
 'фёдор': [('08.03', 'вмч. Феодора Тирона')],
 'харитина': [('05.10', 'мц. Харитины')],
 'христина': [('24.07', 'вмц. Христины Тирской')],
 'юлия': [('29.07', 'мц. Иулии'), ('16.04', 'мц. Иулии')],
 'яков': [('05.11', 'ап. Иакова Зеведеева'), ('13.01', 'прп. Иакова Постника')],
 'яна': [('24.06', 'мц. Иоанны'), ('20.01', 'Собор Иоанна Предтечи')]}



def get_todays_saints() -> list:
    """Возвращает список имён именинников сегодня"""
    today = datetime.now().strftime("%d.%m")
    result = []
    for name, days in SAINTS_BY_NAME.items():
        for day_str, saint in days:
            if day_str == today:
                result.append((name.capitalize(), saint))
    return result

def get_todays_feast() -> str:
    today = datetime.now().strftime("%d.%m")
    return FIXED_FEASTS.get(today, "")


# V4: content safety layer. Liturgical readings and individual fasting rules are
# never invented by AI. The assistant gives a verified verse/reflection and a
# cautious calendar reminder; exact parish practice is confirmed with a priest.
GOSPEL_REFLECTIONS = [
    ("Мф. 7:7", "Просите — и дано будет вам; ищите — и найдёте; стучите — и отворят вам.", "Молитва начинается с честного обращения к Богу. Сегодня можно назвать Ему одну конкретную просьбу и постараться сделать один добрый шаг самому."),
    ("Мф. 11:28", "Придите ко Мне все труждающиеся и обременённые, и Я успокою вас.", "Христос не требует сначала стать безупречным. К Нему можно прийти именно с усталостью, тревогой и растерянностью."),
    ("Мф. 5:9", "Блаженны миротворцы, ибо они будут наречены сынами Божиими.", "Миротворчество — не слабость, а отказ умножать вражду. Полезно начать с одного спокойного разговора или примирительного сообщения."),
    ("Мф. 6:34", "Не заботьтесь о завтрашнем дне, ибо завтрашний сам будет заботиться о своём.", "Эти слова не призывают к беспечности. Они возвращают нас к тому доброму делу, которое возможно совершить сегодня."),
    ("Лк. 6:31", "И как хотите, чтобы с вами поступали люди, так и вы поступайте с ними.", "Перед важным разговором стоит спросить себя: какого отношения я жду к себе — и могу ли первым проявить его к другому?"),
    ("Лк. 18:13", "Боже! будь милостив ко мне, грешнику!", "Короткая молитва мытаря учит не оправдываться и не сравнивать себя с другими, а просить милости с надеждой."),
    ("Ин. 8:12", "Я свет миру; кто последует за Мною, тот не будет ходить во тьме.", "Следовать за Христом — значит выбирать правду, милость и ответственность даже тогда, когда это труднее удобного решения."),
    ("Ин. 13:35", "По тому узнают все, что вы Мои ученики, если будете иметь любовь между собою.", "Вера становится заметной не громкими словами, а терпением, заботой и уважением к человеку рядом."),
    ("Ин. 14:27", "Мир оставляю вам, мир Мой даю вам.", "Христианский мир не означает отсутствие проблем. Это возможность не позволить страху окончательно управлять сердцем."),
    ("1 Кор. 13:4", "Любовь долготерпит, милосердствует.", "Любовь проявляется в конкретном терпении: выслушать, не унизить, помочь и не требовать немедленной благодарности."),
    ("Гал. 6:2", "Носите бремена друг друга, и таким образом исполните закон Христов.", "Иногда духовная помощь — это не совет, а присутствие рядом, практическая забота и готовность выслушать."),
    ("Флп. 4:6–7", "Не заботьтесь ни о чём, но всегда в молитве и прошении с благодарением открывайте свои желания пред Богом.", "Тревогу можно превратить в молитву: назвать страх, поблагодарить за уже полученное и попросить сил для ближайшего шага."),
]


def gospel_reflection_text(markdown: bool = False) -> str:
    today = date.today()
    reference, verse, reflection = GOSPEL_REFLECTIONS[today.toordinal() % len(GOSPEL_REFLECTIONS)]
    title = f"📖 Евангельская мысль • {today.day:02d}.{today.month:02d}"
    note = "Это духовное размышление, а не указание богослужебного чтения дня. Точное чтение уточняйте по календарю своего прихода."
    return f"{title}\n\n«{verse}»\n({reference})\n\n{reflection}\n\nℹ️ {note}"


def orthodox_easter(year: int) -> date:
    """Orthodox Pascha in Gregorian calendar for years 1900–2099."""
    a = year % 4
    b = year % 7
    c = year % 19
    d = (19 * c + 15) % 30
    e = (2 * a + 4 * b - d + 34) % 7
    month = (d + e + 114) // 31
    day = ((d + e + 114) % 31) + 1
    return date(year, month, day) + timedelta(days=13)


def fasting_guidance_text(today: date | None = None) -> str:
    today = today or date.today()
    pascha = orthodox_easter(today.year)
    great_start, great_end = pascha - timedelta(days=48), pascha - timedelta(days=1)
    apostles_start, apostles_end = pascha + timedelta(days=57), date(today.year, 7, 11)
    period = ""
    if great_start <= today <= great_end:
        period = "Великий пост"
    elif apostles_start <= apostles_end and apostles_start <= today <= apostles_end:
        period = "Петров пост"
    elif date(today.year, 8, 14) <= today <= date(today.year, 8, 27):
        period = "Успенский пост"
    elif today >= date(today.year, 11, 28) or today <= date(today.year, 1, 6):
        period = "Рождественский пост"
    elif today.weekday() == 2:
        period = "среда — традиционный постный день"
    elif today.weekday() == 4:
        period = "пятница — традиционный постный день"

    if period:
        lead = f"🕯️ Сегодня: {period}."
    else:
        lead = "☀️ По базовому календарю сегодня не определяется многодневный пост или обычный постный день."
    return (
        lead
        + "\n\nПост — это не только состав пищи, но и молитва, покаяние, милосердие и внимание к ближним. "
          "Мера пищевого поста зависит от церковного дня, здоровья, возраста и жизненных обстоятельств. "
          "Точные правила лучше сверить с календарём своего прихода и, особенно при болезни, беременности или тяжёлой работе, обсудить со священником."
    )


def find_angel_day(name: str, birth_date_str: str) -> str:
    """Returns a possible next commemoration date after birthday."""
    days = SAINTS_BY_NAME.get((name or "").lower().strip()) or []
    if not days or not birth_date_str:
        return ""
    try:
        birth = datetime.strptime(birth_date_str[:5], "%d.%m").replace(year=2000)
        candidates = []
        for day_str, saint in days:
            d = datetime.strptime(day_str, "%d.%m").replace(year=2000)
            delta = (d - birth).days
            if delta < 0:
                delta += 366
            candidates.append((delta, d, day_str, saint))
        _, _d, day_str, saint = min(candidates, key=lambda x: x[0])
        return f"{day_str} ({saint})"
    except Exception:
        return ""

# ========== КОНТЕНТ — МОЛИТВЫ ==========
PRAYERS = {
    "morning_ru": {
        "title": "🌅 Утренняя молитва (русский)",
        "text": (
            "Встав от сна, прежде всякого другого дела,\n"
            "стань благоговейно, помня, что стоишь пред лицом Всевидящего Бога,\n"
            "и, совершив крестное знамение, скажи:\n\n"
            "Господи Боже мой! Благодарю Тебя,\n"
            "что Ты по великой Своей милости и долготерпению\n"
            "не прогневался на меня, грешника,\n"
            "и не погубил меня с беззакониями моими,\n"
            "но явил обычное Своё человеколюбие\n"
            "и воздвиг меня, лежащего в нерадении,\n"
            "чтобы я совершил утреннее пение\n"
            "и славословил Твою державу.\n\n"
            "Просвети ныне очи ума моего,\n"
            "отверзи уста мои для поучения в словах Твоих,\n"
            "разумению заповедей Твоих научи меня,\n"
            "помоги мне творить волю Твою,\n"
            "пети Тебя в сердечном исповедании\n"
            "и восхвалять Всесвятое Имя Твоё,\n"
            "Отца и Сына и Святого Духа,\n"
            "ныне и присно и во веки веков. Аминь."
        )
    },
    "morning_cs": {
        "title": "🌅 Утренняя молитва (церковнославянский)",
        "text": (
            "Востав от сна, прежде всякого другого дела,\n"
            "стани благоговейно, помышляя себе пред Всевидящим Богом,\n"
            "и, сотворив крестное знамение, глаголи:\n\n"
            "Господи Боже мой! Благодарю Тя,\n"
            "яко по великой Твоей милости и долготерпению\n"
            "не прогневался на меня, грешника,\n"
            "и не погубил мя со беззаконьми моими,\n"
            "но явил еси обычное Твое человеколюбие\n"
            "и воздвигл мя, лежащего в нерадении,\n"
            "еже утреннее пение сотворити\n"
            "и славословити державу Твою.\n\n"
            "Просвети ныне очи ума моего,\n"
            "отверзи уста моя поучатися словесем Твоим,\n"
            "разумети заповеди Твоя научи мя,\n"
            "помози ми волю Твою творити,\n"
            "пети Тя в сердечнем исповедании\n"
            "и воспевати Всесвятое Имя Твое,\n"
            "Отца и Сына и Святаго Духа,\n"
            "ныне и присно и во веки веков. Аминь."
        )
    },
    "evening_ru": {
        "title": "🌙 Вечерняя молитва (русский)",
        "text": (
            "Господи Боже мой!\n"
            "Благодарю Тебя за то, что Ты сподобил меня дожить до этого часа.\n"
            "Прости мне грехи, которые я сделал в нынешний день\n"
            "делом, словом и помышлением.\n\n"
            "Прости мне, Господи, и помилуй меня.\n"
            "Избави меня от всякого искушения,\n"
            "от всякой вражьей силы и нападения диавола.\n\n"
            "Дай мне мирный и безмятежный сон,\n"
            "без всякого мечтания и скверны.\n\n"
            "Сохрани меня в ночи сей\n"
            "и воздвигни меня во время утра на прославление Твоё.\n\n"
            "Ибо Ты — Бог мой и Господь мой,\n"
            "и Тебе слава подобает вовеки. Аминь."
        )
    },
    "evening_cs": {
        "title": "🌙 Вечерняя молитва (церковнославянский)",
        "text": (
            "Господи Боже мой!\n"
            "Благодарю Тя, яко сподобил мя еси дожити до часа сего.\n"
            "Прости мне грехи, яже сотворих в день сей\n"
            "делом, словом и помышлением.\n\n"
            "Прости мне, Господи, и помилуй мя.\n"
            "Избави мя от всякия напасти,\n"
            "от всякия вражия силы и нападения диаволя.\n\n"
            "Даруй ми сон мирен и безмятежен,\n"
            "без всякого мечтания и скверны.\n\n"
            "Сохрани мя в нощи сей\n"
            "и воздвигни мя во время утра на прославление Твое.\n\n"
            "Яко Ты еси Бог мой и Господь мой,\n"
            "и Тебе слава подобает во веки. Аминь."
        )
    },
    "before_meal": {
        "title": "🍽️ Молитва перед едой",
        "text": (
            "Отче наш, Иже еси на небесех!\n"
            "Да святится имя Твоё,\n"
            "да приидет Царствие Твоё,\n"
            "да будет воля Твоя,\n"
            "яко на небеси и на земли.\n"
            "Хлеб наш насущный даждь нам днесь;\n"
            "и остави нам долги наша,\n"
            "якоже и мы оставляем должником нашим;\n"
            "и не введи нас во искушение,\n"
            "но избави нас от лукавого.\n\n"
            "Очи всех на Тя, Господи, уповают,\n"
            "и Ты даеши им пищу во благовремении,\n"
            "отверзаеши Ты щедрую руку Твою\n"
            "и исполняеши всякое животно благоволения. Аминь."
        )
    },
    "after_meal": {
        "title": "🙏 Молитва после еды",
        "text": (
            "Благодарим Тя, Христе Боже наш,\n"
            "яко насытил еси нас земных Твоих благ;\n"
            "не лиши нас и Небесного Твоего Царствия,\n"
            "но яко посреде учеников Твоих пришел еси, Спасе,\n"
            "мир даяй им,\n"
            "прииди к нам и спаси нас. Аминь."
        )
    },
    "zdravie": {
        "title": "💛 Молитва о здравии",
        "text": (
            "Боже всесильный и всемилостивый!\n"
            "К Тебе прибегаю в скорби сердца моего\n"
            "и молю Тебя:\n\n"
            "Исцели болящего раба Твоего (имя),\n"
            "ибо Ты один — Врач душ и телес.\n\n"
            "Укрепи его в терпении скорбей,\n"
            "подай ему силы переносить болезнь,\n"
            "облегчи его страдания,\n"
            "даруй ему выздоровление,\n"
            "если это служит ко спасению души его.\n\n"
            "Не лиши его и нас, молящихся за него,\n"
            "Своей благодати и милосердия.\n\n"
            "Господи Иисусе Христе, Боже наш,\n"
            "исцели страдающего. Аминь."
        )
    },
    "upokoenie": {
        "title": "🕯️ Молитва об упокоении",
        "text": (
            "Упокой, Господи, душу усопшего раба Твоего (имя),\n"
            "прости ему вся согрешения его вольные и невольные\n"
            "и даруй ему Царствие Небесное.\n\n"
            "Со святыми упокой, Христе,\n"
            "душу раба Твоего (имя),\n"
            "идеже несть болезнь, ни печаль,\n"
            "ни воздыхание,\n"
            "но жизнь бесконечная. Аминь.\n\n"
            "Вечная память!\n"
            "Вечная память!\n"
            "Вечная память!"
        )
    },
    "doroga": {
        "title": "🚗 Молитва в дороге",
        "text": (
            "Господи Иисусе Христе, Боже наш!\n"
            "Путь и Истина и Живот сый,\n"
            "сопутствуй мне в путешествии моём\n"
            "и молитвами Пречистой Матери Твоей\n"
            "и всех святых Твоих\n"
            "сохрани мя от всякой опасности\n"
            "и напасти и управи путь мой благополучно.\n\n"
            "Буди щит и покров\n"
            "рабу Твоему (имя) на пути сем.\n\n"
            "Ибо у Тебя источник жизни,\n"
            "и во свете Твоем узрим свет. Аминь."
        )
    },
    "o_detyah": {
        "title": "👶 Молитва о детях",
        "text": (
            "Господи Иисусе Христе, Боже наш!\n"
            "Призри с высоты святой Твоей\n"
            "на смиренную молитву мою\n"
            "о детях моих (имена)\n"
            "и сохрани их под кровом Твоей милости.\n\n"
            "Вразуми их и научи их ходить\n"
            "по заповедям Твоим.\n"
            "Сохрани их от всякого злого обычая,\n"
            "вложи в сердца их страх Твой\n"
            "и любовь к Тебе и к ближним.\n\n"
            "Аминь."
        )
    },
    "nikolay": {
        "title": "⭐ Молитва Николаю Чудотворцу",
        "text": (
            "О всесвятый Николае,\n"
            "угодниче преизрядный Господень,\n"
            "тёплый наш заступниче и везде в скорбех скорый помощниче!\n\n"
            "Помози мне грешному и унылому\n"
            "в настоящем сем житии,\n"
            "умоли Господа Бога\n"
            "даровати ми оставление\n"
            "всех моих грехов,\n"
            "елика согреших от юности моея,\n"
            "во всем житии моем,\n"
            "делом, словом, помышлением\n"
            "и всеми моими чувствы.\n\n"
            "И во исходе души моея\n"
            "помози ми окаянному,\n"
            "умоли Господа Бога,\n"
            "всея твари Содетеля,\n"
            "избавити мя воздушных мытарств\n"
            "и вечного мучения:\n\n"
            "Да всегда прославляю Отца и Сына\n"
            "и Святаго Духа\n"
            "и твое милостивное предстательство,\n"
            "ныне и присно и во веки веков. Аминь."
        )
    },
    "matrona": {
        "title": "🕯️ Молитва Матроне Московской",
        "text": (
            "О блаженная мати Матроно,\n"
            "услыши и приими ныне нас, грешных,\n"
            "молящихся тебе,\n"
            "навыкшая во всем житии твоем\n"
            "приимати и выслушивати\n"
            "всех страждущих и скорбящих,\n"
            "с верою и надеждою\n"
            "к твоему заступлению и помощи прибегающих.\n\n"
            "Даруй нам свою материнскую помощь и заступление,\n"
            "да укрепит нас в вере и уповании на Бога,\n"
            "да сохранит нас от соблазнов мира\n"
            "и от ненастий жизни.\n\n"
            "Аминь."
        )
    },
    "prichaschenie": {
        "title": "✝️ Правило ко Причастию (краткое)",
        "text": (
            "Перед Причастием читается следующее правило:\n\n"
            "1. Канон покаянный ко Господу Иисусу Христу\n"
            "2. Канон молебный ко Пресвятой Богородице\n"
            "3. Канон Ангелу Хранителю\n"
            "4. Последование ко Святому Причащению\n\n"
            "— — —\n\n"
            "Молитва перед Причастием:\n\n"
            "Верую, Господи, и исповедую,\n"
            "яко Ты еси воистину Христос,\n"
            "Сын Бога Живаго,\n"
            "пришедый в мир грешныя спасти,\n"
            "от нихже первый есмь аз.\n\n"
            "Еще верую, яко сие есть\n"
            "самое Пречистое Тело Твое\n"
            "и сия есть самая Честная Кровь Твоя.\n\n"
            "Молюся убо Тебе:\n"
            "помилуй мя и прости ми прегрешения моя,\n"
            "вольная и невольная,\n"
            "яже словом, яже делом,\n"
            "яже ведением и неведением,\n"
            "и сподоби мя неосужденно причаститися\n"
            "Пречистых Твоих Таинств,\n"
            "во оставление грехов\n"
            "и в жизнь вечную. Аминь."
        )
    },
    "pokayanny_kanon": {
        "title": "📖 Канон покаянный (начало)",
        "text": (
            "Читается перед исповедью.\n\n"
            "Песнь 1, глас 6:\n\n"
            "Яко по суху пешешествовав Израиль\n"
            "по бездне стопами,\n"
            "гонителя фараона видя потопляема,\n"
            "Богу победную песнь поим, вопияше.\n\n"
            "Помилуй мя, Боже, помилуй мя.\n\n"
            "Ныне нападе на мя суд праведный,\n"
            "ныне мя совесть обличает,\n"
            "ныне вся на мя восстают\n"
            "дела моя лукавая...\n\n"
            "— — —\n\n"
            "⚠️ Полный текст канона рекомендуется читать\n"
            "по молитвослову или православному приложению."
        )
    },
}

# ========== КОНТЕНТ — ТАИНСТВА ==========
SACRAMENTS = {
    "ispoved": {
        "title": "📿 Исповедь — полный путь",
        "text": (
            "☦️ Исповедь — это разговор с Богом в присутствии\n"
            "священника. Вы рассказываете о грехах, искренне\n"
            "раскаиваетесь — и Господь прощает.\n"
            "Священник здесь не судья, а свидетель.\n"
            "Бояться не нужно — батюшка всё слышал и\n"
            "никогда не осудит.\n\n"
            "📅 КАК ПОДГОТОВИТЬСЯ:\n\n"
            "За несколько дней:\n"
            "— Вспоминайте грехи и записывайте на бумагу\n"
            "— Читайте утренние и вечерние молитвы\n"
            "— Старайтесь не ссориться и не осуждать других\n"
            "— Попросите прощения у тех кого обидели\n\n"
            "⚠️ Пост перед исповедью не обязателен —\n"
            "пост установлен перед Причастием, а не перед исповедью.\n\n"
            "Накануне или в день исповеди (по желанию):\n"
            "— Прочитайте Канон покаянный (около 20 минут)\n\n"
            "Утром в день исповеди:\n"
            "— Прочитайте утренние молитвы\n"
            "— Уточните время исповеди в вашем храме заранее\n"
            "  (обычно до начала службы или во время неё)\n\n"
            "🙏 ЧТО ГОВОРИТЬ НА ИСПОВЕДИ:\n\n"
            "Говорите своими словами — Бог слышит сердце.\n"
            "— Называйте конкретные грехи, не общие слова\n"
            "— Говорите от первого лица: «Я солгал», «Я осудил»\n"
            "— Не оправдывайтесь и не обвиняйте других\n"
            "— Если забыли что-то — не страшно\n\n"
            "📝 Можно написать грехи на листочке — это\n"
            "совершенно нормально. Подайте листок батюшке\n"
            "и он прочитает сам. Многие так делают особенно\n"
            "на первой исповеди.\n\n"
            "💡 Совет для первой исповеди:\n"
            "Грехи за всю жизнь не вспоминаются за один день.\n"
            "Заведите заметку в телефоне и записывайте по мере\n"
            "того как вспоминаете. Когда почувствуете что\n"
            "готовы — тогда и начинайте готовиться. Не торопитесь.\n\n"
            "⛪ КАК ЭТО ПРОИСХОДИТ В ХРАМЕ:\n\n"
            "1. Подойдите к аналою (столик с иконой и крестом)\n"
            "2. Священник спросит ваше имя\n"
            "3. Расскажите грехи — тихо, только батюшка слышит\n"
            "4. Священник накроет голову епитрахилью\n"
            "5. Прочитает разрешительную молитву\n"
            "6. Вы целуете крест и Евангелие\n"
            "7. Грехи прощены 🕊️\n\n"
            "Не переживайте если растеряетесь —\n"
            "батюшка поможет. Главное что вы пришли.\n\n"
            "➡️ После исповеди — путь к Причастию:"
        )
    },
    "prichaschenie": {
        "title": "✝️ Причастие — полный путь",
        "text": (
            "☦️ Причастие (Евхаристия) — главное Таинство\n"
            "православной Церкви. Верующий принимает Тело\n"
            "и Кровь Христову. Это не символ — это реальное\n"
            "соединение с Богом. Православные стремятся\n"
            "причащаться регулярно — хотя бы раз в месяц.\n\n"
            "📅 ПУТЬ ПОДГОТОВКИ:\n\n"
            "Начните с Исповеди — без неё причащаться нельзя.\n"
            "Исповедь и Причастие всегда идут вместе.\n\n"
            "За 3 дня:\n"
            "— Воздержитесь от мяса, рыбы, молочного, яиц\n"
            "— Читайте утренние и вечерние молитвы\n"
            "— Избегайте ссор, осуждения, развлечений\n\n"
            "Вечером накануне — правило ко Причастию:\n"
            "— Канон покаянный (20 минут)\n"
            "— Канон Богородице (15 минут)\n"
            "— Канон Ангелу Хранителю (15 минут)\n"
            "— Последование ко Святому Причащению (30 минут)\n"
            "Итого около 1.5 часов — можно разделить\n"
            "на вечер и утро.\n\n"
            "С полуночи до Причастия:\n"
            "— Не есть, не пить (даже воду)\n\n"
            "Утром:\n"
            "— Дочитайте утреннюю часть правила\n"
            "— Придите к началу Литургии (обычно 8-9 утра)\n\n"
            "💡 Не пугайтесь объёма — это читается неспешно.\n"
            "Если правило кажется большим для первого раза —\n"
            "поговорите со священником, он может благословить\n"
            "сокращённое правило для начинающих.\n\n"
            "⛪ КАК ЭТО ПРОИСХОДИТ В ХРАМЕ:\n\n"
            "1. Придите к началу Литургии и стойте до конца\n"
            "2. Когда священник выносит Чашу — подходите\n"
            "3. Сложите руки крестом — правая поверх левой\n"
            "4. Назовите своё имя священнику\n"
            "5. Широко откройте рот — священник даст ложечку\n"
            "6. Не касайтесь Чаши руками\n"
            "7. Поцелуйте край Чаши\n"
            "8. Отойдите к столику — запейте теплотой\n"
            "   и возьмите просфору\n"
            "9. Выслушайте благодарственные молитвы\n\n"
            "В день Причастия старайтесь сохранять\n"
            "мирное состояние души — не ссориться,\n"
            "провести день в тишине и молитве 🕊️"
        )
    },
    "kreshchenie": {
        "title": "💧 Крещение — полный путь",
        "text": (
            "☦️ Крещение — вхождение в Церковь Христову.\n"
            "Первое и главное Таинство — без него остальные\n"
            "недоступны. Крестить можно в любом возрасте.\n\n"
            "📅 ПУТЬ ПОДГОТОВКИ:\n\n"
            "Заранее:\n"
            "— Запишитесь в храм — крещение без записи\n"
            "  обычно не совершается\n"
            "— Пройдите огласительные беседы со священником\n"
            "  (обычно 2-3 встречи)\n\n"
            "Для крёстных — подготовка:\n"
            "— Выучите Символ Веры и Отче наш —\n"
            "  крёстные читают их вслух на обряде\n"
            "— Читайте утренние и вечерние молитвы\n\n"
            "Накануне (уточните у священника вашего прихода):\n"
            "— Во многих храмах крёстные проходят\n"
            "  Исповедь и Причастие накануне\n"
            "— Некоторые приходы рекомендуют пост за 1-3 дня\n"
            "— Требования различаются — уточните у батюшки\n\n"
            "📦 ЧТО ВЗЯТЬ С СОБОЙ:\n\n"
            "— Крестильная рубашка (белая)\n"
            "— Нательный крестик с цепочкой (освящённый)\n"
            "— Крыжма — белое полотенце или пелена\n"
            "— Свечи (продаются в храме)\n"
            "— Икона Спасителя или Богородицы\n\n"
            "👤 КТО ТАКИЕ КРЁСТНЫЕ:\n\n"
            "— Достаточно одного крёстного:\n"
            "  для мальчика — крёстный отец\n"
            "  для девочки — крёстная мать\n"
            "— Двое крёстных — традиция, но не обязательно\n"
            "— Желательно православные и практикующие\n"
            "— Должны знать Символ Веры и Отче наш\n"
            "— Не могут быть супругами между собой\n"
            "— Не могут быть родителями ребёнка\n"
            "— Несут духовную ответственность до конца жизни\n\n"
            "Крёстный — это не почётное звание\n"
            "а настоящая духовная ответственность.\n\n"
            "⛪ КАК ЭТО ПРОИСХОДИТ В ХРАМЕ:\n\n"
            "1. Священник читает молитвы и дует на крещаемого\n"
            "2. Крёстные читают Символ Веры вслух\n"
            "3. Священник трижды погружает в купель:\n"
            "   «Крещается раб Божий (имя) во имя Отца,\n"
            "   и Сына, и Святаго Духа»\n"
            "4. Каждое погружение — смерть греха\n"
            "   и воскресение во Христе\n"
            "5. Крёстный принимает ребёнка в крыжму\n"
            "6. Миропомазание — помазание освящённым миром\n"
            "7. Надевается крестик и крестильная рубашка\n"
            "8. Священник стрижёт прядь волос —\n"
            "   символ посвящения Богу\n"
            "9. Обход вокруг купели трижды с пением\n"
            "10. Младенцев сразу причащают\n\n"
            "Таинство длится около часа 🕊️\n\n"
            "➡️ Крёстным перед Крещением:"
        )
    },
    "venchanie": {
        "title": "💍 Венчание — полный путь",
        "text": (
            "☦️ Венчание — благословение супружеского союза\n"
            "Богом. Это не просто красивый обряд — это Таинство\n"
            "в котором Господь соединяет двух людей в одно целое.\n\n"
            "Венчаться можно только если оба супруга крещены\n"
            "в православной вере.\n\n"
            "📅 ПУТЬ ПОДГОТОВКИ:\n\n"
            "Заранее:\n"
            "— Большинство храмов венчают после регистрации\n"
            "  в ЗАГСе — уточните в вашем приходе.\n"
            "  Некоторые священники могут повенчать и без\n"
            "  государственной регистрации — решается\n"
            "  индивидуально с батюшкой\n"
            "— Запишитесь в храм заранее\n"
            "— Пройдите огласительные беседы (2-3 встречи)\n\n"
            "За 3 дня:\n"
            "— Пост для обоих (мясо, рыба, молочное — исключить)\n"
            "— Читайте утренние и вечерние молитвы\n"
            "— Воздержитесь от супружеской близости\n\n"
            "Накануне:\n"
            "— Оба супруга проходят Исповедь\n\n"
            "Утром в день венчания:\n"
            "— Оба супруга причащаются на Литургии\n"
            "— После Причастия — не есть до венчания\n\n"
            "📦 ЧТО ВЗЯТЬ С СОБОЙ:\n\n"
            "— Обручальные кольца (золотые или серебряные)\n"
            "— Венчальные свечи\n"
            "— Рушник — белое полотенце\n"
            "— Икона Спасителя (жениху) и Богородицы (невесте)\n"
            "— Свидетели — желательно православные\n\n"
            "⚠️ КОГДА НЕЛЬЗЯ ВЕНЧАТЬСЯ:\n\n"
            "— В период постов (Великий, Петров,\n"
            "  Успенский, Рождественский)\n"
            "— В Светлую Седмицу (неделя после Пасхи)\n"
            "— Накануне среды и пятницы\n"
            "— Уточните даты в вашем храме\n\n"
            "⛪ КАК ЭТО ПРОИСХОДИТ В ХРАМЕ:\n\n"
            "Обручение:\n"
            "1. Священник вводит жениха и невесту в храм\n"
            "2. Вручает зажжённые свечи — символ любви\n"
            "3. Трижды благословляет кольцами и надевает их\n\n"
            "Венчание:\n"
            "4. Жених и невеста встают на рушник\n"
            "5. Священник трижды спрашивает о добровольности —\n"
            "   отвечайте громко и чётко\n"
            "6. Священник возлагает венцы на головы —\n"
            "   это главный момент Таинства\n"
            "7. Читается Евангелие и молитвы\n"
            "8. Супруги трижды пьют из общей чаши\n"
            "9. Священник трижды обводит вокруг аналоя\n"
            "10. Венцы снимаются — Таинство совершено 💍\n\n"
            "Венчание длится около часа.\n"
            "Не волнуйтесь — священник проведёт\n"
            "вас через каждый шаг.\n\n"
            "➡️ Перед венчанием оба супруга:"
        )
    },
    "otpevanie": {
        "title": "🕯️ Отпевание — как организовать",
        "text": (
            "☦️ Отпевание — последняя молитва Церкви об усопшем.\n"
            "Это не прощание — это проводы в вечную жизнь.\n"
            "Церковь молится чтобы Господь простил грехи\n"
            "усопшего и принял душу в Царствие Небесное.\n\n"
            "Не бойтесь этого обряда —\n"
            "это акт любви к человеку которого вы потеряли.\n\n"
            "📅 ЧТО СДЕЛАТЬ СРАЗУ ПОСЛЕ СМЕРТИ:\n\n"
            "— Позвоните в храм и сообщите о смерти\n"
            "— Договоритесь о дате и времени отпевания\n"
            "— Сообщите крещёное имя усопшего\n"
            "— Отпевание обычно совершается на 3й день\n\n"
            "Дома до отпевания:\n"
            "— Читайте Псалтирь над усопшим\n"
            "— Зажгите свечу у иконы и молитесь своими словами\n"
            "— Подайте записку о упокоении в храм\n\n"
            "🏛️ ГДЕ МОЖНО ПРОВЕСТИ ОТПЕВАНИЕ:\n\n"
            "— В храме — традиционно\n"
            "— В ритуальном зале — многие агентства имеют\n"
            "  специальные помещения, священника приглашают\n"
            "  отдельно\n"
            "— Дома — священник может приехать\n"
            "— На кладбище — краткое отпевание у могилы\n\n"
            "📦 ЧТО ВЗЯТЬ С СОБОЙ:\n\n"
            "— Свечи (продаются в храме)\n"
            "— Икона (кладётся рядом с гробом)\n"
            "— Погребальное покрывало (белое)\n"
            "— Венчик — выдаётся в храме\n\n"
            "⛪ КАК ЭТО ПРОИСХОДИТ:\n\n"
            "1. Гроб ставится лицом к алтарю\n"
            "2. Вокруг зажигаются свечи — держите в руках\n"
            "3. Священник читает молитвы и Евангелие\n"
            "4. Поётся «Со святыми упокой»\n"
            "5. Священник кладёт разрешительную молитву\n"
            "   в руку усопшего\n"
            "6. Все прощаются — целуют венчик на лбу\n"
            "7. Священник крестообразно посыпает землёй\n"
            "8. Гроб закрывается\n\n"
            "Плакать на отпевании — это нормально.\n"
            "Господь видит вашу скорбь.\n\n"
            "🕯️ КАК ПОМИНАТЬ ПОСЛЕ ОТПЕВАНИЯ:\n\n"
            "— 3й день — день погребения\n"
            "— 9й день — молитва дома и в храме\n"
            "— 40й день — закажите панихиду в храме\n"
            "— Каждый год — в годовщину смерти\n\n"
            "Как помочь душе усопшего:\n"
            "— Подавайте записки о упокоении на каждой Литургии\n"
            "— Закажите сорокоуст (40 дней поминовения)\n"
            "— Творите милостыню в память о нём\n"
            "— Читайте дома Псалтирь\n\n"
            "⚠️ Православная Церковь не отпевает:\n"
            "— Некрещёных\n"
            "— Самоубийц (без разрешения епископа)\n"
            "Если ситуация нестандартная —\n"
            "поговорите со священником."
        )
    },
    "sobor": {
        "title": "🫒 Соборование — полный путь",
        "text": (
            "☦️ Соборование (Елеосвящение) — Таинство исцеления.\n"
            "Болящий получает благодать Божию для исцеления\n"
            "телесного и душевного, прощаются забытые\n"
            "и неосознанные грехи.\n\n"
            "Важно знать: Соборование — это НЕ последнее\n"
            "причастие и НЕ приготовление к смерти.\n"
            "Это Таинство исцеления для живых людей.\n\n"
            "👤 КОМУ НУЖНО СОБОРОВАНИЕ:\n\n"
            "— Тяжелобольным и немощным\n"
            "— Перед серьёзной операцией\n"
            "— Всем православным в Великий пост —\n"
            "  общее соборование совершается для всех желающих\n"
            "— Пожилым людям — раз в год как духовное очищение\n\n"
            "Не нужно ждать смертельной болезни.\n"
            "Соборование полезно каждому.\n\n"
            "📅 ПУТЬ ПОДГОТОВКИ:\n\n"
            "Заранее:\n"
            "— Договоритесь со священником —\n"
            "  в храме или пригласите домой\n"
            "— Если общее соборование в храме —\n"
            "  просто придите в назначенное время\n\n"
            "За 1-3 дня:\n"
            "— Пост по возможности\n"
            "— Если болезнь не позволяет строго поститься —\n"
            "  делайте как можете, Господь видит намерение\n"
            "— Читайте утренние и вечерние молитвы\n\n"
            "Перед Собором:\n"
            "— Пройдите Исповедь\n\n"
            "⛪ КАК ЭТО ПРОИСХОДИТ:\n\n"
            "1. Священник читает молитвы и Евангелие\n"
            "2. Освящается елей (масло) с вином\n"
            "3. Семь раз помазывает болящего маслом:\n"
            "   лоб, ноздри, щёки, губы, грудь, руки\n"
            "4. После каждого помазания — молитва об исцелении\n"
            "5. Евангелие возлагается на голову\n"
            "6. Священник читает разрешительную молитву\n\n"
            "Таинство длится около часа.\n"
            "Дома священник совершает его у постели болящего.\n\n"
            "🕯️ ПОСЛЕ СОБОРОВАНИЯ:\n\n"
            "— Причаститесь — желательно в тот же день\n"
            "— Остатки освящённого масла возьмите домой —\n"
            "  помазывайте больное место с молитвой\n"
            "— Сохраняйте мирное состояние души\n\n"
            "➡️ После Соборования:"
        )
    },
    "osvyashchenie": {
        "title": "🏠 Освящение жилья, машины, вещей",
        "text": (
            "☦️ Освящение — благословение Церкви на использование\n"
            "предметов во благо и защиту от злых сил.\n\n"
            "🏠 Освящение жилья:\n\n"
            "— Пригласите священника в дом —\n"
            "  договоритесь через свой приход\n"
            "— Подготовьте: свечи, икону, воду крещенскую\n"
            "— Уберите в доме, приготовьте угощение для батюшки\n"
            "— Священник обходит все комнаты с молитвами\n"
            "  и кропит освящённой водой крестообразно\n"
            "— На стенах остаются крестики от кропления —\n"
            "  не смывайте их\n\n"
            "🚗 Освящение автомобиля:\n\n"
            "— Подъедьте к храму или вызовите священника\n"
            "— Священник читает молитву о путешествующих\n"
            "  и кропит машину снаружи и внутри\n"
            "— Повесьте иконку в машину после освящения\n\n"
            "✝️ Освящение вещей:\n\n"
            "— Крестики, иконы, медальоны — в любом храме\n"
            "— Принесите вещи на молебен или попросите\n"
            "  священника после службы\n\n"
            "💧 В храме всегда можно бесплатно:\n\n"
            "— Набрать освящённой воды\n"
            "— Взять освящённое масло (елей)\n"
            "— Взять просфору\n\n"
            "Освящённую воду храните в чистом месте\n"
            "рядом с иконами. Пейте натощак с молитвой."
        )
    },
    "svecha": {
        "title": "🕯️ Как правильно ставить свечи",
        "text": (
            "☦️ Свеча — символ нашей молитвы горящей перед Богом.\n"
            "Размер свечи не имеет значения —\n"
            "важна молитва сердца.\n\n"
            "🕯️ КОМУ И ЗАЧЕМ:\n\n"
            "Иисусу Христу (центральный аналой):\n"
            "— О здравии и благополучии\n"
            "— С благодарностью за помощь\n\n"
            "Богородице:\n"
            "— О детях, семье, материнстве\n"
            "— В скорбях и болезнях\n\n"
            "Николаю Чудотворцу:\n"
            "— В дороге и путешествии\n"
            "— О помощи в делах\n\n"
            "Пантелеимону Целителю:\n"
            "— О здоровье и исцелении\n\n"
            "Матроне Московской:\n"
            "— В скорбях и нуждах\n"
            "— О помощи в семейных делах\n\n"
            "На канун (прямоугольный подсвечник):\n"
            "— За упокой усопших\n\n"
            "📋 ПРАВИЛА:\n\n"
            "— Свечу зажигают от другой свечи или лампады\n"
            "— Ставят прямо, укрепляя в гнезде\n"
            "— Можно поставить и уйти — молитва продолжается\n"
            "— Если свеча упала — не страшно, поставьте снова\n"
            "— Нет плохой приметы если свеча гаснет"
        )
    },
    "zapiska": {
        "title": "📝 Как подавать записки",
        "text": (
            "☦️ Записки — молитвенное поминовение\n"
            "на Литургии и молебнах.\n\n"
            "📝 КАК ПИСАТЬ:\n\n"
            "— Вверху: «О здравии» или «О упокоении»\n"
            "— Пишите крупно и разборчиво\n"
            "— Только крещёные православные имена\n"
            "— Церковная форма имени:\n"
            "  Юля → Иулия, Алёша → Алексий,\n"
            "  Оксана → Ксения, Света → Фотиния\n"
            "— Не более 10 имён на листе\n"
            "— Не указывайте фамилии и отчества\n\n"
            "📋 ВИДЫ ПОМИНОВЕНИЯ:\n\n"
            "Простая записка:\n"
            "1 раз на ближайшей Литургии\n\n"
            "Сорокоуст:\n"
            "40 дней подряд — для недавно усопших\n\n"
            "Годовое поминовение:\n"
            "В течение всего года\n\n"
            "Неусыпаемая Псалтирь:\n"
            "Непрерывное чтение — заказывается в монастырях\n\n"
            "⚠️ Нельзя подавать записки о:\n"
            "— Некрещёных\n"
            "— Самоубийцах (без благословения)\n"
            "— Иноверцах\n\n"
            "Если не знаете церковного имени —\n"
            "спросите в храме, там помогут."
        )
    },
    "v_hrame": {
        "title": "⛪ Как вести себя в храме",
        "text": (
            "☦️ Храм — дом Божий. Здесь особое место\n"
            "встречи с Богом. Не бойтесь что-то сделать\n"
            "не так — главное прийти с открытым сердцем.\n\n"
            "👗 ОДЕЖДА:\n\n"
            "Женщины:\n"
            "— Покрытая голова (платок)\n"
            "— Юбка ниже колена или брюки\n"
            "— Закрытые плечи\n"
            "— Минимум косметики\n\n"
            "Мужчины:\n"
            "— Без головного убора (снять при входе)\n"
            "— Без шорт и майки\n"
            "— Деловой или опрятный вид\n\n"
            "🚪 ВХОД В ХРАМ:\n\n"
            "— Остановитесь у входа\n"
            "— Трижды перекреститесь с поклоном\n"
            "— Войдите тихо, не мешая службе\n"
            "— Отключите или переведите телефон в беззвучный\n\n"
            "✝️ КАК КРЕСТИТЬСЯ:\n\n"
            "— Правой рукой\n"
            "— Три первых пальца вместе (символ Троицы)\n"
            "— Два пальца прижаты к ладони\n"
            "— Лоб → живот → правое плечо → левое плечо\n"
            "— С лёгким поклоном\n\n"
            "🕯️ В ХРАМЕ:\n\n"
            "— Говорите тихо или шёпотом\n"
            "— Во время Литургии стойте — не ходите\n"
            "— Сидеть можно (пожилым и больным — всегда)\n"
            "— Не стойте спиной к алтарю\n"
            "— Детей можно выводить если беспокоятся\n\n"
            "💋 ПРИКЛАДЫВАНИЕ К ИКОНАМ:\n\n"
            "— Подходите справа, уступая друг другу\n"
            "— Два поклона → приложиться → поклон\n"
            "— Целуют руку, ногу или край одежды на иконе\n"
            "— Лик (лицо) святого не целуют\n\n"
            "Не стесняйтесь спросить у служащих\n"
            "если что-то непонятно — они всегда помогут 🕊️"
        )
    },
    "pasха": {
        "title": "🥚 Пасха — от поста до праздника",
        "text": (
            "☦️ Пасха — главный праздник православного года.\n"
            "Воскресение Христово — победа жизни над смертью.\n"
            "«Христос Воскресе!» — «Воистину Воскресе!»\n\n"
            "📅 ВЕЛИКИЙ ПОСТ — 48 дней подготовки:\n\n"
            "Великий пост начинается за 48 дней до Пасхи.\n"
            "Дата меняется каждый год — уточните в календаре.\n\n"
            "В пост исключаются:\n"
            "— Мясо, птица\n"
            "— Рыба (кроме особых дней)\n"
            "— Молочные продукты, яйца\n"
            "— Алкоголь\n\n"
            "Рыба разрешена:\n"
            "— В Вербное воскресенье\n"
            "— В Благовещение (7 апреля)\n\n"
            "Строгий пост (сухоядение):\n"
            "— Великая Пятница — самый строгий день\n\n"
            "📅 СТРАСТНАЯ НЕДЕЛЯ — последняя неделя:\n\n"
            "Чистый Четверг:\n"
            "— Причастие — главный день для исповеди\n"
            "— Уборка дома\n"
            "— Крашение яиц\n"
            "— Выпечка куличей\n\n"
            "Великая Пятница:\n"
            "— Строгий пост — день смерти Христа\n"
            "— Вынос Плащаницы в храме\n"
            "— Не есть до выноса Плащаницы\n\n"
            "Великая Суббота:\n"
            "— Освящение куличей, яиц, пасхи — с утра\n"
            "— Подготовка к ночной службе\n\n"
            "🌙 ПАСХАЛЬНАЯ НОЧЬ:\n\n"
            "— Придите в храм к 23:00\n"
            "— В полночь начинается Крестный ход\n"
            "— Все выходят из храма со свечами\n"
            "— Обходят храм трижды\n"
            "— Возвращаются в храм — начинается Пасхальная служба\n"
            "— Служба длится 2-3 часа\n"
            "— В конце — целование и приветствие:\n"
            "  «Христос Воскресе!» — «Воистину Воскресе!»\n\n"
            "🥚 ПАСХАЛЬНЫЕ ТРАДИЦИИ:\n\n"
            "Яйца:\n"
            "— Символ воскресения — внутри жизнь\n"
            "— Красят в красный цвет (кровь Христова)\n"
            "— Освящают в Великую Субботу\n\n"
            "Кулич:\n"
            "— Символ присутствия Христа\n"
            "— Освящают в Великую Субботу\n"
            "— Едят всю Светлую Седмицу\n\n"
            "Верба:\n"
            "— Вербное воскресенье — за неделю до Пасхи\n"
            "— Освящённые ветки хранят весь год\n\n"
            "🌟 СВЕТЛАЯ СЕДМИЦА — неделя после Пасхи:\n\n"
            "— Каждый день как воскресенье\n"
            "— Пост отменяется\n"
            "— Царские врата в храме открыты всю неделю\n"
            "— Пасхальное приветствие — до Вознесения (40 дней)"
        )
    },
    "kreschenije_prazdnik": {
        "title": "💧 Крещение Господне — 19 января",
        "text": (
            "☦️ Крещение Господне — один из великих праздников.\n"
            "В этот день вспоминается крещение Иисуса Христа\n"
            "в реке Иордан от Иоанна Предтечи.\n\n"
            "В момент крещения Христа с небес сошёл Святой Дух\n"
            "в виде голубя и был услышан голос Бога Отца.\n"
            "Поэтому праздник также называется Богоявление.\n\n"
            "💧 ОСВЯЩЕНИЕ ВОДЫ:\n\n"
            "— В храмах совершается Великое освящение воды\n"
            "— 18 января (Крещенский сочельник) — вечером\n"
            "— 19 января — в день праздника\n"
            "— Воду можно набрать в любой из этих дней\n"
            "— Крещенская вода особенная — не портится годами\n\n"
            "КАК ХРАНИТЬ СВЯТУЮ ВОДУ:\n"
            "— В чистом месте рядом с иконами\n"
            "— В стеклянной или пластиковой бутылке\n"
            "— Пейте натощак с молитвой утром\n"
            "— Можно добавлять каплю в обычную воду\n\n"
            "🏊 КУПАНИЕ В ПРОРУБИ:\n\n"
            "Купание в проруби — народная традиция,\n"
            "это НЕ обязательный церковный обряд.\n"
            "Само по себе купание не смывает грехи —\n"
            "для этого есть Исповедь.\n\n"
            "Как правильно окунаться:\n"
            "— Перекреститесь перед входом в воду\n"
            "— Окунитесь трижды с головой\n"
            "— При каждом погружении говорите:\n"
            "  «Во имя Отца, и Сына, и Святаго Духа»\n"
            "— Не задерживайтесь долго в воде\n"
            "— Сразу оденьтесь и согрейтесь\n\n"
            "⚠️ Кому не рекомендуется:\n"
            "— Людям с сердечно-сосудистыми заболеваниями\n"
            "— Пожилым и ослабленным\n"
            "— Детям без согласия врача\n"
            "— Беременным женщинам\n\n"
            "Если здоровье не позволяет — просто умойтесь\n"
            "крещенской водой дома. Это тоже благочестиво 🕊️"
        )
    },
}

# ========== КОНТЕНТ — СВЯТЫЕ МЕСТА ==========
HOLY_PLACES = {
    "podmoskove": {
        "title": "📍 Монастыри Подмосковья",
        "text": (
            "⭐ Троице-Сергиева Лавра (Сергиев Посад)\n"
            "Главная обитель России. Основана прп. Сергием Радонежским в 1337 г.\n"
            "Мощи: прп. Сергия Радонежского\n"
            "Как добраться: экспресс с Ярославского вокзала (1 ч 10 мин)\n\n"
            "🕍 Саввино-Сторожевский монастырь (Звенигород)\n"
            "Любимый монастырь русских царей. Основан в 1398 г.\n"
            "учеником Сергия Радонежского — Саввой.\n"
            "Мощи: прп. Саввы Сторожевского\n"
            "Как добраться: электричка с Белорусского вокзала\n\n"
            "🕍 Николо-Угрешский монастырь (Дзержинский)\n"
            "Основан Дмитрием Донским в 1380 г. после Куликовской битвы.\n"
            "Мощи: прп. Пимена Угрешского\n"
            "Как добраться: автобус от м. Люблино\n\n"
            "🕍 Давидова Пустынь (Чехов)\n"
            "Древняя обитель 1515 г., малоизвестная но намоленная.\n"
            "Тихое место для уединённой молитвы.\n"
            "Как добраться: электричка с Курского вокзала до Чехова\n\n"
            "🗺️ Как найти: откройте Яндекс.Карты и введите\n"
            "название монастыря"
        )
    },
    "central": {
        "title": "📍 Монастыри Центральной России",
        "text": (
            "🌸 Серафимо-Дивеевский монастырь (Нижегородская обл.)\n"
            "Четвёртый удел Богородицы. Основан прп. Серафимом Саровским.\n"
            "Мощи: прп. Серафима Саровского\n"
            "Канавка Богородицы — главная святыня обители.\n"
            "Как добраться: поезд до Арзамаса, автобус до Дивеево\n\n"
            "🌿 Оптина Пустынь (Козельск, Калужская обл.)\n"
            "Место великих старцев. Центр духовного возрождения XIX-XX вв.\n"
            "Здесь подвизались старцы Амвросий, Нектарий, Варсонофий.\n"
            "Мощи: Оптинских старцев\n"
            "Как добраться: поезд до Калуги, автобус до Козельска\n\n"
            "🕍 Шамординский монастырь (рядом с Оптиной)\n"
            "Женский монастырь основан старцем Амвросием Оптинским в 1884 г.\n"
            "Здесь жила сестра Льва Толстого — Мария.\n"
            "Красота природы и тишина — место особой благодати.\n\n"
            "🕍 Тихонова Пустынь (Калужская обл.)\n"
            "Основана прп. Тихоном Калужским в XV веке.\n"
            "Святой источник — место исцелений.\n\n"
            "🕍 Санаксарский монастырь (Мордовия)\n"
            "Мощи праведного Феодора Ушакова — великого адмирала\n"
            "и святого воина. Канонизирован в 2001 г.\n"
            "Малоизвестное но глубокое место.\n\n"
            "🕍 Макарьево-Желтоводский монастырь (Нижегородская обл.)\n"
            "Один из древнейших монастырей России. Основан в 1435 г.\n"
            "Стоит на берегу Волги — красота необыкновенная.\n"
            "Мощи: прп. Макария Желтоводского\n\n"
            "🗺️ Как найти: откройте Яндекс.Карты и введите\n"
            "название монастыря"
        )
    },
    "northwest": {
        "title": "📍 Монастыри Севера и Северо-Запада",
        "text": (
            "🏰 Псково-Печерский монастырь (Псковская обл.)\n"
            "Единственный монастырь России который никогда не закрывался.\n"
            "Пещеры с мощами тысяч монахов и мирян — действующий некрополь.\n"
            "Старцы: Иоанн Крестьянкин, Николай Гурьянов.\n"
            "Как добраться: поезд до Пскова, автобус до Печор\n\n"
            "⛵ Валаам (Республика Карелия)\n"
            "Остров-монастырь на Ладожском озере. Основан в XIV веке.\n"
            "«Северный Афон» — суровая красота и глубокое монашество.\n"
            "Скиты разбросаны по острову — каждый особенный.\n"
            "Как добраться: теплоход из Сортавалы или Приозерска\n\n"
            "🏔️ Соловецкий монастырь (Архангельская обл.)\n"
            "Острова в Белом море. Основан в 1436 г.\n"
            "Место подвигов и мученичества (СЛОН в советское время).\n"
            "Мощи: прпмч. Зосимы и Савватия Соловецких\n"
            "Как добраться: самолёт или теплоход из Кеми\n\n"
            "🕍 Александро-Свирский монастырь (Ленинградская обл.)\n"
            "Единственный русский святой которому явилась Святая Троица.\n"
            "Нетленные мощи прп. Александра Свирского.\n"
            "Малоизвестен — но благодатное место.\n\n"
            "⛵ Коневский монастырь (Ладожское озеро)\n"
            "Остров Коневец. Основан в 1393 г.\n"
            "Конь-камень — языческий идол обращённый в православную святыню.\n"
            "Тишина и уединение — место для глубокой молитвы.\n\n"
            "🕍 Кирилло-Белозерский монастырь (Вологодская обл.)\n"
            "Крупнейший монастырь средневековой Руси. Основан в 1397 г.\n"
            "Библиотека монастыря была богатейшей в России.\n"
            "Мощи: прп. Кирилла Белозерского\n\n"
            "🖼️ Ферапонтов монастырь (Вологодская обл.)\n"
            "Объект ЮНЕСКО. Фрески Дионисия 1502 г. — шедевр мирового значения.\n"
            "Малоизвестен широкой публике — жемчужина русского искусства.\n\n"
            "🕍 Спасо-Прилуцкий монастырь (Вологда)\n"
            "Основан учеником Сергия Радонежского — Димитрием Прилуцким.\n"
            "Один из первых монастырей Русского Севера (1371 г.)\n\n"
            "🗺️ Как найти: откройте Яндекс.Карты и введите\n"
            "название монастыря"
        )
    },
    "ural_siberia": {
        "title": "📍 Монастыри Урала и Сибири",
        "text": (
            "✝️ Ганина Яма (Екатеринбург)\n"
            "Место обретения останков Царской Семьи.\n"
            "Монастырь Святых Царственных Страстотерпцев.\n"
            "7 храмов по числу членов семьи Николая II.\n"
            "Одно из самых посещаемых мест паломников на Урале.\n"
            "Как добраться: автобус от Екатеринбурга (30 мин)\n\n"
            "🕍 Верхотурье (Свердловская обл.)\n"
            "Духовная столица Урала. Мощи прп. Симеона Верхотурского.\n"
            "Симеон Верхотурский — небесный покровитель Урала.\n"
            "Николаевский монастырь — один из крупнейших на Урале.\n"
            "Как добраться: поезд или автобус из Екатеринбурга\n\n"
            "🏔️ Белогорский монастырь (Пермский край)\n"
            "«Уральский Афон» — на высоте 446 м над уровнем моря.\n"
            "Огромный белый собор виден за десятки километров.\n"
            "В советское время — место мученичества монахов.\n"
            "Малоизвестен но очень значимый.\n"
            "Как добраться: автобус из Перми до Белой Горы\n\n"
            "🕍 Знаменский монастырь (Иркутск)\n"
            "Мощи святителя Иннокентия Иркутского — первого сибирского святого.\n"
            "Здесь похоронена Екатерина Трубецкая — декабристка.\n"
            "Старейший монастырь Иркутска (1693 г.)\n\n"
            "🗺️ Как найти: откройте Яндекс.Карты и введите\n"
            "название монастыря"
        )
    },
    "south": {
        "title": "📍 Монастыри Юга и Крыма",
        "text": (
            "⛰️ Свято-Михайловский монастырь (Сочи, гора Физиабго)\n"
            "Высокогорный монастырь на высоте 600 м.\n"
            "Основан в 1878 г. — первый монастырь на Кавказе.\n"
            "Панорама гор и моря — место удивительной красоты.\n"
            "Малоизвестен туристам — настоящее намоленное место.\n"
            "Как добраться: из Майкопа через Хаджох\n\n"
            "🌊 Свято-Георгиевский монастырь (Крым, мыс Фиолент)\n"
            "Один из древнейших монастырей — основан в IX веке.\n"
            "По преданию основан греческими моряками спасёнными от бури.\n"
            "800 ступеней к морю — место невероятной красоты.\n"
            "Пушкин посещал монастырь в 1820 г.\n\n"
            "🕍 Инкерманский монастырь (Крым, Севастополь)\n"
            "Пещерный монастырь высеченный в скале.\n"
            "Основан в VIII-IX веке. Здесь служил апостол Климент\n"
            "сосланный сюда императором.\n"
            "Мощи: сщмч. Климента Римского\n\n"
            "🕍 Успенский монастырь (Крым, Бахчисарай)\n"
            "Пещерный монастырь в отвесной скале.\n"
            "Чудотворная икона Богородицы Панагия.\n"
            "Рядом — средневековый пещерный город Чуфут-Кале.\n"
            "Место паломничества православных Крыма с XII века.\n\n"
            "🗺️ Как найти: откройте Яндекс.Карты и введите\n"
            "название монастыря"
        )
    },
    "moscow": {
        "title": "🏛️ Москва — святые места",
        "text": (
            "🕍 Храм Христа Спасителя\n"
            "Главный собор России. Воссоздан в 1997 году.\n"
            "Адрес: ул. Волхонка, 15\n"
            "Мощи: частицы мощей многих святых\n\n"
            "🕍 Покровский монастырь (Матрона Московская)\n"
            "Здесь находятся мощи блаженной Матроны Московской.\n"
            "Очередь бывает многочасовой — приходите утром.\n"
            "Адрес: ул. Таганская, 58\n\n"
            "🕍 Свято-Данилов монастырь\n"
            "Старейший монастырь Москвы (1282 г.)\n"
            "Мощи: блгв. кн. Даниила Московского\n"
            "Адрес: Даниловский вал, 22\n\n"
            "🕍 Донской монастырь\n"
            "Мощи: Святителя Тихона, Патриарха Московского\n"
            "Адрес: Донская пл., 1\n\n"
            "🕍 Сергиево-Посадская Лавра\n"
            "50 км от Москвы. Главная обитель России.\n"
            "Мощи: Преподобного Сергия Радонежского"
        )
    },
    "spb": {
        "title": "🏛️ Санкт-Петербург — святые места",
        "text": (
            "🕍 Александро-Невская Лавра\n"
            "Главная обитель Петербурга.\n"
            "Мощи: блгв. кн. Александра Невского\n"
            "Адрес: наб. реки Монастырки, 1\n\n"
            "🕍 Казанский собор\n"
            "Чудотворная Казанская икона Богородицы.\n"
            "Захоронение М.И. Кутузова.\n"
            "Адрес: Казанская пл., 2\n\n"
            "🕍 Исаакиевский собор\n"
            "Один из крупнейших соборов мира.\n"
            "Адрес: Исаакиевская пл., 4\n\n"
            "🕍 Смоленское кладбище\n"
            "Часовня Ксении Петербургской — блаженной.\n"
            "Одно из самых посещаемых мест Петербурга.\n"
            "Мощи: блж. Ксении Петербургской\n\n"
            "🕍 Феодоровский собор\n"
            "Чудотворная Феодоровская икона Богородицы.\n"
            "Адрес: Миргородская ул., 1"
        )
    },
    "sergiev": {
        "title": "⭐ Троице-Сергиева Лавра",
        "text": (
            "Главная православная обитель России.\n"
            "Основана преподобным Сергием Радонежским\n"
            "в 1337 году.\n\n"
            "📍 Адрес: г. Сергиев Посад, Троицкая пл., 1\n"
            "Как добраться из Москвы:\n"
            "— Экспресс с Ярославского вокзала (1 час 10 мин)\n"
            "— Автобус №388 от ВДНХ\n\n"
            "🕍 Главные святыни:\n"
            "— Мощи прп. Сергия Радонежского (Троицкий собор)\n"
            "— Икона Троицы (список с Рублёвской)\n"
            "— Чудотворный Черниговский образ Богородицы\n\n"
            "📋 Что посетить:\n"
            "— Троицкий собор (XIV в.) — мощи Сергия\n"
            "— Успенский собор (XVI в.) — главный собор\n"
            "— Духовская церковь\n"
            "— Источник прп. Сергия\n\n"
            "⏰ Богослужения ежедневно.\n"
            "Ранняя Литургия — 6:30\n"
            "Поздняя Литургия — 9:00"
        )
    },
    "afon": {
        "title": "⛵ Святая гора Афон",
        "text": (
            "Афон — особый монашеский удел Богородицы.\n"
            "Полуостров в Греции, где расположены\n"
            "20 православных монастырей.\n\n"
            "📜 История:\n"
            "По преданию, Богородица посетила Афон\n"
            "и объявила его Своим уделом.\n"
            "Монашество здесь непрерывно с IV века.\n\n"
            "🏛️ Главные монастыри:\n"
            "— Великая Лавра (основана 963 г.)\n"
            "— Ватопед (хранится Пояс Богородицы)\n"
            "— Иверский монастырь (Иверская икона Богородицы)\n"
            "— Свято-Пантелеимонов монастырь (русский)\n\n"
            "👨 Как попасть:\n"
            "— Только мужчины (женщинам вход запрещён)\n"
            "— Нужно получить диамонитирион (разрешение)\n"
            "— Подать заявку через Паломническое бюро\n"
            "— Срок ожидания: 6-12 месяцев\n\n"
            "⛪ Монастырская жизнь:\n"
            "— Служба начинается в 3-4 часа ночи\n"
            "— Время здесь особое — по византийскому\n"
            "— Еда простая, постная\n"
            "— Гости живут в архондарике (гостевой дом)\n\n"
            "✝️ Главные святыни:\n"
            "— Иверская икона Богородицы (Ватопед)\n"
            "— Пояс Пресвятой Богородицы (Ватопед)\n"
            "— Мощи многих святых во всех монастырях"
        )
    },
    "abkhazia": {
        "title": "✝️ Абхазия — святые места",
        "text": (
            "Абхазия — одно из древнейших христианских мест\n"
            "на постсоветском пространстве. Христианство здесь\n"
            "с I века — апостол Симон Кананит принял здесь мученичество.\n\n"
            "🕍 Новоафонский монастырь (Новый Афон)\n"
            "Основан в 1875 г. монахами со Святого Афона.\n"
            "Величественный комплекс у моря — шесть храмов.\n"
            "Один из красивейших монастырей на всём Кавказе.\n"
            "Рядом — Новоафонские пещеры (одни из крупнейших в мире).\n\n"
            "⛪ Храм Симона Кананита (Новый Афон)\n"
            "Один из древнейших храмов — I-X века.\n"
            "Место мученичества апостола Симона Кананита.\n"
            "Мощи: частицы мощей ап. Симона Кананита\n\n"
            "🕳️ Пещера апостола Симона Кананита\n"
            "Место уединения и молитвы апостола.\n"
            "Небольшая пещера в скале у реки Псырцха.\n"
            "Намоленное место удивительной тишины.\n\n"
            "🏛️ Бедийский собор (село Бедиа)\n"
            "Построен в X веке царём Багратом III.\n"
            "Один из шедевров абхазской средневековой архитектуры.\n"
            "Малоизвестен туристам — подлинная древность.\n\n"
            "🏛️ Моквский собор (село Моква)\n"
            "X век. Усыпальница абхазских царей.\n"
            "Огромный пятинефный собор — редкость для Кавказа.\n\n"
            "🏛️ Лыхненский храм (село Лыхны)\n"
            "X век. Один из древнейших действующих храмов Кавказа.\n"
            "Фрески XIV века частично сохранились.\n"
            "Место народных собраний абхазов — священное место.\n\n"
            "⛪ Храм мч. Василиска (село Команы)\n"
            "Место мученичества св. Василиска (ок. 308 г.) —\n"
            "племянника вмч. Феодора Тирона.\n"
            "Здесь же находится место погребения свт. Иоанна Златоуста —\n"
            "великого отца Церкви, скончавшегося в Команах в 407 г.\n"
            "Особо почитаемое место паломничества в Абхазии.\n\n"
            "🗺️ Как найти: откройте Яндекс.Карты и введите\n"
            "название монастыря или храма"
        )
    },
    "world": {
        "title": "🌍 Святые места мира",
        "text": (
            "✝️ Иерусалим, Израиль\n"
            "Главное святое место христианства.\n"
            "— Храм Гроба Господня — место Распятия и Воскресения\n"
            "— Голгофа — холм где был распят Христос\n"
            "— Гефсиманский сад — место моления о чаше\n"
            "— Вифлеем — Церковь Рождества Христова\n"
            "— Назарет — место детства Иисуса\n"
            "— Река Иордан — место Крещения Господня\n\n"
            "🏛️ Рим, Италия\n"
            "— Базилика св. Петра (Ватикан) — мощи ап. Петра\n"
            "— Базилика Сан-Паоло — мощи ап. Павла\n"
            "— Катакомбы — первые христианские захоронения\n\n"
            "⭐ Бари, Италия\n"
            "— Базилика Святого Николая Чудотворца\n"
            "— Мощи свт. Николая перенесены сюда в 1087 г.\n"
            "— Одно из главных мест русских православных паломников\n\n"
            "🇬🇷 Греция\n"
            "— Святая гора Афон — монашеский удел Богородицы\n"
            "— Метеоры — монастыри на отвесных скалах (ЮНЕСКО)\n"
            "— Остров Корфу — мощи свт. Спиридона Тримифунтского\n"
            "— Патмос — остров где ап. Иоанн написал Апокалипсис\n"
            "— Салоники — мощи вмч. Димитрия Солунского\n\n"
            "🕌 Турция\n"
            "— Собор Святой Софии (Стамбул) — величайший храм христианства\n"
            "— Эфес — место служения ап. Иоанна Богослова\n"
            "— Мира Ликийская — место служения Николая Чудотворца\n"
            "— Сардис, Смирна, Пергам — семь церквей Апокалипсиса\n\n"
            "🇷🇸 Сербия\n"
            "— Хиландар (Афон) — сербский монастырь\n"
            "— Студеница — мощи св. Симеона Мироточивого\n"
            "— Острог (Черногория) — пещерный монастырь, мощи свт. Василия\n\n"
            "🇧🇬 Болгария\n"
            "— Рильский монастырь — главная святыня Болгарии\n"
            "— Мощи прп. Иоанна Рильского\n\n"
            "🇨🇾 Кипр\n"
            "— Киккский монастырь — чудотворная икона Богородицы\n"
            "— Мощи ап. Варнавы — основателя Кипрской Церкви\n"
            "— Мощи свт. Спиридона Тримифунтского (часть)"
        )
    },
}

# ========== ОТЗЫВЫ ==========
def add_donation_to_sheet(user_id, username, first_name, amount):
    try:
        scopes = ["https://www.googleapis.com/auth/spreadsheets"]
        creds = Credentials.from_service_account_file(CREDENTIALS_FILE, scopes=scopes)
        client = gspread.authorize(creds); sp = client.open_by_key(SPREADSHEET_ID)
        try:
            sheet = sp.worksheet("Пожертвования")
        except Exception:
            sheet = sp.add_worksheet(title="Пожертвования", rows=2000, cols=6)
            sheet.insert_row(["ID","Username","Имя","Сумма (руб)","Дата","Источник"], 1)
        sheet.append_row([str(user_id), f"@{username}" if username else "—", first_name or "—", str(amount), datetime.now().strftime("%d.%m.%Y %H:%M"), "Telegram"])
        try:
            main_sheet = sp.worksheet("ВераТГ"); col = main_sheet.col_values(1)
            if str(user_id) in col:
                row = col.index(str(user_id)) + 1; val = main_sheet.cell(row, 12).value or "0"; main_sheet.update_cell(row, 12, str(int(val) + 1))
        except Exception as e:
            logging.warning(f"Telegram donation counter not updated: {e}")
        return True
    except Exception as e:
        logging.error(f"Sheets add_donation: {e}")
        return False


REVIEW_SHEET_HEADERS_TG = [
    "ID", "Username", "Имя", "Дата", "Тип", "Отзыв",
    "Номер отзыва", "Статус", "Ответ владельца", "Дата ответа", "Ответил"
]


def ensure_review_sheet_tg(sp=None):
    """Создаёт или расширяет лист отзывов до CRM-структуры."""
    try:
        if sp is None:
            scopes = ["https://www.googleapis.com/auth/spreadsheets"]
            creds = Credentials.from_service_account_file(CREDENTIALS_FILE, scopes=scopes)
            client = gspread.authorize(creds)
            sp = client.open_by_key(SPREADSHEET_ID)
        try:
            sheet = sp.worksheet("Отзывы ВераБот")
        except Exception:
            sheet = sp.add_worksheet(title="Отзывы ВераБот", rows=1000, cols=len(REVIEW_SHEET_HEADERS_TG))
        if getattr(sheet, "col_count", 0) < len(REVIEW_SHEET_HEADERS_TG):
            sheet.resize(cols=len(REVIEW_SHEET_HEADERS_TG))
        current = sheet.row_values(1)
        for index, header in enumerate(REVIEW_SHEET_HEADERS_TG, start=1):
            if len(current) < index or current[index - 1] != header:
                sheet.update_cell(1, index, header)
        return sheet
    except Exception as e:
        logging.error(f"ensure_review_sheet_tg: {e}")
        return None


def ensure_review_sheet_schema_tg():
    ensure_review_sheet_tg()


def sheets_add_review_tg(review_id, user_id, username, first_name, text):
    try:
        scopes = ["https://www.googleapis.com/auth/spreadsheets"]
        creds = Credentials.from_service_account_file(CREDENTIALS_FILE, scopes=scopes)
        client = gspread.authorize(creds)
        sp = client.open_by_key(SPREADSHEET_ID)
        sheet = ensure_review_sheet_tg(sp)
        if not sheet:
            return
        sheet.append_row([
            str(user_id),
            f"@{username}" if username else "—",
            first_name or "—",
            datetime.now().strftime("%d.%m.%Y %H:%M"),
            "Отзыв/пожелание",
            text,
            str(review_id),
            "Новый",
            "—",
            "—",
            "—",
        ])
        try:
            main_sheet = sp.worksheet("ВераТГ")
            col = main_sheet.col_values(1)
            if str(user_id) in col:
                row = col.index(str(user_id)) + 1
                val = main_sheet.cell(row, 11).value or "0"
                main_sheet.update_cell(row, 11, str(int(val) + 1))
        except Exception:
            pass
    except Exception as e:
        logging.error(f"sheets_add_review_tg: {e}")


def sheets_update_review_tg(review_id, status, reply_text="", replied_at="", handled_by="Владелец"):
    import time
    for attempt in range(1, 6):
        try:
            scopes = ["https://www.googleapis.com/auth/spreadsheets"]
            creds = Credentials.from_service_account_file(CREDENTIALS_FILE, scopes=scopes)
            client = gspread.authorize(creds)
            sp = client.open_by_key(SPREADSHEET_ID)
            sheet = ensure_review_sheet_tg(sp)
            if not sheet:
                return
            review_ids = sheet.col_values(7)
            review_id_str = str(review_id)
            if review_id_str in review_ids:
                row = review_ids.index(review_id_str) + 1
                sheet.update_cell(row, 8, status)
                sheet.update_cell(row, 9, reply_text or "—")
                sheet.update_cell(row, 10, replied_at or "—")
                sheet.update_cell(row, 11, handled_by or "Владелец")
                return
            if attempt < 5:
                time.sleep(2)
        except Exception as e:
            logging.error(f"sheets_update_review_tg attempt {attempt}: {e}")
            if attempt < 5:
                time.sleep(2)
    logging.warning(f"Отзыв Telegram #{review_id} не найден в Google Sheets")


def sheets_update_latest_review_by_user_tg(user_id, status, reply_text, replied_at, handled_by="Владелец"):
    try:
        scopes = ["https://www.googleapis.com/auth/spreadsheets"]
        creds = Credentials.from_service_account_file(CREDENTIALS_FILE, scopes=scopes)
        client = gspread.authorize(creds)
        sp = client.open_by_key(SPREADSHEET_ID)
        sheet = ensure_review_sheet_tg(sp)
        if not sheet:
            return
        ids = sheet.col_values(1)
        rows = [i + 1 for i, value in enumerate(ids) if value == str(user_id)]
        if not rows:
            return
        row = rows[-1]
        sheet.update_cell(row, 8, status)
        sheet.update_cell(row, 9, reply_text or "—")
        sheet.update_cell(row, 10, replied_at or "—")
        sheet.update_cell(row, 11, handled_by or "Владелец")
    except Exception as e:
        logging.error(f"sheets_update_latest_review_by_user_tg: {e}")

# ========== МЕНЮ ==========
def main_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="☦️ Начать за 60 секунд", callback_data="quick_start")],
        [
            InlineKeyboardButton(text="🙏 Молитвы",            callback_data="prayers"),
            InlineKeyboardButton(text="📅 Календарь",          callback_data="calendar"),
        ],
        [
            InlineKeyboardButton(text="⛪ Таинства и обряды",  callback_data="sacraments"),
            InlineKeyboardButton(text="👼 Святые",             callback_data="saints"),
        ],
        [
            InlineKeyboardButton(text="🏛️ Святые места",       callback_data="holy_places"),
            InlineKeyboardButton(text="📚 Библиотека",         callback_data="library"),
        ],
        [
            InlineKeyboardButton(text="📸 Определить по фото", callback_data="photo_menu"),
            InlineKeyboardButton(text="🗺️ Найти храм рядом",   callback_data="find_church"),
        ],
        [InlineKeyboardButton(text="📖 Евангельская мысль", callback_data="daily_gospel")],
        [
            InlineKeyboardButton(text="👤 Мой профиль",        callback_data="profile"),
            InlineKeyboardButton(text="❓ Задать вопрос",      callback_data="ask_question"),
        ],
        [
            InlineKeyboardButton(text="🕯️ Пожертвование на развитие проекта", callback_data="donate"),
        ],
        [
            InlineKeyboardButton(text="💬 Отзыв или пожелание по улучшению", callback_data="review"),
        ],
        [InlineKeyboardButton(text="🤝 Пригласить близкого", callback_data="invite_friend")],
    ])

def back_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Главное меню", callback_data="main_menu")]
    ])

def back_section(section):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Назад",        callback_data=section)],
        [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")],
    ])

def prayers_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✨ Молитва дня", callback_data="prayer_of_day")],
        [InlineKeyboardButton(text="🙏 Молитва за меня", callback_data="prayer_for_me")],
        [
            InlineKeyboardButton(text="🌅 Утренняя (рус)",    callback_data="prayer_morning_ru"),
            InlineKeyboardButton(text="🌅 Утренняя (цс)",     callback_data="prayer_morning_cs"),
        ],
        [
            InlineKeyboardButton(text="🌙 Вечерняя (рус)",   callback_data="prayer_evening_ru"),
            InlineKeyboardButton(text="🌙 Вечерняя (цс)",    callback_data="prayer_evening_cs"),
        ],
        [
            InlineKeyboardButton(text="🍽️ Перед едой",        callback_data="prayer_before_meal"),
            InlineKeyboardButton(text="🙏 После еды",         callback_data="prayer_after_meal"),
        ],
        [
            InlineKeyboardButton(text="💛 О здравии",         callback_data="prayer_zdravie"),
            InlineKeyboardButton(text="🕯️ Об упокоении",      callback_data="prayer_upokoenie"),
        ],
        [
            InlineKeyboardButton(text="🚗 В дороге",          callback_data="prayer_doroga"),
            InlineKeyboardButton(text="👶 О детях",           callback_data="prayer_o_detyah"),
        ],
        [
            InlineKeyboardButton(text="⭐ Николаю Чудотворцу", callback_data="prayer_nikolay"),
            InlineKeyboardButton(text="🕯️ Матроне Московской", callback_data="prayer_matrona"),
        ],
        [
            InlineKeyboardButton(text="✝️ Правило ко Причастию", callback_data="prayer_prichaschenie"),
            InlineKeyboardButton(text="📖 Канон покаянный",      callback_data="prayer_pokayanny_kanon"),
        ],
        [InlineKeyboardButton(text="◀️ Главное меню", callback_data="main_menu")],
    ])

def sacraments_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📿 Исповедь",         callback_data="sacr_ispoved"),
            InlineKeyboardButton(text="✝️ Причастие",        callback_data="sacr_prichaschenie"),
        ],
        [
            InlineKeyboardButton(text="💧 Крещение",         callback_data="sacr_kreshchenie"),
            InlineKeyboardButton(text="💍 Венчание",         callback_data="sacr_venchanie"),
        ],
        [
            InlineKeyboardButton(text="🕯️ Отпевание",        callback_data="sacr_otpevanie"),
            InlineKeyboardButton(text="🫒 Соборование",      callback_data="sacr_sobor"),
        ],
        [
            InlineKeyboardButton(text="🏠 Освящение",        callback_data="sacr_osvyashchenie"),
            InlineKeyboardButton(text="🕯️ Как ставить свечи", callback_data="sacr_svecha"),
        ],
        [
            InlineKeyboardButton(text="📝 Как подавать записки", callback_data="sacr_zapiska"),
            InlineKeyboardButton(text="✍️ Составить записку", callback_data="make_zapiska"),
            InlineKeyboardButton(text="⛪ Как вести себя в храме", callback_data="sacr_v_hrame"),
        ],
        [InlineKeyboardButton(text="◀️ Главное меню", callback_data="main_menu")],
    ])

def holy_places_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🏙️ Москва",                       callback_data="place_moscow"),
            InlineKeyboardButton(text="🏙️ Санкт-Петербург",              callback_data="place_spb"),
        ],
        [InlineKeyboardButton(text="📍 Монастыри Подмосковья",            callback_data="place_podmoskove")],
        [InlineKeyboardButton(text="📍 Монастыри Центральной России",     callback_data="place_central")],
        [InlineKeyboardButton(text="📍 Монастыри Севера и Северо-Запада", callback_data="place_northwest")],
        [InlineKeyboardButton(text="📍 Монастыри Урала и Сибири",        callback_data="place_ural_siberia")],
        [InlineKeyboardButton(text="📍 Монастыри Юга и Крыма",           callback_data="place_south")],
        [InlineKeyboardButton(text="✝️ Абхазия — святые места",          callback_data="place_abkhazia")],
        [
            InlineKeyboardButton(text="⛵ Афон",                          callback_data="place_afon"),
            InlineKeyboardButton(text="🌍 Святые места мира",             callback_data="place_world"),
        ],
        [InlineKeyboardButton(text="◀️ Главное меню", callback_data="main_menu")],
    ])

def calendar_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📅 Сегодня",                callback_data="cal_today")],
        [InlineKeyboardButton(text="🥗 Пост сегодня",           callback_data="cal_fast_today")],
        [InlineKeyboardButton(text="🎉 Православные праздники", callback_data="cal_feasts")],
        [InlineKeyboardButton(text="🥗 Все посты",              callback_data="cal_fasts")],
        [InlineKeyboardButton(text="👼 Именинники сегодня",     callback_data="cal_namedays")],
        [InlineKeyboardButton(text="🔍 Найти именины по имени", callback_data="cal_find_angel")],
        [InlineKeyboardButton(text="🥚 Пасха — всё о главном празднике", callback_data="cal_pasxa")],
        [InlineKeyboardButton(text="💧 Крещение Господне — традиции",    callback_data="cal_kreschenije")],
        [InlineKeyboardButton(text="◀️ Главное меню",           callback_data="main_menu")],
    ])

def question_depth_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💬 Кратко",     callback_data="depth_short")],
        [InlineKeyboardButton(text="📖 Развёрнуто", callback_data="depth_medium")],
        [InlineKeyboardButton(text="🙏 Глубоко",    callback_data="depth_deep")],
        [InlineKeyboardButton(text="◀️ Назад",      callback_data="main_menu")],
    ])

def photo_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🕌 Фото храма или монастыря", callback_data="photo_church")],
        [InlineKeyboardButton(text="🖼️ Фото иконы",              callback_data="photo_icon")],
        [InlineKeyboardButton(text="◀️ Главное меню",            callback_data="main_menu")],
    ])

def profile_menu(user):
    church = user.get("church_name") or "не указано"
    birth  = user.get("birth_date")  or "не указана"
    angel  = user.get("angel_day")   or "не найден"
    remind = user.get("remind_days") or 3
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=f"✏️ Имя: {church}",
            callback_data="profile_edit_name"
        )],
        [InlineKeyboardButton(
            text=f"🎂 Дата рождения: {birth}",
            callback_data="profile_edit_birth"
        )],
        [InlineKeyboardButton(
            text=f"👼 Возможный день памяти покровителя: {angel}",
            callback_data="profile_angel_info"
        )],
        [InlineKeyboardButton(
            text=f"🔔 Напомнить за {remind} дн.",
            callback_data="profile_remind"
        )],
        [InlineKeyboardButton(text="⭐ Избранные молитвы",             callback_data="favorites")],
        [InlineKeyboardButton(text="🙏 Молитва небесному покровителю", callback_data="profile_patron_prayer")],
        [InlineKeyboardButton(
            text="🔔 Утренние уведомления: ВКЛ" if user.get("notifications", 0) else "🔕 Утренние уведомления: ВЫКЛ",
            callback_data="toggle_notifications"
        )],
        [InlineKeyboardButton(text="🕯️ Пожертвование",                callback_data="donate")],
        [InlineKeyboardButton(text="◀️ Главное меню",                  callback_data="main_menu")],
    ])

def onboarding_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Указать имя и дату рождения", callback_data="onboard_start")],
        [InlineKeyboardButton(text="⏭️ Пропустить",                 callback_data="onboard_skip")],
    ])

def remind_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="За 1 день",   callback_data="remind_1"),
            InlineKeyboardButton(text="За 3 дня",    callback_data="remind_3"),
            InlineKeyboardButton(text="За неделю",   callback_data="remind_7"),
        ],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="profile")],
    ])

def subscription_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🕯️ Поддержать проект", callback_data="donate")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="profile")],
    ])


# ========== AI ФУНКЦИИ ==========
async def ask_claude(question: str, depth: str) -> str:
    depth_prompts = {
        "short": "Ответь кратко — 2–3 предложения.",
        "medium": "Ответь развёрнуто — 5–7 предложений.",
        "deep": "Дай вдумчивый ответ, ясно отделяя проверяемые факты от общего духовного совета.",
    }
    try:
        msg = await claude_messages_create(
            model="claude-sonnet-4-5",
            max_tokens={"short": 300, "medium": 650, "deep": 1100}.get(depth, 650),
            system=(
                "Ты справочный православный помощник, но не священник. "
                "Обращайся нейтрально и уважительно, без слов «чадо», «душа моя» и без имитации духовника. "
                "Не выдумывай цитаты, даты, чудеса, церковные правила или благословения. "
                "Если точный факт не дан в контексте, предложи проверить его по надёжному церковному источнику. "
                "В вопросах Таинств, поста по здоровью и личного духовного руководства советуй обратиться к священнику своего прихода. "
                "Отвечай по-русски, бережно, без осуждения. " + depth_prompts.get(depth, depth_prompts["medium"])
            ),
            messages=[{"role": "user", "content": question}],
        )
        return msg.content[0].text
    except Exception as e:
        logging.error(f"Ошибка Claude: {e}")
        record_critical_error("claude_tg", e)
        return "error"


async def analyze_photo_gpt(photo_url: str, photo_type: str, local_path: str = None) -> str:
    if photo_type == "church":
        prompt = (
            "На фотографии православный храм или монастырь. "
            "Определи: 1) Название если можешь; 2) Архитектурный стиль и эпоха; "
            "3) Особенности (купола, колокольня, цвет); 4) Историческое значение. "
            "Если не можешь определить конкретный храм — опиши архитектуру и символику. "
            "Если на фото не храм — вежливо скажи об этом. Отвечай по-русски."
        )
    else:
        prompt = (
            "На фотографии православная икона. "
            "Определи: 1) Кто изображён (имя святого); 2) Тип иконы; "
            "3) Атрибуты и символика; 4) Чему помогает молитва этому святому; "
            "5) Краткое житие. Если не можешь определить — опиши что видишь. "
            "Если на фото не икона — вежливо скажи об этом. Отвечай по-русски."
        )
    try:
                # Читаем локальный файл и кодируем в base64
        if local_path:
            with open(local_path, "rb") as f:
                image_data = base64.b64encode(f.read()).decode("utf-8")
            image_content = {
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{image_data}"}
            }
        else:
            image_content = {
                "type": "image_url",
                "image_url": {"url": photo_url}
            }

        response = await openai_client.chat.completions.create(
            model="gpt-4o",
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    image_content,
                ]
            }],
            max_tokens=800
        )
        return response.choices[0].message.content
    except Exception as e:
        logging.error(f"GPT-4o vision ошибка: {e}")
        return "Не удалось проанализировать фото. Попробуйте ещё раз."

async def transcribe_voice(file_path: str) -> str:
    path = Path(file_path)
    try:
        with path.open("rb") as f:
            response = await openai_client.audio.transcriptions.create(model="whisper-1", file=f, language="ru")
        return (response.text or "").strip()
    finally:
        with suppress(Exception):
            path.unlink(missing_ok=True)


# ========== КАНАЛ — АВТОПОСТИНГ ==========
async def get_daily_saint() -> str:
    """Legacy-safe helper: never invents today's saint or miracles."""
    today = date_ru("short")
    feast = get_todays_feast()
    if feast:
        return (
            f"✝️ {today}\n\n{feast}\n\n"
            "Краткое напоминание о смысле праздника следует сверять с церковным календарём своего прихода."
        )
    return (
        f"✝️ {today}\n\n"
        "Церковь хранит память святых как свидетельство веры, мужества и милосердия. "
        "Точный календарь памятей на сегодня лучше уточнить по календарю своего прихода."
    )


async def get_daily_quote() -> str:
    reference, verse, reflection = GOSPEL_REFLECTIONS[date.today().toordinal() % len(GOSPEL_REFLECTIONS)]
    return f"✨ Мысль дня\n\n«{verse}»\n({reference})\n\n{reflection}"


def get_fast_today() -> str:
    return fasting_guidance_text()



async def get_daily_gospel() -> str:
    return gospel_reflection_text()


def channel_post_exists(post_key: str) -> bool:
    conn = db_connect()
    row = conn.execute(
        "SELECT status,COALESCE(message_id,'') FROM channel_posts WHERE post_key=?",
        (post_key,),
    ).fetchone()
    conn.close()
    return bool(row and row[0] == "sent" and str(row[1]).strip())


def channel_posts_today(msk_now: datetime = None):
    """Возвращает успешно опубликованные сегодня записи журнала канала."""
    msk_now = msk_now or (datetime.utcnow() + timedelta(hours=3))
    date_key = msk_now.strftime("%Y-%m-%d")
    try:
        conn = db_connect()
        rows = conn.execute(
            """SELECT slot, rubric, topic, created_at
               FROM channel_posts
               WHERE post_date=? AND status='sent' AND COALESCE(message_id,'')<>''
               ORDER BY slot, created_at""",
            (date_key,),
        ).fetchall()
        conn.close()
        return rows
    except Exception as e:
        logging.error(f"Канал ТГ: не удалось прочитать журнал за сегодня: {e}")
        return []


def all_channel_slots(msk_now: datetime):
    """Все запланированные на текущий день публикации, отсортированные по времени."""
    return sorted(build_daily_slots(msk_now) + special_slots(msk_now), key=lambda x: x[0])


def latest_due_channel_slot(msk_now: datetime):
    """Последний уже наступивший слот дня."""
    due = [slot for slot in all_channel_slots(msk_now) if slot[0] <= msk_now.hour]
    return due[-1] if due else None


def select_catchup_channel_slot(msk_now: datetime):
    """Выбирает один актуальный пропущенный слот с приоритетом изображения."""
    due = [slot for slot in all_channel_slots(msk_now) if slot[0] <= msk_now.hour]
    rows = channel_posts_today(msk_now)
    sent_hours = []
    for slot, _rubric, _topic, _created in rows:
        try:
            sent_hours.append(int(str(slot).split(":", 1)[0]))
        except Exception:
            pass
    latest_sent_hour = max(sent_hours, default=-1)
    date_key = msk_now.strftime("%Y-%m-%d")
    eligible = []
    for slot in due:
        hour, rubric, cta_key, _prompt = slot
        post_key = f"{date_key}_{hour:02d}_{rubric}"
        if hour <= latest_sent_hour or channel_post_exists(post_key):
            continue
        eligible.append(slot)
    if not eligible:
        return None
    recent = [slot for slot in eligible if msk_now.hour - slot[0] <= 2] or [eligible[-1]]
    visual = [slot for slot in recent if select_channel_visual(msk_now, slot[0], slot[2], slot[1])]
    return max(visual or recent, key=lambda item: item[0])


def save_channel_post(post_key, post_date, slot, rubric, topic, content, status, message_id=""):
    try:
        conn = db_connect()
        conn.execute(
            """INSERT OR REPLACE INTO channel_posts
               (post_key,post_date,slot,rubric,topic,content,status,created_at,message_id)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (post_key, post_date, slot, rubric, topic[:250], content[:4000], status, datetime.now().isoformat(), str(message_id or "")),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logging.error(f"Канал ТГ: журнал публикации не сохранён: {e}")


def recent_channel_topics(limit: int = 35) -> str:
    try:
        conn = db_connect()
        rows = conn.execute(
            "SELECT rubric,topic FROM channel_posts WHERE status='sent' AND COALESCE(message_id,'')<>'' ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        conn.close()
        return "\n".join(f"- {rubric}: {topic}" for rubric, topic in rows if topic)
    except Exception:
        return ""


def extract_topic(text: str) -> str:
    return " ".join((text or "").replace("\n", " ").split())[:180]



CHANNEL_TITLE_EMOJI = {
    "morning": "🌅", "quote": "✝️", "saint": "👼", "guidance": "🕯️",
    "practical": "⛪", "story": "📖", "evening": "🌙", "qa": "❓",
    "life": "📖", "film": "📚", "gospel": "📖", "photo": "📸",
    "church": "⛪", "showcase_prayer": "🙏", "showcase_photo": "📸",
    "showcase_angel": "👼", "showcase_confession": "📿",
    "interactive": "💬", "community": "🕊️",
}

CHANNEL_FALLBACK_TITLES = {
    "morning": "Доброе начало дня",
    "quote": "Мысль, которую стоит сохранить",
    "saint": "Святой или праздник дня",
    "guidance": "Когда сердцу непросто",
    "practical": "Практическая вера",
    "story": "История, которая укрепляет",
    "evening": "Завершим день с молитвой",
    "qa": "Вопрос, который задают многие",
    "life": "Житие и пример веры",
    "film": "Что посмотреть или прочитать",
    "gospel": "Евангельская мысль",
    "photo": "Как узнать образ на иконе",
    "church": "Храм и православная традиция",
    "showcase_prayer": "Молитва рядом в нужный момент",
    "showcase_photo": "Не знаете, кто изображён на иконе?",
    "showcase_angel": "Как узнать своего небесного покровителя",
    "showcase_confession": "Как подготовиться к первой исповеди",
    "interactive": "Выберите следующую тему",
    "community": "История одного пользователя",
}


def clean_channel_markup(text: str) -> str:
    """Убирает сырой Markdown и технические символы из публикации канала."""
    value = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    value = re.sub(r"```.*?```", "", value, flags=re.S)
    value = re.sub(r"\*\*(.*?)\*\*", r"\1", value, flags=re.S)
    value = re.sub(r"__(.*?)__", r"\1", value, flags=re.S)
    value = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"\1", value)
    value = re.sub(r"(?<!_)_([^_\n]+)_(?!_)", r"\1", value)
    value = value.replace("`", "")
    value = re.sub(r"(?m)^\s{0,3}#{1,6}\s*", "", value)
    value = re.sub(r"(?m)^\s*[-*]\s+", "• ", value)
    value = re.sub(r"https?://\S+", "", value)
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r"\n[ \t]+", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def _shorten_at_sentence(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text.strip()
    chunk = text[:limit].rstrip()
    boundaries = [chunk.rfind(". "), chunk.rfind("! "), chunk.rfind("? "), chunk.rfind("\n\n")]
    cut = max(boundaries)
    if cut >= int(limit * 0.62):
        chunk = chunk[:cut + 1]
    else:
        chunk = chunk.rsplit(" ", 1)[0]
    return chunk.rstrip(" ,;:") + "…"


def _split_readable_paragraphs(paragraphs, max_paragraph_len: int = 330):
    result = []
    for paragraph in paragraphs:
        paragraph = " ".join(paragraph.split()).strip()
        if not paragraph:
            continue
        if len(paragraph) <= max_paragraph_len:
            result.append(paragraph)
            continue
        sentences = re.split(r"(?<=[.!?…])\s+", paragraph)
        current = ""
        for sentence in sentences:
            candidate = f"{current} {sentence}".strip()
            if current and len(candidate) > max_paragraph_len:
                result.append(current)
                current = sentence
            else:
                current = candidate
        if current:
            result.append(current)
    return result


def polish_channel_text(
    text: str,
    cta_key: str,
    rubric: str,
    *,
    has_visual: bool = False,
    platform: str = "max",
) -> str:
    """Делает AI-текст похожим на отредактированную публикацию, а не на сырой ответ модели."""
    cleaned = clean_channel_markup(text)
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", cleaned) if p.strip()]
    fallback_title = CHANNEL_FALLBACK_TITLES.get(cta_key, rubric.capitalize())

    title = ""
    body_paragraphs = paragraphs[:]
    if paragraphs:
        candidate = " ".join(paragraphs[0].split()).strip(" —–-:;,.\"«»")
        looks_like_title = len(candidate) <= 92 and candidate.count(".") <= 1 and "\n" not in candidate
        if looks_like_title:
            title = candidate
            body_paragraphs = paragraphs[1:]
    if not title:
        title = fallback_title

    # Не позволяем модели превращать заголовок в назидательную длинную фразу.
    if len(title) > 92:
        title = fallback_title
    title = title.strip(" —–-:;,.\"«»")
    emoji = CHANNEL_TITLE_EMOJI.get(cta_key, "☦️")
    if not title.startswith(tuple(CHANNEL_TITLE_EMOJI.values())):
        title = f"{emoji} {title}"

    body_paragraphs = _split_readable_paragraphs(body_paragraphs)
    # Заголовок + максимум пять коротких смысловых абзацев.
    body_paragraphs = body_paragraphs[:5]
    if not body_paragraphs and cleaned:
        source = cleaned
        if source.startswith(title.replace(f"{emoji} ", "")):
            source = source[len(title.replace(f"{emoji} ", "")):].lstrip(" .:—-\n")
        if source:
            body_paragraphs = _split_readable_paragraphs([source])[:5]

    result = title
    if body_paragraphs:
        result += "\n\n" + "\n\n".join(body_paragraphs)

    if platform == "telegram":
        max_chars = 760 if has_visual else 1180
    else:
        max_chars = 1180 if has_visual else 1350
    result = _shorten_at_sentence(result, max_chars)
    return clean_channel_markup(result)


FALLBACK_POSTS = {
    "morning": "🌅 Господи, благослови наступающий день. Даруй нам мир в сердце, мудрость в словах и силы делать добро. Помоги не осуждать, не унывать и помнить о Тебе в каждом деле. Аминь.",
    "quote": "✝️ Мир в душе начинается с внимания к собственному сердцу. Прежде чем осудить другого, остановимся и попросим у Бога кротости и рассудительности.",
    "saint": "📅 Сегодня Церковь вспоминает святых, которые своей жизнью показали верность Богу. Их пример напоминает: святость начинается с небольших ежедневных решений — молитвы, милосердия и честности.",
    "guidance": "🕯️ Когда молитва не идёт, не нужно отчаиваться. Скажите Богу несколько простых слов своими словами и останьтесь в тишине. Верность важнее сильных чувств.",
    "practical": "⛪ Первый шаг в храме не требует идеальной подготовки. Придите немного заранее, встаньте там, где удобно, и спокойно наблюдайте за службой. Если что-то непонятно, после богослужения можно вежливо спросить служителя храма.",
    "story": "👼 Святые становились святыми не потому, что у них не было трудностей, а потому, что они снова и снова выбирали верность Богу. Их жизнь учит нас не бояться начинать заново.",
    "evening": "🌙 Господи, благодарю Тебя за прошедший день. Прости всё, чем я согрешил словом, делом и мыслью. Сохрани моих близких и даруй нам мирный сон. Аминь.",
    "qa": "❓ Можно ли молиться своими словами? Да. Церковные молитвы учат нас, но Господь слышит и искреннее обращение сердца. Говорите просто, честно и с доверием.",
    "life": "📖 Жития святых напоминают, что вера раскрывается в поступках: терпении, заботе о ближнем, покаянии и молитве. Даже небольшой добрый шаг может стать началом большого внутреннего изменения.",
    "film": "📽️ Для семейного просмотра выберите проверенный документальный фильм о православных святынях или истории монастыря. После просмотра обсудите, какая мысль особенно затронула каждого.",
    "showcase_prayer": "🙏 Не знаете, какую молитву прочитать в тревоге, дороге, болезни или перед сном? В православном помощнике молитвы собраны по жизненным ситуациям — нужное можно открыть за несколько секунд.",
    "showcase_photo": "📸 Иногда дома хранится икона, но семья уже не помнит, кто на ней изображён. Отправьте фотографию православному помощнику — он постарается определить образ и объяснить символы.",
    "showcase_angel": "👼 День ангела связан с памятью святого, чьё имя человек носит в Крещении. В помощнике можно найти имя и посмотреть возможные дни памяти.",
    "showcase_confession": "📿 Первая исповедь часто пугает неизвестностью. В помощнике есть спокойная пошаговая памятка: как подготовиться, что говорить и как проходит Таинство.",
    "interactive": "💬 Какую тему разобрать следующей: молитву, первую исповедь, день ангела или внутреннюю тревогу? Выберите вариант — канал будет развиваться по реальным запросам читателей.",
    "community": "🕊️ Один из пользователей поделился, что помощник помог спокойнее сделать первый шаг к церковной жизни. Иногда человеку нужна не длинная лекция, а понятный следующий шаг и бережная поддержка.",
}


async def generate_channel_post(prompt: str, cta_key: str, rubric: str, visual_prompt_note: str = ""):
    history = recent_channel_topics(35)
    history_note = f"\n\nНе повторяй эти недавние темы:\n{history}" if history else ""
    visual_note = f"\n\n{visual_prompt_note}" if visual_prompt_note else ""
    length_rule = "560–720" if visual_prompt_note else "800–1150"
    full_prompt = (
        prompt + visual_note + history_note +
        f"\nНапиши редакционный пост объёмом {length_rule} символов. "
        "Первая строка — мягкий живой заголовок до 70 символов. "
        "Затем 3–5 коротких абзацев: одна понятная мысль, один жизненный пример и практический вывод. "
        "Не используй Markdown, звёздочки, решётки, обратные кавычки, ссылки и хэштеги. "
        "Не пиши стену текста и не повторяй одинаковые вступления."
    )
    try:
        msg = await asyncio.wait_for(
            claude_messages_create(
            model="claude-sonnet-4-5",
            max_tokens=480,
            system=(
                "Ты редактор премиального православного медиа. Пиши тепло, спокойно, человечно и без назидательного тона. "
                "Опирайся на православную традицию. Не представляйся священником, не давай личных благословений, "
                "не выдумывай цитаты, факты, чудеса, фильмы или церковные правила. "
                "Каждый абзац должен быть коротким и легко читаться с телефона. "
                "Не добавляй рекламу: компактный CTA добавит программа. Не используй Markdown-разметку."
            ),
                messages=[{"role": "user", "content": full_prompt}],
            ),
            timeout=45,
        )
        post_text = msg.content[0].text.strip()
        if len(post_text) < 60:
            raise RuntimeError("AI вернул слишком короткий текст")
    except Exception as e:
        logging.error(f"Канал ТГ: генерация {rubric} не удалась, fallback: {e}")
        post_text = FALLBACK_POSTS.get(cta_key, FALLBACK_POSTS["guidance"])

    post_text = polish_channel_text(
        post_text, cta_key, rubric,
        has_visual=bool(visual_prompt_note),
        platform="telegram",
    )
    return post_text, extract_topic(post_text)


def build_daily_slots(msk_now: datetime):
    day=msk_now.strftime("%d.%m"); weekday=msk_now.weekday()
    midday_rotation={
        0:("церковное слово","practical","Объясни один церковный термин простыми словами. Не выдумывай происхождение или правила."),
        1:("вопрос новичка","qa","Разбери частый вопрос начинающего. Отделяй общецерковную норму от приходской практики."),
        2:("история святого","story","Используй только сведения из контекста; если их недостаточно, дай общий урок без биографических выдумок."),
        3:("храм и традиция","church","Объясни одну традицию без категоричных указаний и напомни, что местная практика может отличаться."),
        4:("подготовка к Таинству","practical","Дай общую памятку и предложи уточнить правила у священника своего прихода."),
        5:("житие и пример","story","Расскажи проверяемую мысль о христианской добродетели; не придумывай чудеса и исторические детали."),
        6:("воскресное размышление","gospel","Раскрой евангельскую мысль для семейного разговора. Не называй её богослужебным чтением дня."),
    }; midday=midday_rotation[weekday]
    return [(7,"утренняя молитва","morning",f"Короткая утренняя публикация на {day}: благодарность и один спокойный настрой."),(9,"святой или праздник дня","saint","__DYNAMIC_SAINT__"),(13,midday[0],midday[1],midday[2]),(20,"вечерняя молитва","evening","Короткая вечерняя публикация: благодарность, просьба о прощении и мирном сне.")]



def dynamic_saint_prompt(msk_now: datetime) -> str:
    date_text = msk_now.strftime("%d.%m")
    feast = FIXED_FEASTS.get(date_text, "")
    if feast:
        return f"Сегодня {date_text}, фиксированный праздник: {feast}. Объясни его смысл, опираясь только на общеизвестные проверяемые сведения, и дай один практический вывод. Не выдумывай традиции и факты."
    return (
        f"Сегодня {date_text}. Напиши материал о том, зачем Церковь хранит память святых и как их пример помогает христианину. "
        "Не называй конкретного святого памятью сегодняшнего дня и не придумывай календарные сведения."
    )



TRUSTED_MEDIA_LIBRARY = [
    "фильм «Остров» (2006), режиссёр Павел Лунгин",
    "фильм «Поп» (2009), режиссёр Владимир Хотиненко",
    "книга «Несвятые святые» митрополита Тихона (Шевкунова)",
    "книга Ивана Шмелёва «Лето Господне»",
    "сборник свидетельств «Отец Арсений»",
]

def trusted_media_for_week(msk_now: datetime) -> str:
    return TRUSTED_MEDIA_LIBRARY[int(msk_now.strftime("%W")) % len(TRUSTED_MEDIA_LIBRARY)]

def special_slots(msk_now: datetime):
    wd=msk_now.weekday()
    if wd==1: return [(17,"возможности помощника","showcase_prayer",FALLBACK_POSTS["showcase_prayer"])]
    if wd==2: return [(17,"выбор темы читателями","interactive","INTERACTIVE_WEEKLY")]
    if wd==3: return [(17,"практическая помощь","showcase_confession",FALLBACK_POSTS["showcase_confession"])]
    if wd==4:
        voted=top_interactive_topic("Telegram"); return [(17,"тема по выбору читателей","guidance",interactive_topic_prompt(voted))] if voted else []
    if wd==5: return [(11,"житие недели","life","Расскажи только проверяемый общий урок из жития святого; не выдумывай факты и чудеса.")]
    if wd==6:
        item=trusted_media_for_week(msk_now); return [(11,"книга или фильм недели","film",f"Представь проверенную рекомендацию: {item}. Не добавляй неподтверждённых дат, наград или сюжетных подробностей.")]
    return []



async def publish_channel_slot(msk_now, hour, rubric, cta_key, prompt):
    """Публикует один слот. Lock исключает дубль при одновременном старте и плановом цикле."""
    async with CHANNEL_PUBLISH_LOCK:
        date_key = msk_now.strftime("%Y-%m-%d")
        post_key = f"{date_key}_{hour:02d}_{rubric}"
        if channel_post_exists(post_key):
            return False
        if prompt == "__DYNAMIC_SAINT__":
            prompt = dynamic_saint_prompt(msk_now)
        visual = select_channel_visual(msk_now, hour, cta_key, rubric)
        variant = "b" if (int(msk_now.strftime("%Y%m%d")) + int(hour)) % 2 else "a"
        source = make_post_source("t", msk_now, hour, cta_key, variant)
        record_post_experiment(source, "Telegram", post_key, cta_key, variant)
        post_text, topic = await generate_channel_post(
            prompt, cta_key, rubric,
            visual_prompt_note=visual.get("prompt_note", "") if visual else "",
        )
        prefer_generated = bool(visual) and not (hour == 9 or cta_key in {"saint", "life", "story", "showcase_photo"})
        message_id = ""
        try:
            message_id = await asyncio.wait_for(
                send_channel_post(
                    post_text,
                    cta_key,
                    with_photo=bool(visual),
                    msk_now=msk_now,
                    photo_urls=visual.get("urls") if visual else None,
                    visual_title=visual.get("title", "") if visual else "",
                    source_override=source,
                    generation_prompt=build_channel_image_prompt(hour, cta_key, rubric) if visual else "",
                    cache_key=f"shared:{date_key}:{hour}:{rubric}:{cta_key}",
                    prefer_generated=prefer_generated,
                    show_visual_title=bool(visual) and not prefer_generated,
                ),
                timeout=CHANNEL_POST_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            logging.error(
                f"Канал ТГ: публикация {hour:02d}:00 превысила {CHANNEL_POST_TIMEOUT_SECONDS:.0f} секунд; "
                "отправляю текстовый fallback"
            )
            try:
                fallback_text = add_channel_cta(clean_channel_markup(post_text), cta_key)[:4096]
                posted = await asyncio.wait_for(
                    bot.send_message(
                        CHANNEL_ID,
                        fallback_text,
                        reply_markup=channel_button(cta_key, source),
                    ),
                    timeout=30,
                )
                message_id = str(posted.message_id)
            except Exception as fallback_error:
                logging.error(f"Канал ТГ: аварийный текстовый fallback не отправлен: {fallback_error}")
        ok = bool(message_id)
        save_channel_post(
            post_key, date_key, f"{hour:02d}:00", rubric, topic, post_text,
            "sent" if ok else "failed", message_id=message_id,
        )
        save_post_source(post_key, source, variant)
        if ok:
            set_app_setting("tg_last_channel_failure", "")
            logging.info(f"Канал ТГ: опубликовано — {rubric}")
            if hour == 7:
                asyncio.create_task(morning_broadcast())
        else:
            set_app_setting(
                "tg_last_channel_failure",
                f"{datetime.now().isoformat()} | {hour:02d}:00 | {rubric}"
            )
            logging.error(f"Канал ТГ: не опубликовано — {rubric}; будет автоматический повтор")
        return ok


async def verify_channel_access() -> tuple[bool, str]:
    """Проверяет, видит ли бот канал и имеет ли право публиковать."""
    try:
        me = await bot.get_me()
        chat = await bot.get_chat(CHANNEL_ID)
        member = await bot.get_chat_member(CHANNEL_ID, me.id)
        status = getattr(member, "status", "unknown")
        can_post = getattr(member, "can_post_messages", None)
        ok = status in {"administrator", "creator"} and can_post is not False
        detail = f"канал={getattr(chat, 'title', CHANNEL_ID)}, статус={status}, can_post={can_post}"
        return ok, detail
    except Exception as e:
        return False, f"ошибка проверки доступа: {e}"


async def channel_startup_check_and_catchup():
    """
    Проверяет доступ к каналу и восстанавливает одну актуальную публикацию после рестарта.
    Не публикует все пропущенные слоты разом, чтобы не засорять канал.
    """
    msk_now = datetime.utcnow() + timedelta(hours=3)
    access_ok, detail = await verify_channel_access()
    if access_ok:
        logging.info(f"Канал ТГ: доступ подтверждён ({detail})")
    else:
        logging.error(f"Канал ТГ: нет подтверждённого доступа ({detail})")
        try:
            await bot.send_message(
                OWNER_ID,
                "⚠️ Канал «С верой» не прошёл проверку доступа.\n\n"
                f"{detail}\n\n"
                "Проверьте, что бот добавлен администратором канала и ему разрешена публикация сообщений.",
            )
        except Exception:
            pass
        # Всё равно продолжаем: иногда Telegram не даёт прочитать статус, но публикация проходит.

    slot = select_catchup_channel_slot(msk_now)
    if slot is None:
        logging.info("Канал ТГ: актуальных пропущенных публикаций нет")
        return

    hour, rubric, cta_key, prompt = slot
    logging.warning(
        f"Канал ТГ: восстанавливаю один актуальный слот "
        f"{hour:02d}:00 — {rubric}"
    )
    await publish_channel_slot(msk_now, hour, rubric, cta_key, prompt)


async def channel_post_loop():
    """Надёжный автопостинг по МСК с постоянным восстановлением пропусков."""
    await asyncio.sleep(10)
    try:
        await channel_startup_check_and_catchup()
    except Exception as e:
        logging.exception(f"Канал ТГ: ошибка стартовой проверки/catch-up: {e}")

    last_recovery_window = ""
    while True:
        try:
            msk_now = datetime.utcnow() + timedelta(hours=3)
            set_app_setting("tg_channel_scheduler_heartbeat", msk_now.isoformat())

            # Плановый запуск в первые 30 минут слота.
            for hour, rubric, cta_key, prompt in all_channel_slots(msk_now):
                if msk_now.hour == hour and msk_now.minute < 30:
                    await publish_channel_slot(msk_now, hour, rubric, cta_key, prompt)
                    await asyncio.sleep(3)

            # Каждые пять минут восстанавливаем один актуальный пропущенный слот.
            recovery_window = f"{msk_now:%Y-%m-%d-%H}-{msk_now.minute // 5}"
            if recovery_window != last_recovery_window:
                last_recovery_window = recovery_window
                slot = select_catchup_channel_slot(msk_now)
                if slot is not None:
                    hour, rubric, cta_key, prompt = slot
                    logging.warning(
                        f"Канал ТГ: автоматическое восстановление {hour:02d}:00 — {rubric}"
                    )
                    await publish_channel_slot(msk_now, hour, rubric, cta_key, prompt)
        except Exception as e:
            logging.exception(f"Канал ТГ: ошибка планировщика: {e}")
            set_app_setting(
                "tg_last_channel_failure",
                f"{datetime.now().isoformat()} | scheduler | {str(e)[:500]}"
            )
        await asyncio.sleep(30)


async def channel_scheduler_supervisor():
    """Перезапускает планировщик, если его задача неожиданно завершилась."""
    while True:
        task = asyncio.create_task(channel_post_loop())
        try:
            await task
        except asyncio.CancelledError:
            task.cancel()
            raise
        except Exception as e:
            logging.exception(f"Канал ТГ: планировщик аварийно завершился: {e}")
            set_app_setting("tg_last_channel_failure", f"{datetime.now().isoformat()} | supervisor | {str(e)[:500]}")
            try:
                await bot.send_message(OWNER_ID, f"⚠️ Планировщик Telegram-канала перезапускается.\n\n{str(e)[:700]}")
            except Exception:
                pass
        await asyncio.sleep(10)


async def channel_watchdog_loop():
    """Проверяет heartbeat и через 10 минут восстанавливает актуальный пропуск."""
    await asyncio.sleep(90)
    while True:
        try:
            msk_now = datetime.utcnow() + timedelta(hours=3)
            heartbeat = get_app_setting("tg_channel_scheduler_heartbeat", "")
            if heartbeat:
                try:
                    age = (msk_now - datetime.fromisoformat(heartbeat)).total_seconds()
                except Exception:
                    age = 0
                if age > 240:
                    alert_key = f"tg_scheduler_stale_{msk_now:%Y%m%d%H}"
                    if not get_app_setting(alert_key, ""):
                        set_app_setting(alert_key, "1")
                        try:
                            await bot.send_message(OWNER_ID, f"⚠️ Нет пульса планировщика Telegram уже {int(age)} секунд. Запущено автоматическое восстановление.")
                        except Exception:
                            pass
            slot = select_catchup_channel_slot(msk_now)
            if slot is not None:
                hour, rubric, cta_key, prompt = slot
                slot_time = msk_now.replace(hour=hour, minute=0, second=0, microsecond=0)
                if (msk_now - slot_time).total_seconds() >= 600:
                    ok = await publish_channel_slot(msk_now, hour, rubric, cta_key, prompt)
                    if not ok:
                        alert_key = f"tg_missed_alert_{msk_now:%Y%m%d}_{hour:02d}_{rubric}"
                        if not get_app_setting(alert_key, ""):
                            set_app_setting(alert_key, "1")
                            try:
                                await bot.send_message(OWNER_ID, f"⚠️ Не удалось выпустить пост Telegram {hour:02d}:00 — {rubric}. Проверьте /channel_status.")
                            except Exception:
                                pass
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logging.exception(f"Канал ТГ: ошибка watchdog: {e}")
        await asyncio.sleep(120)


# ========== НАПОМИНАНИЯ О ДНЕ АНГЕЛА ==========
async def get_prayer_of_day() -> str:
    """Генерирует или возвращает из кеша молитву дня"""
    today = datetime.now().strftime("%Y-%m-%d")
    # Проверяем кеш
    conn = db_connect()
    c = conn.cursor()
    c.execute("SELECT prayer FROM daily_prayer_cache WHERE date=?", (today,))
    row = c.fetchone()
    conn.close()
    if row:
        return row[0]
    # Генерируем новую
    day_str = date_ru("short")
    feast = get_todays_feast()
    context = f"Сегодня фиксированный праздник: {feast}." if feast else ""
    prompt = (
        f"Напиши православную молитву дня. {context} "
        f"Дата: {day_str}. "
        "Молитва должна быть тёплой, душевной, 8-15 строк. "
        "Начни с обращения к Господу или Богородице. Заверши Аминь. "
        "Пиши только по-русски."
    )
    try:
        msg = await claude_messages_create(
            model="claude-sonnet-4-5",
            max_tokens=600,
            system="Ты православный помощник. Пишешь пример личного молитвенного обращения тепло и душевно. Не выдавай его за официальный богослужебный текст.",
            messages=[{"role": "user", "content": prompt}]
        )
        prayer = msg.content[0].text
        # Сохраняем в кеш
        conn2 = db_connect()
        conn2.execute("INSERT OR REPLACE INTO daily_prayer_cache (date, prayer) VALUES (?,?)", (today, prayer))
        conn2.commit()
        conn2.close()
        return prayer
    except Exception as e:
        logging.error(f"Ошибка молитвы дня: {e}")
        return PRAYERS["morning_ru"]["text"]

async def morning_broadcast():
    """Утренняя рассылка всем пользователям у кого включены уведомления"""
    conn = db_connect()
    c = conn.cursor()
    c.execute("SELECT user_id, church_name FROM users WHERE notifications=1")
    users = c.fetchall()
    conn.close()
    prayer = await get_prayer_of_day()
    day_str = date_ru("short")
    feast = get_todays_feast()
    feast_line = ("\U0001f389 " + feast + "\n\n") if feast else ""
    text = (
        "\U0001f305 *\u0414\u043e\u0431\u0440\u043e\u0435 \u0443\u0442\u0440\u043e, " + day_str + "!*\n\n"
        + feast_line
        + "\u2626\ufe0f *\u041c\u043e\u043b\u0438\u0442\u0432\u0430 \u0434\u043d\u044f*\n\n"
        + prayer
        + "\n\n─────────────\n🙏 Все молитвы → @Moya\\_Vera\\_bot"
    )
    sent = 0
    for user_id, name in users:
        try:
            await bot.send_message(user_id, text, parse_mode="Markdown")
            sent += 1
            await asyncio.sleep(0.05)  # чтобы не превысить лимит Telegram
        except Exception:
            pass
    logging.info(f"Утренняя рассылка: отправлено {sent} из {len(users)}")

async def angel_reminder_loop():
    """Напоминает только пользователям, которые явно включили уведомления."""
    await asyncio.sleep(35)
    last_run = ""
    while True:
        now = datetime.utcnow() + timedelta(hours=3)
        run_key = now.strftime("%Y-%m-%d")
        if now.hour == 9 and run_key != last_run:
            last_run = run_key
            conn = db_connect()
            users = conn.execute(
                "SELECT user_id,church_name,angel_day,remind_days FROM users "
                "WHERE notifications=1 AND angel_day<>'' AND angel_day IS NOT NULL"
            ).fetchall()
            conn.close()
            for user_id, name, angel_day, remind_days in users:
                try:
                    day = datetime.strptime(angel_day.split(" ")[0], "%d.%m").replace(year=now.year)
                    diff = (day.date() - now.date()).days
                    if diff < 0:
                        diff = (day.replace(year=now.year + 1).date() - now.date()).days
                    days_before = int(remind_days or 3)
                    if diff == days_before:
                        await bot.send_message(
                            user_id,
                            (
                                f"🕊️ Через {days_before} дн. — возможная дата памяти "
                                "вашего небесного покровителя:\n\n"
                                f"{angel_day}\n\n"
                                "Точное определение дня ангела лучше уточнить у священника."
                            ),
                        )
                    elif diff == 0:
                        await bot.send_message(
                            user_id,
                            (
                                "👼 Сегодня возможная дата памяти вашего небесного покровителя, "
                                f"{name or 'друг'}:\n\n{angel_day}\n\n"
                                "Можно помолиться святому своими словами. "
                                "Точную дату дня ангела лучше уточнить у священника."
                            ),
                        )
                except Exception as e:
                    logging.error(f"Ошибка напоминания {user_id}: {e}")
        await asyncio.sleep(45)



# ========== TELEGRAM: АКТИВАЦИЯ, УДЕРЖАНИЕ И РЕФЕРАЛЫ ==========
def quick_start_keyboard_tg():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🙏 Нужна молитва", callback_data="quick_choice:prayer"), InlineKeyboardButton(text="🕊️ Нужна поддержка", callback_data="quick_choice:support")],
        [InlineKeyboardButton(text="👼 Узнать день ангела", callback_data="quick_choice:saint"), InlineKeyboardButton(text="📿 Подготовиться к исповеди", callback_data="quick_choice:confession")],
        [InlineKeyboardButton(text="📸 Узнать икону", callback_data="quick_choice:icon"), InlineKeyboardButton(text="📖 Читать Евангелие", callback_data="quick_choice:gospel")],
        [InlineKeyboardButton(text="🗺️ Найти храм", callback_data="quick_choice:church"), InlineKeyboardButton(text="☦️ Посмотреть всё", callback_data="main_menu")],
    ])


def quick_target_for_track_tg(track: str) -> str:
    return {
        "prayer": "prayers", "support": "ask_question", "saint": "saints",
        "confession": "sacr_ispoved", "icon": "photo_icon",
        "gospel": "daily_gospel", "church": "find_church",
    }.get(track, "main_menu")


def next_step_for_track_tg(track: str):
    return {
        "prayer": ("⭐ Сохранить молитву", "favorites"),
        "support": ("❓ Задать уточнение", "ask_question"),
        "saint": ("👤 Заполнить профиль", "profile"),
        "confession": ("🗺️ Найти храм", "find_church"),
        "icon": ("👼 Открыть святых", "saints"),
        "gospel": ("📚 Открыть библиотеку", "library"),
        "church": ("📿 Подготовка к исповеди", "sacr_ispoved"),
    }.get(track, ("☦️ Главное меню", "main_menu"))


async def show_quick_start_tg(message: Message, user_id: int):
    track_funnel_event(user_id, "Telegram", "quick_start_opened")
    await message.answer(
        "☦️ Начнём за 60 секунд\n\nЧто вам сейчас нужнее всего? Выберите один вариант — помощник сразу откроет подходящий раздел.",
        reply_markup=quick_start_keyboard_tg(),
    )


async def notify_referrer_tg(referrer_id: int):
    if not referrer_id:
        return
    try:
        await bot.send_message(
            referrer_id,
            "🤝 Ваш близкий начал пользоваться помощником «С верой».\n\n"
            "Спасибо, что делитесь полезным. Для вас открыта молитвенная подборка за близких.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🎁 Открыть подборку", callback_data="ref_reward")]]),
        )
        conn = _funnel_conn()
        conn.execute("UPDATE referrals SET reward_sent=1 WHERE platform='Telegram' AND referrer_id=? AND status='activated'", (int(referrer_id),))
        conn.commit(); conn.close()
    except Exception as e:
        logging.error(f"Referral reward TG error: {e}")


async def maybe_send_activation_prompt_tg(chat_id: int, user_id: int, track: str):
    await asyncio.sleep(1)
    if not should_send_activation_prompt(user_id, "Telegram"): return
    label,target = next_step_for_track_tg(track)
    await bot.send_message(chat_id, "🕊️ Первый полезный шаг сделан.\n\nМожно выбрать одно продолжение. Никакие личные рассылки не включаются без вашего согласия.", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=label,callback_data=target)],
        [InlineKeyboardButton(text="✅ Спокойный путь на 7 дней",callback_data=f"journey_yes:{track}")],
        [InlineKeyboardButton(text="🔔 Короткая молитва утром",callback_data="notifications_yes")],
        [InlineKeyboardButton(text="Не сейчас",callback_data="main_menu")],
    ]))



class SessionTrackingMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        user=getattr(event,"from_user",None)
        if user: touch_user_session(user.id,"Telegram",target=getattr(event,"data","") or "")
        return await handler(event,data)

class FunnelTrackingMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        result = await handler(event, data)
        if isinstance(event, CallbackQuery):
            payload = event.data or ""
            if payload in FUNNEL_USEFUL_CALLBACKS:
                track = FUNNEL_TRACK_BY_TARGET.get(payload, "support")
                referrer = mark_useful_action(event.from_user.id, "Telegram", payload)
                if referrer:
                    asyncio.create_task(notify_referrer_tg(referrer))
                asyncio.create_task(maybe_send_activation_prompt_tg(event.message.chat.id, event.from_user.id, track))
        return result


dp.message.outer_middleware(SessionTrackingMiddleware())
dp.callback_query.outer_middleware(SessionTrackingMiddleware())
dp.callback_query.outer_middleware(FunnelTrackingMiddleware())


@dp.callback_query(F.data == "quick_start")
async def cb_quick_start_tg(callback: CallbackQuery):
    await show_quick_start_tg(callback.message, callback.from_user.id)
    await callback.answer()


@dp.callback_query(F.data.startswith("quick_choice:"))
async def cb_quick_choice_tg(callback: CallbackQuery):
    track = callback.data.split(":", 1)[1]
    target = quick_target_for_track_tg(track)
    track_funnel_event(callback.from_user.id, "Telegram", "quick_start_choice", target=track)
    await send_deep_link_destination(callback.message, target)
    referrer = mark_useful_action(callback.from_user.id, "Telegram", f"quick_{track}")
    if referrer: asyncio.create_task(notify_referrer_tg(referrer))
    asyncio.create_task(maybe_send_activation_prompt_tg(callback.message.chat.id, callback.from_user.id, track))
    await callback.answer()


@dp.callback_query(F.data == "notifications_yes")
async def cb_notifications_yes_tg(callback: CallbackQuery):
    conn = db_connect(); conn.execute("UPDATE users SET notifications=1 WHERE user_id=?", (callback.from_user.id,)); conn.commit(); conn.close()
    set_funnel_flag(callback.from_user.id, "Telegram", "notifications_enabled", 1)
    track_funnel_event(callback.from_user.id, "Telegram", "notifications_opt_in")
    await callback.message.answer("🔔 Утренняя молитва включена. Отключить её можно в профиле.", reply_markup=main_menu())
    await callback.answer()

@dp.callback_query(F.data.startswith("journey_yes:"))
async def cb_journey_yes_tg(callback: CallbackQuery):
    track = callback.data.split(":", 1)[1]
    start_nurture_journey(callback.from_user.id, "Telegram", track)
    await callback.message.answer(
        "✅ 7-дневное знакомство включено.\n\nПервое короткое сообщение придёт завтра. Отключить серию можно в любой момент.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔕 Отключить серию", callback_data="journey_stop")], [InlineKeyboardButton(text="🏠 Меню", callback_data="main_menu")]]),
    )
    await callback.answer()


@dp.callback_query(F.data == "journey_stop")
async def cb_journey_stop_tg(callback: CallbackQuery):
    stop_nurture_journey(callback.from_user.id, "Telegram")
    await callback.message.answer("🔕 7-дневная серия отключена.", reply_markup=main_menu())
    await callback.answer()


@dp.callback_query(F.data == "invite_friend")
async def cb_invite_friend_tg(callback: CallbackQuery):
    link = f"{BOT_URL}?start=ref_{callback.from_user.id}"
    track_funnel_event(callback.from_user.id, "Telegram", "referral_link_opened")
    await callback.message.answer(
        "🤝 Пригласить близкого\n\n"
        "Отправьте эту персональную ссылку человеку, которому могут пригодиться молитвы, календарь или спокойная подготовка к Таинствам.\n\n"
        f"{link}\n\nКогда близкий получит первый полезный результат, вам откроется молитвенная подборка за родных.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="☦️ Открыть ссылку", url=link)],
            [InlineKeyboardButton(text="🎁 Моя подборка", callback_data="ref_reward")],
        ]),
    )
    await callback.answer()


@dp.callback_query(F.data == "ref_reward")
async def cb_ref_reward_tg(callback: CallbackQuery):
    if has_referral_reward(callback.from_user.id, "Telegram"):
        await callback.message.answer(referral_reward_text(), reply_markup=main_menu())
    else:
        await callback.message.answer(
            "🎁 Подборка откроется, когда приглашённый вами человек получит первый полезный результат в помощнике.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🤝 Получить ссылку", callback_data="invite_friend")]]),
        )
    await callback.answer()


@dp.callback_query(F.data == "interactive_menu")
async def cb_interactive_menu_tg(callback: CallbackQuery):
    await callback.message.answer(
        "💬 Что разобрать в следующей публикации?\n\nВыберите тему — голос будет учтён в аналитике канала.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🙏 Как начать молиться", callback_data="interactive_vote:prayer")],
            [InlineKeyboardButton(text="📿 Первая исповедь", callback_data="interactive_vote:confession")],
            [InlineKeyboardButton(text="👼 День ангела", callback_data="interactive_vote:saint")],
            [InlineKeyboardButton(text="🕊️ Тревога и уныние", callback_data="interactive_vote:support")],
        ]),
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("interactive_vote:"))
async def cb_interactive_vote_tg(callback: CallbackQuery):
    topic = callback.data.split(":", 1)[1]
    record_topic_vote(callback.from_user.id, "Telegram", topic)
    await callback.message.answer("✅ Спасибо! Ваш выбор учтён.", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="☦️ Начать за 60 секунд", callback_data="quick_start")]]))
    await callback.answer()


@dp.callback_query(F.data.startswith("review_consent:"))
async def cb_review_consent_tg(callback: CallbackQuery):
    value = 1 if callback.data.endswith(":yes") else 0
    conn = _funnel_conn()
    conn.execute(
        "UPDATE user_reviews SET publish_consent=? WHERE user_id=? AND id=(SELECT MAX(id) FROM user_reviews WHERE user_id=?)",
        (value, callback.from_user.id, callback.from_user.id),
    )
    conn.commit()
    if value:
        row = conn.execute("SELECT MAX(id) FROM user_reviews WHERE user_id=?", (callback.from_user.id,)).fetchone()
        review_id = int(row[0]) if row and row[0] else 0
        if review_id:
            await bot.send_message(OWNER_ID, f"📢 Пользователь разрешил анонимную публикацию отзыва #{review_id}.", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ Одобрить для канала", callback_data=f"owner_review_public:{review_id}")]]))
    conn.close()
    await callback.message.answer("Спасибо. Отзыв будет использован только анонимно." if value else "Понял. Отзыв останется только внутри команды проекта.", reply_markup=main_menu())
    await callback.answer()


@dp.callback_query(F.data.startswith("owner_review_public:"))
async def cb_owner_review_public_tg(callback: CallbackQuery):
    if callback.from_user.id != OWNER_ID:
        await callback.answer("Недоступно", show_alert=True)
        return
    review_id = int(callback.data.split(":", 1)[1])
    conn = _funnel_conn()
    row = conn.execute("SELECT publish_consent FROM user_reviews WHERE id=?", (review_id,)).fetchone()
    if row and int(row[0] or 0) == 1:
        conn.execute("UPDATE user_reviews SET public_approved=1 WHERE id=?", (review_id,))
        conn.commit(); conn.close()
        await callback.message.answer(f"✅ Отзыв #{review_id} одобрен для анонимной публикации.")
    else:
        conn.close()
        await callback.message.answer("⚠️ Пользователь ещё не дал согласие на публикацию.")
    await callback.answer()


@dp.message(Command("funnel_report"))
async def cmd_funnel_report_tg(message: Message):
    if message.from_user.id != OWNER_ID:
        return
    await message.answer(funnel_report_text("Telegram", 7))



async def weekly_funnel_report_loop_tg():
    await asyncio.sleep(90)
    while True:
        try:
            now = datetime.utcnow() + timedelta(hours=3)
            if now.weekday() == 0 and now.hour >= 10 and weekly_report_due("Telegram"):
                await bot.send_message(OWNER_ID, funnel_report_text("Telegram", 7))
                mark_weekly_report_sent("Telegram")
        except Exception as e:
            logging.error(f"Weekly funnel report TG error: {e}")
        await asyncio.sleep(1800)


async def nurture_loop_tg():
    await asyncio.sleep(45)
    while True:
        for user_id, track, day_index in due_nurture_rows("Telegram"):
            try:
                series = NURTURE_MESSAGES.get(track, NURTURE_MESSAGES["support"])
                idx = min(int(day_index), len(series) - 1)
                text, target = series[idx]
                await bot.send_message(
                    int(user_id), text,
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="Открыть", callback_data=target)],
                        [InlineKeyboardButton(text="🔕 Отключить серию", callback_data="journey_stop")],
                    ]),
                )
                track_funnel_event(user_id, "Telegram", "nurture_message_sent", target=track, value=str(NURTURE_DAY_OFFSETS[idx]))
                advance_nurture(user_id, "Telegram", idx)
                await asyncio.sleep(0.08)
            except Exception as e:
                logging.error(f"Nurture TG send error {user_id}: {e}")
        await asyncio.sleep(600)

# ========== ХЭНДЛЕРЫ ==========

async def send_deep_link_destination(message: Message, target: str):
    """Сразу открывает обещанную функцию, а не промежуточную рекламу."""
    user_id = message.from_user.id
    if target == "prayers":
        await message.answer("🙏 Молитвы\n\nВыберите нужную молитву:", reply_markup=prayers_menu())
    elif target == "saints":
        await message.answer(
            "👼 Святые и небесный покровитель\n\nНайдите святого по имени или посмотрите именинников дня.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔍 Найти святого по имени", callback_data="saint_search")],
                [InlineKeyboardButton(text="👼 Именинники сегодня", callback_data="cal_namedays")],
                [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")],
            ]),
        )
    elif target == "daily_gospel":
        await message.answer(await get_daily_gospel(), parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📅 Календарь", callback_data="calendar")],
            [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")],
        ]))
    elif target == "ask_question":
        set_step(user_id, "ask_depth")
        await message.answer("❓ Задайте свой вопрос о вере\n\nВыберите глубину ответа:", reply_markup=question_depth_menu())
    elif target == "prayer_evening_ru":
        prayer = PRAYERS["evening_ru"]
        await message.answer(
            f"{prayer['title']}\n\n{prayer['text']}",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⭐ Сохранить в избранное", callback_data="save_prayer_evening_ru")],
                [InlineKeyboardButton(text="🙏 Все молитвы", callback_data="prayers")],
                [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")],
            ]),
        )
    elif target == "library":
        await message.answer("📚 Православная библиотека\n\nВыберите раздел:", reply_markup=library_menu())
    elif target == "photo_icon":
        set_step(user_id, "photo_icon")
        await message.answer("🖼️ Отправьте фотографию иконы — я постараюсь определить образ и объяснить символы.", reply_markup=back_menu())
    elif target == "find_church":
        set_step(user_id, "find_church")
        await message.answer("🗺️ Найти храм рядом\n\nОтправьте геолокацию или введите город:", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📍 Отправить геолокацию", callback_data="send_location")],
            [InlineKeyboardButton(text="✏️ Ввести город", callback_data="city_text")],
            [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")],
        ]))
    elif target == "sacraments":
        await message.answer("⛪ Таинства и обряды\n\nВыберите раздел:", reply_markup=sacraments_menu())
    elif target == "calendar":
        await message.answer("📅 Православный календарь\n\nВыберите раздел:", reply_markup=calendar_menu())
    elif target == "profile":
        await message.answer("👤 Мой профиль", reply_markup=profile_menu(get_user(user_id)))
    elif target == "interactive_menu":
        await message.answer(
            "💬 Что разобрать в следующей публикации?",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🙏 Как начать молиться", callback_data="interactive_vote:prayer")],
                [InlineKeyboardButton(text="📿 Первая исповедь", callback_data="interactive_vote:confession")],
                [InlineKeyboardButton(text="👼 День ангела", callback_data="interactive_vote:saint")],
                [InlineKeyboardButton(text="🕊️ Тревога и уныние", callback_data="interactive_vote:support")],
            ]),
        )
    elif target == "sacr_ispoved":
        sacr = SACRAMENTS["ispoved"]
        await message.answer(f"{sacr['title']}\n\n{sacr['text']}", reply_markup=back_section("sacraments"))
    else:
        await message.answer("☦️ Главное меню:", reply_markup=main_menu())


@dp.message(Command("start"))
async def cmd_start(message: Message):
    user_id = message.from_user.id
    username = message.from_user.username or ""
    first_name = message.from_user.first_name or ""
    user = get_user(user_id, username, first_name)
    asyncio.create_task(asyncio.to_thread(sheets_add_user, user_id, username, first_name))

    parts = (message.text or "").split(maxsplit=1)
    raw_start_param = parts[1].strip().lower() if len(parts) > 1 else ""
    start_param = raw_start_param
    if start_param.startswith("ref_"):
        try:
            register_referral("Telegram", int(start_param.split("_", 1)[1]), user_id)
        except Exception:
            pass
        start_param = "ch_start60"
    base_start_param = base_channel_source(start_param)
    touch_funnel_user(user_id, "Telegram", raw_start_param, base_start_param)
    if base_start_param in {"ch_start60", "start60"}:
        track_funnel_event(user_id, "Telegram", "channel_click", source=raw_start_param or base_start_param, target="quick_start")
        await show_quick_start_tg(message, user_id)
        return
    target = CHANNEL_ROUTES.get(base_start_param)
    if target:
        record_channel_click(user_id, raw_start_param or base_start_param, target)
        track_funnel_event(user_id, "Telegram", "channel_click", source=raw_start_param or base_start_param, target=target)
        await send_deep_link_destination(message, target)
        track = FUNNEL_TRACK_BY_TARGET.get(target, "support")
        referrer = mark_useful_action(user_id, "Telegram", target, raw_start_param or base_start_param)
        if referrer:
            asyncio.create_task(notify_referrer_tg(referrer))
        asyncio.create_task(maybe_send_activation_prompt_tg(message.chat.id, user_id, track))
        return

    legacy_routes = {
        "prayers": "prayers", "saints": "saints", "gospel": "daily_gospel",
        "daily_gospel": "daily_gospel", "question": "ask_question", "ask_question": "ask_question",
        "evening": "prayer_evening_ru", "prayer_evening_ru": "prayer_evening_ru",
        "library": "library", "photo_icon": "photo_icon", "find_church": "find_church",
        "sacraments": "sacraments", "calendar": "calendar", "profile": "profile",
        "sacr_ispoved": "sacr_ispoved", "menu": "main_menu", "main_menu": "main_menu",
    }
    if start_param in legacy_routes:
        await send_deep_link_destination(message, legacy_routes[start_param])
        return

    if not user.get("onboarded"):
        await message.answer(
            "☦️ Добро пожаловать в «С верой»!\n\n"
            "Я ваш православный помощник — здесь всё, что нужно для духовной жизни:\n\n"
            "🙏 Молитвы на все случаи жизни\n"
            "📅 Православный календарь и посты\n"
            "⛪ Таинства — как подготовиться\n"
            "👼 Жития святых и дни памяти\n"
            "🏛️ Святые места России и мира\n"
            "📸 Узнать храм или икону по фото\n"
            "❓ Задать вопрос о вере\n\n"
            "Чтобы напоминать о дне ангела, укажите имя при крещении и дату рождения.",
            reply_markup=onboarding_menu(),
        )
    else:
        name = user.get("church_name") or first_name
        await message.answer(f"☦️ С возвращением, {name}!\n\nЧем могу помочь?", reply_markup=main_menu())

@dp.message(Command("privacy"))
async def cmd_privacy(message: Message):
    await message.answer("🔐 Конфиденциальность\n\nБот хранит только данные, необходимые для функций: ID платформы, имя профиля, выбранные настройки, отзывы и историю платежных статусов. Фото и голосовые используются для обработки запроса и временные файлы удаляются. Рекламодателям данные не передаются. Для удаления данных используйте /delete_my_data.")

@dp.message(Command("delete_my_data"))
async def cmd_delete_my_data(message: Message):
    await message.answer("Удалить профиль, настройки, избранное и историю воронки? Платёжные записи, которые требуется хранить для учёта, будут обезличены.", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🗑️ Да, удалить",callback_data="confirm_delete_my_data")],[InlineKeyboardButton(text="Отмена",callback_data="main_menu")]]))

def delete_user_data_tg(user_id: int):
    uid = int(user_id)
    conn = db_connect()
    for table in (
        "favorites", "limits", "subscriptions", "pending_payments", "nurture_journeys",
        "funnel_events", "user_sessions", "channel_clicks", "topic_votes",
    ):
        try:
            conn.execute(f"DELETE FROM {table} WHERE user_id=?", (uid,))
        except Exception:
            pass
    conn.execute("DELETE FROM user_funnel_state WHERE user_id=? AND platform='Telegram'", (uid,))
    conn.execute("DELETE FROM referrals WHERE platform='Telegram' AND (referrer_id=? OR referred_user_id=?)", (uid, uid))
    conn.execute("DELETE FROM user_reviews WHERE user_id=?", (uid,))
    conn.execute(
        "UPDATE donation_payments SET user_id=0,chat_id=0,username='',first_name='' WHERE user_id=? AND platform='Telegram'",
        (uid,),
    )
    conn.execute("DELETE FROM users WHERE user_id=?", (uid,))
    conn.commit()
    conn.close()


@dp.callback_query(F.data == "confirm_delete_my_data")
async def cb_delete_my_data(callback: CallbackQuery):
    await asyncio.to_thread(delete_user_data_tg, callback.from_user.id)
    await callback.message.answer("✅ Данные профиля удалены. Для нового начала отправьте /start.")
    await callback.answer()

@dp.message(Command("health_full"))
async def cmd_health_full_tg(message: Message):
    if message.from_user.id != OWNER_ID:
        return
    conn = db_connect()
    users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    pending = conn.execute(
        "SELECT COUNT(*) FROM donation_payments WHERE status IN ('pending','waiting_for_capture')"
    ).fetchone()[0]
    errors = conn.execute(
        "SELECT COUNT(*) FROM critical_errors WHERE created_at>=?",
        ((datetime.now() - timedelta(days=1)).isoformat(),),
    ).fetchone()[0]
    conn.close()
    access_ok, detail = await verify_channel_access()
    await message.answer(
        (
            "🩺 Полная диагностика Telegram\n\n"
            f"Пользователей: {users}\n"
            f"Ожидают оплаты: {pending}\n"
            f"Критических ошибок за 24 часа: {errors}\n"
            f"Канал: {'доступ есть' if access_ok else 'ошибка'}\n"
            f"{detail}\n"
            "База: WAL включён"
        )
    )

@dp.message(Command("backup_status"))
async def cmd_backup_status_tg(message: Message):
    if message.from_user.id == OWNER_ID:
        await message.answer("💾 Резервные копии Telegram\n\n" + backup_status_text("vera_tg"))


@dp.message(Command("backup_now"))
async def cmd_backup_now_tg(message: Message):
    if message.from_user.id != OWNER_ID:
        return
    try:
        path = await asyncio.to_thread(create_database_backup, "vera_tg")
        await message.answer(f"✅ Резервная копия создана:\n{Path(path).name}")
    except Exception as e:
        record_critical_error("backup_now_tg", e)
        await message.answer(f"⚠️ Не удалось создать резервную копию: {str(e)[:500]}")


@dp.message(Command("menu"))
async def cmd_menu(message: Message):
    await message.answer("☦️ Главное меню:", reply_markup=main_menu())


TELEGRAM_CHANNEL_INTRO = (
    "☦️ С ВЕРОЙ — ПРАВОСЛАВНЫЙ ПОМОЩНИК РЯДОМ КАЖДЫЙ ДЕНЬ\n\n"
    "Этот канал создан для спокойной и понятной духовной жизни без информационного шума.\n\n"
    "Здесь ежедневно выходят:\n"
    "🙏 утренние и вечерние молитвенные публикации\n"
    "👼 святые, праздники и дни памяти\n"
    "📖 Евангелие и простые объяснения веры\n"
    "⛪ практические памятки о храме и Таинствах\n"
    "📚 проверенные книги и фильмы\n\n"
    "А в православном помощнике можно подобрать молитву, узнать день ангела, подготовиться к исповеди, определить икону по фото и задать личный вопрос о вере.\n\n"
    "Помощник не заменяет священника. В вопросах Таинств и личного духовного руководства обращайтесь к священнику своего прихода.\n\n"
    "Подпишитесь на канал и включите уведомления, чтобы не пропускать утренние и вечерние публикации.\n\n"
    "Не знаете, с чего начать? Нажмите «Начать за 60 секунд» — помощник задаст один вопрос и сразу откроет нужный раздел.\n\n"
    "Выберите первый шаг 👇"
)


def telegram_intro_keyboard():
    def dl(source):
        return f"{BOT_URL}?start={source}"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="☦️ Начать за 60 секунд", url=dl("ch_start60"))],
        [InlineKeyboardButton(text="🙏 Молитвы", url=dl("ch_morning")), InlineKeyboardButton(text="👼 День ангела", url=dl("ch_saint"))],
        [InlineKeyboardButton(text="📿 Подготовка к исповеди", url=dl("ch_showcase_confession"))],
        [InlineKeyboardButton(text="📸 Узнать икону", url=dl("ch_photo")), InlineKeyboardButton(text="❓ Задать вопрос", url=dl("ch_guidance"))],
        [InlineKeyboardButton(text="☦️ Открыть помощника", url=BOT_URL)],
    ])


def save_donation_payment(payment_id, user_id, chat_id, username, first_name, amount, platform):
    now=datetime.now(); expires=now+timedelta(hours=72); conn=db_connect()
    conn.execute("""INSERT OR REPLACE INTO donation_payments(payment_id,user_id,chat_id,username,first_name,amount,platform,status,created_at,expires_at,checked_at,last_error) VALUES (?,?,?,?,?,?,?,'pending',?,?,?,'')""", (str(payment_id),int(user_id),int(chat_id),username or "",first_name or "",int(amount),platform,now.isoformat(),expires.isoformat(),now.isoformat()))
    conn.commit(); conn.close()



def _mark_donation_field(payment_id, field, value=1):
    allowed={"status","user_notified","owner_notified","sheet_recorded","paid_at","checked_at","expires_at","last_error"}
    if field not in allowed: return
    conn=db_connect(); conn.execute(f"UPDATE donation_payments SET {field}=? WHERE payment_id=?", (value,str(payment_id))); conn.commit(); conn.close()



async def check_donation_payments_loop_tg():
    await asyncio.sleep(20)
    while True:
        try:
            now=datetime.now(); conn=db_connect(); rows=conn.execute("""SELECT payment_id,user_id,chat_id,username,first_name,amount,status,user_notified,owner_notified,sheet_recorded,created_at,expires_at FROM donation_payments WHERE status IN ('pending','waiting_for_capture') OR (status='succeeded' AND (user_notified=0 OR owner_notified=0 OR sheet_recorded=0))""").fetchall(); conn.close()
            for payment_id,user_id,chat_id,username,first_name,amount,status,user_n,owner_n,sheet_n,created_at,expires_at in rows:
                try:
                    if status!="succeeded":
                        expiry=datetime.fromisoformat(expires_at) if expires_at else datetime.fromisoformat(created_at)+timedelta(hours=72)
                        if now>=expiry: _mark_donation_field(payment_id,"status","expired"); continue
                        payment=await asyncio.to_thread(Payment.find_one,payment_id); remote=str(getattr(payment,"status","pending")); _mark_donation_field(payment_id,"checked_at",now.isoformat())
                        if remote in {"canceled","cancelled"}: _mark_donation_field(payment_id,"status","canceled"); continue
                        if remote!="succeeded": _mark_donation_field(payment_id,"status",remote if remote in {"pending","waiting_for_capture"} else "pending"); continue
                        _mark_donation_field(payment_id,"status","succeeded"); _mark_donation_field(payment_id,"paid_at",now.isoformat()); set_funnel_flag(user_id,"Telegram","donation_made",1); track_funnel_event(user_id,"Telegram","donation_succeeded",value=str(amount))
                    if not user_n:
                        await bot.send_message(chat_id,f"🕯️ Пожертвование {amount} рублей прошло успешно.\n\nБлагодарим за поддержку проекта «С верой». Да хранит вас Господь!",reply_markup=main_menu()); _mark_donation_field(payment_id,"user_notified",1)
                    if not owner_n:
                        await bot.send_message(OWNER_ID,f"💰 Новое пожертвование в «С верой» Telegram\n\nСумма: {amount} ₽\nПользователь: {first_name or '—'}\nUsername: @{username if username else '—'}\nID: {user_id}\nPayment ID: {payment_id}"); _mark_donation_field(payment_id,"owner_notified",1)
                    if not sheet_n:
                        ok=await asyncio.to_thread(add_donation_to_sheet,user_id,username,first_name,amount)
                        if ok: _mark_donation_field(payment_id,"sheet_recorded",1)
                except Exception as e:
                    _mark_donation_field(payment_id,"last_error",str(e)[:1000]); record_critical_error("donation_tg",e)
        except Exception as e:
            logging.error(f"ТГ: ошибка цикла пожертвований: {e}"); record_critical_error("donation_loop_tg",e)
        await asyncio.sleep(60)



def payments_report_text(platform: str) -> str:
    conn=db_connect(); rows=conn.execute("SELECT status,COUNT(*),COALESCE(SUM(amount),0) FROM donation_payments WHERE platform=? GROUP BY status ORDER BY status",(platform,)).fetchall(); pending=conn.execute("SELECT payment_id,amount,created_at,last_error FROM donation_payments WHERE platform=? AND status IN ('pending','waiting_for_capture') ORDER BY created_at LIMIT 10",(platform,)).fetchall(); conn.close()
    lines=["💳 Платежи и пожертвования"]+[f"• {s}: {c} платежей, {total} ₽" for s,c,total in rows]
    if pending: lines += ["\nОжидают проверки:"]+[f"• {pid}: {amount} ₽, {created[:16]}{(' — '+err[:80]) if err else ''}" for pid,amount,created,err in pending]
    return "\n".join(lines)

@dp.message(Command("payments_report"))
async def cmd_payments_report_tg(message: Message):
    if message.from_user.id == OWNER_ID: await message.answer(payments_report_text("Telegram"))

@dp.message(Command("publish_channel_intro"))
async def cmd_publish_channel_intro(message: Message):
    if message.from_user.id != OWNER_ID: return
    existing = get_app_setting("tg_intro_message_id")
    if existing:
        try:
            await bot.pin_chat_message(CHANNEL_ID, int(existing), disable_notification=True)
            set_app_setting("tg_intro_pinned", "1")
            await message.answer("✅ Существующий приветственный пост закреплён повторно — дубль не создан.")
            return
        except Exception as e:
            logging.warning(f"Не удалось закрепить существующий intro: {e}")
    try:
        posted = await bot.send_message(CHANNEL_ID, TELEGRAM_CHANNEL_INTRO, reply_markup=telegram_intro_keyboard())
        set_app_setting("tg_intro_message_id", posted.message_id)
        try:
            await bot.pin_chat_message(CHANNEL_ID, posted.message_id, disable_notification=True)
            set_app_setting("tg_intro_pinned", "1")
            await message.answer("✅ Приветственный пост опубликован и закреплён в Telegram-канале.")
        except Exception as pin_error:
            set_app_setting("tg_intro_pinned", "0")
            await message.answer(f"✅ Пост опубликован. Закрепите его вручную или выдайте боту право управления сообщениями.\n\nОшибка закрепления: {str(pin_error)[:300]}")
    except Exception as e:
        logging.exception(f"Не удалось опубликовать приветственный пост: {e}")
        await message.answer(f"⚠️ Не удалось опубликовать пост: {str(e)[:500]}")



@dp.message(Command("channel_status"))
async def cmd_channel_status(message: Message):
    """Диагностика канала только для владельца."""
    if message.from_user.id != OWNER_ID:
        return
    msk_now = datetime.utcnow() + timedelta(hours=3)
    access_ok, detail = await verify_channel_access()
    rows = channel_posts_today(msk_now)
    slots = all_channel_slots(msk_now)
    upcoming = [f"{h:02d}:00 — {rubric}" for h, rubric, _cta, _prompt in slots if h > msk_now.hour]
    posted = "\n".join(f"✅ {slot} — {rubric}" for slot, rubric, _topic, _created in rows) or "Публикаций сегодня пока нет"
    next_text = "\n".join(upcoming[:4]) or "Плановых слотов сегодня больше нет"
    await message.answer(
        "📊 Статус Telegram-канала\n\n"
        f"Время МСК: {msk_now:%d.%m.%Y %H:%M}\n"
        f"Доступ: {'✅' if access_ok else '❌'} {detail}\n\n"
        f"Сегодня:\n{posted}\n\n"
        f"Следующие слоты:\n{next_text}\n\n"
        f"Последняя ошибка: {get_app_setting('tg_last_channel_failure', 'нет')}\n"
        f"Пульс планировщика: {get_app_setting('tg_channel_scheduler_heartbeat', 'ещё не записан')}\n"
        "Ручное восстановление: /channel_recover"
    )


@dp.message(Command("channel_recover"))
async def cmd_channel_recover(message: Message):
    """Публикует один актуальный пропущенный слот по команде владельца."""
    if message.from_user.id != OWNER_ID:
        return
    msk_now = datetime.utcnow() + timedelta(hours=3)
    slot = select_catchup_channel_slot(msk_now)
    if slot is None:
        await message.answer("✅ Актуальных пропущенных публикаций нет.")
        return
    hour, rubric, cta_key, prompt = slot
    ok = await publish_channel_slot(msk_now, hour, rubric, cta_key, prompt)
    if ok:
        await message.answer(f"✅ Восстановлена публикация {hour:02d}:00 — {rubric}.")
    else:
        await message.answer(
            "⚠️ Публикацию восстановить не удалось. Проверьте /channel_status и журнал службы."
        )


@dp.message(Command("channel_test"))
async def cmd_channel_test(message: Message):
    """Немедленная тестовая публикация в канал только по команде владельца."""
    if message.from_user.id != OWNER_ID:
        return
    msk_now = datetime.utcnow() + timedelta(hours=3)
    ok = await send_channel_post(
        "☦️ Проверка связи канала «С верой». Если вы видите эту публикацию, автопостинг и кнопка перехода работают.",
        "guidance",
        with_photo=False,
        msk_now=msk_now,
    )
    await message.answer("✅ Тестовый пост отправлен в канал" if ok else "❌ Тестовый пост не отправлен. Смотрите журнал службы.")


@dp.message(Command("channel_image_test"))
async def cmd_channel_image_test(message: Message):
    if message.from_user.id == OWNER_ID:
        await message.answer("ℹ️ Изображения канала временно полностью отключены. Текстовые CTA-посты работают.")

@dp.callback_query(F.data == "main_menu")
async def cb_main_menu(callback: CallbackQuery):
    await callback.message.answer("☦️ Главное меню:", reply_markup=main_menu())
    await callback.answer()

# ========== ОНБОРДИНГ ==========
@dp.callback_query(F.data == "onboard_start")
async def cb_onboard_start(callback: CallbackQuery):
    set_step(callback.from_user.id, "onboard_name")
    await callback.message.answer(
        "✏️ Введите ваше имя при крещении.\n\n"
        "Если не знаете церковного имени —\n"
        "введите своё обычное имя:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⏭️ Пропустить", callback_data="onboard_skip")]
        ])
    )
    await callback.answer()

@dp.callback_query(F.data == "onboard_skip")
async def cb_onboard_skip(callback: CallbackQuery):
    set_onboarded(callback.from_user.id)
    await callback.message.answer(
        "☦️ Хорошо! Вы всегда можете заполнить профиль позже\n"
        "в разделе «👤 Мой профиль».\n\n"
        "Чем могу помочь?",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="☦️ Открыть меню", callback_data="main_menu")],
        ])
    )
    await callback.answer()

# ========== МОЛИТВЫ ==========
@dp.callback_query(F.data == "prayers")
async def cb_prayers(callback: CallbackQuery):
    await callback.message.answer(
        "🙏 *Молитвы*\n\nВыберите молитву:",
        parse_mode="Markdown",
        reply_markup=prayers_menu()
    )
    await callback.answer()

@dp.callback_query(F.data == "prayer_of_day")
async def cb_prayer_of_day(callback: CallbackQuery):
    await callback.answer()
    await callback.message.answer("✨ Нахожу молитву дня...")
    prayer = await get_prayer_of_day()
    day_str = date_ru("short")
    feast = get_todays_feast()
    feast_line = ("🎉 *" + feast + "*\n\n") if feast else ""
    await callback.message.answer(
        "✨ *Молитва дня — " + day_str + "*\n\n" + feast_line + prayer,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🙏 Все молитвы", callback_data="prayers")],
            [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")],
        ])
    )

@dp.callback_query(F.data.startswith("prayer_"))
async def cb_prayer(callback: CallbackQuery):
    key = callback.data.replace("prayer_", "")
    prayer = PRAYERS.get(key)
    if not prayer:
        await callback.answer("Молитва не найдена")
        return
    save_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⭐ Сохранить в избранное", callback_data=f"save_prayer_{key}")],
        [InlineKeyboardButton(text="◀️ К молитвам",           callback_data="prayers")],
        [InlineKeyboardButton(text="🏠 Главное меню",         callback_data="main_menu")],
    ])
    await callback.message.answer(
        f"*{prayer['title']}*\n\n{prayer['text']}",
        parse_mode="Markdown",
        reply_markup=save_kb
    )
    await callback.answer()

@dp.callback_query(F.data.startswith("save_prayer_"))
async def cb_save_prayer(callback: CallbackQuery):
    key = callback.data.replace("save_prayer_", "")
    prayer = PRAYERS.get(key)
    if prayer:
        save_favorite(callback.from_user.id, prayer["title"], prayer["text"])
        await callback.answer("⭐ Сохранено в избранное!", show_alert=False)
    else:
        await callback.answer("Ошибка сохранения")

# ========== КАЛЕНДАРЬ ==========
@dp.callback_query(F.data == "calendar")
async def cb_calendar(callback: CallbackQuery):
    await callback.message.answer(
        "📅 *Православный календарь*\n\nВыберите раздел:",
        parse_mode="Markdown",
        reply_markup=calendar_menu()
    )
    await callback.answer()

@dp.callback_query(F.data == "cal_today")
async def cb_cal_today(callback: CallbackQuery):
    today    = datetime.now()
    day_str  = today.strftime("%d.%m")
    feast    = FIXED_FEASTS.get(day_str, "")
    saints   = get_todays_saints()
    weekday  = ["Понедельник","Вторник","Среда","Четверг","Пятница","Суббота","Воскресенье"][today.weekday()]

    text = "📅 *" + date_ru('full') + "*, " + weekday + "\n\n"

    if feast:
        text += f"🎉 *Праздник:* {feast}\n\n"

    if today.weekday() == 2:
        text += "🥗 *Среда* — постный день\n\n"
    elif today.weekday() == 4:
        text += "🥗 *Пятница* — постный день\n\n"

    if saints:
        text += "👼 *Именинники:*\n"
        for name, desc in saints[:5]:
            text += f"— {name} ({desc})\n"
    else:
        text += "👼 Именинников сегодня нет в нашей базе\n"

    await callback.message.answer(
        text,
        parse_mode="Markdown",
        reply_markup=back_section("calendar")
    )
    await callback.answer()

@dp.callback_query(F.data == "cal_pasxa")
async def cb_pasxa(callback: CallbackQuery):
    await callback.message.answer(
        PASCHA_GUIDE_TEXT,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🙏 Молитвы", callback_data="prayers")],
            [InlineKeyboardButton(text="📅 Календарь", callback_data="calendar")],
            [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")],
        ]),
    )
    await callback.answer()


@dp.callback_query(F.data == "cal_kreschenije")
async def cb_kreschenije(callback: CallbackQuery):
    await callback.message.answer(THEOPHANY_GUIDE_TEXT, reply_markup=back_section("calendar"))
    await callback.answer()

@dp.callback_query(F.data == "cal_namedays")
async def cb_namedays(callback: CallbackQuery):
    saints = get_todays_saints()
    today  = date_ru("short")
    if saints:
        text = f"👼 *Именинники {today}:*\n\n"
        for name, desc in saints:
            text += f"✨ *{name}* — {desc}\n"
        text += "\n🙏 Поздравьте своих близких!"
    else:
        text = f"👼 В нашей справочной базе нет записей об именинах на {today}.\n\nДля точного календаря проверьте календарь своего прихода."
    await callback.message.answer(
        text,
        parse_mode="Markdown",
        reply_markup=back_section("calendar")
    )
    await callback.answer()

@dp.callback_query(F.data == "cal_feasts")
async def cb_feasts(callback: CallbackQuery):
    text = "🎉 *Великие православные праздники:*\n\n"
    for date_str, feast in list(FIXED_FEASTS.items())[:15]:
        text += f"📅 {date_str} — {feast}\n"
    await callback.message.answer(
        text,
        parse_mode="Markdown",
        reply_markup=back_section("calendar")
    )
    await callback.answer()

@dp.callback_query(F.data == "cal_fasts")
async def cb_fasts(callback: CallbackQuery):
    text = "🥗 *Православные посты:*\n\n"
    for fast_name, fast_desc in FASTS.items():
        text += f"*{fast_name}*\n{fast_desc}\n\n"
    await callback.message.answer(
        text,
        parse_mode="Markdown",
        reply_markup=back_section("calendar")
    )
    await callback.answer()

@dp.callback_query(F.data == "cal_find_angel")
async def cb_find_angel(callback: CallbackQuery):
    set_step(callback.from_user.id, "find_angel")
    await callback.message.answer(
        "👼 Введите имя — я найду день ангела:\n\n"
        "Например: *Александр* или *Мария*",
        parse_mode="Markdown",
        reply_markup=back_menu()
    )
    await callback.answer()

# ========== ТАИНСТВА ==========
@dp.callback_query(F.data == "daily_gospel")
async def cb_daily_gospel(callback: CallbackQuery):
    await callback.answer()
    await callback.message.answer("📖 Нахожу Евангельская мысль...")
    text = await get_daily_gospel()
    await callback.message.answer(
        text,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📅 Календарь", callback_data="calendar")],
            [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")],
        ])
    )

@dp.callback_query(F.data == "cal_fast_today")
async def cb_fast_today(callback: CallbackQuery):
    await callback.answer()
    text = get_fast_today()
    await callback.message.answer(
        text,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🥗 Все посты", callback_data="cal_fasts")],
            [InlineKeyboardButton(text="📅 Календарь", callback_data="calendar")],
            [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")],
        ])
    )


@dp.callback_query(F.data == "prayer_for_me")
async def cb_prayer_for_me(callback: CallbackQuery):
    await callback.answer()
    user_id = callback.from_user.id
    set_step(user_id, "prayer_for_me_name")
    await callback.message.answer(
        "🙏 *Молитва за меня*\n\n"
        "Напишите ваше имя (церковное) и просьбу к Богу.\n\n"
        "Например: *Александр, прошу о здравии и помощи в трудном деле*\n\n"
        "Или просто: *помощь в работе*, *здоровье семьи*, *мир в душе*",
        parse_mode="Markdown",
        reply_markup=back_menu()
    )

@dp.callback_query(F.data == "make_zapiska")
async def cb_make_zapiska(callback: CallbackQuery):
    await callback.answer()
    user_id = callback.from_user.id
    set_step(user_id, "zapiska_type")
    await callback.message.answer(
        "✍️ *Составить записку в храм*\n\n"
        "Какая записка нужна?",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💛 О здравии", callback_data="zapiska_zdravie")],
            [InlineKeyboardButton(text="🕯️ Об упокоении", callback_data="zapiska_upokoenie")],
            [InlineKeyboardButton(text="◀️ Назад", callback_data="sacraments")],
        ])
    )

@dp.callback_query(F.data.startswith("zapiska_"))
async def cb_zapiska_type(callback: CallbackQuery):
    await callback.answer()
    user_id = callback.from_user.id
    ztype = callback.data.replace("zapiska_", "")
    if ztype == "zdravie":
        set_step(user_id, "zapiska_zdravie_names")
        await callback.message.answer(
            "💛 *Записка о здравии*\n\n"
            "Введите имена через запятую.\n\nПишите как знаете — привычное или полное имя.\nЕсли не знаете крещёного — ничего страшного, в храме помогут.\n\nПример: Саша, Мария, дед Николай",
            parse_mode="Markdown",
            reply_markup=back_menu()
        )
    elif ztype == "upokoenie":
        set_step(user_id, "zapiska_upokoenie_names")
        await callback.message.answer(
            "🕯️ *Записка об упокоении*\n\n"
            "Введите имена через запятую.\n\nПишите как знаете — привычное или полное имя.\nЕсли не знаете крещёного — ничего страшного, в храме помогут.\n\nПример: Бабушка Нина, Николай, дед Василий",
            parse_mode="Markdown",
            reply_markup=back_menu()
        )

@dp.callback_query(F.data == "sacraments")
async def cb_sacraments(callback: CallbackQuery):
    await callback.message.answer(
        "⛪ *Таинства и обряды*\n\nВыберите раздел:",
        parse_mode="Markdown",
        reply_markup=sacraments_menu()
    )
    await callback.answer()

SACRAMENT_PRAYERS = {
    "ispoved": [
        ("📖 Канон покаянный", "prayer_pokayanny_kanon"),
        ("🌅 Утренняя молитва", "prayer_morning_ru"),
    ],
    "prichaschenie": [
        ("📖 Канон покаянный", "prayer_pokayanny_kanon"),
        ("✝️ Правило ко Причастию", "prayer_prichaschenie"),
        ("🌅 Утренняя молитва", "prayer_morning_ru"),
    ],
    "kreshchenie": [
        ("🙏 Отче наш", "prayer_before_meal"),
        ("🌅 Утренняя молитва", "prayer_morning_ru"),
    ],
    "venchanie": [
        ("🌅 Утренняя молитва", "prayer_morning_ru"),
        ("🌙 Вечерняя молитва", "prayer_evening_ru"),
    ],
    "otpevanie": [
        ("🕯️ Молитва об упокоении", "prayer_upokoenie"),
    ],
    "sobor": [
        ("🌅 Утренняя молитва", "prayer_morning_ru"),
        ("✝️ Правило ко Причастию", "prayer_prichaschenie"),
    ],
}

@dp.callback_query(F.data.startswith("sacr_"))
async def cb_sacrament(callback: CallbackQuery):
    key  = callback.data.replace("sacr_", "")
    sacr = SACRAMENTS.get(key)
    if not sacr:
        await callback.answer("Раздел не найден")
        return

    # Строим клавиатуру с кнопками молитв
    kb_rows = []
    prayers = SACRAMENT_PRAYERS.get(key, [])
    if prayers:
        for i in range(0, len(prayers), 2):
            row = []
            row.append(InlineKeyboardButton(text=prayers[i][0], callback_data=prayers[i][1]))
            if i + 1 < len(prayers):
                row.append(InlineKeyboardButton(text=prayers[i+1][0], callback_data=prayers[i+1][1]))
            kb_rows.append(row)

    kb_rows.append([InlineKeyboardButton(text="⭐ Сохранить в избранное", callback_data=f"save_sacr_{key}")])
    kb_rows.append([InlineKeyboardButton(text="◀️ Назад", callback_data="sacraments")])
    kb_rows.append([InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")])

    await callback.message.answer(
        f"*{sacr['title']}*\n\n{sacr['text']}",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows)
    )
    await callback.answer()

@dp.callback_query(F.data.startswith("save_sacr_"))
async def cb_save_sacr(callback: CallbackQuery):
    key  = callback.data.replace("save_sacr_", "")
    sacr = SACRAMENTS.get(key)
    if sacr:
        save_favorite(callback.from_user.id, sacr["title"], sacr["text"])
        await callback.answer("⭐ Сохранено в избранное!", show_alert=False)
    else:
        await callback.answer("Ошибка сохранения")

# ========== СВЯТЫЕ ==========
@dp.callback_query(F.data == "saints")
async def cb_saints(callback: CallbackQuery):
    today_saints = get_todays_saints()
    today_str    = date_ru("short")
    text = f"👼 *Святые*\n\n"
    if today_saints:
        text += f"*Сегодня, {today_str}, память:*\n"
        for name, desc in today_saints[:3]:
            text += f"— {desc}\n"
        text += "\n"
    text += "Выберите действие:"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔍 Найти святого по имени", callback_data="saint_search")],
        [InlineKeyboardButton(text="👼 Все именинники сегодня", callback_data="cal_namedays")],
        [InlineKeyboardButton(text="◀️ Главное меню",           callback_data="main_menu")],
    ])
    await callback.message.answer(text, parse_mode="Markdown", reply_markup=kb)
    await callback.answer()

@dp.callback_query(F.data == "saint_search")
async def cb_saint_search(callback: CallbackQuery):
    set_step(callback.from_user.id, "saint_search")
    await callback.message.answer(
        "🔍 Введите имя святого или своё имя\n"
        "для поиска дней памяти:\n\n"
        "Например: *Николай*, *Матрона*, *Сергий*",
        parse_mode="Markdown",
        reply_markup=back_menu()
    )
    await callback.answer()

# ========== СВЯТЫЕ МЕСТА ==========
@dp.callback_query(F.data == "holy_places")
async def cb_holy_places(callback: CallbackQuery):
    await callback.message.answer(
        "🏛️ *Святые места*\n\nВыберите раздел:",
        parse_mode="Markdown",
        reply_markup=holy_places_menu()
    )
    await callback.answer()

@dp.callback_query(F.data.startswith("place_"))
async def cb_place(callback: CallbackQuery):
    key   = callback.data.replace("place_", "")
    place = HOLY_PLACES.get(key)
    if not place:
        await callback.answer("Раздел не найден")
        return
    await callback.message.answer(
        f"*{place['title']}*\n\n{place['text']}",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⭐ Сохранить в избранное", callback_data=f"save_place_{key}")],
            [InlineKeyboardButton(text="◀️ Назад",                callback_data="holy_places")],
            [InlineKeyboardButton(text="🏠 Главное меню",         callback_data="main_menu")],
        ])
    )
    await callback.answer()

@dp.callback_query(F.data.startswith("save_place_"))
async def cb_save_place(callback: CallbackQuery):
    key   = callback.data.replace("save_place_", "")
    place = HOLY_PLACES.get(key)
    if place:
        save_favorite(callback.from_user.id, place["title"], place["text"])
        await callback.answer("⭐ Сохранено в избранное!", show_alert=False)
    else:
        await callback.answer("Ошибка сохранения")

# ========== БИБЛИОТЕКА ==========
LIBRARY_CONTENT = {
    "slovar": {
        "title": "📝 Церковный словарь",
        "text": (
            "📝 *Церковный словарь*\n\n"
            "Часто встречающиеся слова объяснённые простым языком:\n\n"
            "⛪ *Аналой* — высокий столик с наклонной поверхностью\n"
            "на котором лежат иконы или Евангелие.\n\n"
            "📖 *Акафист* — особое хвалебное песнопение\n"
            "в честь Христа, Богородицы или святого.\n"
            "Читается стоя (а-кафист = не сидя).\n\n"
            "🫒 *Елей* — освящённое растительное масло.\n"
            "Используется при помазании в Таинствах.\n\n"
            "🧣 *Епитрахиль* — длинная лента священника\n"
            "надеваемая на шею. Символ благодати священства.\n\n"
            "📿 *Епитимья* — духовное упражнение\n"
            "назначаемое священником после исповеди.\n"
            "Например: поклоны, пост, молитвы.\n\n"
            "🏛️ *Иконостас* — перегородка из икон\n"
            "отделяющая алтарь от основной части храма.\n\n"
            "🕯️ *Канон* — богослужебное произведение\n"
            "из 9 песней. Читается или поётся на службах.\n\n"
            "⛪ *Канун* — прямоугольный подсвечник\n"
            "с распятием. Свечи здесь ставят за упокой.\n\n"
            "💧 *Крещенская вода* — вода освящённая\n"
            "в праздник Богоявления. Не портится годами.\n\n"
            "🎵 *Литургия* — главное богослужение Церкви.\n"
            "На ней совершается Таинство Причастия.\n\n"
            "🧴 *Миро* — особое освящённое масло\n"
            "с ароматическими веществами. Используется\n"
            "при Таинстве Миропомазания.\n\n"
            "🧣 *Омофор* — широкая лента епископа.\n"
            "Символ заблудшей овцы на плечах пастыря.\n\n"
            "🍞 *Просфора* — небольшой круглый хлеб\n"
            "из которого на Литургии вынимаются частицы.\n"
            "Раздаётся верующим после службы.\n\n"
            "🎵 *Тропарь* — краткое песнопение\n"
            "раскрывающее суть праздника или святого.\n\n"
            "🍷 *Теплота* — тёплая смесь воды и вина\n"
            "которой запивают Причастие.\n\n"
            "✝️ *Царские врата* — центральные двери\n"
            "иконостаса. Открываются только в особые моменты."
        )
    },
    "faq": {
        "title": "❓ Частые вопросы о вере",
        "text": (
            "❓ *Частые вопросы о вере*\n\n"
            "🔸 *Можно ли креститься в любом возрасте?*\n"
            "Да. Крещение совершается над людьми\n"
            "любого возраста — от младенцев до стариков.\n\n"
            "🔸 *Обязательно ли ходить в церковь?*\n"
            "Православная жизнь невозможна без Церкви.\n"
            "Таинства — Причастие, Исповедь — совершаются\n"
            "только в храме. Домашняя молитва важна,\n"
            "но не заменяет церковную жизнь.\n\n"
            "🔸 *Что делать если не понимаю службу?*\n"
            "Это нормально. Купите книгу «Закон Божий»\n"
            "или скачайте объяснение Литургии.\n"
            "Со временем понимание придёт само.\n\n"
            "🔸 *Можно ли причащаться без поста?*\n"
            "Поговорите со священником — в особых случаях\n"
            "(болезнь, немощь) он может разрешить\n"
            "сокращённое правило.\n\n"
            "🔸 *Что такое грех?*\n"
            "Грех — это отступление от Бога и Его заповедей.\n"
            "Не наказание от Бога, а рана которую\n"
            "человек наносит себе сам.\n\n"
            "🔸 *Почему православные постятся?*\n"
            "Пост — это не диета. Это воздержание\n"
            "тела для усиления духа. Пост без молитвы\n"
            "— просто голодание.\n\n"
            "🔸 *Можно ли молиться своими словами?*\n"
            "Да и это очень хорошо. Господь слышит\n"
            "молитву сердца. Можно и нужно говорить\n"
            "с Богом своими словами.\n\n"
            "🔸 *Что будет после смерти?*\n"
            "Православная Церковь учит о воскресении\n"
            "мёртвых и жизни будущего века. Душа\n"
            "бессмертна и продолжает жить после смерти тела.\n\n"
            "🔸 *Почему Бог допускает страдания?*\n"
            "Один из самых глубоких вопросов веры.\n"
            "Страдание может очищать, смирять и вести\n"
            "к Богу. Задайте этот вопрос в разделе\n"
            "❓ Задать вопрос — ответим развёрнуто.\n\n"
            "🔸 *С чего начать церковную жизнь?*\n"
            "1. Покрестититься если не крещены\n"
            "2. Найти свой приход и батюшку\n"
            "3. Прийти на Исповедь\n"
            "4. Причаститься\n"
            "5. Читать утренние и вечерние молитвы"
        )
    },
    "literatura": {
        "title": "📚 Православная литература",
        "text": (
            "📚 *Православная литература*\n\n"
            "📥 *Скачать бесплатно (PDF):*\n\n"
            "Нажмите на кнопку ниже чтобы получить книгу.\n\n"
            "📖 *Рекомендуем прочитать:*\n\n"
            "⭐ *Несвятые святые* — архим. Тихон Шевкунов\n"
            "Самая читаемая православная книга нашего времени.\n"
            "Живые истории из монастырской жизни.\n"
            "Читается как роман — не оторваться.\n\n"
            "📖 *Закон Божий* — прот. Серафим Слободской\n"
            "Лучшая книга для начинающих. Всё о вере\n"
            "доступным языком. Начните с неё.\n\n"
            "📖 *Таинство веры* — митр. Иларион Алфеев\n"
            "Введение в православное богословие.\n"
            "Просто о сложном — для думающего человека.\n\n"
            "📖 *Паисий Святогорец — Слова* (5 томов)\n"
            "Мудрость афонского старца о духовной жизни,\n"
            "семье, молитве, современном мире.\n\n"
            "📖 *Душа после смерти* — иером. Серафим Роуз\n"
            "О том что происходит с душой после смерти.\n"
            "Православный взгляд, основанный на Предании.\n\n"
            "📖 *Лествица* — прп. Иоанн Лествичник\n"
            "Классика православной аскетики. VI век.\n"
            "О ступенях духовного восхождения.\n\n"
            "📖 *Добротолюбие* — антология святых отцов\n"
            "Сборник наставлений подвижников IV-XV вв.\n"
            "Фундамент православной духовности.\n\n"
            "📖 *Несвятые святые* и другие современные книги\n"
            "ищите на: *litres.ru*, *ozon.ru*,\n"
            "в церковных лавках вашего храма."
        )
    },
}

PDF_BOOKS = {
    "pdf_bible": {
        "title": "📖 Библия (Синодальный перевод)",
        "url": "https://azbyka.ru/biblia/in/pdf/bibliya-sinodalnij-perevod.pdf"
    },
    "pdf_nt": {
        "title": "📖 Новый Завет",
        "url": "https://azbyka.ru/otechnik/Biblia/novyj-zavet-sinodalnij-perevod/"
    },
    "pdf_molitvoslov": {
        "title": "🙏 Православный молитвослов",
        "url": "https://azbyka.ru/molitvoslov/"
    },
    "pdf_psaltir": {
        "title": "📜 Псалтирь",
        "url": "https://azbyka.ru/otechnik/Biblia/psaltir-v-russkom-perevode/"
    },
    "pdf_lestvica": {
        "title": "📖 Лествица — прп. Иоанн Лествичник",
        "url": "https://azbyka.ru/otechnik/Ioann_Lestvichnik/lestvitsa/"
    },
    "pdf_dobrotolyubie": {
        "title": "📖 Добротолюбие",
        "url": "https://azbyka.ru/otechnik/prochee/dobrotoljubie_tom1/"
    },
}

def library_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📝 Церковный словарь",         callback_data="lib_slovar")],
        [InlineKeyboardButton(text="❓ Частые вопросы о вере",     callback_data="lib_faq")],
        [InlineKeyboardButton(text="📚 Православная литература",   callback_data="lib_literatura")],
        [InlineKeyboardButton(text="📥 Скачать книги бесплатно",   callback_data="lib_pdf")],
        [InlineKeyboardButton(text="◀️ Главное меню",              callback_data="main_menu")],
    ])

def pdf_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📖 Библия",              url="https://azbyka.ru/biblia/")],
        [InlineKeyboardButton(text="📖 Новый Завет",         url="https://azbyka.ru/otechnik/Biblia/novyj-zavet-sinodalnij-perevod/")],
        [InlineKeyboardButton(text="🙏 Молитвослов",         url="https://azbyka.ru/molitvoslov/")],
        [InlineKeyboardButton(text="📜 Псалтирь",            url="https://azbyka.ru/otechnik/Biblia/psaltir-v-russkom-perevode/")],
        [InlineKeyboardButton(text="📖 Лествица",            url="https://azbyka.ru/otechnik/Ioann_Lestvichnik/lestvitsa/")],
        [InlineKeyboardButton(text="📖 Добротолюбие",        url="https://azbyka.ru/otechnik/prochee/dobrotoljubie_tom1/")],
        [InlineKeyboardButton(text="◀️ Назад",               callback_data="library")],
    ])

@dp.callback_query(F.data == "library")
async def cb_library(callback: CallbackQuery):
    await callback.message.answer(
        "📚 *Библиотека*\n\n"
        "Выберите раздел:",
        parse_mode="Markdown",
        reply_markup=library_menu()
    )
    await callback.answer()

@dp.callback_query(F.data.startswith("lib_"))
async def cb_library_section(callback: CallbackQuery):
    key = callback.data.replace("lib_", "")
    if key == "pdf":
        await callback.message.answer(
            "📥 *Скачать книги бесплатно*\n\n"
            "Все книги размещены на сайте Азбука.ру —\n"
            "крупнейшей православной библиотеке.\n\n"
            "Нажмите на название книги чтобы открыть 👇",
            parse_mode="Markdown",
            reply_markup=pdf_menu()
        )
    else:
        content = LIBRARY_CONTENT.get(key)
        if not content:
            await callback.answer("Раздел не найден")
            return
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Библиотека",   callback_data="library")],
            [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")],
        ])
        if key == "literatura":
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="◀️ Библиотека",  callback_data="library")],
                [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")],
            ])
        await callback.message.answer(
            content["text"],
            parse_mode="Markdown",
            reply_markup=kb
        )
    await callback.answer()

# ========== ФОТО ==========
@dp.callback_query(F.data == "photo_menu")
async def cb_photo_menu(callback: CallbackQuery):
    await callback.message.answer(
        "📸 *Определить по фото*\n\n"
        "Выберите что хотите узнать:",
        parse_mode="Markdown",
        reply_markup=photo_menu()
    )
    await callback.answer()

@dp.callback_query(F.data == "photo_church")
async def cb_photo_church(callback: CallbackQuery):
    set_step(callback.from_user.id, "photo_church")
    await callback.message.answer(
        "🕌 *Фото храма или монастыря*\n\n"
        "Сфотографируйте храм или монастырь —\n"
        "я расскажу его историю и значение.\n\n"
        "Отправьте фотографию 👇",
        parse_mode="Markdown",
        reply_markup=back_section("photo_menu")
    )
    await callback.answer()

@dp.callback_query(F.data == "photo_icon")
async def cb_photo_icon(callback: CallbackQuery):
    set_step(callback.from_user.id, "photo_icon")
    await callback.message.answer(
        "🖼️ *Фото иконы*\n\n"
        "Сфотографируйте икону —\n"
        "я определю кто на ней изображён\n"
        "и расскажу как молиться.\n\n"
        "Отправьте фотографию 👇",
        parse_mode="Markdown",
        reply_markup=back_section("photo_menu")
    )
    await callback.answer()

# ========== НАЙТИ ХРАМ ==========
@dp.callback_query(F.data == "find_church")
async def cb_find_church(callback: CallbackQuery):
    set_step(callback.from_user.id, "find_church")
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📍 Отправить геолокацию", callback_data="send_location")],
        [InlineKeyboardButton(text="✏️ Ввести город текстом",  callback_data="city_text")],
        [InlineKeyboardButton(text="◀️ Главное меню",          callback_data="main_menu")],
    ])
    await callback.message.answer(
        "🗺️ *Найти храм рядом*\n\n"
        "Отправьте геолокацию или введите\n"
        "название города:",
        parse_mode="Markdown",
        reply_markup=kb
    )
    await callback.answer()

@dp.callback_query(F.data == "send_location")
async def cb_send_location(callback: CallbackQuery):
    set_step(callback.from_user.id, "find_church")
    await callback.message.answer(
        "Нажмите кнопку ниже и разрешите Telegram отправить вашу геолокацию:",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="📍 Отправить мою геолокацию", request_location=True)]],
            resize_keyboard=True,
            one_time_keyboard=True,
        ),
    )
    await callback.answer()


@dp.callback_query(F.data == "city_text")
async def cb_city_text(callback: CallbackQuery):
    set_step(callback.from_user.id, "find_church_city")
    await callback.message.answer(
        "Введите название города:",
        reply_markup=back_menu()
    )
    await callback.answer()

# ========== ПРОФИЛЬ ==========
@dp.callback_query(F.data == "profile")
async def cb_profile(callback: CallbackQuery):
    user = get_user(callback.from_user.id)
    await callback.message.answer(
        "👤 *Мой профиль*",
        parse_mode="Markdown",
        reply_markup=profile_menu(user)
    )
    await callback.answer()

@dp.callback_query(F.data == "profile_edit_name")
async def cb_edit_name(callback: CallbackQuery):
    set_step(callback.from_user.id, "edit_name")
    await callback.message.answer(
        "✏️ Введите ваше имя при крещении\n"
        "(или обычное имя если нет церковного):",
        reply_markup=back_section("profile")
    )
    await callback.answer()

@dp.callback_query(F.data == "profile_edit_birth")
async def cb_edit_birth(callback: CallbackQuery):
    set_step(callback.from_user.id, "edit_birth")
    await callback.message.answer(
        "🎂 Введите дату рождения\n"
        "в формате *ДД.ММ*\n\n"
        "Например: *15.03*",
        parse_mode="Markdown",
        reply_markup=back_section("profile")
    )
    await callback.answer()

@dp.callback_query(F.data == "toggle_notifications")
async def cb_toggle_notifications(callback: CallbackQuery):
    user_id = callback.from_user.id
    conn = db_connect()
    c = conn.cursor()
    c.execute("SELECT notifications FROM users WHERE user_id=?", (user_id,))
    row = c.fetchone()
    current = row[0] if row and row[0] is not None else 1
    new_val = 0 if current else 1
    c.execute("UPDATE users SET notifications=? WHERE user_id=?", (new_val, user_id))
    conn.commit()
    conn.close()
    set_funnel_flag(user_id, "Telegram", "notifications_enabled", new_val)
    status = "включены ✅" if new_val else "отключены 🔕"
    await callback.answer(f"Утренние уведомления {status}", show_alert=True)
    user = get_user(user_id)
    await callback.message.edit_reply_markup(reply_markup=profile_menu(user))

@dp.callback_query(F.data == "profile_patron_prayer")
async def cb_patron_prayer(callback: CallbackQuery):
    user = get_user(callback.from_user.id)
    name = (user.get("church_name") or "").strip()
    angel = user.get("angel_day") or ""

    if not name:
        await callback.message.answer(
            "👤 Укажите имя в профиле — тогда найдём молитву вашему покровителю 🙏",
            reply_markup=back_section("profile")
        )
        await callback.answer()
        return

    name_lower = name.lower()
    # Сначала ищем в готовых молитвах
    patron_prayers = {
        "николай": "prayer_nikolay",
        "николай чудотворец": "prayer_nikolay",
        "матрона": "prayer_matrona",
        "матрона московская": "prayer_matrona",
    }
    prayer_key = patron_prayers.get(name_lower)
    if prayer_key and prayer_key in PRAYERS:
        prayer = PRAYERS[prayer_key]
        await callback.message.answer(
            f"🙏 *Молитва вашему небесному покровителю*\n\n"
            f"*{prayer['title']}*\n\n{prayer['text']}",
            parse_mode="Markdown",
            reply_markup=back_section("profile")
        )
        await callback.answer()
        return

    # Проверяем кеш в БД
    conn = db_connect()
    c = conn.cursor()
    c.execute("CREATE TABLE IF NOT EXISTS patron_prayers_cache (name TEXT PRIMARY KEY, prayer TEXT)")
    c.execute("SELECT prayer FROM patron_prayers_cache WHERE name=?", (name_lower,))
    row = c.fetchone()
    conn.close()

    if row:
        await callback.message.answer(
            f"🙏 *Молитва небесному покровителю — {name}*\n\n{row[0]}",
            parse_mode="Markdown",
            reply_markup=back_section("profile")
        )
        await callback.answer()
        return

    # Генерируем через Claude
    await callback.message.answer("🙏 Нахожу молитву вашему покровителю...")
    saint_info = f"{name}"
    if angel:
        saint_info += f" (день памяти: {angel})"

    try:
        message = await claude_messages_create(
            model="claude-sonnet-4-5",
            max_tokens=600,
            system=(
                "Ты православный помощник. Напиши краткую молитву православному святому. "
                "Молитва должна быть в православной традиции, тёплой и доступной. "
                "Длина: 8-12 строк. Начни с обращения к святому. Закончи словом Аминь. "
                "Только текст молитвы, без пояснений."
            ),
            messages=[{"role": "user", "content": f"Напиши молитву святому: {saint_info}"}]
        )
        prayer_text = message.content[0].text

        # Сохраняем в кеш
        conn2 = db_connect()
        c2 = conn2.cursor()
        c2.execute("INSERT OR REPLACE INTO patron_prayers_cache (name, prayer) VALUES (?,?)",
                   (name_lower, prayer_text))
        conn2.commit()
        conn2.close()

        await callback.message.answer(
            f"🙏 *Молитва небесному покровителю — {name}*\n\n{prayer_text}",
            parse_mode="Markdown",
            reply_markup=back_section("profile")
        )
    except Exception as e:
        logging.error(f"Ошибка генерации молитвы: {e}")
        await callback.message.answer(
            "🙏 Обратитесь к своему святому своими словами —\n"
            "Господь слышит молитву из сердца.",
            reply_markup=back_section("profile")
        )
    await callback.answer()

@dp.callback_query(F.data == "profile_angel_info")
async def cb_angel_info(callback: CallbackQuery):
    user  = get_user(callback.from_user.id)
    angel = user.get("angel_day") or "не определён"
    name  = user.get("church_name") or "—"
    await callback.message.answer(
        f"👼 *День ангела*\n\n"
        f"Имя: *{name}*\n"
        f"День ангела: *{angel}*\n\n"
        f"День ангела — это день памяти святого\n"
        f"с вашим именем, ближайший после\n"
        f"вашего дня рождения.\n\n"
        f"Помолитесь своему небесному покровителю! 🙏",
        parse_mode="Markdown",
        reply_markup=back_section("profile")
    )
    await callback.answer()

@dp.callback_query(F.data == "profile_remind")
async def cb_remind(callback: CallbackQuery):
    await callback.message.answer(
        "🔔 За сколько дней напомнить о дне ангела?",
        reply_markup=remind_menu()
    )
    await callback.answer()

@dp.callback_query(F.data.startswith("remind_"))
async def cb_set_remind(callback: CallbackQuery):
    days = int(callback.data.replace("remind_", ""))
    conn = db_connect()
    c    = conn.cursor()
    c.execute("UPDATE users SET remind_days=? WHERE user_id=?", (days, callback.from_user.id))
    conn.commit()
    conn.close()
    await callback.message.answer(
        f"✅ Буду напоминать о дне ангела за {days} дн.!",
        reply_markup=back_section("profile")
    )
    await callback.answer()

# ========== ИЗБРАННОЕ ==========
@dp.callback_query(F.data == "favorites")
async def cb_favorites(callback: CallbackQuery):
    favs = get_favorites(callback.from_user.id)
    if not favs:
        await callback.message.answer(
            "⭐ *Избранные молитвы*\n\n"
            "У вас пока нет сохранённых молитв.\n\n"
            "Нажмите «⭐ Сохранить в избранное»\n"
            "в любой молитве!",
            parse_mode="Markdown",
            reply_markup=back_menu()
        )
    else:
        kb_rows = []
        for fav_id, title, saved_at in favs:
            kb_rows.append([InlineKeyboardButton(
                text=f"🙏 {title[:35]}",
                callback_data=f"fav_{fav_id}"
            )])
        kb_rows.append([InlineKeyboardButton(text="◀️ Главное меню", callback_data="main_menu")])
        await callback.message.answer(
            "⭐ *Избранные молитвы:*",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows)
        )
    await callback.answer()

@dp.callback_query(F.data.startswith("fav_"))
async def cb_fav_view(callback: CallbackQuery):
    fav_id = int(callback.data.replace("fav_", ""))
    conn   = db_connect()
    c      = conn.cursor()
    c.execute("SELECT title, content FROM favorites WHERE id=? AND user_id=?",
              (fav_id, callback.from_user.id))
    row = c.fetchone()
    conn.close()
    if row:
        await callback.message.answer(
            f"*{row[0]}*\n\n{row[1]}",
            parse_mode="Markdown",
            reply_markup=back_section("favorites")
        )
    await callback.answer()

# ========== ОТЗЫВЫ ==========
@dp.callback_query(F.data == "review")
async def cb_review(callback: CallbackQuery):
    set_step(callback.from_user.id, "review")
    await callback.message.answer(
        "💬 *Отзыв или пожелание*\n\n"
        "Вы можете оставить отзыв или пожелание\n"
        "по улучшению проекта.\n\n"
        "Что вам нравится? Чего не хватает?\n"
        "Какие функции хотели бы видеть?\n\n"
        "✏️ Напишите ваш отзыв или пожелание\n"
        "текстом или голосовым сообщением 👇",
        parse_mode="Markdown",
        reply_markup=back_menu()
    )
    await callback.answer()


async def notify_owner_about_review(review_id, message: Message, review_text: str):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✍️ Ответить пользователю", callback_data=f"owner_review_reply:{review_id}")],
        [InlineKeyboardButton(text="✅ Отметить обработанным", callback_data=f"owner_review_done:{review_id}")],
    ])
    username = f"@{message.from_user.username}" if message.from_user.username else "—"
    owner_text = (
        f"💬 Новый отзыв #{review_id} в «С верой» Telegram\n\n"
        f"Имя: {message.from_user.first_name or '—'}\n"
        f"Username: {username}\n"
        f"ID: {message.from_user.id}\n\n"
        f"{review_text[:3000]}"
    )
    await bot.send_message(OWNER_ID, owner_text, reply_markup=keyboard)


async def process_new_review(message: Message, review_text: str):
    review_text = review_text.strip()
    if not review_text:
        await message.answer("⚠️ Отзыв пустой. Напишите текст или отправьте голосовое сообщение.")
        return
    review_id = create_review_record(
        message.from_user.id,
        message.chat.id,
        message.from_user.username or "",
        message.from_user.first_name or "",
        review_text,
    )
    asyncio.create_task(asyncio.to_thread(
        sheets_add_review_tg,
        review_id,
        message.from_user.id,
        message.from_user.username or "",
        message.from_user.first_name or "",
        review_text,
    ))
    try:
        await notify_owner_about_review(review_id, message, review_text)
    except Exception as e:
        logging.error(f"Не удалось уведомить владельца об отзыве #{review_id}: {e}")
    set_step(message.from_user.id, "idle")
    set_funnel_flag(message.from_user.id, "Telegram", "review_left", 1)
    await message.answer(
        "☦️ Спасибо за ваш отзыв!\n\nМы его получили. При необходимости команда проекта ответит вам прямо здесь.\n\nМожно ли использовать отзыв в канале анонимно — без имени и личных данных?",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Да, анонимно", callback_data="review_consent:yes")],
            [InlineKeyboardButton(text="Нет", callback_data="review_consent:no")],
        ]),
    )


async def process_owner_review_reply(message: Message, review_id: int, reply_text: str):
    review = get_review_record(review_id)
    if not review:
        set_step(message.from_user.id, "idle")
        await message.answer(f"⚠️ Отзыв #{review_id} не найден.")
        return
    reply_text = reply_text.strip()
    if not reply_text:
        await message.answer("⚠️ Ответ пустой. Напишите текст ответа.")
        return
    try:
        await bot.send_message(
            review["chat_id"],
            "☦️ Ответ команды проекта «С верой»\n\n" + reply_text[:3500],
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="💬 Написать ещё", callback_data="review")],
                [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")],
            ]),
        )
    except Exception as e:
        await message.answer(f"⚠️ Не удалось отправить ответ пользователю: {e}")
        return
    replied_at = update_review_record(review_id, "answered", reply_text, "Владелец")
    asyncio.create_task(asyncio.to_thread(
        sheets_update_review_tg, review_id, "Отвечено", reply_text, replied_at, "Владелец"
    ))
    set_step(message.from_user.id, "idle")
    await message.answer(f"✅ Ответ на отзыв #{review_id} отправлен пользователю.")


@dp.callback_query(F.data.startswith("owner_review_reply:"))
async def cb_owner_review_reply(callback: CallbackQuery):
    if callback.from_user.id != OWNER_ID:
        await callback.answer("Недоступно", show_alert=True)
        return
    get_user(callback.from_user.id, callback.from_user.username or "", callback.from_user.first_name or "")
    review_id = int(callback.data.split(":", 1)[1])
    review = get_review_record(review_id)
    if not review:
        await callback.message.answer(f"⚠️ Отзыв #{review_id} не найден.")
        await callback.answer()
        return
    set_step(callback.from_user.id, f"owner_review_reply:{review_id}")
    await callback.message.answer(
        f"✍️ Ответ на отзыв #{review_id}\n\n"
        f"Пользователь: {review.get('first_name') or review.get('user_id')}\n"
        f"Отзыв: {review.get('review_text', '')[:2000]}\n\n"
        "Напишите ответ следующим сообщением.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отменить", callback_data="owner_review_cancel")]
        ]),
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("owner_review_done:"))
async def cb_owner_review_done(callback: CallbackQuery):
    if callback.from_user.id != OWNER_ID:
        await callback.answer("Недоступно", show_alert=True)
        return
    review_id = int(callback.data.split(":", 1)[1])
    review = get_review_record(review_id)
    if not review:
        await callback.message.answer(f"⚠️ Отзыв #{review_id} не найден.")
        await callback.answer()
        return
    replied_at = update_review_record(review_id, "handled", review.get("owner_reply", ""), "Владелец")
    asyncio.create_task(asyncio.to_thread(
        sheets_update_review_tg, review_id, "Обработано", review.get("owner_reply", ""), replied_at, "Владелец"
    ))
    await callback.message.answer(f"✅ Отзыв #{review_id} отмечен как обработанный.")
    await callback.answer()


@dp.callback_query(F.data == "owner_review_cancel")
async def cb_owner_review_cancel(callback: CallbackQuery):
    if callback.from_user.id == OWNER_ID:
        set_step(callback.from_user.id, "idle")
        await callback.message.answer("Ответ отменён.")
    await callback.answer()


@dp.message(Command("reply"))
async def cmd_reply_user(message: Message):
    """Резервный ответ на старый отзыв: /reply USER_ID текст ответа"""
    if message.from_user.id != OWNER_ID:
        return
    parts = (message.text or "").split(maxsplit=2)
    if len(parts) < 3 or not parts[1].isdigit():
        await message.answer("Формат: /reply ID_пользователя текст ответа")
        return
    target_user = int(parts[1])
    reply_text = parts[2].strip()
    try:
        await bot.send_message(target_user, "☦️ Ответ команды проекта «С верой»\n\n" + reply_text[:3500])
    except Exception as e:
        await message.answer(f"⚠️ Не удалось отправить ответ: {e}")
        return
    replied_at = datetime.now().strftime("%d.%m.%Y %H:%M")
    asyncio.create_task(asyncio.to_thread(
        sheets_update_latest_review_by_user_tg,
        target_user,
        "Отвечено",
        reply_text,
        replied_at,
        "Владелец",
    ))
    await message.answer(f"✅ Ответ пользователю {target_user} отправлен.")

# ========== ПОЖЕРТВОВАНИЕ ==========
@dp.callback_query(F.data.in_({"donation", "donate"}))
async def cb_donation(callback: CallbackQuery):
    if not YOOKASSA_SHOP_ID or not YOOKASSA_SECRET:
        record_critical_error("donation_config_tg", "YOOKASSA_SHOP_ID/YOOKASSA_SECRET missing")
        await callback.message.answer("⚠️ Платежи временно недоступны. Владелец уже может увидеть ошибку в диагностике.", reply_markup=back_menu())
        await callback.answer()
        return
    set_step(callback.from_user.id, "donate_amount")
    await callback.message.answer(
        "🕯️ *Пожертвование на развитие проекта*\n"
        "*во славу Божию* ☦️\n\n"
        "Если бот помогает вам в духовной жизни —\n"
        "вы можете поддержать его развитие.\n\n"
        "Каждое пожертвование помогает:\n"
        "— Пополнять базу молитв и житий святых\n"
        "— Добавлять новые функции\n"
        "— Поддерживать сервер\n\n"
        "✏️ Напишите в ответ сумму в рублях\n"
        "и я создам ссылку для оплаты 👇",
        parse_mode="Markdown",
        reply_markup=back_menu()
    )
    await callback.answer()


# ========== ВОПРОС AI ==========
@dp.callback_query(F.data == "ask_question")
async def cb_ask_question(callback: CallbackQuery):
    set_step(callback.from_user.id, "ask_depth")
    await callback.message.answer(
        "❓ *Задать вопрос*\n\n"
        "Как вы хотите получить ответ?",
        parse_mode="Markdown",
        reply_markup=question_depth_menu()
    )
    await callback.answer()

@dp.callback_query(F.data.startswith("depth_"))
async def cb_depth(callback: CallbackQuery):
    depth = callback.data.replace("depth_", "")
    set_step(callback.from_user.id, f"question_{depth}")
    labels = {"short": "кратко", "medium": "развёрнуто", "deep": "глубоко"}
    await callback.message.answer(
        f"📝 Хорошо, отвечу *{labels.get(depth, '')}*.\n\n"
        f"Задайте ваш вопрос о вере текстом\n"
        f"или голосовым сообщением 🎤",
        parse_mode="Markdown",
        reply_markup=back_menu()
    )
    await callback.answer()

# Платная подписка V4 отключена до появления честных ограничений и ценности.

# ========== ОБРАБОТКА ФОТО ==========
@dp.message(F.photo)
async def handle_photo(message: Message):
    user_id = message.from_user.id
    user = get_user(user_id)
    step = user.get("step", "")

    if step not in ("photo_church", "photo_icon"):
        await message.answer("Выберите сначала что хотите определить 👇", reply_markup=photo_menu())
        return

    photo = message.photo[-1]
    file = await bot.get_file(photo.file_id)
    local_path = Path(f"/tmp/vera_photo_{user_id}_{uuid.uuid4().hex}.jpg")
    photo_type = "church" if step == "photo_church" else "icon"

    set_step(user_id, "idle")
    await message.answer("⏳ Выполняю предварительное распознавание...")
    try:
        await bot.download_file(file.file_path, str(local_path))
        result = await analyze_photo_gpt("", photo_type, local_path=str(local_path))
    except Exception as e:
        logging.error(f"Ошибка скачивания или анализа фото: {e}")
        record_critical_error("photo_tg", e)
        result = "Не удалось обработать фото. Попробуйте ещё раз."
    finally:
        with suppress(Exception):
            local_path.unlink(missing_ok=True)

    await message.answer(result, reply_markup=back_menu())

# ========== ОБРАБОТКА ГЕОЛОКАЦИИ ==========
@dp.message(F.location)
async def handle_location(message: Message):
    user = get_user(message.from_user.id)
    if user.get("step") != "find_church":
        return
    lat = message.location.latitude
    lon = message.location.longitude
    set_step(message.from_user.id, "idle")
    maps_url = f"https://maps.yandex.ru/?text=православный+храм&ll={lon},{lat}&z=14"
    await message.answer("📍 Геолокация получена.", reply_markup=ReplyKeyboardRemove())
    await message.answer(
        f"🗺️ *Православные храмы рядом с вами:*\n\n"
        f"Нажмите ссылку — откроется Яндекс.Карты\n"
        f"с ближайшими храмами:\n\n"
        f"{maps_url}",
        parse_mode="Markdown",
        reply_markup=back_menu()
    )

# ========== ОБРАБОТКА ГОЛОСОВЫХ ==========
@dp.message(F.voice)
async def handle_voice(message: Message):
    user    = get_user(message.from_user.id)
    step    = user.get("step", "")
    user_id = message.from_user.id

    if not step or step == "idle":
        await message.answer("☦️ Выберите функцию из меню 👇", reply_markup=main_menu())
        return

    await message.answer("🎤 Распознаю голосовое...")
    try:
        file      = await bot.get_file(message.voice.file_id)
        file_path = f"/tmp/vera_voice_{user_id}_{uuid.uuid4().hex}.ogg"
        await bot.download_file(file.file_path, file_path)
        text = await transcribe_voice(file_path)
        await message.answer(f"📝 *Распознал:* {text}\n\n⏳ Обрабатываю...", parse_mode="Markdown")

        # Вместо message.text = text (frozen!) — обрабатываем текст напрямую
        if step.startswith("question_"):
            depth = step.replace("question_", "")
            await message.answer("🙏 Молюсь... отвечаю...")
            answer = await ask_claude(text, depth)
            asyncio.create_task(asyncio.to_thread(sheets_update_activity, user_id))
            depth_labels = {"short": "💬 Кратко", "medium": "📖 Развёрнуто", "deep": "🙏 Глубоко"}
            if answer == "error":
                try:
                    await bot.send_message(OWNER_ID,
                        f"⚠️ Ошибка Claude (голос) в @Moya_Vera_bot\nПользователь: {user_id}\nВопрос: {text[:100]}")
                except Exception:
                    pass
                await message.answer(
                    "⚠️ Не удалось получить ответ. Попробуйте чуть позже.",
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="🔄 Попробовать снова", callback_data="ask_question")],
                        [InlineKeyboardButton(text="📢 Сообщить о проблеме", url="https://t.me/Boss023rus")],
                        [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")],
                    ])
                )
            else:
                await message.answer(
                    f"{depth_labels.get(depth, '')} *Ответ:*\n\n{answer}",
                    parse_mode="Markdown",
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="❓ Задать ещё вопрос", callback_data="ask_question")],
                        [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")],
                    ])
                )
            set_step(user_id, "idle")
        elif step == "review":
            await process_new_review(message, text)
        else:
            await message.answer(
                "☦️ Голосовые сообщения работают только при вводе вопроса о вере.\n"
                "Нажмите *Задать вопрос* в меню 👇",
                parse_mode="Markdown", reply_markup=main_menu()
            )
    except Exception as e:
        logging.error(f"Ошибка голосового: {e}")
        try:
            await bot.send_message(OWNER_ID, f"⚠️ Ошибка голосового в @Moya_Vera_bot\n{e}")
        except Exception:
            pass
        await message.answer(
            "⚠️ Не удалось обработать голосовое сообщение. Попробуйте написать текстом.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔄 Попробовать снова", callback_data="ask_question")],
                [InlineKeyboardButton(text="📢 Сообщить о проблеме", url="https://t.me/Boss023rus")],
                [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")],
            ])
        )

# ========== ОБРАБОТКА ТЕКСТА ==========
@dp.message(F.text)
async def handle_text(message: Message):
    user    = get_user(message.from_user.id)
    step    = user.get("step", "")
    user_id = message.from_user.id
    text    = message.text.strip()

    if user_id == OWNER_ID and step.startswith("owner_review_reply:"):
        try:
            review_id = int(step.split(":", 1)[1])
        except Exception:
            set_step(user_id, "idle")
            await message.answer("⚠️ Некорректный номер отзыва.")
            return
        await process_owner_review_reply(message, review_id, text)
        return

    # Онбординг — имя
    if step == "onboard_name":
        angel = ""
        set_step(user_id, "onboard_birth")
        conn = db_connect()
        c    = conn.cursor()
        c.execute("UPDATE users SET church_name=? WHERE user_id=?", (text, user_id))
        conn.commit()
        conn.close()
        await message.answer(
            f"✅ Записал имя: *{text}*\n\n"
            f"Теперь введите дату рождения\n"
            f"в формате *ДД.ММ*\n\n"
            f"Например: *15.03*",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⏭️ Пропустить", callback_data="onboard_skip")]
            ])
        )
        return

    # Онбординг — дата рождения
    if step == "onboard_birth":
        try:
            datetime.strptime(text, "%d.%m")
            church_name = ""
            conn = db_connect()
            c    = conn.cursor()
            c.execute("SELECT church_name FROM users WHERE user_id=?", (user_id,))
            row  = c.fetchone()
            conn.close()
            church_name = row[0] if row else ""
            angel = find_angel_day(church_name, text)
            save_profile(user_id, church_name, text, angel)
            asyncio.create_task(asyncio.to_thread(
                sheets_update_profile, user_id, church_name, text, angel
            ))
            angel_text = f"\n👼 Возможный день памяти покровителя: *{angel}*" if angel else "\n👼 Возможный день памяти покровителя: имя не найдено в базе"
            await message.answer(
                f"✅ *Профиль сохранён!*\n\n"
                f"Имя: *{church_name}*\n"
                f"Дата рождения: *{text}*\n"
                f"{angel_text}\n\n"
                f"Буду напоминать о дне ангела за 3 дня 🕊️",
                parse_mode="Markdown",
                reply_markup=main_menu()
            )
        except ValueError:
            await message.answer(
                "⚠️ Неверный формат даты.\n"
                "Введите в формате *ДД.ММ*, например: *15.03*",
                parse_mode="Markdown"
            )
        return

    # Редактирование имени
    if step == "edit_name":
        conn = db_connect()
        c    = conn.cursor()
        c.execute("SELECT birth_date FROM users WHERE user_id=?", (user_id,))
        row  = c.fetchone()
        birth = row[0] if row else ""
        conn.close()
        angel = find_angel_day(text, birth) if birth else ""
        conn2 = db_connect()
        c2    = conn2.cursor()
        c2.execute("UPDATE users SET church_name=?, angel_day=? WHERE user_id=?", (text, angel, user_id))
        conn2.commit()
        conn2.close()
        await message.answer(
            f"✅ Имя обновлено: *{text}*\n"
            f"👼 Возможный день памяти покровителя: *{angel or 'не найден'}*",
            parse_mode="Markdown",
            reply_markup=back_section("profile")
        )
        set_step(user_id, "idle")
        return

    # Редактирование даты рождения
    if step == "edit_birth":
        try:
            datetime.strptime(text, "%d.%m")
            conn = db_connect()
            c    = conn.cursor()
            c.execute("SELECT church_name FROM users WHERE user_id=?", (user_id,))
            row  = c.fetchone()
            conn.close()
            church_name = row[0] if row else ""
            angel       = find_angel_day(church_name, text) if church_name else ""
            conn2       = db_connect()
            c2          = conn2.cursor()
            c2.execute("UPDATE users SET birth_date=?, angel_day=? WHERE user_id=?", (text, angel, user_id))
            conn2.commit()
            conn2.close()
            await message.answer(
                f"✅ Дата рождения обновлена: *{text}*\n"
                f"👼 Возможный день памяти покровителя: *{angel or 'не найден'}*",
                parse_mode="Markdown",
                reply_markup=back_section("profile")
            )
            set_step(user_id, "idle")
        except ValueError:
            await message.answer("⚠️ Формат: *ДД.ММ*, например: *15.03*", parse_mode="Markdown")
        return

    # Поиск именин по имени
    if step in ("find_angel", "saint_search"):
        name_lower = text.lower().strip()
        days = SAINTS_BY_NAME.get(name_lower)
        if days:
            result = f"👼 *Дни памяти святых с именем {text.capitalize()}:*\n\n"
            for day_str, desc in days:
                result += f"📅 {day_str} — {desc}\n"
            result += "\n🙏 Для определения вашего личного дня ангела\nукажите дату рождения в профиле."
        else:
            result = (
                f"👼 Имя *{text}* не найдено в базе.\n\n"
                f"База постоянно пополняется.\n"
                f"Попробуйте церковную форму имени:\n"
                f"Юля → Иулия, Алёша → Алексий"
            )
        await message.answer(result, parse_mode="Markdown", reply_markup=back_menu())
        set_step(user_id, "idle")
        return

    # Поиск храма по городу
    if step == "find_church_city":
        city = text.strip()
        maps_url = f"https://maps.yandex.ru/?text=православный+храм+{city}"
        await message.answer(
            f"🗺️ *Православные храмы в городе {city}:*\n\n"
            f"{maps_url}",
            parse_mode="Markdown",
            reply_markup=back_menu()
        )
        set_step(user_id, "idle")
        return

    # Отзыв
    if step == "prayer_for_me_name":
        set_step(user_id, "idle")
        await message.answer("🙏 Молюсь... составляю молитву...")
        try:
            msg = await claude_messages_create(
                model="claude-sonnet-4-5",
                max_tokens=600,
                system=(
                    "Ты православный помощник. Составь пример личного молитвенного обращения для человека "
                    "на основе его имени и просьбы. Молитва должна быть тёплой, искренней, "
                    "3-5 строф. Обращайся к Господу или Богородице. Упомяни имя человека. "
                    "Заверши Аминь. Только по-русски."
                ),
                messages=[{"role": "user", "content": f"Составь молитву для: {text}"}]
            )
            prayer_text = msg.content[0].text
            await message.answer(
                f"🙏 *Пример молитвенного обращения своими словами*\n\n{prayer_text}",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🙏 Ещё молитву", callback_data="prayer_for_me")],
                    [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")],
                ])
            )
        except Exception as e:
            logging.error(f"Ошибка молитвы за меня: {e}")
            await message.answer("⚠️ Не удалось составить молитву. Попробуйте позже.", reply_markup=back_menu())
        return

    if step == "zapiska_zdravie_names":
        set_step(user_id, "idle")
        names = [n.strip() for n in text.replace("\n", ",").split(",") if n.strip()]
        if not names:
            await message.answer("⚠️ Введите хотя бы одно имя.", reply_markup=back_menu())
            return
        zapiska = "О ЗДРАВИИ\n\n"
        for n in names[:10]:
            zapiska += f"{n}\n"
        await message.answer(
            f"💛 *Ваша записка о здравии:*\n\n"
            f"```\n{zapiska}```\n\n"
            f"📋 Распечатайте или перепишите от руки.\n"
            f"Подайте в свечной лавке храма.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="✍️ Ещё записку", callback_data="make_zapiska")],
                [InlineKeyboardButton(text="📝 Как подавать записки", callback_data="sacr_zapiska")],
                [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")],
            ])
        )
        return

    if step == "zapiska_upokoenie_names":
        set_step(user_id, "idle")
        names = [n.strip() for n in text.replace("\n", ",").split(",") if n.strip()]
        if not names:
            await message.answer("⚠️ Введите хотя бы одно имя.", reply_markup=back_menu())
            return
        zapiska = "ОБ УПОКОЕНИИ\n\n"
        for n in names[:10]:
            zapiska += f"{n}\n"
        await message.answer(
            f"🕯️ *Ваша записка об упокоении:*\n\n"
            f"```\n{zapiska}```\n\n"
            f"📋 Распечатайте или перепишите от руки.\n"
            f"Подайте в свечной лавке храма.\n\n"
            f"*Сорокоуст* — закажите отдельно если усопший недавно.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="✍️ Ещё записку", callback_data="make_zapiska")],
                [InlineKeyboardButton(text="📝 Как подавать записки", callback_data="sacr_zapiska")],
                [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")],
            ])
        )
        return

    if step == "review":
        await process_new_review(message, text)
        return

    # Пожертвование — ввод суммы
    if step == "donate_amount":
        if not YOOKASSA_SHOP_ID or not YOOKASSA_SECRET:
            set_step(user_id, "idle")
            record_critical_error("donation_config_tg", "YOOKASSA_SHOP_ID/YOOKASSA_SECRET missing")
            await message.answer("⚠️ Платежи временно недоступны. Попробуйте позже.", reply_markup=back_menu())
            return
        try:
            amount = int(text.strip())
            if amount < 10:
                await message.answer(
                    "⚠️ Минимальная сумма пожертвования — 10 рублей.\n"
                    "Введите сумму цифрой:",
                    reply_markup=back_menu()
                )
                return
            payment_payload = {
                "amount": {"value": f"{amount}.00", "currency": "RUB"},
                "confirmation": {"type": "redirect", "return_url": BOT_URL},
                "capture": True,
                "description": "Пожертвование на развитие «С верой»",
                "metadata": {"user_id": str(user_id), "plan": "donation", "platform": "Telegram"},
                "receipt": {"customer": {"email": "6038484@mail.ru"}, "items": [{
                    "description": "Пожертвование на развитие «С верой»", "quantity": "1.00",
                    "amount": {"value": f"{amount}.00", "currency": "RUB"}, "vat_code": 1,
                    "payment_mode": "full_payment", "payment_subject": "another"
                }]},
            }
            payment = await asyncio.to_thread(Payment.create, payment_payload, str(uuid.uuid4()))
            set_step(user_id, "idle")
            save_donation_payment(
                payment.id, user_id, message.chat.id,
                message.from_user.username or "", message.from_user.first_name or "",
                amount, "Telegram"
            )
            await message.answer(
                f"🕯️ *Пожертвование {amount} рублей*\n\n"
                f"Нажмите кнопку для перехода к оплате 👇",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(
                        text="💳 Перейти к оплате",
                        url=payment.confirmation.confirmation_url
                    )],
                    [InlineKeyboardButton(text="◀️ Главное меню", callback_data="main_menu")],
                ])
            )
        except ValueError:
            await message.answer(
                "⚠️ Введите сумму цифрой, например: *300*",
                parse_mode="Markdown",
                reply_markup=back_menu()
            )
        except Exception as e:
            logging.error(f"Ошибка платежа пожертвования: {e}")
            await message.answer(
                "⚠️ Ошибка при создании платежа.\n"
                "Попробуйте позже или свяжитесь: @Boss023rus",
                reply_markup=back_menu()
            )
        return

    # AI вопрос
    if step.startswith("question_"):
        depth   = step.replace("question_", "")
        await message.answer("🙏 Молюсь... отвечаю...")
        answer = await ask_claude(text, depth)
        asyncio.create_task(asyncio.to_thread(sheets_update_activity, user_id))

        depth_labels = {"short": "💬 Кратко", "medium": "📖 Развёрнуто", "deep": "🙏 Глубоко"}

        if answer == "error":
            # Уведомляем админа
            try:
                await bot.send_message(OWNER_ID,
                    f"⚠️ Ошибка Claude в @Moya_Vera_bot\nПользователь: {user_id}\nВопрос: {text[:100]}")
            except Exception:
                pass
            await message.answer(
                "⚠️ Не удалось получить ответ. Попробуйте чуть позже.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🔄 Попробовать снова", callback_data="ask_question")],
                    [InlineKeyboardButton(text="📢 Сообщить о проблеме", url="https://t.me/Boss023rus")],
                    [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")],
                ])
            )
        else:
            await message.answer(
                f"{depth_labels.get(depth, '')} *Ответ:*\n\n{answer}",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="❓ Задать ещё вопрос", callback_data="ask_question")],
                    [InlineKeyboardButton(text="🏠 Главное меню",      callback_data="main_menu")],
                ])
            )
        set_step(user_id, "idle")
        return

    # Если шаг не определён — показать меню
    await message.answer("☦️ Главное меню:", reply_markup=main_menu())

# ========== MAIN ==========
async def main():
    init_db()
    asyncio.create_task(asyncio.to_thread(ensure_review_sheet_schema_tg))
    asyncio.create_task(channel_scheduler_supervisor())
    asyncio.create_task(channel_watchdog_loop())
    asyncio.create_task(angel_reminder_loop())
    asyncio.create_task(check_donation_payments_loop_tg())
    asyncio.create_task(nurture_loop_tg())
    asyncio.create_task(weekly_funnel_report_loop_tg())
    asyncio.create_task(database_backup_loop("vera_tg"))
    logging.info("Vera Telegram запущен в текстовом режиме")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
