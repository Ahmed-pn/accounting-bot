"""
بوت محاسبة عام لأي محل تجاري - عبر تيليغرام
يدعم: بيع / مصروف / مشترى / ديون / تقارير بفترات مختلفة + الفواتير وإدارة المخزون

طريقة التشغيل:
1. pip install -r requirements.txt
2. حط التوكن تبعك بمتغير البيئة TELEGRAM_BOT_TOKEN
3. python bot.py
"""
import os
import re
import logging
from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters
)

import database as db
import ai_parser

# ---------------- الإعدادات ----------------
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "ضع_التوكن_هون")

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

# متغير لتتبع حالة إدخال الفواتير للمستخدمين
user_invoice_state = {}


# ---------------- أمر البداية ----------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "أهلاً فيك! 👋\n\n"
        "أنا بوت المحاسبة تبعك — بيشتغل لأي محل أو نشاط تجاري.\n\n"
        "*اكتب بشكل طبيعي:*\n"
        "`بيع سكر كيلو 50000` — أو أضف زبون: `بيع سكر 50000 زبون أحمد`\n"
        "`مصروف فاتورة كهرباء 20000`\n"
        "`مشترى بضاعة من المورد 100000`\n\n"
        "*ديون:*\n"
        "`دين لسامر 5000` — عليك دين لحدا\n"
        "`دين من أحمد 100000` — إلك دين عند حدا\n"
        "`تسديد سامر`\n\n"
        "🎙️ *كمان تقدر ترسل رسالة صوتية* وبفهمها تلقائيًا!\n\n"
        "استخدم الأزرار تحت للتقارير وعرض العمليات 👇"
    )
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=MAIN_KEYBOARD)


# ---------------- تسجيل العمليات ----------------

async def add_transaction_reply(update: Update, tx_type: str, amount: float, desc: str, customer_name: str = None):
    db.add_transaction(update.effective_user.id, tx_type, amount, desc, customer_name)
    icon = TYPE_ICONS[tx_type]
    label = TYPE_LABELS[tx_type]
    extra = f" — زبون: {customer_name}" if customer_name else ""
    await update.message.reply_text(
        f"{icon} تسجّل {label[:-1] if label.endswith('ات') else label} بمبلغ {amount:,.0f}"
        f" — {desc or 'بدون وصف'}{extra}"
    )


# ---------------- التقارير ----------------

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
        "today": db.get_today_since_iso(),
        "week": db.get_week_since_iso(),
        "month": db.get_month_since_iso(),
        "year": db.get_year_since_iso(),
    }[period]


async def show_report(query, user_id: int, period: str):
    since = get_since_for_period(period)
    sales, expenses, purchases, net = db.get_balance(user_id, since=since)
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


# ---------------- عرض قوائم العمليات (مع حذف) ----------------

async def show_transactions_list(query, user_id: int, tx_type: str, period: str = None):
    since = get_since_for_period(period) if period else None
    rows = db.get_transactions(user_id, tx_type=tx_type, since=since, limit=15)
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
    for tx_id, _, amount, desc, customer, created_at in rows:
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
    await update.message.reply_text(
        "📋 شو بدك تشوف؟",
        reply_markup=InlineKeyboardMarkup(buttons)
    )


# ---------------- قائمة الفواتير ----------------

async def invoices_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    buttons = [
        [InlineKeyboardButton("📄 فاتورة مبيع", callback_data="inv:sale")],
        [InlineKeyboardButton("🛒 فاتورة شراء", callback_data="inv:purchase")],
    ]
    await update.message.reply_text(
        "📄 *إدارة الفواتير*\n\nاختر نوع الفاتورة التي تريد إصدارها:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(buttons)
    )


# ---------------- الديون ----------------

async def list_debts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    owed_to_me = db.get_open_debts(user_id, "owed_to_me")
    i_owe = db.get_open_debts(user_id, "i_owe")

    if not owed_to_me and not i_owe:
        await update.message.reply_text("💳 ما في ديون مفتوحة حاليًا. 🎉")
        return

    buttons = []
    text = "💳 *الديون المفتوحة*\n\nاضغط (سدد ✅) لتسديد أي دين مباشرة.\n\n*إلك عند غيرك:*"
    for debt_id, _, name, amount, note in owed_to_me:
        label = f"{name}: {amount:,.0f}" + (f" ({note})" if note else "")
        buttons.append([
            InlineKeyboardButton(f"👤 {label}", callback_data="noop"),
            InlineKeyboardButton("سدد ✅", callback_data=f"settle:{debt_id}")
        ])
    if not owed_to_me:
        text += "\n  ما في."

    text += "\n\n*عليك لغيرك:*"
    for debt_id, _, name, amount, note in i_owe:
        label = f"{name}: {amount:,.0f}" + (f" ({note})" if note else "")
        buttons.append([
            InlineKeyboardButton(f"👤 {label}", callback_data="noop"),
            InlineKeyboardButton("سدد ✅", callback_data=f"settle:{debt_id}")
        ])
    if not i_owe:
        text += "\n  ما في."

    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(buttons))


# ---------------- استقبال ضغطات الأزرار (Callback) ----------------

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = query.from_user.id

    if data == "noop":
        return

    if data == "inv:sale":
        user_invoice_state[user_id] = "sale"
        await query.edit_message_text(
            "📄 *إصدار فاتورة مبيع*\n\n"
            "أرسل تفاصيل الفاتورة برسالة واحدة:\n"
            "`اسم الزبون | المادة: الكمية x السعر`\n\n"
            "مثال:\n`أحمد | سكر: 2 x 50000, زيت: 1 x 150000`",
            parse_mode="Markdown"
        )
        return

    if data == "inv:purchase":
        user_invoice_state[user_id] = "purchase"
        await query.edit_message_text(
            "🛒 *إصدار فاتورة شراء*\n\n"
            "أرسل تفاصيل الفاتورة برسالة واحدة (لتضاف للمخزون تلقائياً):\n"
            "`اسم المورد | المادة: الكمية x سعر الشراء`\n\n"
            "مثال:\n`شركة النماء | أرز: 10 x 40000`",
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
        success = db.delete_transaction(int(tx_id), user_id)
        period = None if period == "all" else period
        if success:
            await query.answer("✅ تم الحذف", show_alert=False)
        await show_transactions_list(query, user_id, tx_type, period)
        return

    if data.startswith("settle:"):
        debt_id = int(data.split(":")[1])
        success = db.settle_debt(debt_id, user_id)
        if success:
            await query.edit_message_text(f"✅ تم تسديد الدين رقم #{debt_id} بنجاح.")
        else:
            await query.edit_message_text("⚠️ هالدين مو موجود أو تم تسديده مسبقًا.")
        return


# ---------------- فهم الرسائل الحرة (عربي طبيعي) ----------------

ARABIC_INDIC = "٠١٢٣٤٥٦٧٨٩"
WESTERN = "0123456789"
DIGIT_TABLE = str.maketrans(ARABIC_INDIC, WESTERN)

SALE_KEYWORDS = ["بيع", "بعت"]
EXPENSE_KEYWORDS = ["مصروف", "صرفت", "صرف"]
PURCHASE_KEYWORDS = ["مشترى", "مشتريات", "اشتريت", "شراء"]
SETTLE_KEYWORDS = ["تسديد", "سددت", "دفعت"]


def normalize_digits(text: str) -> str:
    return text.translate(DIGIT_TABLE)


def extract_amount(text: str):
    match = re.search(r"\d+(\.\d+)?", text)
    if not match:
        return None, text
    amount = float(match.group())
    rest = text[:match.start()] + text[match.end():]
    return amount, rest


def extract_customer(text: str):
    """يكتشف 'زبون اسم' بالجملة ويرجع (الاسم أو None, النص بدونه)"""
    m = re.search(r"زبون\s+(\S+)", text)
    if m:
        name = m.group(1)
        rest = text[:m.start()] + text[m.end():]
        return name, rest
    return None, text


def parse_debt(text: str):
    m = re.search(r"دين\s+ل[ـ]?\s*(\S+)\s+(\d+(?:\.\d+)?)\s*(.*)", text)
    if m:
        return "i_owe", m.group(1), float(m.group(2)), m.group(3).strip()
    m = re.search(r"دين\s+من\s+(\S+)\s+(\d+(?:\.\d+)?)\s*(.*)", text)
    if m:
        return "owed_to_me", m.group(1), float(m.group(2)), m.group(3).strip()
    m = re.search(r"لي\s+عند\s+(\S+)\s+(\d+(?:\.\d+)?)\s*(.*)", text)
    if m:
        return "owed_to_me", m.group(1), float(m.group(2)), m.group(3).strip()
    return None


def parse_settle_name(text: str):
    for kw in SETTLE_KEYWORDS:
        m = re.search(rf"{kw}\s+ل?(\S+)", text)
        if m:
            return m.group(1)
    return None


def parse_transaction(text: str):
    tx_type = None
    if any(k in text for k in PURCHASE_KEYWORDS):
        tx_type = "purchase"
        keywords = PURCHASE_KEYWORDS
    elif any(k in text for k in SALE_KEYWORDS):
        tx_type = "sale"
        keywords = SALE_KEYWORDS
    elif any(k in text for k in EXPENSE_KEYWORDS):
        tx_type = "expense"
        keywords = EXPENSE_KEYWORDS
    else:
        return None

    customer_name, text_no_customer = extract_customer(text)
    amount, rest = extract_amount(text_no_customer)
    if amount is None:
        return None

    for k in keywords:
        rest = rest.replace(k, " ")
    desc = " ".join(rest.split())
    return tx_type, amount, desc, customer_name


async def handle_free_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw_text = update.message.text.strip()
    text = normalize_digits(raw_text)
    lower = text.strip()

    user_id = update.effective_user.id

    # ---- معالجة إدخال الفاتورة إذا كان المستخدم ينتظر إدخال موادها ----
    if user_id in user_invoice_state:
        inv_type = user_invoice_state.pop(user_id)
        try:
            if "|" not in text:
                await update.message.reply_text("❌ الصيغة خاطئة. يجب استخدام الرمز | للفصل بين الاسم والمواد.")
                return
            
            parts = text.split("|")
            party_name = parts[0].strip()
            items_text = parts[1].strip()
            
            total_amount = 0
            items_summary = []
            
            for item in items_text.split(","):
                item_parts = item.split(":")
                item_name = item_parts[0].strip()
                calc = item_parts[1].strip().split("x")
                qty = float(calc[0].strip())
                price = float(calc[1].strip())
                
                subtotal = qty * price
                total_amount += subtotal
                items_summary.append(f"- {item_name}: {qty} × {price:,.0f}")
                
                is_p = (inv_type == "purchase")
                db.update_product_stock_and_price(item_name, qty, buy_price=price if is_p else None, is_purchase=is_p)

            tx_type = "purchase" if inv_type == "purchase" else "sale"
            desc = f"فاتورة لـ {party_name}: " + ", ".join(items_summary)
            db.add_transaction(user_id, tx_type, total_amount, desc, customer_name=party_name if inv_type=="sale" else None)

            label = "فاتورة مبيع" if inv_type == "sale" else "فاتورة شراء"
            await update.message.reply_text(
                f"✅ تم إصدار وحفظ {label} بنجاح!\n\n"
                f"👤 الطرف: {party_name}\n"
                f"💰 الإجمالي: {total_amount:,.0f}\n"
                f"⚙️ *(تم تحديث المخزون والحسابات تلقائياً)*"
            )
        except Exception as e:
            await update.message.reply_text("❌ حدث خطأ بصيغة الفاتورة. تأكد من استخدام الرمز | وفصل الكمية بالسعر بـ x مثل: سكر: 2 x 50000")
        return

    # ---- أزرار لوحة المفاتيح ----
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
        await update.message.reply_text("اكتب مثلاً: بيع سكر كيلو 50000\nأو مع زبون: بيع سكر 50000 زبون أحمد")
        return
    if lower == "➖ مصروف":
        await update.message.reply_text("اكتب مثلاً: مصروف فاتورة كهرباء 20000")
        return
    if lower == "🛒 مشترى":
        await update.message.reply_text("اكتب مثلاً: مشترى بضاعة من المورد 100000")
        return

    # ---- ديون ----
    debt_result = parse_debt(text)
    if debt_result:
        direction, name, amount, note = debt_result
        db.add_debt(user_id, direction, name, amount, note)
        if direction == "i_owe":
            await update.message.reply_text(f"✅ تسجّل: عليك دين لـ {name} بمبلغ {amount:,.0f}")
        else:
            await update.message.reply_text(f"✅ تسجّل: {name} إله عندك دين {amount:,.0f}")
        return

    # ---- تسديد دين ----
    if any(k in text for k in SETTLE_KEYWORDS):
        name = parse_settle_name(text)
        if name:
            success, amount, direction = db.settle_debt_by_name(user_id, name)
            if success:
                await update.message.reply_text(f"✅ تم تسديد دين {name} بمبلغ {amount:,.0f}")
            else:
                await update.message.reply_text(f"ما لقيت دين مفتوح باسم {name}.")
            return

    # ---- بيع / مصروف / مشترى ----
    tx_result = parse_transaction(text)
    if tx_result:
        tx_type, amount, desc, customer_name = tx_result
        await add_transaction_reply(update, tx_type, amount, desc, customer_name)
        return

    # ---- لم تفهمها القواعد الثابتة: جرب الذكاء الاصطناعي ----
    if ai_parser.is_configured():
        result = ai_parser.parse_text(text)
        handled = await execute_ai_result(update, result)
        if handled:
            return

    await update.message.reply_text(
        "ما فهمت عليك 🤔 جرب مثلاً:\n"
        "`بيع سكر كيلو 50000`\n"
        "`مصروف كهرباء 20000`\n"
        "`مشترى بضاعة 100000`\n"
        "`دين لسامر 5000`",
        parse_mode="Markdown"
    )


async def execute_ai_result(update: Update, result: dict) -> bool:
    """ينفذ نتيجة الذكاء الاصطناعي. يرجع True لو نجح بتنفيذ إجراء معروف"""
    action = result.get("action")
    amount = result.get("amount")
    desc = result.get("description") or ""
    customer = result.get("customer_name")
    person = result.get("person_name")
    user_id = update.effective_user.id

    if action in ("sale", "expense", "purchase") and amount:
        await add_transaction_reply(update, action, float(amount), desc, customer)
        return True

    if action == "debt_i_owe" and person and amount:
        db.add_debt(user_id, "i_owe", person, float(amount), desc)
        await update.message.reply_text(f"✅ (بالذكاء الاصطناعي) تسجّل: عليك دين لـ {person} بمبلغ {float(amount):,.0f}")
        return True

    if action == "debt_owed_to_me" and person and amount:
        db.add_debt(user_id, "owed_to_me", person, float(amount), desc)
        await update.message.reply_text(f"✅ (بالذكاء الاصطناعي) تسجّل: {person} إله عندك دين {float(amount):,.0f}")
        return True

    if action == "settle" and person:
        success, amt, direction = db.settle_debt_by_name(user_id, person)
        if success:
            await update.message.reply_text(f"✅ تم تسديد دين {person} بمبلغ {amt:,.0f}")
        else:
            await update.message.reply_text(f"ما لقيت دين مفتوح باسم {person}.")
        return True

    return False


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not ai_parser.is_configured():
        await update.message.reply_text(
            "🎙️ استقبال الصوت يحتاج تفعيل الذكاء الاصطناعي أولاً.\n"
      
