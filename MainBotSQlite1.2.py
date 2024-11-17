import os
import logging
import asyncio
from telegram import Update, ReplyKeyboardMarkup, Bot
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ConversationHandler, ContextTypes, filters
import yt_dlp
import requests
import sqlite3
from dotenv import load
from datetime import datetime

load()

# Настройка логирования
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# Константы
MAX_TELEGRAM_FILE_SIZE = 2 * 1024 * 1024 * 1024  # 2ГБ
TELEGRAM_MAX_FILE_SIZE = 50 * 1024 * 1024  # 50МБ

# Состояния для диалога
START, GET_URL, PROCESS, SELECT_QUALITY, SELECT_QUALITY_VK, END = range(6)

# Клавиатура для выбора начального действия
reply_keyboard_start = [['Отправить Ссылку'], ['Отмена']]
markup_start = ReplyKeyboardMarkup(reply_keyboard_start, resize_keyboard=True, one_time_keyboard=True)


# Инициализация базы данных
def init_db():
    conn = sqlite3.connect("telegram_bot.db")
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


# Добавление или обновление пользователя
def save_user(user_id, username, last_url=None, last_action=None):
    conn = sqlite3.connect("telegram_bot.db")
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


# Логирование действий
def log_action(user_id, url, action):
    conn = sqlite3.connect("telegram_bot.db")
    cursor = conn.cursor()

    cursor.execute('''
        INSERT INTO logs (user_id, url, action)
        VALUES (?, ?, ?)
    ''', (user_id, url, action))

    conn.commit()
    conn.close()


# Сохранение информации о скачанном файле
def save_download(user_id, file_path, file_type):
    conn = sqlite3.connect("telegram_bot.db")
    cursor = conn.cursor()

    cursor.execute('''
        INSERT INTO downloads (user_id, file_path, file_type)
        VALUES (?, ?, ?)
    ''', (user_id, file_path, file_type))

    conn.commit()
    conn.close()


# Обработчик команды /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.message.from_user.id
    username = update.message.from_user.username or "Unknown"
    save_user(user_id, username)  # Сохранение пользователя
    await update.message.reply_text('Добро пожаловать! Выберите опцию:', reply_markup=markup_start)
    return START


# Обработчик ввода URL
async def get_url(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text('Пожалуйста, отправьте YouTube или VK URL:',
                                    reply_markup=ReplyKeyboardMarkup([['Назад']], resize_keyboard=True))
    return GET_URL


# Определение типа ссылки (YouTube, VK Видео/Клип или VK История)
def detect_link_type(url):
    if "youtube.com" in url or "youtu.be" in url:
        return "YouTube"
    elif "vk.com/video" in url or "vk.com/clip" in url:
        return "VK_VIDEO_CLIP"
    elif "vk.com/story" in url:
        return "VK_STORY"
    return None

async def handle_quality_selection(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    selection = update.message.text.strip()
    if selection.lower() == 'назад':
        return await process_url(update, context)

    formats = context.user_data.get('formats')

    # Проверка наличия форматов
    if formats is None:
        await update.message.reply_text("Не удалось получить доступные качества для видео. Попробуйте снова.",
                                        reply_markup=markup_start)
        return PROCESS

    # Поиск выбранного формата в списке доступных форматов
    selected_format = next((f for f in formats if f"{f['resolution']} - {f['ext']}" == selection), None)
    if selected_format:
        await update.message.reply_text(
            f"Вы выбрали качество: {selected_format['resolution']} {selected_format['ext']}. Видео загружается..."
        )
        video_file_path, title = await asyncio.to_thread(
            download_video_with_quality, context.user_data.get('url'), selected_format, update.message.from_user.id
        )
        await send_file(update, video_file_path, title, 'video')
        return PROCESS
    else:
        await update.message.reply_text("Неверный выбор, попробуйте еще раз.", reply_markup=markup_start)
        return SELECT_QUALITY_VK

async def handle_quality_selection_VK(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    selection = update.message.text.strip()
    if selection.lower() == 'назад':
        return await process_url(update, context)

    available_qualities = context.user_data.get('available_qualities')
    if selection in available_qualities:
        await update.message.reply_text(f"Вы выбрали качество {selection}. Загрузка началась...")
        vk_story_path, _ = await asyncio.to_thread(download_vk_history, context.user_data.get('url'),
                                                   update.message.from_user.id, quality=selection)
        await send_file(update, vk_story_path, "VK Story", 'video')
        return PROCESS
    else:
        await update.message.reply_text("Неверный выбор, попробуйте еще раз.", reply_markup=markup_start)
        return SELECT_QUALITY_VK

async def stop_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text('До скорых встреч!', reply_markup=markup_start)
    return ConversationHandler.END

# Основной обработчик сообщений
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip().lower()
    if text == 'отправить ссылку':
        return await get_url(update, context)
    elif text == 'отмена':
        return await stop_conversation(update, context)
    else:
        await update.message.reply_text("Выберите доступную опцию.", reply_markup=markup_start)
        return START

def download_vk_history(url, user_id, quality='480'):
    id = url.split('story')[1]
    params = {'v': "5.199"}
    url1 = "https://api.vk.com/method/stories.getById"
    data1 = {"access_token": os.getenv("ACCESS_TOKEN"), 'stories': id}
    res = requests.post(url1, params=params, data=data1)

    available_qualities = {}
    req_1 = res.json()['response']['items'][0]

    for i in req_1['video']['files'].keys():
        if 'mp4' in i:
            available_qualities[i.split('_')[1]] = req_1['video']['files'][i]

    selected_quality_url = available_qualities.get(quality, available_qualities.get('480'))

    req = requests.get(selected_quality_url)
    file_path = f"{user_id}_video.mp4"
    with open(file_path, 'wb') as x:
        x.write(req.content)

    return file_path, available_qualities

def download_vk_content(url, user_id):
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
        info = ydl.extract_info(url, download=True)
        file_path = f"{user_id}_vk.{info['ext']}"
        title = info.get('title', 'VK Content')
        return file_path, title

def get_available_formats(url):
    ydl_opts = {'listformats': True}
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)
        formats = [f for f in info.get('formats', []) if f.get('acodec') != 'none' and f.get('vcodec') != 'none']
        return [{'format_id': f['format_id'], 'resolution': f.get('resolution', 'audio'), 'ext': f['ext']} for f in
                formats]

# Обработка URL
async def process_url(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.message.from_user.id
    username = update.message.from_user.username or "Unknown"
    url = update.message.text.strip()

    save_user(user_id, username, last_url=url)  # Обновление информации о пользователе
    log_action(user_id, url, "Получен URL")  # Логируем действие

    context.user_data['url'] = url
    link_type = detect_link_type(url)
    context.user_data['link_type'] = link_type

    if link_type == "YouTube":
        action_buttons = [['Конвертировать в MP3'], ['Скачать видео'], ['Назад']]
    elif link_type == "VK_VIDEO_CLIP":
        action_buttons = [['Скачать VK Видео/Клип'], ['Назад']]
    elif link_type == "VK_STORY":
        action_buttons = [['Скачать VK Историю'], ['Назад']]
    else:
        await update.message.reply_text(
            "Неподдерживаемый URL. Пожалуйста, отправьте ссылку на YouTube, VK Клип, или VK Историю.")
        return START

    markup_action = ReplyKeyboardMarkup(action_buttons, resize_keyboard=True, one_time_keyboard=True)
    await update.message.reply_text('Выберите действие:', reply_markup=markup_action)
    return PROCESS

async def handle_action_selection(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    action = update.message.text.strip().lower()
    if action == 'назад':
        return await get_url(update, context)

    url = context.user_data.get('url')
    link_type = context.user_data.get('link_type')

    if action == 'скачать vk историю' and link_type == "VK_STORY":
        # Получаем доступные качества
        _, available_qualities = await asyncio.to_thread(download_vk_history, url, update.message.from_user.id)

        quality_buttons = [[quality] for quality in available_qualities.keys()]
        quality_buttons.append(['Назад'])
        markup_quality = ReplyKeyboardMarkup(quality_buttons, resize_keyboard=True, one_time_keyboard=True)

        context.user_data['available_qualities'] = available_qualities
        await update.message.reply_text('Выберите качество для VK Истории:', reply_markup=markup_quality)
        return SELECT_QUALITY_VK

    elif action == 'скачать vk видео/клип' and link_type == "VK_VIDEO_CLIP":
        await update.message.reply_text("Загрузка VK Видео/Клипа началась...")
        vk_file_path, title = await asyncio.to_thread(download_vk_content, url, update.message.from_user.id)
        await send_file(update, vk_file_path, title, 'video')
        return PROCESS

    elif action == 'конвертировать в mp3':
        await update.message.reply_text("Конвертация началась...")
        mp3_file_path, title = await asyncio.to_thread(download_audio, url, update.message.from_user.id)
        await send_file(update, mp3_file_path, title, 'audio')
        return PROCESS

    elif action == 'скачать видео' and link_type == "YouTube":
        formats = await asyncio.to_thread(get_available_formats, url)
        if not formats:
            await update.message.reply_text("Не удалось получить информацию о видеоформатах.",
                                            reply_markup=markup_start)
            return PROCESS

        quality_buttons = [[f"{f['resolution']} - {f['ext']}"] for f in formats]
        quality_buttons.append(['Назад'])
        markup_quality = ReplyKeyboardMarkup(quality_buttons, resize_keyboard=True, one_time_keyboard=True)
        context.user_data['formats'] = formats
        await update.message.reply_text('Выберите качество видео:', reply_markup=markup_quality)
        return SELECT_QUALITY

# Функция отправки файла с проверкой типа
async def send_file(update: Update, file_path, title, file_type):
    if os.path.getsize(file_path) > MAX_TELEGRAM_FILE_SIZE:
        await update.message.reply_text("Файл слишком большой для отправки через Telegram.")
    else:
        with open(file_path, 'rb') as file:
            if file_type == 'audio':
                await update.message.reply_audio(audio=file, title=title, read_timeout=300)
            else:
                await update.message.reply_video(video=file, caption=title, read_timeout=300)
        os.remove(file_path)


# Скачивание аудио в формате MP3
def download_audio(url, user_id):
    ydl_opts = {
        'format': 'bestaudio/best',
        'postprocessors': [{'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3', 'preferredquality': '192'}],
        'outtmpl': f'{user_id}_audio.%(ext)s',
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        file_path = f"{user_id}_audio.mp3"
        title = info.get('title', 'Untitled')

        # Сохраняем информацию о скачивании
        save_download(user_id, file_path, 'audio')
        return file_path, title


# Скачивание видео с выбранным качеством
def download_video_with_quality(url, selected_format, user_id):
    ydl_opts = {'format': selected_format['format_id'], 'outtmpl': f'{user_id}_video.%(ext)s'}
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        file_path = f"{user_id}_video.{selected_format['ext']}"
        title = info.get('title', 'Untitled')

        # Сохраняем информацию о скачивании
        save_download(user_id, file_path, 'video')
        return file_path, title


# Создание и запуск бота
def main():
        init_db()
        token = os.getenv('TOKEN')
        bot = Bot(token=token)
        application = ApplicationBuilder().bot(bot).build()

        conv_handler = ConversationHandler(
            entry_points=[CommandHandler('start', start)],
            states={
                START: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message)],
                GET_URL: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_url)],
                PROCESS: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_action_selection)],
                SELECT_QUALITY: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_quality_selection)],
                SELECT_QUALITY_VK: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_quality_selection_VK)],
            },
            fallbacks=[MessageHandler(filters.TEXT & ~filters.COMMAND, stop_conversation)],
        )

        application.add_handler(conv_handler)
        application.run_polling()


if __name__ == '__main__':
    main()