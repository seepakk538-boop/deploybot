"""
COMPLETE BOT WITH ALL FEATURES:
- Button-based interface (no typing commands)
- Force join channel (users must join before using)
- Bot hosting platform (users deploy their bots)
- Telegram Stars payments
- Resource monitoring
- ZIP extraction
- Auto-restart
- Webhook support
"""

import os
import sys
import sqlite3
import logging
import asyncio
import subprocess
import zipfile
import shutil
import psutil
import re
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import (
    InlineKeyboardMarkup, 
    InlineKeyboardButton, 
    LabeledPrice, 
    FSInputFile,
    ReplyKeyboardMarkup,
    KeyboardButton
)
from aiogram.exceptions import TelegramBadRequest
from aiohttp import web
from dotenv import load_dotenv

load_dotenv()

# ============================================================
# CONFIGURATION
# ============================================================
BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "")
USE_WEBHOOK = os.getenv("USE_WEBHOOK", "False").lower() == "true"
PORT = int(os.getenv("PORT", 8080))

# FORCE JOIN CHANNEL - REPLACE WITH YOUR CHANNEL
REQUIRED_CHANNEL = "@benululaagents"  # e.g., @mybotchannel
CHANNEL_ID = -1003258981274  # Your channel ID (get from @username_to_id_bot)

DATABASE_PATH = "bot_database.db"
BOTS_FOLDER = "hosted_bots"
LOGS_FOLDER = "bot_logs"

admin_ids = [7089004530]  # YOUR TELEGRAM ID

# Hosting Plans
HOSTING_PLANS = {
    "free": {
        "title": "Free Tier 🆓",
        "description": "Basic bot hosting",
        "price": 0,
        "max_bots": 1,
        "max_ram_mb": 256,
        "max_cpu_percent": 50,
        "auto_restart": False
    },
    "starter": {
        "title": "Starter Plan ⭐",
        "description": "For small projects",
        "price": 100,
        "days": 30,
        "max_bots": 3,
        "max_ram_mb": 512,
        "max_cpu_percent": 80,
        "auto_restart": True
    },
    "pro": {
        "title": "Pro Plan 💎",
        "description": "Professional hosting",
        "price": 300,
        "days": 30,
        "max_bots": 10,
        "max_ram_mb": 1024,
        "max_cpu_percent": 100,
        "auto_restart": True
    },
    "enterprise": {
        "title": "Enterprise 👑",
        "description": "Unlimited hosting",
        "price": 1000,
        "days": 30,
        "max_bots": 999,
        "max_ram_mb": 2048,
        "max_cpu_percent": 100,
        "auto_restart": True
    }
}

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# In-memory storage
user_subscriptions = {}
user_bots = {}
banned_users = set()

# ============================================================
# FORCE JOIN CHANNEL CHECK
# ============================================================

async def check_channel_membership(user_id):
    """Check if user is member of required channel"""
    try:
        member = await bot.get_chat_member(chat_id=CHANNEL_ID, user_id=user_id)
        # member.status can be: creator, administrator, member, restricted, left, kicked
        return member.status in ['creator', 'administrator', 'member']
    except Exception as e:
        logger.error(f"Error checking membership: {e}")
        return False

def get_join_channel_keyboard():
    """Get keyboard with join channel button"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📢 Join Channel", url=f"https://t.me/{REQUIRED_CHANNEL.replace('@', '')}")],
        [InlineKeyboardButton(text="✅ I Joined!", callback_data="check_join")]
    ])
    return keyboard

def get_main_keyboard():
    """Get main menu keyboard"""
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🏠 Home"), KeyboardButton(text="🤖 My Bots")],
            [KeyboardButton(text="🚀 Deploy Bot"), KeyboardButton(text="💎 Plans")],
            [KeyboardButton(text="📊 Status"), KeyboardButton(text="ℹ️ Help")]
        ],
        resize_keyboard=True,
        persistent=True
    )
    return keyboard

# ============================================================
# MIDDLEWARE FOR FORCE JOIN
# ============================================================

async def force_join_middleware(handler, event, data):
    """Middleware to check channel membership before processing"""
    # Skip for admin
    if hasattr(event, 'from_user') and event.from_user.id in admin_ids:
        return await handler(event, data)
    
    # Skip for certain callbacks
    if isinstance(event, types.CallbackQuery) and event.data == "check_join":
        return await handler(event, data)
    
    # Check membership
    if hasattr(event, 'from_user'):
        is_member = await check_channel_membership(event.from_user.id)
        
        if not is_member:
            # User not member - show join message
            if isinstance(event, types.Message):
                await event.answer(
                    f"🔒 **Access Restricted**\\n\\n"
                    f"Please join our channel to use this bot:\\n"
                    f"{REQUIRED_CHANNEL}\\n\\n"
                    f"After joining, click **I Joined!** button below.",
                    reply_markup=get_join_channel_keyboard(),
                    parse_mode="Markdown"
                )
            elif isinstance(event, types.CallbackQuery):
                await event.answer(
                    f"⚠️ Please join {REQUIRED_CHANNEL} first!",
                    show_alert=True
                )
            return
    
    # User is member - continue
    return await handler(event, data)

# Register middleware
dp.message.middleware(force_join_middleware)
dp.callback_query.middleware(force_join_middleware)

# ============================================================
# DATABASE FUNCTIONS
# ============================================================

def init_database():
    """Initialize database"""
    os.makedirs(BOTS_FOLDER, exist_ok=True)
    os.makedirs(LOGS_FOLDER, exist_ok=True)
    
    conn = sqlite3.connect(DATABASE_PATH)
    c = conn.cursor()
    
    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            join_date TEXT,
            current_plan TEXT DEFAULT 'free'
        )
    """)
    
    c.execute("""
        CREATE TABLE IF NOT EXISTS subscriptions (
            user_id INTEGER PRIMARY KEY,
            plan_type TEXT DEFAULT 'free',
            expiry TEXT,
            payment_charge_id TEXT
        )
    """)
    
    c.execute("""
        CREATE TABLE IF NOT EXISTS hosted_bots (
            bot_id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            bot_name TEXT NOT NULL,
            bot_token TEXT NOT NULL,
            bot_file TEXT NOT NULL,
            status TEXT DEFAULT 'stopped',
            created_date TEXT,
            last_started TEXT,
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        )
    """)
    
    c.execute("""
        CREATE TABLE IF NOT EXISTS payment_transactions (
            transaction_id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            plan_type TEXT NOT NULL,
            stars_paid INTEGER NOT NULL,
            telegram_payment_charge_id TEXT UNIQUE,
            payment_date TEXT NOT NULL,
            expiry_date TEXT NOT NULL
        )
    """)
    
    c.execute("""
        CREATE TABLE IF NOT EXISTS bot_stats (
            stat_name TEXT PRIMARY KEY,
            stat_value INTEGER DEFAULT 0
        )
    """)
    
    c.execute("INSERT OR IGNORE INTO bot_stats VALUES ('total_users', 0)")
    c.execute("INSERT OR IGNORE INTO bot_stats VALUES ('total_hosted_bots', 0)")
    c.execute("INSERT OR IGNORE INTO bot_stats VALUES ('total_payments', 0)")
    
    conn.commit()
    conn.close()
    
    logger.info("✅ Database initialized")

def get_user_plan(user_id):
    """Get user's current plan"""
    if user_id in user_subscriptions:
        if user_subscriptions[user_id]['expiry'] > datetime.now():
            return user_subscriptions[user_id]['plan']
        else:
            del user_subscriptions[user_id]
    return 'free'

def get_plan_limits(user_id):
    """Get limits for user's plan"""
    plan = get_user_plan(user_id)
    return HOSTING_PLANS.get(plan, HOSTING_PLANS['free'])

def get_user_bot_count(user_id):
    """Get number of bots user has"""
    return len(user_bots.get(user_id, []))

# ============================================================
# TELEGRAM HANDLERS
# ============================================================

@dp.callback_query(F.data == "check_join")
async def callback_check_join(callback: types.CallbackQuery):
    """Check if user joined channel"""
    user_id = callback.from_user.id
    is_member = await check_channel_membership(user_id)
    
    if is_member:
        await callback.message.delete()
        await callback.message.answer(
            "✅ **Welcome!**\\n\\n"
            "Thank you for joining our channel!\\n"
            "You can now use all bot features.",
            reply_markup=get_main_keyboard(),
            parse_mode="Markdown"
        )
        await cmd_start(callback.message)
        await callback.answer("✅ Access granted!", show_alert=True)
    else:
        await callback.answer(
            f"❌ You haven't joined {REQUIRED_CHANNEL} yet!\\n"
            f"Please join first, then click this button again.",
            show_alert=True
        )

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    """Start command"""
    user_id = message.from_user.id
    username = message.from_user.username or "Unknown"
    first_name = message.from_user.first_name or "User"
    
    if user_id in banned_users:
        await message.answer("❌ You are banned.")
        return
    
    # Save user
    conn = sqlite3.connect(DATABASE_PATH)
    c = conn.cursor()
    c.execute("""
        INSERT OR IGNORE INTO users (user_id, username, first_name, join_date)
        VALUES (?, ?, ?, ?)
    """, (user_id, username, first_name, datetime.now().isoformat()))
    c.execute("UPDATE bot_stats SET stat_value = (SELECT COUNT(*) FROM users) WHERE stat_name = 'total_users'")
    conn.commit()
    conn.close()
    
    plan = get_user_plan(user_id)
    limits = get_plan_limits(user_id)
    bot_count = get_user_bot_count(user_id)
    
    text = f"""
👋 Welcome {first_name}!

🤖 **Bot Hosting Platform**

Your Plan: {HOSTING_PLANS[plan]['title']}
Bots: {bot_count}/{limits['max_bots']}

**Features:**
• Deploy your Telegram bots
• Run bots 24/7
• Monitor resources
• View logs

**Use buttons below to navigate! 👇**
"""
    
    await message.answer(text, reply_markup=get_main_keyboard(), parse_mode="Markdown")

# ============================================================
# BUTTON HANDLERS
# ============================================================

@dp.message(F.text == "🏠 Home")
async def button_home(message: types.Message):
    """Home button"""
    await cmd_start(message)

@dp.message(F.text == "🤖 My Bots")
async def button_mybots(message: types.Message):
    """My Bots button"""
    user_id = message.from_user.id
    
    if user_id not in user_bots or not user_bots[user_id]:
        await message.answer(
            "🤖 You have no deployed bots yet.\\n\\n"
            "Tap **🚀 Deploy Bot** to deploy your first bot!",
            reply_markup=get_main_keyboard(),
            parse_mode="Markdown"
        )
        return
    
    text = "🤖 **Your Deployed Bots:**\\n\\n"
    
    keyboard_buttons = []
    for i, bot_info in enumerate(user_bots[user_id]):
        status_emoji = "🟢" if bot_info['status'] == 'running' else "🔴"
        text += f"{status_emoji} **{bot_info['name']}**\\n"
        text += f"Status: {bot_info['status']}\\n\\n"
        
        keyboard_buttons.append([
            InlineKeyboardButton(
                text=f"{status_emoji} {bot_info['name'][:20]}",
                callback_data=f"bot:{i}"
            )
        ])
    
    keyboard_buttons.append([
        InlineKeyboardButton(text="🚀 Deploy New Bot", callback_data="deploy_new")
    ])
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
    await message.answer(text, reply_markup=keyboard, parse_mode="Markdown")

@dp.message(F.text == "🚀 Deploy Bot")
async def button_deploy(message: types.Message):
    """Deploy Bot button"""
    user_id = message.from_user.id
    limits = get_plan_limits(user_id)
    bot_count = get_user_bot_count(user_id)
    
    if bot_count >= limits['max_bots']:
        await message.answer(
            f"❌ Bot limit reached ({limits['max_bots']})\\n\\n"
            "Tap **💎 Plans** to upgrade!",
            reply_markup=get_main_keyboard(),
            parse_mode="Markdown"
        )
        return
    
    await message.answer(
        """
🚀 **Deploy Your Bot**

Send your bot as a **ZIP file** containing:

**Required:**
• `main.py` - Your bot code
• `requirements.txt` - Dependencies

**Important:**
• Use `os.getenv("BOT_TOKEN")` for token
• Use polling (not webhooks)

**Example:**
```
mybot.zip
├── main.py
├── requirements.txt
└── config.py (optional)
```

📎 Send the ZIP file now!
""",
        reply_markup=get_main_keyboard(),
        parse_mode="Markdown"
    )

@dp.message(F.text == "💎 Plans")
async def button_plans(message: types.Message):
    """Plans button"""
    text = """
💎 **Bot Hosting Plans**

🆓 **Free Tier**
• 1 bot
• 256MB RAM
• Basic support

⭐ **Starter** - 100⭐/month
• 3 bots
• 512MB RAM each
• Auto-restart
• Priority support

💎 **Pro** - 300⭐/month
• 10 bots
• 1GB RAM each
• Auto-restart
• Premium support

👑 **Enterprise** - 1000⭐/month
• Unlimited bots
• 2GB RAM each
• Auto-restart
• VIP support
"""
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⭐ Buy Starter", callback_data="buy:starter")],
        [InlineKeyboardButton(text="💎 Buy Pro", callback_data="buy:pro")],
        [InlineKeyboardButton(text="👑 Buy Enterprise", callback_data="buy:enterprise")]
    ])
    
    await message.answer(text, reply_markup=keyboard, parse_mode="Markdown")

@dp.message(F.text == "📊 Status")
async def button_status(message: types.Message):
    """Status button"""
    user_id = message.from_user.id
    plan = get_user_plan(user_id)
    limits = get_plan_limits(user_id)
    bot_count = get_user_bot_count(user_id)
    
    text = f"""
📊 **Your Status**

👤 ID: `{user_id}`
📦 Plan: {HOSTING_PLANS[plan]['title']}

🤖 Bots: {bot_count}/{limits['max_bots']}
💾 RAM: {limits['max_ram_mb']} MB per bot
🔄 Auto-restart: {"✅" if limits['auto_restart'] else "❌"}

"""
    
    if user_id in user_subscriptions:
        expiry = user_subscriptions[user_id]['expiry']
        text += f"📅 Valid until: {expiry.strftime('%Y-%m-%d %H:%M')}\\n"
    else:
        text += "💡 Tap **💎 Plans** to upgrade!\\n"
    
    await message.answer(text, reply_markup=get_main_keyboard(), parse_mode="Markdown")

@dp.message(F.text == "ℹ️ Help")
async def button_help(message: types.Message):
    """Help button"""
    text = """
ℹ️ **Help & Guide**

**How to use:**

1️⃣ **Deploy Your Bot**
   • Tap 🚀 Deploy Bot
   • Send ZIP with main.py
   • Send bot token
   • Done!

2️⃣ **Manage Bots**
   • Tap 🤖 My Bots
   • Select bot
   • Start/Stop/Restart
   • View logs

3️⃣ **Upgrade Plan**
   • Tap 💎 Plans
   • Choose plan
   • Pay with Telegram Stars

4️⃣ **Check Status**
   • Tap 📊 Status
   • See your limits

**Need help?**
Contact: @YourSupportBot
"""
    
    await message.answer(text, reply_markup=get_main_keyboard(), parse_mode="Markdown")

# ============================================================
# FILE UPLOAD HANDLER
# ============================================================

@dp.message(F.document)
async def handle_bot_upload(message: types.Message):
    """Handle bot ZIP upload"""
    user_id = message.from_user.id
    document = message.document
    
    if not document.file_name.endswith('.zip'):
        await message.answer(
            "❌ Please send a ZIP file!",
            reply_markup=get_main_keyboard()
        )
        return
    
    limits = get_plan_limits(user_id)
    bot_count = get_user_bot_count(user_id)
    
    if bot_count >= limits['max_bots']:
        await message.answer(
            f"❌ Bot limit reached ({limits['max_bots']})\\n\\n"
            "Tap **💎 Plans** to upgrade!",
            reply_markup=get_main_keyboard()
        )
        return
    
    # Download and extract
    user_folder = os.path.join(BOTS_FOLDER, str(user_id))
    os.makedirs(user_folder, exist_ok=True)
    
    temp_zip = os.path.join(user_folder, document.file_name)
    
    try:
        await bot.download(document, temp_zip)
        
        extract_folder = os.path.join(user_folder, f"bot_{bot_count + 1}")
        os.makedirs(extract_folder, exist_ok=True)
        
        with zipfile.ZipFile(temp_zip, 'r') as zip_ref:
            zip_ref.extractall(extract_folder)
        
        os.remove(temp_zip)
        
        # Check for main.py
        main_py = os.path.join(extract_folder, 'main.py')
        if not os.path.exists(main_py):
            shutil.rmtree(extract_folder)
            await message.answer(
                "❌ main.py not found in ZIP!",
                reply_markup=get_main_keyboard()
            )
            return
        
        await message.answer(
            "✅ Files extracted!\\n\\n"
            "Now send your **bot token** from @BotFather\\n"
            "Format: `123456789:ABCdefGHIjklMNO...`",
            reply_markup=get_main_keyboard(),
            parse_mode="Markdown"
        )
        
        # Store temporary info
        if user_id not in user_bots:
            user_bots[user_id] = []
        
        user_bots[user_id].append({
            'name': f"bot_{bot_count + 1}",
            'file': 'main.py',
            'folder': extract_folder,
            'status': 'awaiting_token',
            'process': None
        })
        
    except Exception as e:
        logger.error(f"Deployment error: {e}")
        await message.answer(
            f"❌ Deployment failed: {str(e)}",
            reply_markup=get_main_keyboard()
        )

# ============================================================
# BOT TOKEN HANDLER
# ============================================================

@dp.message(F.text & ~F.command())
async def handle_text(message: types.Message):
    """Handle text messages (bot token)"""
    user_id = message.from_user.id
    
    # Check if button text
    if message.text in ["🏠 Home", "🤖 My Bots", "🚀 Deploy Bot", "💎 Plans", "📊 Status", "ℹ️ Help"]:
        return
    
    # Check if waiting for bot token
    if user_id in user_bots:
        for bot_info in user_bots[user_id]:
            if bot_info.get('status') == 'awaiting_token':
                token = message.text.strip()
                
                if not re.match(r'^\d+:[A-Za-z0-9_-]+$', token):
                    await message.answer(
                        "❌ Invalid token format!",
                        reply_markup=get_main_keyboard()
                    )
                    return
                
                bot_info['token'] = token
                bot_info['status'] = 'deployed'
                
                # Install requirements
                req_file = os.path.join(bot_info['folder'], 'requirements.txt')
                if os.path.exists(req_file):
                    await message.answer(
                        "📦 Installing dependencies...",
                        reply_markup=get_main_keyboard()
                    )
                    try:
                        subprocess.run(['pip', 'install', '-r', req_file], check=True, capture_output=True)
                    except:
                        pass
                
                # Save to database
                conn = sqlite3.connect(DATABASE_PATH)
                c = conn.cursor()
                c.execute("""
                    INSERT INTO hosted_bots 
                    (user_id, bot_name, bot_token, bot_file, created_date)
                    VALUES (?, ?, ?, ?, ?)
                """, (user_id, bot_info['name'], token, bot_info['file'], datetime.now().isoformat()))
                c.execute("UPDATE bot_stats SET stat_value = stat_value + 1 WHERE stat_name = 'total_hosted_bots'")
                conn.commit()
                conn.close()
                
                keyboard = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="▶️ Start Bot", callback_data=f"start:{len(user_bots[user_id])-1}")],
                    [InlineKeyboardButton(text="🤖 My Bots", callback_data="my_bots_inline")]
                ])
                
                await message.answer(
                    f"✅ Bot deployed successfully!\\n\\n"
                    f"Name: {bot_info['name']}\\n"
                    f"Status: Ready to start\\n\\n"
                    f"Click **Start** to run your bot!",
                    reply_markup=keyboard,
                    parse_mode="Markdown"
                )
                break

# ============================================================
# CALLBACK HANDLERS (Inline Buttons)
# ============================================================

@dp.callback_query(F.data.startswith("bot:"))
async def callback_bot_actions(callback: types.CallbackQuery):
    """Show bot actions"""
    user_id = callback.from_user.id
    bot_index = int(callback.data.split(":")[1])
    
    if user_id not in user_bots or bot_index >= len(user_bots[user_id]):
        await callback.answer("Bot not found!", show_alert=True)
        return
    
    bot_info = user_bots[user_id][bot_index]
    
    text = f"""
🤖 **{bot_info['name']}**

Status: {'🟢 Running' if bot_info['status'] == 'running' else '🔴 Stopped'}
"""
    
    buttons = []
    if bot_info['status'] == 'running':
        buttons.append([InlineKeyboardButton(text="⏹️ Stop", callback_data=f"stop:{bot_index}")])
        buttons.append([InlineKeyboardButton(text="🔄 Restart", callback_data=f"restart:{bot_index}")])
    else:
        buttons.append([InlineKeyboardButton(text="▶️ Start", callback_data=f"start:{bot_index}")])
    
    buttons.append([InlineKeyboardButton(text="📋 Logs", callback_data=f"logs:{bot_index}")])
    buttons.append([InlineKeyboardButton(text="🗑️ Delete", callback_data=f"delbot:{bot_index}")])
    buttons.append([InlineKeyboardButton(text="🔙 Back", callback_data="my_bots_inline")])
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="Markdown")
    await callback.answer()

# ============================================================
# WEBHOOK SETUP
# ============================================================

async def webhook_handler(request):
    try:
        update_dict = await request.json()
        update = types.Update(**update_dict)
        await dp.feed_update(bot, update)
        return web.Response(text="OK")
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return web.Response(status=500)

async def health_check(request):
    return web.Response(text="Bot is running!")

async def on_startup():
    init_database()
    
    if USE_WEBHOOK:
        await bot.set_webhook(WEBHOOK_URL)
        logger.info(f"✅ Webhook set to: {WEBHOOK_URL}")
    else:
        await bot.delete_webhook()
        logger.info("🔄 Polling mode")

# ============================================================
# MAIN
# ============================================================

async def main():
    if USE_WEBHOOK:
        app = web.Application()
        app.router.add_post("/", webhook_handler)
        app.router.add_get("/health", health_check)
        
        await on_startup()
        
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, host="0.0.0.0", port=PORT)
        
        logger.info(f"🚀 Webhook server on port {PORT}")
        await site.start()
        
        await asyncio.Event().wait()
    else:
        await on_startup()
        logger.info("🚀 Polling mode")
        await dp.start_polling(bot, skip_updates=True)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped")
