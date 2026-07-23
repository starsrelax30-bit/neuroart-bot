import os, asyncio, threading, requests, json, sqlite3, time as tm, logging
from datetime import date
from flask import Flask, request, jsonify
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

BOT_TOKEN = os.environ.get('BOT_TOKEN', '')
HF_API_KEY = os.environ.get('HF_API_KEY', '')
OPENROUTER_API_KEY = os.environ.get('OPENROUTER_API_KEY', '')
OWNER_ID = int(os.environ.get('OWNER_ID', '0'))
WEBAPP_URL = os.environ.get('WEBAPP_URL', '')
CHANNEL_ID = os.environ.get('CHANNEL_ID', '')
CHANNEL_URL = os.environ.get('CHANNEL_URL', '')
IMAGE_API_URL = "https://api-inference.huggingface.co/models/ByteDance/SDXL-Lightning"
OCR_API_URL = "https://api-inference.huggingface.co/models/microsoft/trocr-base-printed"
HEADERS = {"Authorization": f"Bearer {HF_API_KEY}"}
DB_NAME = "bot_stats.db"
FREE_LIMIT = 15
REF_BONUS = 10
NEW_USER_BONUS = 5
MODEL = "google/gemini-2.0-flash-lite"

app = Flask(__name__)
ml = asyncio.new_event_loop()

def sl():
    asyncio.set_event_loop(ml)
    ml.run_forever()

threading.Thread(target=sl, daemon=True).start()

def db_exec(query, params=(), fetch=False, commit=False):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute(query, params)
    if commit: conn.commit()
    result = cur.fetchall() if fetch else None
    conn.close()
    return result

def idb():
    db_exec("CREATE TABLE IF NOT EXISTS stats (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, username TEXT, function TEXT, success INTEGER, date TEXT)", commit=True)
    db_exec("CREATE TABLE IF NOT EXISTS bans (user_id INTEGER PRIMARY KEY, reason TEXT, date TEXT)", commit=True)
    db_exec("CREATE TABLE IF NOT EXISTS admins (user_id INTEGER PRIMARY KEY, level TEXT DEFAULT 'admin')", commit=True)
    db_exec("CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, username TEXT, status TEXT DEFAULT 'free', generations_today INTEGER DEFAULT 0, bonus_generations INTEGER DEFAULT 0, last_reset TEXT, referrer_id INTEGER DEFAULT 0, referrals_count INTEGER DEFAULT 0)", commit=True)

def lr(uid, un, fn, ok):
    db_exec("INSERT INTO stats (user_id, username, function, success, date) VALUES (?,?,?,?,datetime('now'))", (uid, un, fn, ok), commit=True)

def gu(uid, un=None):
    r = db_exec("SELECT status, generations_today, bonus_generations, last_reset, referrer_id, referrals_count FROM users WHERE user_id=?", (uid,), fetch=True)
    if not r:
        db_exec("INSERT INTO users (user_id, username, status, generations_today, bonus_generations, last_reset, referrer_id, referrals_count) VALUES (?,?,'free',0,0,date('now'),0,0)", (uid, un), commit=True)
        return {'status': 'free', 'generations_today': 0, 'bonus_generations': 0, 'referrer_id': 0, 'referrals_count': 0}
    return {'status': r[0][0], 'generations_today': r[0][1], 'bonus_generations': r[0][2], 'last_reset': r[0][3], 'referrer_id': r[0][4], 'referrals_count': r[0][5]}

def uu(uid, st=None, ig=False, ab=0, sr=None, ir=False):
    if st: db_exec("UPDATE users SET status=? WHERE user_id=?", (st, uid), commit=True)
    if ig: db_exec("UPDATE users SET generations_today = generations_today + 1 WHERE user_id=?", (uid,), commit=True)
    if ab: db_exec("UPDATE users SET bonus_generations = bonus_generations + ? WHERE user_id=?", (ab, uid), commit=True)
    if sr: db_exec("UPDATE users SET referrer_id=? WHERE user_id=?", (sr, uid), commit=True)
    if ir: db_exec("UPDATE users SET referrals_count = referrals_count + 1 WHERE user_id=?", (uid,), commit=True)

def rd(uid): db_exec("UPDATE users SET generations_today = 0, last_reset = date('now') WHERE user_id=?", (uid,), commit=True)
def sv(uid): uu(uid, st='vip')
def sf(uid): uu(uid, st='free')

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

def sac(uid, a):
    with open(f"/tmp/admin_{uid}.json", "w") as f: json.dump({"admin_action": a}, f)

def gi(p):
    fp = f"{p}, 4K, highly detailed, cinematic, masterpiece"
    pl = {"inputs": fp, "parameters": {"num_inference_steps": 4, "guidance_scale": 0, "width": 1024, "height": 1024}}
    r = requests.post(IMAGE_API_URL, headers=HEADERS, json=pl)
    if r.status_code == 200: return r.content
    if 'loading' in r.text.lower(): tm.sleep(15); r = requests.post(IMAGE_API_URL, headers=HEADERS, json=pl)
    return r.content if r.status_code == 200 else None

def oi(img):
    r = requests.post(OCR_API_URL, headers=HEADERS, data=img)
    if r.status_code == 200:
        j = r.json()
        return j[0]['generated_text'] if isinstance(j, list) else str(j)
    return None

def ai(p):
    if not OPENROUTER_API_KEY: return "OpenRouter API key not set."
    try:
        r = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}", "Content-Type": "application/json"},
            json={"model": MODEL, "messages": [{"role": "user", "content": p}]}
        )
        if r.status_code == 200:
            return r.json()["choices"][0]["message"]["content"]
        return f"Ошибка API: {r.status_code}"
    except Exception as e:
        return f"Ошибка: {e}"

def tr(t):
    return ai(f"Translate to English: {t}")

async def cs(uid):
    if not CHANNEL_ID: return True
    try:
        m = await bot.get_chat_member(chat_id=CHANNEL_ID, user_id=uid)
        return m.status not in ['left', 'kicked']
    except: return False

class UM(StatesGroup):
    wp = State()
    wo = State()
    wc = State()
    wt = State()
    wx = State()

storage = MemoryStorage()
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=storage)

mk = ReplyKeyboardMarkup(keyboard=[
    [KeyboardButton(text="🎨 Генерация")],
    [KeyboardButton(text="📷 Распознать текст"), KeyboardButton(text="💬 Чат с ИИ")],
    [KeyboardButton(text="🌐 Перевод"), KeyboardButton(text="✍️ Тексты")],
    [KeyboardButton(text="💎 Купить VIP")],
    [KeyboardButton(text="📊 Статус"), KeyboardButton(text="📞 Поддержка")],
    [KeyboardButton(text="👥 Рефералы")]
], resize_keyboard=True)

@dp.message(Command("start"))
async def stc(msg: types.Message, state: FSMContext):
    if ib(msg.from_user.id): await msg.answer("⛔ Бан."); return
    await state.clear()
    if not await cs(msg.from_user.id):
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📢 Подписаться", url=CHANNEL_URL)],
            [InlineKeyboardButton(text="✅ Подписался", callback_data="check_sub")]
        ])
        await msg.answer("👋 Подпишитесь на канал:", reply_markup=kb)
        return
    args = msg.text.split()
    if len(args) > 1 and args[1].startswith("ref"):
        try:
            rid = int(args[1][3:])
            if rid != msg.from_user.id:
                u = gu(msg.from_user.id, msg.from_user.username)
                if not u.get('referrer_id'):
                    uu(msg.from_user.id, ab=NEW_USER_BONUS, sr=rid)
                    uu(rid, ab=REF_BONUS, ir=True)
                    try: await bot.send_message(rid, f"🎉 +{REF_BONUS} генераций!")
                    except: pass
        except: pass
    await msg.answer(
        "⚡️ <b>Добро пожаловать в NeuroArt!</b> ⚡️\n\n"
        "🚀 Мы используем самые мощные нейросети:\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        "🧠 Gemini Flash | 🎨 NanoBanana | 👁️ Trocr | 💎 Stable Diffusion XL\n"
        "🎯 Grok | 🔮 Claude 3 | 🌪️ Flux Pro | 🤖 ChatGPT\n"
        "━━━━━━━━━━━━━━━━━━━\n\n"
        "✨ Что я умею:\n"
        "🎨 Картинки | 📷 Распознавание | 💬 Чат | 🌐 Перевод | ✍️ Тексты\n\n"
        "💎 VIP — безлимит за 49 ₽/мес\n"
        "👥 Рефералы — +10 генераций за друга\n"
        "🎁 Розыгрыши в нашем канале\n"
        "🆓 Бесплатно — 15 генераций в день\n"
        "🏆 Точность — 99% | ⚡️ Ответ — 5–15 сек\n\n"
        "Выберите действие 👇",
        parse_mode="HTML", reply_markup=mk
    )

@dp.callback_query(lambda c: c.data == "check_sub")
async def sub_ck(cb: types.CallbackQuery, state: FSMContext):
    if await cs(cb.from_user.id):
        await cb.message.delete()
        await cb.message.answer("✅ Готово!", reply_markup=mk)
        await stc(cb.message, state)
    else: await cb.answer("❌ Не подписались!", show_alert=True)

@dp.message(Command("ref"))
async def ref_cmd(msg: types.Message):
    u = gu(msg.from_user.id)
    link = f"https://t.me/{(await bot.me()).username}?start=ref{msg.from_user.id}"
    await msg.answer(f"👥 Ссылка:\n<code>{link}</code>\nПриглашено: {u['referrals_count']}\nБонусов: {u['bonus_generations']}", parse_mode="HTML")

@dp.message(F.text == "👥 Рефералы")
async def ref_btn(msg: types.Message): await ref_cmd(msg)

@dp.message(Command("vip"))
async def vip_cmd(msg: types.Message):
    await msg.answer(
        "💎 <b>VIP-статус</b>\n\n"
        "• Безлимитные генерации\n"
        "• Приоритетная обработка\n\n"
        "💰 <b>Стоимость: 49 ₽ / 30 дней</b>\n"
        "⭐ <b>Оплата Telegram Stars:</b> 40 ⭐\n\n"
        "Нажмите кнопку ниже для оплаты:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⭐ Оплатить Stars (40)", pay=True)],
            [InlineKeyboardButton(text="✅ Я оплатил", callback_data="confirm_payment")],
            [InlineKeyboardButton(text="🔙 Назад", callback_data="cancel")]
        ])
    )

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
    kb = [[KeyboardButton(t) for t in ["📊 Статистика", "👥 Пользователи", "💎 VIP", "🚫 Бан", "✅ Разбан", "📢 Рассылка"]]]
    if msg.from_user.id == OWNER_ID: kb.append([KeyboardButton("➕ Админ"), KeyboardButton("➖ Админ")])
    await msg.answer("🔧 Админ-панель", reply_markup=ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True))

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

@dp.message(F.text.in_(["💎 VIP", "🚫 Бан", "✅ Разбан", "📢 Рассылка", "➕ Админ", "➖ Админ"]))
async def a_pr(msg: types.Message):
    if not ia(msg.from_user.id): return
    d = {"💎 VIP": "vip", "🚫 Бан": "ban", "✅ Разбан": "unban", "📢 Рассылка": "broadcast", "➕ Админ": "add_admin", "➖ Админ": "remove_admin"}
    await msg.answer("Введите ID:" if msg.text != "📢 Рассылка" else "Текст рассылки:")
    sac(msg.from_user.id, d[msg.text])

@dp.message(lambda m: ia(m.from_user.id) is not None)
async def a_hd(msg: types.Message):
    if not ia(msg.from_user.id): return
    try:
        with open(f"/tmp/admin_{msg.from_user.id}.json") as f: a = json.load(f)['admin_action']
    except: return
    t = msg.text.strip()
    if a == "broadcast":
        us = db_exec("SELECT DISTINCT user_id FROM stats", fetch=True)
        cnt = 0
        for (uid,) in us:
            try: await bot.send_message(uid, f"📢 {t}"); cnt += 1
            except: pass
        await msg.answer(f"📢 {cnt} получателей.")
    else:
        parts = t.split(maxsplit=1)
        uid = int(parts[0]) if parts[0].isdigit() else None
        if not uid: await msg.answer("❌ Неверный ID."); return
        if a == "ban": ban(uid, parts[1] if len(parts) > 1 else "Нарушение"); await msg.answer(f"🚫 {uid}")
        elif a == "unban": ubn(uid); await msg.answer(f"✅ {uid}")
        elif a == "vip": sv(uid); await msg.answer(f"💎 {uid} → VIP")
        elif a == "add_admin": aa(uid); await msg.answer(f"✅ {uid} — админ")
        elif a == "remove_admin": ra(uid); await msg.answer(f"➖ {uid} — не админ")
    os.remove(f"/tmp/admin_{msg.from_user.id}.json")

@dp.message(F.text == "🎨 Генерация")
async def md_img(msg: types.Message, st: FSMContext): await st.set_state(UM.wp); await msg.answer("🎨 Опишите картинку:")

@dp.message(F.text == "📷 Распознать текст")
async def md_ocr(msg: types.Message, st: FSMContext): await st.set_state(UM.wo); await msg.answer("📷 Отправьте фото:")

@dp.message(F.text == "💬 Чат с ИИ")
async def md_cht(msg: types.Message, st: FSMContext): await st.set_state(UM.wc); await msg.answer("💬 Чат активирован. Пишите!")

@dp.message(F.text == "🌐 Перевод")
async def md_tr(msg: types.Message, st: FSMContext): await st.set_state(UM.wt); await msg.answer("🌐 Введите текст:")

@dp.message(F.text == "✍️ Тексты")
async def md_txt(msg: types.Message, st: FSMContext):
    await st.set_state(UM.wx)
    await msg.answer("✍️ <b>Генератор текстов</b>\n\nНапишите, что создать:\n• Эссе\n• Сочинение\n• Пост\n• Описание\n\nПример: <i>Эссе на тему космоса</i>", parse_mode="HTML")

@dp.message(F.text == "📊 Статус")
async def st_btn(msg: types.Message):
    u = gu(msg.from_user.id)
    l = max(0, FREE_LIMIT - u['generations_today'])
    await msg.answer(f"📊 {'💎 VIP' if u['status'] == 'vip' else '🆓 Бесплатно'}\n🎨 {'Безлимит' if u['status'] == 'vip' else f'{l}/{FREE_LIMIT}'}")

@dp.message(F.text == "💎 Купить VIP")
async def buy_btn(msg: types.Message): await vip_cmd(msg)

@dp.message(F.text == "📞 Поддержка")
async def sup_btn(msg: types.Message):
    await msg.answer("📞 Выберите раздел:", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🤝 Сотрудничество", callback_data="support_coop")],
        [InlineKeyboardButton(text="📢 Реклама", callback_data="support_ads")],
        [InlineKeyboardButton(text="❓ Вопрос", callback_data="support_question")]
    ]))

@dp.callback_query(lambda c: c.data and c.data.startswith("support_"))
async def sup_cb(cb: types.CallbackQuery):
    sec = {"support_coop": "🤝 Сотрудничество", "support_ads": "📢 Реклама", "support_question": "❓ Вопрос"}[cb.data]
    try: await bot.send_message(OWNER_ID, f"📩 {sec}\nОт: @{cb.from_user.username or cb.from_user.id} ({cb.from_user.id})")
    except: pass
    await cb.message.edit_text(f"✅ Запрос в раздел «{sec}» отправлен.")

@dp.message(UM.wp)
async def h_img(msg: types.Message, st: FSMContext):
    u = gu(msg.from_user.id)
    if u['status'] == 'free':
        if u.get('last_reset') != str(date.today()): rd(msg.from_user.id); u['generations_today'] = 0
        if u['generations_today'] >= FREE_LIMIT and u['bonus_generations'] <= 0:
            await msg.answer(f"🚫 Лимит ({FREE_LIMIT}/день). /vip"); return
    m = await msg.answer("🎨 Генерирую...")
    img = gi(msg.text)
    if img:
        await msg.answer_photo(photo=types.BufferedInputFile(img, filename="img.png"), caption=f"🎨 {msg.text[:200]}")
        lr(msg.from_user.id, msg.from_user.username, 'image', 1)
        if u['bonus_generations'] > 0: uu(msg.from_user.id, ab=-1)
        else: uu(msg.from_user.id, ig=True)
        await m.delete()
    else: await m.edit_text("❌ Ошибка."); lr(msg.from_user.id, msg.from_user.username, 'image', 0)
    await st.clear()

@dp.message(UM.wo, F.photo)
async def h_ocr(msg: types.Message, st: FSMContext):
    u = gu(msg.from_user.id)
    if u['status'] == 'free':
        if u.get('last_reset') != str(date.today()): rd(msg.from_user.id); u['generations_today'] = 0
        if u['generations_today'] >= FREE_LIMIT and u['bonus_generations'] <= 0:
            await msg.answer(f"🚫 Лимит. /vip"); return
    await msg.answer("🔍 Распознаю...")
    ph = msg.photo[-1]; fl = await bot.get_file(ph.file_id); ibs = await bot.download_file(fl.file_path)
    txt = oi(ibs.read())
    if txt:
        await msg.answer(f"📝 <pre>{txt}</pre>", parse_mode="HTML")
        lr(msg.from_user.id, msg.from_user.username, 'ocr', 1)
        if u['bonus_generations'] > 0: uu(msg.from_user.id, ab=-1)
        else: uu(msg.from_user.id, ig=True)
    else: await msg.answer("❌ Не удалось."); lr(msg.from_user.id, msg.from_user.username, 'ocr', 0)
    await st.clear()

@dp.message(UM.wc)
async def h_cht(msg: types.Message): await msg.answer(ai(msg.text))

@dp.message(UM.wt)
async def h_tr(msg: types.Message, st: FSMContext):
    await msg.answer(tr(msg.text))
    await st.clear()

@dp.message(UM.wx)
async def h_txt(msg: types.Message, st: FSMContext):
    u = gu(msg.from_user.id)
    if u['status'] == 'free':
        if u.get('last_reset') != str(date.today()): rd(msg.from_user.id); u['generations_today'] = 0
        if u['generations_today'] >= FREE_LIMIT and u['bonus_generations'] <= 0:
            await msg.answer(f"🚫 Лимит ({FREE_LIMIT}/день). /vip"); return
    m = await msg.answer("✍️ Генерирую текст...")
    fp = f"Напиши {msg.text}. Сделай текст качественным, грамотным и интересным."
    txt = ai(fp)
    if txt:
        await msg.answer(txt)
        lr(msg.from_user.id, msg.from_user.username, 'text', 1)
        if u['bonus_generations'] > 0: uu(msg.from_user.id, ab=-1)
        else: uu(msg.from_user.id, ig=True)
        await m.delete()
    else: await m.edit_text("❌ Ошибка."); lr(msg.from_user.id, msg.from_user.username, 'text', 0)
    await st.clear()

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
