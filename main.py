import requests
import sqlite3
import os
import asyncio
from telegram.ext import Application, CommandHandler
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram import Update
from telegram.ext import ContextTypes

print("Імпортую бібліотеки...")

# Налаштування
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
print(f"YOUTUBE_API_KEY: {YOUTUBE_API_KEY}")
print(f"TELEGRAM_TOKEN: {TELEGRAM_TOKEN}")

if not YOUTUBE_API_KEY:
    raise ValueError("YOUTUBE_API_KEY не встановлено")
if not TELEGRAM_TOKEN:
    raise ValueError("TELEGRAM_TOKEN не встановлено")

print("Змінні середовища перевірені")

# Ініціалізація бази даних
conn = sqlite3.connect("comments.db", check_same_thread=False)
cursor = conn.cursor()
cursor.execute("""
    CREATE TABLE IF NOT EXISTS videos (
        video_id TEXT,
        chat_id TEXT
    )
""")
cursor.execute("""
    CREATE TABLE IF NOT EXISTS comments (
        video_id TEXT,
        comment_text TEXT,
        comment_id TEXT
    )
""")
conn.commit()
print("База даних ініціалізована")

# Отримання коментарів із YouTube (включаючи відповіді)
def get_video_comments(video_id):
    print(f"Отримую коментарі для відео {video_id}...")
    comments = []
    next_page_token = None

    while True:
        try:
            url = (f"https://www.googleapis.com/youtube/v3/commentThreads?part=snippet,replies&videoId={video_id}"
                   f"&key={YOUTUBE_API_KEY}&maxResults=100")
            if next_page_token:
                url += f"&pageToken={next_page_token}"
            response = requests.get(url).json()
            print(f"Відповідь YouTube API: {response}")

            if "items" in response:
                for item in response["items"]:
                    # Топ-рівневий коментар
                    top_comment = item["snippet"]["topLevelComment"]["snippet"]["textOriginal"]
                    top_comment_id = item["snippet"]["topLevelComment"]["id"]
                    comments.append((top_comment, top_comment_id))
                    print(f"Топ-рівневий коментар: {top_comment}, ID: {top_comment_id}")

                    # Відповіді на коментар
                    if "replies" in item and item["replies"].get("comments"):
                        for reply in item["replies"]["comments"]:
                            reply_text = reply["snippet"]["textOriginal"]
                            reply_id = reply["id"]
                            comments.append((reply_text, reply_id))
                            print(f"Відповідь: {reply_text}, ID: {reply_id}")

                print(f"Знайдено {len(comments)} коментарів (включаючи відповіді) на цій сторінці")
            else:
                print("Коментарі відсутні або помилка в структурі відповіді")
                break

            # Перевірка на наступну сторінку
            next_page_token = response.get("nextPageToken")
            if not next_page_token:
                break

        except Exception as e:
            print(f"Помилка YouTube API: {e}")
            break

    print(f"Усього знайдено {len(comments)} коментарів")
    return comments

# Перевірка на наявність ключових слів
def has_trigger_words(comment_text):
    trigger_words = ["scam", "fraud"]
    comment_lower = comment_text.lower()
    return any(word in comment_lower for word in trigger_words)

# Команда /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    print("Отримано команду /start")
    await update.message.reply_text(
        "Привіт! Я бот для відстеження нових коментарів під YouTube-відео.\n"
        "Використовуй /track <video_id>, наприклад: /track ixqPzkuY_4U"
    )

# Команда /track
async def track_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    print("Отримано команду /track")
    if not context.args:
        await update.message.reply_text("Вкажи ID відео! Наприклад: /track ixqPzkuY_4U")
        return
    video_id = context.args[0]
    chat_id = str(update.message.chat_id)

    # Перевірка, чи відео вже відстежується
    cursor.execute("SELECT * FROM videos WHERE video_id = ? AND chat_id = ?", (video_id, chat_id))
    if cursor.fetchone():
        await update.message.reply_text("Це відео вже відстежується!")
        return

    # Додаємо відео до відстеження
    cursor.execute("INSERT INTO videos (video_id, chat_id) VALUES (?, ?)", (video_id, chat_id))
    conn.commit()
    await update.message.reply_text(f"Відео {video_id} додано до відстеження! Перевірятиму нові коментарі кожні 2 хвилини.")

# Команда /list
async def list_videos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    print("Отримано команду /list")
    chat_id = str(update.message.chat_id)
    cursor.execute("SELECT video_id FROM videos WHERE chat_id = ?", (chat_id,))
    videos = cursor.fetchall()
    if not videos:
        await update.message.reply_text("Ви не відстежуєте жодного відео.")
        return
    video_list = "\n".join([video[0] for video in videos])
    await update.message.reply_text(f"Відстежувані відео:\n{video_list}")

# Команда /untrack
async def untrack_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    print("Отримано команду /untrack")
    if not context.args:
        await update.message.reply_text("Вкажи ID відео! Наприклад: /untrack ixqPzkuY_4U")
        return
    video_id = context.args[0]
    chat_id = str(update.message.chat_id)
    
    cursor.execute("SELECT * FROM videos WHERE video_id = ? AND chat_id = ?", (video_id, chat_id))
    if not cursor.fetchone():
        await update.message.reply_text("Це відео не відстежується!")
        return
    
    cursor.execute("DELETE FROM videos WHERE video_id = ? AND chat_id = ?", (video_id, chat_id))
    cursor.execute("DELETE FROM comments WHERE video_id = ?", (video_id,))
    conn.commit()
    await update.message.reply_text(f"Відео {video_id} видалено з відстеження.")

# Перевірка нових коментарів
async def check_new_comments():
    print("Перевіряю нові коментарі...")
    cursor.execute("SELECT video_id, chat_id FROM videos")
    videos = cursor.fetchall()
    print(f"Знайдено {len(videos)} відео для перевірки")
    for video_id, chat_id in videos:
        # Отримуємо коментарі з YouTube
        comments = get_video_comments(video_id)
        if not comments:
            print(f"Немає коментарів для відео {video_id}")
            continue

        # Перевіряємо, які коментарі нові
        new_comments = []
        for comment_text, comment_id in comments:
            cursor.execute("SELECT * FROM comments WHERE comment_id = ?", (comment_id,))
            if not cursor.fetchone():
                # Це новий коментар (або відповідь)
                new_comments.append((comment_text, comment_id))
                cursor.execute("INSERT INTO comments (video_id, comment_text, comment_id) VALUES (?, ?, ?)",
                               (video_id, comment_text, comment_id))
        conn.commit()

        print(f"Знайдено {len(new_comments)} нових коментарів (включаючи відповіді) для відео {video_id}")
        # Надсилаємо повідомлення про нові коментарі
        for comment_text, comment_id in new_comments:
            video_url = f"https://www.youtube.com/watch?v={video_id}"
            # Перевіряємо, чи є ключові слова
            if has_trigger_words(comment_text):
                message = f"!!!!!!!!!\nНовий коментар\n{video_url}\n\nКоментар: {comment_text}"
            else:
                message = f"Новий коментар\n{video_url}\n\nКоментар: {comment_text}"
            await application.bot.send_message(
                chat_id=chat_id,
                text=message
            )

# Налаштування бота
print("Налаштовую бота...")
application = Application.builder().token(TELEGRAM_TOKEN).build()
application.add_handler(CommandHandler("start", start))
application.add_handler(CommandHandler("track", track_video))
application.add_handler(CommandHandler("list", list_videos))
application.add_handler(CommandHandler("untrack", untrack_video))
print("Обробники команд додані")

# Періодична перевірка (кожні 2 хвилини)
scheduler = AsyncIOScheduler()
scheduler.add_job(check_new_comments, "interval", minutes=2)

# Асинхронна функція для запуску
async def main():
    print("Запускаю планувальник...")
    scheduler.start()
    print("Планувальник запущений")
    await application.initialize()
    print("Бот ініціалізований")
    await application.start()
    print("Бот стартував")
    await application.updater.start_polling()
    print("Polling запущений")
    await asyncio.Event().wait()

# Запуск бота
if __name__ == "__main__":
    print("Бот запускається...")
    try:
        asyncio.run(main())
    except Exception as e:
        print(f"Помилка при запуску бота: {e}")
