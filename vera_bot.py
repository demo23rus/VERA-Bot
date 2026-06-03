import random

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
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
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

# ========== КОНФИГ ==========
BOT_TOKEN         = "8830150213:AAFcyR-_mnSpdWnlCngaArSKXA_bp-YLTnY"
CHANNEL_ID        = "@SvyatoyPut"

# 31 икона — ротация по числу месяца
DAILY_ICONS_TG = {
    1:  "https://upload.wikimedia.org/wikipedia/commons/thumb/3/30/Our_Lady_of_Kazan_icon.jpg/800px-Our_Lady_of_Kazan_icon.jpg",
    2:  "https://upload.wikimedia.org/wikipedia/commons/thumb/4/4b/Nicholas_icon.jpg/800px-Nicholas_icon.jpg",
    3:  "https://upload.wikimedia.org/wikipedia/commons/thumb/b/b3/Michael_icon.jpg/800px-Michael_icon.jpg",
    4:  "https://upload.wikimedia.org/wikipedia/commons/thumb/6/6e/Seraphim_of_Sarov_icon.jpg/800px-Seraphim_of_Sarov_icon.jpg",
    5:  "https://upload.wikimedia.org/wikipedia/commons/thumb/c/c7/John_the_Baptist_icon.jpg/800px-John_the_Baptist_icon.jpg",
    6:  "https://upload.wikimedia.org/wikipedia/commons/thumb/d/d6/Sergius_icon.jpg/800px-Sergius_icon.jpg",
    7:  "https://upload.wikimedia.org/wikipedia/commons/thumb/8/8c/George_icon.jpg/800px-George_icon.jpg",
    8:  "https://upload.wikimedia.org/wikipedia/commons/thumb/b/b0/Matrona_icon.jpg/800px-Matrona_icon.jpg",
    9:  "https://upload.wikimedia.org/wikipedia/commons/thumb/1/1b/Peter_and_Paul_icon.jpg/800px-Peter_and_Paul_icon.jpg",
    10: "https://upload.wikimedia.org/wikipedia/commons/thumb/5/52/Elijah_icon.jpg/800px-Elijah_icon.jpg",
    11: "https://upload.wikimedia.org/wikipedia/commons/thumb/a/a0/Demetrius_of_Thessaloniki_icon.jpg/800px-Demetrius_of_Thessaloniki_icon.jpg",
    12: "https://upload.wikimedia.org/wikipedia/commons/thumb/5/5b/Nativity_icon_13th_century_Sinai.jpg/800px-Nativity_icon_13th_century_Sinai.jpg",
    13: "https://upload.wikimedia.org/wikipedia/commons/thumb/f/f2/Iverskaya_icon.jpg/800px-Iverskaya_icon.jpg",
    14: "https://upload.wikimedia.org/wikipedia/commons/thumb/7/74/Exaltation_of_the_Cross_icon.jpg/800px-Exaltation_of_the_Cross_icon.jpg",
    15: "https://upload.wikimedia.org/wikipedia/commons/thumb/2/21/Annunciation_icon_Andrei_Rublev.jpg/800px-Annunciation_icon_Andrei_Rublev.jpg",
    16: "https://upload.wikimedia.org/wikipedia/commons/thumb/c/cf/Dormition_icon.jpg/800px-Dormition_icon.jpg",
    17: "https://upload.wikimedia.org/wikipedia/commons/thumb/e/e5/Transfiguration_by_Feofan_Grek.jpg/800px-Transfiguration_by_Feofan_Grek.jpg",
    18: "https://upload.wikimedia.org/wikipedia/commons/thumb/a/a5/Theophany_Baptism_of_Christ_Novgorod_icon_12th_century.jpg/800px-Theophany_Baptism_of_Christ_Novgorod_icon_12th_century.jpg",
    19: "https://upload.wikimedia.org/wikipedia/commons/thumb/2/2b/Pokrov_icon.jpg/800px-Pokrov_icon.jpg",
    20: "https://upload.wikimedia.org/wikipedia/commons/thumb/4/4b/Nicholas_icon.jpg/800px-Nicholas_icon.jpg",
    21: "https://upload.wikimedia.org/wikipedia/commons/thumb/a/a9/Barbara_icon.jpg/800px-Barbara_icon.jpg",
    22: "https://upload.wikimedia.org/wikipedia/commons/thumb/5/59/Luke_icon.jpg/800px-Luke_icon.jpg",
    23: "https://upload.wikimedia.org/wikipedia/commons/thumb/3/3e/Nativity_of_Mary_icon.jpg/800px-Nativity_of_Mary_icon.jpg",
    24: "https://upload.wikimedia.org/wikipedia/commons/thumb/9/9f/Meeting_of_the_Lord_icon.jpg/800px-Meeting_of_the_Lord_icon.jpg",
    25: "https://upload.wikimedia.org/wikipedia/commons/thumb/f/f4/Faith_Hope_Love_icon.jpg/800px-Faith_Hope_Love_icon.jpg",
    26: "https://upload.wikimedia.org/wikipedia/commons/thumb/b/b3/Michael_icon.jpg/800px-Michael_icon.jpg",
    27: "https://upload.wikimedia.org/wikipedia/commons/thumb/3/30/Our_Lady_of_Kazan_icon.jpg/800px-Our_Lady_of_Kazan_icon.jpg",
    28: "https://upload.wikimedia.org/wikipedia/commons/thumb/6/6e/Seraphim_of_Sarov_icon.jpg/800px-Seraphim_of_Sarov_icon.jpg",
    29: "https://upload.wikimedia.org/wikipedia/commons/thumb/d/d6/Sergius_icon.jpg/800px-Sergius_icon.jpg",
    30: "https://upload.wikimedia.org/wikipedia/commons/thumb/8/8c/George_icon.jpg/800px-George_icon.jpg",
    31: "https://upload.wikimedia.org/wikipedia/commons/thumb/c/c7/John_the_Baptist_icon.jpg/800px-John_the_Baptist_icon.jpg",
}

FEAST_ICONS_TG = {
    "07.01": "https://upload.wikimedia.org/wikipedia/commons/thumb/5/5b/Nativity_icon_13th_century_Sinai.jpg/800px-Nativity_icon_13th_century_Sinai.jpg",
    "19.01": "https://upload.wikimedia.org/wikipedia/commons/thumb/a/a5/Theophany_Baptism_of_Christ_Novgorod_icon_12th_century.jpg/800px-Theophany_Baptism_of_Christ_Novgorod_icon_12th_century.jpg",
    "15.02": "https://upload.wikimedia.org/wikipedia/commons/thumb/9/9f/Meeting_of_the_Lord_icon.jpg/800px-Meeting_of_the_Lord_icon.jpg",
    "07.04": "https://upload.wikimedia.org/wikipedia/commons/thumb/2/21/Annunciation_icon_Andrei_Rublev.jpg/800px-Annunciation_icon_Andrei_Rublev.jpg",
    "19.08": "https://upload.wikimedia.org/wikipedia/commons/thumb/e/e5/Transfiguration_by_Feofan_Grek.jpg/800px-Transfiguration_by_Feofan_Grek.jpg",
    "28.08": "https://upload.wikimedia.org/wikipedia/commons/thumb/c/cf/Dormition_icon.jpg/800px-Dormition_icon.jpg",
    "14.10": "https://upload.wikimedia.org/wikipedia/commons/thumb/2/2b/Pokrov_icon.jpg/800px-Pokrov_icon.jpg",
    "19.12": "https://upload.wikimedia.org/wikipedia/commons/thumb/4/4b/Nicholas_icon.jpg/800px-Nicholas_icon.jpg",
    "22.05": "https://upload.wikimedia.org/wikipedia/commons/thumb/4/4b/Nicholas_icon.jpg/800px-Nicholas_icon.jpg",
    "06.05": "https://upload.wikimedia.org/wikipedia/commons/thumb/8/8c/George_icon.jpg/800px-George_icon.jpg",
    "02.05": "https://upload.wikimedia.org/wikipedia/commons/thumb/b/b0/Matrona_icon.jpg/800px-Matrona_icon.jpg",
    "08.10": "https://upload.wikimedia.org/wikipedia/commons/thumb/d/d6/Sergius_icon.jpg/800px-Sergius_icon.jpg",
}

def get_channel_icon() -> str:
    """Возвращает икону для поста канала — праздничную или по числу месяца"""
    today_key = datetime.now().strftime("%d.%m")
    day_num = datetime.now().day
    return FEAST_ICONS_TG.get(today_key) or DAILY_ICONS_TG.get(day_num, DAILY_ICONS_TG[1])

async def send_channel_post(text: str, with_photo: bool = True):
    """Отправляет пост в канал с фото или без"""
    if with_photo:
        icon_url = get_channel_icon()
        try:
            await bot.send_photo(
                CHANNEL_ID,
                photo=icon_url,
                caption=text,
                parse_mode="Markdown"
            )
            return
        except Exception as e:
            logging.error(f"Ошибка отправки фото в канал: {e}")
    # Fallback — без фото
    await bot.send_message(CHANNEL_ID, text, parse_mode="Markdown")
OPENAI_KEY        = _env.get("OPENAI_KEY") or os.environ.get("OPENAI_KEY", "")
ANTHROPIC_KEY     = _env.get("ANTHROPIC_KEY") or os.environ.get("ANTHROPIC_KEY", "")
OWNER_ID          = 549639607
CREDENTIALS_FILE  = "/root/google_credentials.json"
SPREADSHEET_ID    = "1PE7CaFuWOe_eygQqIoMAmUdJBtATbIaNfZR4cvarPCA"

logging.basicConfig(level=logging.INFO)
logging.info(f"OPENAI_KEY loaded: {OPENAI_KEY[:15] if OPENAI_KEY else 'EMPTY'}...")
logging.info(f"ANTHROPIC_KEY loaded: {ANTHROPIC_KEY[:15] if ANTHROPIC_KEY else 'EMPTY'}...")

# Лимиты
FREE_AI_REQUESTS  = 10
FREE_PHOTO        = 3

# ЮКасса
YOOKASSA_SHOP_ID  = "1363324"
YOOKASSA_SECRET   = "live_-RKE9nsi8wZiM-5f00z78E84OYSi3M0Dj9w_-pE0Mvw"
Configuration.account_id = YOOKASSA_SHOP_ID
Configuration.secret_key  = YOOKASSA_SECRET

# ========== КЛИЕНТЫ AI ==========
openai_client = AsyncOpenAI(api_key=OPENAI_KEY)
claude_client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)

# ========== БОТ ==========
bot = Bot(token=BOT_TOKEN)
dp  = Dispatcher(storage=MemoryStorage())

# ========== БАЗА ДАННЫХ ==========
DB_PATH = "/root/vera.db"

def init_db():
    conn = sqlite3.connect(DB_PATH)
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
        notifications INTEGER DEFAULT 1
    )""")
    # Добавляем колонку если её ещё нет (для существующих баз)
    try:
        c.execute("ALTER TABLE users ADD COLUMN notifications INTEGER DEFAULT 1")
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
    c.execute("""CREATE TABLE IF NOT EXISTS favorites (
        id      INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        title   TEXT,
        content TEXT,
        saved_at TEXT
    )""")
    conn.commit()
    conn.close()

def get_user(user_id, username="", first_name=""):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO users (user_id, username, first_name, registered_at) VALUES (?,?,?,?)",
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
            "notifications": row[10] if len(row) > 10 else 1
        }
    return {}

def set_step(user_id, step):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("UPDATE users SET step=? WHERE user_id=?", (step, user_id))
    conn.commit()
    conn.close()

def set_onboarded(user_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("UPDATE users SET onboarded=1 WHERE user_id=?", (user_id,))
    conn.commit()
    conn.close()

def save_profile(user_id, church_name, birth_date, angel_day):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("UPDATE users SET church_name=?, birth_date=?, angel_day=?, onboarded=1 WHERE user_id=?",
              (church_name, birth_date, angel_day, user_id))
    conn.commit()
    conn.close()

def get_subscription(user_id):
    conn = sqlite3.connect(DB_PATH)
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
    conn = sqlite3.connect(DB_PATH)
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
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(f"UPDATE limits SET {field}={field}+1 WHERE user_id=?", (user_id,))
    conn.commit()
    conn.close()

def save_favorite(user_id, title, content):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT INTO favorites (user_id, title, content, saved_at) VALUES (?,?,?,?)",
              (user_id, title, content, datetime.now().isoformat()))
    conn.commit()
    conn.close()

def get_favorites(user_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT id, title, saved_at FROM favorites WHERE user_id=? ORDER BY saved_at DESC LIMIT 20", (user_id,))
    rows = c.fetchall()
    conn.close()
    return rows

# ========== GOOGLE SHEETS ==========
def get_sheet():
    try:
        scopes = ["https://www.googleapis.com/auth/spreadsheets"]
        creds  = Credentials.from_service_account_file(CREDENTIALS_FILE, scopes=scopes)
        client = gspread.authorize(creds)
        sp     = client.open_by_key(SPREADSHEET_ID)
        try:
            sheet = sp.worksheet("ВераБот")
        except Exception:
            sheet = sp.add_worksheet(title="ВераБот", rows=1000, cols=10)
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
    "01.01": "Обрезание Господне, память свт. Василия Великого",
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
    "Великий пост": "48 дней перед Пасхой. Самый строгий пост. Исключаются мясо, рыба, молочные продукты и яйца. В будни — сухоядение.",
    "Петров пост": "С понедельника после Недели всех святых до 12 июля. Можно рыбу в субботу и воскресенье.",
    "Успенский пост": "14–27 августа. Строгий пост, рыба только 19 августа (Преображение).",
    "Рождественский пост": "28 ноября – 6 января. Умеренный пост, рыба разрешена в субботу и воскресенье.",
    "Среда и пятница": "Еженедельный пост в память предательства и распятия Христа.",
}

# Святые по именам (для дня ангела)
SAINTS_BY_NAME = {
    "александр": [("06.06","мч. Александра"), ("12.06","блгв. кн. Александра Невского"), ("12.09","блгв. кн. Александра Невского"), ("23.11","блгв. кн. Александра Невского")],
    "алексей":   [("30.03","прп. Алексия, человека Божия"), ("25.04","сщмч. Алексия"), ("20.09","блгв. кн. Алексия")],
    "анастасия": [("04.01","мц. Анастасии Римляныни"), ("22.12","вмц. Анастасии Узорешительницы")],
    "андрей":    [("13.12","ап. Андрея Первозванного")],
    "анна":      [("03.02","прп. Анны"), ("22.07","равноап. Марии Магдалины"), ("07.08","прп. Анны")],
    "борис":     [("06.08","блгв. кн. Бориса и Глеба"), ("24.07","блгв. кн. Бориса")],
    "василий":   [("14.01","свт. Василия Великого"), ("13.03","мч. Василия"), ("04.04","прп. Василия")],
    "вера":      [("30.09","мц. Веры, Надежды, Любови и матери их Софии")],
    "виктор":    [("11.11","мч. Виктора"), ("05.03","мч. Виктора")],
    "владимир":  [("28.07","равноап. кн. Владимира")],
    "галина":    [("29.03","мц. Галины")],
    "георгий":   [("06.05","вмч. Георгия Победоносца"), ("26.11","освящение храма вмч. Георгия")],
    "дарья":     [("01.04","мц. Дарии")],
    "дмитрий":   [("08.11","вмч. Димитрия Солунского"), ("01.06","блгв. кн. Димитрия Донского")],
    "дима":      [("08.11","вмч. Димитрия Солунского"), ("01.06","блгв. кн. Димитрия Донского")],
    "екатерина": [("07.12","вмц. Екатерины")],
    "елена":     [("03.06","равноап. царицы Елены"), ("24.07","равноап. Елены")],
    "иван":      [("20.01","Собор Иоанна Предтечи"), ("07.07","Рождество Иоанна Предтечи"), ("11.09","Усекновение главы Иоанна Предтечи")],
    "иоанн":     [("20.01","Собор Иоанна Предтечи"), ("07.07","Рождество Иоанна Предтечи"), ("11.09","Усекновение главы Иоанна Предтечи")],
    "ирина":     [("29.04","мц. Ирины"), ("18.05","мц. Ирины")],
    "кирилл":    [("27.02","равноап. Кирилла, учителя Словенского")],
    "константин": [("03.06","равноап. царя Константина")],
    "ксения":    [("06.02","блж. Ксении Петербургской"), ("24.01","мц. Ксении")],
    "лариса":    [("08.04","мц. Ларисы")],
    "людмила":   [("29.09","мц. кн. Людмилы Чешской")],
    "маргарита": [("30.07","вмц. Марины (Маргариты)")],
    "мария":     [("22.07","равноап. Марии Магдалины"), ("17.09","мц. Марии"), ("26.01","прп. Марии")],
    "марина":    [("30.07","вмц. Марины")],
    "матрона":   [("02.05","блж. Матроны Московской"), ("09.08","мц. Матроны")],
    "михаил":    [("21.11","Собор Архистратига Михаила"), ("12.07","ап. Михаила")],
    "надежда":   [("30.09","мц. Надежды")],
    "наталья":   [("08.09","мц. Наталии"), ("26.08","мц. Наталии")],
    "николай":   [("22.05","свт. Николая, архиеп. Мирликийского"), ("19.12","свт. Николая Чудотворца")],
    "оксана":    [("06.10","прп. Ксанфиппы"), ("24.01","мц. Ксении")],
    "ольга":     [("24.07","равноап. кн. Ольги")],
    "павел":     [("12.07","ап. Петра и Павла"), ("03.02","прп. Павла")],
    "пётр":      [("12.07","ап. Петра и Павла"), ("04.07","блгв. кн. Петра")],
    "петр":      [("12.07","ап. Петра и Павла"), ("04.07","блгв. кн. Петра")],
    "светлана":  [("26.02","мц. Фотины (Светланы)")],
    "сергей":    [("08.10","прп. Сергия Радонежского"), ("20.09","мч. Сергия")],
    "сергий":    [("08.10","прп. Сергия Радонежского")],
    "софия":     [("30.09","мц. Софии"), ("17.09","мц. Веры, Надежды, Любови и матери их Софии")],
    "татьяна":   [("25.01","мц. Татианы")],
    "тимур":     [("02.06","прп. Тимофея")],
    "юлия":      [("29.07","мц. Иулии"), ("16.04","мц. Иулии")],
    "абрам":     [("22.10","прп. Авраамия Ростовского")],
    "авраам":    [("22.10","прп. Авраамия Ростовского"), ("09.10","прп. Авраамия Затворника")],
    "агафья":    [("18.02","мц. Агафии Панормской")],
    "агния":     [("21.01","мц. Агнии Римской")],
    "адриан":    [("26.08","мч. Адриана и Наталии")],
    "алла":      [("26.03","мц. Аллы Готфской")],
    "амвросий":  [("20.12","свт. Амвросия Медиоланского"), ("10.10","прп. Амвросия Оптинского")],
    "анатолий":  [("23.07","прп. Анатолия Оптинского"), ("15.08","мч. Анатолия")],
    "антон":     [("17.01","прп. Антония Великого"), ("23.07","прп. Антония Печерского")],
    "антонина":  [("01.03","мц. Антонины"), ("10.06","мц. Антонины")],
    "антоний":   [("17.01","прп. Антония Великого"), ("23.07","прп. Антония Печерского")],
    "аркадий":   [("26.02","прп. Аркадия Новоторжского")],
    "арсений":   [("08.05","свт. Арсения Великого"), ("24.07","прп. Арсения Коневского")],
    "артём":     [("20.10","ап. Артемы"), ("02.11","мч. Артемия")],
    "артемий":   [("02.11","мч. Артемия Антиохийского")],
    "вадим":     [("22.04","прмч. Вадима Персидского")],
    "валентин":  [("12.08","мч. Валентина"), ("19.07","мч. Валентина Доростольского")],
    "валентина": [("10.02","мц. Валентины"), ("07.08","мц. Валентины")],
    "валерий":   [("07.03","мч. Валерия"), ("20.11","мч. Валерия")],
    "валерия":   [("07.06","мц. Валерии")],
    "варвара":   [("17.12","вмц. Варвары Илиопольской")],
    "варлаам":   [("19.11","прп. Варлаама Хутынского")],
    "василиса":  [("15.01","мц. Василисы"), ("04.04","мц. Василисы")],
    "вениамин":  [("13.08","сщмч. Вениамина Петроградского")],
    "виктория":  [("23.12","мц. Виктории"), ("11.11","мц. Виктории")],
    "виталий":   [("04.05","мч. Виталия Медиоланского")],
    "вячеслав":  [("04.03","блгв. кн. Вячеслава Чешского")],
    "гавриил":   [("26.07","арх. Гавриила"), ("08.04","арх. Гавриила")],
    "геннадий":  [("17.12","свт. Геннадия Новгородского")],
    "герасим":   [("17.03","прп. Герасима Иорданского")],
    "глеб":      [("06.08","блгв. кн. Бориса и Глеба"), ("05.09","блгв. кн. Глеба")],
    "григорий":  [("12.01","свт. Григория Нисского"), ("25.01","свт. Григория Богослова")],
    "давид":     [("01.03","прп. Давида"), ("06.03","прп. Давида Солунского")],
    "даниил":    [("17.12","прп. Даниила Столпника"), ("23.12","блгв. кн. Даниила Московского")],
    "денис":     [("16.10","сщмч. Дионисия Ареопагита")],
    "дионисий":  [("16.10","сщмч. Дионисия Ареопагита")],
    "домна":     [("14.01","мц. Домны Никомидийской")],
    "евгений":   [("26.12","мч. Евгения"), ("20.11","мч. Евгения Мелитинского")],
    "евгения":   [("24.12","прмц. Евгении")],
    "евдокия":   [("14.03","прмц. Евдокии"), ("04.08","прав. Евдокии")],
    "елизавета": [("05.09","прмц. Елисаветы Феодоровны"), ("18.09","прмц. Елисаветы")],
    "ефим":      [("20.01","прп. Евфимия Великого")],
    "ефрем":     [("10.02","прп. Ефрема Сирина")],
    "зинаида":   [("23.10","мц. Зинаиды")],
    "зиновий":   [("13.11","мч. Зиновия и Зиновии")],
    "зоя":       [("13.02","мц. Зои Вифлеемской"), ("02.05","мц. Зои")],
    "илья":      [("02.08","прор. Илии Фесвитянина")],
    "илия":      [("02.08","прор. Илии Фесвитянина")],
    "иннокентий":[("26.11","свт. Иннокентия Иркутского"), ("06.10","свт. Иннокентия Московского")],
    "иосиф":     [("19.09","прав. Иосифа Прекрасного"), ("11.04","прп. Иосифа Волоцкого")],
    "капитолина": [("27.10","мц. Капитолины")],
    "клавдия":   [("20.03","мц. Клавдии")],
    "климент":   [("25.11","сщмч. Климента Римского")],
    "кристина":  [("24.07","вмц. Христины")],
    "кузьма":    [("14.07","бессрр. Космы и Дамиана"), ("14.11","бессрр. Космы и Дамиана")],
    "лев":       [("05.03","свт. Льва Катанского"), ("18.02","свт. Льва Великого")],
    "леонид":    [("16.04","мч. Леонида")],
    "лидия":     [("05.04","мц. Лидии")],
    "лука":      [("31.10","ап. Луки"), ("11.06","свт. Луки Крымского")],
    "любовь":    [("30.09","мц. Веры, Надежды, Любови")],
    "макар":     [("19.01","прп. Макария Великого")],
    "макарий":   [("19.01","прп. Макария Великого")],
    "максим":    [("13.08","прп. Максима Исповедника"), ("11.11","блж. Максима Московского")],
    "марк":      [("25.04","ап. Марка")],
    "марфа":     [("04.07","прп. Марфы")],
    "мефодий":   [("11.05","равноап. Мефодия, учителя Словенского")],
    "митрофан":  [("23.11","свт. Митрофана Воронежского")],
    "моисей":    [("04.09","прп. Моисея Угрина")],
    "никита":    [("15.09","вмч. Никиты Готфского")],
    "нина":      [("27.01","равноап. Нины, просветительницы Грузии")],
    "нонна":     [("05.08","прав. Нонны")],
    "олег":      [("03.10","блгв. кн. Олега Брянского")],
    "платон":    [("18.11","мч. Платона Анкирского")],
    "прохор":    [("09.04","прп. Прохора Лебедника"), ("28.01","прп. Прохора Печерского")],
    "раиса":     [("05.09","мц. Раисы Александрийской")],
    "роман":     [("01.10","прп. Романа Сладкопевца"), ("08.08","мч. Романа")],
    "семён":     [("03.02","прп. Симеона Богоприимца"), ("14.09","прп. Симеона Столпника")],
    "серафима":  [("29.07","прмц. Серафимы")],
    "степан":    [("09.01","архидиак. Стефана первомученика")],
    "стефан":    [("09.01","архидиак. Стефана первомученика")],
    "тамара":    [("01.05","блгв. царицы Тамары Грузинской")],
    "тимофей":   [("04.02","ап. Тимофея")],
    "тихон":     [("09.10","свт. Тихона Задонского"), ("29.06","свт. Тихона Амафунтского")],
    "трофим":    [("19.09","мч. Трофима")],
    "ульяна":    [("15.01","мц. Иулиании Никомидийской")],
    "федор":     [("08.03","вмч. Феодора Тирона")],
    "фёдор":     [("08.03","вмч. Феодора Тирона")],
    "феодор":    [("08.03","вмч. Феодора Тирона"), ("09.06","прп. Феодора Освященного")],
    "феодосий":  [("11.01","прп. Феодосия Великого"), ("03.05","прп. Феодосия Печерского")],
    "филипп":    [("27.11","ап. Филиппа"), ("22.01","свт. Филиппа Московского")],
    "фома":      [("19.10","ап. Фомы")],
    "харитина":  [("05.10","мц. Харитины")],
    "христина":  [("24.07","вмц. Христины Тирской")],
    "яков":      [("05.11","ап. Иакова Зеведеева"), ("13.01","прп. Иакова Постника")],
    "яна":       [("24.06","мц. Иоанны")],
    "яна":       [("20.01","Собор Иоанна Предтечи")],
}

def find_angel_day(name: str, birth_date_str: str) -> str:
    """Находит ближайший день ангела после дня рождения"""
    name_lower = name.lower().strip()
    days = SAINTS_BY_NAME.get(name_lower)
    if not days:
        return ""
    try:
        birth = datetime.strptime(birth_date_str, "%d.%m").replace(year=2000)
        best  = None
        for day_str, saint in days:
            d = datetime.strptime(day_str, "%d.%m").replace(year=2000)
            if d >= birth:
                if best is None or d < best[0]:
                    best = (d, day_str, saint)
        if not best:
            # Если все дни раньше — берём первый в следующем году
            d, saint = days[0][0], days[0][1]
            return f"{d} ({saint})"
        return f"{best[1]} ({best[2]})"
    except Exception:
        return ""

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
    """Записывает пожертвование в лист Пожертвования"""
    try:
        scopes = ["https://www.googleapis.com/auth/spreadsheets"]
        creds  = Credentials.from_service_account_file(CREDENTIALS_FILE, scopes=scopes)
        client = gspread.authorize(creds)
        sp     = client.open_by_key(SPREADSHEET_ID)
        try:
            sheet = sp.worksheet("Пожертвования")
        except Exception:
            sheet = sp.add_worksheet(title="Пожертвования", rows=2000, cols=6)
            sheet.insert_row(["ID","Username","Имя","Сумма (руб)","Дата","Источник"], 1)
        sheet.append_row([
            str(user_id),
            f"@{username}" if username else "—",
            first_name or "—",
            str(amount),
            datetime.now().strftime("%d.%m.%Y %H:%M"),
            "Telegram"
        ])
        # Обновляем счётчик пожертвований в листе ВераБот
        try:
            main_sheet = sp.worksheet("ВераБот")
            col = main_sheet.col_values(1)
            if str(user_id) in col:
                row = col.index(str(user_id)) + 1
                val = main_sheet.cell(row, 12).value or "0"
                main_sheet.update_cell(row, 12, str(int(val) + 1))
        except Exception:
            pass
    except Exception as e:
        logging.error(f"Sheets add_donation: {e}")

def add_review_to_sheet(user_id, username, first_name, text):
    try:
        scopes = ["https://www.googleapis.com/auth/spreadsheets"]
        creds  = Credentials.from_service_account_file(CREDENTIALS_FILE, scopes=scopes)
        client = gspread.authorize(creds)
        sp     = client.open_by_key(SPREADSHEET_ID)
        # Записываем в лист отзывов
        try:
            sheet = sp.worksheet("Отзывы ВераБот")
        except Exception:
            sheet = sp.add_worksheet(title="Отзывы ВераБот", rows=1000, cols=6)
            sheet.insert_row(["ID","Username","Имя","Дата","Тип","Отзыв"], 1)
        sheet.append_row([
            str(user_id),
            f"@{username}" if username else "—",
            first_name or "—",
            datetime.now().strftime("%d.%m.%Y %H:%M"),
            "Отзыв/пожелание",
            text
        ])
        # Обновляем счётчик отзывов в листе ВераБот
        try:
            main_sheet = sp.worksheet("ВераБот")
            col = main_sheet.col_values(1)
            if str(user_id) in col:
                row = col.index(str(user_id)) + 1
                val = main_sheet.cell(row, 11).value or "0"
                main_sheet.update_cell(row, 11, str(int(val) + 1))
        except Exception:
            pass
    except Exception as e:
        logging.error(f"Ошибка записи отзыва: {e}")

# ========== МЕНЮ ==========
def main_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
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
        [InlineKeyboardButton(text="📖 Евангелие дня", callback_data="daily_gospel")],
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
            text=f"👼 День ангела: {angel}",
            callback_data="profile_angel_info"
        )],
        [InlineKeyboardButton(
            text=f"🔔 Напомнить за {remind} дн.",
            callback_data="profile_remind"
        )],
        [InlineKeyboardButton(text="⭐ Избранные молитвы",             callback_data="favorites")],
        [InlineKeyboardButton(text="🙏 Молитва небесному покровителю", callback_data="profile_patron_prayer")],
        [InlineKeyboardButton(
            text="🔔 Утренние уведомления: ВКЛ" if user.get("notifications", 1) else "🔕 Утренние уведомления: ВЫКЛ",
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
        [InlineKeyboardButton(text="🌟 Оформить Премиум — 149 руб/мес", callback_data="buy_premium")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="profile")],
    ])

# ========== AI ФУНКЦИИ ==========
async def ask_claude(question: str, depth: str) -> str:
    depth_prompts = {
        "short":  "Отвечай кратко — 2-3 предложения. Простой язык.",
        "medium": "Отвечай развёрнуто — с цитатами из Священного Писания и учением Церкви.",
        "deep":   "Отвечай глубоко и мудро — как опытный православный священник. Приведи цитаты из Писания и святых отцов. Заверши молитвенным пожеланием.",
    }
    greetings = [
        "Душа моя", "Чадо", "Возлюбленное чадо", "Дорогой брат во Христе",
        "Дорогая сестра во Христе", "Дорогой друг", "Брате", "Сестра",
        "Возлюбленный во Христе", "Дорогой мой"
    ]
    greeting = random.choice(greetings)
    system = (
        "Ты православный священник с многолетним опытом пастырского служения. "
        "Отвечаешь на вопросы о вере тепло, по-отечески, как настоящий батюшка на исповеди или после службы. "
        "Говоришь просто и сердечно — не сухо и не академично. "
        f"ОБЯЗАТЕЛЬНО начинай каждый ответ с обращения '{greeting},' — это первое слово ответа. "
        "Опираешься на Священное Писание, Предание, слова святых отцов — объясняешь живым языком. "
        "Никогда не осуждаешь, всегда утешаешь и ободряешь. "
        "В конце ответа — краткое молитвенное пожелание или благословение. "
        "Отвечаешь только по-русски. "
        f"{depth_prompts.get(depth, depth_prompts['medium'])}"
    )
    try:
        message = claude_client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=1000,
            system=system,
            messages=[{"role": "user", "content": question}]
        )
        return message.content[0].text
    except Exception as e:
        logging.error(f"Claude ошибка: {e}")
        return "Произошла ошибка при обращении к AI. Попробуйте позже."

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
        import base64
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
    with open(file_path, "rb") as f:
        response = await openai_client.audio.transcriptions.create(
            model="whisper-1",
            file=f,
            language="ru"
        )
    return response.text

# ========== КАНАЛ — АВТОПОСТИНГ ==========
async def get_daily_saint() -> str:
    today = date_ru("short")
    feast = get_todays_feast()
    saints = get_todays_saints()

    text = f"☦️ *{today}*\n\n"
    if feast:
        text += f"🎉 *{feast}*\n\n"

    if saints:
        text += "👼 *Именинники сегодня:*\n"
        for name, desc in saints[:5]:
            text += f"— {name} ({desc})\n"
        text += "\n"

    text += "🙏 Поздравляем всех именинников!\n"
    text += "Пусть святые покровители хранят вас.\n\n"
    text += f"📖 Подробнее — в боте @Moya\\_Vera\\_bot"
    return text

async def get_daily_quote() -> str:
    quotes = [
        ("Нет ничего невозможного для молящегося.", "Преп. Серафим Саровский"),
        ("Стяжи дух мирен и тысячи спасутся вокруг тебя.", "Преп. Серафим Саровский"),
        ("Не осуждай никого — не знаешь, как сам окончишь жизнь.", "Авва Дорофей"),
        ("Молитва — это разговор с Богом. Относись к ней как к важнейшему делу жизни.", "Свт. Феофан Затворник"),
        ("Смирение — мать всех добродетелей.", "Свт. Иоанн Златоуст"),
        ("Бог гордым противится, а смиренным даёт благодать.", "1 Пет. 5:5"),
        ("Просите — и дано будет вам; ищите — и найдёте; стучите — и отворят вам.", "Мф. 7:7"),
        ("Любовь долготерпит, милосердствует, любовь не завидует.", "1 Кор. 13:4"),
        ("Всё могу в укрепляющем меня Иисусе Христе.", "Флп. 4:13"),
        ("Господь — Пастырь мой; я ни в чём не буду нуждаться.", "Пс. 22:1"),
        ("Где нет смирения — там нет и добродетели.", "Прп. Амвросий Оптинский"),
        ("Радость — признак духовного здоровья.", "Прп. Паисий Святогорец"),
        ("Терпение — корень всех добродетелей.", "Прп. Иоанн Лествичник"),
    ]
    import random
    text_q, author = random.choice(quotes)
    today_str = date_ru("short")
    return (
        f"✨ *СЛОВО НА ДЕНЬ • {today_str}*\n\n"
        f"«{text_q}»\n\n"
        f"— *{author}*\n\n"
        f"─────────────────\n"
        f"☦️ Молитвы и тексты → @Moya\\_Vera\\_bot"
    )

def get_fast_today() -> str:
    """Возвращает информацию о посте сегодня"""
    from datetime import date as _date
    today = _date.today()
    m, d, w = today.month, today.day, today.weekday()

    # Великий пост 2026: 16 февраля — 4 апреля
    if (m == 2 and d >= 16) or m == 3 or (m == 4 and d <= 4):
        if w not in (5, 6):
            return "*\U0001f56f\ufe0f Великий пост*\n\nСегодня постный день.\n\n❌ Мясо, рыба, молочное, яйца\n✅ Хлеб, овощи, фрукты, бобовые, грибы\n\nВеликий пост — время молитвы и покаяния."
        return "*\U0001f56f\ufe0f Великий пост*\n\nСуббота/воскресенье — пост послабляется.\n\n✅ Рыба, растительное масло\n❌ Мясо, молочное, яйца"

    # Петров пост 2026: 15 июня — 12 июля
    if (m == 6 and d >= 15) or (m == 7 and d <= 12):
        if w in (2, 4):
            return "*\U0001f56f\ufe0f Петров пост*\n\nСреда/пятница — строгий день.\n\n❌ Мясо, рыба, молочное\n✅ Растительная пища"
        if w in (5, 6):
            return "*\U0001f56f\ufe0f Петров пост*\n\nСуббота/воскресенье.\n\n✅ Рыба, вино умеренно\n❌ Мясо, молочное, яйца"
        return "*\U0001f56f\ufe0f Петров пост*\n\nПн/вт/чт.\n\n✅ Рыба, растительное масло\n❌ Мясо, молочное, яйца"

    # Успенский пост: 14–27 августа
    if m == 8 and 14 <= d <= 27:
        if d == 19:
            return "*\U0001f56f\ufe0f Успенский пост*\n\nСегодня Преображение Господне — разрешается рыба!\n❌ Мясо, молочное, яйца"
        return "*\U0001f56f\ufe0f Успенский пост*\n\n❌ Мясо, рыба, молочное, яйца\n✅ Растительная пища\n\nПост в честь Успения Богородицы."

    # Рождественский пост: 28 ноября — 6 января
    if (m == 11 and d >= 28) or m == 12 or (m == 1 and d <= 6):
        if w in (5, 6):
            return "*\U0001f56f\ufe0f Рождественский пост*\n\nСуббота/воскресенье.\n\n✅ Рыба, вино умеренно\n❌ Мясо, молочное, яйца"
        return "*\U0001f56f\ufe0f Рождественский пост*\n\n❌ Мясо, молочное, яйца\n✅ Рыба (пн, вт, чт), растительное масло"

    # Среда и пятница
    if w == 2:
        return "*🥗 Среда — постный день*\n\nВ память о предательстве Иуды.\n\n❌ Мясо, молочное, яйца\n✅ Рыба, растительная пища"
    if w == 4:
        return "*🥗 Пятница — постный день*\n\nВ память о Распятии Господа.\n\n❌ Мясо, молочное, яйца\n✅ Рыба, растительная пища"

    return "*☀️ Сегодня не постный день*\n\nМногодневных постов сейчас нет. Сегодня не среда и не пятница.\n\nБлижайшие постные дни:\n🥗 Среда и пятница — еженедельно"


async def get_daily_gospel() -> str:
    today = date_ru("short")
    try:
        message = claude_client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=500,
            system=(
                "Ты православный помощник. Дай евангельское чтение дня "
                "с коротким толкованием (3-4 предложения). "
                "Формат: сначала отрывок из Евангелия (2-3 стиха с указанием источника), "
                "потом краткое толкование простым языком. "
                "Отвечай по-русски. Без лишних вступлений."
            ),
            messages=[{"role": "user", "content": f"Дай евангельское чтение на {today}"}]
        )
        gospel_text = message.content[0].text
        return (
            f"📖 *ЕВАНГЕЛИЕ ДНЯ • {today}*\n\n"
            f"{gospel_text}\n\n"
            f"─────────────────\n"
            f"☦️ Читать Библию → @Moya\\_Vera\\_bot"
        )
    except Exception as e:
        logging.error(f"Ошибка Евангелия дня: {e}")
        # Запасной вариант — цитата из Евангелия
        quotes = [
            ("Просите — и дано будет вам; ищите — и найдёте; стучите — и отворят вам.", "Мф. 7:7"),
            ("Я есмь путь и истина и жизнь.", "Ин. 14:6"),
            ("Бог есть любовь.", "1 Ин. 4:8"),
            ("Всё могу в укрепляющем меня Иисусе Христе.", "Флп. 4:13"),
            ("Господь — Пастырь мой; я ни в чём не буду нуждаться.", "Пс. 22:1"),
        ]
        import random
        text_q, ref = random.choice(quotes)
        return (
            f"📖 *ЕВАНГЕЛИЕ ДНЯ • {today}*\n\n"
            f"«{text_q}»\n\n"
            f"— {ref}\n\n"
            f"─────────────────\n"
            f"☦️ Читать Библию → @Moya\\_Vera\\_bot"
        )

async def channel_post_loop():
    """Автопостинг в канал по расписанию"""
    await asyncio.sleep(10)
    while True:
        now = datetime.now()
        hour, minute = now.hour, now.minute
        today_str = date_ru("short")

        try:
            # 07:00 — Утренняя молитва
            if hour == 7 and minute == 0:
                prayer = PRAYERS["morning_ru"]
                await send_channel_post(
                    f"🌅 *Доброе утро, {today_str}!*\n\n"
                    f"☦️ *Утренняя молитва*\n\n"
                    f"{prayer['text']}\n\n"
                    f"─────────────────\n"
                    f"🙏 Все молитвы → @Moya\_Vera\_bot"
                )

            # 08:00 — Святой дня + краткое житие
            elif hour == 8 and minute == 0:
                text = await get_daily_saint()
                await send_channel_post(text)

            # 09:00 — Именинники (только если есть)
            elif hour == 9 and minute == 0:
                saints = get_todays_saints()
                if saints:
                    text = f"👼 *Именинники {today_str}*\n\n"
                    for name, desc in saints:
                        text += f"✨ *{name}* — {desc}\n"
                    text += f"\n🎉 Поздравьте своих близких!\n\n"
                    text += f"─────────────────\n"
                    text += f"☦️ День ангела → @Moya\\_Vera\\_bot"
                    await send_channel_post(text)

            # 10:00 — Евангелие дня
            elif hour == 10 and minute == 0:
                text = await get_daily_gospel()
                await send_channel_post(text)

            # 12:00 — Цитата святых отцов
            elif hour == 12 and minute == 0:
                text = await get_daily_quote()
                await send_channel_post(text)

            # 20:00 — Вечерняя молитва
            elif hour == 20 and minute == 0:
                prayer = PRAYERS["evening_ru"]
                await send_channel_post(
                    f"🌙 *Добрый вечер, {today_str}!*\n\n"
                    f"☦️ *Вечерняя молитва*\n\n"
                    f"{prayer['text']}\n\n"
                    f"─────────────────\n"
                    f"🙏 Молитвослов → @Moya\_Vera\_bot"
                )

        except Exception as e:
            logging.error(f"Ошибка автопостинга: {e}")

        await asyncio.sleep(60 - datetime.now().second)

# ========== НАПОМИНАНИЯ О ДНЕ АНГЕЛА ==========
async def get_prayer_of_day() -> str:
    """Генерирует или возвращает из кеша молитву дня"""
    today = datetime.now().strftime("%Y-%m-%d")
    # Проверяем кеш
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT prayer FROM daily_prayer_cache WHERE date=?", (today,))
    row = c.fetchone()
    conn.close()
    if row:
        return row[0]
    # Генерируем новую
    day_str = date_ru("short")
    feast = get_todays_feast()
    saints = get_todays_saints()
    context = ""
    if feast:
        context = f"Сегодня праздник: {feast}."
    elif saints:
        context = f"Сегодня память: {', '.join([s[0] for s in saints[:2]])}."
    prompt = (
        f"Напиши православную молитву дня. {context} "
        f"Дата: {day_str}. "
        "Молитва должна быть тёплой, душевной, 8-15 строк. "
        "Начни с обращения к Господу или Богородице. Заверши Аминь. "
        "Пиши только по-русски."
    )
    try:
        msg = claude_client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=600,
            system="Ты православный священник. Пишешь молитвы тепло и душевно.",
            messages=[{"role": "user", "content": prompt}]
        )
        prayer = msg.content[0].text
        # Сохраняем в кеш
        conn2 = sqlite3.connect(DB_PATH)
        conn2.execute("INSERT OR REPLACE INTO daily_prayer_cache (date, prayer) VALUES (?,?)", (today, prayer))
        conn2.commit()
        conn2.close()
        return prayer
    except Exception as e:
        logging.error(f"Ошибка молитвы дня: {e}")
        return PRAYERS["morning_ru"]["text"]

async def morning_broadcast():
    """Утренняя рассылка всем пользователям у кого включены уведомления"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT user_id, church_name FROM users WHERE notifications=1 OR notifications IS NULL")
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
        + "\n\n\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\n\U0001f64f \u0412\u0441\u0435 \u043c\u043e\u043b\u0438\u0442\u0432\u044b \u2192 @Moya\_Vera\_bot"
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
    """Напоминания о дне ангела"""
    await asyncio.sleep(30)
    while True:
        now = datetime.now()
        if now.hour == 9 and now.minute == 0:
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            c.execute("SELECT user_id, church_name, angel_day, remind_days FROM users WHERE angel_day != '' AND angel_day IS NOT NULL")
            users = c.fetchall()
            conn.close()
            for user_id, name, angel_day, remind_days in users:
                try:
                    angel_str = angel_day.split(" ")[0]
                    angel_date = datetime.strptime(angel_str, "%d.%m").replace(year=now.year)
                    diff = (angel_date - now.replace(hour=0, minute=0, second=0, microsecond=0)).days
                    if diff == remind_days:
                        await bot.send_message(
                            user_id,
                            f"🕊️ *Скоро ваш день ангела!*\n\n"
                            f"Через {remind_days} дн. — {angel_day}\n\n"
                            f"Помолитесь своему святому покровителю 🙏",
                            parse_mode="Markdown"
                        )
                    elif diff == 0:
                        await bot.send_message(
                            user_id,
                            f"🎉 *С Днём ангела, {name}!*\n\n"
                            f"{angel_day}\n\n"
                            f"Пусть ваш святой покровитель\n"
                            f"хранит и молится за вас! ☦️",
                            parse_mode="Markdown"
                        )
                except Exception as e:
                    logging.error(f"Ошибка напоминания {user_id}: {e}")
        await asyncio.sleep(60 - datetime.now().second)

# ========== ХЭНДЛЕРЫ ==========

@dp.message(Command("start"))
async def cmd_start(message: Message):
    user_id    = message.from_user.id
    username   = message.from_user.username   or ""
    first_name = message.from_user.first_name or ""
    user = get_user(user_id, username, first_name)
    asyncio.create_task(asyncio.to_thread(sheets_add_user, user_id, username, first_name))

    if not user.get("onboarded"):
        await message.answer(
            f"☦️ *Добро пожаловать в «С верой»!*\n\n"
            f"Я ваш православный помощник — здесь всё\n"
            f"что нужно для духовной жизни:\n\n"
            f"🙏 Молитвы на все случаи жизни\n"
            f"📅 Православный календарь и посты\n"
            f"⛪ Таинства — как подготовиться\n"
            f"👼 Жития святых и мощи\n"
            f"🏛️ Святые места России и мира\n"
            f"📸 Узнать храм или икону по фото\n"
            f"❓ Задать вопрос о вере\n\n"
            f"─────────────────\n"
            f"Чтобы напоминать о *дне ангела* —\n"
            f"укажите имя при крещении и дату рождения.\n"
            f"Займёт 30 секунд 🕊️",
            parse_mode="Markdown",
            reply_markup=onboarding_menu()
        )
    else:
        name = user.get("church_name") or first_name
        await message.answer(
            f"☦️ *С возвращением, {name}!*\n\n"
            f"Рад видеть вас снова 🕊️\n\n"
            f"Чем могу помочь?",
            parse_mode="Markdown",
            reply_markup=main_menu()
        )

@dp.message(Command("menu"))
async def cmd_menu(message: Message):
    await message.answer("☦️ Главное меню:", reply_markup=main_menu())

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
        "Чем могу помочь?\n\n"
        "🕯️ Если бот будет полезен — вы можете поддержать\n"
        "его развитие во славу Божию.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="☦️ Открыть меню",        callback_data="main_menu")],
            [InlineKeyboardButton(text="🕯️ Поддержать проект",   callback_data="donate")],
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
    sacr = SACRAMENTS.get("pasха")
    await callback.message.answer(
        f"*{sacr['title']}*\n\n{sacr['text']}",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🙏 Молитвы",        callback_data="prayers")],
            [InlineKeyboardButton(text="📅 Календарь",      callback_data="calendar")],
            [InlineKeyboardButton(text="🏠 Главное меню",   callback_data="main_menu")],
        ])
    )
    await callback.answer()

@dp.callback_query(F.data == "cal_kreschenije")
async def cb_kreschenije(callback: CallbackQuery):
    sacr = SACRAMENTS.get("kreschenije_prazdnik")
    await callback.message.answer(
        f"*{sacr['title']}*\n\n{sacr['text']}",
        parse_mode="Markdown",
        reply_markup=back_section("calendar")
    )
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
        text = f"👼 В нашей базе нет именинников на {today}.\n\nБаза постоянно пополняется 🙏"
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
    await callback.message.answer("📖 Нахожу Евангелие дня...")
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
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT notifications FROM users WHERE user_id=?", (user_id,))
    row = c.fetchone()
    current = row[0] if row and row[0] is not None else 1
    new_val = 0 if current else 1
    c.execute("UPDATE users SET notifications=? WHERE user_id=?", (new_val, user_id))
    conn.commit()
    conn.close()
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
    conn = sqlite3.connect(DB_PATH)
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
        message = claude_client.messages.create(
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
        conn2 = sqlite3.connect(DB_PATH)
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
    conn = sqlite3.connect(DB_PATH)
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
    conn   = sqlite3.connect(DB_PATH)
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

# ========== ПОЖЕРТВОВАНИЕ ==========
@dp.callback_query(F.data.in_({"donation", "donate"}))
async def cb_donation(callback: CallbackQuery):
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

async def donation_monthly_loop():
    """Рассылка пожертвований раз в месяц"""
    await asyncio.sleep(60)
    while True:
        now = datetime.now()
        if now.day == 1 and now.hour == 12 and now.minute == 0:
            conn = sqlite3.connect(DB_PATH)
            c    = conn.cursor()
            c.execute("SELECT user_id FROM users")
            users = c.fetchall()
            conn.close()
            for (user_id,) in users:
                try:
                    await bot.send_message(
                        user_id,
                        "☦️ *Дорогой друг!*\n\n"
                        "Благодарим что вы с нами.\n"
                        "Если бот «С верой» помогает вам\n"
                        "в духовной жизни — вы можете\n"
                        "поддержать его развитие.\n\n"
                        "Любая сумма — это большая помощь 🕯️",
                        parse_mode="Markdown",
                        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                            [InlineKeyboardButton(
                                text="🕯️ Поддержать проект",
                                callback_data="donate"
                            )],
                        ])
                    )
                    await asyncio.sleep(0.1)
                except Exception:
                    pass
        await asyncio.sleep(60 - datetime.now().second)

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

# ========== ПОДПИСКА ==========
@dp.callback_query(F.data == "subscription")
async def cb_subscription(callback: CallbackQuery):
    plan, sub_end = get_subscription(callback.from_user.id)
    if plan:
        end_date = datetime.fromisoformat(sub_end).strftime("%d.%m.%Y")
        text = (
            f"🌟 *У вас активен Премиум*\n\n"
            f"Действует до: {end_date}\n\n"
            f"Премиум включает:\n"
            f"— Безлимитные AI-вопросы\n"
            f"— Расширенные жития святых\n"
            f"— Приоритетные ответы"
        )
    else:
        lim   = get_limits(callback.from_user.id)
        used  = lim["ai_requests"]
        text  = (
            f"💎 *Тарифы*\n\n"
            f"*Бесплатный:*\n"
            f"— {FREE_AI_REQUESTS} AI-вопросов в день (использовано: {used})\n"
            f"— {FREE_PHOTO} фото-анализа\n"
            f"— Все молитвы, календарь, таинства\n\n"
            f"*🌟 Премиум — 149 руб/мес:*\n"
            f"— Безлимитные AI-вопросы\n"
            f"— Безлимитные фото-анализы\n"
            f"— Расширенные жития и места\n"
            f"— Приоритетная поддержка"
        )
    await callback.message.answer(
        text,
        parse_mode="Markdown",
        reply_markup=subscription_menu()
    )
    await callback.answer()

@dp.callback_query(F.data == "buy_premium")
async def cb_buy_premium(callback: CallbackQuery):
    user_id = callback.from_user.id
    try:
        payment = Payment.create({
            "amount":      {"value": "149.00", "currency": "RUB"},
            "confirmation": {
                "type":       "redirect",
                "return_url": f"https://t.me/Moya_Vera_bot"
            },
            "capture":     True,
            "description": "Премиум подписка — С верой",
            "metadata":    {"user_id": str(user_id), "plan": "premium"},
            "receipt": {
                "customer": {"email": "6038484@mail.ru"},
                "items": [{
                    "description": "Премиум подписка С верой",
                    "quantity": "1.00",
                    "amount": {"value": "149.00", "currency": "RUB"},
                    "vat_code": 1,
                    "payment_mode": "full_payment",
                    "payment_subject": "service"
                }]
            },
        }, str(uuid.uuid4()))

        conn = sqlite3.connect(DB_PATH)
        c    = conn.cursor()
        c.execute("INSERT OR REPLACE INTO pending_payments VALUES (?,?,?,?)",
                  (payment.id, user_id, "premium", datetime.now().isoformat()))
        conn.commit()
        conn.close()

        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💳 Перейти к оплате", url=payment.confirmation.confirmation_url)],
            [InlineKeyboardButton(text="◀️ Назад",            callback_data="subscription")],
        ])
        await callback.message.answer(
            "🌟 *Оформление Премиум подписки*\n\n"
            "149 руб/мес — безлимитный доступ\n\n"
            "Нажмите кнопку для перехода к оплате 👇",
            parse_mode="Markdown",
            reply_markup=kb
        )
    except Exception as e:
        logging.error(f"Ошибка создания платежа: {e}")
        await callback.message.answer(
            "⚠️ Ошибка при создании платежа.\n"
            "Попробуйте позже или свяжитесь с поддержкой:\n"
            "@Boss023rus"
        )
    await callback.answer()

# ========== ПРОВЕРКА ПЛАТЕЖЕЙ ==========
async def check_payments_loop():
    await asyncio.sleep(30)
    while True:
        try:
            conn = sqlite3.connect(DB_PATH)
            c    = conn.cursor()
            c.execute("SELECT payment_id, user_id, plan FROM pending_payments")
            payments = c.fetchall()
            conn.close()
            for payment_id, user_id, plan in payments:
                try:
                    payment = Payment.find_one(payment_id)
                    if payment.status == "succeeded":
                        sub_end = (datetime.now() + timedelta(days=30)).isoformat()
                        conn2   = sqlite3.connect(DB_PATH)
                        c2      = conn2.cursor()
                        c2.execute("INSERT OR REPLACE INTO subscriptions VALUES (?,?,?)",
                                   (user_id, plan, sub_end))
                        c2.execute("DELETE FROM pending_payments WHERE payment_id=?", (payment_id,))
                        conn2.commit()
                        conn2.close()
                        await bot.send_message(
                            user_id,
                            "🌟 *Оплата прошла успешно!*\n\n"
                            "Добро пожаловать в Премиум!\n"
                            "Безлимитные AI-вопросы активированы. 🙏",
                            parse_mode="Markdown",
                            reply_markup=main_menu()
                        )
                except Exception as e:
                    logging.error(f"Ошибка проверки платежа {payment_id}: {e}")
        except Exception as e:
            logging.error(f"Ошибка петли платежей: {e}")
        await asyncio.sleep(60)

# ========== ОБРАБОТКА ФОТО ==========
@dp.message(F.photo)
async def handle_photo(message: Message):
    user_id = message.from_user.id
    user    = get_user(user_id)
    step    = user.get("step", "")

    if step not in ("photo_church", "photo_icon"):
        await message.answer(
            "Выберите сначала что хотите определить 👇",
            reply_markup=photo_menu()
        )
        return

    photo      = message.photo[-1]
    file       = await bot.get_file(photo.file_id)
    local_path = f"/tmp/vera_photo_{user_id}.jpg"
    photo_type = "church" if step == "photo_church" else "icon"

    set_step(user_id, "idle")
    await message.answer("⏳ Анализирую фото...")

    try:
        await bot.download_file(file.file_path, local_path)
        result = await analyze_photo_gpt("", photo_type, local_path=local_path)
    except Exception as e:
        logging.error(f"Ошибка скачивания фото: {e}")
        result = "Не удалось загрузить фото. Попробуйте ещё раз."

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
        file_path = f"/tmp/vera_voice_{user_id}.ogg"
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
                    await bot.send_message(8935471523,
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
            asyncio.create_task(asyncio.to_thread(
                add_review_to_sheet, user_id,
                message.from_user.username or "",
                message.from_user.first_name or "", text
            ))
            set_step(user_id, "idle")
            await message.answer(
                "☦️ *Спасибо за ваш отзыв!*\n\nДа хранит вас Господь 🕊️",
                parse_mode="Markdown", reply_markup=main_menu()
            )
        else:
            await message.answer(
                "☦️ Голосовые сообщения работают только при вводе вопроса о вере.\n"
                "Нажмите *Задать вопрос* в меню 👇",
                parse_mode="Markdown", reply_markup=main_menu()
            )
    except Exception as e:
        logging.error(f"Ошибка голосового: {e}")
        try:
            await bot.send_message(8935471523, f"⚠️ Ошибка голосового в @Moya_Vera_bot\n{e}")
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

    # Онбординг — имя
    if step == "onboard_name":
        angel = ""
        set_step(user_id, "onboard_birth")
        conn = sqlite3.connect(DB_PATH)
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
            conn = sqlite3.connect(DB_PATH)
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
            angel_text = f"\n👼 Ваш день ангела: *{angel}*" if angel else "\n👼 День ангела: имя не найдено в базе"
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
        conn = sqlite3.connect(DB_PATH)
        c    = conn.cursor()
        c.execute("SELECT birth_date FROM users WHERE user_id=?", (user_id,))
        row  = c.fetchone()
        birth = row[0] if row else ""
        conn.close()
        angel = find_angel_day(text, birth) if birth else ""
        conn2 = sqlite3.connect(DB_PATH)
        c2    = conn2.cursor()
        c2.execute("UPDATE users SET church_name=?, angel_day=? WHERE user_id=?", (text, angel, user_id))
        conn2.commit()
        conn2.close()
        await message.answer(
            f"✅ Имя обновлено: *{text}*\n"
            f"👼 День ангела: *{angel or 'не найден'}*",
            parse_mode="Markdown",
            reply_markup=back_section("profile")
        )
        set_step(user_id, "idle")
        return

    # Редактирование даты рождения
    if step == "edit_birth":
        try:
            datetime.strptime(text, "%d.%m")
            conn = sqlite3.connect(DB_PATH)
            c    = conn.cursor()
            c.execute("SELECT church_name FROM users WHERE user_id=?", (user_id,))
            row  = c.fetchone()
            conn.close()
            church_name = row[0] if row else ""
            angel       = find_angel_day(church_name, text) if church_name else ""
            conn2       = sqlite3.connect(DB_PATH)
            c2          = conn2.cursor()
            c2.execute("UPDATE users SET birth_date=?, angel_day=? WHERE user_id=?", (text, angel, user_id))
            conn2.commit()
            conn2.close()
            await message.answer(
                f"✅ Дата рождения обновлена: *{text}*\n"
                f"👼 День ангела: *{angel or 'не найден'}*",
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
    if step == "review":
        asyncio.create_task(asyncio.to_thread(
            add_review_to_sheet,
            user_id,
            message.from_user.username or "",
            message.from_user.first_name or "",
            text
        ))
        set_step(user_id, "idle")
        await message.answer(
            "☦️ *Спасибо за ваш отзыв!*\n\n"
            "Мы обязательно его учтём при развитии проекта.\n"
            "Да хранит вас Господь 🕊️",
            parse_mode="Markdown",
            reply_markup=main_menu()
        )
        return

    # Пожертвование — ввод суммы
    if step == "donate_amount":
        try:
            amount = int(text.strip())
            if amount < 10:
                await message.answer(
                    "⚠️ Минимальная сумма пожертвования — 10 рублей.\n"
                    "Введите сумму цифрой:",
                    reply_markup=back_menu()
                )
                return
            payment = Payment.create({
                "amount":       {"value": f"{amount}.00", "currency": "RUB"},
                "confirmation": {
                    "type":       "redirect",
                    "return_url": "https://t.me/Moya_Vera_bot"
                },
                "capture":     True,
                "description": "Пожертвование на развитие «С верой» во славу Божию",
                "metadata":    {"user_id": str(user_id), "plan": "donation"},
                "receipt": {
                    "customer": {"email": "6038484@mail.ru"},
                    "items": [{
                        "description": "Пожертвование на развитие «С верой»",
                        "quantity": "1.00",
                        "amount": {"value": f"{amount}.00", "currency": "RUB"},
                        "vat_code": 1,
                        "payment_mode": "full_payment",
                        "payment_subject": "another"
                    }]
                },
            }, str(uuid.uuid4()))
            set_step(user_id, "idle")
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
                await bot.send_message(8935471523,
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
    asyncio.create_task(channel_post_loop())
    asyncio.create_task(angel_reminder_loop())
    asyncio.create_task(check_payments_loop())
    asyncio.create_task(donation_monthly_loop())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
