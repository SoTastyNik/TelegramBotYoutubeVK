import os
import logging
import asyncio
from telegram import Update, ReplyKeyboardMarkup,Bot
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ConversationHandler, ContextTypes, filters
import yt_dlp
import requests
from dotenv import load

load()

# Настройка логирования
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# Константы
MAX_TELEGRAM_FILE_SIZE = 2 * 1024 * 1024 * 1024  # 2ГБ
TELEGRAM_MAX_FILE_SIZE = 50 * 1024 * 1024  # 50МБ

# Состояния для диалога
START, GET_URL, PROCESS, SELECT_QUALITY, END = range(5)

# Клавиатура для выбора начального действия
reply_keyboard_start = [['Отправить Ссылку'], ['Отмена']]
markup_start = ReplyKeyboardMarkup(reply_keyboard_start, resize_keyboard=True, one_time_keyboard=True)


# Обработчик команды /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
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


# Обработка URL
async def process_url(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    url = update.message.text.strip()
    if url.lower() == 'назад':
        return await start(update, context)

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


# Обработчик выбора действия
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
        return SELECT_QUALITY

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


# Обработчик выбора качества
async def handle_quality_selection(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
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
        return SELECT_QUALITY


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


# Завершение диалога
async def stop_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text('До скорых встреч!', reply_markup=markup_start)
    return ConversationHandler.END


# Получение доступных форматов с аудио и видео
def get_available_formats(url):
    ydl_opts = {'listformats': True}
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)
        formats = [f for f in info.get('formats', []) if f.get('acodec') != 'none' and f.get('vcodec') != 'none']
        return [{'format_id': f['format_id'], 'resolution': f.get('resolution', 'audio'), 'ext': f['ext']} for f in
                formats]


# Скачивание аудио в формате MP3
def download_audio(url, user_id):
    ydl_opts = {
        'format': 'bestaudio/best',
        'postprocessors': [{'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3', 'preferredquality': '192'}],
        'outtmpl': f'{user_id}_audio.%(ext)s',
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        return f"{user_id}_audio.mp3", info.get('title', 'Untitled')


# Скачивание видео с выбранным качеством
def download_video_with_quality(url, selected_format, user_id):
    ydl_opts = {'format': selected_format['format_id'], 'outtmpl': f'{user_id}_video.%(ext)s'}
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        return f"{user_id}_video.{selected_format['ext']}", info.get('title', 'Untitled')


# Скачивание VK истории с заданным качеством
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


# Создание и запуск бота
def main():
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
            },
            fallbacks=[MessageHandler(filters.TEXT & ~filters.COMMAND, stop_conversation)],
        )

        application.add_handler(conv_handler)
        application.run_polling()


if __name__ == '__main__':
    main()
