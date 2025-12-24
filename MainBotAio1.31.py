import os
import logging
import sqlite3
import requests
import json
import html
from aiogram.enums import ParseMode
from aiogram.utils import markdown
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import FSInputFile
from dotenv import load_dotenv
import yt_dlp

load_dotenv()

logging.basicConfig(level=logging.INFO)

dev_contact_message = "Пожалуйста, отправьте описание вашей проблемы. Разработчик получит ваше сообщение."

MAX_TELEGRAM_FILE_SIZE = 2 * 1024 * 1024 * 1024  # 2ГБ
TELEGRAM_MAX_FILE_SIZE = 50 * 1024 * 1024  # 50МБ

TOKEN = os.getenv("TOKEN")
DEV_ID = os.getenv("DEV_ID")
bot = Bot(token=TOKEN)
dp = Dispatcher(storage=MemoryStorage())


class UserStates(StatesGroup):
    START = State()
    GET_URL = State()
    PROCESS = State()
    SELECT_QUALITY = State()
    SELECT_QUALITY_VK = State()
    SEARCH_VIDEO = State()
    SEARCH_YT = State()
    SELECT_YT_RESULT = State()
    CONTACT_DEV = State()
    COLLECT_URLS = State()


def init_db():
    conn = sqlite3.connect("../telegram_bot.db")
    cursor = conn.cursor()

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY,
            username TEXT,
            last_url TEXT,
            last_action TEXT,
            last_update DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            url TEXT,
            action TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS downloads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            file_path TEXT,
            file_type TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
    ''')
    conn.commit()
    conn.close()


def save_user(user_id, username, last_url=None, last_action=None):
    conn = sqlite3.connect("../telegram_bot.db")
    cursor = conn.cursor()

    cursor.execute('''
        INSERT INTO users (id, username, last_url, last_action)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            username=excluded.username,
            last_url=excluded.last_url,
            last_action=excluded.last_action,
            last_update=CURRENT_TIMESTAMP
    ''', (user_id, username, last_url, last_action))

    conn.commit()
    conn.close()


def log_action(user_id, url, action):
    conn = sqlite3.connect("../telegram_bot.db")
    cursor = conn.cursor()

    cursor.execute('''
        INSERT INTO logs (user_id, url, action)
        VALUES (?, ?, ?)
    ''', (user_id, url, action))

    conn.commit()
    conn.close()


def save_download(user_id, file_path, file_type):
    conn = sqlite3.connect("../telegram_bot.db")
    cursor = conn.cursor()

    cursor.execute('''
        INSERT INTO downloads (user_id, file_path, file_type)
        VALUES (?, ?, ?)
    ''', (user_id, file_path, file_type))

    conn.commit()
    conn.close()


@dp.message(Command("start", "начать", "дарова"))
async def start_command(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    username = message.from_user.username or "Unknown"
    save_user(user_id, username)
    await message.answer("Добро пожаловать! Выберите опцию:", reply_markup=main_menu_keyboard())
    await state.set_state(UserStates.START)


from aiogram import F


@dp.message(F.text.lower() == "привет питер")
async def easteregg1(message: types.Message):
    await message.reply("А может ты пидор ?")


@dp.message(F.text.lower() == "кеша")
async def easteregg2(message: types.Message):
    url = os.getenv("EASTER2")
    (await message.reply(text=f"{markdown.hide_link(url)}А, это наш тестер! 🤩",
                         parse_mode=ParseMode.HTML))


@dp.message(F.text.lower() == "nikisdead")
async def easteregg3(message: types.Message):
    url = os.getenv("EASTER1")
    (await message.reply(text=f"{markdown.hide_link(url)}О, а это главный разраб! ❤️",
                         parse_mode=ParseMode.HTML))


def main_menu_keyboard():
    buttons = [
        [KeyboardButton(text="Отправить ссылку 🔗")],
        [KeyboardButton(text="Отправить несколько ссылок 🔗🔗")],
        [KeyboardButton(text="Поиск видео 🔍")],
        [KeyboardButton(text="Написать разработчику 🛠")],
        [KeyboardButton(text="Отмена ❌")]
    ]
    keyboard = ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)
    return keyboard


def post_download_keyboard():
    buttons = [
        [KeyboardButton(text="Скачать ещё что-нибудь 📩")],
        [KeyboardButton(text="Искать другие видео 🔎")],
        [KeyboardButton(text="Отмена ❌")]
    ]
    keyboard = ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)
    return keyboard


def search_select_keyboard():
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="1", callback_data="search_1"),
            InlineKeyboardButton(text="2", callback_data="search_2"),
            InlineKeyboardButton(text="3", callback_data="search_3")
        ],
        [
            InlineKeyboardButton(text="4", callback_data="search_4"),
            InlineKeyboardButton(text="5", callback_data="search_5")
        ],
        [
            InlineKeyboardButton(text="Отмена ❌", callback_data="search_cancel")
        ]
    ])
    return keyboard


def detect_link_type(url):
    if "youtube.com" in url or "youtu.be" in url:
        return "YouTube"
    elif "vk.com/video" in url or "vk.com/clip" in url:
        return "VK_VIDEO_CLIP"
    elif "vk.com/story" in url:
        return "VK_STORY"
    elif "rutube.ru" in url:
        return "Rutube"
    elif "vt.tiktok.com" in url or "www.tiktok.com":
        return "TikTok"
    elif "Отмена ❌" in url:
        return "отмена ❌"
    return None


@dp.message(UserStates.COLLECT_URLS)
async def collect_urls_handler(message: types.Message, state: FSMContext):
    urls = [url.strip() for url in message.text.split(',') if url.strip()]
    if not urls:
        await message.answer(
            "Пожалуйста, отправьте ссылки, разделенные запятыми. Пример:\n⠀https://youtu.be/xyz, ⠀https://vk.com/video/12345⠀")
        return

    await state.update_data(url_queue=urls)
    await message.answer(f"Добавлено {len(urls)} ссылок в очередь. Начинаю обработку...")
    await process_next_url(message, state)


async def process_next_url(message: types.Message, state: FSMContext):
    data = await state.get_data()
    url_queue = data.get("url_queue", [])

    if not url_queue:
        await message.answer("Все ссылки успешно обработаны ✅.")
        await state.set_state(UserStates.START)
        return

    current_url = url_queue.pop(0)
    await state.update_data(url_queue=url_queue)

    link_type = detect_link_type(current_url)
    if not link_type:
        await message.answer(f"Ссылка `{current_url}` не поддерживается или некорректна ❌. Перехожу к следующей...")
        await process_next_url(message, state)
        return

    try:
        if link_type == "YouTube":
            file_path, title = await download_video_with_quality(current_url, {'format_id': 'best'},
                                                                 message.from_user.id)
            await send_file(message, file_path, title, file_type="video")
        elif link_type == "TikTok":
            file_path, title = await download_tiktok_video(current_url, message.from_user.id)
            await send_file(message, file_path, title, file_type="video")
        elif link_type == "VK_VIDEO_CLIP":
            file_path, title = await download_vk_content(current_url, message.from_user.id)
            await send_file(message, file_path, title, file_type="video")
        elif link_type == "VK_STORY":
            file_path, _ = await download_vk_history(current_url, message.from_user.id)
            await send_file(message, file_path, "VK Story", file_type="video")
        elif link_type == "Rutube":
            file_path, title = await download_rutube_video(current_url, message.from_user.id)
            await send_file(message, file_path, title, file_type="video")
        else:
            await message.answer(f"Тип ссылки `{current_url}` пока не поддерживается ❌.")
    except Exception as e:
        await message.answer(f"Ошибка при обработке `{current_url}`: {e}")

    await process_next_url(message, state)


@dp.message(UserStates.SEARCH_YT)
async def handle_search_query(message: types.Message, state: FSMContext):
    query = message.text.strip()
    if query == "Отмена ❌":
        await message.answer("Поиск отменён.", reply_markup=main_menu_keyboard())
        await state.set_state(UserStates.START)
        return

    if not query:
        await message.answer("Пожалуйста, введите текст для поиска 🔎")
        return

    await message.answer("Ищу видео... 🔍", reply_markup=main_menu_keyboard())

    try:
        results = await search_youtube_videos(query)
    except Exception as e:
        logging.error(f"Search failed: {e}")
        await message.answer("Ошибка поиска ⚠️. Попробуйте позже.")
        await state.set_state(UserStates.START)
        return

    if not results:
        await message.answer("Ничего не найдено. Попробуйте другой запрос 😔")
        await state.set_state(UserStates.START)
        return

    await state.update_data(search_results=results)

    response = ["🔍 Найденные видео:\n\n"]
    for idx, result in enumerate(results, 1):
        title = html.escape(result['title'])
        response.append(
            f"{idx}. <a href='{result['url']}'>{title}</a>\n"
            f"👁 {result.get('view_count', '?')} просмотров | "
            f"⏳ {result.get('duration', '?')} сек.\n"
        )

    response.append("\nВыберите номер видео (1 - 5) для загрузки:")

    await message.answer(
        "\n".join(response),
        disable_web_page_preview=True,
        parse_mode='HTML',
        reply_markup=search_select_keyboard()
    )
    await state.set_state(UserStates.SELECT_YT_RESULT)


@dp.callback_query(UserStates.SELECT_YT_RESULT, F.data.startswith("search_"))
async def handle_search_selection_callback(callback: types.CallbackQuery, state: FSMContext):
    """Обработка выбора результата поиска через inline-кнопки"""
    user_input = callback.data
    await callback.answer()

    if user_input == "search_cancel":
        await callback.message.answer("Выбор отменён.", reply_markup=main_menu_keyboard())
        await state.set_state(UserStates.START)
        return

    try:
        index = int(user_input.split("_")[1]) - 1
    except (ValueError, IndexError):
        await callback.message.answer("Ошибка выбора ⚠️")
        return

    data = await state.get_data()
    results = data.get("search_results", [])

    if index >= len(results):
        await callback.message.answer("Неверный номер результата ❌")
        return

    selected_url = results[index]['url']

    await callback.message.edit_reply_markup(reply_markup=None)

    await process_url_handler(callback.message, state, url=selected_url)


@dp.message(UserStates.GET_URL)
async def process_url_handler(message: types.Message, state: FSMContext, url: str = None):
    if not url:
        url = message.text.strip()
    user_id = message.from_user.id
    username = message.from_user.username or "Unknown"

    save_user(user_id, username, last_url=url, last_action="Получен URL")
    log_action(user_id, url=url, action="Получен URL")

    link_type = detect_link_type(url)
    logging.info(f"Получена ссылка: {url}")
    logging.info(f"Распознан тип ссылки: {link_type}")

    metadata = await get_video_metadata(url)
    response_text = (
        f"Видео 🎦: {metadata['title']}\n"
        f"Автор 👤: {metadata['uploader']}\n"
        f"Просмотры 👁️: {metadata['views']}\n"
        f"Лайки 👍: {metadata['likes']}\n\n"
        f"Выберите действие:"
    )

    if link_type == "YouTube":
        keyboard = ReplyKeyboardMarkup(
            keyboard=[
                [types.KeyboardButton(text="Скачать видео 🎥")],
                [types.KeyboardButton(text="Скачать аудио 🎵")],
                [types.KeyboardButton(text="Назад ◀️")]
            ],
            resize_keyboard=True
        )
        await message.answer(response_text, reply_markup=keyboard)
        await state.update_data(url=url, link_type="YouTube")
        await state.set_state(UserStates.PROCESS)
    elif link_type == "VK_VIDEO_CLIP":
        keyboard = ReplyKeyboardMarkup(
            keyboard=[
                [types.KeyboardButton(text="Скачать VK Видео/Клип 🎥")],
                [types.KeyboardButton(text="Назад ◀️")]
            ],
            resize_keyboard=True
        )
        await message.answer(response_text, reply_markup=keyboard)
        await state.update_data(url=url, link_type="VK_VIDEO_CLIP")
        await state.set_state(UserStates.PROCESS)
    elif link_type == "VK_STORY":
        keyboard = ReplyKeyboardMarkup(
            keyboard=[
                [types.KeyboardButton(text="Скачать VK Историю 🎥")],
                [types.KeyboardButton(text="Назад ◀️")]
            ],
            resize_keyboard=True
        )
        await message.answer("Выберите действие:", reply_markup=keyboard)
        await state.update_data(url=url, link_type="VK_STORY")
        await state.set_state(UserStates.PROCESS)
    elif link_type == "отмена ❌":
        await message.answer("До скорых встреч! ❤️", reply_markup=types.ReplyKeyboardRemove())
        await state.clear()
    elif link_type == "Rutube":
        keyboard = ReplyKeyboardMarkup(
            keyboard=[
                [types.KeyboardButton(text="Скачать видео с Rutube 📺")],
                [types.KeyboardButton(text="Назад ◀️")]
            ],
            resize_keyboard=True
        )
        await message.answer(response_text, reply_markup=keyboard)
        await state.update_data(url=url, link_type="Rutube")
        await state.set_state(UserStates.PROCESS)
    elif link_type == "TikTok":
        keyboard = ReplyKeyboardMarkup(
            keyboard=[
                [types.KeyboardButton(text="Скачать TikTok видео 📱")],
                [types.KeyboardButton(text="Назад ◀️")]
            ],
            resize_keyboard=True
        )
        await message.answer(response_text, reply_markup=keyboard)
        await state.update_data(url=url, link_type="TikTok")
        await state.set_state(UserStates.PROCESS)
    else:
        await message.answer("Неподдерживаемый тип ссылки ❌. Пожалуйста, отправьте другую ссылку.")
        await state.set_state(UserStates.GET_URL)


@dp.message(UserStates.START)
async def handle_text(message: types.Message, state: FSMContext):
    text = message.text.lower()

    if text == "отправить ссылку 🔗":
        await message.answer("Пожалуйста, отправьте ссылку YouTube, TikTok, VK или Rutube 🔗:")
        await state.set_state(UserStates.GET_URL)
    elif text == "отправить несколько ссылок 🔗🔗":
        await message.answer(
            "Отправьте ссылки через запятую. \n\nПример:\nhttps://youtu.be/xyz, https://rutube.ru/video/idk")
        await state.set_state(UserStates.COLLECT_URLS)
    elif text == "скачать ещё что-нибудь 📩":
        await message.answer("Пожалуйста, отправьте новую ссылку 🔗")
        await state.set_state(UserStates.GET_URL)
    elif text == "искать другие видео 🔎":
        await message.answer("Пожалуйста, введите текст для поиска 🔎")
        await state.set_state(UserStates.SEARCH_YT)
    elif message.text == "Написать разработчику 🛠":
        await message.answer(dev_contact_message)
        await state.set_state(UserStates.CONTACT_DEV)
    elif text == "поиск видео 🔍":
        await message.answer("Введите поисковый запрос:")
        await state.set_state(UserStates.SEARCH_YT)
    elif text == "отмена ❌":
        await message.answer("До скорых встреч! ❤️", reply_markup=types.ReplyKeyboardRemove())
        await state.clear()
    else:
        await message.answer("Выберите доступную опцию 💾:", reply_markup=main_menu_keyboard())


@dp.message(UserStates.CONTACT_DEV)
async def contact_dev_handler(message: types.Message, state: FSMContext):
    if message.text == "Отмена ❌":
        await message.answer("Обращение отменено.", reply_markup=main_menu_keyboard())
        await state.set_state(UserStates.START)
    else:
        try:
            await bot.send_message(DEV_ID,
                                   f"Сообщение от {message.from_user.username or message.from_user.id}:\n{message.text}")
            await message.answer("Ваше сообщение успешно отправлено разработчику. ✅", reply_markup=main_menu_keyboard())
            await state.set_state(UserStates.START)
        except Exception as e:
            logging.error(f"Ошибка отправки сообщения разработчику: {e}")
            await message.answer("Не удалось отправить сообщение разработчику. Попробуйте позже.",
                                 reply_markup=main_menu_keyboard())
            await state.set_state(UserStates.START)


@dp.message(UserStates.GET_URL)
async def get_url_handler(message: types.Message, state: FSMContext):
    await message.answer("Пожалуйста, отправьте ссылку YouTube или VK URL:")
    await state.set_state(UserStates.GET_URL)


@dp.message(UserStates.PROCESS)
async def handle_action_selection(message: types.Message, state: FSMContext):
    action = message.text.strip().lower()
    data = await state.get_data()
    url = data.get("url")
    link_type = data.get("link_type")

    if action == "скачать видео 🎥" and link_type == "YouTube":
        formats = await get_available_formats(url)
        if not formats:
            await message.answer("Не удалось получить доступные качества для видео 😭. Попробуйте снова.")
            await state.set_state(UserStates.START)
            return
        keyboard = ReplyKeyboardMarkup(
            keyboard=[[types.KeyboardButton(text=f"{f['resolution']} - {f['ext']}")] for f in formats] +
                     [[types.KeyboardButton(text="Назад ◀️")]],
            resize_keyboard=True
        )
        if action == "Назад ◀️":
            await message.answer("Возврат в главное меню ◀️.", reply_markup=keyboard)
            await state.set_state(UserStates.START)
        await message.answer("Выберите качество видео 📼:", reply_markup=keyboard)
        await state.update_data(formats=formats)
        await state.set_state(UserStates.SELECT_QUALITY)


    elif action == "скачать аудио 🎵" and link_type == "YouTube":
        file_path, title = await download_audio(url, message.from_user.id)
        await send_file(message, file_path, title, file_type="audio")
        await message.answer("Загрузка завершена ✅ Что дальше?",
                             reply_markup=post_download_keyboard())
        await state.set_state(UserStates.START)

    elif action == "скачать vk видео/клип 🎥" and link_type == "VK_VIDEO_CLIP":
        file_path, title = await download_vk_content(url, message.from_user.id)
        await send_file(message, file_path, title, file_type="video")
        await message.answer("Загрузка завершена ✅ Что дальше?", reply_markup=post_download_keyboard())
        await state.set_state(UserStates.START)

    elif action == "скачать vk историю 🎥" and link_type == "VK_STORY":
        file_path, _ = await download_vk_history(url, message.from_user.id)
        await send_file(message, file_path, "VK: " + url, file_type="video")
        await message.answer("Загрузка завершена ✅ Что дальше?", reply_markup=post_download_keyboard())
        await state.set_state(UserStates.START)

    elif action == "скачать vk аудио" and link_type == "VK_AUDIO":
        file_path, title = '', ''
        await send_file(message, file_path, "VK Music", file_type="audio")
        await message.answer("Загрузка завершена ✅ Что дальше?", reply_markup=post_download_keyboard())
        await state.set_state(UserStates.START)

    elif action == "скачать видео с rutube 📺" and link_type == "Rutube":
        await message.answer("Видео загружается...")
        file_path, title = await download_rutube_video(url, message.from_user.id)
        await send_file(message, file_path, title, file_type="video")
        await message.answer("Загрузка завершена ✅ Что дальше?", reply_markup=post_download_keyboard())
        await state.set_state(UserStates.START)

    elif action == "скачать tiktok видео 📱" and link_type == "TikTok":
        file_path, title = await download_tiktok_video(url, message.from_user.id)
        await send_file(message, file_path, title, file_type="video")
        await message.answer("Загрузка завершена ✅ Что дальше?", reply_markup=post_download_keyboard())
        await state.set_state(UserStates.START)

    elif action == "назад ◀️":
        await message.answer("Возврат в главное меню ◀️️.", reply_markup=main_menu_keyboard())
        await state.set_state(UserStates.START)
    else:
        await message.answer("Неподдерживаемое действие ❌. Попробуйте снова.")


async def download_tiktok_video(url, user_id):
    """
    Загружает видео с TikTok с использованием yt-dlp.

    :param url: Ссылка на видео TikTok.
    :param user_id: ID пользователя.
    :return: Путь к видеофайлу и название видео.
    """
    ydl_opts = {
        'format': 'best',
        'outtmpl': f'{user_id}_tiktok.%(ext)s',
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        file_path = f"{user_id}_tiktok.{info['ext']}"
        title = info.get("title", "TikTok")
        save_download(user_id, file_path, 'video')
        return file_path, title


async def download_rutube_video(url, user_id):
    """
    Загружает видео с Rutube с использованием yt-dlp.

    :param url: Ссылка на видео.
    :param user_id: ID пользователя.
    :return: Путь к видеофайлу и название видео.
    """
    ydl_opts = {
        'format': 'best',
        'outtmpl': f'{user_id}_rutube.%(ext)s',
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        file_path = f"{user_id}_rutube.{info['ext']}"
        title = info.get("title", "Rutube")
        save_download(user_id, file_path, 'video')
        return file_path, title


@dp.message(UserStates.SELECT_QUALITY)
async def handle_quality_selection(message: types.Message, state: FSMContext):
    selection = message.text.strip()
    data = await state.get_data()
    formats = data.get("formats")

    selected_format = next((f for f in formats if f"{f['resolution']} - {f['ext']}" == selection), None)
    if selected_format:
        await message.answer(
            f"Вы выбрали качество: {selected_format['resolution']} {selected_format['ext']}. Видео загружается...")
        file_path, title = await download_video_with_quality(data.get("url"), selected_format, message.from_user.id)
        await send_file(message, file_path, title, file_type="video")
        await message.answer("Загрузка завершена ✅ Что дальше?",
                             reply_markup=post_download_keyboard())
        await state.set_state(UserStates.START)
    else:
        await message.answer("Неверный выбор ❌. Попробуйте снова.")


async def handle_quality_selection_vk(message: types.Message, state: FSMContext):
    selection = message.text.strip()
    data = await state.get_data()
    available_qualities = data.get("available_qualities")
    url = data.get("url")

    if selection in available_qualities:
        await message.answer(f"Вы выбрали качество {selection}. Загрузка началась...")
        file_path, _ = await download_vk_history(url, message.from_user.id, quality=selection)
        await send_file(message, file_path, "VK Story", file_type="video")
        await state.set_state(UserStates.START)
    else:
        await message.answer("Неверный выбор ❌. Попробуйте снова.")


async def download_video_with_quality(url, selected_format, user_id):
    ydl_opts = {
        'format': selected_format['format_id'],
        'outtmpl': f'{user_id}_video.%(ext)s',
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        file_path = f"{user_id}_video.{selected_format['ext']}"
        title = info.get('title', 'Untitled')
        save_download(user_id, file_path, 'video')
        return file_path, title


async def get_video_metadata(url):
    """
    Извлекает метаданные видео, такие как количество просмотров и лайков.

    :param url: Ссылка на видео.
    :return: Словарь с метаданными.
    """
    ydl_opts = {'quiet': True, 'skip_download': True}
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            info = ydl.extract_info(url, download=False)
            return {
                "title": info.get("title", "Без названия"),
                "views": info.get("view_count", "Нет данных"),
                "likes": info.get("like_count", "Нет данных"),
                "uploader": info.get("uploader", "Неизвестный")
            }
        except Exception as e:
            logging.error(f"Ошибка извлечения метаданных: {e}")
            return {"title": "Не удалось получить данные", "views": "-", "likes": "-", "uploader": "-"}


async def get_available_formats(url):
    ydl_opts = {'listformats': True}
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)
        formats = [f for f in info.get('formats', []) if f.get('acodec') != 'none' and f.get('vcodec') != 'none']
        return [{'format_id': f['format_id'], 'resolution': f.get('resolution', 'audio'), 'ext': f['ext']} for f in
                formats]


async def download_audio(url, user_id):
    ydl_opts = {
        'format': 'bestaudio/best',
        'postprocessors': [{'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3', 'preferredquality': '192'}],
        'outtmpl': f'{user_id}_audio.%(ext)s',
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        file_path = f"{user_id}_audio.mp3"
        title = info.get('title', 'Untitled')
        save_download(user_id, file_path, 'audio')
        return file_path, title


async def download_vk_content(url, user_id):
    ydl_opts = {
        'format': 'best',
        'outtmpl': f'{user_id}_vk.%(ext)s',
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/94.0.4606.61 Safari/537.36',
            'Accept-Language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7',
            'Referer': 'https://vk.com/',
        },
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            info = ydl.extract_info(url, download=True)
            file_path = f"{user_id}_vk.{info['ext']}"
            title = info.get("title", "VK Content")
            return file_path, title
        except Exception as e:
            raise ValueError(f"Ошибка загрузки: {e}")


async def search_youtube_videos(query: str, max_results=5):
    ydl_opts = {
        'quiet': True,
        'skip_download': True,
        'extract_flat': 'in_playlist',
        'default_search': f'ytsearch{max_results}',
        'force_generic_extractor': True,
        'verbose': True
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            result = ydl.extract_info(
                f'ytsearch{max_results}:{query}',
                download=False
            )

            with open('../search_debug.json', 'w', encoding='utf-8', errors='ignore') as f:
                json.dump(result, f, indent=2, ensure_ascii=False)

            if not result or 'entries' not in result:
                logging.error("No entries in search result")
                return []

            videos = []
            for entry in result['entries']:
                if entry:
                    videos.append({
                        'title': entry.get('title', 'Без названия'),
                        'url': entry.get('url'),
                        'duration': entry.get('duration'),
                        'view_count': entry.get('view_count')
                    })

            return videos[:max_results]

    except Exception as e:
        logging.error(f"Search error: {str(e)}", exc_info=True)
        return []


async def get_available_formats1(url):
    ydl_opts = {'listformats': True}
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)
        formats = [f for f in info.get('formats', []) if f.get('acodec') != 'none' and f.get('vcodec') != 'none']
        return [{'format_id': f['format_id'], 'resolution': f.get('resolution', 'audio'), 'ext': f['ext']} for f in
                formats]

@dp.message(UserStates.SELECT_QUALITY_VK)
async def download_vk_history(url, user_id, quality='720'):
    story_id = url.split('story')[1]
    params = {'v': "5.199"}
    url_api = "https://api.vk.com/method/stories.getById"
    data = {"access_token": os.getenv("ACCESS_TOKEN"), 'stories': story_id}
    res = requests.post(url_api, params=params, data=data)

    available_qualities = {}
    req_data = res.json()['response']['items'][0]

    for key in req_data['video']['files']:
        if 'mp4' in key:
            available_qualities[key.split('_')[1]] = req_data['video']['files'][key]

    if '720' in available_qualities:
        selected_quality_url = available_qualities['720']
    else:
        selected_quality_url = available_qualities.get('480')

    res = requests.get(selected_quality_url)
    file_path = f"{user_id}_vk_story.mp4"
    with open(file_path, 'wb') as f:
        f.write(res.content)

    return file_path, available_qualities


# Функция отправки файла
async def send_file(message: types.Message, file_path: str, title: str, file_type: str):
    """
    Отправляет файл пользователю в зависимости от типа файла.

    :param message: Сообщение от пользователя
    :param file_path: Путь к файлу
    :param title: Название файла
    :param file_type: Тип файла ('audio' или 'video')
    """
    # Проверяем, существует ли файл
    if not os.path.exists(file_path):
        await message.answer("Файл не найден 🗑️. Попробуйте снова.")
        return

    file = FSInputFile(file_path)

    try:
        if file_type == "audio":
            await message.answer_audio(audio=file, caption=title)
        elif file_type == "video":
            await message.answer_video(video=file, caption=title)
        else:
            raise ValueError("Неподдерживаемый тип файла ❌")
    except Exception as e:
        await message.answer(f"Ошибка при отправке файла: {e}")
    finally:
        if os.path.exists(file_path):
            os.remove(file_path)


async def main():
    init_db()
    await dp.start_polling(bot)


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())