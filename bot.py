"""
بوت محاسبة عام لأي محل تجاري - عبر تيليغرام
يدعم: بيع / مصروف / مشترى / ديون / تصوير الفواتير / الإدخال النصي الحر الذكي
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


# ---------------- أمر البداية ----------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "أهلاً فيك! 👋\n\n"
        "أنا بوت المحاسبة الذكي تبعك 📊\n\n"
        "📸 *صور أي فاتورة ورقية* أو أرسلها كصورة وسأقوم بحفظها ومعالجة المخزون تلقائياً.\n"
        "✍️ *اكتب براحتك بدون تعقيد:* \n"
        "• `فاتورة شراء من شركة النماء بـ 150000`\n"
        "• `بعت لأحمد بضاعة بـ 50000`\n"
        "• `فاتورة كهرباء 20000`\n"
        "• `دين لسامر 5000`\n\n"
        "🎙️ أو أرسل رسالة صوتية وسأفهمها مباشرة!"
    )
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=MAIN_KEYBOARD)


# ---------------- تسجيل العمليات ----------------

async def add_transaction_reply(update: Update, tx_type: str, amount: float, desc: str, customer_name: str = None):
    db.add_transaction(update.effective_user.id, tx_type, amount, desc, customer_name)
    icon = TYPE_ICONS[tx_type]
    label = TYPE_LABELS[tx_type]
    extra = f" — الطرف: {customer_name}" if customer_name else ""
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


# ---------------- عرض قوائم العمليات ----------------

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
    await update.message.reply_text(
        "📄 *إدارة الفواتير والعمليات*\n\n"
        "بكل بساطة:\n"
        "1️⃣ **صور الفاتورة بالكاميرا** وأرسلها كصورة 📸\n"
        "2️⃣ أو **اكتب تفاصيل الفاتورة بأي أسلوب طبيعي** (مثل: `فاتورة شراء مواد بـ 100 ألف من شركة كذا`) وسأقوم بتسجيلها وتحديث المخزون تلقائياً!",
        parse_mode="Markdown"
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


# ---------------- استقبال الأزرار ----------------

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = query.from_user.id

    if data == "noop":
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
            await query.edit_message_text(f"✅ تم تسديد دين رقم #{debt_id} بنجاح.")
        else:
            await query.edit_message_text("⚠️ هالدين مو موجود أو تم تسديده مسبقًا.")
        return


# ---------------- معالجة النصوص الذكية عبر الذكاء الاصطناعي ----------------

async def handle_free_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw_text = update.message.text.strip()
    lower = raw_text.strip()
    user_id = update.effective_user.id

    # التعامل مع أزرار القائمة السفلية
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

    # إرسال النص مباشرة إلى محلل الذكاء الاصطناعي لفهمه بغض النظر عن الصيغة
    if ai_parser.is_configured():
        result = ai_parser.parse_text(raw_text)
        handled = await execute_ai_result(update, result)
        if handled:
            return

    await update.message.reply_text(
        "ما فهمت عليك تماماً 🤔 جرب تصوير فاتورة 📸 أو اكتب بأسلوبك مثل:\n"
        "`فاتورة شراء بـ 100 ألف` أو `مصروف كهرباء 20 ألف`",
        parse_mode="Markdown"
    )


async def execute_ai_result(update: Update, result: dict) -> bool:
    action = result.get("action")
    amount = result.get("amount")
    desc = result.get("description") or ""
    customer = result.get("customer_name")
    person = result.get("person_name")
    user_id = update.effective_user.id

    if action in ("sale", "expense", "purchase") and amount:
        # إذا كانت مشتريات أو فاتورة يمكننا أيضاً تحديث المخزون افتراضياً إذا وجد تفاصيل بالوصف
        await add_transaction_reply(update, action, float(amount), desc, customer)
        return True

    if action == "debt_i_owe" and person and amount:
        db.add_debt(user_id, "i_owe", person, float(amount), desc)
        await update.message.reply_text(f"✅ تسجّل: عليك دين لـ {person} بمبلغ {float(amount):,.0f}")
        return True

    if action == "debt_owed_to_me" and person and amount:
        db.add_debt(user_id, "owed_to_me", person, float(amount), desc)
        await update.message.reply_text(f"✅ تسجّل: {person} إله عندك دين {float(amount):,.0f}")
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
        await update.message.reply_text("🎙️ تحتاج تفعيل الذكاء الاصطناعي للصوت.")
        return

    await update.message.reply_text("🎧 عم أسمع...")
    voice = update.message.voice
    tg_file = await context.bot.get_file(voice.file_id)
    audio_bytes = bytes(await tg_file.download_as_bytearray())

    result = ai_parser.parse_audio(audio_bytes, mime_type="audio/ogg")
    handled = await execute_ai_result(update, result)
    if not handled:
        await update.message.reply_text("ما فهمت المقطع الصوتي 🤔 جرب تحكي بوضوح أكتر.")


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """التعامل مع تصوير الفاتورة عبر الكاميرا وتحليلها بالذكاء الاصطناعي"""
    if not ai_parser.is_configured():
        await update.message.reply_text("📸 قراءة الفواتير بالصور تتطلب تفعيل الذكاء الاصطناعي أولاً.")
        return

    await update.message.reply_text("🔍 عم أقرأ الفاتورة والصورة...")
    
    photo = update.message.photo[-1]
    tg_file = await context.bot.get_file(photo.file_id)
    photo_bytes = bytes(await tg_file.download_as_bytearray())

    result = ai_parser.parse_image(photo_bytes, mime_type="image/jpeg")
    handled = await execute_ai_result(update, result)
    
    if not handled:
        await update.message.reply_text("⚠️ ما قدرت استخرج تفاصيل الفاتورة بدقة من الصورة. تأكد أن الصورة واضحة.")


def main():
    db.init_db()
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_free_text))

    logger.info("البوت شغّال...")
    app.run_polling()


if __name__ == "__main__":
    main()
        
