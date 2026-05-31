import asyncio
import sqlite3
import logging
import os
import base64
import httpx
import uuid
from datetime import datetime, date
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from openai import AsyncOpenAI
import anthropic
import uvicorn

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

async def get_photo_bytes(photo_token):
    headers = {"Authorization": MAX_TOKEN}
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(f"{MAX_API}/photos/{photo_token}", headers=headers)
            return r.content
    except Exception as e:
        logging.error(f"Ошибка get_photo: {e}")
        return None

async def register_webhook():
    result = await max_request("POST", "subscriptions", {"url": WEBHOOK_URL})
    logging.info(f"Webhook регистрация: {result}")

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
        [btn("👤 Мой профиль", "profile"), btn("❓ Задать вопрос", "ask_question")],
        [btn("🕯️ Пожертвование на развитие", "donate")],
        [btn("💬 Отзыв или пожелание", "review")],
    ]

def back_main():
    return [[btn("◀️ Главное меню", "main_menu")]]

def prayers_buttons():
    return [
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
}

FASTS = {
    "Великий пост": "48 дней перед Пасхой. Самый строгий пост.",
    "Петров пост": "С понедельника после Недели всех святых до 12 июля.",
    "Успенский пост": "14–27 августа. Строгий пост.",
    "Рождественский пост": "28 ноября – 6 января.",
    "Среда и пятница": "Еженедельный пост в память предательства и распятия Христа.",
}

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
    cols = ["user_id","username","first_name","step","church_name","birth_date","angel_day","onboarded"]
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

# ========== AI ==========
async def ask_claude(question, depth="medium"):
    depths = {
        "short":  ("Отвечай кратко — 2-3 предложения.", 300),
        "medium": ("Отвечай развёрнуто — 5-7 предложений.", 600),
        "deep":   ("Дай глубокий богословский ответ.", 1200),
    }
    system_add, max_tok = depths.get(depth, depths["medium"])
    try:
        msg = claude_client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=max_tok,
            system="Ты православный помощник — отвечаешь тепло, доступно и без осуждения. " + system_add,
            messages=[{"role": "user", "content": question}]
        )
        return msg.content[0].text
    except Exception as e:
        logging.error(f"Ошибка Claude: {e}")
        return "Не удалось получить ответ. Попробуйте позже."

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
        "☦️ Исповедь — разговор с Богом в присутствии священника.\n\n"
        "📅 ПУТЬ ПОДГОТОВКИ:\n\n"
        "За 3 дня:\n— Воздержитесь от мяса, рыбы, молочного\n— Читайте утренние и вечерние молитвы\n— Начните вспоминать грехи — запишите на бумагу\n\n"
        "За 1 день:\n— Прочитайте Канон покаянный\n\n"
        "Утром:\n— Утренние молитвы\n— Постарайтесь не есть до исповеди\n\n"
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
        "Для крёстных — за 3 дня:\n— Пост\n— Утренние и вечерние молитвы\n— Выучите Символ Веры и Отче наш\n\nНакануне:\n— Крёстные проходят Исповедь и Причастие\n\n"
        "Что взять:\n— Крестильная рубашка\n— Нательный крестик\n— Крыжма (белое полотенце)\n\n"
        "Достаточно одного крёстного:\nДля мальчика — крёстный отец\nДля девочки — крёстная мать\n\n"
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
        "☦️ Афон — монашеский удел Богородицы.\nПолуостров в Греции — особое место молитвы.\n\n"
        "Главные монастыри:\n— Великая Лавра (основана 963 г.)\n— Ватопед — один из крупнейших\n— Хиландар — сербский монастырь\n— Пантелеимонов — русский монастырь\n\n"
        "Важно знать:\n— Женщинам въезд запрещён\n— Мужчинам нужна диамонитирион (разрешение)\n— Получают заранее через консульство Греции\n\n"
        "Как добраться:\nАфины → Салоники → Уранополис → паром на Афон"),
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
async def handle_start(chat_id, user_id, first_name, username):
    user = get_user(user_id, username, first_name)
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
            "Чем могу помочь? ☦️",
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
    if payload == "main_menu":
        await send_message(chat_id, "☦️ Главное меню:", main_menu_buttons())

    elif payload == "prayers":
        await send_message(chat_id, "🙏 Выберите молитву:", prayers_buttons())

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
        today_str = datetime.now().strftime("%d %B %Y")
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
        today_str = datetime.now().strftime("%d %B")
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
        today_str = datetime.now().strftime("%d %B")
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
        await send_message(chat_id,
            f"👤 Мой профиль\n\n✏️ Имя: {church}\n🎂 Дата рождения: {birth}\n👼 День ангела: {angel}",
            [
                [btn("✏️ Изменить имя", "profile_edit_name")],
                [btn("🎂 Изменить дату рождения", "profile_edit_birth")],
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
                model="claude-sonnet-4-20250514",
                max_tokens=600,
                system="Напиши краткую православную молитву святому. 8-12 строк. Начни с обращения. Закончи Аминь.",
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

    if step == "review":
        set_step(user_id, "idle")
        await send_message(chat_id,
            "☦️ Спасибо за ваш отзыв!\n\nМы учтём его при развитии проекта.\nДа хранит вас Господь 🕊️",
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
                "confirmation": {"type": "redirect", "return_url": "https://sveroy.ru"},
                "capture": True,
                "description": "Пожертвование на развитие «С верой» во славу Божию",
            }, str(uuid.uuid4()))
            set_step(user_id, "idle")
            await send_message(chat_id, f"🕯️ Пожертвование {amount} рублей\n\nНажмите для оплаты 👇", [
                [link_btn("💳 Перейти к оплате", payment.confirmation.confirmation_url)],
                back_main()[0],
            ])
        except ValueError:
            await send_message(chat_id, "⚠️ Введите сумму цифрой, например: 300")
        except Exception as e:
            logging.error(f"Ошибка платежа: {e}")
            await send_message(chat_id, "⚠️ Ошибка платежа. Попробуйте позже.", back_main())
        return

    if step and step.startswith("question_"):
        depth = step.replace("question_", "")
        await send_message(chat_id, "🙏 Отвечаю...")
        answer = await ask_claude(text, depth)
        set_step(user_id, "idle")
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

# ========== FASTAPI ==========
app = FastAPI()

@app.on_event("startup")
async def startup():
    init_db()
    await register_webhook()
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
                    token = att.get("payload", {}).get("token", "")
                    if token:
                        user = get_user(user_id)
                        if user.get("step") in ("photo_church", "photo_icon"):
                            await handle_photo(chat_id, user_id, token)
                            return JSONResponse({"ok": True})

            text = body.get("text", "").strip()
            if text:
                await handle_text(chat_id, user_id, text, first_name)

        elif update_type == "message_callback":
            cb = data.get("callback", {})
            chat_id = cb.get("chat_id") or data.get("chat_id")
            user = cb.get("user", {})
            user_id = user.get("user_id", 0)
            first_name = user.get("name", "")
            payload = cb.get("payload", "")
            if payload:
                await handle_callback(chat_id, user_id, payload, first_name)

        return JSONResponse({"ok": True})
    except Exception as e:
        logging.error(f"Webhook error: {e}")
        return JSONResponse({"ok": False})

@app.get("/health")
async def health():
    return {"status": "ok", "bot": "С верой MAX"}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8080)
