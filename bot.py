"""
بوت محاسبة عام وشامل لأي محل تجاري - عبر تيليغرام
يدعم: بيع / مصروف / مشترى / ديون / تصوير الفواتير / الإدخال النصي والصوتي الذكي
"""
import os
import json
import logging
import sqlite3
from datetime import datetime, timedelta
from contextlib import contextmanager

from telegram import (
    Update,
    ReplyKeyboardMarkup,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

from google import genai
from google.genai import types

# ---------------- الإعدادات الأساسية ----------------
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "ضع_التوكن_هون")
client = genai.Client()

DB_PATH = "accounting.db"

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

MAIN_KEYBOARD = ReplyKeyboardMarkup(
    [
        ["➕ بيع", "➖ مصروف", "🛒 مشترى"],
        ["📄 الفواتير"],
        ["📊 التقارير", "📋 آخر العمليات"],
        ["💳 الديون"],
    ],
    resize_keyboard=True
)

TYPE_LABELS = {"sale": "مبيعات", "expense": "مصاريف", "purchase": "مشتريات"}
TYPE_ICONS = {"sale": "➕", "expense": "➖", "purchase": "🛒"}


# ==========================================
# 1. وحدة قاعدة البيانات (Database Module)
# ==========================================

def init_db():
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                type TEXT NOT NULL,
                amount REAL NOT NULL,
                description TEXT,
                customer_name TEXT,
                created_at TEXT NOT NULL
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS debts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                direction TEXT NOT NULL,
                person_name TEXT NOT NULL,
                amount REAL NOT NULL,
                note TEXT,
                settled INTEGER DEFAULT 0,
                created_at TEXT NOT NULL
            )
        """)
        conn.commit()


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def add_transaction(user_id: int, tx_type: str, amount: float, description: str = "", customer_name: str = None):
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO transactions (user_id, type, amount, description, customer_name, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, tx_type, amount, description, customer_name, datetime.now().isoformat())
        )
        conn.commit()
        return cur.lastrowid


def delete_transaction(tx_id: int, user_id: int):
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM transactions WHERE id = ? AND user_id = ?", (tx_id, user_id))
        conn.commit()
        return cur.rowcount > 0


def get_balance(user_id: int, since: str = None):
    with get_conn() as conn:
        cur = conn.cursor()
        query = "SELECT type, SUM(amount) FROM transactions WHERE user_id = ?"
        params = [user_id]
        if since:
            query += " AND created_at >= ?"
            params.append(since)
        query += " GROUP BY type"
        cur.execute(query, params)
        rows = dict(cur.fetchall())
        sales = rows.get("sale", 0) or 0
        expenses = rows.get("expense", 0) or 0
        purchases = rows.get("purchase", 0) or 0
        net = sales - expenses - purchases
        return sales, expenses, purchases, net


def get_transactions(user_id: int, tx_type: str = None, since: str = None, limit: int = 15):
    with get_conn() as conn:
        cur = conn.cursor()
        query = "SELECT id, type, amount, description, customer_name, created_at FROM transactions WHERE user_id = ?"
        params = [user_id]
        if tx_type:
            query += " AND type = ?"
            params.append(tx_type)
        if since:
            query += " AND created_at >= ?"
            params.append(since)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        cur.execute(query, params)
        return cur.fetchall()


def add_debt(user_id: int, direction: str, person_name: str, amount: float, note: str):
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO debts (user_id, direction, person_name, amount, note, settled, created_at) VALUES (?, ?, ?, ?, ?, 0, ?)",
            (user_id, direction, person_name, amount, note, datetime.now().isoformat())
        )
        conn.commit()


def get_open_debts(user_id: int, direction: str):
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, direction, person_name, amount, note FROM debts WHERE user_id = ? AND direction = ? AND settled = 0 ORDER BY id DESC",
            (user_id, direction)
        )
        return cur.fetchall()


def settle_debt(debt_id: int, user_id: int) -> bool:
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("UPDATE debts SET settled = 1 WHERE id = ? AND user_id = ? AND settled = 0", (debt_id, user_id))
        conn.commit()
        return cur.rowcount > 0


def settle_debt_by_name(user_id: int, person_name: str):
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, amount, direction FROM debts WHERE user_id = ? AND person_name LIKE ? AND settled = 0 LIMIT 1",
            (user_id, f"%{person_name}%")
        )
        row = cur.fetchone()
        if not row:
            return False, 0, ""
        debt_id, amount, direction = row["id"], row["amount"], row["direction"]
        cur.execute("UPDATE debts SET settled = 1 WHERE id = ?", (debt_id,))
        conn.commit()
        return True, amount, direction


def get_today_since_iso():
    return datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).isoformat()

def get_week_since_iso():
    return (datetime.now() - timedelta(days=7)).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()

def get_month_since_iso():
    return datetime.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()

def get_year_since_iso():
    return datetime.now().replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()


# ==========================================
# 2. وحدة الذكاء الاصطناعي (AI Parser Module)
# ==========================================

def _call_gemini_structured(contents):
    prompt = """
    أنت مساعد محاسب ذكي لمتجر أو محل تجاري. قم بتحليل النص أو الصورة أو الصوت المستلم واستخرج البيانات بدقة بصيغة JSON فقط دون أي نص إضافي بالشكل التالي:
    {
      "action": "sale" أو "expense" أو "purchase" أو "debt_i_owe" أو "debt_owed_to_me" أو "settle",
      "amount": رقم المبلغ الإجمالي (أو الصافي بالفاتورة)،
      "description": "وصف تفصيلي أو المواد الموجودة بالفاتورة",
      "customer_name": "اسم الزبون (في حال المبيعات)",
      "person_name": "اسم الشخص (في حال الديون أو التسديد)"
    }
    
    ملاحظات هامة:
    - إذا كانت فاتورة شراء بضاعة أو مواد، اجعل action تساوي "purchase".
    - إذا كانت فاتورة مبيع أو بيع لزبون، اجعل action تساوي "sale".
    - إذا كانت مصروف عام (كهرباء، إيجار، نقل...)، اجعل action تساوي "expense".
    - إذا تضمنت فاتورة الشراء أو المبيع تفاصيل مواد وأسعار، اذكرها بوضوح في "description".
    - إذا لم تجد مبلغاً واضحاً، ضع amount بقيمة 0.
    """
    try:
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=[prompt, contents],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
            ),
        )
        return json.loads(response.text)
    except Exception as e:
        logger.error(f"AI Parse Error: {e}")
        return {}


def parse_text(text: str) -> dict:
    return _call_gemini_structured(text)


def parse_audio(audio_bytes: bytes, mime_type: str = "audio/ogg") -> dict:
    audio_part = types.Part.from_bytes(data=audio_bytes, mime_type=mime_type)
    return _call_gemini_structured(audio_part)


def parse_image(image_bytes: bytes, mime_type: str = "image/jpeg") -> dict:
    image_part = types.Part.from_bytes(data=image_bytes, mime_type=mime_type)
    return _call_gemini_structured(image_part)


# ==========================================
# 3. واجهة بوت تيليغرام (Telegram Handlers)
# ==========================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "أهلاً فيك! 👋\n\n"
        "أنا بوت المحاسبة الذكي تبعك 📊\n\n"
        "📄 من زر *الفواتير* تقدر تصدر وتصور فواتير البيع والشراء فوراً.\n"
        "✍️ أو اكتب بأسلوبك الطبيعي أو ابعت صوت وسأتولى الباقي!"
    )
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=MAIN_KEYBOARD)


async def add_transaction_reply(update: Update, tx_type: str, amount: float, desc: str, customer_name: str = None):
    user_id = update.effective_user.id
    add_transaction(user_id, tx_type, amount, desc, customer_name)
    icon = TYPE_ICONS[tx_type]
    label = TYPE_LABELS[tx_type]
    extra = f" — الطرف: {customer_name}" if customer_name else ""
    await update.message.reply_text(
        f"{icon} تسجّل {label[:-1] if label.endswith('ات') else label} بمبلغ {amount:,.0f}"
        f" — {desc or 'بدون وصف'}{extra}"
    )


async def invoices_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    buttons = [
        [InlineKeyboardButton("📤 إصدار فاتورة مبيعات", callback_data="inv:sale")],
        [InlineKeyboardButton("📥 إصدار فاتورة شراء", callback_data="inv:purchase")],
    ]
    await update.message.reply_text(
        "📄 *إدارة الفواتير*\n\n"
        "اختر نوع الفاتورة التي تريد إصدارها أو تسجيلها، ثم قم بتصويرها أو كتابة تفاصيلها:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(buttons)
    )


async def reports_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    buttons = [
        [InlineKeyboardButton("📅 اليوم", callback_data="report:today")],
        [InlineKeyboardButton("🗓️ هالأسبوع", callback_data="report:week")],
        [InlineKeyboardButton("📆 هالشهر", callback_data="report:month")],
        [InlineKeyboardButton("📈 هالسنة", callback_data="report:year")],
    ]
    await update.message.reply_text(
        "📊 اختار الفترة يلي بدك التقرير فيها:",
        reply_markup=InlineKeyboardMarkup(buttons)
    )


PERIOD_LABELS = {"today": "اليوم", "week": "هالأسبوع", "month": "هالشهر", "year": "هالسنة"}


def get_since_for_period(period: str):
    return {
        "today": get_today_since_iso(),
        "week": get_week_since_iso(),
        "month": get_month_since_iso(),
        "year": get_year_since_iso(),
    }[period]


async def show_report(query, user_id: int, period: str):
    since = get_since_for_period(period)
    sales, expenses, purchases, net = get_balance(user_id, since=since)
    label = PERIOD_LABELS[period]

    text = (
        f"📊 *تقرير {label}*\n\n"
        f"➕ مبيعات: {sales:,.0f}\n"
        f"➖ مصاريف: {expenses:,.0f}\n"
        f"🛒 مشتريات: {purchases:,.0f}\n"
        f"{'🟢' if net >= 0 else '🔴'} *الصافي: {net:,.0f}*"
    )

    buttons = [
        [
            InlineKeyboardButton("➕ تفاصيل المبيعات", callback_data=f"list:sale:{period}"),
            InlineKeyboardButton("➖ تفاصيل المصاريف", callback_data=f"list:expense:{period}"),
        ],
        [InlineKeyboardButton("🛒 تفاصيل المشتريات", callback_data=f"list:purchase:{period}")],
        [InlineKeyboardButton("🔙 رجوع للفترات", callback_data="report:menu")],
    ]
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(buttons))


async def show_transactions_list(query, user_id: int, tx_type: str, period: str = None):
    since = get_since_for_period(period) if period else None
    rows = get_transactions(user_id, tx_type=tx_type, since=since, limit=15)
    label = TYPE_LABELS[tx_type]
    icon = TYPE_ICONS[tx_type]

    if not rows:
        text = f"{icon} ما في {label} مسجلة بهالفترة."
        buttons = [[InlineKeyboardButton("🔙 رجوع", callback_data=f"report:{period}" if period else "menu:transactions")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(buttons))
        return

    text = f"{icon} *{label}"
    text += f" ({PERIOD_LABELS[period]})*\n\n" if period else "*\n\n"
    buttons = []
    for tx in rows:
        tx_id, _, amount, desc, customer, created_at = tx["id"], tx["type"], tx["amount"], tx["description"], tx["customer_name"], tx["created_at"]
        date_str = created_at.split("T")[0]
        extra = f" - {customer}" if customer else ""
        text += f"#{tx_id}  {amount:,.0f} — {desc or ''}{extra} ({date_str})\n"
        buttons.append([InlineKeyboardButton(f"🗑️ حذف #{tx_id}", callback_data=f"del:{tx_id}:{tx_type}:{period or 'all'}")])

    buttons.append([InlineKeyboardButton("🔙 رجوع", callback_data=f"report:{period}" if period else "menu:transactions")])
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(buttons))


async def recent_transactions_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    buttons = [
        [InlineKeyboardButton("➕ آخر المبيعات", callback_data="list:sale:all")],
        [InlineKeyboardButton("➖ آخر المصاريف", callback_data="list:expense:all")],
        [InlineKeyboardButton("🛒 آخر المشتريات", callback_data="list:purchase:all")],
    ]
    await update.message.reply_text("📋 شو بدك تشوف؟", reply_markup=InlineKeyboardMarkup(buttons))


async def list_debts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    owed_to_me = get_open_debts(user_id, "owed_to_me")
    i_owe = get_open_debts(user_id, "i_owe")

    if not owed_to_me and not i_owe:
        await update.message.reply_text("💳 ما في ديون مفتوحة حاليًا. 🎉")
        return

    buttons = []
    text = "💳 *الديون المفتوحة*\n\nاضغط (سدد ✅) لتسديد أي دين مباشرة.\n\n*إلك عند غيرك:*"
    for debt in owed_to_me:
        debt_id, name, amount, note = debt["id"], debt["person_name"], debt["amount"], debt["note"]
        label = f"{name}: {amount:,.0f}" + (f" ({note})" if note else "")
        buttons.append([
            InlineKeyboardButton(f"👤 {label}", callback_data="noop"),
            InlineKeyboardButton("سدد ✅", callback_data=f"settle:{debt_id}")
        ])
    if not owed_to_me:
        text += "\n  ما في."

    text += "\n\n*عليك لغيرك:*"
    for debt in i_owe:
        debt_id, name, amount, note = debt["id"], debt["person_name"], debt["amount"], debt["note"]
        label = f"{name}: {amount:,.0f}" + (f" ({note})" if note else "")
        buttons.append([
            InlineKeyboardButton(f"👤 {label}", callback_data="noop"),
            InlineKeyboardButton("سدد ✅", callback_data=f"settle:{debt_id}")
        ])
    if not i_owe:
        text += "\n  ما في."

    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(buttons))


async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = query.from_user.id

    if data == "noop":
        return

    if data.startswith("inv:"):
        inv_type = data.split(":")[1]
        if inv_type == "sale":
            await query.edit_message_text(
                "📤 *إصدار فاتورة مبيعات*\n\n"
                "قم بتصوير فاتورة المبيعات أو اكتب تفاصيلها هنا وسأسجلها كعملية مبيع (Sale).",
                parse_mode="Markdown"
            )
        elif inv_type == "purchase":
            await query.edit_message_text(
                "📥 *إصدار فاتورة شراء*\n\n"
                "قم بتصوير فاتورة الشراء أو اكتب تفاصيلها هنا وسأسجلها كعملية مشترى (Purchase).",
                parse_mode="Markdown"
            )
        return

    if data == "report:menu":
        buttons = [
            [InlineKeyboardButton("📅 اليوم", callback_data="report:today")],
            [InlineKeyboardButton("🗓️ هالأسبوع", callback_data="report:week")],
            [InlineKeyboardButton("📆 هالشهر", callback_data="report:month")],
            [InlineKeyboardButton("📈 هالسنة", callback_data="report:year")],
        ]
        await query.edit_message_text("📊 اختار الفترة يلي بدك التقرير فيها:", reply_markup=InlineKeyboardMarkup(buttons))
        return

    if data.startswith("report:"):
        period = data.split(":")[1]
        await show_report(query, user_id, period)
        return

    if data.startswith("list:"):
        _, tx_type, period = data.split(":")
        period = None if period == "all" else period
        await show_transactions_list(query, user_id, tx_type, period)
        return

    if data == "menu:transactions":
        buttons = [
            [InlineKeyboardButton("➕ آخر المبيعات", callback_data="list:sale:all")],
            [InlineKeyboardButton("➖ آخر المصاريف", callback_data="list:expense:all")],
            [InlineKeyboardButton("🛒 آخر المشتريات", callback_data="list:purchase:all")],
        ]
        await query.edit_message_text("📋 شو بدك تشوف؟", reply_markup=InlineKeyboardMarkup(buttons))
        return

    if data.startswith("del:"):
        _, tx_id, tx_type, period = data.split(":")
        success = delete_transaction(int(tx_id), user_id)
        period = None if period == "all" else period
        if success:
            await query.answer("✅ تم الحذف", show_alert=False)
        await show_transactions_list(query, user_id, tx_type, period)
        return

    if data.startswith("settle:"):
        debt_id = int(data.split(":")[1])
        success = settle_debt(debt_id, user_id)
        if success:
            await query.edit_message_text(f"✅ تم تسديد الدين رقم #{debt_id} بنجاح.")
        else:
            await query.edit_message_text("⚠️ هالدين مو موجود أو تم تسديده مسبقًا.")
        return


async def execute_ai_result(update: Update, result: dict) -> bool:
    action = result.get("action")
    amount = result.get("amount")
    desc = result.get("description") or ""
    customer = result.get("customer_name")
    person = result.get("person_name")
    user_id = update.effective_user.id

    if action in ("sale", "expense", "purchase") and amount is not None:
        await add_transaction_reply(update, action, float(amount), desc, customer)
        return True

    if action == "debt_i_owe" and person and amount is not None:
        add_debt(user_id, "i_owe", person, float(amount), desc)
        await update.message.reply_text(f"✅ تسجّل: عليك دين لـ {person} بمبلغ {float(amount):,.0f}")
        return True

    if action == "debt_owed_to_me" and person and amount is not None:
        add_debt(user_id, "owed_to_me", person, float(amount), desc)
        await update.message.reply_text(f"✅ تسجّل: {person} إله عندك دين {float(amount):,.0f}")
        return True

    if action == "settle" and person:
        success, amt, direction = settle_debt_by_name(user_id, person)
        if success:
            await update.message.reply_text(f"✅ تم تسديد دين {person} بمبلغ {amt:,.0f}")
        else:
            await update.message.reply_text(f"ما لقيت دين مفتوح باسم {person}.")
        return True

    return False


async def handle_free_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw_text = update.message.text.strip()
    lower = raw_text.strip()

    if lower == "📊 التقارير":
        await reports_menu(update, context)
        return
    if lower == "📋 آخر العمليات":
        await recent_transactions_menu(update, context)
        return
    if lower == "💳 الديون":
        await list_debts(update, c
                         async def recent_transactions_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    buttons = [
        [InlineKeyboardButton("➕ آخر المبيعات", callback_data="list:sale:all")],
        [InlineKeyboardButton("➖ آخر المصاريف", callback_data="list:expense:all")],
        [InlineKeyboardButton("🛒 آخر المشتريات", callback_data="list:purchase:all")],
    ]
    await update.message.reply_text("📋 شو بدك تشوف؟", reply_markup=InlineKeyboardMarkup(buttons))


async def list_debts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    owed_to_me = get_open_debts(user_id, "owed_to_me")
    i_owe = get_open_debts(user_id, "i_owe")

    if not owed_to_me and not i_owe:
        await update.message.reply_text("💳 ما في ديون مفتوحة حاليًا. 🎉")
        return

    buttons = []
    text = "💳 *الديون المفتوحة*\n\nاضغط (سدد ✅) لتسديد أي دين مباشرة.\n\n*إلك عند غيرك:*"
    for debt in owed_to_me:
        debt_id, name, amount, note = debt["id"], debt["person_name"], debt["amount"], debt["note"]
        label = f"{name}: {amount:,.0f}" + (f" ({note})" if note else "")
        buttons.append([
            InlineKeyboardButton(f"👤 {label}", callback_data="noop"),
            InlineKeyboardButton("سدد ✅", callback_data=f"settle:{debt_id}")
        ])
    if not owed_to_me:
        text += "\n  ما في."

    text += "\n\n*عليك لغيرك:*"
    for debt in i_owe:
        debt_id, name, amount, note = debt["id"], debt["person_name"], debt["amount"], debt["note"]
        label = f"{name}: {amount:,.0f}" + (f" ({note})" if note else "")
        buttons.append([
            InlineKeyboardButton(f"👤 {label}", callback_data="noop"),
            InlineKeyboardButton("سدد ✅", callback_data=f"settle:{debt_id}")
        ])
    if not i_owe:
        text += "\n  ما في."

    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(buttons))


async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = query.from_user.id

    if data == "noop":
        return

    if data.startswith("inv:"):
        inv_type = data.split(":")[1]
        if inv_type == "sale":
            await query.edit_message_text(
                "📤 *إصدار فاتورة مبيعات*\n\n"
                "قم بتصوير فاتورة المبيعات أو اكتب تفاصيلها هنا وسأسجلها كعملية مبيع (Sale).",
                parse_mode="Markdown"
            )
        elif inv_type == "purchase":
            await query.edit_message_text(
                "📥 *إصدار فاتورة شراء*\n\n"
                "قم بتصوير فاتورة الشراء أو اكتب تفاصيلها هنا وسأسجلها كعملية مشترى (Purchase).",
                parse_mode="Markdown"
            )
        return

    if data == "report:menu":
        buttons = [
            [InlineKeyboardButton("📅 اليوم", callback_data="report:today")],
            [InlineKeyboardButton("🗓️ هالأسبوع", callback_data="report:week")],
            [InlineKeyboardButton("📆 هالشهر", callback_data="report:month")],
            [InlineKeyboardButton("📈 هالسنة", callback_data="report:year")],
        ]
        await query.edit_message_text("📊 اختار الفترة يلي بدك التقرير فيها:", reply_markup=InlineKeyboardMarkup(buttons))
        return

    if data.startswith("report:"):
        period = data.split(":")[1]
        await show_report(query, user_id, period)
        return

    if data.startswith("list:"):
        _, tx_type, period = data.split(":")
        period = None if period == "all" else period
        await show_transactions_list(query, user_id, tx_type, period)
        return

    if data == "menu:transactions":
        buttons = [
            [InlineKeyboardButton("➕ آخر المبيعات", callback_data="list:sale:all")],
            [InlineKeyboardButton("➖ آخر المصاريف", callback_data="list:expense:all")],
            [InlineKeyboardButton("🛒 آخر المشتريات", callback_data="list:purchase:all")],
        ]
        await query.edit_message_text("📋 شو بدك تشوف؟", reply_markup=InlineKeyboardMarkup(buttons))
        return

    if data.startswith("del:"):
        _, tx_id, tx_type, period = data.split(":")
        success = delete_transaction(int(tx_id), user_id)
        period = None if period == "all" else period
        if success:
            await query.answer("✅ تم الحذف", show_alert=False)
        await show_transactions_list(query, user_id, tx_type, period)
        return

    if data.startswith("settle:"):
        debt_id = int(data.split(":")[1])
        success = settle_debt(debt_id, user_id)
        if success:
            await query.edit_message_text(f"✅ تم تسديد الدين رقم #{debt_id} بنجاح.")
        else:
            await query.edit_message_text("⚠️ هالدين مو موجود أو تم تسديده مسبقًا.")
        return


async def execute_ai_result(update: Update, result: dict) -> bool:
    action = result.get("action")
    amount = result.get("amount")
    desc = result.get("description") or ""
    customer = result.get("customer_name")
    person = result.get("person_name")
    user_id = update.effective_user.id

    if action in ("sale", "expense", "purchase") and amount is not None:
        await add_transaction_reply(update, action, float(amount), desc, customer)
        return True

    if action == "debt_i_owe" and person and amount is not None:
        add_debt(user_id, "i_owe", person, float(amount), desc)
        await update.message.reply_text(f"✅ تسجّل: عليك دين لـ {person} بمبلغ {float(amount):,.0f}")
        return True

    if action == "debt_owed_to_me" and person and amount is not None:
        add_debt(user_id, "owed_to_me", person, float(amount), desc)
        await update.message.reply_text(f"✅ تسجّل: {person} إله عندك دين {float(amount):,.0f}")
        return True

    if action == "settle" and person:
        success, amt, direction = settle_debt_by_name(user_id, person)
        if success:
            await update.message.reply_text(f"✅ تم تسديد دين {person} بمبلغ {amt:,.0f}")
        else:
            await update.message.reply_text(f"ما لقيت دين مفتوح باسم {person}.")
        return True

    return False


async def handle_free_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw_text = update.message.text.strip()
    lower = raw_text.strip()

    if lower == "📊 التقارير":
        await reports_menu(update, context)
        return
    if lower == "📋 آخر العمليات":
        await recent_transactions_menu(update, context)
        return
    if lower == "💳 الديون":
        await list_debts(update, context)
        return
    if lower == "📄 الفواتير":
        await invoices_menu(update, context)
        return
    if lower == "➕ بيع":
        await update.message.reply_text("اكتب بأسلوبك الطبيعي، مثلاً: `بيع بضاعة لأحمد بـ 50000`", parse_mode="Markdown")
        return
    if lower == "➖ مصروف":
        await update.message.reply_text("اكتب بأسلوبك الطبيعي، مثلاً: `صرفت فاتورة كهرباء 20000`", parse_mode="Markdown")
        return
    if lower == "🛒 مشترى":
        await update.message.reply_text("اكتب بأسلوبك الطبيعي، مثلاً: `اشتريت بضاعة من المورد بـ 100000`", parse_mode="Markdown")
        return

    result = parse_text(raw_text)
    handled = await execute_ai_result(update, result)
    if handled:
        return

    await update.message.reply_text(
        "ما فهمت عليك تماماً 🤔 جرب تصوير فاتورة 📸 أو اكتب بأسلوبك مثل:\n"
        "`فاتورة شراء بـ 100 ألف` أو `مصروف كهرباء 20 ألف`",
        parse_mode="Markdown"
    )


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🎧 عم أسمع...")
    voice = update.message.voice
    tg_file = await context.bot.get_file(voice.file_id)
    audio_bytes = bytes(await tg_file.download_as_bytearray())

    result = parse_audio(audio_bytes, mime_type="audio/ogg")
    handled = await execute_ai_result(update, result)
    if not handled:
        await update.message.reply_text("ما فهمت المقطع الصوتي 🤔 جرب تحكي بوضوح أكتر.")


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔍 عم أقرأ الفاتورة والصورة...")
    
    photo = update.message.photo[-1]
    tg_file = await context.bot.get_file(photo.file_id)
    photo_bytes = bytes(await tg_file.download_as_bytearray())

    result = parse_image(photo_bytes, mime_type="image/jpeg")
    handled = await execute_ai_result(update, result)
    
    if not handled:
        await update.message.reply_text("⚠️ ما قدرت استخرج تفاصيل الفاتورة بدقة من الصورة. تأكد أن الصورة واضحة.")


def main():
    init_db()
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_free_text))

    logger.info("البوت المحاسبي الذكي شغّال...")
    app.run_polling()


if __name__ == "__main__":
    main()
            
