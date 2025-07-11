import os
import re
import time
import base64
import requests
import telegram
import asyncio
from flask import Flask
from dotenv import load_dotenv
from threading import Thread
from collections import defaultdict
from urllib.parse import unquote, urlparse, parse_qs
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
)
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters
)

print("✅ Running from:", __file__)
print("🤖 PTB Version:", telegram.__version__)

load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID"))
ALLOWED_USERS = []

user_modes = {}
user_langs = defaultdict(lambda: 'en')
user_history = defaultdict(list)
last_decode_time = {}
known_users = set()


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

    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    if 'go' in qs:
        param = qs['go'][0]
        if all(c in "0123456789abcdefABCDEF" for c in param) and len(param) % 2 == 0:
            try:
                decoded_hex = bytes.fromhex(param).decode()
                if decoded_hex.startswith("http"):
                    return decoded_hex.strip(), f"Hex decoded from `go` param"
            except:
                pass

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

TEXT = {
    'en': {
        'welcome': "👋 Hello, {name}!\nSend me a shortened or encoded link and I’ll decode it for you.\nUse /help to see all available commands.",
        'help': "*SmartDecode Bot – Help Menu*\n\n/start – Start the bot\n/help – Show this help message\n/mode simple|detailed – Change result display mode\n/info – Show your last decoded result\n/history – View your recent decode history\n/clear – Clear your decode history\n/stats – Bot usage stats (admin)\n/broadcast – Send message to all users (admin)",
        'mode_set': "✅ Display mode set to: {mode}",
        'invalid_mode': "Usage: /mode simple or /mode detailed",
        'no_history': "📂 You haven't decoded any links yet.",
        'history': "📜 Your last decoded links:\n{items}",
        'last_info': "🔍 Your last decoded link:\n{link}",
        'no_info': "ℹ️ No decoded result available.",
        'cleared': "🗑️ Your decode history has been cleared.",
        'nothing_to_clear': "⚠️ You have no decode history to clear.",
        'wait': "⏳ Please wait before sending another link.",
        'unauthorized': "❌ You are not authorized to use this bot.",
        'invalid_link': "❗ Please send a valid link.",
        'decoding': "⏳ Decoding your link...",
        'valid': "✅ Valid link",
        'invalid': "⚠️ Broken link (HTTP {code})",
        'decoded': "*Decoded Steps:*\n{steps}\n\n*Final:* `{final}`\n{status}",
        'button_open': "🔗 Open",
        'button_copy': "📋 Copy",
        'button_lang': "🌐 Language / Bahasa",
        'choose_lang': "🌐 Please choose your language:",
        'lang_set': "✅ Language set to: {lang}",
        'stats': "📊 Total users: {count}",
        'broadcast_usage': "Usage:\n/broadcast Your message here...",
        'broadcast_done': "📢 Broadcast sent to {success} users. Failed: {fail}."
    },
    'id': {
        'welcome': "👋 Halo, {name}!\nKirimkan link pendek atau terenkripsi dan saya akan decode untukmu.\nGunakan /help untuk melihat perintah yang tersedia.",
        'help': "*SmartDecode Bot – Menu Bantuan*\n\n/start – Mulai bot\n/help – Tampilkan bantuan\n/mode simple|detailed – Ubah tampilan hasil\n/info – Tampilkan hasil decode terakhir\n/history – Riwayat decode terakhir\n/clear – Hapus riwayat decode\n/stats – Statistik bot (admin)\n/broadcast – Kirim pesan ke semua user (admin)",
        'mode_set': "✅ Mode tampilan diubah menjadi: {mode}",
        'invalid_mode': "Penggunaan: /mode simple atau /mode detailed",
        'no_history': "📂 Kamu belum pernah decode link.",
        'history': "📜 Riwayat link terbaru:\n{items}",
        'last_info': "🔍 Link terakhir kamu:\n{link}",
        'no_info': "ℹ️ Belum ada hasil decode.",
        'cleared': "🗑️ Riwayat decode kamu telah dihapus.",
        'nothing_to_clear': "⚠️ Tidak ada riwayat untuk dihapus.",
        'wait': "⏳ Tunggu sebentar sebelum mengirim link lain.",
        'unauthorized': "❌ Kamu tidak diizinkan menggunakan bot ini.",
        'invalid_link': "❗ Kirimkan link yang valid.",
        'decoding': "⏳ Sedang decode link...",
        'valid': "✅ Link valid",
        'invalid': "⚠️ Link rusak (HTTP {code})",
        'decoded': "*Tahapan Decode:*\n{steps}\n\n*Final:* `{final}`\n{status}",
        'button_open': "🔗 Buka",
        'button_copy': "📋 Salin",
        'button_lang': "🌐 Language / Bahasa",
        'choose_lang': "🌐 Silakan pilih bahasamu:",
        'lang_set': "✅ Bahasa diatur ke: {lang}",
        'stats': "📊 Total pengguna: {count}",
        'broadcast_usage': "Penggunaan:\n/broadcast Pesan kamu...",
        'broadcast_done': "📢 Broadcast dikirim ke {success} pengguna. Gagal: {fail}."
    }
}

def tr(uid, key, **kwargs):
    lang = user_langs.get(uid, 'en')
    return TEXT[lang][key].format(**kwargs)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    uid = user.id
    lang_code = user.language_code or 'en'
    user_langs[uid] = 'id' if lang_code.startswith('id') else 'en'
    known_users.add(uid)
    buttons = [[InlineKeyboardButton(tr(uid, 'button_lang'), callback_data="lang")]]
    await update.message.reply_text(tr(uid, 'welcome', name=user.first_name),
        reply_markup=InlineKeyboardMarkup(buttons))

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(tr(update.effective_user.id, 'help'), parse_mode="Markdown")

async def set_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if context.args and context.args[0] in ['simple', 'detailed']:
        user_modes[uid] = context.args[0]
        await update.message.reply_text(tr(uid, 'mode_set', mode=context.args[0]))
    else:
        await update.message.reply_text(tr(uid, 'invalid_mode'))

async def show_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    hist = user_history[uid][-5:]
    if not hist:
        await update.message.reply_text(tr(uid, 'no_history'))
    else:
        msg = "\n".join([f"{i+1}. {link}" for i, link in enumerate(hist)])
        await update.message.reply_text(tr(uid, 'history', items=msg))

async def info_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not user_history[uid]:
        await update.message.reply_text(tr(uid, 'no_info'))
    else:
        await update.message.reply_text(tr(uid, 'last_info', link=user_history[uid][-1]))

async def clear_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid in user_history:
        del user_history[uid]
        await update.message.reply_text(tr(uid, 'cleared'))
    else:
        await update.message.reply_text(tr(uid, 'nothing_to_clear'))

async def handle_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    uid = user.id
    now = time.time()
    if uid in last_decode_time and now - last_decode_time[uid] < 3:
        return await update.message.reply_text(tr(uid, 'wait'))
    if ALLOWED_USERS and uid not in ALLOWED_USERS:
        return await update.message.reply_text(tr(uid, 'unauthorized'))
    known_users.add(uid)
    last_decode_time[uid] = now
    link = extract_link(update.message.text.strip())
    if not link:
        return await update.message.reply_text(tr(uid, 'invalid_link'))
    msg = await update.message.reply_text(tr(uid, 'decoding'))
    steps = recursive_decode(link)
    final = steps[-1][1]
    valid, code = validate_link(final)
    user_history[uid].append(final)
    mode = user_modes.get(uid, 'detailed' if uid == ADMIN_ID else 'simple')
    if mode == 'simple':
        reply_markup = InlineKeyboardMarkup([
            [InlineKeyboardButton(tr(uid, 'button_open'), url=final)],
            [InlineKeyboardButton(tr(uid, 'button_lang'), callback_data="lang")]
        ])
        await msg.edit_text(f"🔗 {final}", reply_markup=reply_markup)
    else:
        detail = "\n".join([f"{i+1}. `{s[0]}`\n→ `{s[1]}` ({s[2]})" for i, s in enumerate(steps)])
        status = tr(uid, 'valid') if valid else tr(uid, 'invalid', code=code)
        reply_markup = InlineKeyboardMarkup([
            [InlineKeyboardButton(tr(uid, 'button_open'), url=final)],
            [InlineKeyboardButton(tr(uid, 'button_copy'), switch_inline_query=final)],
            [InlineKeyboardButton(tr(uid, 'button_lang'), callback_data="lang")]
        ])
        await msg.edit_text(tr(uid, 'decoded', steps=detail, final=final, status=status),
                            parse_mode="Markdown", reply_markup=reply_markup)

async def lang_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    buttons = [
        [InlineKeyboardButton("🇮🇩 Bahasa Indonesia", callback_data="setlang_id")],
        [InlineKeyboardButton("🇺🇸 English", callback_data="setlang_en")]
    ]
    await update.callback_query.message.reply_text(
        tr(uid, 'choose_lang'),
        reply_markup=InlineKeyboardMarkup(buttons)
    )

async def set_lang(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    lang = 'id' if update.callback_query.data == "setlang_id" else 'en'
    user_langs[uid] = lang
    await update.callback_query.answer()
    await update.callback_query.message.reply_text(tr(uid, 'lang_set', lang=lang.upper()))

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    await update.message.reply_text(tr(ADMIN_ID, 'stats', count=len(known_users)))

async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    if not context.args:
        return await update.message.reply_text(tr(ADMIN_ID, 'broadcast_usage'))
    msg = " ".join(context.args)
    success, fail = 0, 0
    for uid in list(known_users):
        try:
            await context.bot.send_message(uid, msg)
            success += 1
        except:
            fail += 1
    await update.message.reply_text(tr(ADMIN_ID, 'broadcast_done', success=success, fail=fail))

app_flask = Flask(__name__)
@app_flask.route('/')
def home():
    return "✅ SmartDecode Bot is running!"

def run_flask():
    app_flask.run(host='0.0.0.0', port=8080)

def keep_alive():
    Thread(target=run_flask).start()

async def set_bot_menu(app):
    await app.bot.set_my_commands([
        BotCommand("start", "Start / Mulai"),
        BotCommand("help", "Help / Bantuan"),
        BotCommand("mode", "Set mode"),
        BotCommand("history", "Riwayat"),
        BotCommand("info", "Link terakhir"),
        BotCommand("clear", "Hapus riwayat"),
        BotCommand("stats", "Statistik (admin)"),
        BotCommand("broadcast", "Broadcast (admin)")
    ])

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
        app.add_handler(CommandHandler("stats", stats_command))
        app.add_handler(CommandHandler("broadcast", broadcast_command))
        app.add_handler(CallbackQueryHandler(lang_menu, pattern="lang"))
        app.add_handler(CallbackQueryHandler(set_lang, pattern="setlang_"))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_link))
        print("🤖 SmartDecode Bot is online.")
        await app.initialize()
        await app.start()
        await app.updater.start_polling()
        print("✅ Bot polling started.")

    try:
        asyncio.get_event_loop().create_task(runner())
        asyncio.get_event_loop().run_forever()
    except KeyboardInterrupt:
        print("🛑 Bot stopped manually.")
