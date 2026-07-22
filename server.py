import os
import asyncio
import threading
import requests
import json
import sqlite3
import base64
from datetime import date
from flask import Flask, request, jsonify, render_template
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton

# ---------- ВСЕ СЕКРЕТЫ ИЗ ПЕРЕМЕННЫХ ОКРУЖЕНИЯ ----------
BOT_TOKEN = os.environ.get('BOT_TOKEN', '')
HF_API_KEY = os.environ.get('HF_API_KEY', '')
OWNER_ID = int(os.environ.get('OWNER_ID', '0'))
WEBAPP_URL = os.environ.get('WEBAPP_URL', 'https://neuroart-bot.onrender.com')

API_URL = "https://api-inference.huggingface.co/models/ByteDance/SDXL-Lightning"
HEADERS = {"Authorization": f"Bearer {HF_API_KEY}"}
DB_NAME = "bot_stats.db"
FREE_LIMIT = 15

app = Flask(__name__)
main_loop = asyncio.new_event_loop()

def start_loop():
    asyncio.set_event_loop(main_loop)
    main_loop.run_forever()

threading.Thread(target=start_loop, daemon=True).start()

# ---------- БАЗА ДАННЫХ ----------
def init_db():
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("""CREATE TABLE IF NOT EXISTS stats (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, username TEXT, prompt TEXT, success INTEGER, date TEXT)""")
    cur.execute("""CREATE TABLE IF NOT EXISTS bans (user_id INTEGER PRIMARY KEY, reason TEXT, date TEXT)""")
    cur.execute("""CREATE TABLE IF NOT EXISTS admins (user_id INTEGER PRIMARY KEY, level TEXT DEFAULT 'admin')""")
    cur.execute("""CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, username TEXT, status TEXT DEFAULT 'free', generations_today INTEGER DEFAULT 0, last_reset TEXT)""")
    conn.commit()
    conn.close()

def log_request(user_id, username, prompt, success):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("INSERT INTO stats (user_id, username, prompt, success, date) VALUES (?, ?, ?, ?, datetime('now'))", (user_id, username, prompt[:200], success))
    conn.commit()
    conn.close()

def get_user(user_id, username=None):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT status, generations_today, last_reset FROM users WHERE user_id=?", (user_id,))
    row = cur.fetchone()
    if not row:
        cur.execute("INSERT INTO users (user_id, username, status, generations_today, last_reset) VALUES (?, ?, 'free', 0, date('now'))", (user_id, username))
        conn.commit()
        return {'status': 'free', 'generations_today': 0}
    conn.close()
    return {'status': row[0], 'generations_today': row[1], 'last_reset': row[2]}

def update_user(user_id, status=None, increment_gen=False):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    if status: cur.execute("UPDATE users SET status=? WHERE user_id=?", (status, user_id))
    if increment_gen: cur.execute("UPDATE users SET generations_today = generations_today + 1 WHERE user_id=?", (user_id,))
    conn.commit()
    conn.close()

def reset_daily_generations(user_id):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("UPDATE users SET generations_today = 0, last_reset = date('now') WHERE user_id=?", (user_id,))
    conn.commit()
    conn.close()

def set_vip(user_id): update_user(user_id, status='vip')
def set_free(user_id): update_user(user_id, status='free')

def get_stats():
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*), COUNT(DISTINCT user_id), SUM(success) FROM stats")
    total, users, success = cur.fetchone()
    cur.execute("SELECT COUNT(*) FROM bans"); bans = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM users WHERE status='vip'"); vips = cur.fetchone()[0]
    conn.close()
    return {"total_requests": total or 0, "unique_users": users or 0, "successful": success or 0, "bans": bans, "vips": vips}

def get_recent_users(limit=10):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT DISTINCT user_id, username FROM stats ORDER BY id DESC LIMIT ?", (limit,))
    users = cur.fetchall(); conn.close()
    return users

def is_banned(user_id):
    conn = sqlite3.connect(DB_NAME); cur = conn.cursor()
    cur.execute("SELECT reason FROM bans WHERE user_id=?", (user_id,)); row = cur.fetchone(); conn.close()
    return row[0] if row else None

def ban_user(user_id, reason="Нарушение"):
    conn = sqlite3.connect(DB_NAME); cur = conn.cursor()
    cur.execute("INSERT OR REPLACE INTO bans (user_id, reason, date) VALUES (?, ?, datetime('now'))", (user_id, reason)); conn.commit(); conn.close()

def unban_user(user_id):
    conn = sqlite3.connect(DB_NAME); cur = conn.cursor()
    cur.execute("DELETE FROM bans WHERE user_id=?", (user_id,)); conn.commit(); conn.close()

def is_admin(user_id):
    if user_id == OWNER_ID: return "owner"
    conn = sqlite3.connect(DB_NAME); cur = conn.cursor()
    cur.execute("SELECT level FROM admins WHERE user_id=?", (user_id,)); row = cur.fetchone(); conn.close()
    return row[0] if row else None

def add_admin(user_id, level='admin'):
    conn = sqlite3.connect(DB_NAME); cur = conn.cursor()
    cur.execute("INSERT OR REPLACE INTO admins (user_id, level) VALUES (?, ?)", (user_id, level)); conn.commit(); conn.close()

def remove_admin(user_id):
    conn = sqlite3.connect(DB_NAME); cur = conn.cursor()
    cur.execute("DELETE FROM admins WHERE user_id=?", (user_id,)); conn.commit(); conn.close()

def save_action(user_id, action):
    with open(f"/tmp/admin_{user_id}.json", "w") as f: json.dump({"admin_action": action}, f)

# ---------- ГЕНЕРАЦИЯ ----------
def generate_image(prompt):
    full_prompt = f"{prompt}, 4K, highly detailed, cinematic, masterpiece"
    payload = {"inputs": full_prompt, "parameters": {"num_inference_steps": 4, "guidance_scale": 0, "width": 1024, "height": 1024}}
    response = requests.post(API_URL, headers=HEADERS, json=payload)
    if response.status_code == 200: return response.content
    elif 'loading' in response.text.lower():
        import time; time.sleep(15)
        response = requests.post(API_URL, headers=HEADERS, json=payload)
        if response.status_code == 200: return response.content
    return None

# ---------- HTML ИНТЕРФЕЙС ----------
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/generate', methods=['POST'])
def api_generate():
    data = request.json; prompt = data.get('prompt', '')
    if not prompt: return jsonify({"error": "no prompt"})
    img_bytes = generate_image(prompt)
    if img_bytes:
        return jsonify({"image": base64.b64encode(img_bytes).decode('utf-8')})
    return jsonify({"error": "generation failed"})

# ---------- TELEGRAM BOT ----------
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    if is_banned(message.from_user.id): await message.answer("⛔ Вы заблокированы."); return
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🎨 Открыть генератор", web_app=types.WebAppInfo(url=WEBAPP_URL))]])
    await message.answer("🎨 *NeuroArt*\nГенерирую крутые картинки!\n\n/vip — VIP-статус\n/status — лимиты", parse_mode="Markdown", reply_markup=kb)

@dp.message(Command("vip"))
async def cmd_vip(message: types.Message):
    user = get_user(message.from_user.id)
    if user['status'] == 'vip': await message.answer("💎 У вас уже VIP!"); return
    await message.answer("💎 VIP: безлимит, приоритет.\n199 ₽/мес.\nДля покупки — напишите админу.")

@dp.message(Command("status"))
async def cmd_status(message: types.Message):
    user = get_user(message.from_user.id)
    left = max(0, FREE_LIMIT - user['generations_today'])
    emoji = "💎 VIP" if user['status'] == 'vip' else "🆓 Обычный"
    limit_text = "Безлимит" if user['status'] == 'vip' else f"{left}/{FREE_LIMIT}"
    await message.answer(f"📊 Статус: {emoji}\n🎨 Генераций сегодня: {limit_text}")

@dp.message(Command("admin"))
async def cmd_admin(message: types.Message):
    if not is_admin(message.from_user.id): return
    level = "👑 Владелец" if message.from_user.id == OWNER_ID else "🔧 Админ"
    kb = [
        [KeyboardButton(text="📊 Статистика"), KeyboardButton(text="👥 Пользователи")],
        [KeyboardButton(text="💎 VIP"), KeyboardButton(text="🆓 Снять VIP")],
        [KeyboardButton(text="🚫 Бан"), KeyboardButton(text="✅ Разбан")],
        [KeyboardButton(text="📢 Рассылка")],
    ]
    if message.from_user.id == OWNER_ID: kb.append([KeyboardButton(text="➕ Админ"), KeyboardButton(text="➖ Снять админа")])
    kb.append([KeyboardButton(text="/start")])
    await message.answer(f"🔧 Админ-панель | {level}", reply_markup=ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True))

@dp.message(lambda m: is_admin(m.from_user.id) and m.text == "📊 Статистика")
async def a_stats(message: types.Message):
    s = get_stats()
    await message.answer(f"📊 Запросов: {s['total_requests']}\n👥 Пользователей: {s['unique_users']}\n✅ Успешных: {s['successful']}\n💎 VIP: {s['vips']}\n🚫 Банов: {s['bans']}")

@dp.message(lambda m: is_admin(m.from_user.id) and m.text == "👥 Пользователи")
async def a_users(message: types.Message):
    users = get_recent_users(10)
    if not users: await message.answer("Нет данных."); return
    text = "👥:\n" + "\n".join([f"• `{uid}` — @{uname or 'нет'}" for uid, uname in users])
    await message.answer(text, parse_mode="Markdown")

@dp.message(lambda m: is_admin(m.from_user.id) and m.text in ["💎 VIP", "🆓 Снять VIP", "🚫 Бан", "✅ Разбан", "📢 Рассылка", "➕ Админ", "➖ Снять админа"])
async def admin_prompts(message: types.Message):
    prompts = {"💎 VIP": "vip", "🆓 Снять VIP": "free", "🚫 Бан": "ban", "✅ Разбан": "unban", "📢 Рассылка": "broadcast", "➕ Админ": "add_admin", "➖ Снять админа": "remove_admin"}
    await message.answer("Введите ID пользователя:" if message.text != "📢 Рассылка" else "Введите текст рассылки:")
    save_action(message.from_user.id, prompts[message.text])

@dp.message(lambda m: is_admin(m.from_user.id))
async def admin_handle(message: types.Message):
    try:
        with open(f"/tmp/admin_{message.from_user.id}.json", "r") as f: state = json.load(f)
    except: return
    action = state["admin_action"]; text = message.text.strip()
    if action == "broadcast":
        conn = sqlite3.connect(DB_NAME); cur = conn.cursor(); cur.execute("SELECT DISTINCT user_id FROM stats"); users = cur.fetchall(); conn.close()
        count = 0
        for (uid,) in users:
            try: await bot.send_message(uid, f"📢 {text}"); count += 1
            except: pass
        await message.answer(f"📢 Отправлено {count}.")
    else:
        parts = text.split(maxsplit=1); uid = int(parts[0]) if parts[0].isdigit() else None
        if not uid: await message.answer("❌ ID неверный."); return
        if action == "ban": ban_user(uid, parts[1] if len(parts)>1 else "Нарушение"); await message.answer(f"🚫 {uid} забанен.")
        elif action == "unban": unban_user(uid); await message.answer(f"✅ {uid} разбанен.")
        elif action == "vip": set_vip(uid); await message.answer(f"💎 {uid} → VIP.")
        elif action == "free": set_free(uid); await message.answer(f"🆓 {uid} → Обычный.")
        elif action == "add_admin": add_admin(uid); await message.answer(f"✅ {uid} — админ.")
        elif action == "remove_admin": remove_admin(uid); await message.answer(f"➖ {uid} — не админ.")
    os.remove(f"/tmp/admin_{message.from_user.id}.json")

@dp.message()
async def handle_message(message: types.Message):
    if is_banned(message.from_user.id): await message.answer("⛔ Вы заблокированы."); return
    prompt = message.text.strip()
    if not prompt or len(prompt) < 3: return
    user = get_user(message.from_user.id)
    if user['status'] == 'free':
        if user.get('last_reset') != str(date.today()): reset_daily_generations(message.from_user.id); user['generations_today'] = 0
        if user['generations_today'] >= FREE_LIMIT:
            await message.answer(f"🚫 Лимит ({FREE_LIMIT}/день).\n💎 VIP: /vip"); return
    msg = await message.answer("🎨 Генерирую...")
    img = generate_image(prompt)
    if img:
        await message.answer_photo(photo=types.BufferedInputFile(img, filename="img.png"), caption=f"🎨 *{prompt[:200]}*", parse_mode="Markdown")
        log_request(message.from_user.id, message.from_user.username, prompt, 1); update_user(message.from_user.id, increment_gen=True); await msg.delete()
    else:
        await msg.edit_text("❌ Ошибка."); log_request(message.from_user.id, message.from_user.username, prompt, 0)

# ---------- WEBHOOK ----------
@app.route('/webhook', methods=['GET', 'POST'])
def flask_webhook():
    if request.method == 'GET': return jsonify({"status": "ok"})
    future = asyncio.run_coroutine_threadsafe(dp.feed_webhook_update(bot, request.get_json()), main_loop)
    future.result(); return jsonify({"status": "ok"})

if __name__ == "__main__":
    init_db()
    port = int(os.environ.get('PORT', 8080))
    async def set_webhook():
        try: await bot.delete_webhook(); await bot.set_webhook(f"{WEBAPP_URL}/webhook"); print("OK")
        except Exception as e: print(f"Error: {e}")
    asyncio.run_coroutine_threadsafe(set_webhook(), main_loop)
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)
