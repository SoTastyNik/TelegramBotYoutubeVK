import os
import logging
import sqlite3
import requests
import html
import asyncio
import yt_dlp
from aiogram.enums import ParseMode
from aiogram.utils import markdown
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile, \
    CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from dotenv import load_dotenv
from vkpymusic import Service






load_dotenv()

# Настройка логирования
logging.basicConfig(level=logging.INFO)

# dev-сообщение
dev_contact_message = "Пожалуйста, отправьте описание вашей проблемы. Разработчик получит ваше сообщение."

# Константы
MAX_TELEGRAM_FILE_SIZE = 2 * 1024 * 1024 * 1024  # 2ГБ
TELEGRAM_MAX_FILE_SIZE = 50 * 1024 * 1024  # 50МБ

# Инициализация бота
TOKEN = os.getenv("TOKEN")
DEV_ID = os.getenv("DEV_ID")
VK_USER_LOGIN = os.getenv("VK_USER_LOGIN")
VK_USER_PASSWORD = os.getenv("VK_USER_PASSWORD")

bot = Bot(token=TOKEN)
dp = Dispatcher(storage=MemoryStorage())


# --- КЛАСС ДЛЯ РАБОТЫ С VK ---
class VkMusicHelper:
    def __init__(self):
        self.service = None
        self.token = os.getenv("ACCESS_TOKEN_MUSIC")  # Убедись, что в .env есть этот ключ
        # User-Agent для скачивания, чтобы VK не отдавал заглушку
        self.user_agent = "KateMobileAndroid/56 lite-armeabi-v7a (Android 4.4.2; SDK 19; armeabi-v7a; unknown unknown; ru)"

    def authenticate(self):
        """Инициализация сервиса vkpymusic"""
        if not self.token:
            logging.error("❌ VK_ACCESS_TOKEN не найден в .env")
            return False

        try:
            # Инициализируем сервис, используя токен
            # client=None, так как мы используем готовый токен
            self.service = Service(user_agent=self.user_agent, token=self.token)
            logging.info("✅ Сервис vkpymusic успешно инициализирован")
            return True
        except Exception as e:
            logging.error(f"❌ Ошибка инициализации vkpymusic: {e}")
            return False

    def search_tracks(self, query, limit=5):
        """Поиск треков"""
        if not self.service:
            if not self.authenticate():
                return []

        try:
            # vkpymusic имеет удобный метод для поиска по тексту
            # count=limit ограничивает количество
            songs = self.service.search_songs_by_text(query, count=limit)

            if not songs:
                logging.info("Поиск vkpymusic не дал результатов.")
                return []

            tracks = []
            for song in songs:
                # Библиотека возвращает объекты класса Song, конвертируем их в словарь для бота
                tracks.append({
                    'artist': song.artist,
                    'title': song.title,
                    'url': song.url,
                    'duration': song.duration
                })
            return tracks
        except Exception as e:
            logging.error(f"Ошибка поиска через vkpymusic: {e}")
            return []

    async def download_track(self, url, filename):
        """Скачивание файла трека"""
        try:
            # Запускаем синхронное скачивание в отдельном потоке, чтобы не блокировать бота
            loop = asyncio.get_event_loop()
            success = await loop.run_in_executor(None, self._download_sync, url, filename)
            return filename if success else None
        except Exception as e:
            logging.error(f"Ошибка при асинхронном запуске скачивания: {e}")
            return None

    def _download_sync(self, url, filename):
        """Синхронная функция скачивания с правильными заголовками"""
        try:
            # ОЧЕНЬ ВАЖНО: передаем User-Agent при скачивании файла.
            # Иначе VK видит, что качает скрипт, и отдает mp3-заглушку.
            headers = {
                'User-Agent': self.user_agent
            }

            response = requests.get(url, headers=headers, stream=True, timeout=30)

            if response.status_code == 200:
                with open(filename, 'wb') as f:
                    for chunk in response.iter_content(chunk_size=1024 * 64):
                        if chunk:
                            f.write(chunk)

                # Проверка: если файл слишком маленький (менее 10кб), скорее всего это ошибка или заглушка
                if os.path.getsize(filename) < 10240:
                    logging.warning("Скачанный файл слишком маленький, возможно это заглушка.")
                    # Можно удалить файл, если он битый, но пока оставим для диагностики

                return True
            else:
                logging.error(f"Ошибка скачивания VK. Status code: {response.status_code}")
                return False
        except Exception as e:
            logging.error(f"Ошибка записи файла: {e}")
            return False


# Инициализация хелпера
vk_helper = VkMusicHelper()


# Состояния для FSM
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
    SEARCH_VK_MUSIC = State()


# Инициализация базы данных
def init_db():
    conn = sqlite3.connect("../telegram_bot.db")
    cursor = conn.cursor()

    # Таблица для пользователей
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY,
            username TEXT,
            last_url TEXT,
            last_action TEXT,
            last_update DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # Таблица для логов
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

    # Таблица для скачанных файлов
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


# Функция добавления пользователя в БД
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

def get_music_page(tracks, page=0, per_page=5):
    """
    Генерирует текст и клавиатуру для определенной страницы результатов
    :param tracks: Список всех найденных треков
    :param page: Номер текущей страницы (начинается с 0)
    :param per_page: Количество треков на одной странице
    """
    max_pages = (len(tracks) - 1) // per_page + 1

    # Защита от выхода за пределы
    if page < 0: page = 0
    if page >= max_pages: page = max_pages - 1

    start_index = page * per_page
    end_index = start_index + per_page
    current_tracks = tracks[start_index:end_index]

    # Формируем текст сообщения
    response_text = f"🎶 **Результаты поиска (Стр. {page + 1}/{max_pages}):**\n\n"

    keyboard_buttons = []

    for i, track in enumerate(current_tracks):
        # Абсолютный индекс трека в общем списке (нужен для скачивания)
        abs_index = start_index + i

        # Красивое время
        dur = track.get('duration', 0)
        m, s = divmod(dur, 60)
        time_str = f"{m}:{s:02d}"

        response_text += f"**{abs_index + 1}.** {track['artist']} - {track['title']} ({time_str})\n"

        # Кнопка для скачивания конкретного трека
        # callback_data хранит индекс трека в общем списке
        keyboard_buttons.append([
            InlineKeyboardButton(text=f"📥 Скачать {abs_index + 1}", callback_data=f"music_dl_{abs_index}")
        ])

    # Кнопки навигации (Назад / Стр / Вперед)
    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton(text="⬅️", callback_data=f"music_page_{page - 1}"))

    nav_row.append(InlineKeyboardButton(text=f"📄 {page + 1}/{max_pages}", callback_data="ignore"))

    if page < max_pages - 1:
        nav_row.append(InlineKeyboardButton(text="➡️", callback_data=f"music_page_{page + 1}"))

    keyboard_buttons.append(nav_row)
    keyboard_buttons.append([InlineKeyboardButton(text="Отмена ❌", callback_data="music_cancel")])

    return response_text, InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)

# Хэндлер старта
@dp.message(Command("start", "начать", "дарова"))
async def start_command(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    username = message.from_user.username or "Unknown"
    save_user(user_id, username)
    await message.answer("Добро пожаловать! Выберите опцию:", reply_markup=main_menu_keyboard())
    await state.set_state(UserStates.START)


@dp.message(F.text.lower() == "привет питер")
async def easteregg1(message: types.Message):
    await message.reply("А может ты пидор ?")


@dp.message(F.text.lower() == "кеша")
async def easteregg2(message: types.Message):
    url = os.getenv("EASTER2")
    if url:
        (await message.reply(text=f"{markdown.hide_link(url)}А, это наш тестер! 🤩",
                             parse_mode=ParseMode.HTML))
    else:
        await message.reply("Кеша тут, но пасхалка не настроена.")


@dp.message(F.text.lower() == "nikisdead")
async def easteregg3(message: types.Message):
    url = os.getenv("EASTER1")
    if url:
        (await message.reply(text=f"{markdown.hide_link(url)}О, а это главный разраб! ❤️",
                             parse_mode=ParseMode.HTML))
    else:
        await message.reply("Разраб на месте.")


# Клавиатуры
def main_menu_keyboard():
    # Создаем кнопки
    buttons = [
        [KeyboardButton(text="Отправить ссылку 🔗")],
        [KeyboardButton(text="Поиск музыки VK 🎧"), KeyboardButton(text="Поиск видео 🔍")],
        [KeyboardButton(text="Отправить несколько ссылок 🔗🔗")],
        [KeyboardButton(text="Написать разработчику 🛠")],
        [KeyboardButton(text="Отмена ❌")]
    ]
    # Передаем кнопки в параметр keyboard
    keyboard = ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)
    return keyboard


def post_download_keyboard():
    # Минимальная клавиатура после загрузки
    buttons = [
        [KeyboardButton(text="Скачать ещё что-нибудь 📩")],
        [KeyboardButton(text="Искать другие видео 🔎")],
        [KeyboardButton(text="Отмена ❌")]
    ]
    keyboard = ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)
    return keyboard


def search_select_keyboard():
    # Создаем inline-кнопки
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


# Определение типа ссылки
def detect_link_type(url):
    if "youtube.com" in url or "youtu.be" in url:
        return "YouTube"
    elif "vk.com/video" in url or "vk.com/clip" in url:
        return "VK_VIDEO_CLIP"
    elif "vk.com/story" in url:
        return "VK_STORY"
    elif "rutube.ru" in url:
        return "Rutube"
    elif "vt.tiktok.com" in url or "tiktok.com" in url:
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

    # Берем первую ссылку из очереди
    current_url = url_queue.pop(0)
    await state.update_data(url_queue=url_queue)

    # Распознаем тип ссылки
    link_type = detect_link_type(current_url)
    if not link_type:
        await message.answer(f"Ссылка `{current_url}` не поддерживается или некорректна ❌. Перехожу к следующей...")
        await process_next_url(message, state)
        return

    try:
        # Скачиваем и отправляем контент в зависимости от типа ссылки
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

    # Переходим к следующей ссылке
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


# --- ОБРАБОТЧИКИ VK MUSIC ---

@dp.message(UserStates.SEARCH_VK_MUSIC)
async def process_vk_music_search(message: types.Message, state: FSMContext):
    query = message.text.strip()
    if query == "Отмена ❌":
        await message.answer("Поиск отменён", reply_markup=main_menu_keyboard())
        await state.set_state(UserStates.START)
        return

    await message.answer(f"🔎 Ищу в VK: {query}...")

    # 🔥 ЗАПРАШИВАЕМ 50 ТРЕКОВ
    tracks = vk_helper.search_tracks(query, limit=50)

    if not tracks:
        await message.answer("Ничего не найдено 😔.\nПопробуйте другой запрос.")
        # Не сбрасываем состояние, даем возможность ввести другой запрос
        return

    # Сохраняем результаты и текущую страницу (0) в FSM
    await state.update_data(vk_tracks=tracks, current_page=0)

    # Генерируем первую страницу
    text, kb = get_music_page(tracks, page=0)

    await message.answer(text, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)


@dp.callback_query(F.data.startswith("music_"))
async def handle_music_callback(callback: CallbackQuery, state: FSMContext):
    data = callback.data

    # --- 1. ОТМЕНА ---
    if data == "music_cancel":
        await callback.message.delete()
        await callback.message.answer("Поиск музыки завершен", reply_markup=main_menu_keyboard())
        await state.set_state(UserStates.START)
        await callback.answer()
        return

    # Получаем данные из хранилища (список треков)
    state_data = await state.get_data()
    tracks = state_data.get("vk_tracks", [])

    if not tracks:
        await callback.answer("Сессия устарела. Повторите поиск", show_alert=True)
        return

    # --- 2. ПЕРЕЛИСТЫВАНИЕ СТРАНИЦ ---
    if data.startswith("music_page_"):
        new_page = int(data.split("_")[2])

        # Обновляем страницу в памяти
        await state.update_data(current_page=new_page)

        # Генерируем новый текст и кнопки
        text, kb = get_music_page(tracks, page=new_page)

        # Редактируем сообщение (чтобы не спамить новыми)
        try:
            await callback.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)
        except Exception:
            pass  # Если текст не изменился, Telegram кинет ошибку, игнорируем

        await callback.answer()
        return

    # --- 3. СКАЧИВАНИЕ ТРЕКА ---
    if data.startswith("music_dl_"):
        try:
            index = int(data.split("_")[2])

            if index >= len(tracks):
                await callback.answer("Ошибка: трек не найден", show_alert=True)
                return

            track = tracks[index]

            # Уведомляем пользователя
            await callback.answer(f"Загружаю: {track['title']}...")
            await callback.message.answer(f"⏳ Скачиваю: {track['artist']} - {track['title']}...")

            # Скачиваем
            filename = f"{callback.from_user.id}_music.mp3"
            file_path = await vk_helper.download_track(track['url'], filename)

            if file_path:
                await send_file(callback.message, file_path, f"{track['artist']} - {track['title']}", "audio")
                # Кнопка "Готово" не обязательна, пользователь может продолжить качать из списка выше
            else:
                await callback.message.answer("Ошибка при скачивании файла 😔")

        except Exception as e:
            logging.error(f"Error music download: {e}")
            await callback.message.answer("Произошла ошибка при загрузке.")

        return

    # Игнорируем нажатие на счетчик страниц
    if data == "ignore":
        await callback.answer()


@dp.message(UserStates.GET_URL)
async def process_url_handler(message: types.Message, state: FSMContext, url: str = None):
    if not url:
        url = message.text.strip()
    user_id = message.from_user.id
    username = message.from_user.username or "Unknown"

    save_user(user_id, username, last_url=url, last_action="Получен URL")
    log_action(user_id, url=url, action="Получен URL")

    link_type = detect_link_type(url)
    logging.info(f"Получена ссылка: {url}, тип: {link_type}")

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
        await message.answer("Введите поисковый запрос для видео:")
        await state.set_state(UserStates.SEARCH_YT)
    elif text == "поиск музыки vk 🎧":
        await message.answer("Введите название трека или исполнителя:")
        await state.set_state(UserStates.SEARCH_VK_MUSIC)
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
            if DEV_ID:
                await bot.send_message(DEV_ID,
                                       f"Сообщение от {message.from_user.username or message.from_user.id}:\n{message.text}")
                await message.answer("Ваше сообщение успешно отправлено разработчику. ✅",
                                     reply_markup=main_menu_keyboard())
            else:
                await message.answer("ID разработчика не настроен.", reply_markup=main_menu_keyboard())
            await state.set_state(UserStates.START)
        except Exception as e:
            logging.error(f"Ошибка отправки сообщения разработчику: {e}")
            await message.answer("Не удалось отправить сообщение разработчику. Попробуйте позже.",
                                 reply_markup=main_menu_keyboard())
            await state.set_state(UserStates.START)


# Обработчик выбора действия
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
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            result = ydl.extract_info(
                f'ytsearch{max_results}:{query}',
                download=False
            )
            if not result or 'entries' not in result:
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


async def download_vk_history(url, user_id, quality='720'):
    # Эта функция работает криво с токеном бота, для историй нужен User Token,
    # но пока оставлю как было в исходнике, предполагая что ACCESS_TOKEN есть в env
    if "story" not in url:
        return None, None

    try:
        story_id = url.split('story')[1]
        params = {'v': "5.199"}
        url_api = "https://api.vk.com/method/stories.getById"
        # Нужен ACCESS_TOKEN в .env для историй
        data = {"access_token": os.getenv("ACCESS_TOKEN"), 'stories': story_id}
        res = requests.post(url_api, params=params, data=data)

        available_qualities = {}
        items = res.json().get('response', {}).get('items', [])
        if not items:
            raise ValueError("История не найдена или доступ закрыт")

        req_data = items[0]

        # Если это видео-история
        if 'video' in req_data:
            for key in req_data['video']['files']:
                if 'mp4' in key:
                    available_qualities[key.split('_')[1]] = req_data['video']['files'][key]

            if '720' in available_qualities:
                selected_quality_url = available_qualities['720']
            else:
                # Берем лучшее что есть
                selected_quality_url = list(available_qualities.values())[0]

            res = requests.get(selected_quality_url)
            file_path = f"{user_id}_vk_story.mp4"
            with open(file_path, 'wb') as f:
                f.write(res.content)

            return file_path, available_qualities
        else:
            # Если это фото
            return None, None

    except Exception as e:
        logging.error(f"VK Story Error: {e}")
        return None, None


# Функция отправки файла
async def send_file(message: types.Message, file_path: str, title: str, file_type: str):
    if not os.path.exists(file_path):
        await message.answer("Файл не найден 🗑️. Попробуйте снова.")
        return

    file = FSInputFile(file_path)

    try:
        if file_type == "audio":
            await message.answer_audio(audio=file, caption=title, title=title)
        elif file_type == "video":
            await message.answer_video(video=file, caption=title)
        else:
            raise ValueError("Неподдерживаемый тип файла ❌")
    except Exception as e:
        await message.answer(f"Ошибка при отправке файла: {e}")
    finally:
        if os.path.exists(file_path):
            os.remove(file_path)


# Запуск бота
async def main():
    init_db()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())