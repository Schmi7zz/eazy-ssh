import json
import os

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import Application, CommandHandler, ContextTypes

TOKEN = os.getenv("BOT_TOKEN", "")
WEBAPP_URL = os.getenv("WEBAPP_URL", "https://ssh-terminal.yourdomain.com")
USERS_FILE = os.getenv("USERS_FILE", "/opt/ssh-terminal/users.json")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))


def load_users():
    try:
        with open(USERS_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {}


def save_users(users):
    with open(USERS_FILE, "w") as f:
        json.dump(users, f, ensure_ascii=False, indent=2)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    users = load_users()
    users[str(user.id)] = {
        "name": user.full_name,
        "username": user.username or "",
    }
    save_users(users)

    keyboard = [
        [InlineKeyboardButton("🖥 Open Terminal", web_app=WebAppInfo(url=WEBAPP_URL))]
    ]
    await update.message.reply_text(
        "👋 Welcome to EazySSH!\nConnect to your servers securely from Telegram.",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if ADMIN_ID == 0 or update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Access denied.")
        return

    users = load_users()
    text = f"👥 Total users: {len(users)}\n\n"
    for uid, info in list(users.items())[-20:]:
        uname = f"@{info['username']}" if info["username"] else "—"
        text += f"• {info['name']} ({uname}) [{uid}]\n"

    if len(users) > 20:
        text += f"\n... and {len(users) - 20} more"

    await update.message.reply_text(text)


def main():
    if not TOKEN:
        print("ERROR: BOT_TOKEN environment variable is required")
        return

    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stats", stats))
    print("Bot running...")
    app.run_polling()


if __name__ == "__main__":
    main()
