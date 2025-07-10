import os
import re
import time
import base64
import requests
from flask import Flask
from dotenv import load_dotenv
from threading import Thread
from collections import defaultdict
from urllib.parse import unquote, urlparse, parse_qs
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters

print("✅ Running from file:", __file__)
import telegram
print("🤖 PTB Version:", telegram.__version__)

load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID"))
ALLOWED_USERS = []

user_modes = {}
user_history = defaultdict(list)
last_decode_time = {}

def extract_link(text):
    match = re.search(r'https?://\S+', text)
    return match.group(0) if match else None

def follow_redirects(url):
    try:
        r = requests.get(url, allow_redirects=True, timeout=7)
        return r.url
    except:
        return url

def decode_base64_url_param(url):
    try:
        qs = parse_qs(urlparse(url).query)
        if 'url' in qs:
            raw = qs['url'][0]
            if raw.startswith("http"):
                return raw, "Direct URL param"
            decoded = base64.urlsafe_b64decode(raw + '===').decode()
            return decoded, "Base64 decoded"
    except:
        return None, None
    return None, None

def decode_once(url):
    result = url
    reason = "Original"
    b64_decoded, reason_b64 = decode_base64_url_param(url)
    if b64_decoded:
        return b64_decoded.strip(), reason_b64
    if '%' in result:
        result = unquote(result)
        reason = "URL unquoted"
    match = re.search(r'[?&](s|url|data)=([^&]+)', result)
    if match:
        param = match.group(2)
        try:
            if all(c in "0123456789abcdefABCDEF" for c in param) and len(param) % 2 == 0:
                decoded_hex = bytes.fromhex(param).decode()
                if decoded_hex.startswith("http"):
                    return decoded_hex.strip(), f"Hex decoded from `{match.group(1)}` param"
        except:
            pass
        if param.startswith("http"):
            return param.strip(), f"Param `{match.group(1)}`"
    result = follow_redirects(result)
    return result.strip(), reason

def recursive_decode(url, depth=7):
    prev = url
    steps = []
    for _ in range(depth):
        new, reason = decode_once(prev)
        steps.append((prev, new, reason))
        if new == prev:
            break
        prev = new
    return steps

def validate_link(url):
    try:
        r = requests.head(url, timeout=5)
        return r.status_code in [200, 301, 302], r.status_code
    except:
        return False, 0

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await update.message.reply_text(
        f"👋 Hello, {user.first_name}!\n"
        "Send me a shortened or encoded link and I’ll decode it for you.\n"
        "Use /help to see all available commands."
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "*SmartDecode Bot – Help Menu*\n\n"
        "/start – Start the bot\n"
        "/help – Show this help message\n"
        "/mode simple|detailed – Change result display mode\n"
        "/info – Show your last decoded result\n"
        "/history – View your recent decode history\n"
        "/clear – Clear your decode history\n\n"
        "📎 Just send any link to begin decoding.",
        parse_mode="Markdown"
    )

async def set_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if context.args and context.args[0] in ['simple', 'detailed']:
        user_modes[uid] = context.args[0]
        await update.message.reply_text(f"✅ Display mode set to: {context.args[0]}")
    else:
        await update.message.reply_text("Usage: /mode simple or /mode detailed")

async def show_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    hist = user_history[uid][-5:]
    if not hist:
        await update.message.reply_text("📂 You haven't decoded any links yet.")
    else:
        msg = "\n".join([f"{i+1}. {link}" for i, link in enumerate(hist)])
        await update.message.reply_text(f"📜 Your last decoded links:\n{msg}")

async def info_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not user_history[uid]:
        return await update.message.reply_text("ℹ️ No decoded result available.")
    last = user_history[uid][-1]
    await update.message.reply_text(f"🔍 Your last decoded link:\n{last}")

async def clear_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid in user_history:
        del user_history[uid]
        await update.message.reply_text("🗑️ Your decode history has been cleared.")
    else:
        await update.message.reply_text("⚠️ You have no decode history to clear.")

async def handle_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    uid = user.id
    now = time.time()
    if uid in last_decode_time and now - last_decode_time[uid] < 3:
        return await update.message.reply_text("⏳ Please wait before sending another link.")
    if ALLOWED_USERS and uid not in ALLOWED_USERS:
        return await update.message.reply_text("❌ You are not authorized to use this bot.")
    last_decode_time[uid] = now
    text = update.message.text.strip()
    link = extract_link(text)
    if not link:
        return await update.message.reply_text("❗ Please send a valid link.")
    loading_msg = await update.message.reply_text("⏳ Decoding your link...")
    if uid not in user_modes:
        user_modes[uid] = 'detailed' if uid == ADMIN_ID else 'simple'
    steps = recursive_decode(link)
    final = steps[-1][1]
    valid, code = validate_link(final)
    user_history[uid].append(final)
    mode = user_modes[uid]
    if mode == 'simple':
        reply_markup = InlineKeyboardMarkup([[InlineKeyboardButton("Open", url=final)]])
        response_text = f"🔗 {final}"
    else:
        detail = "\n".join([f"{i+1}. `{s[0]}`\n→ `{s[1]}` ({s[2]})" for i, s in enumerate(steps)])
        status = "✅ Valid link" if valid else f"⚠️ Broken link (HTTP {code})"
        response_text = f"*Decoded Steps:*\n{detail}\n\n*Final:* `{final}`\n{status}"
        reply_markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔗 Open", url=final)],
            [InlineKeyboardButton("📋 Copy", switch_inline_query=final)]
        ])
    await loading_msg.edit_text(response_text, parse_mode="Markdown", reply_markup=reply_markup)

app_flask = Flask('')

@app_flask.route('/')
def home():
    return "✅ SmartDecode Bot is running!"

def run_flask():
    app_flask.run(host='0.0.0.0', port=8080)

def keep_alive():
    Thread(target=run_flask).start()

async def set_bot_menu(app):
    await app.bot.set_my_commands([
        BotCommand("start", "Start the bot"),
        BotCommand("help", "Help menu"),
        BotCommand("mode", "Set display mode"),
        BotCommand("history", "Recent decode history"),
        BotCommand("info", "Last decoded link"),
        BotCommand("clear", "Clear your decode history")
    ])

import asyncio
from telegram.ext import ApplicationBuilder

if __name__ == "__main__":
    keep_alive()

    async def runner():
        app = ApplicationBuilder().token(TOKEN).post_init(set_bot_menu).build()
        app.add_handler(CommandHandler("start", start))
        app.add_handler(CommandHandler("help", help_command))
        app.add_handler(CommandHandler("mode", set_mode))
        app.add_handler(CommandHandler("history", show_history))
        app.add_handler(CommandHandler("info", info_command))
        app.add_handler(CommandHandler("clear", clear_history))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_link))
        print("🤖 SmartDecode Bot is online.")

        await app.initialize()
        await app.start()
        await app.updater.start_polling()
        print("✅ Bot polling has started and is now running...")

    try:
        loop = asyncio.get_event_loop()
        loop.create_task(runner())
        loop.run_forever()
    except KeyboardInterrupt:
        print("🛑 Bot stopped manually.")