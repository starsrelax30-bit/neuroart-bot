import os, asyncio, threading, requests, json, sqlite3, time as tm, logging
from datetime import date
from flask import Flask, request, jsonify
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton

BOT_TOKEN = os.environ.get('BOT_TOKEN', '')
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY', '')
HF_API_KEY = os.environ.get('HF_API_KEY', '')
OWNER_ID = int(os.environ.get('OWNER_ID', '0'))
WEBAPP_URL = os.environ.get('WEBAPP_URL', '')
CHANNEL_ID = os.environ.get('CHANNEL_ID', '')
CHANNEL_URL = os.environ.get('CHANNEL_URL', '')
OCR_API_URL = "https://api-inference.huggingface.co/models/microsoft/trocr-base-printed"
HEADERS = {"Authorization": f"Bearer {HF_API_KEY}"}
DB_NAME = "bot_stats.db"
FREE_LIMIT, REF_BONUS, NEW_USER_BONUS, DAILY_BONUS = 15, 10, 5, 2
PROMO_CODES = {"WELCOME": 5, "LATENT": 10, "NEURO": 15}
app = Flask(__name__)
ml = asyncio.new_event_loop()

def sl():
    asyncio.set_event_loop(ml)
    ml.run_forever()

threading.Thread(target=sl, daemon=True).start()

def db_exec(q, p=(), fetch=False, commit=False):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute(q, p)
    if commit: conn.commit()
    r = cur.fetchall() if fetch else None
    conn.close()
    return r

def idb():
    db_exec("CREATE TABLE IF NOT EXISTS stats (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, username TEXT, function TEXT, success INTEGER, date TEXT)", commit=True)
    db_exec("CREATE TABLE IF NOT EXISTS bans (user_id INTEGER PRIMARY KEY, reason TEXT, date TEXT)", commit=True)
    db_exec("CREATE TABLE IF NOT EXISTS admins (user_id INTEGER PRIMARY KEY, level TEXT DEFAULT 'admin')", commit=True)
    db_exec("CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, username TEXT, status TEXT DEFAULT 'free', generations_today INTEGER DEFAULT 0, bonus_generations INTEGER DEFAULT 0, total_generations INTEGER DEFAULT 0, last_reset TEXT, last_bonus TEXT, referrer_id INTEGER DEFAULT 0, referrals_count INTEGER DEFAULT 0)", commit=True)
    db_exec("CREATE TABLE IF NOT EXISTS promo_usage (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, promo_code TEXT, date TEXT)", commit=True)
    db_exec("CREATE TABLE IF NOT EXISTS chat_context (user_id INTEGER, role TEXT, content TEXT, timestamp REAL)", commit=True)

def lr(uid, un, fn, ok): db_exec("INSERT INTO stats (user_id, username, function, success, date) VALUES (?,?,?,?,datetime('now'))", (uid, un, fn, ok), commit=True)

def gu(uid, un=None):
    r = db_exec("SELECT status, generations_today, bonus_generations, total_generations, last_reset, last_bonus, referrer_id, referrals_count FROM users WHERE user_id=?", (uid,), fetch=True)
    if not r:
        db_exec("INSERT INTO users (user_id, username, status, generations_today, bonus_generations, total_generations, last_reset, last_bonus, referrer_id, referrals_count) VALUES (?,?,'free',0,0,0,date('now'),'2000-01-01',0,0)", (uid, un), commit=True)
        return {'status': 'free', 'generations_today': 0, 'bonus_generations': 0, 'total_generations': 0, 'referrer_id': 0, 'referrals_count': 0}
    return {'status': r[0][0], 'generations_today': r[0][1], 'bonus_generations': r[0][2], 'total_generations': r[0][3], 'last_reset': r[0][4], 'last_bonus': r[0][5], 'referrer_id': r[0][6], 'referrals_count': r[0][7]}

def uu(uid, st=None, ig=False, ab=0, sr=None, ir=False):
    if st: db_exec("UPDATE users SET status=? WHERE user_id=?", (st, uid), commit=True)
    if ig: db_exec("UPDATE users SET generations_today = generations_today + 1, total_generations = total_generations + 1 WHERE user_id=?", (uid,), commit=True)
    if ab: db_exec("UPDATE users SET bonus_generations = bonus_generations + ? WHERE user_id=?", (ab, uid), commit=True)
    if sr: db_exec("UPDATE users SET referrer_id=? WHERE user_id=?", (sr, uid), commit=True)
    if ir: db_exec("UPDATE users SET referrals_count = referrals_count + 1 WHERE user_id=?", (uid,), commit=True)

def rd(uid): db_exec("UPDATE users SET generations_today = 0, last_reset = date('now') WHERE user_id=?", (uid,), commit=True)
def sv(uid): uu(uid, st='vip')
def sf(uid): uu(uid, st='free')

def check_daily_bonus(uid):
    u = gu(uid)
    today = str(date.today())
    if u.get('last_bonus') != today: uu(uid, ab=DAILY_BONUS); db_exec("UPDATE users SET last_bonus=? WHERE user_id=?", (today, uid), commit=True); return True
    return False

def use_promo(uid, code):
    code = code.upper()
    if code in PROMO_CODES:
        if db_exec("SELECT id FROM promo_usage WHERE user_id=? AND promo_code=?", (uid, code), fetch=True): return False, "Уже использован."
        db_exec("INSERT INTO promo_usage (user_id, promo_code, date) VALUES (?,?,datetime('now'))", (uid, code), commit=True)
        uu(uid, ab=PROMO_CODES[code])
        return True, PROMO_CODES[code]
    return False, "Не найден."

def get_top_users(limit=5): return db_exec("SELECT username, total_generations FROM users ORDER BY total_generations DESC LIMIT ?", (limit,), fetch=True)

def get_user_total(uid):
    r = db_exec("SELECT total_generations FROM users WHERE user_id=?", (uid,), fetch=True)
    return r[0][0] if r else 0

def get_chat_context(uid):
    rows = db_exec("SELECT role, content FROM chat_context WHERE user_id=? ORDER BY timestamp ASC LIMIT 10", (uid,), fetch=True)
    return [{"role": r[0], "content": r[1]} for r in rows]

def save_chat_message(uid, role, content):
    db_exec("INSERT INTO chat_context (user_id, role, content, timestamp) VALUES (?,?,?,?)", (uid, role, content, tm.time()), commit=True)
    db_exec("DELETE FROM chat_context WHERE user_id=? AND id NOT IN (SELECT id FROM chat_context WHERE user_id=? ORDER BY timestamp DESC LIMIT 10)", (uid, uid), commit=True)

def gs():
    t = db_exec("SELECT COUNT(*), COUNT(DISTINCT user_id), SUM(success) FROM stats", fetch=True)[0]
    b = db_exec("SELECT COUNT(*) FROM bans", fetch=True)[0][0]
    v = db_exec("SELECT COUNT(*) FROM users WHERE status='vip'", fetch=True)[0][0]
    return {"total_requests": t[0] or 0, "unique_users": t[1] or 0, "successful": t[2] or 0, "bans": b, "vips": v}

def gru(n=10): return db_exec("SELECT DISTINCT user_id, username FROM stats ORDER BY id DESC LIMIT ?", (n,), fetch=True)

def ib(uid):
    r = db_exec("SELECT reason FROM bans WHERE user_id=?", (uid,), fetch=True)
    return r[0][0] if r else None

def ban(uid, r="Нарушение"): db_exec("INSERT OR REPLACE INTO bans (user_id, reason, date) VALUES (?,?,datetime('now'))", (uid, r), commit=True)
def ubn(uid): db_exec("DELETE FROM bans WHERE user_id=?", (uid,), commit=True)

def ia(uid):
    if uid == OWNER_ID: return "owner"
    r = db_exec("SELECT level FROM admins WHERE user_id=?", (uid,), fetch=True)
    return r[0][0] if r else None

def aa(uid, l='admin'): db_exec("INSERT OR REPLACE INTO admins (user_id, level) VALUES (?,?)", (uid, l), commit=True)
def ra(uid): db_exec("DELETE FROM admins WHERE user_id=?", (uid,), commit=True)

def gi(p): return None

def oi(img):
    r = requests.post(OCR_API_URL, headers=HEADERS, data=img)
    if r.status_code == 200:
        j = r.json()
        return j[0]['generated_text'] if isinstance(j, list) else str(j)
    return None

def ai(p, uid=None):
    if not GEMINI_API_KEY: return "❌ Ключ Gemini не установлен."
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
        r = requests.post(url, json={"contents": [{"parts": [{"text": p}]}]})
        if r.status_code == 200: return r.json()["candidates"][0]["content"]["parts"][0]["text"]
        return f"❌ Ошибка Gemini: {r.status_code}"
    except Exception as e: return f"❌ Сетевая ошибка: {e}"

def tr(t): return ai(f"Translate to English: {t}")

async def cs(uid):
    if not CHANNEL_ID: return True
    try:
        m = await bot.get_chat_member(chat_id=CHANNEL_ID, user_id=uid)
        return m.status not in ['left', 'kicked']
    except: return False

def check_limit(uid):
    u = gu(uid)
    if u['status'] == 'vip': return True
    if u.get('last_reset') != str(date.today()): rd(uid); u['generations_today'] = 0
    return not (u['generations_today'] >= FREE_LIMIT and u['bonus_generations'] <= 0)

def use_generation(uid):
    u = gu(uid)
    if u['bonus_generations'] > 0: uu(uid, ab=-1)
    else: uu(uid, ig=True)
    left = max(0, FREE_LIMIT - u['generations_today'])
    if u['status'] == 'free' and left <= 3 and left > 0:
        asyncio.run_coroutine_threadsafe(bot.send_message(uid, f"⚠️ Осталось {left} генераций. 💎 /vip"), ml)

user_modes, admin_waiting, text_styles = {}, {}, {}
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

mk = ReplyKeyboardMarkup(keyboard=[
    [KeyboardButton(text="📷 Распознать текст"), KeyboardButton(text="💬 Чат с ИИ")],
    [KeyboardButton(text="🌐 Перевод"), KeyboardButton(text="✍️ Тексты")],
    [KeyboardButton(text="💎 Купить VIP"), KeyboardButton(text="📊 Статус")],
    [KeyboardButton(text="📞 Поддержка"), KeyboardButton(text="👥 Рефералы")],
    [KeyboardButton(text="📢 Канал"), KeyboardButton(text="📈 Топ")]
], resize_keyboard=True)

admin_kb = ReplyKeyboardMarkup(keyboard=[
    [KeyboardButton(text="📊 Статистика"), KeyboardButton(text="👥 Пользователи")],
    [KeyboardButton(text="💎 VIP"), KeyboardButton(text="🚫 Бан")],
    [KeyboardButton(text="✅ Разбан"), KeyboardButton(text="📢 Рассылка")],
    [KeyboardButton(text="➕ Админ"), KeyboardButton(text="➖ Админ")],
    [KeyboardButton(text="🔙 Выход")]
], resize_keyboard=True)

@dp.message(Command("start"))
async def stc(msg: types.Message):
    if ib(msg.from_user.id): await msg.answer("⛔ Бан."); return
    user_modes.pop(msg.from_user.id, None)
    uid = msg.from_user.id
    bonus_given = check_daily_bonus(uid)
    if not await cs(msg.from_user.id):
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="📢 Подписаться", url=CHANNEL_URL)], [InlineKeyboardButton(text="✅ Подписался", callback_data="check_sub")]])
        await msg.answer("👋 Подпишитесь на канал:", reply_markup=kb)
        return
    args = msg.text.split()
    if len(args) > 1 and args[1].startswith("ref"):
        try:
            rid = int(args[1][3:])
            if rid != msg.from_user.id:
                u = gu(msg.from_user.id, msg.from_user.username)
                if not u.get('referrer_id'): uu(msg.from_user.id, ab=NEW_USER_BONUS, sr=rid); uu(rid, ab=REF_BONUS, ir=True)
                try: await bot.send_message(rid, f"🎉 +{REF_BONUS} генераций!")
                except: pass
        except: pass
    bonus_msg = "🎁 +2 бонусные генерации за вход!\n" if bonus_given else ""
    await msg.answer(f"⚡️ <b>Добро пожаловать в NeuroArt!</b> ⚡️\n\n{bonus_msg}🚀 Мы используем самые мощные нейросети:\n━━━━━━━━━━━━━━━━━━━\n🧠 Gemini 1.5 Flash | 👁️ Trocr\n━━━━━━━━━━━━━━━━━━━\n\n✨ Что я умею:\n📷 Распознавание | 💬 Чат | 🌐 Перевод | ✍️ Тексты\n\n💎 VIP — безлимит за 49 ₽/мес\n👥 Рефералы — +10 генераций за друга\n🎁 Бонус за вход — +2 генерации\n🎯 Промокоды — /promo КОД\n🆓 Бесплатно — {FREE_LIMIT} генераций в день\n\nВыберите действие 👇", parse_mode="HTML", reply_markup=mk)

@dp.message(Command("promo"))
async def promo_cmd(msg: types.Message):
    args = msg.text.split()
    if len(args) < 2: await msg.answer("🎯 /promo КОД"); return
    success, result = use_promo(msg.from_user.id, args[1])
    await msg.answer(f"🎉 +{result} бонусных генераций!" if success else f"❌ {result}")

@dp.callback_query(lambda c: c.data == "check_sub")
async def sub_ck(cb: types.CallbackQuery):
    if await cs(cb.from_user.id): await cb.message.delete(); await cb.message.answer("✅ Готово!", reply_markup=mk); await stc(cb.message)
    else: await cb.answer("❌ Не подписались!", show_alert=True)

@dp.message(Command("ref"))
async def ref_cmd(msg: types.Message):
    u = gu(msg.from_user.id)
    link = f"https://t.me/{(await bot.me()).username}?start=ref{msg.from_user.id}"
    await msg.answer(f"👥 Ссылка:\n<code>{link}</code>\nПриглашено: {u['referrals_count']}\nБонусов: {u['bonus_generations']}", parse_mode="HTML")

@dp.message(Command("vip"))
async def vip_cmd(msg: types.Message):
    await msg.answer("💎 <b>VIP-статус</b>\n\n• Безлимитные генерации\n• Приоритетная обработка\n\n💰 <b>Стоимость: 49 ₽ / 30 дней</b>\n⭐ <b>Оплата Telegram Stars:</b> 40 ⭐\n\nНажмите кнопку ниже:", parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⭐ Оплатить Stars (40)", pay=True)], [InlineKeyboardButton(text="✅ Я оплатил", callback_data="confirm_payment")], [InlineKeyboardButton(text="🔙 Назад", callback_data="cancel")]]))

@dp.callback_query(lambda c: c.data == "confirm_payment")
async def pay_cf(cb: types.CallbackQuery):
    if gu(cb.from_user.id)['status'] == 'vip': await cb.answer("Уже VIP!"); return
    try: await bot.send_message(OWNER_ID, f"💰 Оплата от @{cb.from_user.username or cb.from_user.id} ({cb.from_user.id})")
    except: pass
    await cb.message.edit_text("✅ Заявка отправлена!")

@dp.callback_query(lambda c: c.data == "cancel")
async def pay_cc(cb: types.CallbackQuery): await cb.message.delete()

@dp.message(Command("give_vip"))
async def gv_cmd(msg: types.Message):
    if not ia(msg.from_user.id): return
    p = msg.text.split()
    if len(p) < 2: return
    uid = int(p[1]) if p[1].isdigit() else None
    if uid: sv(uid); await msg.answer(f"💎 {uid} → VIP")

@dp.message(Command("admin"))
async def adm_cmd(msg: types.Message):
    if not ia(msg.from_user.id): return
    admin_waiting.pop(msg.from_user.id, None)
    await msg.answer("🔧 Админ-панель", reply_markup=admin_kb)

@dp.message(F.text == "📊 Статистика")
async def a_st(msg: types.Message):
    if not ia(msg.from_user.id): return
    s = gs()
    await msg.answer(f"📊 Запросов: {s['total_requests']}\n👥 Пользователей: {s['unique_users']}\n✅ Успешных: {s['successful']}\n💎 VIP: {s['vips']}\n🚫 Банов: {s['bans']}")

@dp.message(F.text == "👥 Пользователи")
async def a_us(msg: types.Message):
    if not ia(msg.from_user.id): return
    u = gru(10)
    await msg.answer("\n".join([f"• `{uid}` — @{un or 'нет'}" for uid, un in u]) or "Нет данных.", parse_mode="Markdown")

@dp.message(F.text.in_(["💎 VIP", "🚫 Бан", "✅ Разбан", "➕ Админ", "➖ Админ", "📢 Рассылка"]))
async def a_prompt(msg: types.Message):
    if not ia(msg.from_user.id): return
    if msg.text == "📢 Рассылка": admin_waiting[msg.from_user.id] = {'action': 'broadcast'}; await msg.answer("Введите текст рассылки:")
    else: admin_waiting[msg.from_user.id] = {'action': msg.text}; await msg.answer("Введите ID пользователя:")

  @dp.message()
async def handle_all(msg: types.Message):
    uid = msg.from_user.id
    if ib(uid): await msg.answer("⛔ Вы заблокированы."); return
    if uid in admin_waiting:
        data = admin_waiting.pop(uid)
        action = data['action']
        if action == 'broadcast':
            us = db_exec("SELECT DISTINCT user_id FROM stats", fetch=True)
            cnt = 0
            for (uid_,) in us:
                try: await bot.send_message(uid_, f"📢 {msg.text}"); cnt += 1
                except: pass
            await msg.answer(f"📢 Отправлено {cnt} получателям.")
            return
        target_id = int(msg.text) if msg.text.strip().isdigit() else None
        if not target_id: await msg.answer("❌ Неверный ID."); return
        if action == "💎 VIP": sv(target_id); await msg.answer(f"💎 {target_id} → VIP")
        elif action == "🚫 Бан": ban(target_id); await msg.answer(f"🚫 {target_id} забанен")
        elif action == "✅ Разбан": ubn(target_id); await msg.answer(f"✅ {target_id} разбанен")
        elif action == "➕ Админ": aa(target_id); await msg.answer(f"✅ {target_id} → админ")
        elif action == "➖ Админ": ra(target_id); await msg.answer(f"➖ {target_id} → не админ")
        return
    if msg.text == "🔙 Выход" and ia(uid): admin_waiting.pop(uid, None); await msg.answer("Вы вышли из админ-панели.", reply_markup=mk); return
    if msg.text == "📢 Канал": await msg.answer(f"📢 Подпишитесь: {CHANNEL_URL}"); return
    if msg.text == "📈 Топ":
        top = get_top_users(5)
        text = "📈 Топ:\n"
        for i, (un, t) in enumerate(top, 1): text += f"{i}. {un or 'Аноним'} — {t}\n"
        await msg.answer(text); return
    if msg.text == "📷 Распознать текст": user_modes[uid] = 'ocr'; await msg.answer("📷 Отправьте фото:"); return
    if msg.text == "💬 Чат с ИИ": user_modes[uid] = 'chat'; save_chat_message(uid, "system", "Новый диалог"); await msg.answer("💬 Чат активирован. Пишите!"); return
    if msg.text == "🌐 Перевод": user_modes[uid] = 'translate'; await msg.answer("🌐 Введите текст:"); return
    if msg.text == "✍️ Тексты": user_modes[uid] = 'text_style'; await msg.answer("✍️ Выберите стиль:\n• Деловой\n• Художественный\n• Разговорный\n\nНапишите стиль и тему.\nПример: <i>Деловой, отчёт</i>", parse_mode="HTML"); return
    if msg.text == "📊 Статус":
        u = gu(uid); l = max(0, FREE_LIMIT - u['generations_today']); total = get_user_total(uid)
        await msg.answer(f"📊 {'💎 VIP' if u['status']=='vip' else '🆓 Бесплатно'}\n🎨 {'Безлимит' if u['status']=='vip' else f'{l}/{FREE_LIMIT}'}\n🎁 Бонусных: {u['bonus_generations']}\n📈 Всего: {total} генераций"); return
    if msg.text == "💎 Купить VIP": await vip_cmd(msg); return
    if msg.text == "📞 Поддержка": user_modes[uid] = 'support'; await msg.answer("📞 Выберите раздел (можно прикрепить скриншот):", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🤝 Сотрудничество", callback_data="support_coop")], [InlineKeyboardButton(text="📢 Реклама", callback_data="support_ads")], [InlineKeyboardButton(text="❓ Вопрос", callback_data="support_question")]])); return
    if msg.text == "👥 Рефералы": await ref_cmd(msg); return
    mode = user_modes.get(uid)
    if mode == 'support' and msg.photo:
        try: await bot.send_photo(OWNER_ID, msg.photo[-1].file_id, caption=f"📩 Скриншот от @{msg.from_user.username or msg.from_user.id} ({msg.from_user.id})")
        except: pass
        await msg.answer("✅ Скриншот отправлен! Опишите вопрос текстом."); return
    if mode == 'support': await msg.answer("✅ Сообщение отправлено!"); user_modes.pop(uid, None); return
    if mode == 'ocr' and msg.photo:
        if not check_limit(uid): await msg.answer(f"🚫 Лимит. /vip"); return
        await msg.answer("🔍 Распознаю..."); ph = msg.photo[-1]; fl = await bot.get_file(ph.file_id); ibs = await bot.download_file(fl.file_path)
        txt = oi(ibs.read())
        if txt: await msg.answer(f"📝 <pre>{txt}</pre>", parse_mode="HTML"); lr(uid, msg.from_user.username, 'ocr', 1); use_generation(uid)
        else: await msg.answer("❌ Не удалось."); lr(uid, msg.from_user.username, 'ocr', 0)
        user_modes.pop(uid, None)
    elif mode == 'chat':
        if not check_limit(uid): await msg.answer(f"🚫 Лимит. /vip"); return
        save_chat_message(uid, "user", msg.text); context = get_chat_context(uid)
        prompt = "Контекст:\n" + "\n".join([f"{m['role']}: {m['content']}" for m in context])
        answer = ai(prompt)
        if not answer.startswith("❌"): save_chat_message(uid, "assistant", answer); use_generation(uid)
        await msg.answer(answer)
    elif mode == 'translate':
        if not check_limit(uid): await msg.answer(f"🚫 Лимит. /vip"); return
        translation = tr(msg.text)
        if not translation.startswith("❌"): use_generation(uid)
        await msg.answer(translation); user_modes.pop(uid, None)
    elif mode == 'text_style':
        text_styles[uid] = msg.text; user_modes[uid] = 'text_content'
        await msg.answer(f"✍️ Стиль: {msg.text}\nТеперь напишите тему:")
    elif mode == 'text_content':
        if not check_limit(uid): await msg.answer(f"🚫 Лимит. /vip"); return
        style = text_styles.pop(uid, ""); m = await msg.answer("✍️ Генерирую...")
        txt = ai(f"Напиши текст в стиле: {style}. Тема: {msg.text}. Сделай качественно.")
        if txt and not txt.startswith("❌"): await msg.answer(txt); lr(uid, msg.from_user.username, 'text', 1); use_generation(uid); await m.delete()
        else: await m.edit_text("❌ Ошибка."); lr(uid, msg.from_user.username, 'text', 0)
        user_modes.pop(uid, None)

@app.route('/webhook', methods=['POST'])
def fw():
    future = asyncio.run_coroutine_threadsafe(dp.feed_webhook_update(bot, request.get_json()), ml)
    future.result()
    return jsonify({"status": "ok"})

if __name__ == "__main__":
    idb()
    port = int(os.environ.get('PORT', 8080))
    async def sw():
        try: await bot.delete_webhook(); await bot.set_webhook(f"{WEBAPP_URL}/webhook"); logging.info("OK")
        except Exception as e: logging.error(f"Error: {e}")
    asyncio.run_coroutine_threadsafe(sw(), ml)
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)
