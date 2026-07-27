# free_deploy.py
import os
import json
import asyncio
import logging
import secrets
import time
from typing import Optional
from datetime import datetime
from typing import Dict, Set, Optional, Any
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, HTTPException, Depends, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
import uvicorn
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, ContextTypes, CallbackQueryHandler, MessageHandler, filters
import random
from database import db
from game_service import GameService, GameRoom, load_catalog

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Create FastAPI app
app = FastAPI(title="Joy Bingo API")

# Add CORS middleware
ALLOWED_ORIGINS = [x.strip() for x in os.getenv("ALLOWED_ORIGINS", "").split(",") if x.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS or ["http://localhost:8000"],
    allow_credentials=bool(ALLOWED_ORIGINS),
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type"],
)

# Admin configuration
def _parse_admin_ids():
    raw = os.getenv("ADMIN_IDS", "")
    values = []
    for item in raw.split(","):
        item = item.strip()
        if item.isdigit():
            values.append(int(item))
    return values

ADMIN_IDS = _parse_admin_ids()
ADMIN_SECRET_KEY = os.getenv("ADMIN_SECRET_KEY")
ADMIN_TELEGRAM_ID = ADMIN_IDS[0] if ADMIN_IDS else None

# Lightweight in-process API rate limiter. Financial/game validation still happens server-side.
_action_times = {}

def allow_action(user_id: str, per_second: int = 5, per_minute: int = 120) -> bool:
    now = time.time() if "time" in globals() else __import__("time").time()
    history = _action_times.setdefault(str(user_id), [])
    history[:] = [t for t in history if t > now - 60]
    if len(history) >= per_minute or sum(t > now - 1 for t in history) >= per_second:
        return False
    history.append(now)
    return True

# Initialize bot application
BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBAPP_URL = os.getenv("WEBAPP_URL")

# Starting balance for new users
STARTING_BALANCE = 20  # 20 Birr for new registrations

# Create bot application
bot_app = None

# Load cards from cards.json
CARDS_DATA = {}
try:
    with open("cards.json", "r") as f:
        CARDS_DATA = json.load(f)
    logger.info(f"✅ Loaded {len(CARDS_DATA)} cards from cards.json")
except Exception as e:
    logger.error(f"❌ Failed to load cards.json: {e}")
    def generate_sample_card(card_number):
        card = []
        for col in range(5):
            start = col * 15 + 1
            numbers = random.sample(range(start, start + 15), 5)
            card.extend(numbers)
        return card
    
    for i in range(1, 401):
        CARDS_DATA[str(i)] = generate_sample_card(i)
    logger.info(f"✅ Generated {len(CARDS_DATA)} fallback cards")

def verify_admin_token(authorization: Optional[str] = Header(None)):
    """Verify admin token from header"""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    token = authorization.replace("Bearer ", "")
    
    if not ADMIN_SECRET_KEY or token != ADMIN_SECRET_KEY:
        raise HTTPException(status_code=401, detail="Invalid token")
    
    return True

def is_admin_user(user_id: int) -> bool:
    """Check if a Telegram user is admin"""
    return str(user_id) in [str(uid) for uid in ADMIN_IDS]

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = str(user.id)
    
    existing_user = await db.get_user(user_id)
    
    if not existing_user:
        keyboard = [
            [InlineKeyboardButton("📝 REGISTER NOW", callback_data="register")],
            [InlineKeyboardButton("❓ What is Joy Bingo?", callback_data="about")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            f"🎉 Welcome to Joy Bingo, {user.first_name}!\n\n"
            f"You are not registered yet. Click the button below to create your account and get **{STARTING_BALANCE} Birr** starting bonus!",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
        return
    
    logger.info(f"User {user.id} (@{user.username}) started the bot")
    
    keyboard = [
        [InlineKeyboardButton("🎮 PLAY BINGO", web_app=WebAppInfo(url=f"{WEBAPP_URL}/webapp/lobby.html"))],
        [InlineKeyboardButton("💰 My Balance", callback_data="balance"),
         InlineKeyboardButton("📥 Deposit", callback_data="deposit")],
        [InlineKeyboardButton("📤 Withdraw", callback_data="withdraw"),
         InlineKeyboardButton("👤 My Profile", callback_data="profile")],
        [InlineKeyboardButton("📋 Game Rules", callback_data="rules")],
        [InlineKeyboardButton("❓ Help", callback_data="help"),
         InlineKeyboardButton("📞 Contact Support", callback_data="support")]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    balance = existing_user.get("balance", 0) if isinstance(existing_user, dict) else existing_user.balance
    
    await update.message.reply_text(
        f"🎉 Welcome back to Joy Bingo, {user.first_name}!\n\n"
        f"💰 Your current balance: **{balance} Birr**\n"
        f"🎮 Choose an option below:",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def register_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = str(user.id)
    
    existing_user = await db.get_user(user_id)
    if existing_user:
        await update.message.reply_text(
            "✅ You are already registered! Use /start to access the main menu.",
            parse_mode='Markdown'
        )
        return
    
    try:
        new_user = await db.create_user(
            telegram_id=user_id,
            username=user.username,
            first_name=user.first_name,
            last_name=user.last_name
        )
        
        logger.info(f"✅ New user registered: {user_id} with {STARTING_BALANCE} Birr bonus")
        
        keyboard = [
            [InlineKeyboardButton("🎮 PLAY BINGO", web_app=WebAppInfo(url=f"{WEBAPP_URL}/webapp/lobby.html"))],
            [InlineKeyboardButton("💰 My Balance", callback_data="balance"),
             InlineKeyboardButton("📥 Deposit", callback_data="deposit")],
            [InlineKeyboardButton("📤 Withdraw", callback_data="withdraw"),
             InlineKeyboardButton("👤 My Profile", callback_data="profile")],
            [InlineKeyboardButton("📋 Game Rules", callback_data="rules")],
            [InlineKeyboardButton("❓ Help", callback_data="help"),
             InlineKeyboardButton("📞 Contact Support", callback_data="support")]
        ]
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            f"✅ **Registration Successful!**\n\n"
            f"Welcome to Joy Bingo, {user.first_name}!\n"
            f"💰 Your starting balance: **{STARTING_BALANCE} Birr** (Free bonus!)\n\n"
            f"🎮 You can now play bingo and enjoy all features!",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
        
    except Exception as e:
        logger.error(f"❌ Database error during registration: {e}")
        await update.message.reply_text(
            "❌ Registration failed due to database error. Please try again later.",
            parse_mode='Markdown'
        )

async def about_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    about_text = f"""
🎮 **About Joy Bingo**
═══════════════════

**What is Joy Bingo?**
Joy Bingo is a fun and exciting Telegram-based bingo game where you can play with friends and win real prizes!

**Features:**
• 🎯 Play classic bingo with 400 unique cards
• 💰 **Get {STARTING_BALANCE} Birr free** when you register!
• 💰 Deposit and withdraw funds via Telebirr or CBE Birr
• 👤 View your profile and statistics
• 🎮 Easy-to-use WebApp interface

**How to Play:**
1. Register for free (get {STARTING_BALANCE} Birr bonus)
2. Deposit funds to buy cards
3. Select a card and start playing
4. Mark numbers as they're called
5. Get BINGO to win!

**💳 Payment Methods:**
• Telebirr: Send to 0948813201
• CBE Birr: Send to 0948813201
• After payment, send transaction ID to complete deposit

**Fair Play:**
• All games are verified
• Random number generation is fair
• 80% of pot goes to winners
• 20% platform fee

Ready to play? Click the Register button below to get your free {STARTING_BALANCE} Birr!
"""
    
    keyboard = [[InlineKeyboardButton("📝 Register Now", callback_data="register")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        about_text,
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = f"""
🎮 **JOY BINGO - HELP & COMMANDS**
═══════════════════════════

**📋 AVAILABLE COMMANDS:**
• `/start` - Main menu
• `/register` - Register new account (get {STARTING_BALANCE} Birr free!)
• `/play` - Play bingo
• `/balance` - Check your balance
• `/deposit` - Add funds
• `/withdraw` - Withdraw winnings
• `/profile` - View your profile
• `/rules` - Game rules
• `/help` - This help menu

**🎯 HOW TO PLAY:**
1. Register with /register (get {STARTING_BALANCE} Birr free!)
2. Click "PLAY BINGO" button
3. Select a card (costs 10 Birr)
4. Numbers are called every 3 seconds
5. Click numbers on your card to mark them
6. Get BINGO to win!

**💰 DEPOSIT & WITHDRAW:**
• Minimum deposit: 10 Birr
• Minimum withdrawal: 50 Birr
• Payment Methods: Telebirr / CBE Birr
• Payment Number: `0948813201`
• Withdrawals processed within 24h

**🏆 PRIZES:**
• Winner takes 80% of the pot
• Multiple winners split the prize

Need more help? Contact @admin
"""
    await update.message.reply_text(help_text, parse_mode='Markdown')

async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    
    if user_id not in [str(uid) for uid in ADMIN_IDS]:
        await update.message.reply_text("❌ You are not authorized to access the admin panel.")
        return
    
    keyboard = [[InlineKeyboardButton("🔐 Open Admin Panel", web_app=WebAppInfo(url=f"{WEBAPP_URL}/webapp/admin_login.html"))]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "🔐 **Admin Panel Access**\n\n"
        "Click the button below to open the admin panel:",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def id_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    await update.message.reply_text(
        f"👤 **Your Telegram Information**\n\n"
        f"🆔 User ID: `{user_id}`\n"
        f"📝 First Name: {user.first_name}\n"
        f"🔤 Username: @{user.username if user.username else 'N/A'}\n\n"
        f"Copy your ID for manual admin login.",
        parse_mode='Markdown'
    )

async def play_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    
    existing_user = await db.get_user(user_id)
    
    if not existing_user:
        keyboard = [[InlineKeyboardButton("📝 Register First", callback_data="register")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            "❌ You need to register first!\n\n"
            "Click the button below to register and get free 20 Birr:",
            reply_markup=reply_markup
        )
        return
    
    keyboard = [[InlineKeyboardButton("🎮 PLAY BINGO", web_app=WebAppInfo(url=f"{WEBAPP_URL}/webapp/lobby.html"))]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "🎮 Click below to enter the game lobby:",
        reply_markup=reply_markup
    )

async def balance_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    
    try:
        user = await db.get_user(user_id)
        
        if not user:
            keyboard = [[InlineKeyboardButton("📝 Register Now", callback_data="register")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await update.message.reply_text(
                "❌ You need to register first!\n\n"
                "Click the button below to register and get free 20 Birr:",
                reply_markup=reply_markup
            )
            return
        
        balance = user.get("balance", 0) if isinstance(user, dict) else user.balance
        total_deposits = user.get("total_deposits", 0) if isinstance(user, dict) else user.total_deposits
        total_withdrawals = user.get("total_withdrawals", 0) if isinstance(user, dict) else user.total_withdrawals
        games_played = user.get("games_played", 0) if isinstance(user, dict) else user.games_played
        games_won = user.get("games_won", 0) if isinstance(user, dict) else user.games_won
        
        keyboard = [
            [InlineKeyboardButton("📥 Deposit", callback_data="deposit"),
             InlineKeyboardButton("📤 Withdraw", callback_data="withdraw")],
            [InlineKeyboardButton("🎮 Play Bingo", web_app=WebAppInfo(url=f"{WEBAPP_URL}/webapp/lobby.html"))],
            [InlineKeyboardButton("◀️ Back to Menu", callback_data="back_to_menu")]
        ]
        
        await update.message.reply_text(
            f"💰 **YOUR BALANCE**\n\n"
            f"Current Balance: **{balance} Birr**\n"
            f"Total Deposits: **{total_deposits} Birr**\n"
            f"Total Withdrawals: **{total_withdrawals} Birr**\n"
            f"Games Played: **{games_played}**\n"
            f"Wins: **{games_won}**",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
        
    except Exception as e:
        logger.error(f"❌ Database error in balance command: {e}")
        await update.message.reply_text(
            "❌ Error fetching balance. Please try again.",
            parse_mode='Markdown'
        )

async def deposit_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /deposit command - Deposit funds with payment options"""
    user_id = str(update.effective_user.id)
    
    existing_user = await db.get_user(user_id)
    
    if not existing_user:
        keyboard = [[InlineKeyboardButton("📝 Register Now", callback_data="register")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            "❌ You need to register first!\n\n"
            "Click the button below to register and get free 20 Birr:",
            reply_markup=reply_markup
        )
        return
    
    keyboard = [
        [InlineKeyboardButton("💳 Telebirr", callback_data="deposit_telebirr"),
         InlineKeyboardButton("💳 CBE Birr", callback_data="deposit_cbe")],
        [InlineKeyboardButton("📱 Send Money to", callback_data="payment_info"),
         InlineKeyboardButton("ℹ️ Payment Instructions", callback_data="payment_instructions")],
        [InlineKeyboardButton("✅ I've Made Payment", callback_data="payment_submitted")],
        [InlineKeyboardButton("◀️ Back", callback_data="balance")]
    ]
    
    await update.message.reply_text(
        f"📥 **DEPOSIT FUNDS**\n\n"
        f"Select your payment method:\n\n"
        f"💳 **Telebirr** - Fast and secure\n"
        f"💳 **CBE Birr** - Convenient mobile banking\n\n"
        f"📱 **Payment Number:** `0948813201`\n\n"
        f"After sending payment, click 'I've Made Payment' to confirm.",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )

async def withdraw_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /withdraw command - Withdraw funds with payment options"""
    user_id = str(update.effective_user.id)
    
    existing_user = await db.get_user(user_id)
    
    if not existing_user:
        keyboard = [[InlineKeyboardButton("📝 Register Now", callback_data="register")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            "❌ You need to register first!\n\n"
            "Click the button below to register and get free 20 Birr:",
            reply_markup=reply_markup
        )
        return
    
    balance = existing_user.get("balance", 0) if isinstance(existing_user, dict) else existing_user.balance
    
    keyboard = [
        [InlineKeyboardButton("💳 Withdraw to Telebirr", callback_data="withdraw_telebirr"),
         InlineKeyboardButton("💳 Withdraw to CBE Birr", callback_data="withdraw_cbe")],
        [InlineKeyboardButton("📝 Withdrawal Instructions", callback_data="withdraw_instructions")],
        [InlineKeyboardButton("📋 Withdrawal History", callback_data="withdraw_history")],
        [InlineKeyboardButton("◀️ Back", callback_data="balance")]
    ]
    
    await update.message.reply_text(
        f"📤 **WITHDRAW FUNDS**\n\n"
        f"Available Balance: **{balance} Birr**\n"
        f"Minimum Withdrawal: **50 Birr**\n\n"
        f"Select your preferred withdrawal method:\n\n"
        f"💳 **Telebirr** - Receive directly to your Telebirr account\n"
        f"💳 **CBE Birr** - Receive to your CBE Birr account\n\n"
        f"📱 **Payment Number:** `0948813201` for verification",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )

async def profile_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = str(user.id)
    
    existing_user = await db.get_user(user_id)
    
    if not existing_user:
        keyboard = [[InlineKeyboardButton("📝 Register Now", callback_data="register")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            "❌ You need to register first!\n\n"
            "Click the button below to register and get free 20 Birr:",
            reply_markup=reply_markup
        )
        return
    
    stats = existing_user
    games_played = stats.get("games_played", 0) if isinstance(stats, dict) else stats.games_played
    games_won = stats.get("games_won", 0) if isinstance(stats, dict) else stats.games_won
    win_rate = (games_won / games_played * 100) if games_played > 0 else 0
    
    first_name = stats.get("first_name", user.first_name) if isinstance(stats, dict) else stats.first_name
    last_name = stats.get("last_name", user.last_name) if isinstance(stats, dict) else stats.last_name
    username = stats.get("username", user.username) if isinstance(stats, dict) else stats.username
    balance = stats.get("balance", 0) if isinstance(stats, dict) else stats.balance
    total_deposits = stats.get("total_deposits", 0) if isinstance(stats, dict) else stats.total_deposits
    total_withdrawals = stats.get("total_withdrawals", 0) if isinstance(stats, dict) else stats.total_withdrawals
    registered_at = stats.get("created_at", datetime.now().isoformat()) if isinstance(stats, dict) else stats.created_at
    
    profile_text = f"""
👤 **USER PROFILE**
══════════════════

**Personal Info:**
• Name: {first_name} {last_name or ''}
• Username: @{username or 'N/A'}
• User ID: `{user_id}`
• Registered: {registered_at[:10] if registered_at else 'N/A'}

**Game Statistics:**
• Games Played: {games_played}
• Wins: {games_won}
• Win Rate: {win_rate:.1f}%

**Financial:**
• Current Balance: {balance} Birr
• Total Deposits: {total_deposits} Birr
• Total Withdrawals: {total_withdrawals} Birr
"""
    
    keyboard = [[InlineKeyboardButton("◀️ Back to Menu", callback_data="back_to_menu")]]
    
    await update.message.reply_text(
        profile_text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )

async def rules_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rules_text = """
📋 **JOY BINGO - RULES**
══════════════════════

**🎯 OBJECTIVE:**
Mark all numbers in a row, column, or diagonal to win!

**🃏 CARD SELECTION:**
• Choose from 400 unique cards
• Each card costs 10 Birr
• FREE space (⭐) is automatically marked

**🔢 NUMBER CALLING:**
• Numbers 1-75 are called randomly
• New number every 3 seconds
• Called numbers turn green on the board

**✅ MARKING NUMBERS:**
• Click numbers on your card to mark them
• Numbers must be called first
• Marked numbers turn green

**🏆 WINNING:**
• First player to complete a line wins!
• Multiple winners split the prize pool
• Prize pool = 80% of total bets

**💰 PRIZE DISTRIBUTION:**
• 80% to winners
• 20% platform fee

**⚠️ FAIR PLAY:**
• All games are verified
• Random number generation is fair
• Cheating results in ban

Good luck and have fun! 🎮
"""
    keyboard = [[InlineKeyboardButton("🎮 Play Now", web_app=WebAppInfo(url=f"{WEBAPP_URL}/webapp/lobby.html"))]]
    
    await update.message.reply_text(
        rules_text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = str(update.effective_user.id)
    user = update.effective_user
    data = query.data
    
    if data == "register":
        existing_user = await db.get_user(user_id)
        if existing_user:
            await query.edit_message_text(
                "✅ You are already registered! Use /start to access the main menu.",
                parse_mode='Markdown'
            )
            return
        
        try:
            new_user = await db.create_user(
                telegram_id=user_id,
                username=user.username,
                first_name=user.first_name,
                last_name=user.last_name
            )
            
            keyboard = [
                [InlineKeyboardButton("🎮 PLAY BINGO", web_app=WebAppInfo(url=f"{WEBAPP_URL}/webapp/lobby.html"))],
                [InlineKeyboardButton("💰 My Balance", callback_data="balance"),
                 InlineKeyboardButton("📥 Deposit", callback_data="deposit")],
                [InlineKeyboardButton("📤 Withdraw", callback_data="withdraw"),
                 InlineKeyboardButton("👤 My Profile", callback_data="profile")],
                [InlineKeyboardButton("📋 Game Rules", callback_data="rules")],
                [InlineKeyboardButton("❓ Help", callback_data="help"),
                 InlineKeyboardButton("📞 Contact Support", callback_data="support")]
            ]
            
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(
                f"✅ **Registration Successful!**\n\n"
                f"Welcome to Joy Bingo, {user.first_name}!\n"
                f"💰 Your starting balance: **{STARTING_BALANCE} Birr** (Free bonus!)\n\n"
                f"🎮 You can now play bingo and enjoy all features!",
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )
            return
            
        except Exception as e:
            logger.error(f"❌ Database error during registration: {e}")
            await query.edit_message_text(
                "❌ Registration failed due to database error. Please try again later.",
                parse_mode='Markdown'
            )
            return
    
    if data == "about":
        about_text = f"""
🎮 **About Joy Bingo**
═══════════════════

**What is Joy Bingo?**
Joy Bingo is a fun and exciting Telegram-based bingo game where you can play with friends and win real prizes!

**Features:**
• 🎯 Play classic bingo with 400 unique cards
• 💰 **Get {STARTING_BALANCE} Birr free** when you register!
• 💰 Deposit and withdraw funds via Telebirr or CBE Birr
• 👤 View your profile and statistics
• 🎮 Easy-to-use WebApp interface

**How to Play:**
1. Register for free (get {STARTING_BALANCE} Birr bonus)
2. Deposit funds to buy cards
3. Select a card and start playing
4. Mark numbers as they're called
5. Get BINGO to win!

**💳 Payment Methods:**
• Telebirr: Send to 0948813201
• CBE Birr: Send to 0948813201
• After payment, send transaction ID to complete deposit

**Fair Play:**
• All games are verified
• Random number generation is fair
• 80% of pot goes to winners
• 20% platform fee

Ready to play? Click the Register button below to get your free {STARTING_BALANCE} Birr!
"""
        keyboard = [[InlineKeyboardButton("📝 Register Now", callback_data="register")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            about_text,
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
        return
    
    # ============= PAYMENT HANDLERS =============
    
    if data == "payment_info":
        await query.edit_message_text(
            f"💳 **PAYMENT INFORMATION**\n\n"
            f"📱 **Payment Number:** `0948813201`\n\n"
            f"**Supported Methods:**\n"
            f"• Telebirr\n"
            f"• CBE Birr\n\n"
            f"**How to Deposit:**\n"
            f"1. Open Telebirr or CBE Birr app\n"
            f"2. Send the desired amount to `0948813201`\n"
            f"3. Note your transaction ID\n"
            f"4. Click 'I've Made Payment' and enter your transaction ID\n"
            f"5. Wait for confirmation (within 5 minutes)\n\n"
            f"**Minimum Deposit:** 10 Birr\n"
            f"**Maximum Deposit:** 10,000 Birr\n\n"
            f"⚠️ Always include your Telegram username in the payment reference!",
            parse_mode='Markdown'
        )
        return
    
    if data == "payment_instructions":
        await query.edit_message_text(
            f"📋 **PAYMENT INSTRUCTIONS**\n\n"
            f"**Telebirr Instructions:**\n"
            f"1. Open Telebirr app\n"
            f"2. Tap 'Send Money'\n"
            f"3. Enter number: `0948813201`\n"
            f"4. Enter amount (10-10000 Birr)\n"
            f"5. Add reference: Your Telegram username\n"
            f"6. Confirm and send\n\n"
            f"**CBE Birr Instructions:**\n"
            f"1. Open CBE Birr app\n"
            f"2. Tap 'Transfer'\n"
            f"3. Enter recipient: `0948813201`\n"
            f"4. Enter amount\n"
            f"5. Add note: Your Telegram username\n"
            f"6. Confirm transfer\n\n"
            f"After sending, click 'I've Made Payment' and provide your transaction ID.",
            parse_mode='Markdown'
        )
        return
    
    if data == "payment_submitted":
        await query.edit_message_text(
            f"✅ **PAYMENT CONFIRMATION**\n\n"
            f"Please send the following information:\n\n"
            f"1️⃣ Transaction ID\n"
            f"2️⃣ Amount sent\n"
            f"3️⃣ Payment method (Telebirr/CBE Birr)\n\n"
            f"Example: `TXN123456789 - 100 Birr - Telebirr`\n\n"
            f"Send this information as a message, and our system will verify your payment.",
            parse_mode='Markdown'
        )
        context.user_data['awaiting_payment_confirmation'] = True
        return
    
    if data == "withdraw_telebirr":
        await query.edit_message_text(
            f"📤 **WITHDRAW TO TELEBIRR**\n\n"
            f"Please send your withdrawal details:\n\n"
            f"1️⃣ Amount (minimum 50 Birr)\n"
            f"2️⃣ Your Telebirr phone number\n\n"
            f"Example: `200 - 0912345678`\n\n"
            f"Your withdrawal will be processed within 24 hours.",
            parse_mode='Markdown'
        )
        context.user_data['awaiting_withdraw_telebirr'] = True
        return
    
    if data == "withdraw_cbe":
        await query.edit_message_text(
            f"📤 **WITHDRAW TO CBE BIRR**\n\n"
            f"Please send your withdrawal details:\n\n"
            f"1️⃣ Amount (minimum 50 Birr)\n"
            f"2️⃣ Your CBE Birr phone number\n\n"
            f"Example: `200 - 0912345678`\n\n"
            f"Your withdrawal will be processed within 24 hours.",
            parse_mode='Markdown'
        )
        context.user_data['awaiting_withdraw_cbe'] = True
        return
    
    if data == "withdraw_instructions":
        await query.edit_message_text(
            f"📋 **WITHDRAWAL INSTRUCTIONS**\n\n"
            f"**To withdraw your winnings:**\n\n"
            f"1. Minimum withdrawal: 50 Birr\n"
            f"2. Choose your preferred method (Telebirr or CBE Birr)\n"
            f"3. Enter your amount and phone number\n"
            f"4. Your request will be processed within 24 hours\n"
            f"5. You'll receive a confirmation when processed\n\n"
            f"⚠️ Make sure your phone number is correct to avoid delays.",
            parse_mode='Markdown'
        )
        return
    
    if data == "withdraw_history":
        await query.edit_message_text(
            f"📋 **WITHDRAWAL HISTORY**\n\n"
            f"To view your withdrawal history, please check the admin panel or contact support.\n\n"
            f"Recent withdrawals will appear here soon.",
            parse_mode='Markdown'
        )
        return
    
    # Get user from database for other callbacks
    db_user = await db.get_user(user_id)
    
    if not db_user:
        keyboard = [[InlineKeyboardButton("📝 Register Now", callback_data="register")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(
            "❌ You need to register first!\n\n"
            "Click the button below to register and get free 20 Birr:",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
        return
    
    if data == "balance":
        balance = db_user.get("balance", 0) if isinstance(db_user, dict) else db_user.balance
        total_deposits = db_user.get("total_deposits", 0) if isinstance(db_user, dict) else db_user.total_deposits
        total_withdrawals = db_user.get("total_withdrawals", 0) if isinstance(db_user, dict) else db_user.total_withdrawals
        games_played = db_user.get("games_played", 0) if isinstance(db_user, dict) else db_user.games_played
        games_won = db_user.get("games_won", 0) if isinstance(db_user, dict) else db_user.games_won
        
        keyboard = [
            [InlineKeyboardButton("📥 Deposit", callback_data="deposit"),
             InlineKeyboardButton("📤 Withdraw", callback_data="withdraw")],
            [InlineKeyboardButton("🎮 Play Bingo", web_app=WebAppInfo(url=f"{WEBAPP_URL}/webapp/lobby.html"))],
            [InlineKeyboardButton("◀️ Back to Menu", callback_data="back_to_menu")]
        ]
        await query.edit_message_text(
            f"💰 **YOUR BALANCE**\n\n"
            f"Current Balance: **{balance} Birr**\n"
            f"Total Deposits: **{total_deposits} Birr**\n"
            f"Total Withdrawals: **{total_withdrawals} Birr**\n"
            f"Games Played: **{games_played}**\n"
            f"Wins: **{games_won}**",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
    
    elif data == "deposit":
        keyboard = [
            [InlineKeyboardButton("💳 Telebirr", callback_data="deposit_telebirr"),
             InlineKeyboardButton("💳 CBE Birr", callback_data="deposit_cbe")],
            [InlineKeyboardButton("📱 Send Money to", callback_data="payment_info"),
             InlineKeyboardButton("ℹ️ Payment Instructions", callback_data="payment_instructions")],
            [InlineKeyboardButton("✅ I've Made Payment", callback_data="payment_submitted")],
            [InlineKeyboardButton("◀️ Back", callback_data="balance")]
        ]
        await query.edit_message_text(
            f"📥 **DEPOSIT FUNDS**\n\n"
            f"Select your payment method:\n\n"
            f"💳 **Telebirr** - Fast and secure\n"
            f"💳 **CBE Birr** - Convenient mobile banking\n\n"
            f"📱 **Payment Number:** `0948813201`\n\n"
            f"After sending payment, click 'I've Made Payment' to confirm.",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
    
    elif data == "withdraw":
        balance = db_user.get("balance", 0) if isinstance(db_user, dict) else db_user.balance
        keyboard = [
            [InlineKeyboardButton("💳 Withdraw to Telebirr", callback_data="withdraw_telebirr"),
             InlineKeyboardButton("💳 Withdraw to CBE Birr", callback_data="withdraw_cbe")],
            [InlineKeyboardButton("📝 Withdrawal Instructions", callback_data="withdraw_instructions")],
            [InlineKeyboardButton("📋 Withdrawal History", callback_data="withdraw_history")],
            [InlineKeyboardButton("◀️ Back", callback_data="balance")]
        ]
        await query.edit_message_text(
            f"📤 **WITHDRAW FUNDS**\n\n"
            f"Available Balance: **{balance} Birr**\n"
            f"Minimum Withdrawal: **50 Birr**\n\n"
            f"Select your preferred withdrawal method:",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
    
    elif data.startswith("deposit_"):
        amount = data.replace("deposit_", "")
        if amount in ["telebirr", "cbe"]:
            method = "Telebirr" if amount == "telebirr" else "CBE Birr"
            await query.edit_message_text(
                f"📥 **DEPOSIT via {method}**\n\n"
                f"To complete your deposit:\n\n"
                f"1. Open your {method} app\n"
                f"2. Send the amount to `0948813201`\n"
                f"3. Include your Telegram username in reference\n"
                f"4. Click 'I've Made Payment' and provide transaction ID\n\n"
                f"Your balance will be updated after verification.",
                parse_mode='Markdown'
            )
        return
    
    elif data == "profile":
        stats = db_user
        games_played = stats.get("games_played", 0) if isinstance(stats, dict) else stats.games_played
        games_won = stats.get("games_won", 0) if isinstance(stats, dict) else stats.games_won
        win_rate = (games_won / games_played * 100) if games_played > 0 else 0
        
        first_name = stats.get("first_name", user.first_name) if isinstance(stats, dict) else stats.first_name
        last_name = stats.get("last_name", user.last_name) if isinstance(stats, dict) else stats.last_name
        username = stats.get("username", user.username) if isinstance(stats, dict) else stats.username
        balance = stats.get("balance", 0) if isinstance(stats, dict) else stats.balance
        total_deposits = stats.get("total_deposits", 0) if isinstance(stats, dict) else stats.total_deposits
        total_withdrawals = stats.get("total_withdrawals", 0) if isinstance(stats, dict) else stats.total_withdrawals
        registered_at = stats.get("created_at", datetime.now().isoformat()) if isinstance(stats, dict) else stats.created_at
        
        profile_text = f"""
👤 **USER PROFILE**
══════════════════

**Personal Info:**
• Name: {first_name} {last_name or ''}
• Username: @{username or 'N/A'}
• User ID: `{user_id}`
• Registered: {registered_at[:10] if registered_at else 'N/A'}

**Game Statistics:**
• Games Played: {games_played}
• Wins: {games_won}
• Win Rate: {win_rate:.1f}%

**Financial:**
• Current Balance: {balance} Birr
• Total Deposits: {total_deposits} Birr
• Total Withdrawals: {total_withdrawals} Birr
"""
        keyboard = [[InlineKeyboardButton("◀️ Back to Menu", callback_data="back_to_menu")]]
        await query.edit_message_text(
            profile_text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
    
    elif data == "rules":
        rules_text = """
📋 **JOY BINGO - RULES**
══════════════════════

**🎯 OBJECTIVE:**
Mark all numbers in a row, column, or diagonal to win!

**🃏 CARD SELECTION:**
• Choose from 400 unique cards
• Each card costs 10 Birr
• FREE space (⭐) is automatically marked

**🔢 NUMBER CALLING:**
• Numbers 1-75 are called randomly
• New number every 3 seconds
• Called numbers turn green on the board

**✅ MARKING NUMBERS:**
• Click numbers on your card to mark them
• Numbers must be called first
• Marked numbers turn green

**🏆 WINNING:**
• First player to complete a line wins!
• Multiple winners split the prize pool
• Prize pool = 80% of total bets

**💰 PRIZE DISTRIBUTION:**
• 80% to winners
• 20% platform fee
"""
        keyboard = [[InlineKeyboardButton("🎮 Play Now", web_app=WebAppInfo(url=f"{WEBAPP_URL}/webapp/lobby.html"))]]
        await query.edit_message_text(
            rules_text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
    
    elif data == "help":
        help_text = """
🎮 **JOY BINGO - HELP**
═══════════════════

**COMMANDS:**
• /start - Main menu
• /play - Play bingo
• /balance - Check balance
• /deposit - Add funds
• /withdraw - Withdraw
• /profile - Your stats
• /rules - Game rules
• /help - This menu

**SUPPORT:**
• Email: support@joybingo.com
• Telegram: @joybingo_support

**Need assistance?** Contact our support team!
"""
        keyboard = [[InlineKeyboardButton("◀️ Back to Menu", callback_data="back_to_menu")]]
        await query.edit_message_text(
            help_text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
    
    elif data == "support":
        support_text = f"""
📞 **CONTACT SUPPORT**
══════════════════

**How can we help you?**

**Common Issues:**
• Deposit problems - Provide transaction ID
• Withdrawal issues - Check balance and phone number
• Game questions
• Technical support
• Account issues

**Contact Methods:**
• Email: support@joybingo.com
• Telegram: @joybingo_support
• Response time: 24 hours

**Payment Number:** `0948813201`

Please include your User ID and transaction ID when contacting support.
"""
        keyboard = [[InlineKeyboardButton("📝 Send Message", callback_data="send_support_message")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            support_text,
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
    
    elif data == "send_support_message":
        await query.edit_message_text(
            f"📝 **SEND MESSAGE TO SUPPORT**\n\n"
            f"Please write your message below.\n\n"
            f"Include details about your issue:\n"
            f"• For deposits: transaction ID, amount, method\n"
            f"• For withdrawals: amount, phone number\n"
            f"• For game issues: describe the problem\n\n"
            f"Your message will be sent directly to admin.",
            parse_mode='Markdown'
        )
        context.user_data['awaiting_support_message'] = True
    
    elif data == "back_to_menu":
        balance = db_user.get("balance", 0) if isinstance(db_user, dict) else db_user.balance
        
        keyboard = [
            [InlineKeyboardButton("🎮 PLAY BINGO", web_app=WebAppInfo(url=f"{WEBAPP_URL}/webapp/lobby.html"))],
            [InlineKeyboardButton("💰 My Balance", callback_data="balance"),
             InlineKeyboardButton("📥 Deposit", callback_data="deposit")],
            [InlineKeyboardButton("📤 Withdraw", callback_data="withdraw"),
             InlineKeyboardButton("👤 My Profile", callback_data="profile")],
            [InlineKeyboardButton("📋 Game Rules", callback_data="rules")],
            [InlineKeyboardButton("❓ Help", callback_data="help"),
             InlineKeyboardButton("📞 Contact Support", callback_data="support")]
        ]
        
        await query.edit_message_text(
            f"🎉 Welcome back!\n\n"
            f"💰 Your current balance: **{balance} Birr**\n"
            f"🎮 Choose an option below:",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )

async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    text = update.message.text.strip()
    user = update.effective_user
    
    db_user = await db.get_user(user_id)
    
    # Support message handler - forward to admin
    if context.user_data.get('awaiting_support_message'):
        # Forward message to admin
        admin_id = ADMIN_TELEGRAM_ID
        if not admin_id:
            await update.message.reply_text("Support is not configured yet. Please try again later.")
            return
        
        support_message = f"""
📞 **SUPPORT MESSAGE FROM USER**

👤 **User Info:**
• ID: `{user_id}`
• Username: @{user.username or 'N/A'}
• Name: {user.first_name} {user.last_name or ''}

💰 **User Balance:** {db_user.get('balance', 0) if db_user else 'N/A'} Birr

📝 **Message:**
{text}

⏰ **Time:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

---
Reply to this user by sending /reply_{user_id} [your message]
"""
        
        try:
            await bot_app.bot.send_message(
                chat_id=admin_id,
                text=support_message,
                parse_mode='Markdown'
            )
            
            await update.message.reply_text(
                f"✅ **Message Sent!**\n\n"
                f"Your message has been sent to support.\n"
                f"We will get back to you within 24 hours.\n\n"
                f"Please keep your User ID: `{user_id}` for reference.",
                parse_mode='Markdown'
            )
            context.user_data['awaiting_support_message'] = False
            
        except Exception as e:
            logger.error(f"Failed to send support message: {e}")
            await update.message.reply_text(
                "❌ Failed to send message. Please try again later or contact @joybingo_support directly.",
                parse_mode='Markdown'
            )
        return
    
    # Payment confirmation / withdrawal request handlers.
    if context.user_data.get('awaiting_payment_confirmation'):
        try:
            parts = [x.strip() for x in text.split(' - ')]
            if len(parts) < 3:
                raise ValueError
            transaction_id, amount_text, method = parts[0], parts[1], parts[2]
            amount_num = int(amount_text)
            if not 10 <= amount_num <= 10000:
                raise ValueError
            if method.lower() not in {"telebirr", "cbe", "cbe birr"}:
                raise ValueError
            method = "Telebirr" if method.lower() == "telebirr" else "CBE Birr"
            request_row = await db.create_deposit_request(user_id, amount_num, method, transaction_id)
            if not request_row:
                await update.message.reply_text("❌ This transaction ID is already submitted or your account is unavailable.")
                return
            context.user_data['awaiting_payment_confirmation'] = False
            await update.message.reply_text(
                f"✅ Deposit request submitted.\n\nAmount: **{amount_num} Birr**\nMethod: {method}\nTransaction ID: `{transaction_id}`\n\nYour balance will change only after admin verification.",
                parse_mode='Markdown'
            )
            if ADMIN_TELEGRAM_ID:
                await bot_app.bot.send_message(
                    chat_id=ADMIN_TELEGRAM_ID,
                    text=(f"💰 DEPOSIT REQUEST\n\nUser: @{user.username or user.first_name}\n"
                           f"ID: `{user_id}`\nMethod: {method}\nAmount: {amount_num} Birr\n"
                           f"TXN: `{transaction_id}`\nRequest ID: {request_row['id']}"),
                    parse_mode='Markdown'
                )
        except (ValueError, TypeError):
            await update.message.reply_text("❌ Format: `TXN123 - 100 - Telebirr`", parse_mode='Markdown')
        return

    if context.user_data.get('awaiting_withdraw_telebirr') or context.user_data.get('awaiting_withdraw_cbe'):
        method = "Telebirr" if context.user_data.get('awaiting_withdraw_telebirr') else "CBE Birr"
        try:
            parts = [x.strip() for x in text.split(' - ')]
            if len(parts) < 2:
                raise ValueError
            amount = int(parts[0])
            phone = parts[1]
            if amount < 50 or amount > 100000 or not phone:
                raise ValueError
            request_row = await db.create_withdrawal_request(user_id, amount, method, phone)
            if not request_row:
                await update.message.reply_text("❌ Insufficient balance or withdrawal could not be created.")
                return
            context.user_data['awaiting_withdraw_telebirr'] = False
            context.user_data['awaiting_withdraw_cbe'] = False
            updated_user = await db.get_user(user_id)
            new_balance = updated_user.get("balance", 0) if updated_user else 0
            await update.message.reply_text(
                f"✅ **Withdrawal request submitted.**\n\nAmount: **{amount} Birr**\nTo: {phone} ({method})\n"
                f"Available balance after reservation: **{new_balance} Birr**\n\nAdmin approval is required.",
                parse_mode='Markdown'
            )
            if ADMIN_TELEGRAM_ID:
                await bot_app.bot.send_message(
                    chat_id=ADMIN_TELEGRAM_ID,
                    text=(f"📤 WITHDRAWAL REQUEST\n\nUser: @{user.username or user.first_name}\n"
                           f"ID: `{user_id}`\nMethod: {method}\nPhone: {phone}\nAmount: {amount} Birr\n"
                           f"Request ID: {request_row['id']}"),
                    parse_mode='Markdown'
                )
        except (ValueError, TypeError):
            await update.message.reply_text("❌ Format: `200 - 0912345678`", parse_mode='Markdown')
        return

    if context.user_data.get('awaiting_deposit'):
        # Legacy flow now creates a pending request instead of crediting money automatically.
        try:
            amount = int(text)
            if not 10 <= amount <= 10000:
                raise ValueError
            context.user_data['awaiting_deposit'] = False
            context.user_data['awaiting_payment_confirmation'] = True
            await update.message.reply_text("Send the payment reference in this format: `TXN123 - 100 - Telebirr`", parse_mode='Markdown')
        except ValueError:
            await update.message.reply_text("❌ Please enter an amount between 10 and 10000 Birr.")
        return

    if context.user_data.get('awaiting_withdraw'):
        try:
            amount = int(text)
            if amount < 50:
                raise ValueError
            request_row = await db.create_withdrawal_request(user_id, amount, "Unknown", "Pending details")
            if not request_row:
                await update.message.reply_text("❌ Insufficient balance or withdrawal could not be created.")
                return
            context.user_data['awaiting_withdraw'] = False
            await update.message.reply_text("✅ Withdrawal request created. An admin will review it.")
        except ValueError:
            await update.message.reply_text("❌ Please enter a valid withdrawal amount (minimum 50 Birr).")
        return

    await update.message.reply_text("I don't understand that command. Use /help to see available commands.")

# Health check endpoint
@app.get("/")
async def root():
    return {
        "status": "online",
        "service": "Joy Bingo",
        "mode": "production",
        "bot_configured": bool(BOT_TOKEN),
        "timestamp": datetime.now().isoformat()
    }

@app.get("/test-db")
async def test_database(auth: bool = Depends(verify_admin_token)):
    try:
        if db.pool is None:
            return JSONResponse({
                "status": "❌ Database not initialized",
                "error": "Database pool not created"
            }, status_code=500)
        
        async with db.pool.acquire() as conn:
            result = await conn.fetchval("SELECT 1")
        
        return JSONResponse({
            "status": "✅ Database connected!",
            "message": "Your database is working and ready to store user data!",
            "result": result
        })
    except Exception as e:
        return JSONResponse({
            "status": "❌ Database connection failed",
            "error": str(e)
        }, status_code=500)

# Webhook endpoint for Telegram
@app.post("/api/webhook")
async def telegram_webhook(request: Request):
    try:
        update_data = await request.json()
        logger.info(f"📨 Received webhook update: {update_data.get('update_id', 'unknown')}")
        
        if not bot_app:
            logger.error("❌ Bot application not initialized")
            return JSONResponse(status_code=200, content={"ok": False, "error": "Bot not initialized"})
        
        update = Update.de_json(update_data, bot_app.bot)
        await bot_app.process_update(update)
        
        return {"ok": True, "message": "Update processed"}
        
    except Exception as e:
        logger.error(f"❌ Webhook error: {e}")
        return JSONResponse(status_code=200, content={"ok": False, "error": str(e)})

@app.get("/api/webhook")
async def webhook_get():
    return {
        "message": "Webhook endpoint is active",
        "method": "GET",
        "use": "Send POST requests with Telegram updates",
        "bot_configured": bool(BOT_TOKEN),
        "webapp_url": WEBAPP_URL
    }

@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "bot_ready": bot_app is not None if BOT_TOKEN else False
    }

@app.get("/ping")
async def ping():
    return {"pong": True}

# Simple game rooms
rooms_data = {
    "classic": {
        "id": "classic",
        "name": "🎲 Classic Bingo",
        "players": 0,
        "max_players": 400,
        "status": "waiting",
        "prize_pool": 0,
        "card_price": 10,
        "description": "Complete any row, column, or diagonal to win!"
    },
    "blackout": {
        "id": "blackout",
        "name": "⬛ Blackout",
        "players": 0,
        "max_players": 200,
        "status": "waiting",
        "prize_pool": 0,
        "card_price": 20,
        "description": "Fill your entire card to win!"
    },
    "four_corners": {
        "id": "four_corners",
        "name": "📦 Four Corners",
        "players": 0,
        "max_players": 350,
        "status": "waiting",
        "prize_pool": 0,
        "card_price": 12,
        "description": "Get all four corners to win!"
    },
    "line": {
        "id": "line",
        "name": "📏 Line Bingo",
        "players": 0,
        "max_players": 400,
        "status": "waiting",
        "prize_pool": 0,
        "card_price": 10,
        "description": "Complete any line (row, column, or diagonal) to win!"
    }
}

# Canonical production game service. The database is authoritative for money.
game_service = GameService(db)
game_service.create_room(GameRoom("classic", "🎲 Classic Bingo", 10, 80, 2, 400, 2.0, 20, "line", rooms_data["classic"]["description"]))
game_service.create_room(GameRoom("blackout", "⬛ Blackout", 20, 80, 2, 200, 2.0, 20, "blackout", rooms_data["blackout"]["description"]))
game_service.create_room(GameRoom("four_corners", "📦 Four Corners", 12, 80, 2, 350, 2.0, 20, "four_corners", rooms_data["four_corners"]["description"]))
game_service.create_room(GameRoom("line", "📏 Line Bingo", 10, 80, 2, 400, 2.0, 20, "line", rooms_data["line"]["description"]))


def _sync_room_metadata():
    for item in game_service.rooms_state():
        room_id = item["room_id"]
        if room_id in rooms_data:
            rooms_data[room_id].update({
                "players": item["players"], "status": item["status"],
                "prize_pool": item["prize_pool"], "max_players": item["max_players"],
                "card_price": item["card_price"],
            })


@app.get("/api/rooms")
async def get_rooms():
    _sync_room_metadata()
    return JSONResponse(list(rooms_data.values()))


@app.get("/api/rooms/{room_id}")
async def get_room(room_id: str):
    if room_id not in game_service.rooms:
        raise HTTPException(status_code=404, detail="Room not found")
    _sync_room_metadata()
    return JSONResponse(rooms_data[room_id])


@app.post("/api/game/join")
async def join_game(request: Request):
    data = await request.json()
    user_id = str(data.get("user_id") or "")
    username = str(data.get("username") or "Player")[:64]
    room_id = str(data.get("room_id") or "")
    try:
        card_number = int(data.get("card_number"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid card number")
    if not user_id or room_id not in game_service.rooms:
        raise HTTPException(status_code=400, detail="Missing or invalid game information")
    if not allow_action(user_id, per_second=3, per_minute=30):
        raise HTTPException(status_code=429, detail="Too many requests")
    ok, message, state = await game_service.join(user_id, username, room_id, card_number)
    _sync_room_metadata()
    if not ok:
        raise HTTPException(status_code=400, detail=message)
    return JSONResponse({"success": True, "message": message, "game_state": state, "player_data": state.get("player")})


@app.post("/api/game/select_card")
async def select_card(request: Request):
    return await join_game(request)


@app.get("/api/game/taken_cards/{room_id}")
async def get_taken_cards(room_id: str):
    if room_id not in game_service.rooms:
        raise HTTPException(status_code=404, detail="Room not found")
    game = game_service.games.get(game_service.rooms[room_id].current_game_id)
    taken = sorted(game.used_cards) if game and game.status == "waiting" else []
    return JSONResponse({"success": True, "taken_cards": [str(x) for x in taken]})


@app.get("/api/game/selected_count/{room_id}")
async def get_selected_count(room_id: str):
    if room_id not in game_service.rooms:
        raise HTTPException(status_code=404, detail="Room not found")
    game = game_service.games.get(game_service.rooms[room_id].current_game_id)
    return {"count": len(game.players) if game else 0}


@app.get("/api/game/state/{user_id}")
async def get_game_state(user_id: str):
    state = game_service.state_for_user(str(user_id))
    return JSONResponse({"success": True, "state": state or {"in_game": False}})


@app.post("/api/game/mark_number")
async def mark_number(request: Request):
    data = await request.json()
    user_id = str(data.get("user_id") or "")
    if not allow_action(user_id, per_second=3, per_minute=60):
        raise HTTPException(status_code=429, detail="Too many requests")
    try:
        number = int(data.get("number"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid number")
    ok, message, bingo, state = await game_service.mark(user_id, number)
    if not ok:
        raise HTTPException(status_code=400, detail=message)
    _sync_room_metadata()
    return JSONResponse({"success": True, "message": message, "bingo": bingo, "marked": state["player"]["marked"], "game_state": state})


@app.post("/api/game/call_bingo")
async def call_bingo(request: Request):
    data = await request.json()
    user_id = str(data.get("user_id") or "")
    state = game_service.state_for_user(user_id)
    if not state:
        raise HTTPException(status_code=404, detail="Game not found")
    player = state.get("player", {})
    if player.get("has_bingo"):
        return JSONResponse({"success": True, "message": "BINGO confirmed!", "prize": player.get("win_amount", 0), "game_state": state})
    return JSONResponse({"success": False, "message": "No valid Bingo yet."}, status_code=400)


@app.post("/api/game/check_bingo")
async def check_bingo(request: Request):
    data = await request.json()
    user_id = str(data.get("user_id") or "")
    state = game_service.state_for_user(user_id)
    if not state:
        return JSONResponse({"status": "error", "message": "Not in a game"}, status_code=404)
    player = state.get("player", {})
    if player.get("has_bingo"):
        return {"status": "win", "prize": player.get("win_amount", 0), "message": "BINGO confirmed!"}
    return {"status": "no_bingo", "message": "No valid Bingo yet."}


@app.get("/api/game/{game_id}/cards")
async def get_game_cards(game_id: str):
    game = game_service.games.get(game_id)
    if not game:
        raise HTTPException(status_code=404, detail="Game not found")
    return {"taken": [str(x) for x in game.used_cards], "count": len(game.used_cards)}


@app.get("/api/leaderboard")
async def get_leaderboard():
    return JSONResponse(await db.get_leaderboard())

@app.get("/bingo_game.html")
async def bingo_game_redirect(request: Request):
    try:
        with open("webapp/bingo_game.html", "r") as f:
            content = f.read()
        return HTMLResponse(content=content, status_code=200)
    except FileNotFoundError:
        return HTMLResponse(content="<h1>Bingo Game Page Not Found</h1><p>Please ensure bingo_game.html exists in the webapp folder.</p>", status_code=404)

# ============= ADMIN API ENDPOINTS =============

@app.post("/api/admin/login")
async def admin_login(request: Request):
    data = await request.json()
    password = str(data.get("password") or "")
    user_id = data.get("user_id")
    configured_password = os.getenv("ADMIN_PASSWORD")
    if not configured_password or not ADMIN_SECRET_KEY:
        raise HTTPException(status_code=503, detail="Admin authentication is not configured")
    if not is_admin_user(int(user_id)):
        raise HTTPException(status_code=403, detail="Not authorized")
    import hmac
    if not hmac.compare_digest(password, configured_password):
        raise HTTPException(status_code=401, detail="Invalid password")
    return JSONResponse({"success": True, "token": ADMIN_SECRET_KEY})

@app.get("/api/admin/deposits")
async def admin_get_deposits(auth: bool = Depends(verify_admin_token)):
    return JSONResponse(await db.get_pending_deposits())

@app.post("/api/admin/deposits/{request_id}/approve")
async def admin_approve_deposit(request_id: int, auth: bool = Depends(verify_admin_token)):
    if not ADMIN_IDS or not await db.approve_deposit(request_id, ADMIN_IDS[0]):
        raise HTTPException(status_code=400, detail="Deposit is invalid, already processed, or could not be approved")
    return {"success": True}

@app.post("/api/admin/deposits/{request_id}/reject")
async def admin_reject_deposit(request_id: int, auth: bool = Depends(verify_admin_token)):
    if not ADMIN_IDS or not await db.reject_deposit(request_id, ADMIN_IDS[0]):
        raise HTTPException(status_code=400, detail="Deposit is invalid or already processed")
    return {"success": True}

@app.get("/api/admin/withdrawals")
async def admin_get_withdrawals(auth: bool = Depends(verify_admin_token)):
    return JSONResponse(await db.get_pending_withdrawals())

@app.post("/api/admin/withdrawals/{request_id}/approve")
async def admin_approve_withdrawal(request_id: int, auth: bool = Depends(verify_admin_token)):
    if not ADMIN_IDS or not await db.approve_withdrawal(request_id, ADMIN_IDS[0]):
        raise HTTPException(status_code=400, detail="Withdrawal is invalid or already processed")
    return {"success": True}

@app.post("/api/admin/withdrawals/{request_id}/reject")
async def admin_reject_withdrawal(request_id: int, auth: bool = Depends(verify_admin_token)):
    if not ADMIN_IDS or not await db.reject_withdrawal(request_id, ADMIN_IDS[0]):
        raise HTTPException(status_code=400, detail="Withdrawal is invalid or already processed")
    return {"success": True}

@app.get("/api/admin/dashboard")
async def admin_dashboard(auth: bool = Depends(verify_admin_token)):
    games = list(game_service.games.values())
    active = [g for g in games if g.status == "active"]
    total_volume = sum(g.total_bet for g in games)
    total_commission = sum(g.commission for g in games)
    return JSONResponse({
        "totalUsers": await db.get_user_count(),
        "activeGames": len(active),
        "totalVolume": total_volume,
        "totalCommission": total_commission,
        "revenue": {"labels": [], "values": []},
        "gamesHistory": {"labels": [], "values": []},
    })

@app.get("/api/admin/users")
async def admin_get_users(
    search: str = "", 
    status: str = "all", 
    sort: str = "balance_desc",
    auth: bool = Depends(verify_admin_token)
):
    try:
        users_list = await db.get_all_users(limit=100, offset=0)
        
        filtered_users = []
        for user in users_list:
            if search:
                if search.lower() in user.get('first_name', '').lower() or search in user.get('telegram_id', ''):
                    filtered_users.append(user)
            else:
                filtered_users.append(user)
        
        if sort == "balance_desc":
            filtered_users.sort(key=lambda x: x.get('balance', 0), reverse=True)
        elif sort == "balance_asc":
            filtered_users.sort(key=lambda x: x.get('balance', 0))
        elif sort == "games_desc":
            filtered_users.sort(key=lambda x: x.get('games_played', 0), reverse=True)
        elif sort == "wins_desc":
            filtered_users.sort(key=lambda x: x.get('games_won', 0), reverse=True)
        
        total_balance = sum(u.get('balance', 0) for u in filtered_users)
        
        return JSONResponse({
            "total": len(filtered_users),
            "activeToday": len(filtered_users),
            "newToday": len([u for u in filtered_users if u.get('created_at', '').startswith(datetime.now().strftime("%Y-%m-%d"))]),
            "totalBalance": total_balance,
            "list": filtered_users[:50]
        })
    except Exception as e:
        logger.error(f"Admin get users error: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.get("/api/admin/users/{user_id}")
async def admin_get_user(user_id: str, auth: bool = Depends(verify_admin_token)):
    try:
        user = await db.get_user(user_id)
        
        if not user:
            return JSONResponse(status_code=404, content={"error": "User not found"})
        
        return JSONResponse({
            "id": user.get("telegram_id", user_id),
            "username": user.get("username"),
            "first_name": user.get("first_name"),
            "last_name": user.get("last_name"),
            "balance": float(user.get("balance", 0)),
            "games_played": user.get("games_played", 0),
            "games_won": user.get("games_won", 0),
            "total_deposits": float(user.get("total_deposits", 0)),
            "total_withdrawals": float(user.get("total_withdrawals", 0)),
            "is_banned": False,
            "is_vip": False,
            "created_at": user.get("created_at", datetime.now().isoformat()),
            "last_seen": user.get("last_seen", datetime.now().isoformat())
        })
    except Exception as e:
        logger.error(f"Admin get user error: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.post("/api/admin/adjust-balance")
async def admin_adjust_balance(request: Request, auth: bool = Depends(verify_admin_token)):
    try:
        data = await request.json()
        user_id = data.get("userId")
        amount = float(data.get("amount"))
        if amount < 0:
            raise HTTPException(status_code=400, detail="Amount must be non-negative")
        type_op = data.get("type")
        reason = data.get("reason", "")
        
        user = await db.get_user(user_id)
        
        if not user:
            return JSONResponse(status_code=404, content={"success": False, "error": "User not found"})
        
        current = user.get("balance", 0)
        
        if type_op == "add":
            user_id_val = user.get("id") if isinstance(user, dict) else user.id
            if not await db.update_balance(
                user_id=user_id_val, amount=amount, transaction_type='admin_deposit',
                description=f'Admin adjustment: {reason}', reference=f'admin:{user_id}:add:{datetime.now().timestamp()}'
            ):
                raise HTTPException(status_code=400, detail="Balance update failed")
        elif type_op == "subtract":
            if current < amount:
                return JSONResponse({"success": False, "error": "Insufficient balance"})
            user_id_val = user.get("id") if isinstance(user, dict) else user.id
            if not await db.update_balance(
                user_id=user_id_val, amount=-amount, transaction_type='admin_withdrawal',
                description=f'Admin adjustment: {reason}', reference=f'admin:{user_id}:subtract:{datetime.now().timestamp()}'
            ):
                raise HTTPException(status_code=400, detail="Balance update failed")
        elif type_op == "set":
            diff = amount - current
            if diff > 0:
                user_id_val = user.get("id") if isinstance(user, dict) else user.id
                if not await db.update_balance(
                    user_id=user_id_val, amount=diff, transaction_type='admin_deposit',
                    description=f'Admin set balance to {amount}: {reason}', reference=f'admin:{user_id}:set:{datetime.now().timestamp()}'
                ):
                    raise HTTPException(status_code=400, detail="Balance update failed")
            elif diff < 0:
                user_id_val = user.get("id") if isinstance(user, dict) else user.id
                if not await db.update_balance(
                    user_id=user_id_val, amount=diff, transaction_type='admin_withdrawal',
                    description=f'Admin set balance to {amount}: {reason}', reference=f'admin:{user_id}:set:{datetime.now().timestamp()}'
                ):
                    raise HTTPException(status_code=400, detail="Balance update failed")
        
        updated_user = await db.get_user(user_id)
        new_balance = updated_user.get("balance", 0)
        
        logger.info(f"Admin adjusted balance for user {user_id}: {current} -> {new_balance} ({reason})")
        
        return JSONResponse({
            "success": True,
            "new_balance": float(new_balance)
        })
    except Exception as e:
        logger.error(f"Admin adjust balance error: {e}")
        return JSONResponse(status_code=500, content={"success": False, "error": str(e)})

@app.post("/api/admin/toggle-ban")
async def admin_toggle_ban(request: Request, auth: bool = Depends(verify_admin_token)):
    data = await request.json()
    target_id = str(data.get("userId") or "")
    user = await db.get_user(target_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    new_status = not bool(user.get("is_banned"))
    # The authenticated token contains the admin ID only in the legacy secret-token flow,
    # so use the configured primary admin as the audit actor.
    admin_id = ADMIN_IDS[0] if ADMIN_IDS else None
    if admin_id is None or not await db.set_user_banned(target_id, new_status, admin_id):
        raise HTTPException(status_code=500, detail="Could not update ban status")
    return JSONResponse({"success": True, "is_banned": new_status})

@app.get("/api/admin/games")
async def admin_get_games(search: str = "", status: str = "all", room: str = "all", auth: bool = Depends(verify_admin_token)):
    result = []
    for game in game_service.games.values():
        if search and search not in game.game_id:
            continue
        if status != "all" and game.status != status:
            continue
        if room != "all" and game.room.room_id != room:
            continue
        duration = 0
        if game.started_at and game.finished_at:
            duration = int((game.finished_at - game.started_at).total_seconds())
        result.append({
            "game_id": game.game_id, "room": game.room.room_id, "status": game.status,
            "players": len(game.players), "max_players": game.room.max_players,
            "prize_pool": game.prize_pool, "duration": duration, "winners": len(game.winners)
        })
    return JSONResponse(result)

@app.post("/api/admin/end-game")
async def admin_end_game(request: Request, auth: bool = Depends(verify_admin_token)):
    data = await request.json()
    game_id = str(data.get("gameId") or "")
    game = game_service.games.get(game_id)
    if not game:
        raise HTTPException(status_code=404, detail="Game not found")
    if not await game_service.cancel_and_refund(game_id):
        raise HTTPException(status_code=400, detail="Game cannot be cancelled or refunds failed")
    _sync_room_metadata()
    return JSONResponse({"success": True, "game_id": game_id, "status": "cancelled"})

@app.get("/api/admin/transactions")
async def admin_get_transactions(
    search: str = "",
    type: str = "all",
    from_date: str = "",
    to_date: str = "",
    auth: bool = Depends(verify_admin_token)
):
    try:
        transactions = await db.get_all_transactions(limit=100, offset=0)
        
        if type != "all":
            transactions = [t for t in transactions if t.get('type') == type]
        
        today = datetime.now().date()
        def is_today(tx):
            value = tx.get("created_at")
            return hasattr(value, "date") and value.date() == today
        today_deposits = sum(float(t.get('amount', 0)) for t in transactions if t.get('type') == 'deposit' and is_today(t))
        today_withdrawals = sum(float(t.get('amount', 0)) for t in transactions if t.get('type') == 'withdrawal' and is_today(t))
        today_wins = sum(float(t.get('amount', 0)) for t in transactions if t.get('type') == 'win' and is_today(t))
        net_revenue = today_deposits - today_withdrawals
        
        return JSONResponse({
            "transactions": transactions[:50],
            "todayDeposits": today_deposits,
            "todayWithdrawals": today_withdrawals,
            "todayWins": today_wins,
            "netRevenue": net_revenue
        })
    except Exception as e:
        logger.error(f"Admin get transactions error: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.post("/api/admin/broadcast")
async def admin_broadcast(request: Request, auth: bool = Depends(verify_admin_token)):
    try:
        data = await request.json()
        message_type = data.get("type")
        room = data.get("room")
        message = data.get("message")
        link = data.get("link", "")
        
        total_users = await db.get_user_count()
        
        logger.info(f"Broadcast: {message_type} - {message}")
        
        return JSONResponse({
            "success": True,
            "recipients": total_users
        })
    except Exception as e:
        logger.error(f"Admin broadcast error: {e}")
        return JSONResponse(status_code=500, content={"success": False, "error": str(e)})

@app.get("/api/admin/settings")
async def admin_get_settings(auth: bool = Depends(verify_admin_token)):
    try:
        return JSONResponse({
            "cardPrice": 10,
            "prizePercent": 80,
            "minPlayers": 2,
            "maxPlayers": 400,
            "callInterval": 2.0,
            "selectionTime": 20,
            "emailVerify": False,
            "maxLoginAttempts": 5,
            "sessionTimeout": 60,
            "rateLimit": 60,
            "notifyWins": True,
            "notifyDeposits": True,
            "adminEmail": "admin@joybingo.com",
            "rooms": [
                {"id": "classic", "name": "Classic", "cardPrice": 10, "minPlayers": 2, "maxPlayers": 400, "mode": "classic"},
                {"id": "blackout", "name": "Blackout", "cardPrice": 20, "minPlayers": 2, "maxPlayers": 200, "mode": "blackout"},
                {"id": "line", "name": "Line", "cardPrice": 10, "minPlayers": 2, "maxPlayers": 400, "mode": "line"}
            ]
        })
    except Exception as e:
        logger.error(f"Admin get settings error: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.post("/api/admin/settings")
async def admin_save_settings(request: Request, auth: bool = Depends(verify_admin_token)):
    try:
        settings = await request.json()
        logger.info(f"Settings updated: {settings}")
        return JSONResponse({"success": True})
    except Exception as e:
        logger.error(f"Admin save settings error: {e}")
        return JSONResponse(status_code=500, content={"success": False, "error": str(e)})

@app.get("/api/admin/analytics")
async def admin_analytics(
    period: str = "today",
    start: str = "",
    end: str = "",
    auth: bool = Depends(verify_admin_token)
):
    try:
        return JSONResponse({
            "arpdau": 45.50,
            "conversionRate": 12.5,
            "retention": {"d1": 45, "d3": 30, "d7": 20, "d14": 15, "d30": 8},
            "avgGameDuration": 85,
            "gameDistribution": {"classic": 65, "blackout": 20, "line": 10, "corners": 5}
        })
    except Exception as e:
        logger.error(f"Admin analytics error: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.get("/api/admin/logs")
async def admin_logs(
    search: str = "",
    level: str = "all",
    action: str = "all",
    auth: bool = Depends(verify_admin_token)
):
    try:
        logs = await db.get_audit_logs(limit=100)
        return JSONResponse(logs)
    except Exception as e:
        logger.error(f"Admin logs error: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.get("/api/admin/stats")
async def admin_stats(auth: bool = Depends(verify_admin_token)):
    try:
        total_users = await db.get_user_count()
        return JSONResponse({
            "totalUsers": total_users
        })
    except Exception as e:
        logger.error(f"Admin stats error: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.get("/api/admin/rooms")
async def admin_rooms(auth: bool = Depends(verify_admin_token)):
    try:
        rooms = [
            {"id": "classic", "name": "Classic Bingo"},
            {"id": "blackout", "name": "Blackout"},
            {"id": "line", "name": "Line Bingo"},
            {"id": "four_corners", "name": "Four Corners"},
        ]
        return JSONResponse(rooms)
    except Exception as e:
        logger.error(f"Admin rooms error: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.get("/api/admin/export/users")
async def admin_export_users(auth: bool = Depends(verify_admin_token)):
    try:
        import csv
        from io import StringIO
        
        users_list = await db.get_all_users(limit=1000, offset=0)
        
        output = StringIO()
        writer = csv.writer(output)
        writer.writerow(["User ID", "Username", "First Name", "Balance", "Games Played", "Wins", "Joined"])
        
        for user in users_list:
            writer.writerow([
                user.get('telegram_id', ''),
                user.get('username', ''),
                user.get('first_name', ''),
                user.get('balance', 0),
                user.get('games_played', 0),
                user.get('games_won', 0),
                user.get('created_at', '')[:10] if user.get('created_at') else ''
            ])
        
        return Response(
            content=output.getvalue(),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=users.csv"}
        )
    except Exception as e:
        logger.error(f"Admin export users error: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)})

# WebSocket connection manager
class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[str, Set[WebSocket]] = {}
        self.user_rooms: Dict[str, str] = {}
    
    async def connect(self, websocket: WebSocket, room_id: str, user_id: str):
        await websocket.accept()
        if room_id not in self.active_connections:
            self.active_connections[room_id] = set()
        self.active_connections[room_id].add(websocket)
        self.user_rooms[user_id] = room_id
        logger.info(f"User {user_id} connected to room {room_id}")
        
        if room_id in rooms_data:
            rooms_data[room_id]["players"] = len(self.active_connections[room_id])
    
    def disconnect(self, websocket: WebSocket, user_id: str):
        room_id = self.user_rooms.get(user_id)
        if room_id and room_id in self.active_connections:
            self.active_connections[room_id].discard(websocket)
            if room_id in rooms_data:
                rooms_data[room_id]["players"] = len(self.active_connections[room_id])
        if user_id in self.user_rooms:
            del self.user_rooms[user_id]
        logger.info(f"User {user_id} disconnected")
    
    async def broadcast(self, room_id: str, message: dict, exclude_user: str = None):
        if room_id in self.active_connections:
            disconnected = set()
            for connection in self.active_connections[room_id]:
                try:
                    await connection.send_json(message)
                except:
                    disconnected.add(connection)
            
            for conn in disconnected:
                self.active_connections[room_id].discard(conn)
            
            if room_id in rooms_data:
                rooms_data[room_id]["players"] = len(self.active_connections[room_id])

manager = ConnectionManager()

@app.websocket("/ws/{room_id}/{user_id}")
async def websocket_endpoint(websocket: WebSocket, room_id: str, user_id: str):
    await manager.connect(websocket, room_id, user_id)
    poll_task = None
    try:
        state = game_service.state_for_user(user_id)
        await websocket.send_json({
            "type": "connected",
            "message": f"Connected to room {room_id}",
            "room_data": rooms_data.get(room_id, {}),
            "game_state": state,
            "timestamp": datetime.now().isoformat(),
        })

        async def state_poller():
            last_signature = None
            while True:
                await asyncio.sleep(1)
                current = game_service.state_for_user(user_id)
                if current is None:
                    continue
                signature = json.dumps(current, sort_keys=True, default=str)
                if signature != last_signature:
                    await websocket.send_json({"type": "game_state", "data": current})
                    last_signature = signature

        poll_task = asyncio.create_task(state_poller())
        while True:
            data = await websocket.receive_text()
            message = json.loads(data)
            msg_type = message.get("type")

            if msg_type == "ping":
                await websocket.send_json({"type": "pong", "timestamp": datetime.now().isoformat()})
                continue

            if msg_type in {"mark", "mark_number"}:
                try:
                    number = int(message.get("number"))
                except (TypeError, ValueError):
                    await websocket.send_json({"type": "error", "message": "Invalid number"})
                    continue
                ok, msg, bingo, state = await game_service.mark(user_id, number)
                if not ok:
                    await websocket.send_json({"type": "error", "message": msg})
                    continue
                await manager.broadcast(room_id, {
                    "type": "number_marked", "user_id": user_id, "number": number,
                    "timestamp": datetime.now().isoformat()
                }, exclude_user=user_id)
                await websocket.send_json({"type": "mark_confirmed", "number": number, "bingo": bingo, "data": state})
                _sync_room_metadata()
                continue

            if msg_type in {"bingo", "call_bingo"}:
                state = game_service.state_for_user(user_id)
                if not state:
                    await websocket.send_json({"type": "error", "message": "Not in a game"})
                    continue
                player = state.get("player", {})
                await websocket.send_json({
                    "type": "bingo_confirmed" if player.get("has_bingo") else "error",
                    "message": "Bingo confirmed!" if player.get("has_bingo") else "No valid Bingo yet.",
                    "prize": player.get("win_amount", 0),
                    "data": state,
                })
                continue

            await websocket.send_json({"type": "ack", "received": message})
    except json.JSONDecodeError:
        await websocket.send_json({"type": "error", "message": "Invalid JSON format"})
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.error("WebSocket error: %s", e)
    finally:
        if poll_task:
            poll_task.cancel()
        manager.disconnect(websocket, user_id)

# Serve static files
try:
    app.mount("/webapp", StaticFiles(directory="webapp"), name="webapp")
    logger.info("✅ Mounted webapp directory")
except Exception as e:
    logger.warning(f"⚠️ webapp directory not found: {e}")

# For local testing
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    logger.info(f"Starting server on port {port}")
    uvicorn.run(app, host="0.0.0.0", port=port)
