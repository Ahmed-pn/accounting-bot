"""
بوت محاسبة عام وشامل لأي محل تجاري - عبر تيليغرام
يدعم: بيع / مصروف / مشترى / ديون / إدارة المخزون / الفواتير / الإدخال النصي والصوتي الذكي
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
        ["📦 المخزون", "📄 الفواتير"],
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
        cur.execute("""
            CREATE TABLE IF NOT EXISTS inventory (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                sell_price REAL NOT NULL,
                buy_price REAL NOT NULL,
                quantity REAL NOT NULL,
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


def get_transaction_by_id(tx_id: int, user_id: int):
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM transactions WHERE id = ? AND user_id = ?", (tx_id, user_id))
        return cur.fetchone()


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


# ---------- إدارة المخزون (Inventory) ----------

def add_inventory_item(user_id: int, name: str, sell_price: float, buy_price: float, quantity: float):
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO inventory (user_id, name, sell_price, buy_price, quantity, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, name, sell_price, buy_price, quantity, datetime.now().isoformat())
        )
        conn.commit()
        return cur.lastrowid


def get_inventory_items(user_id: int):
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM inventory WHERE user_id = ? ORDER BY id DESC", (user_id,))
        return cur.fetchall()


def delete_inventory_item(item_id: int, user_id: int):
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM inventory WHERE id = ? AND user_id = ?", (item_id, user_id))
        conn.commit()
        return cur.rowcount > 0


def sell_from_inventory(user_id: int, item_name: str, quantity: float, customer_name: str = None):
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM inventory WHERE user_id = ? AND name LIKE ? LIMIT 1", (user_id, f"%{item_name}%"))
        item = cur.fetchone()
        if not item:
            return False, "المنتج غير موجود بالمخزون."
        
        if item["quantity"] < quantity:
            return False, f"الكمية المتوفرة غير كافية ({item['quantity']} فقط)."

        total_amount = item["sell_price"] * quantity
        new_qty = item["quantity"] - quantity

        cur.execute("UPDATE inventory SET quantity = ? WHERE id = ?", (new_qty, item["id"]))
        
        cur.execute(
            "INSERT INTO transactions (user_id, type, amount, description, customer_name, created_at) VALUES (?, 'sale', ?, ?, ?, ?)",
            (user_id, total_amount, f"بيع {quantity} من {item['name']}", customer_name, datetime.now().isoformat())
        )
        tx_id = cur.lastrowid
        conn.commit()
        return True, tx_id


# ---------- إدارة الديون ----------

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
      "action": "sale" أو "expense" أو "purchase" أو "debt_i_owe" أو "debt_owed_to_me" أو "settle" أو "add_inventory",
      "amount": رقم المبلغ الإجمالي (أو الصافي بالفاتورة)،
      "description": "وصف تفصيلي أو المواد الموجودة بالفاتورة",
      "customer_name": "اسم الزبون (في حال المبيعات)",
      "person_name": "اسم الشخص (في حال الديون أو التسديد)",
      "item_name": "اسم المادة أو المنتج (في حال المخزون)",
      "sell_price": سعر البيع (رقم),
      "buy_price": سعر الشراء (رقم),
      "quantity": الكمية (رقم)
    }
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
        "أنا بوت المحاسبة الذكي والمخزون تبعك 📊\n\n"
        "📦 من زر *المخزون* تقدر تدير منتجاتك وأسعارها.\n"
        "📄 من زر *الفواتير* وتفاصيل العمليات تقدر تصدر فواتير فورية للزبائن.\n"
        "✍️ أو اكتب بأسلوبك الطبيعي أو ابعت صوت وسأتولى الباقي!"
    )
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=MAIN_KEYBOARD)


async def add_transaction_reply(update: Update, tx_type: str, amount: float, desc: str, customer_name: str = None, tx_id: int = None):
    icon = TYPE_ICONS[tx_type]
    label = TYPE_LABELS[tx_type]
    extra = f" — الزبون: {customer_name}" if customer_name else ""
    
    reply_markup = None
    if tx_type == "sale" and tx_id:
        reply_markup = InlineKeyboardMarkup([[InlineKeyboardButton("🧾 فاتورة البيع", callback_data=f"invoice:{tx_id}")]])

    await update.message.reply_text(
        f"{icon} تسجّل {label[:-1] if label.endswith('ات') else label} بمبلغ {amount:,.0f}"
        f" — {desc or 'بدون وصف'}{extra}",
        reply_markup=reply_markup
    )


async def inventory_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    items = get_inventory_items(user_id)
    
    text = "📦 *إدارة المخزون*\n\nالمنتجات الحالية:"
    buttons = []
    
    if not items:
        text += "\n(المخزون فارغ حالياً)"
    else:
        for item in items:
            it_id, name, sell, buy, qty = item["id"], item["name"], item["sell_price"], item["buy_price"], item["quantity"]
            text += f"\n• *{name}* | بيع: {sell:,.0f} | شراء: {buy:,.0f} | الكمية: {qty}"
            buttons.append([InlineKeyboardButton(f"🗑️ حذف: {name}", callback_data=f"inv_del:{it_id}")])

    buttons.insert(0, [InlineKeyboardButton("➕ إضافة منتج جديد", callback_data="inv_add_prompt")])
    
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(buttons))


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

    if data == "inv_add_prompt":
        await query.message.reply_text(
            "➕ *إضافة منتج للمخزون*\n\nاكتب بالصيغة التالية:\n`إضافة منتج [الاسم] بيع [السعر] شراء [السعر] كمية [الكمية]`",
            parse_mode="Markdown"
        )
        return

    if data.startswith("inv_del:"):
        item_id = int(data.split(":")[1])
        delete_inventory_item(item_id, user_id)
        await query.answer("✅ تم حذف المنتج من المخزون", show_alert=False)
        await inventory_menu(query, context)
        return

    if data.startswith("invoice:"):
        tx_id = int(data.split(":")[1])
        tx = get_transaction_by_id(tx_id, user_id)
        if tx:
            date_str = tx["created_at"].split("T")[0]
            invoice_text = (
          
