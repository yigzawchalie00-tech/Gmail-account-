import os
import asyncio
import logging
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from dotenv import load_dotenv
import asyncpg
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    ContextTypes, ConversationHandler, filters
)

load_dotenv()

BOT_TOKEN = os.environ.get("BOT_TOKEN", "8663005620:AAFK83aPI-bEyRot7dsTlHfmnwLWiXRyhNs")
DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://neondb_owner:npg_d7L4xXgESPUR@ep-frosty-water-axd2u33z-pooler.c-4.us-east-2.aws.neon.tech/neondb?sslmode=require&channel_binding=require")
ADMIN_ID = 8079356870
TASK_EXPIRY_HOURS = 2

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

(REG_NAME, REG_PAYMENT_METHOD, REG_PAYMENT_NUMBER, WITHDRAW_AMOUNT) = range(4)

db_pool = None


async def get_db():
    return await asyncpg.create_pool(DATABASE_URL)


async def init_db(pool):
    async with pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                telegram_id BIGINT PRIMARY KEY,
                name TEXT NOT NULL,
                payment_method TEXT NOT NULL,
                payment_number TEXT NOT NULL,
                balance INTEGER DEFAULT 0,
                registered_at TIMESTAMP DEFAULT NOW()
            );

            CREATE TABLE IF NOT EXISTS tasks (
                id SERIAL PRIMARY KEY,
                first_name TEXT,
                last_name TEXT,
                email TEXT UNIQUE NOT NULL,
                password TEXT,
                birth_year TEXT,
                status TEXT DEFAULT 'available',
                assigned_to BIGINT,
                assigned_at TIMESTAMP,
                expires_at TIMESTAMP,
                completed_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT NOW()
            );

            CREATE TABLE IF NOT EXISTS withdrawals (
                id SERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                amount INTEGER NOT NULL,
                payment_method TEXT,
                payment_number TEXT,
                status TEXT DEFAULT 'pending',
                requested_at TIMESTAMP DEFAULT NOW(),
                paid_at TIMESTAMP
            );
        """)
    logger.info("Database initialized.")


async def expire_tasks(pool):
    while True:
        try:
            async with pool.acquire() as conn:
                expired = await conn.fetch("""
                    UPDATE tasks SET status='available', assigned_to=NULL,
                    assigned_at=NULL, expires_at=NULL
                    WHERE status='assigned' AND expires_at < NOW()
                    RETURNING id, assigned_to
                """)
                for row in expired:
                    logger.info(f"[expiry] Task {row['id']} expired from user {row['assigned_to']}")
        except Exception as e:
            logger.error(f"[expiry] Error: {e}")
        await asyncio.sleep(60)


def main_keyboard():
    return ReplyKeyboardMarkup(
        [["📋 Get Task", "✅ Done"],
         ["💰 My Balance", "💸 Withdraw"],
         ["📊 My Stats"]],
        resize_keyboard=True
    )


def admin_keyboard():
    return ReplyKeyboardMarkup(
        [["📋 Get Task", "✅ Done"],
         ["💰 My Balance", "💸 Withdraw"],
         ["👥 All Workers", "📦 All Tasks"],
         ["📊 My Stats"]],
        resize_keyboard=True
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    async with db_pool.acquire() as conn:
        user = await conn.fetchrow("SELECT * FROM users WHERE telegram_id=$1", user_id)

    if user:
        kb = admin_keyboard() if user_id == ADMIN_ID else main_keyboard()
        await update.message.reply_text(
            f"Welcome back, {user['name']}! 👋\nUse the menu below.",
            reply_markup=kb
        )
        return ConversationHandler.END

    await update.message.reply_text(
        "👋 Welcome to Chalie's Task Bot!\n\nLet's register you.\n\nWhat is your full name?"
    )
    return REG_NAME


async def reg_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["name"] = update.message.text.strip()
    await update.message.reply_text(
        "Choose your payment method:",
        reply_markup=ReplyKeyboardMarkup(
            [["CBE Birr", "Telebirr"]], resize_keyboard=True, one_time_keyboard=True
        )
    )
    return REG_PAYMENT_METHOD


async def reg_payment_method(update: Update, context: ContextTypes.DEFAULT_TYPE):
    method = update.message.text.strip()
    if method not in ["CBE Birr", "Telebirr"]:
        await update.message.reply_text("Please choose CBE Birr or Telebirr.")
        return REG_PAYMENT_METHOD
    context.user_data["payment_method"] = method
    label = "CBE account number" if method == "CBE Birr" else "Telebirr phone number"
    await update.message.reply_text(
        f"Enter your {label}:",
        reply_markup=ReplyKeyboardRemove()
    )
    return REG_PAYMENT_NUMBER


async def reg_payment_number(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    number = update.message.text.strip()
    name = context.user_data["name"]
    method = context.user_data["payment_method"]

    async with db_pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO users (telegram_id, name, payment_method, payment_number)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (telegram_id) DO NOTHING
        """, user_id, name, method, number)

    kb = admin_keyboard() if user_id == ADMIN_ID else main_keyboard()
    await update.message.reply_text(
        f"✅ Registered!\n\nName: {name}\nPayment: {method} — {number}\n\nYou can now get tasks!",
        reply_markup=kb
    )

    if user_id != ADMIN_ID:
        await context.bot.send_message(
            ADMIN_ID,
            f"🆕 New worker registered!\nName: {name}\nPayment: {method} — {number}\nID: {user_id}"
        )
    return ConversationHandler.END


async def get_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    async with db_pool.acquire() as conn:
        user = await conn.fetchrow("SELECT * FROM users WHERE telegram_id=$1", user_id)
        if not user:
            await update.message.reply_text("Please register first with /start")
            return

        active = await conn.fetchrow("""
            SELECT * FROM tasks WHERE assigned_to=$1 AND status='assigned'
        """, user_id)

        if active:
            remaining = active['expires_at'] - datetime.utcnow()
            mins = int(remaining.total_seconds() / 60)
            await update.message.reply_text(
                f"⚠️ You already have an active task!\n\n"
                f"👤 First name: {active['first_name']}\n"
                f"👤 Last name: {active['last_name']}\n"
                f"📧 Email: {active['email']}\n"
                f"🔑 Password: {active['password']}\n"
                f"🎂 Birth year: {active['birth_year']}\n\n"
                f"⏳ Time remaining: {mins} minutes\n\n"
                f"Press ✅ Done when you finish."
            )
            return

        task = await conn.fetchrow("""
            UPDATE tasks SET status='assigned', assigned_to=$1,
            assigned_at=NOW(), expires_at=NOW() + INTERVAL '2 hours'
            WHERE id = (
                SELECT id FROM tasks WHERE status='available'
                ORDER BY created_at ASC LIMIT 1
            )
            RETURNING *
        """, user_id)

        if not task:
            await update.message.reply_text(
                "😔 No tasks available right now. Please check back in a few minutes."
            )
            return

        await update.message.reply_text(
            f"📋 Your Task:\n\n"
            f"👤 First name: {task['first_name']}\n"
            f"👤 Last name: {task['last_name']}\n"
            f"📧 Email: {task['email']}\n"
            f"🔑 Password: {task['password']}\n"
            f"🎂 Birth year: {task['birth_year']}\n\n"
            f"⚠️ Use exactly these details!\n"
            f"⏳ You have 2 hours to complete this task.\n\n"
            f"Press ✅ Done when you finish."
        )


async def done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    async with db_pool.acquire() as conn:
        task = await conn.fetchrow("""
            SELECT * FROM tasks WHERE assigned_to=$1 AND status='assigned'
        """, user_id)

        if not task:
            await update.message.reply_text("You have no active task to mark as done.")
            return

        await conn.execute("""
            UPDATE tasks SET status='pending_verification', completed_at=NOW()
            WHERE id=$1
        """, task['id'])

        user = await conn.fetchrow("SELECT * FROM users WHERE telegram_id=$1", user_id)

    await update.message.reply_text(
        "✅ Task submitted! Waiting for verification.\nYou'll be notified once confirmed."
    )

    await context.bot.send_message(
        ADMIN_ID,
        f"🔔 Task completed — needs verification!\n\n"
        f"👤 Worker: {user['name']} (ID: {user_id})\n"
        f"📧 Email: {task['email']}\n"
        f"Task ID: {task['id']}\n\n"
        f"To confirm: /confirm_{task['id']}\n"
        f"To reject: /reject_{task['id']}"
    )


async def confirm_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    try:
        task_id = int(update.message.text.split("_")[1])
    except (IndexError, ValueError):
        await update.message.reply_text("Invalid command.")
        return

    async with db_pool.acquire() as conn:
        task = await conn.fetchrow("SELECT * FROM tasks WHERE id=$1", task_id)
        if not task or task['status'] != 'pending_verification':
            await update.message.reply_text("Task not found or already processed.")
            return
        await conn.execute("UPDATE tasks SET status='completed' WHERE id=$1", task_id)
        await conn.execute(
            "UPDATE users SET balance=balance+10 WHERE telegram_id=$1",
            task['assigned_to']
        )
        user = await conn.fetchrow("SELECT * FROM users WHERE telegram_id=$1", task['assigned_to'])

    await update.message.reply_text(f"✅ Task {task_id} confirmed. 10 birr added to {user['name']}.")
    await context.bot.send_message(
        task['assigned_to'],
        "🎉 Your task has been verified!\n+10 birr added to your balance.\n\nPress 📋 Get Task to get a new one!"
    )


async def reject_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    try:
        task_id = int(update.message.text.split("_")[1])
    except (IndexError, ValueError):
        await update.message.reply_text("Invalid command.")
        return

    async with db_pool.acquire() as conn:
        task = await conn.fetchrow("SELECT * FROM tasks WHERE id=$1", task_id)
        if not task:
            await update.message.reply_text("Task not found.")
            return
        await conn.execute("""
            UPDATE tasks SET status='available', assigned_to=NULL,
            assigned_at=NULL, expires_at=NULL, completed_at=NULL
            WHERE id=$1
        """, task_id)

    await update.message.reply_text(f"❌ Task {task_id} rejected. Returned to pool.")
    await context.bot.send_message(
        task['assigned_to'],
        "❌ Your task was not verified. Please try again more carefully.\nPress 📋 Get Task for a new task."
    )


async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    async with db_pool.acquire() as conn:
        user = await conn.fetchrow("SELECT * FROM users WHERE telegram_id=$1", user_id)
    if not user:
        await update.message.reply_text("Please register first with /start")
        return
    await update.message.reply_text(
        f"💰 Your Balance: {user['balance']} birr\n\nPress 💸 Withdraw to request a payout."
    )


async def withdraw_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    async with db_pool.acquire() as conn:
        user = await conn.fetchrow("SELECT * FROM users WHERE telegram_id=$1", user_id)

    if not user:
        await update.message.reply_text("Please register first with /start")
        return ConversationHandler.END

    if user['balance'] < 10:
        await update.message.reply_text(
            f"❌ Minimum withdrawal is 10 birr.\nYour balance: {user['balance']} birr."
        )
        return ConversationHandler.END

    await update.message.reply_text(
        f"Your balance: {user['balance']} birr.\nHow much do you want to withdraw? (minimum 10 birr)"
    )
    return WITHDRAW_AMOUNT


async def withdraw_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    try:
        amount = int(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("Please enter a valid number.")
        return WITHDRAW_AMOUNT

    async with db_pool.acquire() as conn:
        user = await conn.fetchrow("SELECT * FROM users WHERE telegram_id=$1", user_id)

        if amount < 10:
            await update.message.reply_text("Minimum withdrawal is 10 birr.")
            return WITHDRAW_AMOUNT

        if amount > user['balance']:
            await update.message.reply_text(
                f"❌ Insufficient balance. Your balance: {user['balance']} birr."
            )
            return WITHDRAW_AMOUNT

        await conn.execute(
            "UPDATE users SET balance=balance-$1 WHERE telegram_id=$2", amount, user_id
        )
        await conn.execute("""
            INSERT INTO withdrawals (user_id, amount, payment_method, payment_number)
            VALUES ($1, $2, $3, $4)
        """, user_id, amount, user['payment_method'], user['payment_number'])

    kb = admin_keyboard() if user_id == ADMIN_ID else main_keyboard()
    await update.message.reply_text(
        f"✅ Withdrawal request submitted!\nAmount: {amount} birr\n"
        f"Payment: {user['payment_method']} — {user['payment_number']}\n\n"
        f"You'll be notified once paid.",
        reply_markup=kb
    )

    await context.bot.send_message(
        ADMIN_ID,
        f"💸 Withdrawal Request!\n\n"
        f"👤 Worker: {user['name']} (ID: {user_id})\n"
        f"💰 Amount: {amount} birr\n"
        f"💳 {user['payment_method']}: {user['payment_number']}\n\n"
        f"To confirm payment: /paid_{user_id}"
    )
    return ConversationHandler.END


async def paid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    try:
        worker_id = int(update.message.text.split("_")[1])
    except (IndexError, ValueError):
        await update.message.reply_text("Invalid command.")
        return

    async with db_pool.acquire() as conn:
        withdrawal = await conn.fetchrow("""
            UPDATE withdrawals SET status='paid', paid_at=NOW()
            WHERE user_id=$1 AND status='pending'
            RETURNING *
        """, worker_id)

    if not withdrawal:
        await update.message.reply_text("No pending withdrawal found for this user.")
        return

    await update.message.reply_text(f"✅ Payment confirmed for user {worker_id}.")
    await context.bot.send_message(
        worker_id,
        f"✅ Your withdrawal of {withdrawal['amount']} birr has been paid!\n"
        f"Check your {withdrawal['payment_method']} account."
    )


async def my_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    async with db_pool.acquire() as conn:
        user = await conn.fetchrow("SELECT * FROM users WHERE telegram_id=$1", user_id)
        completed = await conn.fetchval(
            "SELECT COUNT(*) FROM tasks WHERE assigned_to=$1 AND status='completed'", user_id
        )
        pending = await conn.fetchval(
            "SELECT COUNT(*) FROM tasks WHERE assigned_to=$1 AND status='pending_verification'", user_id
        )

    await update.message.reply_text(
        f"📊 Your Stats\n\n"
        f"✅ Completed tasks: {completed}\n"
        f"⏳ Pending verification: {pending}\n"
        f"💰 Current balance: {user['balance']} birr"
    )


async def all_workers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    async with db_pool.acquire() as conn:
        workers = await conn.fetch("SELECT * FROM users ORDER BY registered_at DESC")

    if not workers:
        await update.message.reply_text("No workers registered yet.")
        return

    text = "👥 All Workers:\n\n"
    for w in workers:
        text += (
            f"👤 {w['name']} (ID: {w['telegram_id']})\n"
            f"💳 {w['payment_method']}: {w['payment_number']}\n"
            f"💰 Balance: {w['balance']} birr\n\n"
        )
    await update.message.reply_text(text)


async def all_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    async with db_pool.acquire() as conn:
        available = await conn.fetchval("SELECT COUNT(*) FROM tasks WHERE status='available'")
        assigned = await conn.fetchval("SELECT COUNT(*) FROM tasks WHERE status='assigned'")
        pending = await conn.fetchval("SELECT COUNT(*) FROM tasks WHERE status='pending_verification'")
        completed = await conn.fetchval("SELECT COUNT(*) FROM tasks WHERE status='completed'")

    await update.message.reply_text(
        f"📦 Task Summary:\n\n"
        f"🟢 Available: {available}\n"
        f"🔵 Assigned: {assigned}\n"
        f"🟡 Pending verification: {pending}\n"
        f"✅ Completed: {completed}"
    )


# --- Keep-alive ping server ---

class PingHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        self.wfile.write(b"OK - bot running")

    def log_message(self, format, *args):
        pass


def start_ping_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), PingHandler)
    print(f"[keep-alive] Ping server on port {port}")
    server.serve_forever()


async def main():
    global db_pool
    threading.Thread(target=start_ping_server, daemon=True).start()

    db_pool = await get_db()
    await init_db(db_pool)

    app = Application.builder().token(BOT_TOKEN).build()

    reg_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            REG_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_name)],
            REG_PAYMENT_METHOD: [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_payment_method)],
            REG_PAYMENT_NUMBER: [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_payment_number)],
        },
        fallbacks=[CommandHandler("start", start)],
    )

    withdraw_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^💸 Withdraw$"), withdraw_start)],
        states={
            WITHDRAW_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, withdraw_amount)],
        },
        fallbacks=[CommandHandler("start", start)],
    )

        app.add_handler(reg_handler)
    app.add_handler(withdraw_handler)
    app.add_handler(MessageHandler(filters.Regex("^📋 Get Task$"), get_task))
    app.add_handler(MessageHandler(filters.Regex("^✅ Done$"), done))
    app.add_handler(MessageHandler(filters.Regex("^💰 My Balance$"), balance))
    app.add_handler(MessageHandler(filters.Regex("^📊 My Stats$"), my_stats))
    app.add_handler(MessageHandler(filters.Regex("^👥 All Workers$"), all_workers))
    app.add_handler(MessageHandler(filters.Regex("^📦 All Tasks$"), all_tasks))
    app.add_handler(MessageHandler(filters.Regex(r"^/confirm_\d+$"), confirm_task))
    app.add_handler(MessageHandler(filters.Regex(r"^/reject_\d+$"), reject_task))
    app.add_handler(MessageHandler(filters.Regex(r"^/paid_\d+$"), paid))

    asyncio.create_task(expire_tasks(db_pool))

    logger.info("Bot started.")
    await app.run_polling()


if __name__ == "__main__":
    asyncio.run(main())
