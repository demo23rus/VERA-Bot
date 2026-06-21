import asyncio
import random
import re
import sqlite3
import logging
import os
import base64
import httpx
import uuid
from datetime import datetime, date, timedelta
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from openai import AsyncOpenAI
import anthropic
import uvicorn
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

# ========== КОНФИГ ==========
MAX_TOKEN     = _env.get("MAX_TOKEN") or os.environ.get("MAX_TOKEN", "")
MAX_API       = "https://platform-api.max.ru"
OPENAI_KEY    = _env.get("OPENAI_KEY") or os.environ.get("OPENAI_KEY", "")
ANTHROPIC_KEY = _env.get("ANTHROPIC_KEY") or os.environ.get("ANTHROPIC_KEY", "")
OWNER_ID      = 549639607
DB_PATH       = "/root/vera_max.db"
WEBHOOK_URL   = "https://sveroy.ru/webhook"

logging.info(f"MAX_TOKEN: {MAX_TOKEN[:15] if MAX_TOKEN else 'EMPTY'}...")
logging.info(f"OPENAI_KEY: {OPENAI_KEY[:15] if OPENAI_KEY else 'EMPTY'}...")

CREDENTIALS_FILE = "/root/google_credentials.json"
SPREADSHEET_ID   = "1PE7CaFuWOe_eygQqIoMAmUdJBtATbIaNfZR4cvarPCA"

# ========== КЛИЕНТЫ AI ==========
openai_client = AsyncOpenAI(api_key=OPENAI_KEY)
claude_client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)

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
    result = await max_request("POST", "subscriptions", {"url": WEBHOOK_URL})
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
    """Записывает пожертвование в общий лист Пожертвования"""
    try:
        sp = get_spreadsheet()
        if not sp: return
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
            source
        ])
        # Обновляем счётчик в основном листе MAX
        if source == "MAX":
            try:
                main_sheet = sp.worksheet("С верой MAX")
                col = main_sheet.col_values(1)
                if str(user_id) in col:
                    row = col.index(str(user_id)) + 1
                    don_val = main_sheet.cell(row, 8).value or "0"
                    main_sheet.update_cell(row, 8, str(int(don_val) + 1))
            except Exception:
                pass
    except Exception as e:
        logging.error(f"Sheets add_donation: {e}")

# ========== КНОПКИ ==========
def btn(text, payload):
    return {"type": "callback", "text": text[:40], "payload": payload}

def link_btn(text, url):
    return {"type": "link", "text": text[:40], "url": url}

def main_menu_buttons():
    return [
        [btn("🙏 Молитвы", "prayers"), btn("📅 Календарь", "calendar")],
        [btn("⛪ Таинства и обряды", "sacraments"), btn("👼 Святые", "saints")],
        [btn("🏛️ Святые места", "holy_places"), btn("📚 Библиотека", "library")],
        [btn("📸 Определить по фото", "photo_menu"), btn("🗺️ Найти храм рядом", "find_church")],
        [btn("📖 Евангелие дня", "daily_gospel")],
        [btn("👤 Мой профиль", "profile"), btn("❓ Задать вопрос", "ask_question")],
        [btn("🕯️ Пожертвование на развитие", "donate")],
        [btn("💬 Отзыв или пожелание", "review")],
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
    "01.01": "Обрезание Господне, память свт. Василия Великого",
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

SAINTS_BY_NAME = {
    "александр": [("06.06","мч. Александра"), ("12.09","блгв. кн. Александра Невского"), ("23.11","блгв. кн. Александра Невского")],
    "алексей":   [("30.03","прп. Алексия, человека Божия"), ("25.04","сщмч. Алексия")],
    "анастасия": [("04.01","мц. Анастасии Римляныни"), ("22.12","вмц. Анастасии Узорешительницы")],
    "андрей":    [("13.12","ап. Андрея Первозванного")],
    "анна":      [("03.02","прп. Анны"), ("07.08","прп. Анны")],
    "борис":     [("06.08","блгв. кн. Бориса и Глеба"), ("24.07","блгв. кн. Бориса")],
    "василий":   [("14.01","свт. Василия Великого"), ("13.03","мч. Василия")],
    "вера":      [("30.09","мц. Веры, Надежды, Любови и матери их Софии")],
    "виктор":    [("11.11","мч. Виктора"), ("05.03","мч. Виктора")],
    "владимир":  [("28.07","равноап. кн. Владимира")],
    "галина":    [("29.03","мц. Галины")],
    "георгий":   [("06.05","вмч. Георгия Победоносца")],
    "дарья":     [("01.04","мц. Дарии")],
    "дмитрий":   [("08.11","вмч. Димитрия Солунского"), ("01.06","блгв. кн. Димитрия Донского")],
    "дима":      [("08.11","вмч. Димитрия Солунского"), ("01.06","блгв. кн. Димитрия Донского")],
    "екатерина": [("07.12","вмц. Екатерины")],
    "елена":     [("03.06","равноап. царицы Елены"), ("24.07","равноап. Елены")],
    "иван":      [("20.01","Собор Иоанна Предтечи"), ("07.07","Рождество Иоанна Предтечи")],
    "иоанн":     [("20.01","Собор Иоанна Предтечи"), ("07.07","Рождество Иоанна Предтечи")],
    "ирина":     [("29.04","мц. Ирины"), ("18.05","мц. Ирины")],
    "кирилл":    [("27.02","равноап. Кирилла, учителя Словенского")],
    "константин": [("03.06","равноап. царя Константина")],
    "ксения":    [("06.02","блж. Ксении Петербургской")],
    "лариса":    [("08.04","мц. Ларисы")],
    "людмила":   [("29.09","мц. кн. Людмилы Чешской")],
    "маргарита": [("30.07","вмц. Марины (Маргариты)")],
    "мария":     [("22.07","равноап. Марии Магдалины"), ("17.09","мц. Марии")],
    "марина":    [("30.07","вмц. Марины")],
    "матрона":   [("02.05","блж. Матроны Московской"), ("09.08","мц. Матроны")],
    "михаил":    [("21.11","Собор Архистратига Михаила")],
    "надежда":   [("30.09","мц. Надежды")],
    "наталья":   [("08.09","мц. Наталии"), ("26.08","мц. Наталии")],
    "николай":   [("22.05","свт. Николая, архиеп. Мирликийского"), ("19.12","свт. Николая Чудотворца")],
    "ольга":     [("24.07","равноап. кн. Ольги")],
    "павел":     [("12.07","ап. Петра и Павла")],
    "пётр":      [("12.07","ап. Петра и Павла")],
    "петр":      [("12.07","ап. Петра и Павла")],
    "светлана":  [("26.02","мц. Фотины (Светланы)")],
    "сергей":    [("08.10","прп. Сергия Радонежского")],
    "сергий":    [("08.10","прп. Сергия Радонежского")],
    "софия":     [("30.09","мц. Софии")],
    "татьяна":   [("25.01","мц. Татианы")],
    "юлия":      [("29.07","мц. Иулии")],
    "абрам":    [('22.10', 'прп. Авраамия Ростовского')],
    "авраам":    [('22.10', 'прп. Авраамия Ростовского'), ('09.10', 'прп. Авраамия Затворника')],
    "агафья":    [('18.02', 'мц. Агафии Панормской')],
    "агния":    [('21.01', 'мц. Агнии Римской')],
    "адриан":    [('26.08', 'мч. Адриана и Наталии')],
    "аким":    [('20.08', 'прав. Иоакима и Анны')],
    "аксинья":    [('24.01', 'прп. Ксении')],
    "алла":    [('26.03', 'мц. Аллы Готфской')],
    "альберт":    [('14.11', 'мч. Альберта')],
    "амвросий":    [('20.12', 'свт. Амвросия Медиоланского'), ('10.10', 'прп. Амвросия Оптинского')],
    "анатолий":    [('23.07', 'прп. Анатолия Оптинского'), ('15.08', 'мч. Анатолия')],
    "антон":    [('17.01', 'прп. Антония Великого'), ('23.07', 'прп. Антония Печерского')],
    "антонина":    [('01.03', 'мц. Антонины'), ('10.06', 'мц. Антонины')],
    "антоний":    [('17.01', 'прп. Антония Великого'), ('23.07', 'прп. Антония Печерского')],
    "аркадий":    [('26.02', 'прп. Аркадия Новоторжского')],
    "арсений":    [('08.05', 'свт. Арсения Великого'), ('24.07', 'прп. Арсения Коневского')],
    "артём":    [('20.10', 'ап. Артемы'), ('02.11', 'мч. Артемия Антиохийского')],
    "артемий":    [('02.11', 'мч. Артемия Антиохийского')],
    "аскольд":    [('11.07', 'блгв. кн. Аскольда')],
    "афанасий":    [('18.01', 'свт. Афанасия Великого'), ('25.01', 'свт. Афанасия и Кирилла')],
    "ахмат":    [('11.09', 'мч. Ахмата')],
    "вадим":    [('22.04', 'прмч. Вадима Персидского')],
    "валентин":    [('12.08', 'мч. Валентина'), ('19.07', 'мч. Валентина Доростольского')],
    "валентина":    [('10.02', 'мц. Валентины'), ('07.08', 'мц. Валентины')],
    "валерий":    [('07.03', 'мч. Валерия'), ('20.11', 'мч. Валерия')],
    "валерия":    [('07.06', 'мц. Валерии')],
    "варвара":    [('17.12', 'вмц. Варвары Илиопольской')],
    "варвар":    [('06.05', 'прп. Варвара')],
    "варлаам":    [('19.11', 'прп. Варлаама Хутынского')],
    "василиса":    [('15.01', 'мц. Василисы'), ('04.04', 'мц. Василисы')],
    "вениамин":    [('13.08', 'сщмч. Вениамина Петроградского'), ('11.06', 'прп. Вениамина Нитрийского')],
    "вера":    [('30.09', 'мц. Веры, Надежды, Любови')],
    "виктория":    [('23.12', 'мц. Виктории'), ('11.11', 'мц. Виктории')],
    "виталий":    [('04.05', 'мч. Виталия Медиоланского'), ('22.04', 'мч. Виталия')],
    "виталия":    [('04.05', 'мц. Виталии')],
    "вячеслав":    [('04.03', 'блгв. кн. Вячеслава Чешского'), ('11.03', 'блгв. кн. Вячеслава')],
    "гавриил":    [('26.07', 'арх. Гавриила'), ('08.04', 'арх. Гавриила')],
    "геннадий":    [('17.12', 'свт. Геннадия Новгородского'), ('25.11', 'прп. Геннадия Костромского')],
    "геrasим":    [('17.03', 'прп. Герасима Иорданского')],
    "герасим":    [('17.03', 'прп. Герасима Иорданского')],
    "глеб":    [('06.08', 'блгв. кн. Бориса и Глеба'), ('05.09', 'блгв. кн. Глеба')],
    "григорий":    [('12.01', 'свт. Григория Нисского'), ('25.01', 'свт. Григория Богослова')],
    "давид":    [('01.03', 'прп. Давида'), ('06.03', 'прп. Давида Солунского')],
    "даниил":    [('17.12', 'прп. Даниила Столпника'), ('23.12', 'блгв. кн. Даниила Московского')],
    "денис":    [('16.10', 'сщмч. Дионисия Ареопагита')],
    "дионисий":    [('16.10', 'сщмч. Дионисия Ареопагита'), ('05.10', 'свт. Дионисия Суздальского')],
    "дмитрий":    [('08.11', 'вмч. Димитрия Солунского'), ('01.06', 'блгв. кн. Димитрия Донского')],
    "дима":    [('08.11', 'вмч. Димитрия Солунского')],
    "домна":    [('14.01', 'мц. Домны Никомидийской')],
    "домника":    [('08.01', 'прп. Домники')],
    "евгений":    [('26.12', 'мч. Евгения'), ('20.11', 'мч. Евгения Мелитинского')],
    "евгения":    [('24.12', 'прмц. Евгении')],
    "евдокия":    [('14.03', 'прмц. Евдокии'), ('04.08', 'прав. Евдокии')],
    "евдоким":    [('05.08', 'прав. Евдокима Каппадокийского')],
    "елизавета":    [('05.09', 'прмц. Елисаветы Феодоровны'), ('18.09', 'прмц. Елисаветы')],
    "ефим":    [('20.01', 'прп. Евфимия Великого'), ('02.02', 'прп. Евфимия Нового')],
    "ефрем":    [('10.02', 'прп. Ефрема Сирина'), ('05.03', 'свт. Ефрема Сербского')],
    "зинаида":    [('23.10', 'мц. Зинаиды'), ('11.10', 'мц. Зинаиды')],
    "зиновий":    [('13.11', 'мч. Зиновия и Зиновии')],
    "зоя":    [('13.02', 'мц. Зои Вифлеемской'), ('02.05', 'мц. Зои')],
    "илья":    [('02.08', 'прор. Илии Фесвитянина')],
    "илия":    [('02.08', 'прор. Илии Фесвитянина')],
    "иннокентий":    [('26.11', 'свт. Иннокентия Иркутского'), ('06.10', 'свт. Иннокентия Московского')],
    "иосиф":    [('19.09', 'прав. Иосифа Прекрасного'), ('11.04', 'прп. Иосифа Волоцкого')],
    "иулиания":    [('15.01', 'мц. Иулиании Никомидийской'), ('02.01', 'блж. Иулиании Лазаревской')],
    "капитолина":    [('27.10', 'мц. Капитолины')],
    "клавдия":    [('20.03', 'мц. Клавдии'), ('07.04', 'мц. Клавдии')],
    "климент":    [('25.11', 'сщмч. Климента Римского')],
    "кристина":    [('24.07', 'вмц. Христины')],
    "кузьма":    [('14.07', 'бессрр. Космы и Дамиана'), ('14.11', 'бессрр. Космы и Дамиана')],
    "лариса":    [('08.04', 'мц. Ларисы Готфской')],
    "лев":    [('05.03', 'свт. Льва Катанского'), ('18.02', 'свт. Льва Великого')],
    "леонид":    [('16.04', 'мч. Леонида'), ('10.07', 'прп. Леонида')],
    "лидия":    [('05.04', 'мц. Лидии'), ('23.03', 'мц. Лидии')],
    "лука":    [('31.10', 'ап. Луки'), ('11.06', 'свт. Луки Крымского')],
    "любовь":    [('30.09', 'мц. Веры, Надежды, Любови')],
    "макар":    [('19.01', 'прп. Макария Великого'), ('01.02', 'свт. Макария Московского')],
    "макарий":    [('19.01', 'прп. Макария Великого'), ('01.02', 'свт. Макария Московского')],
    "максим":    [('13.08', 'прп. Максима Исповедника'), ('11.11', 'блж. Максима Московского')],
    "максима":    [('26.04', 'мц. Максимы')],
    "маргарита":    [('30.07', 'вмц. Марины (Маргариты)')],
    "марк":    [('25.04', 'ап. Марка'), ('07.05', 'ап. Марка')],
    "мартин":    [('14.04', 'свт. Мартина Исповедника')],
    "марфа":    [('04.07', 'прп. Марфы'), ('01.09', 'прп. Марфы')],
    "мефодий":    [('11.05', 'равноап. Мефодия, учителя Словенского')],
    "милана":    [('19.07', 'мц. Миланы')],
    "мирон":    [('17.08', 'сщмч. Мирона Кизического')],
    "митрофан":    [('23.11', 'свт. Митрофана Воронежского'), ('06.06', 'свт. Митрофана')],
    "моисей":    [('04.09', 'прп. Моисея Угрина'), ('28.08', 'прп. Моисея Мурина')],
    "наталия":    [('26.08', 'мц. Наталии')],
    "никита":    [('15.09', 'вмч. Никиты Готфского'), ('31.01', 'прп. Никиты Столпника')],
    "никифор":    [('13.02', 'свт. Никифора Константинопольского')],
    "нина":    [('27.01', 'равноап. Нины, просветительницы Грузии')],
    "нонна":    [('05.08', 'прав. Нонны')],
    "оксана":    [('24.01', 'прп. Ксении')],
    "олег":    [('03.10', 'блгв. кн. Олега Брянского')],
    "олеся":    [('03.10', 'мц. Александры')],
    "платон":    [('18.11', 'мч. Платона Анкирского')],
    "полина":    [('23.07', 'мц. Аполлинарии')],
    "прасковья":    [('26.07', 'мц. Параскевы Пятницы')],
    "прохор":    [('09.04', 'прп. Прохора Лебедника'), ('28.01', 'прп. Прохора Печерского')],
    "раиса":    [('05.09', 'мц. Раисы Александрийской')],
    "регина":    [('07.09', 'мц. Регины')],
    "роман":    [('01.10', 'прп. Романа Сладкопевца'), ('08.08', 'мч. Романа')],
    "руслан":    [('17.03', 'мч. Руслана')],
    "светлана":    [('26.02', 'мц. Фотины (Светланы)')],
    "семён":    [('03.02', 'прп. Симеона Богоприимца'), ('14.09', 'прп. Симеона Столпника')],
    "серафима":    [('29.07', 'прмц. Серафимы')],
    "снежана":    [('26.03', 'мц. Снежаны')],
    "степан":    [('09.01', 'архидиак. Стефана первомученика')],
    "стефан":    [('09.01', 'архидиак. Стефана первомученика')],
    "тамара":    [('01.05', 'блгв. царицы Тамары Грузинской')],
    "тимофей":    [('04.02', 'ап. Тимофея'), ('22.01', 'прп. Тимофея')],
    "тихон":    [('29.06', 'свт. Тихона Амафунтского'), ('09.10', 'свт. Тихона Задонского')],
    "трофим":    [('19.09', 'мч. Трофима'), ('23.07', 'мч. Трофима')],
    "ульяна":    [('15.01', 'мц. Иулиании Никомидийской')],
    "федор":    [('08.03', 'вмч. Феодора Тирона'), ('09.06', 'прп. Феодора Освященного')],
    "фёдор":    [('08.03', 'вмч. Феодора Тирона'), ('09.06', 'прп. Феодора Освященного')],
    "феодор":    [('08.03', 'вмч. Феодора Тирона'), ('09.06', 'прп. Феодора Освященного')],
    "феодосий":    [('11.01', 'прп. Феодосия Великого'), ('03.05', 'прп. Феодосия Печерского')],
    "филипп":    [('27.11', 'ап. Филиппа'), ('22.01', 'свт. Филиппа Московского')],
    "фома":    [('19.10', 'ап. Фомы')],
    "харитина":    [('05.10', 'мц. Харитины')],
    "христина":    [('24.07', 'вмц. Христины Тирской')],
    "яков":    [('05.11', 'ап. Иакова Зеведеева'), ('13.01', 'прп. Иакова Постника')],
    "яна":    [('24.06', 'мц. Иоанны')],
}

FASTS = {
    "Великий пост": "48 дней перед Пасхой. Самый строгий пост.",
    "Петров пост": "С понедельника после Недели всех святых до 12 июля.",
    "Успенский пост": "14–27 августа. Строгий пост.",
    "Рождественский пост": "28 ноября – 6 января.",
    "Среда и пятница": "Еженедельный пост в память предательства и распятия Христа.",
}

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

# ========== БАЗА ДАННЫХ ==========
def init_db():
    conn = sqlite3.connect(DB_PATH)
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
        notifications INTEGER DEFAULT 1,
        remind_days INTEGER DEFAULT 3
    )""")
    for col in ["notifications INTEGER DEFAULT 1", "remind_days INTEGER DEFAULT 3"]:
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
    conn.commit()
    conn.close()

def get_user(user_id, username="", first_name=""):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
    row = c.fetchone()
    if not row:
        c.execute("INSERT INTO users (user_id,username,first_name) VALUES (?,?,?)",
                  (user_id, username, first_name))
        conn.commit()
        c.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
        row = c.fetchone()
    conn.close()
    cols = ["user_id","username","first_name","step","church_name","birth_date","angel_day","onboarded","notifications","remind_days"]
    return dict(zip(cols, row))

def set_step(user_id, step):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("UPDATE users SET step=? WHERE user_id=?", (step, user_id))
    conn.commit()
    conn.close()

def save_favorite(user_id, title, content):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("INSERT INTO favorites (user_id,title,content,created_at) VALUES (?,?,?,?)",
                 (user_id, title, content[:500], datetime.now().strftime("%d.%m.%Y")))
    conn.commit()
    conn.close()

def get_favorites(user_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT title, content FROM favorites WHERE user_id=? ORDER BY id DESC LIMIT 10", (user_id,))
    rows = c.fetchall()
    conn.close()
    return rows


def create_review_record(chat_id, user_id, username, first_name, review_text):
    """Сохраняет отзыв локально и возвращает его уникальный номер."""
    conn = sqlite3.connect(DB_PATH)
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
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM user_reviews WHERE id=?",
        (int(review_id),)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def update_review_record(review_id, status, owner_reply="", handled_by="Владелец"):
    replied_at = datetime.now().isoformat(timespec="seconds")
    conn = sqlite3.connect(DB_PATH)
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
        "short":  ("Отвечай кратко — 2-3 предложения.", 300),
        "medium": ("Отвечай развёрнуто — 5-7 предложений.", 600),
        "deep":   ("Дай глубокий богословский ответ.", 1200),
    }
    system_add, max_tok = depths.get(depth, depths["medium"])
    greetings = [
        "Душа моя", "Чадо", "Возлюбленное чадо", "Дорогой брат во Христе",
        "Дорогая сестра во Христе", "Дорогой друг", "Брате", "Сестра",
        "Возлюбленный во Христе", "Дорогой мой"
    ]
    greeting = random.choice(greetings)
    try:
        msg = claude_client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=max_tok,
            system=(
                "Ты православный помощник. "
                "Отвечай тепло, бережно и понятно, опираясь на православную традицию, Священное Писание и святоотеческое наследие. "
                "Не представляйся священником, не выдавай ответ за личное благословение и не заменяй разговор с духовником. "
                "В вопросах Таинств, тяжёлых жизненных решений и личного духовного руководства мягко советуй обратиться к священнику. "
                "Говоришь просто и сердечно, не сухо. "
                f"ОБЯЗАТЕЛЬНО начинай каждый ответ с обращения '{greeting},' — это первое слово ответа. "
                "Опираешься на Писание и святых отцов — объясняешь живым языком. "
                "Никогда не осуждаешь, всегда утешаешь. "
                "В конце — краткое молитвенное пожелание. "
                "Отвечаешь только по-русски. " + system_add
            ),
            messages=[{"role": "user", "content": question}]
        )
        return msg.content[0].text
    except Exception as e:
        logging.error(f"Ошибка Claude: {e}")
        return "error"

async def transcribe_voice_max(audio_url: str) -> str:
    """Скачиваем голосовое и транскрибируем через Whisper"""
    try:
        headers = {"Authorization": MAX_TOKEN}
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(audio_url, headers=headers)
            audio_bytes = r.content
        # Сохраняем во временный файл
        tmp_path = "/tmp/vera_voice_max.ogg"
        with open(tmp_path, "wb") as f:
            f.write(audio_bytes)
        with open(tmp_path, "rb") as f:
            response = await openai_client.audio.transcriptions.create(
                model="whisper-1",
                file=f,
                language="ru"
            )
        return response.text
    except Exception as e:
        logging.error(f"Ошибка транскрибации: {e}")
        return None

async def analyze_photo(photo_bytes, photo_type):
    prompt = (
        "На фотографии православный храм или монастырь. Определи: 1) Название; 2) Архитектурный стиль; 3) История. Отвечай по-русски."
        if photo_type == "church" else
        "На фотографии православная икона. Определи: 1) Кто изображён; 2) Атрибуты; 3) Краткое житие. Отвечай по-русски."
    )
    try:
        image_data = base64.b64encode(photo_bytes).decode("utf-8")
        response = await openai_client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_data}"}}
            ]}],
            max_tokens=800
        )
        return response.choices[0].message.content
    except Exception as e:
        logging.error(f"Ошибка GPT-4o: {e}")
        return "Не удалось проанализировать фото. Попробуйте ещё раз."

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

# ========== ОБРАБОТЧИКИ ==========
async def handle_start(chat_id, user_id, first_name, username, start_payload=""):
    """Обрабатывает обычный запуск и запуск по MAX deep-link.

    При наличии start_payload пользователь сразу попадает в обещанный раздел,
    а не в общее меню. Формат ссылки: https://max.ru/<bot>?start=<payload>
    """
    user = get_user(user_id, username, first_name)
    # Записываем в Sheets (в фоне)
    import threading
    threading.Thread(target=sheets_add_user_max, args=(user_id, username, first_name), daemon=True).start()

    if not user.get("onboarded"):
        conn = sqlite3.connect(DB_PATH)
        conn.execute("UPDATE users SET onboarded=1 WHERE user_id=?", (user_id,))
        conn.commit()
        conn.close()

    # Deep-link источники канала. Каждый источник одновременно открывает
    # обещанную функцию и записывается в аналитику воронки.
    channel_routes = {
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
    }
    target_payload = channel_routes.get(start_payload)
    if target_payload:
        try:
            conn = sqlite3.connect(DB_PATH)
            conn.execute(
                "INSERT INTO channel_clicks (user_id,source,target,clicked_at) VALUES (?,?,?,?)",
                (user_id, start_payload, target_payload, datetime.now().isoformat()),
            )
            conn.commit()
            conn.close()
        except Exception as e:
            logging.error(f"Не удалось записать переход из канала: {e}")
        await handle_callback(chat_id, user_id, target_payload, first_name)
        return

    # Прямые deep-link сценарии оставлены для совместимости со старыми постами.
    allowed_payloads = {
        "prayers", "saints", "daily_gospel", "ask_question",
        "prayer_evening_ru", "library", "photo_icon", "find_church",
        "sacraments", "calendar", "main_menu", "profile", "sacr_ispoved",
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
            [[btn("🙏 Все молитвы", "prayers"), btn("🏠 Меню", "main_menu")]]
        )

    elif payload.startswith("prayer_"):
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
            await send_message(chat_id, f"{title}\n\n{text}", buttons)

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
            text += "Сегодня нет особых праздников."
        await send_message(chat_id, text, [[btn("◀️ Календарь", "calendar")]])

    elif payload == "cal_namedays":
        saints = get_todays_saints()
        today_str = date_ru("short")
        if saints:
            text = f"👼 Именинники {today_str}:\n\n"
            for name, desc in saints:
                text += f"✨ {name} — {desc}\n"
        else:
            text = f"👼 Сегодня именинников нет."
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
        text = ("🥚 ПАСХА — ГЛАВНЫЙ ПРАЗДНИК\n\n"
                "Пасха — Воскресение Христово — победа жизни над смертью.\n\n"
                "📅 ВЕЛИКИЙ ПОСТ (48 дней):\n— Исключаются мясо, рыба, молочное, яйца\n— Рыба разрешена: Вербное воскресенье, Благовещение\n\n"
                "📅 СТРАСТНАЯ НЕДЕЛЯ:\nЧистый Четверг — причастие, уборка, крашение яиц\nВеликая Пятница — строгий пост, плащаница\nВеликая Суббота — освящение куличей и яиц\n\n"
                "🌙 ПАСХАЛЬНАЯ НОЧЬ:\n— Придите в храм к 23:00\n— Крестный ход в полночь\n— Пасхальная служба 2-3 часа\n— «Христос Воскресе!» — «Воистину Воскресе!»\n\n"
                "🥚 ТРАДИЦИИ:\n— Яйца — символ воскресения\n— Кулич — символ присутствия Христа\n— Верба — Вербное воскресенье за неделю до Пасхи")
        await send_message(chat_id, text, [[btn("◀️ Календарь", "calendar")]])

    elif payload == "cal_kreschenije":
        text = ("💧 КРЕЩЕНИЕ ГОСПОДНЕ — 19 января\n\n"
                "☦️ Один из великих праздников. Крещение Христа в реке Иордан.\n\n"
                "💧 ОСВЯЩЕНИЕ ВОДЫ:\n— 18 января вечером (сочельник)\n— 19 января в день праздника\n— Крещенская вода не портится годами\n\n"
                "КАК ХРАНИТЬ:\n— У икон в чистом месте\n— Пейте натощак с молитвой\n\n"
                "🏊 КУПАНИЕ В ПРОРУБИ:\n— Народная традиция, не церковный обряд\n— Окунитесь трижды с молитвой\n— Не рекомендуется при сердечных заболеваниях\n\n"
                "Если не можете — умойтесь крещенской водой дома.")
        await send_message(chat_id, text, [[btn("◀️ Календарь", "calendar")]])

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
        notif = user.get("notifications", 1)
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
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT prayer FROM patron_prayers_cache WHERE name=?", (name_lower,))
        row = c.fetchone()
        conn.close()
        if row:
            await send_message(chat_id, f"🙏 Молитва небесному покровителю — {name}\n\n{row[0]}", [[btn("◀️ Профиль", "profile")]])
            return
        await send_message(chat_id, "🙏 Нахожу молитву...")
        try:
            msg = claude_client.messages.create(
                model="claude-sonnet-4-5",
                max_tokens=600,
                system="Ты православный помощник. Напиши пример краткого личного молитвенного обращения к святому, 8-12 строк. Не выдавай текст за утверждённую церковную молитву. Начни с обращения и закончи словом Аминь.",
                messages=[{"role": "user", "content": f"Напиши молитву святому: {name}"}]
            )
            prayer_text = msg.content[0].text
            conn2 = sqlite3.connect(DB_PATH)
            conn2.execute("INSERT OR REPLACE INTO patron_prayers_cache (name, prayer) VALUES (?,?)", (name_lower, prayer_text))
            conn2.commit()
            conn2.close()
            await send_message(chat_id, f"🙏 Молитва небесному покровителю — {name}\n\n{prayer_text}", [[btn("◀️ Профиль", "profile")]])
        except Exception as e:
            logging.error(f"Ошибка молитвы: {e}")
            await send_message(chat_id, "🙏 Обратитесь к своему святому своими словами.", [[btn("◀️ Профиль", "profile")]])

    elif payload == "toggle_notifications":
        notif = get_user(user_id).get("notifications", 1)
        new_val = 0 if notif else 1
        conn = sqlite3.connect(DB_PATH)
        conn.execute("UPDATE users SET notifications=? WHERE user_id=?", (new_val, user_id))
        conn.commit()
        conn.close()
        status = "включены ✅" if new_val else "отключены 🔕"
        await send_message(chat_id, f"Утренние уведомления {status}", back_main())

    elif payload == "daily_gospel":
        await send_message(chat_id, "📖 Нахожу Евангелие дня...")
        text = await get_daily_gospel_max()
        await send_message(chat_id, text, [
            [btn("📅 Календарь", "calendar"), btn("🏠 Меню", "main_menu")]
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

async def handle_text(chat_id, user_id, text, first_name=""):
    user = get_user(user_id)
    step = user.get("step", "idle")

    if text.strip() in ("/start", "start"):
        await handle_start(chat_id, user_id, first_name, "")
        return

    # Диагностика MAX-канала доступна только владельцу.
    owner_command = text.strip().lower()
    if int(user_id) == int(OWNER_ID) and owner_command in {
        "/channel_status", "канал статус"
    }:
        msk_now = datetime.utcnow() + timedelta(hours=3)
        rows = channel_posts_today(msk_now)
        if rows:
            journal = "\n".join(
                f"• {slot} — {rubric}: {status}"
                for slot, rubric, status, _ in rows[-20:]
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
            "Для проверки изображения: /channel_image_test"
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

    if int(user_id) == int(OWNER_ID) and owner_command in {
        "/channel_image_test", "канал картинка тест"
    }:
        msk_now = datetime.utcnow() + timedelta(hours=3)
        visual = select_channel_visual(msk_now, 20, "evening", "тест изображения")
        footer, buttons, deep_link = get_channel_cta("evening")
        test_text = (
            "🖼️ Проверка визуальной публикации\n\n"
            "Если вы видите изображение, подпись и кнопку, визуальная система канала работает корректно."
            f"\n\n🖼️ На изображении: {visual['title']}"
            + footer
        )
        ok = await post_to_channel(test_text, visual["urls"], buttons, deep_link)
        await send_message(
            chat_id,
            "✅ Тестовый пост с изображением отправлен в MAX-канал."
            if ok else
            "⚠️ Не удалось подтвердить тестовую публикацию с изображением. Проверьте журнал службы."
        )
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
        conn = sqlite3.connect(DB_PATH)
        conn.execute("UPDATE users SET church_name=? WHERE user_id=?", (name, user_id))
        conn.commit()
        conn.close()
        # Определяем день ангела
        angel = ""
        name_lower = name.lower()
        if name_lower in SAINTS_BY_NAME:
            dates = SAINTS_BY_NAME[name_lower]
            if dates:
                angel = f"{dates[0][0]} ({dates[0][1]})"
                conn2 = sqlite3.connect(DB_PATH)
                conn2.execute("UPDATE users SET angel_day=? WHERE user_id=?", (angel, user_id))
                conn2.commit()
                conn2.close()
        set_step(user_id, "idle")
        msg = f"✅ Имя сохранено: {name}"
        if angel:
            msg += f"\n👼 День ангела: {angel}"
        await send_message(chat_id, msg, [[btn("◀️ Профиль", "profile")]])
        return

    if step == "edit_birth":
        birth = text.strip()
        try:
            parts = birth.split(".")
            if len(parts) >= 2 and 1 <= int(parts[0]) <= 31 and 1 <= int(parts[1]) <= 12:
                conn = sqlite3.connect(DB_PATH)
                conn.execute("UPDATE users SET birth_date=? WHERE user_id=?", (birth, user_id))
                conn.commit()
                conn.close()
                set_step(user_id, "idle")
                await send_message(chat_id, f"✅ Дата рождения сохранена: {birth}", [[btn("◀️ Профиль", "profile")]])
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
            msg = claude_client.messages.create(
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
            "Да хранит вас Господь 🕊️",
            main_menu_buttons()
        )
        return

    if step == "donate_amount":
        try:
            amount = int(text.strip())
            if amount < 10:
                await send_message(chat_id, "⚠️ Минимальная сумма — 10 рублей:")
                return
            from yookassa import Configuration, Payment as YPayment
            Configuration.account_id = "1363324"
            Configuration.secret_key = "live_-RKE9nsi8wZiM-5f00z78E84OYSi3M0Dj9w_-pE0Mvw"
            payment = YPayment.create({
                "amount": {"value": f"{amount}.00", "currency": "RUB"},
                "confirmation": {
                    "type": "redirect",
                    "return_url": "https://sveroy.ru/payment/success"
                },
                "capture": True,
                "description": "Пожертвование на развитие «С верой» во славу Божию",
                "receipt": {
                    "customer": {
                        "email": "6038484@mail.ru"
                    },
                    "items": [
                        {
                            "description": "Пожертвование на развитие «С верой»",
                            "quantity": "1.00",
                            "amount": {"value": f"{amount}.00", "currency": "RUB"},
                            "vat_code": 1,
                            "payment_mode": "full_payment",
                            "payment_subject": "another"
                        }
                    ]
                }
            }, str(uuid.uuid4()))
            set_step(user_id, "idle")
            pay_url = payment.confirmation.confirmation_url
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
    from datetime import date as _date
    today = _date.today()
    m, d, w = today.month, today.day, today.weekday()
    if (m == 2 and d >= 16) or m == 3 or (m == 4 and d <= 4):
        if w not in (5, 6):
            return "🕯️ Великий пост\n\nСегодня постный день.\n❌ Мясо, рыба, молочное, яйца\n✅ Хлеб, овощи, фрукты, бобовые, грибы\n\nВеликий пост — время молитвы и покаяния."
        return "🕯️ Великий пост\n\nСуббота/воскресенье — пост послабляется.\n✅ Рыба, растительное масло\n❌ Мясо, молочное, яйца"
    if (m == 6 and d >= 15) or (m == 7 and d <= 12):
        if w in (2, 4):
            return "🕯️ Петров пост\n\nСреда/пятница — строгий день.\n❌ Мясо, рыба, молочное\n✅ Растительная пища"
        if w in (5, 6):
            return "🕯️ Петров пост\n\nСуббота/воскресенье.\n✅ Рыба, вино умеренно\n❌ Мясо, молочное, яйца"
        return "🕯️ Петров пост\n\n✅ Рыба, растительное масло\n❌ Мясо, молочное, яйца"
    if m == 8 and 14 <= d <= 27:
        if d == 19:
            return "🕯️ Успенский пост\n\nСегодня Преображение Господне — разрешается рыба!\n❌ Мясо, молочное, яйца"
        return "🕯️ Успенский пост\n\n❌ Мясо, рыба, молочное, яйца\n✅ Растительная пища"
    if (m == 11 and d >= 28) or m == 12 or (m == 1 and d <= 6):
        if w in (5, 6):
            return "🕯️ Рождественский пост\n\nСуббота/воскресенье.\n✅ Рыба, вино умеренно\n❌ Мясо, молочное, яйца"
        return "🕯️ Рождественский пост\n\n❌ Мясо, молочное, яйца\n✅ Рыба (пн, вт, чт), растительное масло"
    if w == 2:
        return "🥗 Среда — постный день\n\nВ память о предательстве Иуды.\n❌ Мясо, молочное, яйца\n✅ Рыба, растительная пища"
    if w == 4:
        return "🥗 Пятница — постный день\n\nВ память о Распятии Господа.\n❌ Мясо, молочное, яйца\n✅ Рыба, растительная пища"
    return "☀️ Сегодня не постный день\n\nМногодневных постов сейчас нет. Сегодня не среда и не пятница.\n\nБлижайшие постные дни: среда и пятница."

async def get_daily_gospel_max() -> str:
    today = date_ru("short")
    try:
        msg = claude_client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=500,
            system="Ты православный помощник. Дай евангельское чтение дня с коротким толкованием (3-4 предложения). Формат: отрывок из Евангелия (2-3 стиха с указанием источника), потом краткое толкование простым языком. Отвечай по-русски. Без лишних вступлений.",
            messages=[{"role": "user", "content": f"Дай евангельское чтение на {today}"}]
        )
        return "📖 Евангелие дня — " + today + "\n\n" + msg.content[0].text
    except Exception as e:
        logging.error(f"Ошибка Евангелия MAX: {e}")
        return "📖 Евангелие дня\n\n«Просите — и дано будет вам; ищите — и найдёте; стучите — и отворят вам.»\n(Мф. 7:7)"

# ========== МОЛИТВА ДНЯ И РАССЫЛКА ==========
async def get_prayer_of_day_max() -> str:
    today = datetime.now().strftime("%Y-%m-%d")
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT prayer FROM daily_prayer_cache WHERE date=?", (today,))
    row = c.fetchone()
    conn.close()
    if row:
        return row[0]
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
            system="Ты православный помощник. Пишешь молитвенные обращения тепло и душевно, не представляясь священником.",
            messages=[{"role": "user", "content": prompt}]
        )
        prayer = msg.content[0].text
        conn2 = sqlite3.connect(DB_PATH)
        conn2.execute("INSERT OR REPLACE INTO daily_prayer_cache (date, prayer) VALUES (?,?)", (today, prayer))
        conn2.commit()
        conn2.close()
        return prayer
    except Exception as e:
        logging.error(f"Ошибка молитвы дня MAX: {e}")
        return PRAYER_TEXTS["prayer_morning_ru"][1]

async def morning_broadcast_max():
    """Утренняя рассылка всем пользователям MAX у кого включены уведомления"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT user_id, church_name FROM users WHERE notifications=1 OR notifications IS NULL")
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
    """Напоминания о дне ангела для пользователей MAX"""
    while True:
        now = datetime.now()
        msk_hour = (datetime.utcnow().hour + 3) % 24
        if msk_hour == 9 and now.minute == 0:
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            c.execute("SELECT user_id, church_name, angel_day FROM users WHERE angel_day != '' AND angel_day IS NOT NULL")
            users = c.fetchall()
            conn.close()
            for user_id, name, angel_day in users:
                try:
                    angel_str = angel_day.split(" ")[0]
                    angel_date = datetime.strptime(angel_str, "%d.%m").replace(year=now.year)
                    diff = (angel_date.date() - now.date()).days
                    if diff == 3:
                        await max_request("POST", f"messages?chat_id={user_id}",
                            {"text": f"🕊️ Скоро ваш день ангела!\n\nЧерез 3 дня — {angel_day}\n\nПомолитесь своему святому покровителю 🙏"})
                    elif diff == 0:
                        await max_request("POST", f"messages?chat_id={user_id}",
                            {"text": f"🎉 С Днём ангела, {name or 'дорогой'}!\n\n{angel_day}\n\nПусть ваш святой покровитель хранит и молится за вас! ☦️"})
                except Exception as e:
                    logging.error(f"Ошибка напоминания MAX {user_id}: {e}")
            await asyncio.sleep(61)
        await asyncio.sleep(30)

# ========== АВТОПОСТИНГ В КАНАЛ — PREMIUM FUNNEL ==========
MAX_CHANNEL_ID = -75405929805299
MAX_BOT_URL = "https://max.ru/id232007136009_1_bot"

# Иконы для главных праздников (Wikimedia Commons — свободные изображения)
FEAST_ICONS = {
    # Господские праздники
    "07.01": "https://upload.wikimedia.org/wikipedia/commons/thumb/5/5b/Nativity_icon_13th_century_Sinai.jpg/800px-Nativity_icon_13th_century_Sinai.jpg",
    "19.01": "https://upload.wikimedia.org/wikipedia/commons/thumb/a/a5/Theophany_Baptism_of_Christ_Novgorod_icon_12th_century.jpg/800px-Theophany_Baptism_of_Christ_Novgorod_icon_12th_century.jpg",
    "15.02": "https://upload.wikimedia.org/wikipedia/commons/thumb/9/9f/Meeting_of_the_Lord_icon.jpg/800px-Meeting_of_the_Lord_icon.jpg",
    "07.04": "https://upload.wikimedia.org/wikipedia/commons/thumb/2/21/Annunciation_icon_Andrei_Rublev.jpg/800px-Annunciation_icon_Andrei_Rublev.jpg",
    "19.08": "https://upload.wikimedia.org/wikipedia/commons/thumb/e/e5/Transfiguration_by_Feofan_Grek.jpg/800px-Transfiguration_by_Feofan_Grek.jpg",
    "27.09": "https://upload.wikimedia.org/wikipedia/commons/thumb/7/74/Exaltation_of_the_Cross_icon.jpg/800px-Exaltation_of_the_Cross_icon.jpg",
    "04.12": "https://upload.wikimedia.org/wikipedia/commons/thumb/1/1e/Entry_into_the_Temple.jpg/800px-Entry_into_the_Temple.jpg",
    # Богородичные праздники
    "28.08": "https://upload.wikimedia.org/wikipedia/commons/thumb/c/cf/Dormition_icon.jpg/800px-Dormition_icon.jpg",
    "21.09": "https://upload.wikimedia.org/wikipedia/commons/thumb/3/3e/Nativity_of_Mary_icon.jpg/800px-Nativity_of_Mary_icon.jpg",
    "14.10": "https://upload.wikimedia.org/wikipedia/commons/thumb/2/2b/Pokrov_icon.jpg/800px-Pokrov_icon.jpg",
    "22.08": "https://upload.wikimedia.org/wikipedia/commons/thumb/3/30/Our_Lady_of_Kazan_icon.jpg/800px-Our_Lady_of_Kazan_icon.jpg",
    "04.11": "https://upload.wikimedia.org/wikipedia/commons/thumb/3/30/Our_Lady_of_Kazan_icon.jpg/800px-Our_Lady_of_Kazan_icon.jpg",
    "06.03": "https://upload.wikimedia.org/wikipedia/commons/thumb/f/f2/Iverskaya_icon.jpg/800px-Iverskaya_icon.jpg",
    # Святые
    "19.12": "https://upload.wikimedia.org/wikipedia/commons/thumb/4/4b/Nicholas_icon.jpg/800px-Nicholas_icon.jpg",
    "22.05": "https://upload.wikimedia.org/wikipedia/commons/thumb/4/4b/Nicholas_icon.jpg/800px-Nicholas_icon.jpg",
    "06.05": "https://upload.wikimedia.org/wikipedia/commons/thumb/8/8c/George_icon.jpg/800px-George_icon.jpg",
    "02.05": "https://upload.wikimedia.org/wikipedia/commons/thumb/b/b0/Matrona_icon.jpg/800px-Matrona_icon.jpg",
    "08.10": "https://upload.wikimedia.org/wikipedia/commons/thumb/d/d6/Sergius_icon.jpg/800px-Sergius_icon.jpg",
    "01.08": "https://upload.wikimedia.org/wikipedia/commons/thumb/6/6e/Seraphim_of_Sarov_icon.jpg/800px-Seraphim_of_Sarov_icon.jpg",
    "15.01": "https://upload.wikimedia.org/wikipedia/commons/thumb/6/6e/Seraphim_of_Sarov_icon.jpg/800px-Seraphim_of_Sarov_icon.jpg",
    "02.08": "https://upload.wikimedia.org/wikipedia/commons/thumb/5/52/Elijah_icon.jpg/800px-Elijah_icon.jpg",
    "12.07": "https://upload.wikimedia.org/wikipedia/commons/thumb/1/1b/Peter_and_Paul_icon.jpg/800px-Peter_and_Paul_icon.jpg",
    "13.07": "https://upload.wikimedia.org/wikipedia/commons/thumb/1/1b/Peter_and_Paul_icon.jpg/800px-Peter_and_Paul_icon.jpg",
    "11.09": "https://upload.wikimedia.org/wikipedia/commons/thumb/c/c7/John_the_Baptist_icon.jpg/800px-John_the_Baptist_icon.jpg",
    "07.07": "https://upload.wikimedia.org/wikipedia/commons/thumb/c/c7/John_the_Baptist_icon.jpg/800px-John_the_Baptist_icon.jpg",
    "17.12": "https://upload.wikimedia.org/wikipedia/commons/thumb/a/a9/Barbara_icon.jpg/800px-Barbara_icon.jpg",
    "31.10": "https://upload.wikimedia.org/wikipedia/commons/thumb/5/59/Luke_icon.jpg/800px-Luke_icon.jpg",
    "21.11": "https://upload.wikimedia.org/wikipedia/commons/thumb/b/b3/Michael_icon.jpg/800px-Michael_icon.jpg",
    "08.11": "https://upload.wikimedia.org/wikipedia/commons/thumb/a/a0/Demetrius_of_Thessaloniki_icon.jpg/800px-Demetrius_of_Thessaloniki_icon.jpg",
    "30.09": "https://upload.wikimedia.org/wikipedia/commons/thumb/f/f4/Faith_Hope_Love_icon.jpg/800px-Faith_Hope_Love_icon.jpg",
    "03.06": "https://upload.wikimedia.org/wikipedia/commons/thumb/d/d6/Sergius_icon.jpg/800px-Sergius_icon.jpg",
    "04.06": "https://upload.wikimedia.org/wikipedia/commons/thumb/d/d6/Sergius_icon.jpg/800px-Sergius_icon.jpg",
}

# 31 икона — ротация по числу месяца (каждый день месяца своя икона)
DAILY_ICONS = {
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

def get_icon_for_today() -> str:
    """Возвращает икону дня — сначала праздничную, потом по числу месяца"""
    today_key = datetime.now().strftime("%d.%m")
    day_num = datetime.now().day
    return FEAST_ICONS.get(today_key) or DAILY_ICONS.get(day_num, DAILY_ICONS[1])


def get_icon_for_date(msk_now: datetime) -> str:
    """Икона выбирается по московской дате, а не по часовому поясу сервера."""
    today_key = msk_now.strftime("%d.%m")
    return FEAST_ICONS.get(today_key) or DAILY_ICONS.get(msk_now.day, DAILY_ICONS[1])


# Единая визуальная редакционная система канала.
# Изображение всегда связано с темой публикации; подпись честно называет образ.
CHANNEL_VISUAL_ASSETS = [
    {"key": "kazan", "title": "Казанская икона Божией Матери", "kind": "theotokos", "url": DAILY_ICONS[1]},
    {"key": "nicholas", "title": "святитель Николай Чудотворец", "kind": "saint", "url": DAILY_ICONS[2]},
    {"key": "michael", "title": "Архангел Михаил", "kind": "saint", "url": DAILY_ICONS[3]},
    {"key": "seraphim", "title": "преподобный Серафим Саровский", "kind": "saint", "url": DAILY_ICONS[4]},
    {"key": "john_baptist", "title": "Иоанн Предтеча", "kind": "saint", "url": DAILY_ICONS[5]},
    {"key": "sergius", "title": "преподобный Сергий Радонежский", "kind": "saint", "url": DAILY_ICONS[6]},
    {"key": "george", "title": "великомученик Георгий Победоносец", "kind": "saint", "url": DAILY_ICONS[7]},
    {"key": "matrona", "title": "блаженная Матрона Московская", "kind": "saint", "url": DAILY_ICONS[8]},
    {"key": "peter_paul", "title": "апостолы Пётр и Павел", "kind": "saint", "url": DAILY_ICONS[9]},
    {"key": "elijah", "title": "пророк Илия", "kind": "saint", "url": DAILY_ICONS[10]},
    {"key": "demetrius", "title": "великомученик Димитрий Солунский", "kind": "saint", "url": DAILY_ICONS[11]},
    {"key": "nativity", "title": "Рождество Христово", "kind": "feast", "url": DAILY_ICONS[12]},
    {"key": "iverskaya", "title": "Иверская икона Божией Матери", "kind": "theotokos", "url": DAILY_ICONS[13]},
    {"key": "cross", "title": "Воздвижение Креста Господня", "kind": "feast", "url": DAILY_ICONS[14]},
    {"key": "annunciation", "title": "Благовещение Пресвятой Богородицы", "kind": "feast", "url": DAILY_ICONS[15]},
    {"key": "dormition", "title": "Успение Пресвятой Богородицы", "kind": "feast", "url": DAILY_ICONS[16]},
    {"key": "transfiguration", "title": "Преображение Господне", "kind": "feast", "url": DAILY_ICONS[17]},
    {"key": "theophany", "title": "Крещение Господне", "kind": "feast", "url": DAILY_ICONS[18]},
    {"key": "pokrov", "title": "Покров Пресвятой Богородицы", "kind": "feast", "url": DAILY_ICONS[19]},
    {"key": "barbara", "title": "великомученица Варвара", "kind": "saint", "url": DAILY_ICONS[21]},
    {"key": "luke", "title": "апостол и евангелист Лука", "kind": "saint", "url": DAILY_ICONS[22]},
    {"key": "nativity_mary", "title": "Рождество Пресвятой Богородицы", "kind": "feast", "url": DAILY_ICONS[23]},
    {"key": "meeting", "title": "Сретение Господне", "kind": "feast", "url": DAILY_ICONS[24]},
    {"key": "faith_hope_love", "title": "мученицы Вера, Надежда, Любовь и София", "kind": "saint", "url": DAILY_ICONS[25]},
]

FEAST_VISUAL_TITLES = {
    "07.01": "Рождество Христово", "19.01": "Крещение Господне",
    "15.02": "Сретение Господне", "07.04": "Благовещение Пресвятой Богородицы",
    "19.08": "Преображение Господне", "27.09": "Воздвижение Креста Господня",
    "28.08": "Успение Пресвятой Богородицы", "21.09": "Рождество Пресвятой Богородицы",
    "14.10": "Покров Пресвятой Богородицы", "04.11": "Казанская икона Божией Матери",
    "19.12": "святитель Николай Чудотворец", "22.05": "святитель Николай Чудотворец",
    "06.05": "великомученик Георгий Победоносец", "02.05": "блаженная Матрона Московская",
    "08.10": "преподобный Сергий Радонежский", "02.08": "пророк Илия",
    "12.07": "апостолы Пётр и Павел", "11.09": "Иоанн Предтеча",
    "07.07": "Иоанн Предтеча", "17.12": "великомученица Варвара",
    "31.10": "апостол и евангелист Лука", "21.11": "Архангел Михаил",
    "08.11": "великомученик Димитрий Солунский", "30.09": "мученицы Вера, Надежда, Любовь и София",
}

SAINT_VISUAL_KEYWORDS = {
    "никол": "nicholas", "михаил": "michael", "серафим": "seraphim",
    "иоанн предтеч": "john_baptist", "серги": "sergius", "георги": "george",
    "матрон": "matrona", "петр": "peter_paul", "пётр": "peter_paul", "павел": "peter_paul",
    "илия": "elijah", "димитри": "demetrius", "варвар": "barbara",
    "лук": "luke", "вера": "faith_hope_love", "надежд": "faith_hope_love",
    "любов": "faith_hope_love", "софи": "faith_hope_love",
}


def _visual_by_key(key: str):
    return next((item for item in CHANNEL_VISUAL_ASSETS if item["key"] == key), None)


def _rotating_visual(msk_now: datetime, salt: int = 0, saint_only: bool = False):
    pool = [x for x in CHANNEL_VISUAL_ASSETS if (not saint_only or x["kind"] == "saint")]
    return pool[(msk_now.toordinal() + salt) % len(pool)]


def _saints_for_visual_date(msk_now: datetime):
    date_key = msk_now.strftime("%d.%m")
    result = []
    for name, days in SAINTS_BY_NAME.items():
        for day_str, description in days:
            if day_str == date_key:
                result.append((name, description))
    return result


def _unique_visual_urls(*urls):
    result = []
    for url in urls:
        if url and url not in result:
            result.append(url)
    return result


def select_channel_visual(msk_now: datetime, hour: int, cta_key: str, rubric: str):
    """Возвращает тематический визуал или None согласно утверждённой сетке.

    Каждый день: 09:00 и 20:00 с изображением.
    Третий визуальный слот: 07:00 во вторник/пятницу или 12:00 в остальные дни.
    Субботнее житие и демонстрация распознавания иконы также всегда с изображением.
    """
    date_key = msk_now.strftime("%d.%m")
    weekday = msk_now.weekday()

    if hour == 9 or cta_key == "saint":
        if date_key in FEAST_ICONS:
            title = FEAST_VISUAL_TITLES.get(date_key, get_todays_feast() or "праздничная икона")
            primary = FEAST_ICONS[date_key]
            fallback = _rotating_visual(msk_now, salt=9)
            return {
                "title": title,
                "urls": _unique_visual_urls(primary, fallback["url"], DAILY_ICONS[1]),
                "prompt_note": f"На изображении будет «{title}». Текст должен быть прямо связан с этим праздником или святым и не содержать непроверенных фактов.",
            }
        saints = _saints_for_visual_date(msk_now)
        searchable = " ".join(f"{n} {d}" for n, d in saints).lower()
        for keyword, key in SAINT_VISUAL_KEYWORDS.items():
            if keyword in searchable:
                asset = _visual_by_key(key)
                return {
                    "title": asset["title"],
                    "urls": _unique_visual_urls(asset["url"], get_icon_for_date(msk_now), DAILY_ICONS[1]),
                    "prompt_note": f"На изображении будет «{asset['title']}». Сделай публикацию непосредственно об этом святом и сегодняшней памяти, используя только проверяемые сведения.",
                }
        asset = _rotating_visual(msk_now, salt=9, saint_only=True)
        return {
            "title": f"{asset['title']} — образ для молитвенного размышления",
            "urls": _unique_visual_urls(asset["url"], get_icon_for_date(msk_now), DAILY_ICONS[1]),
            "prompt_note": f"Календарная часть остаётся главной. На изображении будет «{asset['title']}» как отдельный образ для молитвенного размышления. Не называй его святым сегодняшнего дня без подтверждения.",
        }

    if hour == 20 or cta_key == "evening":
        asset = _rotating_visual(msk_now, salt=20)
        return {
            "title": asset["title"],
            "urls": _unique_visual_urls(asset["url"], DAILY_ICONS[13], DAILY_ICONS[1]),
            "prompt_note": f"На изображении будет «{asset['title']}». Свяжи вечернюю молитву с благодарностью, надеждой и миром в сердце; не приписывай образу неподтверждённые свойства.",
        }

    if cta_key == "life":
        asset = _rotating_visual(msk_now, salt=11, saint_only=True)
        return {
            "title": asset["title"],
            "urls": _unique_visual_urls(asset["url"], DAILY_ICONS[2], DAILY_ICONS[4]),
            "prompt_note": f"На изображении будет «{asset['title']}». Расскажи проверяемое житие именно этого святого, его подвиг и один практический урок.",
        }

    if cta_key == "showcase_photo":
        asset = _rotating_visual(msk_now, salt=17)
        return {
            "title": asset["title"],
            "urls": _unique_visual_urls(asset["url"], DAILY_ICONS[1]),
            "prompt_note": f"На изображении будет «{asset['title']}». Объясни, что незнакомую икону можно сфотографировать и отправить помощнику для предварительного определения образа.",
        }

    # Третий визуал дня: 12:00 в пн/ср/чт/сб/вс.
    if hour == 12 and weekday in {0, 2, 3, 5, 6}:
        saint_only = weekday in {2, 5}
        asset = _rotating_visual(msk_now, salt=12, saint_only=saint_only)
        if weekday in {2, 5}:
            note = f"На изображении будет «{asset['title']}». Расскажи проверяемый эпизод именно из жизни этого святого и практический урок для современного человека."
        elif weekday == 3:
            note = f"На изображении будет «{asset['title']}». Объясни связанную с этим образом православную традицию, символ или правило поведения в храме."
        elif weekday == 6:
            note = f"На изображении будет «{asset['title']}». Порекомендуй реально существующую книгу, фильм или документальный материал, напрямую связанный с этим святым, праздником или темой."
        else:
            note = f"На изображении будет «{asset['title']}». Объясни один церковный термин, символ или практику, которые естественно связаны с этим образом."
        return {
            "title": asset["title"],
            "urls": _unique_visual_urls(asset["url"], DAILY_ICONS[1]),
            "prompt_note": note,
        }

    # Во вторник и пятницу третий визуальный слот переносится на утро.
    if hour == 7 and weekday in {1, 4}:
        asset = _rotating_visual(msk_now, salt=7)
        return {
            "title": asset["title"],
            "urls": _unique_visual_urls(asset["url"], DAILY_ICONS[13], DAILY_ICONS[1]),
            "prompt_note": f"На изображении будет «{asset['title']}». Свяжи утренний текст с надеждой, благодарностью и началом дня, не выдумывая фактов об образе.",
        }

    return None


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
    "gospel": ("📖 Откройте Евангелие дня.", "📖 Евангелие дня", "ch_gospel"),
    "photo": ("📸 Отправьте фото иконы помощнику.", "📸 Узнать икону", "ch_photo"),
    "church": ("🗺️ Найдите ближайший храм.", "🗺️ Найти храм", "ch_church"),
    "showcase_prayer": ("🙏 Выберите молитву по своей ситуации.", "🙏 Выбрать молитву", "ch_showcase_prayer"),
    "showcase_photo": ("📸 Отправьте фото иконы для определения образа.", "📸 Определить икону", "ch_showcase_photo"),
    "showcase_angel": ("👼 Найдите возможные дни памяти покровителя.", "👼 Узнать день ангела", "ch_showcase_angel"),
    "showcase_confession": ("📿 Откройте спокойную подготовку к исповеди.", "📿 Подготовиться", "ch_showcase_confession"),
}


def get_channel_cta(cta_key: str):
    footer, button, source = CHANNEL_CTA.get(cta_key, CHANNEL_CTA["guidance"])
    deep_link = f"{MAX_BOT_URL}?start={source}"
    return "\n\n─────────────────\n" + footer, [[link_btn(button, deep_link)]], deep_link


def channel_post_exists(post_key: str) -> bool:
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute("SELECT status FROM channel_posts WHERE post_key=?", (post_key,)).fetchone()
    conn.close()
    return bool(row and row[0] == "sent")


def save_channel_post(post_key: str, post_date: str, slot: str, rubric: str, topic: str, content: str, status: str):
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute(
            """INSERT OR REPLACE INTO channel_posts
               (post_key,post_date,slot,rubric,topic,content,status,created_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (post_key, post_date, slot, rubric, topic[:250], content[:3900], status, datetime.now().isoformat()),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logging.error(f"Канал: не удалось сохранить журнал публикации: {e}")


def recent_channel_topics(limit: int = 30) -> str:
    try:
        conn = sqlite3.connect(DB_PATH)
        rows = conn.execute(
            "SELECT rubric,topic FROM channel_posts WHERE status='sent' ORDER BY created_at DESC LIMIT ?", (limit,)
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


def _max_response_ok(payload):
    if not isinstance(payload, dict) or not payload:
        return False
    raw = str(payload).lower()
    if "error" in payload or "errors" in payload or "attachment.not.ready" in raw:
        return False
    return bool(payload.get("message") or payload.get("body") or payload.get("timestamp") or payload.get("message_id") or payload.get("success") is True)


def _attachment_not_ready(payload):
    return "attachment.not.ready" in str(payload).lower()


async def post_to_channel(text, photo_url=None, buttons=None, deep_link=None):
    """Отправляет пост в MAX; для изображения пробует несколько резервных URL."""
    text = clean_channel_markup(text)
    keyboard = {"type": "inline_keyboard", "payload": {"buttons": buttons}} if buttons else None
    photo_candidates = photo_url if isinstance(photo_url, (list, tuple)) else ([photo_url] if photo_url else [])

    for candidate_url in _unique_visual_urls(*photo_candidates):
        try:
            timeout = httpx.Timeout(45.0, connect=20.0)
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
                meta = await client.post(f"{MAX_API}/uploads?type=image", headers={"Authorization": MAX_TOKEN})
                meta.raise_for_status()
                upload_url = meta.json().get("url")
                if not upload_url:
                    raise RuntimeError(f"MAX не вернул upload_url: {meta.text[:300]}")

                img = await client.get(candidate_url, headers={"User-Agent": "Mozilla/5.0 VeraBot/2.0"})
                img.raise_for_status()
                if len(img.content) < 1000:
                    raise RuntimeError("Изображение пустое или слишком маленькое")
                ctype = img.headers.get("content-type", "image/jpeg").split(";")[0]
                if not ctype.startswith("image/"):
                    ctype = "image/jpeg"
                ext = "png" if "png" in ctype else "webp" if "webp" in ctype else "jpg"

                uploaded = await client.post(
                    upload_url,
                    files={"data": (f"vera_visual.{ext}", img.content, ctype)},
                )
                uploaded.raise_for_status()
                token = _extract_upload_token(uploaded.json())
                if not token:
                    raise RuntimeError(f"MAX не вернул token: {uploaded.text[:300]}")

                attachments = [{"type": "image", "payload": {"token": token}}]
                if keyboard:
                    attachments.append(keyboard)
                payload = {"text": text[:4000], "attachments": attachments}

                for attempt, delay in enumerate((0, 2, 4, 7), 1):
                    if delay:
                        await asyncio.sleep(delay)
                    result = await max_request("POST", f"messages?chat_id={MAX_CHANNEL_ID}", payload)
                    if _max_response_ok(result):
                        logging.info(f"Канал: изображение+CTA отправлены, попытка {attempt}, url={candidate_url}")
                        return True
                    if not _attachment_not_ready(result):
                        raise RuntimeError(f"MAX отклонил изображение: {result}")
        except Exception as e:
            logging.error(f"Канал: визуал не отправлен ({candidate_url}), пробую резервный: {e}")

    payload = {"text": text[:4000]}
    if keyboard:
        payload["attachments"] = [keyboard]
    result = await max_request("POST", f"messages?chat_id={MAX_CHANNEL_ID}", payload)
    if _max_response_ok(result):
        logging.warning("Канал: публикация отправлена без изображения после исчерпания визуальных fallback")
        return True
    fallback = (text + f"\n\nОткрыть нужный раздел: {deep_link or MAX_BOT_URL}")[:4000]
    result2 = await max_request("POST", f"messages?chat_id={MAX_CHANNEL_ID}", {"text": fallback})
    return _max_response_ok(result2)



CHANNEL_TITLE_EMOJI = {
    "morning": "🌅", "quote": "✝️", "saint": "👼", "guidance": "🕯️",
    "practical": "⛪", "story": "📖", "evening": "🌙", "qa": "❓",
    "life": "📖", "film": "📚", "gospel": "📖", "photo": "📸",
    "church": "⛪", "showcase_prayer": "🙏", "showcase_photo": "📸",
    "showcase_angel": "👼", "showcase_confession": "📿",
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
    "gospel": "Евангелие дня",
    "photo": "Как узнать образ на иконе",
    "church": "Храм и православная традиция",
    "showcase_prayer": "Молитва рядом в нужный момент",
    "showcase_photo": "Не знаете, кто изображён на иконе?",
    "showcase_angel": "Как узнать своего небесного покровителя",
    "showcase_confession": "Как подготовиться к первой исповеди",
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
}


async def generate_channel_post(prompt, cta_key, rubric, visual_prompt_note="", visual_title=""):
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
        msg = await asyncio.to_thread(
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
    footer, buttons, deep_link = get_channel_cta(cta_key)
    visual_line = f"\n\n🖼️ На изображении: {clean_channel_markup(visual_title)}" if visual_title else ""
    return text + visual_line + footer, buttons, deep_link, extract_topic(text)


def build_daily_slots(msk_now: datetime):
    day = msk_now.strftime("%d %B")
    weekday = msk_now.weekday()
    midday_rotation = {
        0: ("церковное слово", "practical", "Объясни одно церковное слово или элемент богослужения простыми словами и приведи практический пример."),
        1: ("вопрос новичка", "qa", "Разбери один частый вопрос человека, который недавно пришёл к вере. Дай спокойный и конкретный ответ."),
        2: ("история святого", "story", "Расскажи проверяемый эпизод из жизни православного святого и практический урок для современного человека."),
        3: ("храм и традиция", "church", "Расскажи об одной православной традиции или о том, как вести себя в храме. Дай 3 понятных практических шага."),
        4: ("подготовка к Таинству", "practical", "Дай бережную практическую памятку по подготовке к исповеди, Причастию или посещению храма. Уточни, что правила согласуют со священником своего прихода."),
        5: ("житие и пример", "story", "Расскажи краткую проверяемую историю православного святого и чему учит его пример."),
        6: ("семейное чтение", "film", "Порекомендуй реально существующую православную книгу, фильм или документальный проект для семейного просмотра. Если не уверен в точных данных, не указывай год."),
    }
    midday = midday_rotation[weekday]
    return [
        (7, "утренняя молитва", "morning", f"Утренняя молитвенная публикация на {day}: благодарность, просьба о помощи и один простой настрой на день."),
        (8, "мысль дня", "quote", "Передай одну проверяемую мысль святого отца без сомнительной дословной цитаты и кратко объясни её на жизненном примере."),
        (9, "святой или праздник дня", "saint", "__DYNAMIC_SAINT__"),
        (10, "практическая вера", "guidance", "Разбери конкретную жизненную трудность: рассеянность в молитве, тревога, обида, уныние, семейная ссора или страх. Дай 3 бережных практических шага."),
        (12, midday[0], midday[1], midday[2]),
        (20, "вечерняя молитва", "evening", "Вечерняя молитвенная публикация: благодарность за день, просьба о прощении и мирном сне. Коротко и тепло."),
    ]


def dynamic_saint_prompt(msk_now: datetime) -> str:
    feast = get_todays_feast()
    saints = get_todays_saints()
    date_text = msk_now.strftime("%d.%m")
    if feast:
        return f"Сегодня {date_text}, праздник: {feast}. Кратко и точно объясни смысл праздника, традицию дня и один практический вывод."
    if saints:
        names = ", ".join(s[0] for s in saints[:2])
        desc = saints[0][1] or ""
        return f"Сегодня {date_text}, память: {names}. {desc}. Расскажи только проверяемые сведения и один практический урок."
    return f"Сегодня {date_text}. Напиши календарное духовное напоминание без выдумывания святого дня."


def special_slots(msk_now: datetime):
    """Два демонстрационных поста в неделю + три традиционные спецрубрики."""
    wd = msk_now.weekday()
    slots = []
    if wd == 1:  # вторник
        slots.append((17, "возможности помощника: молитвы", "showcase_prayer", FALLBACK_POSTS["showcase_prayer"]))
    elif wd == 3:  # четверг, чередуем по номеру недели
        if int(msk_now.strftime("%W")) % 2:
            slots.append((17, "возможности помощника: икона", "showcase_photo", FALLBACK_POSTS["showcase_photo"]))
        else:
            slots.append((17, "возможности помощника: исповедь", "showcase_confession", FALLBACK_POSTS["showcase_confession"]))
    if wd == 4:
        slots.append((11, "вопрос-ответ недели", "qa", "Выбери частый вопрос о вере, молитве или Таинствах у начинающего и дай конкретный бережный ответ."))
    elif wd == 5:
        slots.append((11, "житие недели", "life", "Расскажи проверяемое житие одного православного святого: путь, подвиг и значение для Церкви. Без выдуманных чудес."))
    elif wd == 6:
        slots.append((11, "фильм или книга недели", "film", "Порекомендуй реально существующий православный фильм, документальный проект или книгу. Укажи, кому подойдёт и почему."))
    return slots


CHANNEL_PUBLISH_LOCK = asyncio.Lock()


async def publish_channel_slot(msk_now: datetime, hour: int, rubric: str, cta_key: str, prompt: str):
    """Публикует один слот и фиксирует успех только после подтверждения MAX."""
    async with CHANNEL_PUBLISH_LOCK:
        date_key = msk_now.strftime("%Y-%m-%d")
        post_key = f"{date_key}_{hour:02d}_{rubric}"
        if channel_post_exists(post_key):
            return False
        if prompt == "__DYNAMIC_SAINT__":
            prompt = dynamic_saint_prompt(msk_now)
        visual = select_channel_visual(msk_now, hour, cta_key, rubric)
        text, buttons, deep_link, topic = await generate_channel_post(
            prompt, cta_key, rubric,
            visual_prompt_note=visual.get("prompt_note", "") if visual else "",
            visual_title=visual.get("title", "") if visual else "",
        )
        photo_urls = visual.get("urls") if visual else None
        ok = await post_to_channel(text, photo_urls, buttons, deep_link)
        save_channel_post(
            post_key, date_key, f"{hour:02d}:00", rubric,
            topic, text, "sent" if ok else "failed"
        )
        if ok:
            logging.info(f"Канал: успешно опубликовано — {rubric}")
            if hour == 7:
                asyncio.create_task(morning_broadcast_max())
        else:
            logging.error(
                f"Канал: публикация не прошла — {rubric}; "
                "будет повторена в текущем окне"
            )
        return ok


def channel_posts_today(msk_now: datetime):
    """Возвращает журнал публикаций канала за московскую дату."""
    try:
        date_key = msk_now.strftime("%Y-%m-%d")
        conn = sqlite3.connect(DB_PATH)
        rows = conn.execute(
            """SELECT slot,rubric,status,created_at
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
    for slot, _rubric, status, _created in sent_rows:
        if status == "sent":
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


async def channel_scheduler():
    """Автопостинг MAX по МСК с восстановлением после перезапуска."""
    await asyncio.sleep(15)
    last_error_notice = None

    # Критично: раньше здесь возникал NameError из-за отсутствующего timedelta,
    # поэтому цикл не доходил до публикаций. Теперь время вычисляется корректно,
    # а после старта выпускается один последний пропущенный пост.
    try:
        startup_msk = datetime.utcnow() + timedelta(hours=3)
        await publish_latest_missed_slot(startup_msk)
    except Exception as e:
        logging.exception(f"Канал: ошибка восстановления после запуска: {e}")
        try:
            await send_message(
                OWNER_ID,
                "⚠️ Канал MAX не смог восстановить публикацию после запуска.\n\n"
                f"Ошибка: {str(e)[:700]}"
            )
        except Exception:
            pass

    while True:
        try:
            msk_now = datetime.utcnow() + timedelta(hours=3)
            slots = build_daily_slots(msk_now) + special_slots(msk_now)
            for hour, rubric, cta_key, prompt in slots:
                if msk_now.hour == hour and msk_now.minute < 30:
                    await publish_channel_slot(
                        msk_now, hour, rubric, cta_key, prompt
                    )
                    await asyncio.sleep(3)
        except Exception as e:
            logging.exception(f"Канал: ошибка планировщика: {e}")
            now = datetime.utcnow()
            if (
                last_error_notice is None
                or now - last_error_notice >= timedelta(hours=1)
            ):
                last_error_notice = now
                try:
                    await send_message(
                        OWNER_ID,
                        "⚠️ Ошибка автопостинга в MAX-канал.\n\n"
                        f"{str(e)[:700]}"
                    )
                except Exception:
                    pass
        await asyncio.sleep(30)

# ========== FASTAPI ==========
app = FastAPI()

@app.on_event("startup")
async def startup():
    init_db()
    await register_webhook()
    asyncio.create_task(asyncio.to_thread(ensure_review_sheet_schema))
    asyncio.create_task(channel_scheduler())
    asyncio.create_task(angel_reminder_loop_max())
    logging.info("Vera MAX Bot запущен!")

@app.post("/webhook")
async def webhook(request: Request):
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
  <p>Вы можете вернуться в бот <strong>@Moya_Vera_bot</strong></p>
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
