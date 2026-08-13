"""
بوت محاسبة بسيط عبر تيليغرام
المرحلة 1: أوامر يدوية (بدون ذكاء اصطناعي بعد)

طريقة التشغيل:
1. pip install -r requirements.txt
2. حط التوكن تبعك بمتغير البيئة TELEGRAM_BOT_TOKEN أو مباشرة بمتغير BOT_TOKEN تحت
3. python bot.py
"""
import os
import logging
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    ContextTypes, filters
)

import database as db

# ---------------- الإعدادات ----------------
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "ضع_التوكن_هون")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

MAIN_KEYBOARD = ReplyKeyboardMarkup(
    [["➕ بيع", "➖ مصروف"], ["📊 الرصيد", "📋 آخر العمليات"], ["💳 الديون"]],
    resize_keyboard=True
)


# ---------------- الأوامر ----------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "أهلاً فيك! 👋\n\n"
        "أنا بوت المحاسبة تبعك. بقدر أسجلّك المبيعات والمصاريف والديون.\n\n"
        "*طريقة الاستخدام:*\n"
        "`بيع 50000 سكر وشاي` — لتسجيل عملية بيع\n"
        "`مصروف 20000 فاتورة كهرباء` — لتسجيل مصروف\n"
        "`/balance` — لمعرفة رصيد اليوم\n"
        "`/report` — تقرير الشهر\n"
        "`/debt_owed اسم_الشخص 100000 سبب` — إلك دين عند حدا\n"
        "`/debt_mine اسم_الشخص 50000 سبب` — عليك دين لحدا\n"
        "`/debts` — عرض الديون المفتوحة\n"
    )
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=MAIN_KEYBOARD)


async def add_sale(update: Update, context: ContextTypes.DEFAULT_TYPE, amount: float, desc: str):
    db.add_transaction(update.effective_user.id, "sale", amount, desc)
    await update.message.reply_text(f"✅ تسجّل بيع بمبلغ {amount:,.0f} — {desc or 'بدون وصف'}")


async def add_expense(update: Update, context: ContextTypes.DEFAULT_TYPE, amount: float, desc: str):
    db.add_transaction(update.effective_user.id, "expense", amount, desc)
    await update.message.reply_text(f"✅ تسجّل مصروف بمبلغ {amount:,.0f} — {desc or 'بدون وصف'}")


async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    sales, expenses, net = db.get_balance(user_id, since=db.get_today_since_iso())
    text = (
        f"📊 *رصيد اليوم*\n\n"
        f"💰 مبيعات: {sales:,.0f}\n"
        f"💸 مصاريف: {expenses:,.0f}\n"
        f"{'🟢' if net >= 0 else '🔴'} الصافي: {net:,.0f}"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


async def report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    sales, expenses, net = db.get_balance(user_id, since=db.get_month_since_iso())
    recent = db.get_recent_transactions(user_id, limit=5)

    text = (
        f"📈 *تقرير الشهر الحالي*\n\n"
        f"💰 إجمالي المبيعات: {sales:,.0f}\n"
        f"💸 إجمالي المصاريف: {expenses:,.0f}\n"
        f"{'🟢' if net >= 0 else '🔴'} الصافي: {net:,.0f}\n\n"
        f"*آخر 5 عمليات:*\n"
    )
    for tx_type, amount, desc, created_at in recent:
        icon = "➕" if tx_type == "sale" else "➖"
        date_str = created_at.split("T")[0]
        text += f"{icon} {amount:,.0f} — {desc or ''} ({date_str})\n"

    await update.message.reply_text(text, parse_mode="Markdown")


async def recent_transactions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    recent = db.get_recent_transactions(user_id, limit=10)
    if not recent:
        await update.message.reply_text("ما في عمليات مسجلة بعد.")
        return
    text = "📋 *آخر العمليات*\n\n"
    for tx_type, amount, desc, created_at in recent:
        icon = "➕" if tx_type == "sale" else "➖"
        date_str = created_at.split("T")[0]
        text += f"{icon} {amount:,.0f} — {desc or ''} ({date_str})\n"
    await update.message.reply_text(text, parse_mode="Markdown")


async def debt_owed_to_me(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # /debt_owed اسم المبلغ سبب
    args = context.args
    if len(args) < 2:
        await update.message.reply_text("الصيغة: /debt_owed اسم_الشخص المبلغ سبب(اختياري)")
        return
    name = args[0]
    try:
        amount = float(args[1])
    except ValueError:
        await update.message.reply_text("المبلغ لازم يكون رقم.")
        return
    note = " ".join(args[2:]) if len(args) > 2 else ""
    db.add_debt(update.effective_user.id, "owed_to_me", name, amount, note)
    await update.message.reply_text(f"✅ تسجّل: {name} إله عندك دين {amount:,.0f}")


async def debt_i_owe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if len(args) < 2:
        await update.message.reply_text("الصيغة: /debt_mine اسم_الشخص المبلغ سبب(اختياري)")
        return
    name = args[0]
    try:
        amount = float(args[1])
    except ValueError:
        await update.message.reply_text("المبلغ لازم يكون رقم.")
        return
    note = " ".join(args[2:]) if len(args) > 2 else ""
    db.add_debt(update.effective_user.id, "i_owe", name, amount, note)
    await update.message.reply_text(f"✅ تسجّل: عليك دين لـ {name} بمبلغ {amount:,.0f}")


async def list_debts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    owed_to_me = db.get_open_debts(user_id, "owed_to_me")
    i_owe = db.get_open_debts(user_id, "i_owe")

    text = "💳 *الديون المفتوحة*\n\n"
    text += "*إلك عند غيرك:*\n"
    if owed_to_me:
        for debt_id, _, name, amount, note in owed_to_me:
            text += f"  #{debt_id} {name}: {amount:,.0f} ({note})\n"
    else:
        text += "  ما في.\n"

    text += "\n*عليك لغيرك:*\n"
    if i_owe:
        for debt_id, _, name, amount, note in i_owe:
            text += f"  #{debt_id} {name}: {amount:,.0f} ({note})\n"
    else:
        text += "  ما في.\n"

    text += "\nلتسديد دين استخدم: /settle رقم_الدين"
    await update.message.reply_text(text, parse_mode="Markdown")


async def settle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args:
        await update.message.reply_text("الصيغة: /settle رقم_الدين")
        return
    try:
        debt_id = int(args[0])
    except ValueError:
        await update.message.reply_text("رقم الدين لازم يكون رقم صحيح.")
        return
    success = db.settle_debt(debt_id, update.effective_user.id)
    if success:
        await update.message.reply_text(f"✅ تم تسديد الدين #{debt_id}")
    else:
        await update.message.reply_text("ما لقيت هالدين، تأكد من الرقم.")


# ---------------- معالجة الرسائل النصية الحرة ----------------
# مرحلة 1: تحليل بسيط بالكلمات المفتاحية (بيع / مصروف)
# مرحلة 2 (لاحقًا): استبدال هاد بنموذج ذكاء اصطناعي يفهم الجملة بشكل حر

async def handle_free_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    lower = text.replace("➕", "").replace("➖", "").strip()

    if lower in ("📊 الرصيد",):
        await balance(update, context)
        return
    if lower in ("📋 آخر العمليات",):
        await recent_transactions(update, context)
        return
    if lower in ("💳 الديون",):
        await list_debts(update, context)
        return
    if lower in ("➕ بيع",):
        await update.message.reply_text("اكتب: بيع المبلغ ثم الوصف\nمثال: بيع 50000 سكر وشاي")
        return
    if lower in ("➖ مصروف",):
        await update.message.reply_text("اكتب: مصروف المبلغ ثم الوصف\nمثال: مصروف 20000 فاتورة كهرباء")
        return

    parts = text.split(maxsplit=2)
    if len(parts) < 2:
        await update.message.reply_text(
            "ما فهمت الأمر. جرب:\n`بيع 50000 وصف`\n`مصروف 20000 وصف`",
            parse_mode="Markdown"
        )
        return

    keyword = parts[0]
    try:
        amount = float(parts[1])
    except ValueError:
        await update.message.reply_text("لازم تكتب المبلغ كرقم بعد الكلمة (بيع/مصروف).")
        return
    desc = parts[2] if len(parts) > 2 else ""

    if keyword in ("بيع", "بعت"):
        await add_sale(update, context, amount, desc)
    elif keyword in ("مصروف", "صرفت", "صرف"):
        await add_expense(update, context, amount, desc)
    else:
        await update.message.reply_text(
            "ما فهمت نوع العملية. ابدأ الجملة بـ 'بيع' أو 'مصروف'."
        )


def main():
    db.init_db()
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("balance", balance))
    app.add_handler(CommandHandler("report", report))
    app.add_handler(CommandHandler("transactions", recent_transactions))
    app.add_handler(CommandHandler("debt_owed", debt_owed_to_me))
    app.add_handler(CommandHandler("debt_mine", debt_i_owe))
    app.add_handler(CommandHandler("debts", list_debts))
    app.add_handler(CommandHandler("settle", settle))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_free_text))

    logger.info("البوت شغّال...")
    app.run_polling()


if __name__ == "__main__":
    main()
