import asyncio
import random
import re
import sqlite3
import logging
import os
import httpx
import uuid
from pathlib import Path
from datetime import datetime, date, timedelta
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from openai import AsyncOpenAI
import anthropic
import uvicorn
import json
from contextlib import suppress
try:
    import gspread
    from google.oauth2.service_account import Credentials
    SHEETS_AVAILABLE = True
except ImportError:
    SHEETS_AVAILABLE = False
    logging.warning("gspread не установлен — Google Sheets отключены")

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
logging.basicConfig(level=logging.INFO)

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
MAX_TOKEN     = _env.get("MAX_TOKEN") or os.environ.get("MAX_TOKEN", "")
MAX_API       = "https://platform-api.max.ru"
OPENAI_KEY    = _env.get("OPENAI_KEY") or os.environ.get("OPENAI_KEY", "")
ANTHROPIC_KEY = _env.get("ANTHROPIC_KEY") or os.environ.get("ANTHROPIC_KEY", "")
CHANNEL_POST_TIMEOUT_SECONDS = _config_float("CHANNEL_POST_TIMEOUT_SECONDS", 75.0, 30.0, 120.0)


def _config_int(name: str, default: int) -> int:
    """Читает числовой параметр сначала из .env_vera, затем из окружения."""
    raw = _env.get(name) or os.environ.get(name)
    if raw is None or str(raw).strip() == "":
        return int(default)
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        logging.warning(f"Некорректное значение {name}={raw!r}; используется {default}")
        return int(default)


# Реальный MAX ID владельца лучше хранить в /root/.env_vera:
# MAX_OWNER_ID=123456789
OWNER_ID      = _config_int("MAX_OWNER_ID", 549639607)
DB_PATH       = _env.get("MAX_DB_PATH") or os.environ.get("MAX_DB_PATH", "/root/vera_max.db")
BACKUP_DIR     = _env.get("VERA_BACKUP_DIR") or os.environ.get("VERA_BACKUP_DIR", "/root/vera_backups")
WEBHOOK_URL   = _env.get("MAX_WEBHOOK_URL") or os.environ.get("MAX_WEBHOOK_URL", "https://sveroy.ru/webhook")

logging.info(f"MAX_TOKEN: {'configured' if MAX_TOKEN else 'missing'}")
logging.info(f"OPENAI_KEY: {'configured' if OPENAI_KEY else 'missing'}")
logging.info(f"MAX_OWNER_ID: {OWNER_ID}")

CREDENTIALS_FILE = _env.get("GOOGLE_CREDENTIALS_FILE") or os.environ.get("GOOGLE_CREDENTIALS_FILE", "/root/google_credentials.json")
SPREADSHEET_ID   = _env.get("VERA_SPREADSHEET_ID") or os.environ.get("VERA_SPREADSHEET_ID", "1PE7CaFuWOe_eygQqIoMAmUdJBtATbIaNfZR4cvarPCA")

# YooKassa credentials are read from /root/.env_vera when available.
YOOKASSA_SHOP_ID = _env.get("YOOKASSA_SHOP_ID") or os.environ.get("YOOKASSA_SHOP_ID", "")
YOOKASSA_SECRET = _env.get("YOOKASSA_SECRET") or os.environ.get("YOOKASSA_SECRET", "")

def validate_core_config():
    missing = [
        name for name, value in (
            ("MAX_TOKEN", MAX_TOKEN),
            ("OPENAI_KEY", OPENAI_KEY),
            ("ANTHROPIC_KEY", ANTHROPIC_KEY),
        ) if not str(value or "").strip()
    ]
    if missing:
        raise RuntimeError(
            "Не заданы обязательные параметры в /root/.env_vera: " + ", ".join(missing)
        )


validate_core_config()

# ========== КЛИЕНТЫ AI ==========
openai_client = AsyncOpenAI(api_key=OPENAI_KEY)
claude_client = anthropic.Anthropic(api_key=ANTHROPIC_KEY, timeout=45.0)

# ========== MAX API ==========
async def max_request(method, endpoint, data=None):
    headers = {
        "Authorization": MAX_TOKEN,
        "Content-Type": "application/json"
    }
    url = f"{MAX_API}/{endpoint}"
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            if method == "GET":
                r = await client.get(url, headers=headers)
            elif method == "POST":
                r = await client.post(url, json=data, headers=headers)
            elif method == "PUT":
                r = await client.put(url, json=data, headers=headers)
            elif method == "PATCH":
                r = await client.patch(url, json=data, headers=headers)
            elif method == "DELETE":
                r = await client.delete(url, headers=headers)
            logging.info(f"MAX {method} {endpoint}: {r.status_code}")
            return r.json()
    except Exception as e:
        logging.error(f"Ошибка MAX API {method} {endpoint}: {e}")
        return {}

async def send_message(chat_id, text, buttons=None):
    payload = {"text": text[:4000]}
    if buttons:
        payload["attachments"] = [{"type": "inline_keyboard", "payload": {"buttons": buttons}}]
    return await max_request("POST", f"messages?chat_id={chat_id}", payload)

async def get_photo_bytes(photo_url):
    """Скачиваем фото по прямому URL"""
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(photo_url)
            if r.status_code == 200 and len(r.content) > 100:
                return r.content
            logging.error(f"Ошибка скачивания фото: {r.status_code}")
            return None
    except Exception as e:
        logging.error(f"Ошибка get_photo: {e}")
        return None

async def register_webhook():
    result = await max_request("POST", "subscriptions", {"url": WEBHOOK_URL, "update_types": ["message_created", "message_callback", "bot_started", "bot_stopped"]})
    logging.info(f"Webhook регистрация: {result}")


# ========== GOOGLE SHEETS ==========
def get_spreadsheet():
    if not SHEETS_AVAILABLE:
        return None
    try:
        scopes = ["https://www.googleapis.com/auth/spreadsheets"]
        creds  = Credentials.from_service_account_file(CREDENTIALS_FILE, scopes=scopes)
        client = gspread.authorize(creds)
        return client.open_by_key(SPREADSHEET_ID)
    except Exception as e:
        logging.error(f"Sheets connect error: {e}")
        return None

def sheets_add_user_max(user_id, username, first_name):
    try:
        sp = get_spreadsheet()
        if not sp: return
        try:
            sheet = sp.worksheet("С верой MAX")
        except Exception:
            sheet = sp.add_worksheet(title="С верой MAX", rows=2000, cols=8)
            sheet.insert_row(["ID","Username","Имя","Дата регистрации","Последняя активность","Запросов AI","Отзывов","Пожертвований"], 1)
        col = sheet.col_values(1)
        if str(user_id) not in col:
            sheet.append_row([
                str(user_id),
                f"@{username}" if username else "—",
                first_name or "—",
                datetime.now().strftime("%d.%m.%Y %H:%M"),
                datetime.now().strftime("%d.%m.%Y %H:%M"),
                "0", "0", "0"
            ])
    except Exception as e:
        logging.error(f"Sheets add_user_max: {e}")

def sheets_update_activity_max(user_id):
    try:
        sp = get_spreadsheet()
        if not sp: return
        sheet = sp.worksheet("С верой MAX")
        col = sheet.col_values(1)
        if str(user_id) in col:
            row = col.index(str(user_id)) + 1
            sheet.update_cell(row, 5, datetime.now().strftime("%d.%m.%Y %H:%M"))
            ai_val = sheet.cell(row, 6).value or "0"
            sheet.update_cell(row, 6, str(int(ai_val) + 1))
    except Exception as e:
        logging.error(f"Sheets update_activity_max: {e}")

REVIEW_SHEET_HEADERS = [
    "ID", "Username", "Имя", "Дата", "Отзыв",
    "Номер отзыва", "Статус", "Ответ владельца", "Дата ответа", "Ответил"
]


def ensure_review_sheet(sp=None):
    """Создаёт или обновляет лист отзывов до CRM-структуры."""
    try:
        sp = sp or get_spreadsheet()
        if not sp:
            return None
        try:
            sheet = sp.worksheet("Отзывы MAX")
        except Exception:
            sheet = sp.add_worksheet(
                title="Отзывы MAX",
                rows=1000,
                cols=len(REVIEW_SHEET_HEADERS)
            )
        if getattr(sheet, "col_count", 0) < len(REVIEW_SHEET_HEADERS):
            sheet.resize(cols=len(REVIEW_SHEET_HEADERS))
        current_headers = sheet.row_values(1)
        for index, header in enumerate(REVIEW_SHEET_HEADERS, start=1):
            if len(current_headers) < index or current_headers[index - 1] != header:
                sheet.update_cell(1, index, header)
        return sheet
    except Exception as e:
        logging.error(f"ensure_review_sheet: {e}")
        return None


def ensure_review_sheet_schema():
    """Подготавливает столбцы листа отзывов при запуске."""
    ensure_review_sheet()


def sheets_add_review_max(review_id, user_id, username, first_name, text):
    try:
        sp = get_spreadsheet()
        if not sp:
            return
        sheet = ensure_review_sheet(sp)
        if not sheet:
            return
        sheet.append_row([
            str(user_id),
            f"@{username}" if username else "—",
            first_name or "—",
            datetime.now().strftime("%d.%m.%Y %H:%M"),
            text,
            str(review_id),
            "Новый",
            "—",
            "—",
            "—"
        ])
        try:
            main_sheet = sp.worksheet("С верой MAX")
            col = main_sheet.col_values(1)
            if str(user_id) in col:
                row = col.index(str(user_id)) + 1
                rev_val = main_sheet.cell(row, 7).value or "0"
                main_sheet.update_cell(row, 7, str(int(rev_val) + 1))
        except Exception:
            pass
    except Exception as e:
        logging.error(f"sheets_add_review_max: {e}")


def sheets_update_review_max(
    review_id,
    status,
    reply_text="",
    replied_at="",
    handled_by="Владелец"
):
    """Обновляет статус ответа. Повторяет поиск, если append ещё выполняется."""
    import time
    for attempt in range(1, 6):
        try:
            sp = get_spreadsheet()
            if not sp:
                return
            sheet = ensure_review_sheet(sp)
            if not sheet:
                return
            review_ids = sheet.col_values(6)
            review_id_str = str(review_id)
            if review_id_str in review_ids:
                row = review_ids.index(review_id_str) + 1
                sheet.update_cell(row, 7, status)
                sheet.update_cell(row, 8, reply_text or "—")
                sheet.update_cell(row, 9, replied_at or "—")
                sheet.update_cell(row, 10, handled_by or "Владелец")
                return
            if attempt < 5:
                time.sleep(2)
        except Exception as e:
            logging.error(f"sheets_update_review_max attempt {attempt}: {e}")
            if attempt < 5:
                time.sleep(2)
    logging.warning(f"Отзыв #{review_id} не найден в Google Sheets после повторов")


def sheets_update_latest_review_by_user(
    user_id,
    status,
    reply_text,
    replied_at,
    handled_by="Владелец"
):
    """Обновляет последний старый отзыв по ID пользователя, даже без номера отзыва."""
    try:
        sp = get_spreadsheet()
        if not sp:
            return
        sheet = ensure_review_sheet(sp)
        if not sheet:
            return
        user_ids = sheet.col_values(1)
        target = str(user_id)
        matching_rows = [i + 1 for i, value in enumerate(user_ids) if value == target]
        if not matching_rows:
            logging.warning(f"Отзывы пользователя {user_id} не найдены в Google Sheets")
            return
        row = matching_rows[-1]
        sheet.update_cell(row, 7, status)
        sheet.update_cell(row, 8, reply_text or "—")
        sheet.update_cell(row, 9, replied_at or "—")
        sheet.update_cell(row, 10, handled_by or "Владелец")
    except Exception as e:
        logging.error(f"sheets_update_latest_review_by_user: {e}")

def sheets_add_donation(user_id, username, first_name, amount, source="MAX"):
    try:
        sp = get_spreadsheet()
        if not sp:
            return False
        try:
            sheet = sp.worksheet("Пожертвования")
        except Exception:
            sheet = sp.add_worksheet(title="Пожертвования", rows=2000, cols=6)
            sheet.insert_row(["ID","Username","Имя","Сумма (руб)","Дата","Источник"], 1)
        sheet.append_row([str(user_id), f"@{username}" if username else "—", first_name or "—", str(amount), datetime.now().strftime("%d.%m.%Y %H:%M"), source])
        if source == "MAX":
            try:
                main_sheet = sp.worksheet("С верой MAX")
                col = main_sheet.col_values(1)
                if str(user_id) in col:
                    row = col.index(str(user_id)) + 1
                    don_val = main_sheet.cell(row, 8).value or "0"
                    main_sheet.update_cell(row, 8, str(int(don_val) + 1))
            except Exception as e:
                logging.warning(f"MAX donation counter not updated: {e}")
        return True
    except Exception as e:
        logging.error(f"Sheets add_donation: {e}")
        return False


# ========== КНОПКИ ==========
def btn(text, payload):
    return {"type": "callback", "text": text[:40], "payload": payload}

def link_btn(text, url):
    return {"type": "link", "text": text[:40], "url": url}

def main_menu_buttons():
    return [
        [btn("☦️ Начать за 60 секунд", "quick_start")],
        [btn("🙏 Молитвы", "prayers"), btn("📅 Календарь", "calendar")],
        [btn("⛪ Таинства", "sacraments"), btn("👼 Святые", "saints")],
        [btn("🏛️ Святыни", "holy_places"), btn("📚 Библиотека", "library")],
        [btn("📸 Узнать по фото", "photo_menu"), btn("🗺️ Храм рядом", "find_church")],
        [btn("📖 Евангельская мысль", "daily_gospel")],
        [btn("👤 Мой профиль", "profile"), btn("❓ Задать вопрос", "ask_question")],
        [btn("🕯️ Пожертвование на развитие", "donate")],
        [btn("💬 Отзыв или пожелание", "review")],
        [btn("🤝 Пригласить близкого", "invite_friend")],
    ]

def back_main():
    return [[btn("◀️ Главное меню", "main_menu")]]

def prayers_buttons():
    return [
        [btn("✨ Молитва дня", "prayer_of_day")],
        [btn("🙏 Молитва за меня", "prayer_for_me")],
        [btn("🌅 Утренняя (рус)", "prayer_morning_ru"), btn("🌅 Утренняя (цс)", "prayer_morning_cs")],
        [btn("🌙 Вечерняя (рус)", "prayer_evening_ru"), btn("🌙 Вечерняя (цс)", "prayer_evening_cs")],
        [btn("🍽️ Перед едой", "prayer_before_meal"), btn("✝️ Символ веры", "prayer_symbol")],
        [btn("📿 Иисусова молитва", "prayer_jesus"), btn("🙏 Отче наш", "prayer_otche")],
        [btn("🌸 Богородице Дево", "prayer_bogorodice"), btn("📖 Канон покаянный", "prayer_pokayanny_kanon")],
        [btn("🕯️ Николаю Чудотворцу", "prayer_nikolay"), btn("🌺 Матроне Московской", "prayer_matrona")],
        [btn("✝️ Правило ко Причастию", "prayer_prichaschenie"), btn("🕯️ Об упокоении", "prayer_upokoenie")],
        [btn("⭐ Избранные молитвы", "favorites")],
        [btn("◀️ Главное меню", "main_menu")],
    ]

def sacraments_buttons():
    return [
        [btn("📿 Исповедь", "sacr_ispoved"), btn("✝️ Причастие", "sacr_prichaschenie")],
        [btn("💧 Крещение", "sacr_kreshchenie"), btn("💍 Венчание", "sacr_venchanie")],
        [btn("🕯️ Отпевание", "sacr_otpevanie"), btn("🫒 Соборование", "sacr_sobor")],
        [btn("🏠 Освящение", "sacr_osvyashchenie"), btn("🕯️ Как ставить свечи", "sacr_svecha")],
        [btn("📝 Как подавать записки", "sacr_zapiska"), btn("⛪ Как вести себя", "sacr_v_hrame")],
        [btn("✍️ Составить записку", "make_zapiska")],
        [btn("◀️ Главное меню", "main_menu")],
    ]

def holy_places_buttons():
    return [
        [btn("🏙️ Москва", "place_moscow"), btn("🏙️ Санкт-Петербург", "place_spb")],
        [btn("📍 Подмосковье", "place_podmoskove")],
        [btn("📍 Центральная Россия", "place_central")],
        [btn("📍 Север и Северо-Запад", "place_northwest")],
        [btn("📍 Урал и Сибирь", "place_ural_siberia")],
        [btn("📍 Юг и Крым", "place_south")],
        [btn("✝️ Абхазия", "place_abkhazia")],
        [btn("⛵ Афон", "place_afon"), btn("🌍 Святые места мира", "place_world")],
        [btn("◀️ Главное меню", "main_menu")],
    ]

def calendar_buttons():
    return [
        [btn("📅 Сегодня", "cal_today")],
        [btn("🥗 Пост сегодня", "cal_fast_today")],
        [btn("🎉 Православные праздники", "cal_feasts")],
        [btn("🥗 Все посты", "cal_fasts")],
        [btn("👼 Именинники сегодня", "cal_namedays")],
        [btn("🔍 Найти именины по имени", "cal_find_angel")],
        [btn("🥚 Пасха — всё о главном празднике", "cal_pasxa")],
        [btn("💧 Крещение Господне", "cal_kreschenije")],
        [btn("◀️ Главное меню", "main_menu")],
    ]

def library_buttons():
    return [
        [btn("📝 Церковный словарь", "lib_slovar")],
        [btn("❓ Частые вопросы о вере", "lib_faq")],
        [btn("📚 Православная литература", "lib_literatura")],
        [btn("📥 Скачать книги бесплатно", "lib_pdf")],
        [btn("◀️ Главное меню", "main_menu")],
    ]

# ========== ДАННЫЕ ==========
FIXED_FEASTS = {
    "14.01": "Обрезание Господне, память свт. Василия Великого",
    "07.01": "Рождество Христово ☀️",
    "19.01": "Богоявление (Крещение Господне) 💧",
    "15.02": "Сретение Господне",
    "07.04": "Благовещение Пресвятой Богородицы",
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

SAINTS_BY_NAME = {'абрам': [('22.10', 'прп. Авраамия Ростовского')],
 'авраам': [('22.10', 'прп. Авраамия Ростовского'), ('09.10', 'прп. Авраамия Затворника')],
 'агафья': [('18.02', 'мц. Агафии Панормской')],
 'агния': [('21.01', 'мц. Агнии Римской')],
 'адриан': [('26.08', 'мч. Адриана и Наталии')],
 'аким': [('20.08', 'прав. Иоакима и Анны')],
 'аксинья': [('24.01', 'прп. Ксении')],
 'александр': [('06.06', 'мч. Александра'),
               ('12.09', 'блгв. кн. Александра Невского'),
               ('23.11', 'блгв. кн. Александра Невского')],
 'алексей': [('30.03', 'прп. Алексия, человека Божия'), ('25.04', 'сщмч. Алексия')],
 'алла': [('26.03', 'мц. Аллы Готфской')],
 'альберт': [('14.11', 'мч. Альберта')],
 'амвросий': [('20.12', 'свт. Амвросия Медиоланского'), ('10.10', 'прп. Амвросия Оптинского')],
 'анастасия': [('04.01', 'мц. Анастасии Римляныни'), ('22.12', 'вмц. Анастасии Узорешительницы')],
 'анатолий': [('23.07', 'прп. Анатолия Оптинского'), ('15.08', 'мч. Анатолия')],
 'андрей': [('13.12', 'ап. Андрея Первозванного')],
 'анна': [('03.02', 'прп. Анны'), ('07.08', 'прп. Анны')],
 'антон': [('17.01', 'прп. Антония Великого'), ('23.07', 'прп. Антония Печерского')],
 'антоний': [('17.01', 'прп. Антония Великого'), ('23.07', 'прп. Антония Печерского')],
 'антонина': [('01.03', 'мц. Антонины'), ('10.06', 'мц. Антонины')],
 'аркадий': [('26.02', 'прп. Аркадия Новоторжского')],
 'арсений': [('08.05', 'свт. Арсения Великого'), ('24.07', 'прп. Арсения Коневского')],
 'артемий': [('02.11', 'мч. Артемия Антиохийского')],
 'артём': [('20.10', 'ап. Артемы'), ('02.11', 'мч. Артемия Антиохийского')],
 'аскольд': [('11.07', 'блгв. кн. Аскольда')],
 'афанасий': [('18.01', 'свт. Афанасия Великого'), ('25.01', 'свт. Афанасия и Кирилла')],
 'ахмат': [('11.09', 'мч. Ахмата')],
 'борис': [('06.08', 'блгв. кн. Бориса и Глеба'), ('24.07', 'блгв. кн. Бориса')],
 'вадим': [('22.04', 'прмч. Вадима Персидского')],
 'валентин': [('12.08', 'мч. Валентина'), ('19.07', 'мч. Валентина Доростольского')],
 'валентина': [('10.02', 'мц. Валентины'), ('07.08', 'мц. Валентины')],
 'валерий': [('07.03', 'мч. Валерия'), ('20.11', 'мч. Валерия')],
 'валерия': [('07.06', 'мц. Валерии')],
 'варвар': [('06.05', 'прп. Варвара')],
 'варвара': [('17.12', 'вмц. Варвары Илиопольской')],
 'варлаам': [('19.11', 'прп. Варлаама Хутынского')],
 'василий': [('14.01', 'свт. Василия Великого'), ('13.03', 'мч. Василия')],
 'василиса': [('15.01', 'мц. Василисы'), ('04.04', 'мц. Василисы')],
 'вениамин': [('13.08', 'сщмч. Вениамина Петроградского'), ('11.06', 'прп. Вениамина Нитрийского')],
 'вера': [('30.09', 'мц. Веры, Надежды, Любови и матери их Софии'), ('30.09', 'мц. Веры, Надежды, Любови')],
 'виктор': [('11.11', 'мч. Виктора'), ('05.03', 'мч. Виктора')],
 'виктория': [('23.12', 'мц. Виктории'), ('11.11', 'мц. Виктории')],
 'виталий': [('04.05', 'мч. Виталия Медиоланского'), ('22.04', 'мч. Виталия')],
 'виталия': [('04.05', 'мц. Виталии')],
 'владимир': [('28.07', 'равноап. кн. Владимира')],
 'вячеслав': [('04.03', 'блгв. кн. Вячеслава Чешского'), ('11.03', 'блгв. кн. Вячеслава')],
 'гавриил': [('26.07', 'арх. Гавриила'), ('08.04', 'арх. Гавриила')],
 'галина': [('29.03', 'мц. Галины')],
 'геннадий': [('17.12', 'свт. Геннадия Новгородского'), ('25.11', 'прп. Геннадия Костромского')],
 'георгий': [('06.05', 'вмч. Георгия Победоносца')],
 'герасим': [('17.03', 'прп. Герасима Иорданского')],
 'глеб': [('06.08', 'блгв. кн. Бориса и Глеба'), ('05.09', 'блгв. кн. Глеба')],
 'григорий': [('12.01', 'свт. Григория Нисского'), ('25.01', 'свт. Григория Богослова')],
 'давид': [('01.03', 'прп. Давида'), ('06.03', 'прп. Давида Солунского')],
 'даниил': [('17.12', 'прп. Даниила Столпника'), ('23.12', 'блгв. кн. Даниила Московского')],
 'дарья': [('01.04', 'мц. Дарии')],
 'денис': [('16.10', 'сщмч. Дионисия Ареопагита')],
 'дима': [('08.11', 'вмч. Димитрия Солунского'), ('01.06', 'блгв. кн. Димитрия Донского')],
 'дионисий': [('16.10', 'сщмч. Дионисия Ареопагита'), ('05.10', 'свт. Дионисия Суздальского')],
 'дмитрий': [('08.11', 'вмч. Димитрия Солунского'), ('01.06', 'блгв. кн. Димитрия Донского')],
 'домна': [('14.01', 'мц. Домны Никомидийской')],
 'домника': [('08.01', 'прп. Домники')],
 'евгений': [('26.12', 'мч. Евгения'), ('20.11', 'мч. Евгения Мелитинского')],
 'евгения': [('24.12', 'прмц. Евгении')],
 'евдоким': [('05.08', 'прав. Евдокима Каппадокийского')],
 'евдокия': [('14.03', 'прмц. Евдокии'), ('04.08', 'прав. Евдокии')],
 'екатерина': [('07.12', 'вмц. Екатерины')],
 'елена': [('03.06', 'равноап. царицы Елены'), ('24.07', 'равноап. Елены')],
 'елизавета': [('05.09', 'прмц. Елисаветы Феодоровны'), ('18.09', 'прмц. Елисаветы')],
 'ефим': [('20.01', 'прп. Евфимия Великого'), ('02.02', 'прп. Евфимия Нового')],
 'ефрем': [('10.02', 'прп. Ефрема Сирина'), ('05.03', 'свт. Ефрема Сербского')],
 'зинаида': [('23.10', 'мц. Зинаиды'), ('11.10', 'мц. Зинаиды')],
 'зиновий': [('13.11', 'мч. Зиновия и Зиновии')],
 'зоя': [('13.02', 'мц. Зои Вифлеемской'), ('02.05', 'мц. Зои')],
 'иван': [('20.01', 'Собор Иоанна Предтечи'), ('07.07', 'Рождество Иоанна Предтечи')],
 'илия': [('02.08', 'прор. Илии Фесвитянина')],
 'илья': [('02.08', 'прор. Илии Фесвитянина')],
 'иннокентий': [('26.11', 'свт. Иннокентия Иркутского'), ('06.10', 'свт. Иннокентия Московского')],
 'иоанн': [('20.01', 'Собор Иоанна Предтечи'), ('07.07', 'Рождество Иоанна Предтечи')],
 'иосиф': [('19.09', 'прав. Иосифа Прекрасного'), ('11.04', 'прп. Иосифа Волоцкого')],
 'ирина': [('29.04', 'мц. Ирины'), ('18.05', 'мц. Ирины')],
 'иулиания': [('15.01', 'мц. Иулиании Никомидийской'), ('02.01', 'блж. Иулиании Лазаревской')],
 'капитолина': [('27.10', 'мц. Капитолины')],
 'кирилл': [('27.02', 'равноап. Кирилла, учителя Словенского')],
 'клавдия': [('20.03', 'мц. Клавдии'), ('07.04', 'мц. Клавдии')],
 'климент': [('25.11', 'сщмч. Климента Римского')],
 'константин': [('03.06', 'равноап. царя Константина')],
 'кристина': [('24.07', 'вмц. Христины')],
 'ксения': [('06.02', 'блж. Ксении Петербургской')],
 'кузьма': [('14.07', 'бессрр. Космы и Дамиана'), ('14.11', 'бессрр. Космы и Дамиана')],
 'лариса': [('08.04', 'мц. Ларисы'), ('08.04', 'мц. Ларисы Готфской')],
 'лев': [('05.03', 'свт. Льва Катанского'), ('18.02', 'свт. Льва Великого')],
 'леонид': [('16.04', 'мч. Леонида'), ('10.07', 'прп. Леонида')],
 'лидия': [('05.04', 'мц. Лидии'), ('23.03', 'мц. Лидии')],
 'лука': [('31.10', 'ап. Луки'), ('11.06', 'свт. Луки Крымского')],
 'любовь': [('30.09', 'мц. Веры, Надежды, Любови')],
 'людмила': [('29.09', 'мц. кн. Людмилы Чешской')],
 'макар': [('19.01', 'прп. Макария Великого'), ('01.02', 'свт. Макария Московского')],
 'макарий': [('19.01', 'прп. Макария Великого'), ('01.02', 'свт. Макария Московского')],
 'максим': [('13.08', 'прп. Максима Исповедника'), ('11.11', 'блж. Максима Московского')],
 'максима': [('26.04', 'мц. Максимы')],
 'маргарита': [('30.07', 'вмц. Марины (Маргариты)')],
 'марина': [('30.07', 'вмц. Марины')],
 'мария': [('22.07', 'равноап. Марии Магдалины'), ('17.09', 'мц. Марии')],
 'марк': [('25.04', 'ап. Марка'), ('07.05', 'ап. Марка')],
 'мартин': [('14.04', 'свт. Мартина Исповедника')],
 'марфа': [('04.07', 'прп. Марфы'), ('01.09', 'прп. Марфы')],
 'матрона': [('02.05', 'блж. Матроны Московской'), ('09.08', 'мц. Матроны')],
 'мефодий': [('11.05', 'равноап. Мефодия, учителя Словенского')],
 'милана': [('19.07', 'мц. Миланы')],
 'мирон': [('17.08', 'сщмч. Мирона Кизического')],
 'митрофан': [('23.11', 'свт. Митрофана Воронежского'), ('06.06', 'свт. Митрофана')],
 'михаил': [('21.11', 'Собор Архистратига Михаила')],
 'моисей': [('04.09', 'прп. Моисея Угрина'), ('28.08', 'прп. Моисея Мурина')],
 'надежда': [('30.09', 'мц. Надежды')],
 'наталия': [('26.08', 'мц. Наталии')],
 'наталья': [('08.09', 'мц. Наталии'), ('26.08', 'мц. Наталии')],
 'никита': [('15.09', 'вмч. Никиты Готфского'), ('31.01', 'прп. Никиты Столпника')],
 'никифор': [('13.02', 'свт. Никифора Константинопольского')],
 'николай': [('22.05', 'свт. Николая, архиеп. Мирликийского'), ('19.12', 'свт. Николая Чудотворца')],
 'нина': [('27.01', 'равноап. Нины, просветительницы Грузии')],
 'нонна': [('05.08', 'прав. Нонны')],
 'оксана': [('24.01', 'прп. Ксении')],
 'олег': [('03.10', 'блгв. кн. Олега Брянского')],
 'олеся': [('03.10', 'мц. Александры')],
 'ольга': [('24.07', 'равноап. кн. Ольги')],
 'павел': [('12.07', 'ап. Петра и Павла')],
 'петр': [('12.07', 'ап. Петра и Павла')],
 'платон': [('18.11', 'мч. Платона Анкирского')],
 'полина': [('23.07', 'мц. Аполлинарии')],
 'прасковья': [('26.07', 'мц. Параскевы Пятницы')],
 'прохор': [('09.04', 'прп. Прохора Лебедника'), ('28.01', 'прп. Прохора Печерского')],
 'пётр': [('12.07', 'ап. Петра и Павла')],
 'раиса': [('05.09', 'мц. Раисы Александрийской')],
 'регина': [('07.09', 'мц. Регины')],
 'роман': [('01.10', 'прп. Романа Сладкопевца'), ('08.08', 'мч. Романа')],
 'руслан': [('17.03', 'мч. Руслана')],
 'светлана': [('26.02', 'мц. Фотины (Светланы)')],
 'семён': [('03.02', 'прп. Симеона Богоприимца'), ('14.09', 'прп. Симеона Столпника')],
 'серафима': [('29.07', 'прмц. Серафимы')],
 'сергей': [('08.10', 'прп. Сергия Радонежского')],
 'сергий': [('08.10', 'прп. Сергия Радонежского')],
 'снежана': [('26.03', 'мц. Снежаны')],
 'софия': [('30.09', 'мц. Софии')],
 'степан': [('09.01', 'архидиак. Стефана первомученика')],
 'стефан': [('09.01', 'архидиак. Стефана первомученика')],
 'тамара': [('01.05', 'блгв. царицы Тамары Грузинской')],
 'татьяна': [('25.01', 'мц. Татианы')],
 'тимофей': [('04.02', 'ап. Тимофея'), ('22.01', 'прп. Тимофея')],
 'тихон': [('29.06', 'свт. Тихона Амафунтского'), ('09.10', 'свт. Тихона Задонского')],
 'трофим': [('19.09', 'мч. Трофима'), ('23.07', 'мч. Трофима')],
 'ульяна': [('15.01', 'мц. Иулиании Никомидийской')],
 'федор': [('08.03', 'вмч. Феодора Тирона'), ('09.06', 'прп. Феодора Освященного')],
 'феодор': [('08.03', 'вмч. Феодора Тирона'), ('09.06', 'прп. Феодора Освященного')],
 'феодосий': [('11.01', 'прп. Феодосия Великого'), ('03.05', 'прп. Феодосия Печерского')],
 'филипп': [('27.11', 'ап. Филиппа'), ('22.01', 'свт. Филиппа Московского')],
 'фома': [('19.10', 'ап. Фомы')],
 'фёдор': [('08.03', 'вмч. Феодора Тирона'), ('09.06', 'прп. Феодора Освященного')],
 'харитина': [('05.10', 'мц. Харитины')],
 'христина': [('24.07', 'вмц. Христины Тирской')],
 'юлия': [('29.07', 'мц. Иулии')],
 'яков': [('05.11', 'ап. Иакова Зеведеева'), ('13.01', 'прп. Иакова Постника')],
 'яна': [('24.06', 'мц. Иоанны')]}


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


MONTHS_RU = {
    1: "января", 2: "февраля", 3: "марта", 4: "апреля",
    5: "мая", 6: "июня", 7: "июля", 8: "августа",
    9: "сентября", 10: "октября", 11: "ноября", 12: "декабря"
}

def date_ru(fmt="full"):
    """Возвращает дату на русском языке"""
    now = datetime.now()
    month = MONTHS_RU[now.month]
    if fmt == "full":
        return f"{now.day} {month} {now.year}"
    elif fmt == "short":
        return f"{now.day} {month}"
    return f"{now.day}.{now.month:02d}"

def get_todays_saints():
    today = datetime.now().strftime("%d.%m")
    result = []
    for name, days in SAINTS_BY_NAME.items():
        for day_str, saint in days:
            if day_str == today:
                result.append((name.capitalize(), saint))
    return result

def get_todays_feast():
    today = datetime.now().strftime("%d.%m")
    return FIXED_FEASTS.get(today, "")


def db_connect():
    """SQLite connection tuned for two concurrently running bot processes."""
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def create_database_backup(prefix: str = "vera_max") -> str:
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


def backup_status_text(prefix: str = "vera_max") -> str:
    backup_dir = Path(BACKUP_DIR)
    files = sorted(backup_dir.glob(f"{prefix}_*.db"), key=lambda p: p.stat().st_mtime, reverse=True) if backup_dir.exists() else []
    if not files:
        return "Резервных копий пока нет."
    latest = files[0]
    return f"Последняя копия: {latest.name}\nРазмер: {latest.stat().st_size // 1024} КБ\nВсего сохранено: {len(files)}"


async def database_backup_loop(prefix: str = "vera_max"):
    await asyncio.sleep(90)
    while True:
        try:
            await asyncio.to_thread(create_database_backup, prefix)
            logging.info("Резервная копия базы MAX создана")
        except Exception as e:
            logging.error(f"Ошибка резервного копирования MAX: {e}")
            record_critical_error("backup_max", e)
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

# ========== БАЗА ДАННЫХ ==========
def init_db():
    conn = db_connect()
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT DEFAULT '',
        first_name TEXT DEFAULT '',
        step TEXT DEFAULT 'idle',
        church_name TEXT DEFAULT '',
        birth_date TEXT DEFAULT '',
        angel_day TEXT DEFAULT '',
        onboarded INTEGER DEFAULT 0,
        notifications INTEGER DEFAULT 0,
        remind_days INTEGER DEFAULT 3
    )""")
    for col in ["notifications INTEGER DEFAULT 0", "remind_days INTEGER DEFAULT 3"]:
        try:
            c.execute(f"ALTER TABLE users ADD COLUMN {col}")
            conn.commit()
        except Exception:
            pass
    c.execute("""CREATE TABLE IF NOT EXISTS daily_prayer_cache (
        date TEXT PRIMARY KEY,
        prayer TEXT
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS favorites (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        title TEXT,
        content TEXT,
        created_at TEXT
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS patron_prayers_cache (
        name TEXT PRIMARY KEY,
        prayer TEXT
    )""")
    # Журнал публикаций канала: переживает перезапуск и не допускает дублей.
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
    # Переходы из канала в бот по источникам.
    c.execute("""CREATE TABLE IF NOT EXISTS channel_clicks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        source TEXT NOT NULL,
        target TEXT NOT NULL,
        clicked_at TEXT NOT NULL
    )""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_channel_clicks_source ON channel_clicks(source)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_channel_posts_date ON channel_posts(post_date)")
    # Отзывы и ответы владельца.
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
    c.execute("CREATE INDEX IF NOT EXISTS idx_user_reviews_status ON user_reviews(status)")
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
    c.execute("CREATE INDEX IF NOT EXISTS idx_donation_payment_status ON donation_payments(status)")
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
    "prayers": "prayer", "prayer_of_day": "prayer", "prayer_for_me": "prayer", "prayer_evening_ru": "prayer", "saints": "saint", "saint_search": "saint",
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




def _source_base(source: str) -> str:
    """Нормализует уникальную метку поста до базовой рубрики."""
    return (source or "").split("__", 1)[0]


def resolve_funnel_source(user_id: int, platform: str, max_age_days: int = 30) -> str:
    """Возвращает последнюю достоверную метку канала для последующей конверсии."""
    try:
        conn = db_connect()
        row = conn.execute(
            """SELECT last_source,last_source_at,first_source
               FROM user_funnel_state WHERE user_id=? AND platform=?""",
            (int(user_id), platform),
        ).fetchone()
        conn.close()
        if not row:
            return ""
        last_source, last_source_at, first_source = row
        if last_source and last_source_at:
            try:
                age = datetime.now() - datetime.fromisoformat(last_source_at)
                if age.total_seconds() <= max_age_days * 86400:
                    return str(last_source)
            except Exception:
                pass
        return str(first_source or "")
    except Exception as e:
        logging.error(f"Funnel attribution read error: {e}")
        return ""


def track_attributed_event(
    user_id: int,
    platform: str,
    event_name: str,
    target: str = "",
    value: str = "",
    metadata: str = "",
) -> None:
    source = resolve_funnel_source(user_id, platform)
    track_funnel_event(
        user_id, platform, event_name,
        source=source, target=target, value=value, metadata=metadata,
    )


def funnel_source_report_text(platform: str, days: int = 30) -> str:
    """Сквозной отчёт по источникам: клик → активация → возврат → отзыв → пожертвование."""
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()
    try:
        conn = db_connect()
        rows = conn.execute(
            """SELECT
                   CASE WHEN instr(source,'__')>0 THEN substr(source,1,instr(source,'__')-1) ELSE source END AS src,
                   COUNT(DISTINCT CASE WHEN event_name='channel_click' THEN user_id END) AS clicks,
                   COUNT(DISTINCT CASE WHEN event_name='activated' THEN user_id END) AS activated,
                   COUNT(DISTINCT CASE WHEN event_name='return_d7' THEN user_id END) AS returned_d7,
                   COUNT(DISTINCT CASE WHEN event_name='review_submitted' THEN user_id END) AS reviews,
                   COUNT(DISTINCT CASE WHEN event_name='donation_succeeded' THEN user_id END) AS donors,
                   COALESCE(SUM(CASE WHEN event_name='donation_succeeded' THEN CAST(value AS INTEGER) ELSE 0 END),0) AS revenue
               FROM funnel_events
               WHERE platform=? AND created_at>=? AND COALESCE(source,'')<>''
               GROUP BY src
               ORDER BY activated DESC, clicks DESC
               LIMIT 20""",
            (platform, cutoff),
        ).fetchall()
        conn.close()
        if not rows:
            return f"📊 Источники воронки — {platform}, {days} дней\n\nДанных пока недостаточно."
        lines = [f"📊 Источники воронки — {platform}, {days} дней", ""]
        for src, clicks, activated, returned_d7, reviews, donors, revenue in rows:
            clicks = int(clicks or 0); activated = int(activated or 0)
            rate = activated / clicks * 100 if clicks else 0
            lines.append(
                f"• {src}: {clicks} → {activated} ({rate:.1f}%) | "
                f"D7 {int(returned_d7 or 0)} | отзывы {int(reviews or 0)} | "
                f"доноры {int(donors or 0)} / {int(revenue or 0)} ₽"
            )
        lines += ["", "Путь: уникальный переход → первое полезное действие → D7 → отзыв → пожертвование."]
        return "\n".join(lines)
    except Exception as e:
        logging.error(f"Funnel source report error: {e}")
        return f"⚠️ Не удалось построить сквозной отчёт: {e}"


def touch_funnel_user(user_id: int, platform: str, source: str = "", target: str = "", increment_visit: bool = True):
    now = datetime.now()
    referral_code = f"ref_{int(user_id)}"
    try:
        touch_user_session(user_id, platform, source, target)
        conn = db_connect()
        row = conn.execute("SELECT visit_count,first_source,first_target,first_seen_at,last_source,last_target FROM user_funnel_state WHERE user_id=? AND platform=?", (int(user_id), platform)).fetchone()
        if row:
            conn.execute(
                """UPDATE user_funnel_state SET last_seen_at=?,visit_count=visit_count+?,
                   last_source=CASE WHEN ?<>'' THEN ? ELSE last_source END,
                   last_target=CASE WHEN ?<>'' THEN ? ELSE last_target END,
                   last_source_at=CASE WHEN ?<>'' THEN ? ELSE last_source_at END
                   WHERE user_id=? AND platform=?""",
                (now.isoformat(), 1 if increment_visit else 0, source, source, target, target, source, now.isoformat(), int(user_id), platform),
            )
            if increment_visit and row[3]:
                age_days = (now - datetime.fromisoformat(row[3])).days
                for threshold in (1, 3, 7):
                    if age_days >= threshold:
                        event_name = f"return_d{threshold}"
                        exists = conn.execute("SELECT 1 FROM funnel_events WHERE user_id=? AND platform=? AND event_name=? LIMIT 1", (int(user_id), platform, event_name)).fetchone()
                        if not exists:
                            conn.execute("INSERT INTO funnel_events(user_id,platform,event_name,source,target,created_at) VALUES (?,?,?,?,?,?)", (int(user_id), platform, event_name, (source or (row[4] if len(row) > 4 else row[1]) or ""), (target or (row[5] if len(row) > 5 else row[2]) or ""), now.isoformat()))
        else:
            conn.execute(
                """INSERT INTO user_funnel_state(user_id,platform,first_source,first_target,first_seen_at,last_seen_at,visit_count,referral_code,last_source,last_target,last_source_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (int(user_id), platform, source or "", target or "", now.isoformat(), now.isoformat(), 1 if increment_visit else 0, referral_code, source or "", target or "", now.isoformat() if source else ""),
            )
        profile = conn.execute("SELECT church_name,birth_date,notifications FROM users WHERE user_id=?", (int(user_id),)).fetchone()
        if profile:
            conn.execute("UPDATE user_funnel_state SET profile_completed=?,notifications_enabled=? WHERE user_id=? AND platform=?", (1 if (profile[0] or profile[1]) else 0, int(profile[2] or 0), int(user_id), platform))
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
    touch_funnel_user(user_id, platform, source, action, increment_visit=False)
    now = datetime.now()
    referrer_id = 0
    try:
        conn = db_connect()
        row = conn.execute("SELECT useful_actions,activated_at,first_source,last_source,last_source_at FROM user_funnel_state WHERE user_id=? AND platform=?", (int(user_id), platform)).fetchone()
        if not source and row:
            source = row[2] or ""
            if row[3] and row[4]:
                try:
                    if (now - datetime.fromisoformat(row[4])).total_seconds() <= 7 * 86400:
                        source = row[3]
                except Exception:
                    pass
        first_activation = bool(row and int(row[0] or 0) == 0)
        conn.execute("UPDATE user_funnel_state SET useful_actions=useful_actions+1,activated_at=CASE WHEN activated_at='' THEN ? ELSE activated_at END,last_seen_at=? WHERE user_id=? AND platform=?", (now.isoformat(), now.isoformat(), int(user_id), platform))
        if first_activation:
            ref = conn.execute("SELECT referrer_id,status FROM referrals WHERE platform=? AND referred_user_id=?", (platform, int(user_id))).fetchone()
            if ref and ref[1] != "activated":
                referrer_id = int(ref[0]); conn.execute("UPDATE referrals SET status='activated',activated_at=? WHERE platform=? AND referred_user_id=?", (now.isoformat(), platform, int(user_id)))
        conn.commit(); conn.close()
        track_funnel_event(user_id, platform, "useful_action", source=source, target=action)
        if first_activation:
            track_funnel_event(user_id, platform, "activated", source=source, target=action)
        return referrer_id
    except Exception as e:
        logging.error(f"Useful action error: {e}")
        return 0



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
    conn = db_connect()
    conn.execute("INSERT OR REPLACE INTO topic_votes(platform,week_key,user_id,topic,updated_at) VALUES (?,?,?,?,?)", (platform, week_key, int(user_id), topic, datetime.now().isoformat()))
    conn.commit(); conn.close()
    track_funnel_event(user_id, platform, "interactive_vote", value=topic)

def top_interactive_topic(platform: str) -> str:
    try:
        week_key = datetime.now().strftime("%G-W%V")
        conn = db_connect()
        row = conn.execute("SELECT topic,COUNT(*) FROM topic_votes WHERE platform=? AND week_key=? GROUP BY topic ORDER BY COUNT(*) DESC,topic LIMIT 1", (platform, week_key)).fetchone()
        conn.close(); return row[0] if row else ""
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
        conn = db_connect()
        row = conn.execute("SELECT id,review_text FROM user_reviews WHERE publish_consent=1 AND public_approved=1 AND COALESCE(published_at,'')='' ORDER BY id LIMIT 1").fetchone()
        if not row:
            conn.close(); return ""
        conn.execute("UPDATE user_reviews SET published_at=? WHERE id=?", (datetime.now().isoformat(), int(row[0])))
        conn.commit(); conn.close()
        return (row[1] or "").strip()[:700]
    except Exception as e:
        logging.error(f"Public review read error: {e}"); return ""


def get_user(user_id, username="", first_name=""):
    conn = db_connect()
    c = conn.cursor()
    c.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
    row = c.fetchone()
    if not row:
        c.execute("INSERT INTO users (user_id,username,first_name,notifications) VALUES (?,?,?,0)",
                  (user_id, username, first_name))
        conn.commit()
        c.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
        row = c.fetchone()
    conn.close()
    cols = ["user_id","username","first_name","step","church_name","birth_date","angel_day","onboarded","notifications","remind_days"]
    return dict(zip(cols, row))

def set_step(user_id, step):
    conn = db_connect()
    conn.execute("UPDATE users SET step=? WHERE user_id=?", (step, user_id))
    conn.commit()
    conn.close()

def save_favorite(user_id, title, content):
    conn = db_connect()
    conn.execute("INSERT INTO favorites (user_id,title,content,created_at) VALUES (?,?,?,?)",
                 (user_id, title, content[:500], datetime.now().strftime("%d.%m.%Y")))
    conn.commit()
    conn.close()

def get_favorites(user_id):
    conn = db_connect()
    c = conn.cursor()
    c.execute("SELECT title, content FROM favorites WHERE user_id=? ORDER BY id DESC LIMIT 10", (user_id,))
    rows = c.fetchall()
    conn.close()
    return rows


def create_review_record(chat_id, user_id, username, first_name, review_text):
    """Сохраняет отзыв локально и возвращает его уникальный номер."""
    conn = db_connect()
    c = conn.cursor()
    c.execute(
        """INSERT INTO user_reviews
           (user_id, chat_id, username, first_name, review_text, status, created_at)
           VALUES (?, ?, ?, ?, ?, 'new', ?)""",
        (
            int(user_id),
            int(chat_id),
            username or "",
            first_name or "",
            review_text.strip(),
            datetime.now().isoformat(timespec="seconds")
        )
    )
    review_id = c.lastrowid
    conn.commit()
    conn.close()
    return review_id


def get_review_record(review_id):
    conn = db_connect()
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM user_reviews WHERE id=?",
        (int(review_id),)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def update_review_record(review_id, status, owner_reply="", handled_by="Владелец"):
    replied_at = datetime.now().isoformat(timespec="seconds")
    conn = db_connect()
    conn.execute(
        """UPDATE user_reviews
           SET status=?, owner_reply=?, replied_at=?, handled_by=?
           WHERE id=?""",
        (
            status,
            owner_reply.strip(),
            replied_at,
            handled_by,
            int(review_id)
        )
    )
    conn.commit()
    conn.close()
    return replied_at

# ========== AI ==========
async def ask_claude(question, depth="medium"):
    depths = {
        "short": ("Ответь кратко — 2–3 предложения.", 300),
        "medium": ("Ответь развёрнуто — 5–7 предложений.", 650),
        "deep": ("Дай вдумчивый ответ с ясным различением фактов и пастырского совета.", 1100),
    }
    system_add, max_tok = depths.get(depth, depths["medium"])
    try:
        msg = await asyncio.to_thread(
            claude_client.messages.create,
            model="claude-sonnet-4-5",
            max_tokens=max_tok,
            system=(
                "Ты справочный православный помощник, но не священник. "
                "Обращайся нейтрально и уважительно, без слов «чадо», «душа моя» и без имитации духовника. "
                "Не выдумывай цитаты, церковные правила, даты, чудеса или благословения. "
                "Если точный факт не дан в контексте, прямо скажи, что его нужно проверить по надёжному церковному источнику. "
                "В вопросах Таинств, поста по здоровью, тяжёлых решений и духовного руководства советуй обратиться к священнику своего прихода. "
                "Отвечай по-русски, бережно, без осуждения. " + system_add
            ),
            messages=[{"role": "user", "content": question}],
        )
        return msg.content[0].text
    except Exception as e:
        logging.error(f"Ошибка Claude: {e}")
        record_critical_error("claude_max", e)
        return "error"


async def transcribe_voice_max(audio_url: str) -> str:
    """Downloads a voice message into a unique temporary file and always removes it."""
    tmp_path = Path(f"/tmp/vera_voice_max_{uuid.uuid4().hex}.ogg")
    try:
        headers = {"Authorization": MAX_TOKEN}
        async with httpx.AsyncClient(timeout=35, follow_redirects=True) as client:
            r = await client.get(audio_url, headers=headers)
            r.raise_for_status()
            if len(r.content) < 100:
                raise RuntimeError("voice file is empty")
            tmp_path.write_bytes(r.content)
        with tmp_path.open("rb") as f:
            response = await openai_client.audio.transcriptions.create(model="whisper-1", file=f, language="ru")
        return (response.text or "").strip()
    except Exception as e:
        logging.error(f"Ошибка транскрибации: {e}")
        record_critical_error("voice_max", e)
        return ""
    finally:
        with suppress(Exception):
            tmp_path.unlink(missing_ok=True)


async def analyze_photo(photo_bytes, photo_type):
    if photo_type == "church":
        prompt = (
            "Выполни предварительное распознавание православного храма или монастыря по фотографии. "
            "Не утверждай название, если нет надёжных отличительных признаков. Дай: вероятные варианты, видимые признаки и способ проверки. "
            "Не выдумывай историю объекта. Ответь по-русски."
        )
    else:
        prompt = (
            "Выполни предварительное распознавание православного образа по фотографии. "
            "Не выдавай предположение за точное определение. Дай: кого или какой сюжет, вероятно, изображает и какие признаки видны. "
            "Не выдумывай житие, чудеса и название иконы; посоветуй сверить подпись на иконе или уточнить в храме. Ответь по-русски."
        )
    try:
        image_data = base64.b64encode(photo_bytes).decode("utf-8")
        response = await openai_client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_data}"}},
            ]}],
            max_tokens=700,
        )
        return "📸 Предварительное распознавание\n\n" + (response.choices[0].message.content or "")
    except Exception as e:
        logging.error(f"GPT vision ошибка: {e}")
        record_critical_error("vision_max", e)
        return "Не удалось выполнить предварительное распознавание. Попробуйте более чёткое фото или уточните образ в храме."


# ========== ТЕКСТЫ (краткие версии) ==========
PRAYER_TEXTS = {
    "prayer_morning_ru": ("🌅 Утренняя молитва", "Господи Иисусе Христе, Сыне Божий, молитв ради Пречистыя Твоея Матере и всех святых, помилуй нас. Аминь.\n\nСлава Тебе, Боже наш, слава Тебе.\n\nЦарю Небесный, Утешителю, Душе истины, Иже везде сый и вся исполняяй, Сокровище благих и жизни Подателю, прииди и вселися в ны, и очисти ны от всякия скверны, и спаси, Блаже, души наша."),
    "prayer_evening_ru": ("🌙 Вечерняя молитва", "Боже вечный и Царю всякого создания, сподобивый мя даже в час сей доспети, прости мне грехи, яже сотворих в сей день делом, словом и помышлением, и очисти, Господи, смиренную мою душу от всякия скверны плоти и духа. И даждь ми, Господи, в нощи сей сон прейти в мире, да восстав от смиренного ми ложа, благоугожду Пресвятому Имени Твоему, во вся дни живота моего. Аминь."),
    "prayer_otche": ("🙏 Отче наш", "Отче наш, Иже еси на небесех!\nДа святится имя Твое,\nда приидет Царствие Твое,\nда будет воля Твоя,\nяко на небеси и на земли.\nХлеб наш насущный даждь нам днесь;\nи остави нам долги наша,\nякоже и мы оставляем должником нашим;\nи не введи нас во искушение,\nно избави нас от лукаваго.\nАминь."),
    "prayer_bogorodice": ("🌸 Богородице Дево", "Богородице Дево, радуйся,\nБлагодатная Марие, Господь с Тобою;\nблагословена Ты в женах\nи благословен плод чрева Твоего,\nяко Спаса родила еси душ наших."),
    "prayer_jesus": ("📿 Иисусова молитва", "Господи Иисусе Христе, Сыне Божий, помилуй мя, грешного.\n\nЭта краткая молитва — сердце православной молитвенной практики. Повторяйте её в течение дня, особенно в трудные моменты."),
    "prayer_symbol": ("✝️ Символ веры", "Верую во единого Бога Отца, Вседержителя, Творца небу и земли, видимым же всем и невидимым.\nИ во единого Господа Иисуса Христа, Сына Божия, Единородного, Иже от Отца рожденного прежде всех век...\nИ в Духа Святаго, Господа, Животворящего, Иже от Отца исходящего...\nВо едину Святую, Соборную и Апостольскую Церковь.\nИсповедую едино крещение во оставление грехов.\nЧаю воскресения мертвых, и жизни будущего века. Аминь."),
    "prayer_before_meal": ("🍽️ Молитва перед едой", "Отче наш, Иже еси на небесех!\nДа святится имя Твое,\nда приидет Царствие Твое...\n\nИли краткая: Господи, благослови!\n\nОчи всех на Тя, Господи, уповают, и Ты даеши им пищу во благовремении, отверзаеши Ты щедрую руку Твою и исполняеши всякое животно благоволения. Аминь."),
    "prayer_nikolay": ("🕯️ Молитва Николаю Чудотворцу", "О, всесвятый Николае, угодниче преизрядный Господень, теплый наш заступниче и везде в скорбех скорый помощниче! Помози мне грешному и унылому в настоящем сем житии, умоли Господа Бога даровати ми оставление всех моих грехов. Аминь."),
    "prayer_matrona": ("🌺 Молитва Матроне Московской", "О, блаженная мати Матроно, услыши и приими ныне нас, грешных, молящихся тебе, научена еси от Господа Бога и имея дерзновение к Нему, моли Его, да сотворит и нам, грешным, милость Свою. Аминь."),
    "prayer_prichaschenie": ("✝️ Правило ко Причастию", "Верую, Господи, и исповедую, яко Ты еси воистину Христос, Сын Бога живаго, пришедый в мир грешныя спасти, от нихже первый есмь аз.\n\nПравило ко Причастию состоит из:\n— Канона покаянного\n— Канона Богородице\n— Канона Ангелу Хранителю\n— Последования ко Святому Причащению\n\nЧитается вечером накануне Причастия."),
    "prayer_upokoenie": ("🕯️ Молитва об упокоении", "Упокой, Господи, душу усопшего раба Твоего (имя), и прости ему вся согрешения его вольная и невольная, и даруй ему Царствие Небесное.\n\nПомяни, Господи Боже наш, в вере и надежди живота вечного преставившагося раба Твоего (имя), и яко Благ и Человеколюбец, отпущаяй грехи и потребляяй неправды, ослаби, остави и прости вся вольная его согрешения и невольная. Аминь."),
    "prayer_morning_cs": ("🌅 Утренняя (церковнославянский)", "Востав от сна, прежде всякого другого дела, стани благоговейно, представ пред Всевидящим Богом, и сотвори крестное знамение, глаголя:\n\nВо имя Отца и Сына и Святаго Духа. Аминь.\n\nГосподи Иисусе Христе, Сыне Божий, молитв ради Пречистыя Твоея Матере и всех святых, помилуй нас. Аминь."),
    "prayer_evening_cs": ("🌙 Вечерняя (церковнославянский)", "Боже вечный и Царю всякого создания, сподобивый мя даже в час сей доспети, прости мне грехи, яже сотворих в сей день делом, словом и помышлением. Аминь."),
    "prayer_pokayanny_kanon": ("📖 Канон покаянный", "Канон покаянный ко Господу нашему Иисусу Христу читается при подготовке к Исповеди и Причастию.\n\nПеснь 1:\nПомилуй мя, Боже, помилуй мя.\n\nЯко пуст есмь, к Тебе прибегаю, Богу и Творцу моему. Умными очесы предстоя Тебе вопию: даждь ми, Господи, благодать Твою...\n\nПолный текст канона содержит 9 песней с тропарями и читается около 20 минут."),
}

SACRAMENT_TEXTS = {
    "ispoved": ("📿 Исповедь — полный путь",
        "☦️ Исповедь — разговор с Богом в присутствии священника.\n"
        "Бояться не нужно — батюшка всё слышал и никогда не осудит.\n\n"
        "📅 КАК ПОДГОТОВИТЬСЯ:\n\n"
        "За несколько дней:\n— Вспоминайте грехи и записывайте на бумагу\n— Читайте утренние и вечерние молитвы\n— Попросите прощения у тех кого обидели\n\n"
        "⚠️ Пост перед исповедью не обязателен —\n"
        "пост установлен перед Причастием, а не перед исповедью.\n\n"
        "Накануне (по желанию):\n— Прочитайте Канон покаянный (около 20 минут)\n\n"
        "Утром:\n— Прочитайте утренние молитвы\n— Уточните время исповеди в вашем храме заранее\n\n"
        "📝 Можно написать грехи на листочке — батюшка прочитает сам.\n\n"
        "⛪ КАК ПРОХОДИТ:\n1. Подойдите к аналою\n2. Священник спросит имя\n3. Расскажите грехи тихо\n4. Священник накроет голову епитрахилью\n5. Прочитает разрешительную молитву\n6. Целуете крест и Евангелие\n\nПосле исповеди — путь к Причастию."),
    "prichaschenie": ("✝️ Причастие — полный путь",
        "☦️ Причастие — главное Таинство православной Церкви.\n\n"
        "📅 ПУТЬ ПОДГОТОВКИ:\n\nНачните с Исповеди.\n\n"
        "За 3 дня:\n— Пост (мясо, рыба, молочное, яйца — исключить)\n— Утренние и вечерние молитвы\n\n"
        "Вечером накануне:\n— Канон покаянный\n— Канон Богородице\n— Канон Ангелу Хранителю\n— Последование ко Причастию\n(около 1.5 часов)\n\n"
        "С полуночи: не есть и не пить.\n\n"
        "⛪ КАК ПРОХОДИТ:\n1. Придите к началу Литургии\n2. Сложите руки крестом (правая поверх)\n3. Назовите имя священнику\n4. Откройте рот — священник даст ложечку\n5. Не касайтесь Чаши руками\n6. Поцелуйте край Чаши\n7. Запейте теплотой"),
    "kreshchenie": ("💧 Крещение — полный путь",
        "☦️ Крещение — вхождение в Церковь Христову.\n\n"
        "Для крёстных — подготовка:\n— Выучите Символ Веры и Отче наш\n— Читайте утренние и вечерние молитвы\n\n"
        "Накануне (уточните у священника):\n— Во многих храмах крёстные проходят Исповедь и Причастие\n— Некоторые приходы рекомендуют пост за 1-3 дня\n— Требования различаются — уточните у батюшки\n\n"
        "Что взять:\n— Крестильная рубашка\n— Нательный крестик\n— Крыжма (белое полотенце)\n\n"
        "Достаточно одного крёстного:\nДля мальчика — крёстный отец\nДля девочки — крёстная мать\nКрёстные должны быть желательно православными.\n\n"
        "⛪ КАК ПРОХОДИТ:\n1. Чтение молитв, Символа Веры\n2. Тройное погружение в купель\n3. Миропомазание\n4. Надевается крестик\n5. Младенцев сразу причащают"),
    "venchanie": ("💍 Венчание — полный путь",
        "☦️ Венчание — благословение союза Богом.\n\n"
        "За 3 дня — оба супруга:\n— Пост\n— Молитвы\n— Воздержание\n\nНакануне:\n— Оба проходят Исповедь\n\nУтром в день венчания:\n— Оба причащаются на Литургии\n\n"
        "Большинство храмов венчают после регистрации в ЗАГСе — уточните в вашем приходе.\n\n"
        "Что взять:\n— Обручальные кольца\n— Венчальные свечи\n— Рушник\n— Иконы Спасителя и Богородицы\n\n"
        "⛪ КАК ПРОХОДИТ:\n1. Обручение — обмен кольцами\n2. Жених и невеста встают на рушник\n3. Священник возлагает венцы\n4. Чтение Евангелия и молитвы\n5. Общая чаша — символ единства\n6. Троекратный обход вокруг аналоя"),
    "otpevanie": ("🕯️ Отпевание",
        "☦️ Отпевание — проводы в вечную жизнь.\n\n"
        "Где проводится:\n— В храме\n— В ритуальном зале\n— Дома (священник приедет)\n— На кладбище\n\n"
        "Что взять:\n— Свечи\n— Икона\n— Погребальное покрывало\n— Венчик (выдаётся в храме)\n\n"
        "⛪ КАК ПРОХОДИТ:\n1. Гроб ставится лицом к алтарю\n2. Священник читает молитвы и Евангелие\n3. Поётся «Со святыми упокой»\n4. Разрешительная молитва в руку\n5. Все прощаются\n6. Гроб закрывается\n\n"
        "Поминовение:\n— 3й день\n— 9й день\n— 40й день — закажите панихиду"),
    "sobor": ("🫒 Соборование",
        "☦️ Соборование — Таинство исцеления.\nЭто НЕ последнее причастие!\n\n"
        "Кому нужно:\n— Тяжелобольным\n— Перед операцией\n— Всем православным в Великий пост\n— Пожилым — раз в год\n\n"
        "Подготовка:\n— Пост по возможности\n— Исповедь перед Собором\n\n"
        "⛪ КАК ПРОХОДИТ:\n1. Священник читает молитвы и Евангелие\n2. Освящается елей с вином\n3. Семь помазаний маслом\n4. Евангелие на голову\n5. Разрешительная молитва\n\nПосле — желательно Причастие."),
    "osvyashchenie": ("🏠 Освящение",
        "☦️ Освящение — благословение Церкви.\n\n"
        "🏠 Жилья:\n— Пригласите священника\n— Подготовьте свечи, икону, крещенскую воду\n— Священник обходит все комнаты с молитвами\n\n"
        "🚗 Автомобиля:\n— Подъедьте к храму или вызовите батюшку\n— Священник читает молитву и кропит машину\n\n"
        "✝️ Вещей:\n— Крестики, иконы — в любом храме\n— Попросите священника после службы"),
    "svecha": ("🕯️ Как ставить свечи",
        "☦️ Свеча — символ нашей молитвы.\n\n"
        "Иисусу Христу:\n— О здравии, с благодарностью\n\n"
        "Богородице:\n— О детях, семье, в скорбях\n\n"
        "Николаю Чудотворцу:\n— В дороге, о помощи в делах\n\n"
        "Пантелеимону:\n— О здоровье и исцелении\n\n"
        "На канун (прямоугольный подсвечник):\n— За упокой усопших\n\n"
        "Правила:\n— Зажигают от другой свечи\n— Ставят прямо в гнездо\n— Размер не важен — важна молитва"),
    "zapiska": ("📝 Как подавать записки",
        "☦️ Записки — молитвенное поминовение на Литургии.\n\n"
        "Как писать:\n— Вверху: «О здравии» или «О упокоении»\n— Крупно и разборчиво\n— Только крещёные православные имена\n— Не более 10 имён\n— Без фамилий и отчеств\n\n"
        "Виды:\n— Простая — один раз\n— Сорокоуст — 40 дней\n— Годовое поминовение\n\n"
        "Нельзя подавать записки о некрещёных."),
    "v_hrame": ("⛪ Как вести себя в храме",
        "☦️ Храм — дом Божий.\n\n"
        "Одежда:\nЖенщины: платок, юбка ниже колена, закрытые плечи\nМужчины: без головного убора, без шорт\n\n"
        "Вход:\n— Трижды перекреститесь с поклоном\n— Войдите тихо\n— Телефон на беззвучный\n\n"
        "Как креститься:\n— Правой рукой\n— Три пальца вместе\n— Лоб → живот → правое плечо → левое\n\n"
        "В храме:\n— Говорите тихо\n— Во время Литургии — стойте\n— Можно сидеть (пожилым — всегда)\n— Не стойте спиной к алтарю"),
}

HOLY_PLACES_TEXTS = {
    "moscow": ("🏛️ Москва — святые места",
        "🕍 Храм Христа Спасителя\nГлавный собор России. Восстановлен в 1990-х.\n\n"
        "🌺 Покровский монастырь (Матрона Московская)\nМощи блж. Матроны Московской.\nОчереди круглый год — приходите с утра.\n\n"
        "🕍 Данилов монастырь\nРезиденция Патриарха. Основан в XIII веке.\n\n"
        "🕍 Новодевичий монастырь\nОбъект ЮНЕСКО. Основан в 1524 г.\n\n"
        "🕍 Донской монастырь\nМощи святителя Тихона, Патриарха Московского.\n\n"
        "🗺️ Как найти: Яндекс.Карты → название монастыря"),
    "spb": ("🏛️ Санкт-Петербург — святые места",
        "⛪ Казанский собор\nЧудотворная Казанская икона Богородицы.\nЗдесь похоронен Кутузов.\n\n"
        "⛪ Исаакиевский собор\nОдин из крупнейших соборов мира.\n\n"
        "🕍 Александро-Невская Лавра\nМощи блгв. кн. Александра Невского.\nДействующий монастырь в центре города.\n\n"
        "🌸 Иоанновский монастырь\nМощи праведного Иоанна Кронштадтского.\n\n"
        "🕍 Смольный собор\nШедевр Растрелли. Действующий храм.\n\n"
        "🗺️ Как найти: Яндекс.Карты → название"),
    "afon": ("⛵ Святая гора Афон",
        "Афон — особый монашеский удел Богородицы. Полуостров в Греции, 20 православных монастырей.\n\n"
        "🏛️ Главные монастыри:\n— Великая Лавра (963 г.)\n— Ватопед (Пояс Богородицы)\n— Иверский (Иверская икона)\n— Пантелеимонов (русский)\n\n"
        "👨 Как попасть:\n— Только мужчины\n— Нужен диамонитирион (разрешение)\n— Заявка через Паломническое бюро\n— Срок ожидания: 6-12 месяцев\n\n"
        "Как добраться: Афины → Салоники → Уранополис → паром"),
    "podmoskove": ("📍 Монастыри Подмосковья",
        "⭐ Троице-Сергиева Лавра (Сергиев Посад)\n"
        "Главная обитель России. Основана прп. Сергием Радонежским в 1337 г.\n"
        "Мощи: прп. Сергия Радонежского\n"
        "Как добраться: экспресс с Ярославского вокзала (1 ч 10 мин)\n\n"
        "🕍 Саввино-Сторожевский монастырь (Звенигород)\n"
        "Любимый монастырь русских царей. Основан в 1398 г.\n"
        "Мощи: прп. Саввы Сторожевского\n\n"
        "🕍 Николо-Угрешский монастырь (Дзержинский)\n"
        "Основан Дмитрием Донским в 1380 г. после Куликовской битвы.\n\n"
        "🕍 Давидова Пустынь (Чехов)\n"
        "Древняя обитель 1515 г. Тихое место для молитвы.\n\n"
        "🗺️ Как найти: Яндекс.Карты → название монастыря"),
    "central": ("📍 Монастыри Центральной России",
        "🌸 Серафимо-Дивеевский монастырь (Нижегородская обл.)\n"
        "Четвёртый удел Богородицы. Мощи: прп. Серафима Саровского.\n"
        "Канавка Богородицы — главная святыня обители.\n"
        "Как добраться: поезд до Арзамаса, автобус до Дивеево\n\n"
        "🌿 Оптина Пустынь (Козельск, Калужская обл.)\n"
        "Место великих старцев. Мощи: Оптинских старцев.\n"
        "Как добраться: поезд до Калуги, автобус до Козельска\n\n"
        "🕍 Шамординский монастырь (рядом с Оптиной)\n"
        "Женский монастырь основан старцем Амвросием Оптинским в 1884 г.\n\n"
        "🕍 Санаксарский монастырь (Мордовия)\n"
        "Мощи праведного Феодора Ушакова — великого адмирала и святого воина.\n\n"
        "🗺️ Как найти: Яндекс.Карты → название монастыря"),
    "northwest": ("📍 Монастыри Севера и Северо-Запада",
        "🏰 Псково-Печерский монастырь (Псковская обл.)\n"
        "Единственный монастырь России который никогда не закрывался.\n"
        "Старцы: Иоанн Крестьянкин, Николай Гурьянов.\n"
        "Как добраться: поезд до Пскова, автобус до Печор\n\n"
        "⛵ Валаам (Республика Карелия)\n"
        "Остров-монастырь на Ладожском озере. «Северный Афон».\n"
        "Как добраться: теплоход из Сортавалы или Приозерска\n\n"
        "🏔️ Соловецкий монастырь (Архангельская обл.)\n"
        "Острова в Белом море. Основан в 1436 г.\n"
        "Мощи: прпмч. Зосимы и Савватия Соловецких\n\n"
        "🕍 Александро-Свирский монастырь (Ленинградская обл.)\n"
        "Нетленные мощи прп. Александра Свирского.\n"
        "Единственный русский святой которому явилась Святая Троица.\n\n"
        "🗺️ Как найти: Яндекс.Карты → название монастыря"),
    "ural_siberia": ("📍 Монастыри Урала и Сибири",
        "✝️ Ганина Яма (Екатеринбург)\n"
        "Место обретения останков Царской Семьи.\n"
        "7 храмов по числу членов семьи Николая II.\n"
        "Как добраться: автобус от Екатеринбурга (30 мин)\n\n"
        "🕍 Верхотурье (Свердловская обл.)\n"
        "Духовная столица Урала. Мощи прп. Симеона Верхотурского.\n"
        "Как добраться: поезд или автобус из Екатеринбурга\n\n"
        "🏔️ Белогорский монастырь (Пермский край)\n"
        "«Уральский Афон» — на высоте 446 м над уровнем моря.\n"
        "В советское время — место мученичества монахов.\n\n"
        "🕍 Знаменский монастырь (Иркутск)\n"
        "Мощи святителя Иннокентия Иркутского — первого сибирского святого.\n"
        "Старейший монастырь Иркутска (1693 г.)\n\n"
        "🗺️ Как найти: Яндекс.Карты → название монастыря"),
    "south": ("📍 Монастыри Юга и Крыма",
        "⛰️ Свято-Михайловский монастырь (Сочи, гора Физиабго)\n"
        "Высокогорный монастырь на высоте 600 м. Основан в 1878 г.\n"
        "Первый монастырь на Кавказе. Малоизвестен туристам.\n\n"
        "🌊 Свято-Георгиевский монастырь (Крым, мыс Фиолент)\n"
        "Один из древнейших — основан в IX веке. 800 ступеней к морю.\n"
        "Пушкин посещал монастырь в 1820 г.\n\n"
        "🕍 Инкерманский монастырь (Крым, Севастополь)\n"
        "Пещерный монастырь высеченный в скале. Основан в VIII-IX веке.\n"
        "Мощи: сщмч. Климента Римского\n\n"
        "🕍 Успенский монастырь (Крым, Бахчисарай)\n"
        "Пещерный монастырь в отвесной скале.\n"
        "Чудотворная икона Богородицы Панагия.\n\n"
        "🗺️ Как найти: Яндекс.Карты → название монастыря"),
    "abkhazia": ("✝️ Абхазия — святые места",
        "Абхазия — одно из древнейших христианских мест. Христианство здесь с I века.\n\n"
        "🕍 Новоафонский монастырь (Новый Афон)\n"
        "Основан в 1875 г. монахами со Святого Афона.\n"
        "Величественный комплекс у моря — шесть храмов.\n\n"
        "⛪ Храм Симона Кананита (Новый Афон)\n"
        "Один из древнейших — I-X века.\n"
        "Место мученичества апостола Симона Кананита.\n\n"
        "🕳️ Пещера апостола Симона Кананита\n"
        "Намоленное место удивительной тишины.\n\n"
        "🏛️ Бедийский собор (село Бедиа)\n"
        "Построен в X веке царём Багратом III.\n\n"
        "⛪ Храм мч. Василиска (село Команы)\n"
        "Место мученичества св. Василиска (ок. 308 г.) —\n"
        "племянника вмч. Феодора Тирона.\n"
        "Здесь также скончался свт. Иоанн Златоуст в 407 г.\n"
        "Особо почитаемое место паломничества в Абхазии.\n\n"
        "🗺️ Как найти: Яндекс.Карты → название"),
    "world": ("🌍 Святые места мира",
        "✝️ Иерусалим, Израиль\n"
        "— Храм Гроба Господня — место Распятия и Воскресения\n"
        "— Голгофа, Гефсиманский сад, Вифлеем, река Иордан\n\n"
        "🏛️ Рим, Италия\n"
        "— Базилика св. Петра — мощи ап. Петра\n"
        "— Базилика Сан-Паоло — мощи ап. Павла\n\n"
        "⭐ Бари, Италия\n"
        "— Базилика Святого Николая Чудотворца\n"
        "— Главное место русских православных паломников\n\n"
        "🇬🇷 Греция\n"
        "— Афон, Метеоры (ЮНЕСКО), о. Корфу (мощи свт. Спиридона)\n\n"
        "🕌 Турция\n"
        "— Собор Святой Софии (Стамбул)\n"
        "— Мира Ликийская — место служения Николая Чудотворца\n\n"
        "🇷🇸 Сербия / Черногория\n"
        "— Острог — пещерный монастырь, мощи свт. Василия\n\n"
        "🇧🇬 Болгария\n"
        "— Рильский монастырь — главная святыня Болгарии"),
}

LIBRARY_TEXTS = {
    "lib_slovar": ("📝 Церковный словарь",
        "📝 ЦЕРКОВНЫЙ СЛОВАРЬ\n\n"
        "⛪ Аналой — высокий столик с наклонной поверхностью для икон.\n\n"
        "📖 Акафист — хвалебное песнопение в честь Христа, Богородицы или святого.\n\n"
        "🫒 Елей — освящённое масло. Используется в Таинствах.\n\n"
        "🧣 Епитрахиль — длинная лента священника на шее. Символ благодати.\n\n"
        "🏛️ Иконостас — перегородка из икон между алтарём и храмом.\n\n"
        "🕯️ Канон — богослужебное произведение из 9 песней.\n\n"
        "⛪ Канун — прямоугольный подсвечник за упокой.\n\n"
        "🎵 Литургия — главное богослужение Церкви.\n\n"
        "🧴 Миро — освящённое масло для Таинства Миропомазания.\n\n"
        "🍞 Просфора — круглый хлеб из которого вынимаются частицы на Литургии.\n\n"
        "🎵 Тропарь — краткое песнопение о сути праздника.\n\n"
        "✝️ Царские врата — центральные двери иконостаса."),
    "lib_faq": ("❓ Частые вопросы о вере",
        "❓ ЧАСТЫЕ ВОПРОСЫ О ВЕРЕ\n\n"
        "Можно ли креститься в любом возрасте?\nДа. Крещение совершается над людьми любого возраста.\n\n"
        "Обязательно ли ходить в церковь?\nПравославная жизнь невозможна без Церкви — Таинства совершаются только в храме.\n\n"
        "Что делать если не понимаю службу?\nЭто нормально. Читайте «Закон Божий» — понимание придёт.\n\n"
        "Можно ли молиться своими словами?\nДа — Господь слышит молитву сердца.\n\n"
        "С чего начать церковную жизнь?\n1. Покреститься\n2. Найти приход\n3. Прийти на Исповедь\n4. Причаститься\n5. Читать молитвы утром и вечером"),
    "lib_literatura": ("📚 Православная литература",
        "📚 ПРАВОСЛАВНАЯ ЛИТЕРАТУРА\n\n"
        "⭐ Несвятые святые — архим. Тихон Шевкунов\nСамая читаемая православная книга нашего времени. Живые истории.\n\n"
        "📖 Закон Божий — прот. Серафим Слободской\nЛучшая книга для начинающих. Всё о вере доступным языком.\n\n"
        "📖 Таинство веры — митр. Иларион Алфеев\nВведение в православное богословие. Просто о сложном.\n\n"
        "📖 Паисий Святогорец — Слова (5 томов)\nМудрость афонского старца о духовной жизни.\n\n"
        "📖 Лествица — прп. Иоанн Лествичник\nКлассика православной аскетики. VI век.\n\n"
        "Искать на: litres.ru, ozon.ru, в церковных лавках"),
}


# ========== MAX: АКТИВАЦИЯ, УДЕРЖАНИЕ И РЕФЕРАЛЫ ==========
def quick_start_buttons_max():
    return [
        [btn("🙏 Нужна молитва", "quick_choice:prayer"), btn("🕊️ Нужна поддержка", "quick_choice:support")],
        [btn("👼 Узнать день ангела", "quick_choice:saint"), btn("📿 Подготовиться к исповеди", "quick_choice:confession")],
        [btn("📸 Узнать икону", "quick_choice:icon"), btn("📖 Читать Евангелие", "quick_choice:gospel")],
        [btn("🗺️ Найти храм", "quick_choice:church"), btn("☦️ Посмотреть всё", "main_menu")],
    ]


def quick_target_for_track(track: str) -> str:
    return {
        "prayer": "prayers", "support": "ask_question", "saint": "saints",
        "confession": "sacr_ispoved", "icon": "photo_icon",
        "gospel": "daily_gospel", "church": "find_church",
    }.get(track, "main_menu")


def next_step_for_track(track: str):
    return {
        "prayer": ("⭐ Сохранить молитву", "favorites"),
        "support": ("❓ Задать уточнение", "ask_question"),
        "saint": ("👤 Заполнить профиль", "profile"),
        "confession": ("🗺️ Найти храм", "find_church"),
        "icon": ("👼 Открыть святых", "saints"),
        "gospel": ("📚 Открыть библиотеку", "library"),
        "church": ("📿 Подготовка к исповеди", "sacr_ispoved"),
    }.get(track, ("☦️ Главное меню", "main_menu"))


async def notify_referrer_max(referrer_id: int):
    if not referrer_id:
        return
    try:
        await send_message(
            referrer_id,
            "🤝 Ваш близкий начал пользоваться помощником «С верой».\n\n"
            "Спасибо, что делитесь полезным. Для вас открыта молитвенная подборка за близких.",
            [[btn("🎁 Открыть подборку", "ref_reward")]],
        )
        conn = _funnel_conn()
        conn.execute(
            "UPDATE referrals SET reward_sent=1 WHERE platform='MAX' AND referrer_id=? AND status='activated'",
            (int(referrer_id),),
        )
        conn.commit(); conn.close()
    except Exception as e:
        logging.error(f"Referral reward MAX error: {e}")


async def maybe_send_activation_prompt_max(chat_id: int, user_id: int, track: str):
    await asyncio.sleep(1)
    if not should_send_activation_prompt(user_id, "MAX"):
        return
    label, target = next_step_for_track(track)
    await send_message(chat_id, "🕊️ Первый полезный шаг сделан.\n\nМожно выбрать одно продолжение. Никакие личные рассылки не включаются без вашего согласия.", [
        [btn(label, target)],
        [btn("✅ Спокойный путь на 7 дней", f"journey_yes:{track}")],
        [btn("🔔 Короткая молитва утром", "notifications_yes")],
        [btn("Не сейчас", "main_menu")],
    ])



async def handle_funnel_callback_max(chat_id: int, user_id: int, payload: str, first_name: str = "") -> bool:
    if payload == "confirm_delete_my_data":
        delete_user_data(user_id, "MAX")
        await send_message(chat_id, "✅ Данные профиля удалены. Чтобы начать заново, отправьте /start.")
        return True

    if payload == "quick_start":
        track_funnel_event(user_id, "MAX", "quick_start_opened")
        await send_message(
            chat_id,
            "☦️ Начнём за 60 секунд\n\nЧто вам сейчас нужнее всего? Выберите один вариант — помощник сразу откроет подходящий раздел.",
            quick_start_buttons_max(),
        )
        return True

    if payload.startswith("quick_choice:"):
        track = payload.split(":", 1)[1]
        target = quick_target_for_track(track)
        track_funnel_event(user_id, "MAX", "quick_start_choice", target=track)
        await handle_callback(chat_id, user_id, target, first_name)
        return True

    if payload == "notifications_yes":
        conn = db_connect(); conn.execute("UPDATE users SET notifications=1 WHERE user_id=?", (int(user_id),)); conn.commit(); conn.close()
        set_funnel_flag(user_id, "MAX", "notifications_enabled", 1)
        track_funnel_event(user_id, "MAX", "notifications_opt_in")
        await send_message(chat_id, "🔔 Утренняя молитва включена. Отключить её можно в профиле.", [[btn("🏠 Меню", "main_menu")]])
        return True

    if payload.startswith("journey_yes:"):
        track = payload.split(":", 1)[1]
        start_nurture_journey(user_id, "MAX", track)
        await send_message(
            chat_id,
            "✅ 7-дневное знакомство включено.\n\nПервое короткое сообщение придёт завтра. Отключить серию можно в любой момент.",
            [[btn("🔕 Отключить серию", "journey_stop"), btn("🏠 Меню", "main_menu")]],
        )
        return True

    if payload == "journey_stop":
        stop_nurture_journey(user_id, "MAX")
        await send_message(chat_id, "🔕 7-дневная серия отключена.", main_menu_buttons())
        return True

    if payload == "invite_friend":
        link = f"{MAX_BOT_URL}?start=ref_{int(user_id)}"
        track_funnel_event(user_id, "MAX", "referral_link_opened")
        await send_message(
            chat_id,
            "🤝 Пригласить близкого\n\n"
            "Отправьте эту персональную ссылку человеку, которому могут пригодиться молитвы, календарь или спокойная подготовка к Таинствам.\n\n"
            f"{link}\n\nКогда близкий получит первый полезный результат, вам откроется молитвенная подборка за родных.",
            [[link_btn("☦️ Открыть ссылку", link)], [btn("🎁 Моя подборка", "ref_reward")]],
        )
        return True

    if payload == "ref_reward":
        if has_referral_reward(user_id, "MAX"):
            await send_message(chat_id, referral_reward_text(), [[btn("🙏 Все молитвы", "prayers"), btn("🏠 Меню", "main_menu")]])
        else:
            await send_message(chat_id, "🎁 Подборка откроется, когда приглашённый вами человек получит первый полезный результат в помощнике.", [[btn("🤝 Получить ссылку", "invite_friend")]])
        return True

    if payload == "interactive_menu":
        await send_message(
            chat_id,
            "💬 Что разобрать в следующей публикации?\n\nВыберите тему — голос будет учтён в аналитике канала.",
            [
                [btn("🙏 Как начать молиться", "interactive_vote:prayer")],
                [btn("📿 Первая исповедь", "interactive_vote:confession")],
                [btn("👼 День ангела", "interactive_vote:saint")],
                [btn("🕊️ Тревога и уныние", "interactive_vote:support")],
            ],
        )
        return True

    if payload.startswith("interactive_vote:"):
        topic = payload.split(":", 1)[1]
        record_topic_vote(user_id, "MAX", topic)
        await send_message(chat_id, "✅ Спасибо! Ваш выбор учтён.", [[btn("☦️ Начать за 60 секунд", "quick_start")]])
        return True

    if payload.startswith("review_consent:"):
        value = 1 if payload.endswith(":yes") else 0
        conn = _funnel_conn()
        conn.execute(
            "UPDATE user_reviews SET publish_consent=? WHERE user_id=? AND id=(SELECT MAX(id) FROM user_reviews WHERE user_id=?)",
            (value, int(user_id), int(user_id)),
        )
        conn.commit()
        if value:
            row = conn.execute("SELECT MAX(id) FROM user_reviews WHERE user_id=?", (int(user_id),)).fetchone()
            review_id = int(row[0]) if row and row[0] else 0
            if review_id:
                await send_message(OWNER_ID, f"📢 Пользователь разрешил анонимную публикацию отзыва #{review_id}.", [[btn("✅ Одобрить для канала", f"owner_review_public:{review_id}")]])
        conn.close()
        await send_message(chat_id, "Спасибо. Отзыв будет использован только анонимно." if value else "Понял. Отзыв останется только внутри команды проекта.", main_menu_buttons())
        return True

    if payload.startswith("owner_review_public:"):
        if int(user_id) != int(OWNER_ID):
            return True
        review_id = int(payload.split(":", 1)[1])
        conn = _funnel_conn()
        row = conn.execute("SELECT publish_consent FROM user_reviews WHERE id=?", (review_id,)).fetchone()
        if row and int(row[0] or 0) == 1:
            conn.execute("UPDATE user_reviews SET public_approved=1 WHERE id=?", (review_id,))
            conn.commit(); conn.close()
            await send_message(chat_id, f"✅ Отзыв #{review_id} одобрен для анонимной публикации.")
        else:
            conn.close()
            await send_message(chat_id, "⚠️ Пользователь ещё не дал согласие на публикацию.")
        return True

    return False



async def weekly_funnel_report_loop_max():
    await asyncio.sleep(90)
    while True:
        try:
            now = datetime.utcnow() + timedelta(hours=3)
            if now.weekday() == 0 and now.hour >= 10 and weekly_report_due("MAX"):
                await send_message(OWNER_ID, funnel_report_text("MAX", 7))
                mark_weekly_report_sent("MAX")
        except Exception as e:
            logging.error(f"Weekly funnel report MAX error: {e}")
        await asyncio.sleep(1800)


async def nurture_loop_max():
    await asyncio.sleep(45)
    while True:
        for user_id, track, day_index in due_nurture_rows("MAX"):
            try:
                series = NURTURE_MESSAGES.get(track, NURTURE_MESSAGES["support"])
                idx = min(int(day_index), len(series) - 1)
                text, target = series[idx]
                await send_message(
                    int(user_id),
                    text,
                    [[btn("Открыть", target)], [btn("🔕 Отключить серию", "journey_stop")]],
                )
                track_funnel_event(user_id, "MAX", "nurture_message_sent", target=track, value=str(NURTURE_DAY_OFFSETS[idx]))
                advance_nurture(user_id, "MAX", idx)
                await asyncio.sleep(0.15)
            except Exception as e:
                logging.error(f"Nurture MAX send error {user_id}: {e}")
        await asyncio.sleep(600)

# ========== ОБРАБОТЧИКИ ==========
async def handle_start(chat_id, user_id, first_name, username, start_payload=""):
    """Обрабатывает обычный запуск и запуск по MAX deep-link.

    При наличии start_payload пользователь сразу попадает в обещанный раздел,
    а не в общее меню. Формат ссылки: https://max.ru/<bot>?start=<payload>
    """
    user = get_user(user_id, username, first_name)
    raw_start_payload = (start_payload or "").strip()
    if raw_start_payload.startswith("ref_"):
        try:
            register_referral("MAX", int(raw_start_payload.split("_", 1)[1]), user_id)
        except Exception:
            pass
        start_payload = "ch_start60"
    base_start_payload = base_channel_source(start_payload)
    touch_funnel_user(user_id, "MAX", raw_start_payload, base_start_payload)
    # Записываем в Sheets (в фоне)
    import threading
    threading.Thread(target=sheets_add_user_max, args=(user_id, username, first_name), daemon=True).start()

    if not user.get("onboarded"):
        conn = db_connect()
        conn.execute("UPDATE users SET onboarded=1 WHERE user_id=?", (user_id,))
        conn.commit()
        conn.close()

    # Deep-link источники канала. Каждый источник одновременно открывает
    # обещанную функцию и записывается в аналитику воронки.
    channel_routes = {
        "ch_morning": "prayer_of_day",
        "ch_quote": "ask_question",
        "ch_saint": "saint_search",
        "ch_guidance": "ask_question",
        "ch_practical": "sacr_ispoved",
        "ch_story": "saint_search",
        "ch_evening": "prayer_evening_ru",
        "ch_qa": "ask_question",
        "ch_life": "saint_search",
        "ch_film": "library",
        "ch_gospel": "daily_gospel",
        "ch_photo": "photo_icon",
        "ch_church": "find_church",
        "ch_profile": "profile",
        "ch_calendar": "calendar",
        "ch_showcase_prayer": "prayer_for_me",
        "ch_showcase_photo": "photo_icon",
        "ch_showcase_angel": "saint_search",
        "ch_showcase_confession": "sacr_ispoved",
        "ch_interactive": "interactive_menu",
        "ch_community": "ask_question",
    }
    if base_start_payload in {"ch_start60", "start60"}:
        track_funnel_event(user_id, "MAX", "channel_click", source=raw_start_payload or base_start_payload, target="quick_start")
        await handle_funnel_callback_max(chat_id, user_id, "quick_start", first_name)
        return
    target_payload = channel_routes.get(base_start_payload)
    if target_payload:
        try:
            conn = db_connect()
            conn.execute(
                "INSERT INTO channel_clicks (user_id,source,target,clicked_at) VALUES (?,?,?,?)",
                (user_id, raw_start_payload or base_start_payload, target_payload, datetime.now().isoformat()),
            )
            conn.commit()
            conn.close()
        except Exception as e:
            logging.error(f"Не удалось записать переход из канала: {e}")
        actual_source = raw_start_payload or base_start_payload
        track_funnel_event(user_id, "MAX", "channel_click", source=actual_source, target=target_payload)
        await handle_callback(chat_id, user_id, target_payload, first_name)
        track_funnel_event(user_id, "MAX", "result_delivered", source=actual_source, target=target_payload)
        return

    # Прямые deep-link сценарии оставлены для совместимости со старыми постами.
    allowed_payloads = {
        "prayers", "saints", "daily_gospel", "ask_question",
        "prayer_evening_ru", "library", "photo_icon", "find_church",
        "sacraments", "calendar", "main_menu", "profile", "sacr_ispoved",
        "prayer_of_day", "prayer_for_me", "saint_search",
    }
    if start_payload in allowed_payloads:
        await handle_callback(chat_id, user_id, start_payload, first_name)
        return

    if not user.get("onboarded"):
        await send_message(chat_id,
            "☦️ Добро пожаловать в «С верой»!\n\n"
            "Я ваш православный помощник:\n\n"
            "🙏 Молитвы на все случаи жизни\n"
            "📅 Православный календарь и посты\n"
            "⛪ Таинства — как подготовиться\n"
            "👼 Жития святых и мощи\n"
            "🏛️ Святые места России и мира\n"
            "📸 Узнать храм или икону по фото\n"
            "❓ Задать вопрос о вере\n\n"
            "📢 Наш канал → https://max.ru/-75405929805299\n\n"
            "Чем могу помочь? ☦️",
            main_menu_buttons()
        )
    else:
        name = user.get("church_name") or first_name
        await send_message(chat_id,
            f"☦️ С возвращением, {name}!\n\nРад видеть вас снова 🕊️\n\nЧем могу помочь?\n\n📢 Наш канал → https://max.ru/-75405929805299",
            main_menu_buttons()
        )

async def handle_callback(chat_id, user_id, payload, first_name=""):
    touch_user_session(user_id, "MAX", target=payload)
    touch_funnel_user(user_id, "MAX", increment_visit=False)
    if payload in {"notifications_yes", "prayer_for_me", "find_church", "ask_question", "profile", "journey_stop", "invite_friend", "review", "donate"}:
        track_funnel_event(user_id, "MAX", "next_step_clicked", target=payload)
    if await handle_funnel_callback_max(chat_id, user_id, payload, first_name):
        return
    if payload in FUNNEL_USEFUL_CALLBACKS:
        track = FUNNEL_TRACK_BY_TARGET.get(payload, "support")
        referrer = mark_useful_action(user_id, "MAX", payload)
        if referrer:
            asyncio.create_task(notify_referrer_max(referrer))
        asyncio.create_task(maybe_send_activation_prompt_max(chat_id, user_id, track))
    # Служебные действия владельца по отзывам.
    if payload.startswith("owner_review_reply:"):
        if int(user_id) != int(OWNER_ID):
            await send_message(chat_id, "⛔ Эта команда доступна только владельцу проекта.")
            return
        try:
            review_id = int(payload.split(":", 1)[1])
        except (ValueError, IndexError):
            await send_message(chat_id, "⚠️ Не удалось определить номер отзыва.")
            return
        review = get_review_record(review_id)
        if not review:
            await send_message(chat_id, f"⚠️ Отзыв #{review_id} не найден.")
            return
        get_user(user_id, first_name=first_name)
        set_step(user_id, f"owner_review_reply:{review_id}")
        await send_message(
            chat_id,
            f"✍️ Ответ на отзыв #{review_id}\n\n"
            f"Пользователь: {review.get('first_name') or '—'}\n"
            f"Отзыв: {review.get('review_text', '')[:1800]}\n\n"
            "Напишите ответ одним сообщением. Бот отправит его пользователю от имени проекта «С верой».",
            [[btn("❌ Отменить", "owner_review_cancel")]]
        )
        return

    if payload.startswith("owner_review_done:"):
        if int(user_id) != int(OWNER_ID):
            await send_message(chat_id, "⛔ Эта команда доступна только владельцу проекта.")
            return
        try:
            review_id = int(payload.split(":", 1)[1])
        except (ValueError, IndexError):
            await send_message(chat_id, "⚠️ Не удалось определить номер отзыва.")
            return
        review = get_review_record(review_id)
        if not review:
            await send_message(chat_id, f"⚠️ Отзыв #{review_id} не найден.")
            return
        replied_at = update_review_record(
            review_id,
            "processed",
            "",
            first_name or "Владелец"
        )
        asyncio.create_task(asyncio.to_thread(
            sheets_update_review_max,
            review_id,
            "Обработано",
            "",
            datetime.fromisoformat(replied_at).strftime("%d.%m.%Y %H:%M"),
            first_name or "Владелец"
        ))
        await send_message(chat_id, f"✅ Отзыв #{review_id} отмечен как обработанный.")
        return

    if payload == "owner_review_cancel":
        if int(user_id) == int(OWNER_ID):
            get_user(user_id, first_name=first_name)
            set_step(user_id, "idle")
            await send_message(chat_id, "Отправка ответа отменена.")
        return

    if payload == "main_menu":
        await send_message(chat_id, "☦️ Главное меню:", main_menu_buttons())

    elif payload == "prayers":
        await send_message(chat_id, "🙏 Выберите молитву:", prayers_buttons())

    elif payload == "prayer_of_day":
        await send_message(chat_id, "✨ Нахожу молитву дня...")
        prayer = await get_prayer_of_day_max()
        day_str = date_ru("short")
        feast = get_todays_feast()
        feast_line = ("🎉 " + feast + "\n\n") if feast else ""
        await send_message(chat_id,
            "✨ Молитва дня — " + day_str + "\n\n" + feast_line + prayer,
            [
                [btn("🔔 Получать молитву утром", "notifications_yes")],
                [btn("🙏 Подобрать молитву по ситуации", "prayer_for_me")],
                [btn("🏠 Главное меню", "main_menu")],
            ]
        )

    elif payload.startswith("prayer_") and payload != "prayer_for_me":
        key = payload
        prayer_data = PRAYER_TEXTS.get(key)
        if prayer_data:
            title, text = prayer_data
            await send_message(chat_id, f"{title}\n\n{text}", [
                [btn("⭐ Сохранить в избранное", f"save_fav_{key}")],
                [btn("◀️ Молитвы", "prayers"), btn("🏠 Меню", "main_menu")],
            ])
        else:
            await send_message(chat_id, "Молитва не найдена.", back_main())

    elif payload.startswith("save_fav_"):
        key = payload.replace("save_fav_", "")
        prayer_data = PRAYER_TEXTS.get(key)
        if prayer_data:
            save_favorite(user_id, prayer_data[0], prayer_data[1])
            await send_message(chat_id, "⭐ Сохранено в избранное!", [[btn("◀️ Молитвы", "prayers")]])

    elif payload == "favorites":
        favs = get_favorites(user_id)
        if not favs:
            await send_message(chat_id, "⭐ Избранных молитв нет.\n\nНажимайте ⭐ при чтении — молитва сохранится.", back_main())
        else:
            text = "⭐ Ваши избранные молитвы:\n\n"
            for i, (title, content) in enumerate(favs[:5]):
                text += f"{i+1}. {title}\n"
            await send_message(chat_id, text, back_main())

    elif payload == "sacraments":
        await send_message(chat_id, "⛪ Таинства и обряды\n\nВыберите раздел:", sacraments_buttons())

    elif payload.startswith("sacr_"):
        key = payload.replace("sacr_", "")
        sacr = SACRAMENT_TEXTS.get(key)
        if sacr:
            title, text = sacr
            sacr_prayers = {
                "ispoved": [btn("📖 Канон покаянный", "prayer_pokayanny_kanon"), btn("🌅 Утренняя молитва", "prayer_morning_ru")],
                "prichaschenie": [btn("📖 Канон покаянный", "prayer_pokayanny_kanon"), btn("✝️ Правило ко Причастию", "prayer_prichaschenie")],
                "venchanie": [btn("🌅 Утренняя молитва", "prayer_morning_ru"), btn("🌙 Вечерняя молитва", "prayer_evening_ru")],
                "otpevanie": [btn("🕯️ Об упокоении", "prayer_upokoenie")],
            }
            buttons = []
            if key in sacr_prayers:
                buttons.append(sacr_prayers[key] if isinstance(sacr_prayers[key], list) else [sacr_prayers[key]])
            buttons.append([btn("⭐ Сохранить", f"save_sacr_{key}")])
            buttons.append([btn("◀️ Таинства", "sacraments"), btn("🏠 Меню", "main_menu")])
            await send_message(chat_id, f"{title}\n\n{text}\n\n{PASTORAL_DISCLAIMER}", buttons)

    elif payload.startswith("save_sacr_"):
        key = payload.replace("save_sacr_", "")
        sacr = SACRAMENT_TEXTS.get(key)
        if sacr:
            save_favorite(user_id, sacr[0], sacr[1])
            await send_message(chat_id, "⭐ Сохранено!", [[btn("◀️ Таинства", "sacraments")]])

    elif payload == "holy_places":
        await send_message(chat_id, "🏛️ Святые места\n\nВыберите раздел:", holy_places_buttons())

    elif payload.startswith("place_"):
        key = payload.replace("place_", "")
        place = HOLY_PLACES_TEXTS.get(key)
        if place:
            title, text = place
            await send_message(chat_id, f"{title}\n\n{text}", [
                [btn("⭐ Сохранить", f"save_place_{key}")],
                [btn("◀️ Святые места", "holy_places"), btn("🏠 Меню", "main_menu")],
            ])
        else:
            await send_message(chat_id, "Раздел скоро появится 🙏", [[btn("◀️ Святые места", "holy_places")]])

    elif payload.startswith("save_place_"):
        key = payload.replace("save_place_", "")
        place = HOLY_PLACES_TEXTS.get(key)
        if place:
            save_favorite(user_id, place[0], place[1])
            await send_message(chat_id, "⭐ Сохранено!", [[btn("◀️ Святые места", "holy_places")]])

    elif payload == "calendar":
        await send_message(chat_id, "📅 Православный календарь:", calendar_buttons())

    elif payload == "cal_today":
        today_str = date_ru("full")
        feast = get_todays_feast()
        saints = get_todays_saints()
        text = f"📅 Сегодня {today_str}\n\n"
        if feast:
            text += f"🎉 {feast}\n\n"
        if saints:
            text += "👼 Память святых:\n"
            for name, desc in saints[:5]:
                text += f"— {name} ({desc})\n"
        if not feast and not saints:
            text += "В нашей справочной базе нет особой записи на сегодня. Для полного календаря проверьте календарь своего прихода."
        await send_message(chat_id, text, [[btn("◀️ Календарь", "calendar")]])

    elif payload == "cal_namedays":
        saints = get_todays_saints()
        today_str = date_ru("short")
        if saints:
            text = f"👼 Именинники {today_str}:\n\n"
            for name, desc in saints:
                text += f"✨ {name} — {desc}\n"
        else:
            text = f"👼 В нашей справочной базе нет записей об именинах на сегодня. Точный календарь лучше уточнить в своём приходе."
        await send_message(chat_id, text, [[btn("◀️ Календарь", "calendar")]])

    elif payload == "cal_feasts":
        text = "🎉 Главные православные праздники:\n\n"
        for date_str, feast in FIXED_FEASTS.items():
            text += f"📅 {date_str} — {feast}\n"
        await send_message(chat_id, text, [[btn("◀️ Календарь", "calendar")]])

    elif payload == "cal_fasts":
        text = "🥗 Православные посты:\n\n"
        for name, desc in FASTS.items():
            text += f"📿 {name}:\n{desc}\n\n"
        await send_message(chat_id, text, [[btn("◀️ Календарь", "calendar")]])

    elif payload == "cal_find_angel":
        set_step(user_id, "find_angel")
        await send_message(chat_id, "🔍 Введите имя для поиска дня ангела:", [[btn("◀️ Назад", "calendar")]])

    elif payload == "cal_pasxa":
        await send_message(chat_id, PASCHA_GUIDE_TEXT, [[btn("◀️ Календарь", "calendar")]])

    elif payload == "cal_kreschenije":
        await send_message(chat_id, THEOPHANY_GUIDE_TEXT, [[btn("◀️ Календарь", "calendar")]])

    elif payload == "library":
        await send_message(chat_id, "📚 Библиотека\n\nВыберите раздел:", library_buttons())

    elif payload.startswith("lib_"):
        key = payload
        if key == "lib_pdf":
            await send_message(chat_id, "📥 Книги на сайте Азбука.ру:", [
                [link_btn("📖 Библия", "https://azbyka.ru/biblia/")],
                [link_btn("📖 Новый Завет", "https://azbyka.ru/otechnik/Biblia/novyj-zavet-sinodalnij-perevod/")],
                [link_btn("🙏 Молитвослов", "https://azbyka.ru/molitvoslov/")],
                [link_btn("📜 Псалтирь", "https://azbyka.ru/otechnik/Biblia/psaltir-v-russkom-perevode/")],
                [link_btn("📖 Лествица", "https://azbyka.ru/otechnik/Ioann_Lestvichnik/lestvitsa/")],
                [btn("◀️ Библиотека", "library")],
            ])
        else:
            lib = LIBRARY_TEXTS.get(key)
            if lib:
                title, text = lib
                await send_message(chat_id, text, [[btn("◀️ Библиотека", "library")]])

    elif payload == "saints":
        saints = get_todays_saints()
        today_str = date_ru("short")
        text = "👼 Святые\n\n"
        if saints:
            text += f"Сегодня, {today_str}, память:\n"
            for name, desc in saints[:3]:
                text += f"— {name} ({desc})\n"
            text += "\n"
        text += "Выберите действие:"
        await send_message(chat_id, text, [
            [btn("🔍 Найти святого по имени", "saint_search")],
            [btn("👼 Именинники сегодня", "cal_namedays")],
            [btn("◀️ Главное меню", "main_menu")],
        ])

    elif payload == "saint_search":
        set_step(user_id, "saint_search")
        await send_message(chat_id, "🔍 Введите имя святого:\n\nНапример: Николай, Матрона, Сергий", [[btn("◀️ Назад", "saints")]])

    elif payload == "photo_menu":
        await send_message(chat_id, "📸 Что хотите определить?", [
            [btn("⛪ Это храм или монастырь", "photo_church")],
            [btn("🖼️ Это икона", "photo_icon")],
            [btn("◀️ Главное меню", "main_menu")],
        ])

    elif payload in ("photo_church", "photo_icon"):
        set_step(user_id, payload)
        ptype = "храм или монастырь" if payload == "photo_church" else "икону"
        await send_message(chat_id, f"📸 Отправьте фотографию — определю {ptype} и расскажу о нём.", [[btn("◀️ Назад", "photo_menu")]])

    elif payload == "find_church":
        set_step(user_id, "find_church_city")
        await send_message(chat_id, "🗺️ Напишите название города — найду православные храмы рядом.", back_main())

    elif payload == "profile":
        user = get_user(user_id)
        church = user.get("church_name") or "не указано"
        birth = user.get("birth_date") or "не указана"
        angel = user.get("angel_day") or "не найден"
        notif = user.get("notifications", 0)
        notif_btn = "🔔 Уведомления: ВКЛ" if notif else "🔕 Уведомления: ВЫКЛ"
        await send_message(chat_id,
            f"👤 Мой профиль\n\n✏️ Имя: {church}\n🎂 Дата рождения: {birth}\n👼 День ангела: {angel}",
            [
                [btn("✏️ Изменить имя", "profile_edit_name")],
                [btn("🎂 Изменить дату рождения", "profile_edit_birth")],
                [btn(notif_btn, "toggle_notifications")],
                [btn("⭐ Избранные молитвы", "favorites")],
                [btn("🙏 Молитва покровителю", "profile_patron_prayer")],
                [btn("🕯️ Пожертвование", "donate")],
                [btn("◀️ Главное меню", "main_menu")],
            ]
        )

    elif payload == "profile_edit_name":
        set_step(user_id, "edit_name")
        await send_message(chat_id, "✏️ Введите ваше имя при крещении:", [[btn("◀️ Профиль", "profile")]])

    elif payload == "profile_edit_birth":
        set_step(user_id, "edit_birth")
        await send_message(chat_id, "🎂 Введите дату рождения в формате ДД.ММ\nНапример: 15.03", [[btn("◀️ Профиль", "profile")]])

    elif payload == "profile_patron_prayer":
        user = get_user(user_id)
        name = (user.get("church_name") or "").strip()
        if not name:
            await send_message(chat_id, "👤 Укажите имя в профиле 🙏", [[btn("✏️ Указать имя", "profile_edit_name")]])
            return
        name_lower = name.lower()
        conn = db_connect()
        c = conn.cursor()
        c.execute("SELECT prayer FROM patron_prayers_cache WHERE name=?", (name_lower,))
        row = c.fetchone()
        conn.close()
        if row:
            await send_message(chat_id, f"🙏 Молитва небесному покровителю — {name}\n\n{row[0]}", [[btn("◀️ Профиль", "profile")]])
            return
        await send_message(chat_id, "🙏 Нахожу молитву...")
        try:
            msg = await asyncio.to_thread(
                claude_client.messages.create,
                model="claude-sonnet-4-5",
                max_tokens=600,
                system="Ты православный помощник. Напиши пример краткого личного молитвенного обращения к святому, 8-12 строк. Не выдавай текст за утверждённую церковную молитву. Начни с обращения и закончи словом Аминь.",
                messages=[{"role": "user", "content": f"Напиши молитву святому: {name}"}]
            )
            prayer_text = msg.content[0].text
            conn2 = db_connect()
            conn2.execute("INSERT OR REPLACE INTO patron_prayers_cache (name, prayer) VALUES (?,?)", (name_lower, prayer_text))
            conn2.commit()
            conn2.close()
            await send_message(chat_id, f"🙏 Молитва небесному покровителю — {name}\n\n{prayer_text}", [[btn("◀️ Профиль", "profile")]])
        except Exception as e:
            logging.error(f"Ошибка молитвы: {e}")
            await send_message(chat_id, "🙏 Обратитесь к своему святому своими словами.", [[btn("◀️ Профиль", "profile")]])

    elif payload == "toggle_notifications":
        notif = get_user(user_id).get("notifications", 0)
        new_val = 0 if notif else 1
        conn = db_connect()
        conn.execute("UPDATE users SET notifications=? WHERE user_id=?", (new_val, user_id))
        conn.commit()
        conn.close()
        set_funnel_flag(user_id, "MAX", "notifications_enabled", new_val)
        status = "включены ✅" if new_val else "отключены 🔕"
        await send_message(chat_id, f"Утренние уведомления {status}", back_main())

    elif payload == "daily_gospel":
        await send_message(chat_id, "📖 Нахожу Евангельская мысль...")
        text = await get_daily_gospel_max()
        await send_message(chat_id, text, [
            [btn("🙏 Молитва по моей ситуации", "prayer_for_me")],
            [btn("❓ Задать вопрос", "ask_question")],
            [btn("🏠 Главное меню", "main_menu")],
        ])

    elif payload == "cal_fast_today":
        text = get_fast_today_max()
        await send_message(chat_id, text, [
            [btn("🥗 Все посты", "cal_fasts")],
            [btn("📅 Календарь", "calendar"), btn("🏠 Меню", "main_menu")]
        ])

    elif payload == "prayer_for_me":
        set_step(user_id, "prayer_for_me_name")
        await send_message(chat_id,
            "🙏 Молитва за меня\n\n"
            "Напишите ваше имя и просьбу к Богу.\n\n"
            "Например: Александр, прошу о здравии и помощи\n"
            "Или просто: здоровье семьи, мир в душе",
            back_main()
        )

    elif payload == "make_zapiska":
        set_step(user_id, "zapiska_type")
        await send_message(chat_id,
            "✍️ Составить записку в храм\n\nКакая записка нужна?",
            [[btn("💛 О здравии", "zapiska_zdravie")],
             [btn("🕯️ Об упокоении", "zapiska_upokoenie")],
             back_main()[0]]
        )

    elif payload == "zapiska_zdravie":
        set_step(user_id, "zapiska_zdravie_names")
        await send_message(chat_id,
            "💛 Записка о здравии\n\n"
            "Введите имена через запятую.\n\n"
            "Пишите как знаете — привычное или полное имя.\n"
            "Если не знаете крещёного имени — ничего страшного,\n"
            "в храме помогут разобраться.\n\n"
            "Пример: Саша, Мария, Дед Николай",
            back_main()
        )

    elif payload == "zapiska_upokoenie":
        set_step(user_id, "zapiska_upokoenie_names")
        await send_message(chat_id,
            "🕯️ Записка об упокоении\n\n"
            "Введите имена через запятую.\n\n"
            "Пишите как знаете — привычное или полное имя.\n"
            "Если не знаете крещёного имени — ничего страшного,\n"
            "в храме помогут.\n\n"
            "Пример: Бабушка Нина, Николай, дед Василий",
            back_main()
        )

    elif payload == "ask_question":
        await send_message(chat_id, "❓ Выберите формат ответа:", [
            [btn("💬 Кратко", "q_short"), btn("📖 Развёрнуто", "q_medium"), btn("🔍 Глубоко", "q_deep")],
            [btn("◀️ Главное меню", "main_menu")],
        ])

    elif payload in ("q_short", "q_medium", "q_deep"):
        depth_map = {"q_short": "short", "q_medium": "medium", "q_deep": "deep"}
        set_step(user_id, f"question_{depth_map[payload]}")
        await send_message(chat_id, "✏️ Напишите ваш вопрос о вере:", [[btn("◀️ Назад", "ask_question")]])

    elif payload == "donate":
        set_step(user_id, "donate_amount")
        await send_message(chat_id,
            "🕯️ Пожертвование на развитие проекта\nво славу Божию ☦️\n\n"
            "Если бот помогает вам — вы можете поддержать его развитие.\n\n"
            "✏️ Напишите сумму в рублях и я создам ссылку для оплаты 👇",
            back_main()
        )

    elif payload == "review":
        set_step(user_id, "review")
        await send_message(chat_id,
            "💬 Отзыв или пожелание\n\n"
            "Что нравится? Чего не хватает?\n\n"
            "✏️ Напишите ваш отзыв 👇",
            back_main()
        )

    else:
        await send_message(chat_id, "☦️ Главное меню:", main_menu_buttons())

def delete_user_data(user_id: int, platform: str):
    """Deletes user-owned data and anonymises accounting rows."""
    uid = int(user_id)
    conn = db_connect()
    for table in (
        "favorites", "nurture_journeys", "funnel_events", "user_sessions",
        "channel_clicks", "topic_votes", "limits", "subscriptions", "pending_payments",
    ):
        try:
            conn.execute(f"DELETE FROM {table} WHERE user_id=?", (uid,))
        except Exception:
            pass
    conn.execute("DELETE FROM user_funnel_state WHERE user_id=? AND platform=?", (uid, platform))
    conn.execute("DELETE FROM referrals WHERE platform=? AND (referrer_id=? OR referred_user_id=?)", (platform, uid, uid))
    conn.execute("DELETE FROM user_reviews WHERE user_id=?", (uid,))
    conn.execute(
        "UPDATE donation_payments SET user_id=0,chat_id=0,username='',first_name='' WHERE user_id=? AND platform=?",
        (uid, platform),
    )
    conn.execute("DELETE FROM users WHERE user_id=?", (uid,))
    conn.commit()
    conn.close()

async def handle_text(chat_id, user_id, text, first_name=""):
    touch_user_session(user_id, "MAX")
    user = get_user(user_id)
    step = user.get("step", "idle")

    owner_command = text.strip().lower()

    # Команда доступна всем: помогает один раз узнать реальный MAX user_id.
    # Для служебных команд используется именно user_id, а не chat_id.
    if owner_command in {"/myid", "мой id", "мой айди"}:
        owner_mark = "да" if int(user_id) == int(OWNER_ID) else "нет"
        await send_message(
            chat_id,
            "🪪 Ваши идентификаторы MAX\n\n"
            f"User ID: {user_id}\n"
            f"Chat ID: {chat_id}\n"
            f"Сейчас распознан как владелец: {owner_mark}\n\n"
            "Для настройки владельца добавьте в /root/.env_vera строку:\n"
            f"MAX_OWNER_ID={user_id}\n\n"
            "После изменения перезапустите службу MAX-бота."
        )
        return

    if text.strip() in ("/start", "start"):
        await handle_start(chat_id, user_id, first_name, "")
        return
    if owner_command in {"/privacy", "конфиденциальность"}:
        await send_message(
            chat_id,
            (
                "🔐 Конфиденциальность\n\n"
                "Бот хранит только данные, необходимые для работы: ID платформы, "
                "профиль, настройки, избранное, отзывы и статусы платежей. "
                "Временные голосовые файлы удаляются после обработки.\n\n"
                "Для удаления профиля отправьте /delete_my_data."
            ),
        )
        return
    if owner_command in {"/delete_my_data", "удалить мои данные"}:
        await send_message(
            chat_id,
            "Удалить профиль, настройки и историю использования? Платёжные записи будут обезличены.",
            [[btn("🗑️ Да, удалить", "confirm_delete_my_data")], [btn("Отмена", "main_menu")]],
        )
        return
    if int(user_id) == int(OWNER_ID) and owner_command in {"/health_full", "полная диагностика"}:
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
        await send_message(
            chat_id,
            (
                "🩺 Полная диагностика MAX\n\n"
                f"Пользователей: {users}\n"
                f"Ожидают оплаты: {pending}\n"
                f"Критических ошибок за 24 часа: {errors}\n"
                "Webhook: фоновая обработка + защита от дублей\n"
                "База: WAL включён"
            ),
        )
        return

    if int(user_id) == int(OWNER_ID) and owner_command in {"/backup_status", "резервные копии"}:
        await send_message(chat_id, "💾 Резервные копии MAX\n\n" + backup_status_text("vera_max"))
        return
    if int(user_id) == int(OWNER_ID) and owner_command in {"/backup_now", "сделать резервную копию"}:
        try:
            path = await asyncio.to_thread(create_database_backup, "vera_max")
            await send_message(chat_id, f"✅ Резервная копия создана:\n{Path(path).name}")
        except Exception as e:
            record_critical_error("backup_now_max", e)
            await send_message(chat_id, f"⚠️ Не удалось создать резервную копию: {str(e)[:500]}")
        return

    # Диагностика MAX-канала доступна только владельцу.
    if int(user_id) == int(OWNER_ID) and owner_command in {"/payments_report", "платежи"}:
        await send_message(chat_id, payments_report_text("MAX"))
        return
    if int(user_id) == int(OWNER_ID) and owner_command in {"/funnel_report", "воронка"}:
        await send_message(chat_id, funnel_report_text("MAX", 7))
        return
    if int(user_id) == int(OWNER_ID) and owner_command in {"/funnel_sources", "источники воронки"}:
        await send_message(chat_id, funnel_source_report_text("MAX", 30))
        return
    if int(user_id) == int(OWNER_ID) and owner_command in {
        "/channel_status", "канал статус"
    }:
        msk_now = datetime.utcnow() + timedelta(hours=3)
        rows = channel_posts_today(msk_now)
        if rows:
            journal = "\n".join(
                f"• {slot} — {rubric}: "
                f"{status if (status != 'sent' or str(message_id).strip()) else 'unconfirmed'}"
                for slot, rubric, status, _, message_id in rows[-20:]
            )
        else:
            journal = "Сегодня в журнале ещё нет публикаций."
        await send_message(
            chat_id,
            "📊 Статус MAX-канала\n\n"
            f"Канал ID: {MAX_CHANNEL_ID}\n"
            f"Московское время: {msk_now.strftime('%d.%m.%Y %H:%M')}\n\n"
            f"{journal}\n\n"
            "Для проверки текста: /channel_test\n"
            "Для восстановления пропуска: /channel_recover\n"
            "Для нового закрепа: /publish_channel_intro\n"
            "Отчёт по воронке: /funnel_report\n"
            "Платежи: /payments_report\n\n"
            f"Последняя ошибка: {get_app_setting('max_last_channel_failure', 'нет')}\n"
            f"Пульс планировщика: {get_app_setting('max_channel_scheduler_heartbeat', 'ещё не записан')}"
        )
        return

    if int(user_id) == int(OWNER_ID) and owner_command in {
        "/channel_recover", "канал восстановить"
    }:
        msk_now = datetime.utcnow() + timedelta(hours=3)
        slot = select_catchup_channel_slot(msk_now)
        if slot is None:
            await send_message(chat_id, "✅ Актуальных пропущенных публикаций нет.")
            return
        hour, rubric, cta_key, prompt = slot
        ok = await publish_channel_slot(msk_now, hour, rubric, cta_key, prompt)
        await send_message(
            chat_id,
            f"✅ Восстановлена публикация {hour:02d}:00 — {rubric}."
            if ok else
            "⚠️ Публикацию восстановить не удалось. Проверьте /channel_status и журнал службы."
        )
        return

    if int(user_id) == int(OWNER_ID) and owner_command in {
        "/channel_test", "канал тест"
    }:
        footer, buttons, deep_link = get_channel_cta("guidance")
        test_text = (
            "🧪 Проверка публикации канала\n\n"
            "Если вы видите это сообщение, бот имеет доступ к каналу, "
            "а текст и кликабельная кнопка отправляются корректно."
            + footer
        )
        ok = await post_to_channel(
            test_text, None, buttons, deep_link
        )
        await send_message(
            chat_id,
            "✅ Тестовый пост отправлен в канал."
            if ok else
            "⚠️ MAX не подтвердил отправку тестового поста. "
            "Проверьте журнал службы и права бота в канале."
        )
        return

    if int(user_id) == int(OWNER_ID) and owner_command in {"/channel_image_test", "тест картинки"}:
        await send_message(chat_id, "ℹ️ Изображения канала временно полностью отключены. Текстовые CTA-посты работают.")
        return

    if int(user_id) == int(OWNER_ID) and owner_command in {
        "/publish_channel_intro", "опубликовать закреп"
    }:
        ok, detail = await publish_and_pin_max_intro()
        await send_message(chat_id, ("✅ " if ok else "⚠️ ") + detail)
        return

    # Ручной ответ по MAX ID — нужен и для отзывов, оставленных до обновления.
    # Формат: /reply 150083051 Текст ответа пользователю
    if int(user_id) == int(OWNER_ID) and text.strip().lower().startswith("/reply "):
        parts = text.strip().split(maxsplit=2)
        if len(parts) < 3:
            await send_message(
                chat_id,
                "Формат команды:\n/reply ID_ПОЛЬЗОВАТЕЛЯ текст ответа"
            )
            return
        try:
            target_user_id = int(parts[1])
        except ValueError:
            await send_message(chat_id, "⚠️ ID пользователя должен состоять из цифр.")
            return
        reply_text = parts[2].strip()
        result = await send_message(
            target_user_id,
            "☦️ Ответ команды проекта «С верой»\n\n"
            f"{reply_text[:3500]}\n\n"
            "Спасибо, что помогаете нам делать православного помощника лучше 🕊️",
            [[btn("💬 Написать ещё", "review"), btn("🏠 Главное меню", "main_menu")]]
        )
        if not result or result.get("error") or result.get("error_code"):
            await send_message(chat_id, "⚠️ MAX не подтвердил отправку ответа пользователю.")
            return
        replied_at_sheet = datetime.now().strftime("%d.%m.%Y %H:%M")
        asyncio.create_task(asyncio.to_thread(
            sheets_update_latest_review_by_user,
            target_user_id,
            "Отвечено",
            reply_text,
            replied_at_sheet,
            first_name or "Владелец"
        ))
        await send_message(
            chat_id,
            f"✅ Ответ пользователю {target_user_id} отправлен.\n"
            "Последний его отзыв в таблице будет отмечен как «Отвечено»."
        )
        return

    # Ответ владельца на конкретный отзыв.
    if int(user_id) == int(OWNER_ID) and step.startswith("owner_review_reply:"):
        try:
            review_id = int(step.split(":", 1)[1])
        except (ValueError, IndexError):
            set_step(user_id, "idle")
            await send_message(
                chat_id,
                "⚠️ Не удалось определить отзыв. Нажмите «Ответить» ещё раз."
            )
            return
        review = get_review_record(review_id)
        if not review:
            set_step(user_id, "idle")
            await send_message(chat_id, f"⚠️ Отзыв #{review_id} не найден.")
            return
        reply_text = text.strip()
        if not reply_text:
            await send_message(chat_id, "⚠️ Ответ не может быть пустым.")
            return
        user_message = (
            "☦️ Ответ команды проекта «С верой»\n\n"
            f"{reply_text[:3500]}\n\n"
            "Спасибо, что помогаете нам делать православного помощника лучше 🕊️"
        )
        result = await send_message(
            review["chat_id"],
            user_message,
            [[btn("💬 Написать ещё", "review"), btn("🏠 Главное меню", "main_menu")]]
        )
        if not result or result.get("error") or result.get("error_code"):
            await send_message(
                chat_id,
                "⚠️ MAX не подтвердил отправку. Ответ не отмечен как отправленный. Попробуйте ещё раз позже."
            )
            return
        set_step(user_id, "idle")
        replied_at = update_review_record(
            review_id,
            "answered",
            reply_text,
            first_name or "Владелец"
        )
        replied_at_sheet = datetime.fromisoformat(replied_at).strftime("%d.%m.%Y %H:%M")
        asyncio.create_task(asyncio.to_thread(
            sheets_update_review_max,
            review_id,
            "Отвечено",
            reply_text,
            replied_at_sheet,
            first_name or "Владелец"
        ))
        await send_message(
            chat_id,
            f"✅ Ответ на отзыв #{review_id} отправлен пользователю.\n\n"
            f"Пользователь: {review.get('first_name') or review.get('user_id')}\n"
            "Статус в Google Таблице будет изменён на «Отвечено»."
        )
        return

    if step == "edit_name":
        name = text.strip()
        conn = db_connect()
        conn.execute("UPDATE users SET church_name=? WHERE user_id=?", (name, user_id))
        conn.commit()
        conn.close()
        # Возможная дата памяти определяется только при наличии даты рождения.
        current = get_user(user_id)
        angel = find_angel_day(name, current.get("birth_date", ""))
        if angel:
            conn2 = db_connect()
            conn2.execute("UPDATE users SET angel_day=? WHERE user_id=?", (angel, user_id))
            conn2.commit(); conn2.close()
        set_step(user_id, "idle")
        msg = f"✅ Имя сохранено: {name}"
        if angel:
            msg += f"\n👼 Возможный день памяти покровителя: {angel}\nТочное определение лучше уточнить у священника."
        set_funnel_flag(user_id, "MAX", "profile_completed", 1)
        await send_message(chat_id, msg, [[btn("◀️ Профиль", "profile")]])
        return

    if step == "edit_birth":
        birth = text.strip()
        try:
            parts = birth.split(".")
            if len(parts) >= 2 and 1 <= int(parts[0]) <= 31 and 1 <= int(parts[1]) <= 12:
                conn = db_connect()
                conn.execute("UPDATE users SET birth_date=? WHERE user_id=?", (birth, user_id))
                conn.commit()
                conn.close()
                set_step(user_id, "idle")
                current = get_user(user_id)
                angel = find_angel_day(current.get("church_name", ""), birth)
                if angel:
                    conn3 = db_connect(); conn3.execute("UPDATE users SET angel_day=? WHERE user_id=?", (angel, user_id)); conn3.commit(); conn3.close()
                set_funnel_flag(user_id, "MAX", "profile_completed", 1)
                suffix = f"\n👼 Возможный день памяти покровителя: {angel}" if angel else ""
                await send_message(chat_id, f"✅ Дата рождения сохранена: {birth}{suffix}", [[btn("◀️ Профиль", "profile")]])
                return
        except:
            pass
        await send_message(chat_id, "⚠️ Формат: ДД.ММ, например: 15.03", [[btn("◀️ Профиль", "profile")]])
        return

    if step in ("find_angel", "saint_search"):
        name = text.strip().lower()
        results = []
        for saint_name, days in SAINTS_BY_NAME.items():
            if name in saint_name:
                for day_str, desc in days[:2]:
                    results.append(f"👼 {saint_name.capitalize()}: {day_str} ({desc})")
        if results:
            response = f"🔍 Найдено для «{text.strip()}»:\n\n" + "\n".join(results[:10])
        else:
            response = f"👼 Не найдено святых с именем «{text.strip()}»."
        set_step(user_id, "idle")
        await send_message(chat_id, response, [[btn("◀️ Календарь", "calendar")]])
        return

    if step == "find_church_city":
        city = text.strip()
        maps_url = f"https://maps.yandex.ru/?text=православный+храм+{city}"
        set_step(user_id, "idle")
        await send_message(chat_id, f"🗺️ Православные храмы в городе {city}:\n\n{maps_url}", back_main())
        return

    if step == "prayer_for_me_name":
        set_step(user_id, "idle")
        await send_message(chat_id, "🙏 Молюсь... составляю молитву...")
        try:
            msg = await asyncio.to_thread(
                claude_client.messages.create,
                model="claude-sonnet-4-5",
                max_tokens=600,
                system=(
                    "Ты православный помощник. Составь пример личного молитвенного обращения своими словами "
                    "на основе имени человека и его просьбы. Текст должен быть тёплым и искренним, 3-5 строф. "
                    "Обращайся к Господу или Богородице, упомяни имя и заверши словом Аминь. "
                    "Не выдавай этот текст за утверждённую церковную молитву и не представляйся священником. Пиши по-русски."
                ),
                messages=[{"role": "user", "content": f"Составь молитву для: {text}"}]
            )
            prayer_text = msg.content[0].text
            await send_message(chat_id,
                "🙏 Молитвенное обращение за тебя\n\nЭто пример молитвы своими словами:\n\n" + prayer_text,
                [[btn("🙏 Ещё молитву", "prayer_for_me"), btn("🏠 Меню", "main_menu")]]
            )
        except Exception as e:
            logging.error(f"Ошибка молитвы за меня MAX: {e}")
            await send_message(chat_id, "⚠️ Не удалось составить молитву. Попробуйте позже.", back_main())
        return

    if step == "zapiska_zdravie_names":
        set_step(user_id, "idle")
        names = [n.strip() for n in text.replace("\n", ",").split(",") if n.strip()]
        if not names:
            await send_message(chat_id, "⚠️ Введите хотя бы одно имя.", back_main())
            return
        zapiska = "О ЗДРАВИИ\n\n" + "\n".join(names[:10])
        await send_message(chat_id,
            "💛 Ваша записка о здравии:\n\n" + zapiska + "\n\n"
            "Перепишите от руки и подайте в свечной лавке.",
            [[btn("✍️ Ещё записку", "make_zapiska")],
             [btn("📝 Как подавать", "sacr_zapiska"), btn("🏠 Меню", "main_menu")]]
        )
        return

    if step == "zapiska_upokoenie_names":
        set_step(user_id, "idle")
        names = [n.strip() for n in text.replace("\n", ",").split(",") if n.strip()]
        if not names:
            await send_message(chat_id, "⚠️ Введите хотя бы одно имя.", back_main())
            return
        zapiska = "ОБ УПОКОЕНИИ\n\n" + "\n".join(names[:10])
        await send_message(chat_id,
            "🕯️ Ваша записка об упокоении:\n\n" + zapiska + "\n\n"
            "Перепишите от руки и подайте в свечной лавке.\n"
            "Сорокоуст закажите отдельно если усопший недавно.",
            [[btn("✍️ Ещё записку", "make_zapiska")],
             [btn("📝 Как подавать", "sacr_zapiska"), btn("🏠 Меню", "main_menu")]]
        )
        return

    if step == "review":
        set_step(user_id, "idle")
        review_text = text.strip()
        if not review_text:
            await send_message(
                chat_id,
                "⚠️ Отзыв не может быть пустым. Напишите текст сообщения.",
                back_main()
            )
            return
        user_data = get_user(user_id)
        username = user_data.get("username", "")
        saved_name = (
            user_data.get("church_name")
            or user_data.get("first_name")
            or first_name
        )

        # Локальный номер связывает уведомление владельца, ответ и строку Sheets.
        review_id = create_review_record(
            chat_id,
            user_id,
            username,
            saved_name,
            review_text
        )

        # Google Sheets обновляется в фоне и не блокирует webhook.
        asyncio.create_task(asyncio.to_thread(
            sheets_add_review_max,
            review_id,
            user_id,
            username,
            saved_name,
            review_text
        ))

        try:
            owner_text = (
                f"💬 Новый отзыв #{review_id} в «С верой» MAX\n\n"
                f"Пользователь: {saved_name or '—'}\n"
                f"ID: {user_id}\n\n"
                f"{review_text[:2800]}"
            )
            await send_message(
                OWNER_ID,
                owner_text,
                [
                    [btn("✍️ Ответить пользователю", f"owner_review_reply:{review_id}")],
                    [btn("✅ Отметить обработанным", f"owner_review_done:{review_id}")]
                ]
            )
        except Exception as e:
            logging.error(f"Ошибка отправки отзыва владельцу: {e}")

        await send_message(
            chat_id,
            "☦️ Спасибо за ваш отзыв!\n\n"
            "Мы сохранили его. Если потребуется уточнение или ответ, команда проекта напишет вам прямо здесь, в MAX.\n\n"
            "Да хранит вас Господь 🕊️\n\nМожно ли использовать ваш отзыв в канале анонимно — без имени и личных данных?",
            [[btn("✅ Да, анонимно", "review_consent:yes")], [btn("Нет", "review_consent:no")], [btn("🏠 Меню", "main_menu")]]
        )
        set_funnel_flag(user_id, "MAX", "review_left", 1)
        track_attributed_event(user_id, "MAX", "review_submitted", target="review", value=str(review_id))
        return

    if step == "donate_amount":
        try:
            amount = int(text.strip())
            if amount < 10:
                await send_message(chat_id, "⚠️ Минимальная сумма — 10 рублей:")
                return
            if not YOOKASSA_SHOP_ID or not YOOKASSA_SECRET:
                record_critical_error("donation_config_max", "YOOKASSA_SHOP_ID/YOOKASSA_SECRET missing")
                await send_message(chat_id, "⚠️ Платежи временно недоступны. Владелец уже может увидеть ошибку в диагностике.", back_main())
                return
            from yookassa import Configuration, Payment as YPayment
            Configuration.account_id = YOOKASSA_SHOP_ID
            Configuration.secret_key = YOOKASSA_SECRET
            payment_payload = {
                "amount": {"value": f"{amount}.00", "currency": "RUB"},
                "confirmation": {"type": "redirect", "return_url": "https://sveroy.ru/payment/success"},
                "capture": True,
                "description": "Пожертвование на развитие «С верой»",
                "metadata": {"user_id": str(user_id), "platform": "MAX", "kind": "donation"},
                "receipt": {"customer": {"email": "6038484@mail.ru"}, "items": [{
                    "description": "Пожертвование на развитие «С верой»", "quantity": "1.00",
                    "amount": {"value": f"{amount}.00", "currency": "RUB"}, "vat_code": 1,
                    "payment_mode": "full_payment", "payment_subject": "another"
                }]},
            }
            payment = await asyncio.to_thread(YPayment.create, payment_payload, str(uuid.uuid4()))
            set_step(user_id, "idle")
            pay_url = payment.confirmation.confirmation_url
            save_donation_payment(payment.id, user_id, chat_id, user.get("username", ""), first_name or user.get("first_name", ""), amount, "MAX")
            await send_message(chat_id, f"🕯️ Пожертвование {amount} рублей\n\nНажмите для оплаты 👇", [
                [link_btn("💳 Перейти к оплате", pay_url)],
                back_main()[0],
            ])
        except ValueError:
            await send_message(chat_id, "⚠️ Введите сумму цифрой, например: 300")
        except Exception as e:
            logging.error(f"Ошибка платежа: {e}")
            err_text = str(e)
            if "return_url" in err_text.lower():
                user_msg = "⚠️ Ошибка настройки платежа (return_url). Обратитесь к администратору."
            elif "account" in err_text.lower() or "secret" in err_text.lower():
                user_msg = "⚠️ Ошибка авторизации ЮКасса. Обратитесь к администратору."
            else:
                user_msg = f"⚠️ Ошибка платежа. Попробуйте позже."
            await send_message(chat_id, user_msg, back_main())
        return

    if step and step.startswith("question_"):
        depth = step.replace("question_", "")
        await send_message(chat_id, "🙏 Отвечаю...")
        answer = await ask_claude(text, depth)
        set_step(user_id, "idle")
        # Обновляем активность в Sheets
        import threading
        threading.Thread(target=sheets_update_activity_max, args=(user_id,), daemon=True).start()
        if answer == "error":
            try:
                await max_request("POST", f"messages?chat_id=8935471523",
                    {"text": f"⚠️ Ошибка Claude в MAX боте\nПользователь: {user_id}\nВопрос: {text[:100]}"})
            except Exception:
                pass
            await send_message(chat_id,
                "⚠️ Не удалось получить ответ. Попробуйте позже.",
                [[btn("🔄 Попробовать снова", "ask_question")],
                 [link_btn("📢 Сообщить о проблеме", "https://t.me/Boss023rus")],
                 [btn("🏠 Меню", "main_menu")]])
        else:
            await send_message(chat_id, answer, [
                [btn("❓ Ещё вопрос", "ask_question"), btn("🏠 Меню", "main_menu")],
            ])
        return

    await send_message(chat_id, "☦️ Главное меню:", main_menu_buttons())

async def handle_photo(chat_id, user_id, photo_token):
    user = get_user(user_id)
    step = user.get("step", "")
    photo_type = "church" if step == "photo_church" else "icon"
    set_step(user_id, "idle")
    await send_message(chat_id, "⏳ Анализирую фото...")
    photo_bytes = await get_photo_bytes(photo_token)
    if not photo_bytes:
        await send_message(chat_id, "⚠️ Не удалось загрузить фото.", [[btn("◀️ Назад", "photo_menu")]])
        return
    result = await analyze_photo(photo_bytes, photo_type)
    await send_message(chat_id, result, [[btn("📸 Ещё фото", "photo_menu"), btn("🏠 Меню", "main_menu")]])

# ========== ЕВАНГЕЛИЕ И ПОСТ ДНЯ ==========
def get_fast_today_max() -> str:
    return fasting_guidance_text()


async def get_daily_gospel_max() -> str:
    return gospel_reflection_text()


# ========== МОЛИТВА ДНЯ И РАССЫЛКА ==========
async def get_prayer_of_day_max() -> str:
    today = datetime.now().strftime("%Y-%m-%d")
    conn = db_connect()
    c = conn.cursor()
    c.execute("SELECT prayer FROM daily_prayer_cache WHERE date=?", (today,))
    row = c.fetchone()
    conn.close()
    if row:
        return row[0]
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
        msg = await asyncio.to_thread(
            claude_client.messages.create,
            model="claude-sonnet-4-5",
            max_tokens=600,
            system="Ты православный справочный помощник. Пишешь только пример личного молитвенного обращения своими словами, не официальный церковный текст, не представляясь священником.",
            messages=[{"role": "user", "content": prompt}]
        )
        prayer = msg.content[0].text
        conn2 = db_connect()
        conn2.execute("INSERT OR REPLACE INTO daily_prayer_cache (date, prayer) VALUES (?,?)", (today, prayer))
        conn2.commit()
        conn2.close()
        return prayer
    except Exception as e:
        logging.error(f"Ошибка молитвы дня MAX: {e}")
        return PRAYER_TEXTS["prayer_morning_ru"][1]

async def morning_broadcast_max():
    """Утренняя рассылка всем пользователям MAX у кого включены уведомления"""
    conn = db_connect()
    c = conn.cursor()
    c.execute("SELECT user_id, church_name FROM users WHERE notifications=1")
    users = c.fetchall()
    conn.close()
    prayer = await get_prayer_of_day_max()
    day_str = date_ru("short")
    feast = get_todays_feast()
    feast_line = ("🎉 " + feast + "\n\n") if feast else ""
    text = "🌅 Доброе утро, " + day_str + "!\n\n" + feast_line + "☦️ Молитва дня\n\n" + prayer + "\n\n─────────────────\n☦️ Православный помощник → @id232007136009_1_bot"
    sent = 0
    for user_id, name in users:
        try:
            await max_request("POST", f"messages?chat_id={user_id}", {"text": text})
            sent += 1
            await asyncio.sleep(0.1)
        except Exception:
            pass
    logging.info(f"MAX утренняя рассылка: {sent} из {len(users)}")

async def angel_reminder_loop_max():
    """Напоминает только пользователям, которые явно включили уведомления."""
    await asyncio.sleep(35)
    last_run = ""
    while True:
        now_msk = datetime.utcnow() + timedelta(hours=3)
        run_key = now_msk.strftime("%Y-%m-%d")
        if now_msk.hour == 9 and run_key != last_run:
            last_run = run_key
            conn = db_connect()
            users = conn.execute(
                "SELECT user_id,church_name,angel_day FROM users "
                "WHERE notifications=1 AND angel_day<>'' AND angel_day IS NOT NULL"
            ).fetchall()
            conn.close()
            for user_id, name, angel_day in users:
                try:
                    day = datetime.strptime(angel_day.split(" ")[0], "%d.%m").replace(year=now_msk.year)
                    diff = (day.date() - now_msk.date()).days
                    if diff < 0:
                        diff = (day.replace(year=now_msk.year + 1).date() - now_msk.date()).days
                    if diff == 3:
                        await send_message(
                            user_id,
                            (
                                "🕊️ Через 3 дня — возможная дата памяти вашего небесного покровителя:\n\n"
                                f"{angel_day}\n\n"
                                "Точное определение дня ангела лучше уточнить у священника."
                            ),
                        )
                    elif diff == 0:
                        await send_message(
                            user_id,
                            (
                                "👼 Сегодня возможная дата памяти вашего небесного покровителя, "
                                f"{name or 'друг'}:\n\n{angel_day}\n\n"
                                "Можно помолиться святому своими словами. "
                                "Точную дату дня ангела лучше уточнить у священника."
                            ),
                        )
                except Exception as e:
                    logging.error(f"Ошибка напоминания MAX {user_id}: {e}")
        await asyncio.sleep(45)


# ========== АВТОПОСТИНГ В КАНАЛ — PREMIUM FUNNEL ==========
MAX_CHANNEL_ID = _config_int("MAX_CHANNEL_ID", -75405929805299)
MAX_BOT_URL = _env.get("MAX_BOT_URL") or os.environ.get("MAX_BOT_URL", "https://max.ru/id232007136009_1_bot")

# Визуалы канала временно полностью отключены.

def select_channel_visual(*args, **kwargs):
    return None

def build_channel_image_prompt(*args, **kwargs):
    return ""

CHANNEL_CTA = {
    "morning": ("🙏 Получите молитву дня — сразу, без поиска по меню.", "🙏 Получить молитву на сегодня", "ch_morning"),
    "quote": ("❓ Разберите именно свою ситуацию с помощником.", "❓ Разобрать мою ситуацию", "ch_quote"),
    "saint": ("👼 Найдите своего святого по имени.", "👼 Найти моего покровителя", "ch_saint"),
    "guidance": ("🕊️ Получите бережный ответ по вашей ситуации.", "🕊️ Получить бережный ответ", "ch_guidance"),
    "practical": ("⛪ Перейдите сразу к спокойной подготовке к исповеди.", "📿 Подготовиться к исповеди", "ch_practical"),
    "story": ("👼 Найдите святого по имени и возможные дни памяти.", "👼 Найти святого по имени", "ch_story"),
    "evening": ("🌙 Откройте вечернюю молитву — сразу в боте.", "🌙 Получить молитву перед сном", "ch_evening"),
    "qa": ("✍️ Задайте свой вопрос и выберите глубину ответа.", "✍️ Задать свой вопрос", "ch_qa"),
    "life": ("👼 Найдите небесного покровителя и молитву к нему.", "👼 Узнать моего покровителя", "ch_life"),
    "film": ("📚 Откройте подборку православных материалов.", "📚 Выбрать материал для чтения", "ch_film"),
    "gospel": ("📖 Прочитайте сегодняшнюю евангельскую мысль.", "📖 Прочитать мысль на сегодня", "ch_gospel"),
    "photo": ("📸 Отправьте фотографию иконы для предварительного определения.", "📸 Определить икону по фото", "ch_photo"),
    "church": ("🗺️ Перейдите сразу к поиску ближайшего храма.", "🗺️ Найти храм рядом", "ch_church"),
    "showcase_prayer": ("🙏 Получите молитву по вашей личной просьбе.", "🙏 Подобрать молитву по ситуации", "ch_showcase_prayer"),
    "showcase_photo": ("📸 Отправьте фото иконы для предварительного определения.", "📸 Узнать, что за икона", "ch_showcase_photo"),
    "showcase_angel": ("👼 Найдите святого по своему имени.", "👼 Узнать моего покровителя", "ch_showcase_angel"),
    "showcase_confession": ("📿 Откройте бережную памятку к первой исповеди.", "📿 Подготовиться спокойно", "ch_showcase_confession"),
    "interactive": ("💬 Выберите тему следующей полезной публикации.", "💬 Выбрать следующую тему", "ch_interactive"),
    "community": ("❓ Расскажите о похожей ситуации и получите личный ответ.", "❓ Разобрать похожую ситуацию", "ch_community"),
}

CHANNEL_CTA_B_LABELS = {
    "morning": "🙏 Начать день с молитвы",
    "quote": "❓ Получить ответ на мой вопрос",
    "saint": "👼 Узнать день ангела",
    "guidance": "🕊️ Разобрать, что меня тревожит",
    "practical": "📿 Открыть памятку к исповеди",
    "story": "👼 Найти небесного покровителя",
    "evening": "🌙 Завершить день с молитвой",
    "qa": "✍️ Спросить православного помощника",
    "life": "🙏 Получить молитву покровителю",
    "film": "📚 Открыть полезную подборку",
    "gospel": "📖 Прочитать и применить сегодня",
    "photo": "📸 Отправить фото иконы",
    "church": "🗺️ Найти ближайший храм",
    "showcase_prayer": "🙏 Получить молитву для меня",
    "showcase_photo": "📸 Определить образ",
    "showcase_confession": "📿 Подготовиться без страха",
    "interactive": "💬 Выбрать тему канала",
}




def save_post_source(post_key: str, source: str, variant: str):
    try:
        conn = _funnel_conn()
        conn.execute("UPDATE channel_posts SET source=?,variant=? WHERE post_key=?", (source, variant, post_key))
        conn.commit(); conn.close()
    except Exception as e:
        logging.error(f"Post source save error: {e}")


def get_channel_cta(cta_key: str, source_override: str = ""):
    footer, button, source = CHANNEL_CTA.get(cta_key, CHANNEL_CTA["guidance"])
    source = source_override or source
    if source_override.endswith("b"):
        button = CHANNEL_CTA_B_LABELS.get(cta_key, button)
    deep_link = f"{MAX_BOT_URL}?start={source}"
    return "\n\n─────────────────\n" + footer, [[link_btn(button, deep_link)]], deep_link


def channel_post_exists(post_key: str) -> bool:
    """Не допускает повторную отправку уже подтверждённого или выполняющегося слота."""
    conn = db_connect()
    row = conn.execute(
        "SELECT status,COALESCE(message_id,''),created_at FROM channel_posts WHERE post_key=?",
        (post_key,),
    ).fetchone()
    conn.close()
    if not row:
        return False
    status, message_id, created_at = row
    if status == "sent":
        return True
    if status == "sending" and created_at:
        try:
            return (datetime.now() - datetime.fromisoformat(created_at)).total_seconds() < 900
        except Exception:
            return True
    return False


def save_channel_post(post_key: str, post_date: str, slot: str, rubric: str, topic: str, content: str, status: str, message_id: str = ""):
    try:
        conn = db_connect()
        conn.execute(
            """INSERT OR REPLACE INTO channel_posts
               (post_key,post_date,slot,rubric,topic,content,status,created_at,message_id)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (post_key, post_date, slot, rubric, topic[:250], content[:3900], status, datetime.now().isoformat(), str(message_id or "")),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logging.error(f"Канал: не удалось сохранить журнал публикации: {e}")


def recent_channel_topics(limit: int = 30) -> str:
    try:
        conn = db_connect()
        rows = conn.execute(
            "SELECT rubric,topic FROM channel_posts WHERE status='sent' AND COALESCE(message_id,'')<>'' ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        conn.close()
        if not rows:
            return ""
        return "\n".join(f"- {rubric}: {topic}" for rubric, topic in rows if topic)
    except Exception:
        return ""


def extract_topic(text: str) -> str:
    clean = " ".join((text or "").replace("\n", " ").split())
    return clean[:180]


def _extract_upload_token(payload):
    if not isinstance(payload, dict):
        return ""
    if payload.get("token"):
        return str(payload["token"])
    for key in ("photo", "image", "file", "payload"):
        nested = payload.get(key)
        if isinstance(nested, dict) and nested.get("token"):
            return str(nested["token"])
    photos = payload.get("photos")
    values = photos.values() if isinstance(photos, dict) else photos if isinstance(photos, list) else []
    for item in values:
        if isinstance(item, dict) and item.get("token"):
            return str(item["token"])
    return ""


def _extract_message_id(payload):
    """Безопасно извлекает ID сообщения из разных форматов ответа MAX."""
    if payload is None:
        return ""
    if isinstance(payload, dict):
        for key in ("message_id", "mid", "id"):
            value = payload.get(key)
            if value not in (None, "") and not isinstance(value, (dict, list)):
                return str(value)
        for key in ("message", "body", "data", "result", "payload"):
            if key in payload:
                found = _extract_message_id(payload.get(key))
                if found:
                    return found
        for value in payload.values():
            if isinstance(value, (dict, list)):
                found = _extract_message_id(value)
                if found:
                    return found
    elif isinstance(payload, list):
        for item in payload:
            found = _extract_message_id(item)
            if found:
                return found
    return ""


def _max_response_ok(payload):
    if not isinstance(payload, dict) or not payload:
        return False
    raw = str(payload).lower()
    if "error" in payload or "errors" in payload or "attachment.not.ready" in raw:
        return False
    return bool(payload.get("message") or payload.get("body") or payload.get("timestamp") or payload.get("message_id") or payload.get("success") is True)


def _attachment_not_ready(payload):
    return "attachment.not.ready" in str(payload).lower()



async def generate_channel_image_bytes(*args, **kwargs):
    return None

async def post_to_channel(
    text, photo_url=None, buttons=None, deep_link=None,
    generation_prompt="", cache_key="", prefer_generated=False,
    visual_title="", show_visual_title=False,
):
    """Публикует текстовый MAX-пост и сохраняет CTA-кнопку воронки."""
    base_text = clean_channel_markup(text)
    payload = {"text": base_text[:4000]}
    if buttons:
        payload["attachments"] = [{"type": "inline_keyboard", "payload": {"buttons": buttons}}]
    try:
        result = await max_request("POST", f"messages?chat_id={MAX_CHANNEL_ID}", payload)
        if not _max_response_ok(result):
            logging.error(f"Канал MAX: API не подтвердил публикацию: {result}")
            return ""
        message_id = _extract_message_id(result) or f"confirmed_{int(datetime.now().timestamp())}"
        logging.info(f"Канал MAX: текст+CTA отправлены, message_id={message_id}")
        return message_id
    except Exception as e:
        logging.error(f"Канал MAX: публикация не удалась: {e}")
        return ""


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
    "saint": "👼 Святитель Лука Крымский был хирургом и архиереем. Даже в годы ссылок он продолжал лечить людей и сохранять верность своему служению. Его пример напоминает: вера не уводит от ответственности, а помогает честно делать необходимое для другого человека.",
    "guidance": "🕯️ Когда молитва не идёт, не нужно отчаиваться. Скажите Богу несколько простых слов своими словами и останьтесь в тишине. Верность важнее сильных чувств.",
    "practical": "⛪ Первый шаг в храме не требует идеальной подготовки. Придите немного заранее, встаньте там, где удобно, и спокойно наблюдайте за службой. Если что-то непонятно, после богослужения можно вежливо спросить служителя храма.",
    "story": "👼 После личной трагедии преподобномученица Елисавета Феодоровна посвятила себя помощи больным и бедным. Её история показывает: боль может не только замкнуть сердце, но и стать началом деятельного милосердия.",
    "evening": "🌙 Господи, благодарю Тебя за прошедший день. Прости всё, чем я согрешил словом, делом и мыслью. Сохрани моих близких и даруй нам мирный сон. Аминь.",
    "qa": "❓ Можно ли молиться своими словами? Да. Церковные молитвы учат нас, но Господь слышит и искреннее обращение сердца. Говорите просто, честно и с доверием.",
    "life": "📖 Праведный Иоанн Кронштадтский не ограничивался словами о сострадании: он посещал бедные семьи и помогал создавать возможность для труда. Его пример задаёт простой вопрос: во что сегодня может превратиться наше сочувствие?",
    "film": "📽️ Для семейного просмотра выберите проверенный документальный фильм о православных святынях или истории монастыря. После просмотра обсудите, какая мысль особенно затронула каждого.",
    "showcase_prayer": "🙏 Не знаете, какую молитву прочитать в тревоге, дороге, болезни или перед сном? В православном помощнике молитвы собраны по жизненным ситуациям — нужное можно открыть за несколько секунд.",
    "showcase_photo": "📸 Иногда дома хранится икона, но семья уже не помнит, кто на ней изображён. Отправьте фотографию православному помощнику — он постарается определить образ и объяснить символы.",
    "showcase_angel": "👼 День ангела связан с памятью святого, чьё имя человек носит в Крещении. В помощнике можно найти имя и посмотреть возможные дни памяти.",
    "showcase_confession": "📿 Первая исповедь часто пугает неизвестностью. В помощнике есть спокойная пошаговая памятка: как подготовиться, что говорить и как проходит Таинство.",
    "interactive": "💬 Какую тему разобрать следующей: молитву, первую исповедь, день ангела или внутреннюю тревогу? Выберите вариант — канал будет развиваться по реальным запросам читателей.",
    "community": "🕊️ Один из пользователей поделился, что помощник помог спокойнее сделать первый шаг к церковной жизни. Иногда человеку нужна не длинная лекция, а понятный следующий шаг и бережная поддержка.",
}


async def generate_channel_post(prompt, cta_key, rubric, visual_prompt_note="", visual_title="", source_override=""):
    history = recent_channel_topics(35)
    history_note = f"\n\nНе повторяй эти недавние темы:\n{history}" if history else ""
    visual_note = f"\n\n{visual_prompt_note}" if visual_prompt_note else ""
    length_rule = "700–1050" if visual_prompt_note else "850–1250"
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
            asyncio.to_thread(
                claude_client.messages.create,
                model="claude-sonnet-4-5",
                max_tokens=500,
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
        text = msg.content[0].text.strip()
        if len(text) < 60:
            raise RuntimeError("AI вернул слишком короткий текст")
    except Exception as e:
        logging.error(f"Канал: генерация {rubric} не удалась, используется fallback: {e}")
        text = FALLBACK_POSTS.get(cta_key, FALLBACK_POSTS["guidance"])

    text = polish_channel_text(
        text, cta_key, rubric,
        has_visual=bool(visual_prompt_note or visual_title),
        platform="max",
    )
    footer, buttons, deep_link = get_channel_cta(cta_key, source_override)
    return text + footer, buttons, deep_link, extract_topic(text)


def build_daily_slots(msk_now: datetime):
    day = msk_now.strftime("%d.%m")
    weekday = msk_now.weekday()
    midday_rotation = {
        0: ("церковное слово", "practical", "Объясни один церковный термин простыми словами. Не выдумывай происхождение или правила."),
        1: ("вопрос новичка", "qa", "Разбери один частый вопрос начинающего. Отделяй общецерковную норму от приходской практики."),
        2: ("история святого", "story", saint_story_prompt(msk_now, offset=5, format_kind="episode")),
        3: ("храм и традиция", "church", "Объясни одну православную традицию без категоричных указаний и напомни, что местная практика может отличаться."),
        4: ("подготовка к Таинству", "practical", "Дай общую бережную памятку и обязательно предложи уточнить правила у священника своего прихода."),
        5: ("житие и пример", "story", saint_story_prompt(msk_now, offset=9, format_kind="weekend")),
        6: ("воскресное размышление", "gospel", "Раскрой одну евангельскую мысль для семейного разговора. Не называй её богослужебным чтением дня."),
    }
    midday = midday_rotation[weekday]
    return [
        (7, "утренняя молитва", "morning", f"Короткая утренняя публикация на {day}: благодарность и один спокойный настрой на день."),
        (9, "святой или праздник дня", "saint", "__DYNAMIC_SAINT__"),
        (13, midday[0], midday[1], midday[2]),
        (20, "вечерняя молитва", "evening", "Короткая вечерняя публикация: благодарность, просьба о прощении и мирном сне."),
    ]



# Проверенная редакционная библиотека для постов о святых.
# Она не зависит от AI-календаря: модель получает только заранее заданные факты.
SAINT_STORY_LIBRARY = [
    {
        "name": "святитель Николай Чудотворец",
        "theme": "тайная помощь и милосердие",
        "facts": "Святитель Николай был епископом Мир Ликийских. Предание Церкви связывает с ним тайную помощь обедневшему отцу трёх дочерей: помощь была оказана незаметно, без ожидания благодарности.",
        "lesson": "Настоящее добро не требует внимания к себе и бережёт достоинство того, кому помогают.",
    },
    {
        "name": "преподобный Сергий Радонежский",
        "theme": "смирение, труд и примирение",
        "facts": "Преподобный Сергий основал обитель в радонежских лесах, сам трудился вместе с братией и избегал почестей. К нему обращались за советом в тяжёлые времена раздоров.",
        "lesson": "Мир вокруг часто начинается с внутренней тишины, труда без ропота и готовности первым сделать шаг к примирению.",
    },
    {
        "name": "святитель Лука Крымский",
        "theme": "служение людям в испытаниях",
        "facts": "Святитель Лука был выдающимся хирургом и архиереем. Он продолжал лечить людей, преподавать медицину и сохранять веру даже в годы ссылок и гонений.",
        "lesson": "Вера не освобождает от профессиональной ответственности, а помогает служить человеку честно даже в тяжёлых обстоятельствах.",
    },
    {
        "name": "преподобный Серафим Саровский",
        "theme": "мир сердца и внимание к человеку",
        "facts": "Преподобный Серафим много лет жил в молитве и уединении, а затем принимал множество людей, обращаясь к ним с теплом и надеждой.",
        "lesson": "Духовная жизнь узнаётся не по суровости к другим, а по миру, который человек приносит тем, кто рядом.",
    },
    {
        "name": "блаженная Ксения Петербургская",
        "theme": "самоотверженность и помощь ближним",
        "facts": "После смерти мужа Ксения раздала имущество нуждающимся и избрала путь добровольной бедности. Жители Петербурга запомнили её как человека молитвы и бескорыстной помощи.",
        "lesson": "Даже пережив большую утрату, человек может не замкнуться в боли, а превратить её в сострадание к другим.",
    },
    {
        "name": "праведный Иоанн Кронштадтский",
        "theme": "внимание к бедным и деятельная вера",
        "facts": "Праведный Иоанн служил в Кронштадте, посещал бедные семьи, помогал нуждающимся и участвовал в создании Дома трудолюбия, где люди могли получить работу и поддержку.",
        "lesson": "Сочувствие становится христианской любовью тогда, когда превращается в конкретную помощь.",
    },
    {
        "name": "преподобномученица Елисавета Феодоровна",
        "theme": "милосердие после личной трагедии",
        "facts": "После гибели мужа великая княгиня Елисавета Феодоровна посвятила себя служению больным и бедным и основала Марфо-Мариинскую обитель милосердия.",
        "lesson": "Христианское прощение не отменяет боли, но не позволяет боли превратиться в ненависть.",
    },
    {
        "name": "святой благоверный князь Александр Невский",
        "theme": "ответственность и трудный выбор",
        "facts": "Князю Александру пришлось защищать русские земли и одновременно принимать сложные дипломатические решения ради сохранения народа в тяжёлую эпоху.",
        "lesson": "Ответственность иногда требует не эффектного поступка, а трезвого решения, которое сохраняет других.",
    },
    {
        "name": "святитель Спиридон Тримифунтский",
        "theme": "простота и забота о нуждающихся",
        "facts": "Святитель Спиридон происходил из простой семьи, был пастухом, а став епископом, сохранил простоту жизни и особое внимание к бедным.",
        "lesson": "Высокое положение не делает человека ближе к Богу; важнее простота, щедрость и доступность для тех, кому трудно.",
    },
    {
        "name": "святая равноапостольная княгиня Ольга",
        "theme": "перемена жизни и мудрость",
        "facts": "Княгиня Ольга приняла христианство и стала одной из первых правительниц Руси, открыто исповедовавших новую веру. Её выбор подготовил почву для Крещения Руси.",
        "lesson": "Человек не обязан навсегда оставаться пленником прежних решений: искренняя перемена может повлиять и на будущие поколения.",
    },
    {
        "name": "святой равноапостольный князь Владимир",
        "theme": "покаяние и изменение направления жизни",
        "facts": "Крещение князя Владимира стало поворотным событием его собственной жизни и истории Руси. После принятия христианства изменились его отношение к людям и государственные решения.",
        "lesson": "Покаяние — это не только сожаление о прошлом, но и реальная смена образа жизни.",
    },
    {
        "name": "преподобный Амвросий Оптинский",
        "theme": "бережный совет и терпение",
        "facts": "К преподобному Амвросию приезжали люди самого разного положения. Он умел отвечать просто, с юмором и вниманием к конкретной человеческой боли.",
        "lesson": "Добрый совет начинается не с готовой формулы, а с умения услышать человека.",
    },
    {
        "name": "святитель Тихон Задонский",
        "theme": "борьба с унынием и надежда",
        "facts": "Святитель Тихон много писал о внутренней жизни христианина, милосердии и надежде на Бога. Сам он переживал периоды тяжёлого душевного состояния.",
        "lesson": "Духовная зрелость не означает отсутствия тяжёлых чувств; важно не оставаться с ними в одиночестве и продолжать искать помощь.",
    },
    {
        "name": "святой праведный Алексий Мечёв",
        "theme": "сострадание вместо осуждения",
        "facts": "Праведный Алексий Мечёв служил в московском храме святителя Николая в Клённиках. Люди запомнили его как пастыря, который внимательно выслушивал и старался не подавлять человека строгостью.",
        "lesson": "Иногда человеку прежде всего нужно, чтобы его увидели и выслушали без унижения.",
    },
]


def saint_story_for_date(msk_now: datetime, offset: int = 0) -> dict:
    """Детерминированно выбирает историю; разные рубрики дня получают разных святых."""
    index = (msk_now.date().toordinal() + int(offset)) % len(SAINT_STORY_LIBRARY)
    return SAINT_STORY_LIBRARY[index]


def saint_story_prompt(msk_now: datetime, offset: int = 0, format_kind: str = "portrait") -> str:
    item = saint_story_for_date(msk_now, offset)
    if format_kind == "episode":
        structure = (
            "Сделай главным один человеческий эпизод или трудный выбор. "
            "Покажи ситуацию, поступок и то, чему она может научить современного человека."
        )
    elif format_kind == "weekend":
        structure = (
            "Расскажи историю так, чтобы её хотелось пересказать близкому: начало с интригующей ситуации, "
            "затем поступок святого, итог и вопрос читателю для личного размышления."
        )
    else:
        structure = (
            "Кратко представь человека, расскажи один яркий эпизод из указанных фактов, "
            "свяжи его с обычной жизненной ситуацией и заверши практическим выводом."
        )
    return (
        f"Подготовь живой редакционный пост о {item['name']}. Тема: {item['theme']}. "
        f"Разрешённые факты: {item['facts']} "
        f"Главный вывод: {item['lesson']} {structure} "
        "Не добавляй других биографических деталей, точных дат, прямых цитат, чудес или преданий, которых нет в разрешённых фактах. "
        "Не называй этого святого святым сегодняшнего дня, если это прямо не указано в календарном контексте."
    )


def dynamic_saint_prompt(msk_now: datetime) -> str:
    date_text = msk_now.strftime("%d.%m")
    feast = FIXED_FEASTS.get(date_text, "")
    if feast:
        return f"Сегодня {date_text}, фиксированный праздник: {feast}. Объясни его смысл, опираясь только на общеизвестные проверяемые сведения, и дай один практический вывод. Не выдумывай традиции и факты."
    return saint_story_prompt(msk_now, offset=0, format_kind="portrait")



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
    wd = msk_now.weekday()
    if wd == 1:
        return [(17, "возможности помощника", "showcase_prayer", FALLBACK_POSTS["showcase_prayer"])]
    if wd == 2:
        return [(17, "выбор темы читателями", "interactive", "INTERACTIVE_WEEKLY")]
    if wd == 3:
        return [(17, "практическая помощь", "showcase_confession", FALLBACK_POSTS["showcase_confession"])]
    if wd == 4:
        voted = top_interactive_topic("MAX")
        return [(17, "тема по выбору читателей", "guidance", interactive_topic_prompt(voted))] if voted else []
    if wd == 5:
        return [(11, "житие недели", "life", saint_story_prompt(msk_now, offset=12, format_kind="weekend"))]
    if wd == 6:
        item = trusted_media_for_week(msk_now)
        return [(11, "книга или фильм недели", "film", f"Представь проверенную рекомендацию: {item}. Не добавляй неподтверждённых дат, наград или сюжетных подробностей.")]
    return []




CHANNEL_FAILSAFE_DAILY_LIMIT = 5
CHANNEL_FAILSAFE_COOLDOWN_MINUTES = 20


def _channel_alert_once_per_day(key: str) -> bool:
    day_key = datetime.utcnow().strftime("%Y-%m-%d")
    setting_key = f"channel_alert_{key}_{day_key}"
    if get_app_setting(setting_key, ""):
        return False
    set_app_setting(setting_key, "1")
    return True


def acquire_channel_publish_guard(post_key: str, post_date: str, slot: str, rubric: str) -> tuple[bool, str]:
    """Atomic fail-safe guard. Any first attempt permanently locks the slot."""
    conn = db_connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute("SELECT status FROM channel_posts WHERE post_key=?", (post_key,)).fetchone()
        if existing:
            conn.rollback()
            return False, f"slot_locked:{existing[0]}"
        day_count = conn.execute(
            "SELECT COUNT(*) FROM channel_posts WHERE post_date=? AND status IN ('reserved','sending','sent','uncertain_locked','failed_locked')",
            (post_date,),
        ).fetchone()[0]
        if int(day_count or 0) >= CHANNEL_FAILSAFE_DAILY_LIMIT:
            conn.rollback()
            return False, "daily_limit"
        latest = conn.execute(
            "SELECT created_at FROM channel_posts WHERE status IN ('reserved','sending','sent','uncertain_locked','failed_locked') ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        if latest and latest[0]:
            try:
                age = (datetime.now() - datetime.fromisoformat(latest[0])).total_seconds()
                if age < CHANNEL_FAILSAFE_COOLDOWN_MINUTES * 60:
                    conn.rollback()
                    return False, "cooldown"
            except Exception:
                pass
        conn.execute(
            """INSERT INTO channel_posts(post_key,post_date,slot,rubric,topic,content,status,created_at,message_id)
               VALUES (?,?,?,?,?,'','reserved',?,'')""",
            (post_key, post_date, slot, rubric, "", datetime.now().isoformat()),
        )
        conn.commit()
        return True, "reserved"
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def finalize_channel_publish_guard(post_key: str, status: str, topic: str = "", content: str = "", message_id: str = "") -> None:
    """Final states are permanent and are never retried automatically."""
    conn = db_connect()
    try:
        conn.execute(
            "UPDATE channel_posts SET status=?,topic=?,content=?,message_id=? WHERE post_key=?",
            (status, (topic or "")[:250], (content or "")[:4000], str(message_id or ""), post_key),
        )
        conn.commit()
    finally:
        conn.close()


CHANNEL_PUBLISH_LOCK = asyncio.Lock()



async def publish_channel_slot(msk_now: datetime, hour: int, rubric: str, cta_key: str, prompt: str):
    """Exactly one network attempt per slot. No fallback and no automatic retry."""
    async with CHANNEL_PUBLISH_LOCK:
        date_key = msk_now.strftime("%Y-%m-%d")
        post_key = f"{date_key}_{hour:02d}_{rubric}"
        acquired, reason = acquire_channel_publish_guard(post_key, date_key, f"{hour:02d}:00", rubric)
        if not acquired:
            logging.warning(f"Канал MAX: публикация заблокирована защитой ({reason}) — {post_key}")
            return False
        source = ""
        try:
            if prompt == "__DYNAMIC_SAINT__":
                prompt = dynamic_saint_prompt(msk_now)
            variant = "b" if (int(msk_now.strftime("%Y%m%d")) + int(hour)) % 2 else "a"
            source = make_post_source("m", msk_now, hour, cta_key, variant)
            record_post_experiment(source, "MAX", post_key, cta_key, variant)
            text, buttons, deep_link, topic = await generate_channel_post(
                prompt, cta_key, rubric, visual_prompt_note="", visual_title="", source_override=source,
            )
            save_post_source(post_key, source, variant)
            finalize_channel_publish_guard(post_key, "sending", topic, text, "")
            try:
                message_id = await asyncio.wait_for(
                    post_to_channel(text, None, buttons, deep_link),
                    timeout=CHANNEL_POST_TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError:
                finalize_channel_publish_guard(post_key, "uncertain_locked", topic, text, "")
                set_app_setting("max_last_channel_failure", f"{datetime.now().isoformat()} | uncertain | {post_key}")
                logging.error(f"Канал MAX: таймаут публикации; слот навсегда заблокирован без повтора — {post_key}")
                if _channel_alert_once_per_day("max_uncertain"):
                    await send_message(OWNER_ID, "⚠️ MAX: результат одной публикации не подтверждён. Автоповтор отключён, слот заблокирован. Проверьте канал вручную.")
                return False
            if message_id:
                finalize_channel_publish_guard(post_key, "sent", topic, text, message_id)
                set_app_setting("max_last_channel_failure", "")
                track_funnel_event(OWNER_ID, "MAX", "channel_post_published", source=source, target=cta_key, value=post_key, metadata=rubric)
                logging.info(f"Канал MAX: опубликовано один раз — {rubric}")
                if hour == 7:
                    asyncio.create_task(morning_broadcast_max())
                return True
            finalize_channel_publish_guard(post_key, "uncertain_locked", topic, text, "")
            set_app_setting("max_last_channel_failure", f"{datetime.now().isoformat()} | no_confirmation | {post_key}")
            logging.error(f"Канал MAX: нет подтверждения API; слот заблокирован без повтора — {post_key}")
            if _channel_alert_once_per_day("max_no_confirmation"):
                await send_message(OWNER_ID, "⚠️ MAX не подтвердил публикацию. Повтор автоматически не выполняется, чтобы исключить спам.")
            return False
        except Exception as e:
            finalize_channel_publish_guard(post_key, "failed_locked", "", str(e), "")
            set_app_setting("max_last_channel_failure", f"{datetime.now().isoformat()} | failed_locked | {post_key} | {str(e)[:300]}")
            logging.exception(f"Канал MAX: ошибка до/во время единственной попытки; слот заблокирован — {post_key}")
            if _channel_alert_once_per_day("max_publish_error"):
                try:
                    await send_message(OWNER_ID, f"⚠️ MAX: публикация остановлена защитой. Автоповтора нет. Ошибка: {str(e)[:500]}")
                except Exception:
                    pass
            return False



def channel_posts_today(msk_now: datetime):
    """Возвращает журнал публикаций канала за московскую дату."""
    try:
        date_key = msk_now.strftime("%Y-%m-%d")
        conn = db_connect()
        rows = conn.execute(
            """SELECT slot,rubric,status,created_at,COALESCE(message_id,'')
               FROM channel_posts
               WHERE post_date=?
               ORDER BY slot,created_at""",
            (date_key,),
        ).fetchall()
        conn.close()
        return rows
    except Exception as e:
        logging.error(f"Канал: не удалось прочитать журнал: {e}")
        return []


def select_catchup_channel_slot(msk_now: datetime):
    """Выбирает один актуальный пропущенный слот, отдавая приоритет визуалу.

    Более старый текстовый пост не догоняется, если более поздний слот уже
    отмечен успешным. Это исключает появление утренней «стены текста» после
    рестарта в середине дня.
    """
    slots = sorted(build_daily_slots(msk_now) + special_slots(msk_now), key=lambda item: item[0])
    due_slots = [slot for slot in slots if slot[0] <= msk_now.hour]
    date_key = msk_now.strftime("%Y-%m-%d")
    sent_rows = channel_posts_today(msk_now)
    sent_hours = []
    for slot, _rubric, status, _created, message_id in sent_rows:
        if status == "sent" and str(message_id).strip():
            try:
                sent_hours.append(int(str(slot).split(":", 1)[0]))
            except Exception:
                pass
    latest_sent_hour = max(sent_hours, default=-1)

    eligible = []
    for slot in due_slots:
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


async def publish_latest_missed_slot(msk_now: datetime):
    """После перезапуска выпускает только один актуальный пропущенный пост."""
    slot = select_catchup_channel_slot(msk_now)
    if slot is None:
        logging.info("Канал: актуальных пропущенных публикаций нет")
        return False
    hour, rubric, cta_key, prompt = slot
    logging.info(f"Канал: восстановление после запуска — {hour:02d}:00, {rubric}")
    return await publish_channel_slot(msk_now, hour, rubric, cta_key, prompt)



MAX_CHANNEL_INTRO = (
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


def max_intro_buttons():
    def dl(source):
        return f"{MAX_BOT_URL}?start={source}"
    return [
        [link_btn("☦️ Начать за 60 секунд", dl("ch_start60"))],
        [link_btn("🙏 Молитвы", dl("ch_morning")), link_btn("👼 День ангела", dl("ch_saint"))],
        [link_btn("📿 Подготовка к исповеди", dl("ch_showcase_confession"))],
        [link_btn("📸 Узнать икону", dl("ch_photo")), link_btn("❓ Задать вопрос", dl("ch_guidance"))],
        [link_btn("☦️ Открыть помощника", MAX_BOT_URL)],
    ]


async def publish_and_pin_max_intro():
    existing_id = get_app_setting("max_intro_message_id")
    if existing_id:
        pin_result = await max_request("PUT", f"chats/{MAX_CHANNEL_ID}/pin", {"message_id": existing_id, "notify": False})
        if isinstance(pin_result, dict) and (pin_result.get("success") is True or not pin_result.get("error")):
            set_app_setting("max_intro_pinned", "1")
            return True, "Существующий приветственный пост закреплён повторно — дубль не создан."
    payload = {"text": MAX_CHANNEL_INTRO, "attachments": [{"type": "inline_keyboard", "payload": {"buttons": max_intro_buttons()}}]}
    result = await max_request("POST", f"messages?chat_id={MAX_CHANNEL_ID}", payload)
    if not _max_response_ok(result):
        return False, f"MAX не подтвердил публикацию: {result}"
    message_id = _extract_message_id(result)
    if not message_id:
        return True, "Пост опубликован, но ID сообщения не найден — закрепите его вручную."
    set_app_setting("max_intro_message_id", message_id)
    pin_result = await max_request("PUT", f"chats/{MAX_CHANNEL_ID}/pin", {"message_id": message_id, "notify": False})
    pinned = isinstance(pin_result, dict) and (pin_result.get("success") is True or not pin_result.get("error"))
    set_app_setting("max_intro_pinned", "1" if pinned else "0")
    return True, "Приветственный пост опубликован и закреплён." if pinned else f"Пост опубликован, но MAX не подтвердил закрепление: {pin_result}"



def save_donation_payment(payment_id, user_id, chat_id, username, first_name, amount, platform):
    now = datetime.now(); expires = now + timedelta(hours=72)
    conn = db_connect()
    conn.execute("""INSERT OR REPLACE INTO donation_payments
        (payment_id,user_id,chat_id,username,first_name,amount,platform,status,created_at,expires_at,checked_at,last_error)
        VALUES (?,?,?,?,?,?,?,'pending',?,?,?,'')""",
        (str(payment_id), int(user_id), int(chat_id), username or "", first_name or "", int(amount), platform, now.isoformat(), expires.isoformat(), now.isoformat()))
    conn.commit(); conn.close()



def _mark_donation_field(payment_id, field, value=1):
    allowed = {"status","user_notified","owner_notified","sheet_recorded","paid_at","checked_at","expires_at","last_error"}
    if field not in allowed:
        return
    conn = db_connect(); conn.execute(f"UPDATE donation_payments SET {field}=? WHERE payment_id=?", (value, str(payment_id))); conn.commit(); conn.close()



def payments_report_text(platform: str) -> str:
    conn = db_connect()
    rows = conn.execute("SELECT status,COUNT(*),COALESCE(SUM(amount),0) FROM donation_payments WHERE platform=? GROUP BY status ORDER BY status", (platform,)).fetchall()
    pending = conn.execute("SELECT payment_id,amount,created_at,last_error FROM donation_payments WHERE platform=? AND status IN ('pending','waiting_for_capture') ORDER BY created_at LIMIT 10", (platform,)).fetchall()
    conn.close()
    lines = ["💳 Платежи и пожертвования"] + [f"• {status}: {count} платежей, {total} ₽" for status,count,total in rows]
    if pending:
        lines.append("\nОжидают проверки:")
        lines.extend(f"• {pid}: {amount} ₽, {created[:16]}{(' — '+err[:80]) if err else ''}" for pid,amount,created,err in pending)
    return "\n".join(lines)

async def check_donation_payments_loop_max():
    from yookassa import Configuration, Payment as YPayment
    await asyncio.sleep(20)
    if not YOOKASSA_SHOP_ID or not YOOKASSA_SECRET:
        logging.error("ЮКасса MAX не настроена: проверка пожертвований отключена")
        record_critical_error("donation_config_max", "YOOKASSA_SHOP_ID/YOOKASSA_SECRET missing")
        return
    Configuration.account_id = YOOKASSA_SHOP_ID
    Configuration.secret_key = YOOKASSA_SECRET
    while True:
        try:
            now = datetime.now()
            conn = db_connect()
            rows = conn.execute("""SELECT payment_id,user_id,chat_id,username,first_name,amount,status,user_notified,owner_notified,sheet_recorded,created_at,expires_at
                FROM donation_payments WHERE status IN ('pending','waiting_for_capture') OR (status='succeeded' AND (user_notified=0 OR owner_notified=0 OR sheet_recorded=0))""").fetchall()
            conn.close()
            for payment_id,user_id,chat_id,username,first_name,amount,status,user_n,owner_n,sheet_n,created_at,expires_at in rows:
                try:
                    if status != "succeeded":
                        expiry = datetime.fromisoformat(expires_at) if expires_at else datetime.fromisoformat(created_at) + timedelta(hours=72)
                        if now >= expiry:
                            _mark_donation_field(payment_id, "status", "expired"); continue
                        payment = await asyncio.to_thread(YPayment.find_one, payment_id)
                        remote = str(getattr(payment, "status", "pending"))
                        _mark_donation_field(payment_id, "checked_at", now.isoformat())
                        if remote in {"canceled", "cancelled"}:
                            _mark_donation_field(payment_id, "status", "canceled"); continue
                        if remote != "succeeded":
                            _mark_donation_field(payment_id, "status", remote if remote in {"pending","waiting_for_capture"} else "pending"); continue
                        _mark_donation_field(payment_id, "status", "succeeded"); _mark_donation_field(payment_id, "paid_at", now.isoformat())
                        set_funnel_flag(user_id, "MAX", "donation_made", 1); track_attributed_event(user_id, "MAX", "donation_succeeded", target="donate", value=str(amount))
                    if not user_n:
                        result = await send_message(chat_id, f"🕯️ Пожертвование {amount} рублей прошло успешно.\n\nБлагодарим за поддержку проекта «С верой». Да хранит вас Господь!", main_menu_buttons())
                        if _max_response_ok(result): _mark_donation_field(payment_id, "user_notified", 1)
                    if not owner_n:
                        result = await send_message(OWNER_ID, f"💰 Новое пожертвование в «С верой» MAX\n\nСумма: {amount} ₽\nПользователь: {first_name or '—'}\nUsername: @{username if username else '—'}\nID: {user_id}\nPayment ID: {payment_id}")
                        if _max_response_ok(result): _mark_donation_field(payment_id, "owner_notified", 1)
                    if not sheet_n:
                        ok = await asyncio.to_thread(sheets_add_donation, user_id, username, first_name, amount, "MAX")
                        if ok: _mark_donation_field(payment_id, "sheet_recorded", 1)
                except Exception as e:
                    _mark_donation_field(payment_id, "last_error", str(e)[:1000]); record_critical_error("donation_max", e)
        except Exception as e:
            logging.error(f"MAX donation loop error: {e}"); record_critical_error("donation_loop_max", e)
        await asyncio.sleep(60)




async def channel_scheduler():
    """Fail-safe scheduler: exact slots only, no catch-up and no retry."""
    await asyncio.sleep(15)
    processed_windows = set()
    while True:
        try:
            msk_now = datetime.utcnow() + timedelta(hours=3)
            set_app_setting("max_channel_scheduler_heartbeat", msk_now.isoformat())
            window = f"{msk_now:%Y-%m-%d-%H}"
            if window not in processed_windows and msk_now.minute < 10:
                processed_windows.add(window)
                processed_windows = {x for x in processed_windows if x.startswith(msk_now.strftime("%Y-%m-%d"))}
                for hour, rubric, cta_key, prompt in build_daily_slots(msk_now) + special_slots(msk_now):
                    if msk_now.hour == hour:
                        await publish_channel_slot(msk_now, hour, rubric, cta_key, prompt)
                        break
        except Exception as e:
            logging.exception(f"Канал MAX: ошибка планировщика: {e}")
            set_app_setting("max_last_channel_failure", f"{datetime.now().isoformat()} | scheduler | {str(e)[:500]}")
            if _channel_alert_once_per_day("max_scheduler"):
                try:
                    await send_message(OWNER_ID, f"⚠️ Планировщик MAX остановил текущий слот без повтора. Ошибка: {str(e)[:500]}")
                except Exception:
                    pass
        await asyncio.sleep(30)




async def channel_scheduler_supervisor():
    """Перезапускает планировщик, если его задача неожиданно завершилась."""
    while True:
        task = asyncio.create_task(channel_scheduler())
        try:
            await task
        except asyncio.CancelledError:
            task.cancel()
            raise
        except Exception as e:
            logging.exception(f"Канал MAX: планировщик аварийно завершился: {e}")
            set_app_setting("max_last_channel_failure", f"{datetime.now().isoformat()} | supervisor | {str(e)[:500]}")
            try:
                await send_message(OWNER_ID, f"⚠️ Планировщик MAX-канала перезапускается.\n\n{str(e)[:700]}")
            except Exception:
                pass
        await asyncio.sleep(10)



async def channel_watchdog_loop():
    """Read-only watchdog. It never publishes channel posts."""
    await asyncio.sleep(90)
    while True:
        try:
            msk_now = datetime.utcnow() + timedelta(hours=3)
            heartbeat = get_app_setting("max_channel_scheduler_heartbeat", "")
            if heartbeat:
                try:
                    age = (msk_now - datetime.fromisoformat(heartbeat)).total_seconds()
                except Exception:
                    age = 0
                if age > 300 and _channel_alert_once_per_day("max_stale"):
                    await send_message(OWNER_ID, f"⚠️ Нет пульса планировщика MAX уже {int(age)} секунд. Watchdog ничего не публикует автоматически.")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logging.exception(f"Канал MAX: ошибка read-only watchdog: {e}")
        await asyncio.sleep(300)



# ========== FASTAPI / LIFECYCLE ==========
app = FastAPI(title="С верой — MAX", version="5.0.2")
BACKGROUND_TASKS = set()


def spawn_background(coro):
    """Запускает корутину и удерживает ссылку на задачу до её завершения."""
    task = asyncio.create_task(coro)
    BACKGROUND_TASKS.add(task)
    task.add_done_callback(BACKGROUND_TASKS.discard)
    return task


@app.on_event("startup")
async def startup():
    """Инициализирует БД, webhook и все постоянные фоновые процессы."""
    init_db()
    await register_webhook()
    spawn_background(asyncio.to_thread(ensure_review_sheet_schema))
    spawn_background(channel_scheduler_supervisor())
    spawn_background(channel_watchdog_loop())
    spawn_background(angel_reminder_loop_max())
    spawn_background(check_donation_payments_loop_max())
    spawn_background(nurture_loop_max())
    spawn_background(weekly_funnel_report_loop_max())
    spawn_background(database_backup_loop("vera_max"))
    logging.info("Vera MAX Bot запущен в текстовом режиме")


@app.on_event("shutdown")
async def shutdown():
    """Корректно останавливает фоновые задачи при рестарте сервиса."""
    tasks = list(BACKGROUND_TASKS)
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    logging.info("Vera MAX Bot остановлен")

async def _process_webhook_request(request):
    try:
        data = await request.json()
        logging.info(f"MAX: {data.get('update_type', 'unknown')}")

        update_type = data.get("update_type", "")

        if update_type == "message_created":
            msg = data.get("message", {})
            body = msg.get("body", {})
            sender = msg.get("sender", {})
            chat_id = msg.get("recipient", {}).get("chat_id") or data.get("chat_id")
            user_id = sender.get("user_id", 0)
            first_name = sender.get("name", "")

            # Фото
            for att in body.get("attachments", []):
                if att.get("type") == "image":
                    payload_data = att.get("payload", {})
                    # В MAX фото передаётся как прямой URL в payload
                    photo_url = (
                        payload_data.get("url") or
                        payload_data.get("photo_url") or
                        # Пробуем достать из photos массива
                        (payload_data.get("photos", [{}])[0].get("url") if payload_data.get("photos") else None)
                    )
                    logging.info(f"Фото attachment payload: {payload_data}")
                    if photo_url:
                        user = get_user(user_id)
                        if user.get("step") in ("photo_church", "photo_icon"):
                            await handle_photo(chat_id, user_id, photo_url)
                            return JSONResponse({"ok": True})
                    else:
                        logging.error(f"Не найден URL фото в payload: {payload_data}")

                # Голосовое сообщение
                elif att.get("type") in ("audio", "voice"):
                    audio_url = att.get("payload", {}).get("url", "")
                    if not audio_url:
                        # Пробуем достать через token
                        audio_token = att.get("payload", {}).get("token", "")
                        if audio_token:
                            audio_url = f"{MAX_API}/audio/{audio_token}"
                    if audio_url:
                        user = get_user(user_id)
                        step = user.get("step", "")
                        if step.startswith("question_"):
                            await send_message(chat_id, "🎤 Распознаю голосовое...")
                            recognized = await transcribe_voice_max(audio_url)
                            if recognized:
                                await send_message(chat_id, "📝 Распознал: " + recognized + "\n\n⏳ Отвечаю...")
                                depth = step.replace("question_", "")
                                answer = await ask_claude(recognized, depth)
                                if answer == "error":
                                    try:
                                        await max_request("POST", f"messages?chat_id={OWNER_ID}",
                                            {"text": "⚠️ Ошибка Claude (голос MAX)\nПользователь: " + str(user_id) + "\nВопрос: " + recognized[:100]})
                                    except Exception:
                                        pass
                                    await send_message(chat_id, "⚠️ Не удалось получить ответ. Попробуйте позже.",
                                        [[btn("🔄 Попробовать снова", "ask_question")],
                                         [link_btn("📢 Сообщить о проблеме", "https://t.me/Boss023rus")],
                                         [btn("🏠 Меню", "main_menu")]])
                                else:
                                    await send_message(chat_id, answer,
                                        [[btn("❓ Ещё вопрос", "ask_question"), btn("🏠 Меню", "main_menu")]])
                                set_step(user_id, "idle")
                            else:
                                await send_message(chat_id, "⚠️ Не удалось распознать голосовое. Попробуйте написать текстом.",
                                    [[btn("🏠 Меню", "main_menu")]])
                            return JSONResponse({"ok": True})
                        elif step == "review":
                            await send_message(chat_id, "🎤 Распознаю ваш отзыв...")
                            recognized = await transcribe_voice_max(audio_url)
                            if recognized:
                                await handle_text(chat_id, user_id, recognized, first_name)
                            else:
                                await send_message(
                                    chat_id,
                                    "⚠️ Не удалось распознать голосовое. Попробуйте ещё раз или напишите текстом.",
                                    [[btn("◀️ Главное меню", "main_menu")]]
                                )
                            return JSONResponse({"ok": True})
                        else:
                            await send_message(
                                chat_id,
                                "☦️ Голосовые работают при вводе вопроса о вере или при отправке отзыва.",
                                main_menu_buttons()
                            )
                            return JSONResponse({"ok": True})

            text = body.get("text", "").strip()
            if text:
                await handle_text(chat_id, user_id, text, first_name)

        elif update_type == "bot_started":
            user = data.get("user", {})
            chat_id = data.get("chat_id") or user.get("user_id")
            user_id = user.get("user_id", 0)
            first_name = user.get("name", "друг")
            start_payload = str(
                data.get("payload")
                or data.get("start_payload")
                or data.get("message", {}).get("body", {}).get("payload")
                or ""
            ).strip()
            logging.info(
                f"BOT_STARTED: chat_id={chat_id} user_id={user_id} payload={start_payload}"
            )
            await handle_start(chat_id, user_id, first_name, "", start_payload)

        elif update_type == "message_callback":
            cb = data.get("callback", {})
            # В MAX chat_id находится в message.recipient.chat_id
            message = data.get("message", {})
            recipient = message.get("recipient", {})
            raw_chat_id = (
                cb.get("chat_id") or
                recipient.get("chat_id") or
                message.get("sender", {}).get("chat_id") or
                data.get("chat_id")
            )
            user = cb.get("user", {})
            user_id = user.get("user_id", 0)
            first_name = user.get("name", "")
            payload = cb.get("payload", "")
            # Если callback пришёл из канала — отвечаем в личку пользователю
            chat_type = message.get("chat_type", "")
            if chat_type == "channel" or (raw_chat_id and str(raw_chat_id).startswith("-")):
                chat_id = user_id
                logging.info(f"CALLBACK из канала — перенаправляем в личку: user_id={user_id}")
            else:
                chat_id = raw_chat_id
            logging.info(f"CALLBACK: chat_id={chat_id} user_id={user_id} payload={payload}")
            if payload and chat_id:
                await handle_callback(chat_id, user_id, payload, first_name)
            elif payload and not chat_id:
                logging.error(f"Нет chat_id в callback: {data}")

        return JSONResponse({"ok": True})
    except Exception as e:
        logging.error(f"Webhook error: {e}")
        return JSONResponse({"ok": False})


class _PayloadRequest:
    def __init__(self, payload): self.payload = payload
    async def json(self): return self.payload


def _max_update_key(data: dict) -> str:
    """Stable dedupe key; callbacks must not reuse the parent message id."""
    update_type = str(data.get("update_type", "unknown"))
    message = data.get("message") or {}
    callback = data.get("callback") or {}
    if update_type == "message_callback":
        candidate = callback.get("callback_id") or callback.get("id")
        if candidate:
            return f"{update_type}:{candidate}"
        raw_callback = {
            "payload": callback.get("payload"),
            "user_id": (callback.get("user") or {}).get("user_id"),
            "message_id": message.get("message_id"),
            "timestamp": data.get("timestamp") or callback.get("timestamp"),
        }
        raw = json.dumps(raw_callback, ensure_ascii=False, sort_keys=True, default=str)
        return f"{update_type}:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()
    candidate = data.get("update_id") or message.get("message_id") or data.get("timestamp")
    if candidate:
        return f"{update_type}:{candidate}"
    raw = json.dumps(data, ensure_ascii=False, sort_keys=True, default=str)
    return f"{update_type}:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _claim_max_update(update_id: str, update_type: str) -> bool:
    try:
        conn = db_connect()
        conn.execute("DELETE FROM processed_updates WHERE received_at<?", ((datetime.now()-timedelta(days=14)).isoformat(),))
        cur = conn.execute("INSERT OR IGNORE INTO processed_updates(update_id,update_type,received_at) VALUES (?,?,?)", (update_id, update_type, datetime.now().isoformat()))
        conn.commit(); claimed = cur.rowcount == 1; conn.close(); return claimed
    except Exception as e:
        logging.error(f"MAX dedupe error: {e}"); return True


async def _process_max_update_background(data: dict):
    try:
        await _process_webhook_request(_PayloadRequest(data))
    except Exception as e:
        logging.exception(f"MAX background update error: {e}")
        record_critical_error("max_webhook_background", e)
        try:
            await send_message(OWNER_ID, f"⚠️ Критическая ошибка MAX webhook\n\n{str(e)[:800]}")
        except Exception:
            pass


@app.post("/webhook")
async def webhook(request: Request):
    try:
        data = await request.json()
        update_key = _max_update_key(data)
        if not _claim_max_update(update_key, str(data.get("update_type", ""))):
            return JSONResponse({"ok": True, "duplicate": True})
        spawn_background(_process_max_update_background(data))
        return JSONResponse({"ok": True, "accepted": True})
    except Exception as e:
        logging.error(f"MAX webhook accept error: {e}")
        record_critical_error("max_webhook_accept", e)
        return JSONResponse({"ok": False}, status_code=400)

@app.get("/payment/success")
async def payment_success():
    html = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Пожертвование принято — С верой</title>
<style>
  body { font-family: Arial, sans-serif; text-align: center; padding: 60px 20px; background: #f5f0e8; color: #3a2a1a; }
  .icon { font-size: 64px; margin-bottom: 20px; }
  h1 { font-size: 28px; margin-bottom: 12px; }
  p { font-size: 16px; color: #6b5a4e; line-height: 1.6; }
  .cross { color: #8b1a1a; font-size: 36px; margin-top: 30px; }
</style>
</head>
<body>
  <div class="icon">🕯️</div>
  <h1>Пожертвование принято</h1>
  <p>Благодарим вас за вашу щедрость.<br>Да благословит вас Господь!</p>
  <p>Вы можете вернуться в православного помощника «С верой» в MAX.</p>
  <p><a href="https://max.ru/id232007136009_1_bot">Открыть помощника</a></p>
  <div class="cross">☦️</div>
</body>
</html>"""
    from fastapi.responses import HTMLResponse
    return HTMLResponse(content=html)

@app.get("/health")
async def health():
    return {"status": "ok", "bot": "С верой MAX"}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8080)
