import asyncio
import sqlite3
import logging
import os
import base64
import httpx
from datetime import datetime, date
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from openai import AsyncOpenAI
import anthropic
import uvicorn
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
MAX_TOKEN     = _env.get("MAX_TOKEN") or os.environ.get("MAX_TOKEN", "")
MAX_API       = "https://platform-api.max.ru"
OPENAI_KEY    = _env.get("OPENAI_KEY") or os.environ.get("OPENAI_KEY", "")
ANTHROPIC_KEY = _env.get("ANTHROPIC_KEY") or os.environ.get("ANTHROPIC_KEY", "")
OWNER_ID      = 549639607
DB_PATH       = "/root/vera_max.db"

logging.basicConfig(level=logging.INFO)
logging.info(f"MAX_TOKEN: {MAX_TOKEN[:15] if MAX_TOKEN else 'EMPTY'}...")
logging.info(f"OPENAI_KEY: {OPENAI_KEY[:15] if OPENAI_KEY else 'EMPTY'}...")

# ========== КЛИЕНТЫ AI ==========
openai_client = AsyncOpenAI(api_key=OPENAI_KEY)
claude_client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)

# ========== MAX API ==========
async def send_message(chat_id, text, buttons=None):
    headers = {"Authorization": f"Bearer {MAX_TOKEN}", "Content-Type": "application/json"}
    payload = {"text": text}
    if buttons:
        payload["attachments"] = [{"type": "inline_keyboard", "payload": {"buttons": buttons}}]
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(f"{MAX_API}/messages?chat_id={chat_id}", json=payload, headers=headers)
            return r.json()
    except Exception as e:
        logging.error(f"Ошибка send_message: {e}")

async def get_photo_bytes(photo_token):
    headers = {"Authorization": f"Bearer {MAX_TOKEN}"}
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(f"{MAX_API}/photos/{photo_token}", headers=headers)
            return r.content
    except Exception as e:
        logging.error(f"Ошибка get_photo: {e}")
        return None

# ========== КНОПКИ ==========
def btn(text, payload):
    return {"type": "callback", "text": text, "payload": payload}

def link_btn(text, url):
    return {"type": "link", "text": text, "url": url}

def main_menu_buttons():
    return [
        [btn("🙏 Молитвы", "prayers"), btn("📅 Календарь", "calendar")],
        [btn("⛪ Таинства и обряды", "sacraments"), btn("👼 Святые", "saints")],
        [btn("🏛️ Святые места", "holy_places"), btn("📚 Библиотека", "library")],
        [btn("📸 Определить по фото", "photo_menu"), btn("🗺️ Найти храм рядом", "find_church")],
        [btn("👤 Мой профиль", "profile"), btn("❓ Задать вопрос", "ask_question")],
        [btn("🕯️ Пожертвование на развитие проекта", "donate")],
        [btn("💬 Отзыв или пожелание", "review")],
    ]

def back_btn(payload="main_menu"):
    return [[btn("◀️ Главное меню", payload)]]

def prayers_buttons():
    return [
        [btn("🌅 Утренняя (рус)", "prayer_morning_ru"), btn("🌅 Утренняя (цс)", "prayer_morning_cs")],
        [btn("🌙 Вечерняя (рус)", "prayer_evening_ru"), btn("🌙 Вечерняя (цс)", "prayer_evening_cs")],
        [btn("🍽️ Перед едой", "prayer_before_meal"), btn("✝️ Символ веры", "prayer_symbol")],
        [btn("📿 Иисусова молитва", "prayer_jesus"), btn("🙏 Отче наш", "prayer_otche")],
        [btn("🌸 Богородице Дево", "prayer_bogorodice"), btn("📖 Канон покаянный", "prayer_pokayanny_kanon")],
        [btn("🕯️ Николаю Чудотворцу", "prayer_nikolay"), btn("🌺 Матроне Московской", "prayer_matrona")],
        [btn("✝️ Правило ко Причастию", "prayer_prichaschenie"), btn("🕯️ Молитва об упокоении", "prayer_upokoenie")],
        [btn("⭐ Избранные молитвы", "favorites")],
        [btn("◀️ Главное меню", "main_menu")],
    ]

def sacraments_buttons():
    return [
        [btn("📿 Исповедь", "sacr_ispoved"), btn("✝️ Причастие", "sacr_prichaschenie")],
        [btn("💧 Крещение", "sacr_kreshchenie"), btn("💍 Венчание", "sacr_venchanie")],
        [btn("🕯️ Отпевание", "sacr_otpevanie"), btn("🫒 Соборование", "sacr_sobor")],
        [btn("🏠 Освящение", "sacr_osvyashchenie"), btn("🕯️ Как ставить свечи", "sacr_svecha")],
        [btn("📝 Как подавать записки", "sacr_zapiska"), btn("⛪ Как вести себя в храме", "sacr_v_hrame")],
        [btn("◀️ Главное меню", "main_menu")],
    ]

def holy_places_buttons():
    return [
        [btn("🏙️ Москва", "place_moscow"), btn("🏙️ Санкт-Петербург", "place_spb")],
        [btn("📍 Монастыри Подмосковья", "place_podmoskove")],
        [btn("📍 Монастыри Центральной России", "place_central")],
        [btn("📍 Монастыри Севера и Северо-Запада", "place_northwest")],
        [btn("📍 Монастыри Урала и Сибири", "place_ural_siberia")],
        [btn("📍 Монастыри Юга и Крыма", "place_south")],
        [btn("✝️ Абхазия", "place_abkhazia")],
        [btn("⛵ Афон", "place_afon"), btn("🌍 Святые места мира", "place_world")],
        [btn("◀️ Главное меню", "main_menu")],
    ]

def calendar_buttons():
    return [
        [btn("📅 Сегодня", "cal_today")],
        [btn("🎉 Православные праздники", "cal_feasts")],
        [btn("🥗 Посты", "cal_fasts")],
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

def ask_question_buttons():
    return [
        [btn("💬 Кратко", "q_short"), btn("📖 Развёрнуто", "q_medium"), btn("🔍 Глубоко", "q_deep")],
        [btn("◀️ Главное меню", "main_menu")],
    ]

# ========== БАЗА ДАННЫХ ==========
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT DEFAULT '',
        first_name TEXT DEFAULT '',
        step TEXT DEFAULT '',
        church_name TEXT DEFAULT '',
        birth_date TEXT DEFAULT '',
        angel_day TEXT DEFAULT '',
        remind_days INTEGER DEFAULT 3,
        onboarded INTEGER DEFAULT 0
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
    cols = ["user_id","username","first_name","step","church_name","birth_date","angel_day","remind_days","onboarded"]
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

# ========== ДАННЫЕ ==========
# Импортируем все данные из основного бота
import sys
sys.path.insert(0, '/root')

# Копируем нужные словари прямо сюда для независимости
PRAYERS = {}
SACRAMENTS = {}
HOLY_PLACES = {}
LIBRARY_CONTENT = {}
SAINTS_BY_NAME = {}
FIXED_FEASTS = {}

# Загружаем из основного файла
try:
    import importlib.util
    spec = importlib.util.spec_from_file_location("vera_bot", "/root/vera_bot.py")
    # Не импортируем весь модуль чтобы не запускать бота
    # Читаем словари напрямую через exec
    exec_globals = {}
    with open("/root/vera_bot.py") as f:
        source = f.read()
    # Выполняем только блоки с данными
    import re
    # Берём всё до первого async def
    data_part = source.split("# ========== БАЗА ДАННЫХ ==========")[0]
    exec(data_part, exec_globals)
    PRAYERS = exec_globals.get("PRAYERS", {})
    SACRAMENTS = exec_globals.get("SACRAMENTS", {})
    HOLY_PLACES = exec_globals.get("HOLY_PLACES", {})
    LIBRARY_CONTENT = exec_globals.get("LIBRARY_CONTENT", {})
    SAINTS_BY_NAME = exec_globals.get("SAINTS_BY_NAME", {})
    FIXED_FEASTS = exec_globals.get("FIXED_FEASTS", {})
    logging.info(f"Данные загружены: {len(PRAYERS)} молитв, {len(SACRAMENTS)} таинств")
except Exception as e:
    logging.error(f"Ошибка загрузки данных: {e}")

def get_todays_saints():
    today = date.today()
    key = f"{today.month:02d}-{today.day:02d}"
    saints = []
    if key in FIXED_FEASTS:
        for name in FIXED_FEASTS[key]:
            if name in SAINTS_BY_NAME:
                saints.append((name, SAINTS_BY_NAME[name].get("desc", "")))
            else:
                saints.append((name, ""))
    return saints

# ========== AI ФУНКЦИИ ==========
async def ask_claude(question, depth="medium"):
    depths = {
        "short":  ("Отвечай кратко — 2-3 предложения.", 300),
        "medium": ("Отвечай развёрнуто — 5-7 предложений.", 600),
        "deep":   ("Дай глубокий богословский ответ — 10-15 предложений.", 1200),
    }
    system_add, max_tok = depths.get(depth, depths["medium"])
    try:
        msg = claude_client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=max_tok,
            system=(
                "Ты православный помощник — отвечаешь на вопросы о вере тепло, "
                "доступно и без осуждения. " + system_add
            ),
            messages=[{"role": "user", "content": question}]
        )
        return msg.content[0].text
    except Exception as e:
        logging.error(f"Ошибка Claude: {e}")
        return "Не удалось получить ответ. Попробуйте позже."

async def analyze_photo_max(photo_bytes, photo_type):
    prompt = (
        "На фотографии православный храм или монастырь. Определи: 1) Название если можешь; "
        "2) Архитектурный стиль; 3) Особенности; 4) Историческое значение. Отвечай по-русски."
        if photo_type == "church" else
        "На фотографии православная икона. Определи: 1) Кто изображён; 2) Тип иконы; "
        "3) Атрибуты и символика; 4) Краткое житие. Отвечай по-русски."
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

# ========== ОБРАБОТЧИКИ ==========
async def handle_start(chat_id, user_id, first_name, username):
    user = get_user(user_id, username, first_name)
    if not user.get("onboarded"):
        await send_message(chat_id,
            f"☦️ Добро пожаловать в «С верой»!\n\n"
            f"Я ваш православный помощник — здесь всё\n"
            f"что нужно для духовной жизни:\n\n"
            f"🙏 Молитвы на все случаи жизни\n"
            f"📅 Православный календарь и посты\n"
            f"⛪ Таинства — как подготовиться\n"
            f"👼 Жития святых и мощи\n"
            f"🏛️ Святые места России и мира\n"
            f"📸 Узнать храм или икону по фото\n"
            f"❓ Задать вопрос о вере\n\n"
            f"Чем могу помочь? ☦️",
            main_menu_buttons()
        )
        conn = sqlite3.connect(DB_PATH)
        conn.execute("UPDATE users SET onboarded=1 WHERE user_id=?", (user_id,))
        conn.commit()
        conn.close()
    else:
        name = user.get("church_name") or first_name
        await send_message(chat_id,
            f"☦️ С возвращением, {name}!\n\nРад видеть вас снова 🕊️\n\nЧем могу помочь?",
            main_menu_buttons()
        )

async def handle_callback(chat_id, user_id, payload, first_name=""):
    # Главное меню
    if payload == "main_menu":
        await send_message(chat_id, "☦️ Главное меню:", main_menu_buttons())

    # Молитвы
    elif payload == "prayers":
        await send_message(chat_id, "🙏 Выберите молитву:", prayers_buttons())

    elif payload.startswith("prayer_"):
        key = payload.replace("prayer_", "")
        prayer = PRAYERS.get(key)
        if prayer:
            text = f"🙏 {prayer['title']}\n\n{prayer['text']}"
            await send_message(chat_id, text[:4000], [
                [btn("⭐ Сохранить в избранное", f"save_prayer_{key}")],
                [btn("◀️ Молитвы", "prayers"), btn("🏠 Меню", "main_menu")],
            ])
        else:
            await send_message(chat_id, "Молитва не найдена.", back_btn())

    elif payload.startswith("save_prayer_"):
        key = payload.replace("save_prayer_", "")
        prayer = PRAYERS.get(key)
        if prayer:
            save_favorite(user_id, prayer["title"], prayer["text"])
            await send_message(chat_id, "⭐ Сохранено в избранное!", [
                [btn("◀️ Молитвы", "prayers")],
            ])

    elif payload == "favorites":
        favs = get_favorites(user_id)
        if not favs:
            await send_message(chat_id,
                "⭐ Избранных молитв пока нет.\n\nНажимайте ⭐ при чтении молитвы — она сохранится здесь.",
                back_btn()
            )
        else:
            buttons = [[btn(f"⭐ {title[:40]}", f"fav_show_{i}")] for i, (title, _) in enumerate(favs)]
            buttons.append([btn("◀️ Главное меню", "main_menu")])
            await send_message(chat_id, "⭐ Ваши избранные молитвы:", buttons)

    # Таинства
    elif payload == "sacraments":
        await send_message(chat_id, "⛪ Таинства и обряды\n\nВыберите раздел:", sacraments_buttons())

    elif payload.startswith("sacr_"):
        key = payload.replace("sacr_", "")
        sacr = SACRAMENTS.get(key)
        if sacr:
            text = f"{sacr['title']}\n\n{sacr['text']}"
            sacrament_prayers = {
                "ispoved": [("📖 Канон покаянный", "prayer_pokayanny_kanon"), ("🌅 Утренняя молитва", "prayer_morning_ru")],
                "prichaschenie": [("📖 Канон покаянный", "prayer_pokayanny_kanon"), ("✝️ Правило ко Причастию", "prayer_prichaschenie")],
                "kreshchenie": [("🌅 Утренняя молитва", "prayer_morning_ru")],
                "venchanie": [("🌅 Утренняя молитва", "prayer_morning_ru"), ("🌙 Вечерняя молитва", "prayer_evening_ru")],
                "otpevanie": [("🕯️ Молитва об упокоении", "prayer_upokoenie")],
                "sobor": [("🌅 Утренняя молитва", "prayer_morning_ru"), ("✝️ Правило ко Причастию", "prayer_prichaschenie")],
            }
            buttons = []
            for pname, pkey in sacrament_prayers.get(key, []):
                buttons.append([btn(pname, pkey)])
            buttons.append([btn("⭐ Сохранить", f"save_sacr_{key}")])
            buttons.append([btn("◀️ Таинства", "sacraments"), btn("🏠 Меню", "main_menu")])
            await send_message(chat_id, text[:4000], buttons)

    elif payload.startswith("save_sacr_"):
        key = payload.replace("save_sacr_", "")
        sacr = SACRAMENTS.get(key)
        if sacr:
            save_favorite(user_id, sacr["title"], sacr["text"])
            await send_message(chat_id, "⭐ Сохранено в избранное!", [[btn("◀️ Таинства", "sacraments")]])

    # Святые места
    elif payload == "holy_places":
        await send_message(chat_id, "🏛️ Святые места\n\nВыберите раздел:", holy_places_buttons())

    elif payload.startswith("place_"):
        key = payload.replace("place_", "")
        place = HOLY_PLACES.get(key)
        if place:
            text = f"{place['title']}\n\n{place['text']}"
            await send_message(chat_id, text[:4000], [
                [btn("⭐ Сохранить", f"save_place_{key}")],
                [btn("◀️ Святые места", "holy_places"), btn("🏠 Меню", "main_menu")],
            ])

    elif payload.startswith("save_place_"):
        key = payload.replace("save_place_", "")
        place = HOLY_PLACES.get(key)
        if place:
            save_favorite(user_id, place["title"], place["text"])
            await send_message(chat_id, "⭐ Сохранено!", [[btn("◀️ Святые места", "holy_places")]])

    # Календарь
    elif payload == "calendar":
        await send_message(chat_id, "📅 Православный календарь:", calendar_buttons())

    elif payload == "cal_today":
        today = datetime.now()
        today_str = today.strftime("%d %B %Y")
        saints = get_todays_saints()
        text = f"📅 Сегодня {today_str}\n\n"
        if saints:
            text += "👼 Память святых:\n"
            for name, desc in saints[:5]:
                text += f"— {name}\n"
        await send_message(chat_id, text, [[btn("◀️ Календарь", "calendar")]])

    elif payload == "cal_namedays":
        saints = get_todays_saints()
        today_str = datetime.now().strftime("%d %B")
        if saints:
            text = f"👼 Именинники {today_str}:\n\n"
            for name, desc in saints:
                text += f"✨ {name}\n"
        else:
            text = f"👼 Сегодня {today_str} именинников нет."
        await send_message(chat_id, text, [[btn("◀️ Календарь", "calendar")]])

    elif payload == "cal_pasxa":
        sacr = SACRAMENTS.get("pasха")
        if sacr:
            await send_message(chat_id, f"{sacr['title']}\n\n{sacr['text']}"[:4000], [[btn("◀️ Календарь", "calendar")]])

    elif payload == "cal_kreschenije":
        sacr = SACRAMENTS.get("kreschenije_prazdnik")
        if sacr:
            await send_message(chat_id, f"{sacr['title']}\n\n{sacr['text']}"[:4000], [[btn("◀️ Календарь", "calendar")]])

    # Библиотека
    elif payload == "library":
        await send_message(chat_id, "📚 Библиотека\n\nВыберите раздел:", library_buttons())

    elif payload.startswith("lib_"):
        key = payload.replace("lib_", "")
        if key == "pdf":
            pdf_buttons = [
                [link_btn("📖 Библия", "https://azbyka.ru/biblia/")],
                [link_btn("📖 Новый Завет", "https://azbyka.ru/otechnik/Biblia/novyj-zavet-sinodalnij-perevod/")],
                [link_btn("🙏 Молитвослов", "https://azbyka.ru/molitvoslov/")],
                [link_btn("📜 Псалтирь", "https://azbyka.ru/otechnik/Biblia/psaltir-v-russkom-perevode/")],
                [link_btn("📖 Лествица", "https://azbyka.ru/otechnik/Ioann_Lestvichnik/lestvitsa/")],
                [link_btn("📖 Добротолюбие", "https://azbyka.ru/otechnik/prochee/dobrotoljubie_tom1/")],
                [btn("◀️ Библиотека", "library")],
            ]
            await send_message(chat_id, "📥 Книги на сайте Азбука.ру — нажмите для открытия:", pdf_buttons)
        else:
            content = LIBRARY_CONTENT.get(key)
            if content:
                await send_message(chat_id, content["text"][:4000], [[btn("◀️ Библиотека", "library")]])

    # Святые
    elif payload == "saints":
        saints = get_todays_saints()
        today_str = datetime.now().strftime("%d %B")
        text = f"👼 Святые\n\n"
        if saints:
            text += f"Сегодня, {today_str}, память:\n"
            for name, _ in saints[:3]:
                text += f"— {name}\n"
            text += "\n"
        text += "Выберите действие:"
        await send_message(chat_id, text, [
            [btn("🔍 Найти святого по имени", "saint_search")],
            [btn("👼 Именинники сегодня", "cal_namedays")],
            [btn("◀️ Главное меню", "main_menu")],
        ])

    elif payload == "saint_search":
        set_step(user_id, "saint_search")
        await send_message(chat_id,
            "🔍 Введите имя святого или своё имя для поиска дней памяти:\n\nНапример: Николай, Матрона, Сергий",
            [[btn("◀️ Назад", "saints")]]
        )

    # Фото
    elif payload == "photo_menu":
        await send_message(chat_id, "📸 Что хотите определить?", [
            [btn("⛪ Это храм или монастырь", "photo_church")],
            [btn("🖼️ Это икона", "photo_icon")],
            [btn("◀️ Главное меню", "main_menu")],
        ])

    elif payload in ("photo_church", "photo_icon"):
        set_step(user_id, payload)
        ptype = "храм или монастырь" if payload == "photo_church" else "икону"
        await send_message(chat_id,
            f"📸 Отправьте фотографию — я определю {ptype} и расскажу о нём.",
            [[btn("◀️ Назад", "photo_menu")]]
        )

    # Найти храм
    elif payload == "find_church":
        set_step(user_id, "find_church_city")
        await send_message(chat_id,
            "🗺️ Напишите название вашего города — я найду православные храмы рядом.",
            [[btn("◀️ Главное меню", "main_menu")]]
        )

    # Профиль
    elif payload == "profile":
        user = get_user(user_id)
        church = user.get("church_name") or "не указано"
        birth = user.get("birth_date") or "не указана"
        angel = user.get("angel_day") or "не найден"
        await send_message(chat_id,
            f"👤 Мой профиль\n\n"
            f"✏️ Имя: {church}\n"
            f"🎂 Дата рождения: {birth}\n"
            f"👼 День ангела: {angel}",
            [
                [btn(f"✏️ Изменить имя", "profile_edit_name")],
                [btn(f"🎂 Изменить дату рождения", "profile_edit_birth")],
                [btn("⭐ Избранные молитвы", "favorites")],
                [btn("🙏 Молитва небесному покровителю", "profile_patron_prayer")],
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
            await send_message(chat_id, "👤 Укажите имя в профиле — найдём молитву вашему покровителю 🙏", [[btn("✏️ Указать имя", "profile_edit_name")]])
            return
        name_lower = name.lower()
        # Проверяем кеш
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT prayer FROM patron_prayers_cache WHERE name=?", (name_lower,))
        row = c.fetchone()
        conn.close()
        if row:
            await send_message(chat_id, f"🙏 Молитва небесному покровителю — {name}\n\n{row[0]}"[:4000], [[btn("◀️ Профиль", "profile")]])
            return
        await send_message(chat_id, "🙏 Нахожу молитву вашему покровителю...")
        try:
            msg = claude_client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=600,
                system="Напиши краткую православную молитву святому. 8-12 строк. Начни с обращения. Закончи Аминь. Только текст молитвы.",
                messages=[{"role": "user", "content": f"Напиши молитву святому: {name}"}]
            )
            prayer_text = msg.content[0].text
            conn2 = sqlite3.connect(DB_PATH)
            conn2.execute("INSERT OR REPLACE INTO patron_prayers_cache (name, prayer) VALUES (?,?)", (name_lower, prayer_text))
            conn2.commit()
            conn2.close()
            await send_message(chat_id, f"🙏 Молитва небесному покровителю — {name}\n\n{prayer_text}"[:4000], [[btn("◀️ Профиль", "profile")]])
        except Exception as e:
            logging.error(f"Ошибка молитвы: {e}")
            await send_message(chat_id, "🙏 Обратитесь к своему святому своими словами — Господь слышит молитву из сердца.", [[btn("◀️ Профиль", "profile")]])

    # Вопрос о вере
    elif payload == "ask_question":
        await send_message(chat_id, "❓ Выберите формат ответа:", ask_question_buttons())

    elif payload in ("q_short", "q_medium", "q_deep"):
        depth_map = {"q_short": "short", "q_medium": "medium", "q_deep": "deep"}
        set_step(user_id, f"question_{depth_map[payload]}")
        await send_message(chat_id, "✏️ Напишите ваш вопрос о вере:", [[btn("◀️ Назад", "ask_question")]])

    # Пожертвование
    elif payload == "donate":
        set_step(user_id, "donate_amount")
        await send_message(chat_id,
            "🕯️ Пожертвование на развитие проекта\nво славу Божию ☦️\n\n"
            "Если бот помогает вам в духовной жизни —\n"
            "вы можете поддержать его развитие.\n\n"
            "✏️ Напишите сумму в рублях и я создам ссылку для оплаты 👇",
            [[btn("◀️ Главное меню", "main_menu")]]
        )

    # Отзыв
    elif payload == "review":
        set_step(user_id, "review")
        await send_message(chat_id,
            "💬 Отзыв или пожелание\n\n"
            "Что вам нравится? Чего не хватает?\n"
            "Какие функции хотели бы видеть?\n\n"
            "✏️ Напишите ваш отзыв 👇",
            [[btn("◀️ Главное меню", "main_menu")]]
        )

    else:
        await send_message(chat_id, "☦️ Главное меню:", main_menu_buttons())

async def handle_text(chat_id, user_id, text, first_name=""):
    user = get_user(user_id)
    step = user.get("step", "")

    # Команды
    if text.strip() == "/start":
        await handle_start(chat_id, user_id, first_name, "")
        return

    # Шаги
    if step == "edit_name":
        name = text.strip()
        conn = sqlite3.connect(DB_PATH)
        conn.execute("UPDATE users SET church_name=? WHERE user_id=?", (name, user_id))
        conn.commit()
        conn.close()
        set_step(user_id, "idle")
        await send_message(chat_id, f"✅ Имя сохранено: {name}", [[btn("◀️ Профиль", "profile")]])
        return

    if step == "edit_birth":
        birth = text.strip()
        try:
            parts = birth.split(".")
            if len(parts) >= 2:
                day, month = int(parts[0]), int(parts[1])
                if 1 <= day <= 31 and 1 <= month <= 12:
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
        name = text.strip()
        results = []
        for saint_name, data in SAINTS_BY_NAME.items():
            if name.lower() in saint_name.lower():
                dates = data.get("dates", [])
                results.append(f"👼 {saint_name}: {', '.join(dates[:3])}")
        if results:
            response = f"🔍 Найдено для «{name}»:\n\n" + "\n".join(results[:10])
        else:
            response = f"👼 Не найдено святых с именем «{name}».\n\nПопробуйте другое написание."
        set_step(user_id, "idle")
        await send_message(chat_id, response, [[btn("◀️ Святые", "saints")]])
        return

    if step == "find_church_city":
        city = text.strip()
        maps_url = f"https://maps.yandex.ru/?text=православный+храм+{city}"
        set_step(user_id, "idle")
        await send_message(chat_id,
            f"🗺️ Православные храмы в городе {city}:\n\n{maps_url}",
            [[btn("◀️ Главное меню", "main_menu")]]
        )
        return

    if step == "review":
        set_step(user_id, "idle")
        await send_message(chat_id,
            "☦️ Спасибо за ваш отзыв!\n\nМы обязательно учтём его при развитии проекта.\nДа хранит вас Господь 🕊️",
            main_menu_buttons()
        )
        return

    if step == "donate_amount":
        try:
            amount = int(text.strip())
            if amount < 10:
                await send_message(chat_id, "⚠️ Минимальная сумма — 10 рублей. Введите сумму цифрой:")
                return
            from yookassa import Configuration, Payment as YPayment
            Configuration.account_id = "1363324"
            Configuration.secret_key = "live_-RKE9nsi8wZiM-5f00z78E84OYSi3M0Dj9w_-pE0Mvw"
            payment = YPayment.create({
                "amount": {"value": f"{amount}.00", "currency": "RUB"},
                "confirmation": {"type": "redirect", "return_url": "https://sveroy.ru"},
                "capture": True,
                "description": "Пожертвование на развитие «С верой» во славу Божию",
            }, str(uuid.uuid4()))
            set_step(user_id, "idle")
            await send_message(chat_id,
                f"🕯️ Пожертвование {amount} рублей\n\nНажмите кнопку для перехода к оплате 👇",
                [
                    [link_btn("💳 Перейти к оплате", payment.confirmation.confirmation_url)],
                    [btn("◀️ Главное меню", "main_menu")],
                ]
            )
        except ValueError:
            await send_message(chat_id, "⚠️ Введите сумму цифрой, например: 300")
        except Exception as e:
            logging.error(f"Ошибка платежа: {e}")
            await send_message(chat_id, "⚠️ Ошибка при создании платежа. Попробуйте позже.", [[btn("◀️ Меню", "main_menu")]])
        return

    if step and step.startswith("question_"):
        depth = step.replace("question_", "")
        await send_message(chat_id, "🙏 Отвечаю...")
        answer = await ask_claude(text, depth)
        set_step(user_id, "idle")
        await send_message(chat_id, answer[:4000], [
            [btn("❓ Ещё вопрос", "ask_question"), btn("🏠 Меню", "main_menu")],
        ])
        return

    # Если нет активного шага
    await send_message(chat_id, "☦️ Главное меню:", main_menu_buttons())

async def handle_photo_message(chat_id, user_id, photo_token):
    user = get_user(user_id)
    step = user.get("step", "")
    photo_type = "church" if step == "photo_church" else "icon"
    set_step(user_id, "idle")
    await send_message(chat_id, "⏳ Анализирую фото...")
    photo_bytes = await get_photo_bytes(photo_token)
    if not photo_bytes:
        await send_message(chat_id, "⚠️ Не удалось загрузить фото. Попробуйте ещё раз.", [[btn("◀️ Меню", "photo_menu")]])
        return
    result = await analyze_photo_max(photo_bytes, photo_type)
    await send_message(chat_id, result[:4000], [[btn("📸 Ещё фото", "photo_menu"), btn("🏠 Меню", "main_menu")]])

# ========== FASTAPI WEBHOOK ==========
app = FastAPI()

@app.post("/webhook")
async def webhook(request: Request):
    try:
        data = await request.json()
        logging.info(f"MAX webhook: {data}")

        update_type = data.get("update_type", "")

        if update_type == "message_created":
            msg = data.get("message", {})
            body = msg.get("body", {})
            chat_id = msg.get("recipient", {}).get("chat_id") or data.get("chat_id")
            sender = msg.get("sender", {})
            user_id = sender.get("user_id", 0)
            first_name = sender.get("name", "")

            # Фото
            attachments = body.get("attachments", [])
            for att in attachments:
                if att.get("type") == "image":
                    photo_token = att.get("payload", {}).get("token", "")
                    if photo_token:
                        user = get_user(user_id)
                        if user.get("step") in ("photo_church", "photo_icon"):
                            await handle_photo_message(chat_id, user_id, photo_token)
                            return JSONResponse({"ok": True})

            # Текст
            text = body.get("text", "").strip()
            if text:
                await handle_text(chat_id, user_id, text, first_name)

        elif update_type == "message_callback":
            callback = data.get("callback", {})
            chat_id = callback.get("chat_id") or data.get("chat_id")
            user = callback.get("user", {})
            user_id = user.get("user_id", 0)
            first_name = user.get("name", "")
            payload = callback.get("payload", "")
            if payload:
                await handle_callback(chat_id, user_id, payload, first_name)

        return JSONResponse({"ok": True})
    except Exception as e:
        logging.error(f"Webhook error: {e}")
        return JSONResponse({"ok": False, "error": str(e)})

@app.get("/health")
async def health():
    return {"status": "ok", "bot": "С верой MAX"}

# ========== ЗАПУСК ==========
if __name__ == "__main__":
    init_db()
    uvicorn.run(app, host="0.0.0.0", port=8080)
