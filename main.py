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
                    top_comment = item["snippet"]["topLevelComment"]["snippet"]["textOriginal"]
                    top_comment_id = item["snippet"]["topLevelComment"]["id"]
                    comments.append((top_comment, top_comment_id))
                    print(f"Топ-рівневий коментар: {top_comment}, ID: {top_comment_id}")

                    if "replies" in item and item["replies"].get("comments"):
                        for reply in item["replies"]["comments"]:
                            reply_text = reply["snippet"]["textOriginal"]
                            reply_id = reply["id"]
                            comments.append((reply_text, reply_id))
                            print(f"Відповідь: {reply_text}, ID: {reply_id}")

            else:
                break

            next_page_token = response.get("nextPageToken")
            if not next_page_token:
                break

        except Exception as e:
            print(f"Помилка YouTube API: {e}")
            break

    print(f"Усього знайдено {len(comments)} коментарів")
    return comments

def has_trigger_words(comment_text):
    trigger_words = ["scam", "fraud"]
    comment_lower = comment_text.lower()
    return any(word in comment_lower for word in trigger_words)

# Команди
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привіт! Я бот для відстеження нових коментарів під YouTube-відео.\n"
        "Використовуй /track <video_id>, наприклад: /track ixqPzkuY_4U"
    )

async def track_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Вкажи ID відео! Наприклад: /track ixqPzkuY_4U")
        return
    video_id = context.args[0]
    chat_id = str(update.message.chat_id)

    cursor.execute("SELECT * FROM videos WHERE video_id = ? AND chat_id = ?", (video_id, chat_id))
    if cursor.fetchone():
        await update.message.reply_text("Це відео вже відстежується!")
        return

    cursor.execute("INSERT INTO videos (video_id, chat_id) VALUES (?, ?)", (video_id, chat_id))
    conn.commit()
    await update.message.reply_text(f"Відео {video_id} додано до відстеження!")

async def list_videos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.message.chat_id)
    cursor.execute("SELECT video_id FROM videos WHERE chat_id = ?", (chat_id,))
    videos = cursor.fetchall()
    if not videos:
        await update.message.reply_text("Ви не відстежуєте жодного відео.")
        return
    video_list = "\n".join([video[0] for video in videos])
    await update.message.reply_text(f"Відстежувані відео:\n{video_list}")

async def untrack_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
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

async def untrack_all_videos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.message.chat_id)
    cursor.execute("SELECT video_id FROM videos WHERE chat_id = ?", (chat_id,))
    tracked = cursor.fetchall()
    if not tracked:
        await update.message.reply_text("Немає відео для видалення.")
        return

    cursor.execute("DELETE FROM videos WHERE chat_id = ?", (chat_id,))
    conn.commit()
    await update.message.reply_text(f"Видалено {len(tracked)} відео з відстеження.")

# Перевірка нових коментарів
async def check_new_comments():
    print("Перевіряю нові коментарі...")
    cursor.execute("SELECT video_id, chat_id FROM videos")
    videos = cursor.fetchall()
    for video_id, chat_id in videos:
        comments = get_video_comments(video_id)
        if not comments:
            continue

        new_comments = []
        for comment_text, comment_id in comments:
            cursor.execute("SELECT * FROM comments WHERE comment_id = ?", (comment_id,))
            if not cursor.fetchone():
                new_comments.append((comment_text, comment_id))
                cursor.execute("INSERT INTO comments (video_id, comment_text, comment_id) VALUES (?, ?, ?)",
                               (video_id, comment_text, comment_id))
        conn.commit()

        for comment_text, comment_id in new_comments:
            video_url = f"https://www.youtube.com/watch?v={video_id}"
            if has_trigger_words(comment_text):
                message = f"‼️ Новий підозрілий коментар:\n{video_url}\n\n{comment_text}"
            else:
                message = f"Новий коментар:\n{video_url}\n\n{comment_text}"
            await application.bot.send_message(chat_id=chat_id, text=message)

# Налаштування бота
application = Application.builder().token(TELEGRAM_TOKEN).build()
application.add_handler(CommandHandler("start", start))
application.add_handler(CommandHandler("track", track_video))
application.add_handler(CommandHandler("list", list_videos))
application.add_handler(CommandHandler("untrack", untrack_video))
application.add_handler(CommandHandler("untrack_all", untrack_all_videos))

# Запуск планувальника кожні 10 хв
scheduler = AsyncIOScheduler()
scheduler.add_job(check_new_comments, "interval", minutes=10)

# Асинхронний запуск
async def main():
    scheduler.start()
    await application.initialize()
    await application.start()
    await application.updater.start_polling()
    await asyncio.Event().wait()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        print(f"Помилка при запуску бота: {e}")
