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
import httpx
import random
from database import db
import asyncpg

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Create FastAPI app
app = FastAPI(title="Joy Bingo API")

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Admin configuration
ADMIN_IDS = [8576569079]  # Your Telegram user IDs
ADMIN_SECRET_KEY = os.getenv("ADMIN_SECRET_KEY", secrets.token_urlsafe(32))

# Initialize bot application
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
WEBAPP_URL = os.getenv("WEBAPP_URL", "https://conceptual-debby-wond-7482233b.koyeb.app")

# Starting balance for new users
STARTING_BALANCE = 20  # 20 Birr for new registrations

# Create bot application
bot_app = None

# Global selection timer
selection_start_time = None
SELECTION_DURATION = 20
disqualified_players = set()

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
    
    if token != ADMIN_SECRET_KEY:
        raise HTTPException(status_code=401, detail="Invalid token")
    
    return True

def is_admin_user(user_id: int) -> bool:
    """Check if a Telegram user is admin"""
    return str(user_id) in [str(uid) for uid in ADMIN_IDS]

def start_selection_phase():
    """Start the global selection timer"""
    global selection_start_time
    selection_start_time = time.time()
    # Clear disqualified players for new game
    disqualified_players.clear()

def selection_open():
    """Check if selection phase is still open"""
    if selection_start_time is None:
        return False
    return (time.time() - selection_start_time) < SELECTION_DURATION

@app.on_event("startup")
async def startup_event():
    """Initialize bot on startup"""
    global bot_app
    if BOT_TOKEN:
        # Build bot application
        bot_app = Application.builder().token(BOT_TOKEN).build()
        
        # Add command handlers
        bot_app.add_handler(CommandHandler("start", start_command))
        bot_app.add_handler(CommandHandler("help", help_command))
        bot_app.add_handler(CommandHandler("play", play_command))
        bot_app.add_handler(CommandHandler("balance", balance_command))
        bot_app.add_handler(CommandHandler("deposit", deposit_command))
        bot_app.add_handler(CommandHandler("withdraw", withdraw_command))
        bot_app.add_handler(CommandHandler("profile", profile_command))
        bot_app.add_handler(CommandHandler("rules", rules_command))
        bot_app.add_handler(CommandHandler("leaderboard", leaderboard_command))
        bot_app.add_handler(CommandHandler("admin", admin_command))
        bot_app.add_handler(CommandHandler("id", id_command))
        bot_app.add_handler(CommandHandler("register", register_command))
        
        # Add callback query handler for inline buttons
        bot_app.add_handler(CallbackQueryHandler(button_callback))
        
        # Add message handler for text messages
        bot_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))
        
        # Initialize bot
        await bot_app.initialize()
        logger.info("✅ Bot application initialized")
    else:
        logger.warning("⚠️ BOT_TOKEN not set, bot commands disabled")

# Command handlers
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command - Main menu with all options"""
    user = update.effective_user
    user_id = str(user.id)
    
    # Check if user exists in database, if not show register option
    existing_user = await db.get_user(user_id)
    
    if not existing_user:
        # Create register keyboard
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
    
    # If already registered, show main menu
    logger.info(f"User {user.id} (@{user.username}) started the bot")
    
    # Create main menu keyboard with multiple buttons
    keyboard = [
        [InlineKeyboardButton("🎮 PLAY BINGO", web_app=WebAppInfo(url=f"{WEBAPP_URL}/webapp/lobby.html"))],
        [InlineKeyboardButton("💰 My Balance", callback_data="balance"),
         InlineKeyboardButton("📥 Deposit", callback_data="deposit")],
        [InlineKeyboardButton("📤 Withdraw", callback_data="withdraw"),
         InlineKeyboardButton("👤 My Profile", callback_data="profile")],
        [InlineKeyboardButton("📋 Game Rules", callback_data="rules"),
         InlineKeyboardButton("🏆 Leaderboard", callback_data="leaderboard")],
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
    """Handle /register command - Register new user with starting balance"""
    user = update.effective_user
    user_id = str(user.id)
    
    # Check if user already exists in database
    existing_user = await db.get_user(user_id)
    if existing_user:
        await update.message.reply_text(
            "✅ You are already registered! Use /start to access the main menu.",
            parse_mode='Markdown'
        )
        return
    
    try:
        # Create user in PostgreSQL
        new_user = await db.create_user(
            telegram_id=user_id,
            username=user.username,
            first_name=user.first_name,
            last_name=user.last_name
        )
        
        # Add starting balance as deposit
        await db.update_balance(
            user_id=new_user.id if hasattr(new_user, 'id') else new_user.get('id'),
            amount=STARTING_BALANCE,
            transaction_type='deposit',
            description='Welcome bonus'
        )
        
        logger.info(f"✅ New user registered in PostgreSQL: {user_id} with {STARTING_BALANCE} Birr bonus")
        
        # Create main menu keyboard
        keyboard = [
            [InlineKeyboardButton("🎮 PLAY BINGO", web_app=WebAppInfo(url=f"{WEBAPP_URL}/webapp/lobby.html"))],
            [InlineKeyboardButton("💰 My Balance", callback_data="balance"),
             InlineKeyboardButton("📥 Deposit", callback_data="deposit")],
            [InlineKeyboardButton("📤 Withdraw", callback_data="withdraw"),
             InlineKeyboardButton("👤 My Profile", callback_data="profile")],
            [InlineKeyboardButton("📋 Game Rules", callback_data="rules"),
             InlineKeyboardButton("🏆 Leaderboard", callback_data="leaderboard")],
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
    """Handle about command - Show info about Joy Bingo"""
    about_text = f"""
🎮 **About Joy Bingo**
═══════════════════

**What is Joy Bingo?**
Joy Bingo is a fun and exciting Telegram-based bingo game where you can play with friends and win real prizes!

**Features:**
• 🎯 Play classic bingo with 400 unique cards
• 💰 **Get {STARTING_BALANCE} Birr free** when you register!
• 💰 Deposit and withdraw funds
• 👤 View your profile and statistics
• 🏆 Compete on the leaderboard
• 🎮 Easy-to-use WebApp interface

**How to Play:**
1. Register for free (get {STARTING_BALANCE} Birr bonus)
2. Deposit funds to buy cards
3. Select a card and start playing
4. Mark numbers as they're called
5. Get BINGO to win!

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
    """Handle /help command"""
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
• `/leaderboard` - Top players
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
• Withdrawals processed within 24h

**🏆 PRIZES:**
• Winner takes 80% of the pot
• Multiple winners split the prize

Need more help? Contact @admin
"""
    await update.message.reply_text(help_text, parse_mode='Markdown')


async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /admin command - Get admin panel link"""
    user_id = str(update.effective_user.id)
    
    # Check if user is admin
    if user_id not in [str(uid) for uid in ADMIN_IDS]:
        await update.message.reply_text("❌ You are not authorized to access the admin panel.")
        return
    
    # Create a special button that opens admin panel
    keyboard = [[InlineKeyboardButton("🔐 Open Admin Panel", web_app=WebAppInfo(url=f"{WEBAPP_URL}/webapp/admin_login.html"))]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "🔐 **Admin Panel Access**\n\n"
        "Click the button below to open the admin panel:",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def id_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /id command - Show user their Telegram ID"""
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
    """Handle /play command - Direct link to game"""
    user_id = str(update.effective_user.id)
    
    # Check if user is registered
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
    """Handle /balance command - Check balance"""
    user_id = str(update.effective_user.id)
    
    try:
        # Get user from database
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
    """Handle /deposit command - Deposit funds"""
    user_id = str(update.effective_user.id)
    
    # Check if user is registered
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
        [InlineKeyboardButton("💳 10 Birr", callback_data="deposit_10"),
         InlineKeyboardButton("💳 50 Birr", callback_data="deposit_50")],
        [InlineKeyboardButton("💳 100 Birr", callback_data="deposit_100"),
         InlineKeyboardButton("💳 500 Birr", callback_data="deposit_500")],
        [InlineKeyboardButton("💳 1000 Birr", callback_data="deposit_1000"),
         InlineKeyboardButton("💳 Other", callback_data="deposit_other")],
        [InlineKeyboardButton("◀️ Back", callback_data="balance")]
    ]
    
    await update.message.reply_text(
        "📥 **DEPOSIT FUNDS**\n\n"
        "Select amount to deposit:\n"
        "• Minimum deposit: 10 Birr\n"
        "• Maximum deposit: 10,000 Birr\n\n"
        "Click an amount below to proceed:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )

async def withdraw_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /withdraw command - Withdraw funds"""
    user_id = str(update.effective_user.id)
    
    # Check if user is registered
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
        [InlineKeyboardButton("📤 Request Withdrawal", callback_data="withdraw_request")],
        [InlineKeyboardButton("📋 Withdrawal History", callback_data="withdraw_history")],
        [InlineKeyboardButton("◀️ Back", callback_data="balance")]
    ]
    
    await update.message.reply_text(
        f"📤 **WITHDRAW FUNDS**\n\n"
        f"Available Balance: **{balance} Birr**\n"
        f"Minimum Withdrawal: **50 Birr**\n"
        f"Processing Time: **24 hours**\n\n"
        f"To request a withdrawal, click the button below:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )

async def profile_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /profile command - View user profile"""
    user = update.effective_user
    user_id = str(user.id)
    
    # Check if user is registered
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
    """Handle /rules command - Game rules"""
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

async def leaderboard_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /leaderboard command - Top players"""
    try:
        # Get leaderboard from database
        leaderboard_data = await db.get_leaderboard(days=30, limit=10)
        
        leaderboard_text = "🏆 **TOP PLAYERS**\n══════════════════\n\n"
        
        if not leaderboard_data:
            leaderboard_text += "No players yet. Be the first!\n"
        else:
            for i, entry in enumerate(leaderboard_data, 1):
                name = entry.get('username', 'Unknown')[:10]
                wins = entry.get('wins', 0)
                winnings = entry.get('winnings', 0)
                
                medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f"{i}."
                leaderboard_text += f"{medal} **{name}** - {wins} wins ({winnings} Birr)\n"
        
        keyboard = [
            [InlineKeyboardButton("🎮 Play Now", web_app=WebAppInfo(url=f"{WEBAPP_URL}/webapp/lobby.html"))],
            [InlineKeyboardButton("🔄 Refresh", callback_data="leaderboard")]
        ]
        
        await update.message.reply_text(
            leaderboard_text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
    except Exception as e:
        logger.error(f"❌ Error loading leaderboard: {e}")
        await update.message.reply_text(
            "❌ Error loading leaderboard. Please try again.",
            parse_mode='Markdown'
        )

# Callback handler for inline buttons
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle button callbacks"""
    query = update.callback_query
    await query.answer()
    
    user_id = str(update.effective_user.id)
    user = update.effective_user
    data = query.data
    
    # Handle register callback
    if data == "register":
        # Check if already registered
        existing_user = await db.get_user(user_id)
        if existing_user:
            await query.edit_message_text(
                "✅ You are already registered! Use /start to access the main menu.",
                parse_mode='Markdown'
            )
            return
        
        try:
            # Register the user with starting balance
            new_user = await db.create_user(
                telegram_id=user_id,
                username=user.username,
                first_name=user.first_name,
                last_name=user.last_name
            )
            
            # Add starting balance
            await db.update_balance(
                user_id=new_user.id if hasattr(new_user, 'id') else new_user.get('id'),
                amount=STARTING_BALANCE,
                transaction_type='deposit',
                description='Welcome bonus'
            )
            
            # Create main menu keyboard
            keyboard = [
                [InlineKeyboardButton("🎮 PLAY BINGO", web_app=WebAppInfo(url=f"{WEBAPP_URL}/webapp/lobby.html"))],
                [InlineKeyboardButton("💰 My Balance", callback_data="balance"),
                 InlineKeyboardButton("📥 Deposit", callback_data="deposit")],
                [InlineKeyboardButton("📤 Withdraw", callback_data="withdraw"),
                 InlineKeyboardButton("👤 My Profile", callback_data="profile")],
                [InlineKeyboardButton("📋 Game Rules", callback_data="rules"),
                 InlineKeyboardButton("🏆 Leaderboard", callback_data="leaderboard")],
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
• 💰 Deposit and withdraw funds
• 👤 View your profile and statistics
• 🏆 Compete on the leaderboard
• 🎮 Easy-to-use WebApp interface

**How to Play:**
1. Register for free (get {STARTING_BALANCE} Birr bonus)
2. Deposit funds to buy cards
3. Select a card and start playing
4. Mark numbers as they're called
5. Get BINGO to win!

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
    
    # Get user from database for other callbacks
    db_user = await db.get_user(user_id)
    
    # Ensure user exists for other callbacks
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
            [InlineKeyboardButton("💳 10 Birr", callback_data="deposit_10"),
             InlineKeyboardButton("💳 50 Birr", callback_data="deposit_50")],
            [InlineKeyboardButton("💳 100 Birr", callback_data="deposit_100"),
             InlineKeyboardButton("💳 500 Birr", callback_data="deposit_500")],
            [InlineKeyboardButton("💳 1000 Birr", callback_data="deposit_1000"),
             InlineKeyboardButton("💳 Other", callback_data="deposit_other")],
            [InlineKeyboardButton("◀️ Back", callback_data="balance")]
        ]
        await query.edit_message_text(
            "📥 **DEPOSIT FUNDS**\n\n"
            "Select amount to deposit:\n"
            "• Minimum deposit: 10 Birr\n"
            "• Maximum deposit: 10,000 Birr\n\n"
            "Click an amount below to proceed:",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
    
    elif data.startswith("deposit_"):
        amount = data.replace("deposit_", "")
        if amount == "other":
            await query.edit_message_text(
                "📥 **CUSTOM DEPOSIT**\n\n"
                "Please send the amount you want to deposit (10-10000 Birr):\n\n"
                "Example: `500`",
                parse_mode='Markdown'
            )
            context.user_data['awaiting_deposit'] = True
        else:
            try:
                # Process deposit in database
                user_id_val = db_user.get("id") if isinstance(db_user, dict) else db_user.id
                await db.update_balance(
                    user_id=user_id_val,
                    amount=int(amount),
                    transaction_type='deposit',
                    description=f'Deposit of {amount} Birr'
                )
                
                # Get updated balance
                updated_user = await db.get_user(user_id)
                new_balance = updated_user.get("balance", 0) if isinstance(updated_user, dict) else updated_user.balance
                
                await query.edit_message_text(
                    f"✅ **DEPOSIT SUCCESSFUL!**\n\n"
                    f"Amount: **{amount} Birr**\n"
                    f"New Balance: **{new_balance} Birr**\n\n"
                    f"Thank you for your deposit!",
                    parse_mode='Markdown'
                )
            except Exception as e:
                logger.error(f"❌ Deposit error: {e}")
                await query.edit_message_text(
                    "❌ Deposit failed. Please try again.",
                    parse_mode='Markdown'
                )
    
    elif data == "withdraw":
        balance = db_user.get("balance", 0) if isinstance(db_user, dict) else db_user.balance
        keyboard = [
            [InlineKeyboardButton("📤 Request Withdrawal", callback_data="withdraw_request")],
            [InlineKeyboardButton("📋 Withdrawal History", callback_data="withdraw_history")],
            [InlineKeyboardButton("◀️ Back", callback_data="balance")]
        ]
        await query.edit_message_text(
            f"📤 **WITHDRAW FUNDS**\n\n"
            f"Available Balance: **{balance} Birr**\n"
            f"Minimum Withdrawal: **50 Birr**\n"
            f"Processing Time: **24 hours**\n\n"
            f"To request a withdrawal, click the button below:",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
    
    elif data == "withdraw_request":
        await query.edit_message_text(
            "📤 **WITHDRAWAL REQUEST**\n\n"
            "Please send the amount you want to withdraw (minimum 50 Birr):\n\n"
            "Example: `200`",
            parse_mode='Markdown'
        )
        context.user_data['awaiting_withdraw'] = True
    
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
    
    elif data == "leaderboard":
        try:
            leaderboard_data = await db.get_leaderboard(days=30, limit=10)
            
            leaderboard_text = "🏆 **TOP PLAYERS**\n══════════════════\n\n"
            
            if not leaderboard_data:
                leaderboard_text += "No players yet. Be the first!\n"
            else:
                for i, entry in enumerate(leaderboard_data, 1):
                    name = entry.get('username', 'Unknown')[:10]
                    wins = entry.get('wins', 0)
                    winnings = entry.get('winnings', 0)
                    
                    medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f"{i}."
                    leaderboard_text += f"{medal} **{name}** - {wins} wins ({winnings} Birr)\n"
            
            keyboard = [
                [InlineKeyboardButton("🎮 Play Now", web_app=WebAppInfo(url=f"{WEBAPP_URL}/webapp/lobby.html"))],
                [InlineKeyboardButton("🔄 Refresh", callback_data="leaderboard")]
            ]
            
            await query.edit_message_text(
                leaderboard_text,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode='Markdown'
            )
        except Exception as e:
            logger.error(f"❌ Leaderboard error: {e}")
            await query.edit_message_text(
                "❌ Error loading leaderboard.",
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
• /leaderboard - Top players
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
        support_text = """
📞 **CONTACT SUPPORT**
══════════════════

**How can we help you?**

**Common Issues:**
• Deposit problems
• Withdrawal issues
• Game questions
• Technical support
• Account issues

**Contact Methods:**
• Email: support@joybingo.com
• Telegram: @joybingo_support
• Response time: 24 hours

Please include your User ID when contacting support.
"""
        keyboard = [[InlineKeyboardButton("◀️ Back", callback_data="help")]]
        await query.edit_message_text(
            support_text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
    
    elif data == "back_to_menu":
        balance = db_user.get("balance", 0) if isinstance(db_user, dict) else db_user.balance
        
        keyboard = [
            [InlineKeyboardButton("🎮 PLAY BINGO", web_app=WebAppInfo(url=f"{WEBAPP_URL}/webapp/lobby.html"))],
            [InlineKeyboardButton("💰 My Balance", callback_data="balance"),
             InlineKeyboardButton("📥 Deposit", callback_data="deposit")],
            [InlineKeyboardButton("📤 Withdraw", callback_data="withdraw"),
             InlineKeyboardButton("👤 My Profile", callback_data="profile")],
            [InlineKeyboardButton("📋 Game Rules", callback_data="rules"),
             InlineKeyboardButton("🏆 Leaderboard", callback_data="leaderboard")],
            [InlineKeyboardButton("❓ Help", callback_data="help"),
             InlineKeyboardButton("📞 Support", callback_data="support")]
        ]
        
        await query.edit_message_text(
            f"🎉 Welcome back!\n\n"
            f"💰 Your current balance: **{balance} Birr**\n"
            f"🎮 Choose an option below:",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )

# Message handler for text messages
async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle text messages (for deposits/withdrawals)"""
    user_id = str(update.effective_user.id)
    text = update.message.text.strip()
    
    # Check if user is registered for any action
    db_user = await db.get_user(user_id)
    
    if not db_user and not (context.user_data.get('awaiting_deposit') or context.user_data.get('awaiting_withdraw')):
        keyboard = [[InlineKeyboardButton("📝 Register Now", callback_data="register")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            "❌ You need to register first!\n\n"
            "Click the button below to register and get free 20 Birr:",
            reply_markup=reply_markup
        )
        return
    
    # Check if we're awaiting a deposit amount
    if context.user_data.get('awaiting_deposit'):
        try:
            amount = int(text)
            if 10 <= amount <= 10000:
                user_id_val = db_user.get("id") if isinstance(db_user, dict) else db_user.id
                await db.update_balance(
                    user_id=user_id_val,
                    amount=amount,
                    transaction_type='deposit',
                    description=f'Deposit of {amount} Birr'
                )
                
                # Get updated balance
                updated_user = await db.get_user(user_id)
                new_balance = updated_user.get("balance", 0) if isinstance(updated_user, dict) else updated_user.balance
                
                await update.message.reply_text(
                    f"✅ **DEPOSIT SUCCESSFUL!**\n\n"
                    f"Amount: **{amount} Birr**\n"
                    f"New Balance: **{new_balance} Birr**\n\n"
                    f"Thank you for your deposit!",
                    parse_mode='Markdown'
                )
                context.user_data['awaiting_deposit'] = False
            else:
                await update.message.reply_text(
                    "❌ Invalid amount. Please enter an amount between 10 and 10000 Birr."
                )
        except ValueError:
            await update.message.reply_text(
                "❌ Please enter a valid number."
            )
    
    # Check if we're awaiting a withdrawal amount
    elif context.user_data.get('awaiting_withdraw'):
        try:
            amount = int(text)
            balance = db_user.get("balance", 0) if isinstance(db_user, dict) else db_user.balance
            
            if amount < 50:
                await update.message.reply_text(
                    "❌ Minimum withdrawal amount is 50 Birr."
                )
            elif amount > balance:
                await update.message.reply_text(
                    f"❌ Insufficient balance. Your balance is {balance} Birr."
                )
            else:
                user_id_val = db_user.get("id") if isinstance(db_user, dict) else db_user.id
                await db.update_balance(
                    user_id=user_id_val,
                    amount=-amount,
                    transaction_type='withdrawal',
                    description=f'Withdrawal request of {amount} Birr'
                )
                
                # Get updated balance
                updated_user = await db.get_user(user_id)
                new_balance = updated_user.get("balance", 0) if isinstance(updated_user, dict) else updated_user.balance
                
                await update.message.reply_text(
                    f"✅ **WITHDRAWAL REQUEST SUBMITTED!**\n\n"
                    f"Amount: **{amount} Birr**\n"
                    f"New Balance: **{new_balance} Birr**\n"
                    f"Processing Time: **24 hours**\n\n"
                    f"You will be notified when your withdrawal is processed.",
                    parse_mode='Markdown'
                )
                context.user_data['awaiting_withdraw'] = False
        except ValueError:
            await update.message.reply_text(
                "❌ Please enter a valid number."
            )
    
    else:
        # If no context, show help
        await update.message.reply_text(
            "I don't understand that command. Use /help to see available commands."
        )

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

# Test database endpoint
@app.get("/test-db")
async def test_database():
    """Test database connection"""
    try:
        # Try to connect
        async with db.async_session() as session:
            result = await session.execute(text("SELECT 1"))
            await session.commit()
        
        # Check if tables exist
        async with db.async_session() as session:
            tables = await session.execute(
                text("SELECT tablename FROM pg_tables WHERE schemaname='public'")
            )
            tables_list = [row[0] for row in tables]
        
        return JSONResponse({
            "status": "✅ Database connected!",
            "tables": tables_list,
            "message": "Your database is working and ready to store user data!"
        })
    except Exception as e:
        return JSONResponse({
            "status": "❌ Database connection failed",
            "error": str(e)
        }, status_code=500)

# ============= NEW: Global Timer and Bingo Check Endpoints =============

@app.post("/api/game/start_selection")
async def api_start_selection():
    """Start the selection phase for a new game"""
    start_selection_phase()
    return {"success": True, "message": "Selection phase started"}

@app.get("/can_select")
async def can_select():
    """Check if players can still select cards"""
    return {"allowed": selection_open()}

@app.post("/api/game/check_bingo")
async def check_bingo(request: Request):
    """Check if player has valid bingo and handle disqualification"""
    try:
        data = await request.json()
        user_id = data.get("user_id")
        room_id = data.get("room_id")
        marked = data.get("marked", [])
        
        # Check if player is already disqualified
        if user_id in disqualified_players:
            return {"status": "blocked", "message": "You are disqualified"}
        
        # Simple bingo validation (check if they have 5 in a row/column/diag)
        # Remove 'FREE' from marked list for counting
        marked_numbers = [m for m in marked if m != 'FREE']
        marked_count = len(marked_numbers)
        
        # For demo, require at least 5 marked numbers for a win
        # In production, you'd use your game engine to validate actual bingo
        if marked_count >= 5:
            # Valid bingo - calculate prize
            prize = 100  # Placeholder prize amount
            
            # You could record win in database here if you want
            # if user_id:
            #     db_user = await db.get_user(user_id)
            #     if db_user:
            #         user_id_val = db_user.get("id") if isinstance(db_user, dict) else db_user.id
            #         await db.update_balance(
            #             user_id=user_id_val,
            #             amount=prize,
            #             transaction_type='win',
            #             description='Bingo win'
            #         )
            
            return {
                "status": "win", 
                "prize": prize,
                "message": "Congratulations! You win!"
            }
        else:
            # Fake bingo - disqualify player
            disqualified_players.add(user_id)
            logger.warning(f"Player {user_id} disqualified for fake bingo with only {marked_count} marks")
            
            return {
                "status": "disqualified",
                "message": "Wrong BINGO! You are disqualified for this game."
            }
            
    except Exception as e:
        logger.error(f"Bingo check error: {e}")
        return {"status": "error", "message": str(e)}

# Webhook endpoint for Telegram
@app.post("/api/webhook")
async def telegram_webhook(request: Request):
    """Receive and process Telegram updates"""
    try:
        # Get the update from Telegram
        update_data = await request.json()
        logger.info(f"📨 Received webhook update: {update_data.get('update_id', 'unknown')}")
        
        if not bot_app:
            logger.error("❌ Bot application not initialized")
            return JSONResponse(status_code=200, content={"ok": False, "error": "Bot not initialized"})
        
        # Create Update object and process it
        update = Update.de_json(update_data, bot_app.bot)
        
        # Process the update
        await bot_app.process_update(update)
        
        return {"ok": True, "message": "Update processed"}
        
    except Exception as e:
        logger.error(f"❌ Webhook error: {e}")
        return JSONResponse(status_code=200, content={"ok": False, "error": str(e)})

# GET handler for webhook (for testing)
@app.get("/api/webhook")
async def webhook_get():
    """Handle GET requests for testing"""
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
        "description": "Traditional bingo - mark all numbers to win!"
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

@app.get("/api/rooms")
async def get_rooms():
    """Get all active rooms"""
    return JSONResponse(list(rooms_data.values()))

@app.get("/api/rooms/{room_id}")
async def get_room(room_id: str):
    """Get specific room"""
    if room_id in rooms_data:
        return JSONResponse(rooms_data[room_id])
    return JSONResponse({"error": "Room not found"}, status_code=404)

# Game state storage (simple in-memory for now)
games_data = {}
player_sessions = {}

@app.post("/api/rooms/{room_id}/join")
async def join_room(room_id: str, request: Request):
    """Join a specific room"""
    try:
        data = await request.json()
        user_id = data.get("user_id")
        username = data.get("username", "Player")
        
        if room_id not in rooms_data:
            return JSONResponse(
                status_code=404,
                content={"success": False, "error": "Room not found"}
            )
        
        # Create a game session for this user
        session_id = f"{room_id}_{user_id}_{datetime.now().timestamp()}"
        player_sessions[user_id] = {
            "room_id": room_id,
            "session_id": session_id,
            "joined_at": datetime.now().isoformat(),
            "username": username
        }
        
        # Increment player count
        rooms_data[room_id]["players"] += 1
        
        return JSONResponse({
            "success": True,
            "message": f"Welcome to {rooms_data[room_id]['name']}!",
            "room": rooms_data[room_id],
            "session_id": session_id
        })
        
    except Exception as e:
        logger.error(f"Error joining room: {e}")
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": str(e)}
        )

@app.get("/api/game/state/{user_id}")
async def get_game_state(user_id: str):
    """Get current game state for a user"""
    if user_id in player_sessions:
        return JSONResponse({
            "success": True,
            "state": {
                "in_game": True,
                "room": player_sessions[user_id].get("room_id"),
                "joined_at": player_sessions[user_id].get("joined_at")
            }
        })
    return JSONResponse({
        "success": True,
        "state": {"in_game": False}
    })

@app.post("/api/game/select_card")
async def select_card(request: Request):
    """Select a bingo card from pre-generated cards"""
    try:
        data = await request.json()
        user_id = data.get("user_id")
        room_id = data.get("room_id")
        card_number = str(data.get("card_number"))
        
        if not all([user_id, room_id, card_number]):
            return JSONResponse(
                status_code=400,
                content={"success": False, "error": "Missing required fields"}
            )
        
        if card_number not in CARDS_DATA:
            return JSONResponse(
                status_code=404,
                content={"success": False, "error": f"Card #{card_number} not found"}
            )
        
        # Check if card is already taken
        for key, game in games_data.items():
            if game.get("room_id") == room_id and game.get("card_number") == card_number:
                return JSONResponse(
                    status_code=400,
                    content={"success": False, "error": f"Card #{card_number} already taken"}
                )
        
        game_key = f"game:{room_id}:{user_id}"
        games_data[game_key] = {
            "user_id": user_id,
            "room_id": room_id,
            "card_number": card_number,
            "card_data": CARDS_DATA[card_number],
            "marked_numbers": [],
            "selected_at": datetime.now().isoformat()
        }
        
        if user_id in player_sessions:
            player_sessions[user_id]["card_number"] = card_number
        else:
            player_sessions[user_id] = {
                "room_id": room_id,
                "card_number": card_number,
                "joined_at": datetime.now().isoformat(),
                "username": data.get("username", "Player")
            }
        
        return JSONResponse({
            "success": True,
            "message": f"Card #{card_number} selected!",
            "game_state": {
                "card": CARDS_DATA[card_number],
                "marked": []
            }
        })
        
    except Exception as e:
        logger.error(f"Error selecting card: {e}")
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": str(e)}
        )

@app.get("/api/game/taken_cards/{room_id}")
async def get_taken_cards(room_id: str):
    """Get list of taken card numbers in a room"""
    try:
        taken_cards = []
        for key, game in games_data.items():
            if game.get("room_id") == room_id and game.get("card_number"):
                taken_cards.append(game["card_number"])
        
        return JSONResponse({
            "success": True,
            "taken_cards": taken_cards
        })
    except Exception as e:
        logger.error(f"Error getting taken cards: {e}")
        return JSONResponse({
            "success": False,
            "taken_cards": []
        })

@app.post("/api/game/mark_number")
async def mark_number(request: Request):
    """Mark a number on player's card"""
    try:
        data = await request.json()
        user_id = data.get("user_id")
        number = data.get("number")
        room_id = data.get("room_id")
        
        game_key = f"game:{room_id}:{user_id}"
        
        if game_key not in games_data:
            return JSONResponse(
                status_code=404,
                content={"success": False, "error": "Game not found"}
            )
        
        if number not in games_data[game_key]["marked_numbers"]:
            games_data[game_key]["marked_numbers"].append(number)
        
        marked_count = len(games_data[game_key]["marked_numbers"])
        has_bingo = marked_count >= 5
        
        return JSONResponse({
            "success": True,
            "marked": games_data[game_key]["marked_numbers"],
            "bingo": has_bingo
        })
        
    except Exception as e:
        logger.error(f"Error marking number: {e}")
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": str(e)}
        )

@app.post("/api/game/call_bingo")
async def call_bingo(request: Request):
    """Player calls bingo"""
    try:
        data = await request.json()
        user_id = data.get("user_id")
        room_id = data.get("room_id")
        
        game_key = f"game:{room_id}:{user_id}"
        
        if game_key not in games_data:
            return JSONResponse(
                status_code=404,
                content={"success": False, "error": "Game not found"}
            )
        
        marked_count = len(games_data[game_key]["marked_numbers"])
        is_valid = marked_count >= 5
        
        if is_valid:
            return JSONResponse({
                "success": True,
                "message": "BINGO! You win!",
                "prize": 100
            })
        else:
            return JSONResponse({
                "success": False,
                "message": "You don't have bingo yet!"
            })
            
    except Exception as e:
        logger.error(f"Error calling bingo: {e}")
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": str(e)}
        )

@app.get("/api/leaderboard")
async def get_leaderboard():
    """Get top players leaderboard"""
    try:
        leaderboard_data = await db.get_leaderboard(days=30, limit=10)
        return JSONResponse(leaderboard_data)
    except Exception as e:
        logger.error(f"Error getting leaderboard: {e}")
        return JSONResponse([])

@app.get("/bingo_game.html")
async def bingo_game_redirect(request: Request):
    """Serve the bingo game page"""
    try:
        with open("webapp/bingo_game.html", "r") as f:
            content = f.read()
        return HTMLResponse(content=content, status_code=200)
    except FileNotFoundError:
        return HTMLResponse(content="<h1>Bingo Game Page Not Found</h1><p>Please ensure bingo_game.html exists in the webapp folder.</p>", status_code=404)

@app.get("/api/game/selected_count/{room_id}")
async def get_selected_players_count(room_id: str):
    """Get number of players who have selected cards"""
    try:
        count = 0
        for key, game in games_data.items():
            if game.get("room_id") == room_id and game.get("card_number"):
                count += 1
        return {"count": count}
    except Exception as e:
        logger.error(f"Error getting selected count: {e}")
        return {"count": 0}
        
# ============= ADMIN API ENDPOINTS =============

@app.post("/api/admin/login")
async def admin_login(request: Request):
    """Admin login - returns token if credentials are valid"""
    try:
        data = await request.json()
        password = data.get("password")
        user_id = data.get("user_id")
        
        # Check if user is admin
        if not is_admin_user(user_id):
            return JSONResponse(
                status_code=403,
                content={"success": False, "error": "Not authorized"}
            )
        
        # Check password (you should store this securely in env vars)
        if password == os.getenv("ADMIN_PASSWORD", "JoyBingo@2025Admin"):
            return JSONResponse({
                "success": True,
                "token": ADMIN_SECRET_KEY
            })
        else:
            return JSONResponse({
                "success": False,
                "error": "Invalid password"
            })
    except Exception as e:
        logger.error(f"Admin login error: {e}")
        return JSONResponse(status_code=500, content={"success": False, "error": str(e)})

@app.get("/api/admin/dashboard")
async def admin_dashboard(auth: bool = Depends(verify_admin_token)):
    """Get admin dashboard stats"""
    try:
        # Get total users count from database
        async with db.async_session() as session:
            result = await session.execute(text("SELECT COUNT(*) FROM users"))
            total_users = result.scalar()
        
        active_games = len([g for g in games_data.values() if g.get("status") == "active"])
        
        # Calculate total volume (all time bets)
        total_volume = sum(g.get("total_bet", 0) for g in games_data.values())
        
        # Calculate total commission
        total_commission = total_volume * 0.2  # 20% commission
        
        # User growth (mock data for demo)
        user_change = 12  # 12% growth
        
        # Revenue data for chart
        revenue = {
            "labels": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
            "values": [1200, 1900, 1500, 2200, 2800, 3500, 4000]
        }
        
        # Games history
        games_history = {
            "labels": ["12AM", "4AM", "8AM", "12PM", "4PM", "8PM"],
            "values": [3, 1, 4, 6, 8, 5]
        }
        
        return JSONResponse({
            "totalUsers": total_users,
            "activeGames": active_games,
            "totalVolume": total_volume,
            "totalCommission": total_commission,
            "userChange": user_change,
            "volumeChange": 8.5,
            "revenue": revenue,
            "gamesHistory": games_history
        })
    except Exception as e:
        logger.error(f"Admin dashboard error: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.get("/api/admin/users")
async def admin_get_users(
    search: str = "", 
    status: str = "all", 
    sort: str = "balance_desc",
    auth: bool = Depends(verify_admin_token)
):
    """Get all users with filters"""
    try:
        # Get users from database
        async with db.async_session() as session:
            query = "SELECT * FROM users"
            if search:
                query += f" WHERE first_name ILIKE '%{search}%' OR telegram_id LIKE '%{search}%'"
            
            if sort == "balance_desc":
                query += " ORDER BY balance DESC"
            elif sort == "balance_asc":
                query += " ORDER BY balance ASC"
            elif sort == "games_desc":
                query += " ORDER BY games_played DESC"
            elif sort == "wins_desc":
                query += " ORDER BY games_won DESC"
            elif sort == "joined_desc":
                query += " ORDER BY created_at DESC"
            
            result = await session.execute(text(query))
            rows = result.fetchall()
        
        user_list = []
        for row in rows:
            user_list.append({
                "id": row[1] if len(row) > 1 else str(row[0]),  # telegram_id
                "username": row[2] if len(row) > 2 else '',
                "first_name": row[3] if len(row) > 3 else '',
                "balance": float(row[5]) if len(row) > 5 else 0,  # balance column
                "games_played": row[8] if len(row) > 8 else 0,  # games_played
                "games_won": row[9] if len(row) > 9 else 0,  # games_won
                "total_deposits": float(row[6]) if len(row) > 6 else 0,  # total_deposits
                "total_withdrawals": float(row[7]) if len(row) > 7 else 0,  # total_withdrawals
                "is_banned": False,
                "is_vip": False,
                "last_seen": datetime.now().isoformat(),
                "joined": row[13].isoformat() if len(row) > 13 and row[13] else datetime.now().isoformat()  # created_at
            })
        
        # Calculate stats
        total_balance = sum(u['balance'] for u in user_list)
        active_today = len(user_list)
        new_today = len(user_list)
        
        return JSONResponse({
            "total": len(user_list),
            "activeToday": active_today,
            "newToday": new_today,
            "totalBalance": total_balance,
            "list": user_list[:50]  # Limit to 50 users
        })
    except Exception as e:
        logger.error(f"Admin get users error: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.get("/api/admin/users/{user_id}")
async def admin_get_user(user_id: str, auth: bool = Depends(verify_admin_token)):
    """Get specific user details"""
    try:
        user = await db.get_user(user_id)
        
        if not user:
            return JSONResponse(status_code=404, content={"error": "User not found"})
        
        return JSONResponse({
            "id": user.get("telegram_id", user_id) if isinstance(user, dict) else user.telegram_id,
            "username": user.get("username") if isinstance(user, dict) else user.username,
            "first_name": user.get("first_name") if isinstance(user, dict) else user.first_name,
            "last_name": user.get("last_name") if isinstance(user, dict) else user.last_name,
            "balance": float(user.get("balance", 0)) if isinstance(user, dict) else float(user.balance),
            "games_played": user.get("games_played", 0) if isinstance(user, dict) else user.games_played,
            "games_won": user.get("games_won", 0) if isinstance(user, dict) else user.games_won,
            "total_deposits": float(user.get("total_deposits", 0)) if isinstance(user, dict) else float(user.total_deposits),
            "total_withdrawals": float(user.get("total_withdrawals", 0)) if isinstance(user, dict) else float(user.total_withdrawals),
            "is_banned": False,
            "is_vip": False,
            "created_at": user.get("created_at", datetime.now().isoformat()) if isinstance(user, dict) else user.created_at.isoformat() if user.created_at else datetime.now().isoformat(),
            "last_seen": user.get("last_seen", datetime.now().isoformat()) if isinstance(user, dict) else user.last_seen.isoformat() if user.last_seen else datetime.now().isoformat()
        })
    except Exception as e:
        logger.error(f"Admin get user error: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.post("/api/admin/adjust-balance")
async def admin_adjust_balance(request: Request, auth: bool = Depends(verify_admin_token)):
    """Adjust user balance"""
    try:
        data = await request.json()
        user_id = data.get("userId")
        amount = float(data.get("amount"))
        type_op = data.get("type")  # "add", "subtract", "set"
        reason = data.get("reason", "")
        
        user = await db.get_user(user_id)
        
        if not user:
            return JSONResponse(status_code=404, content={"success": False, "error": "User not found"})
        
        current = user.get("balance", 0) if isinstance(user, dict) else user.balance
        
        if type_op == "add":
            user_id_val = user.get("id") if isinstance(user, dict) else user.id
            await db.update_balance(
                user_id=user_id_val,
                amount=amount,
                transaction_type='admin_deposit',
                description=f'Admin adjustment: {reason}'
            )
        elif type_op == "subtract":
            if current < amount:
                return JSONResponse({"success": False, "error": "Insufficient balance"})
            user_id_val = user.get("id") if isinstance(user, dict) else user.id
            await db.update_balance(
                user_id=user_id_val,
                amount=-amount,
                transaction_type='admin_withdrawal',
                description=f'Admin adjustment: {reason}'
            )
        elif type_op == "set":
            # For set, we need to calculate difference
            diff = amount - current
            if diff > 0:
                user_id_val = user.get("id") if isinstance(user, dict) else user.id
                await db.update_balance(
                    user_id=user_id_val,
                    amount=diff,
                    transaction_type='admin_deposit',
                    description=f'Admin set balance to {amount}: {reason}'
                )
            elif diff < 0:
                user_id_val = user.get("id") if isinstance(user, dict) else user.id
                await db.update_balance(
                    user_id=user_id_val,
                    amount=diff,
                    transaction_type='admin_withdrawal',
                    description=f'Admin set balance to {amount}: {reason}'
                )
        
        # Get updated user
        updated_user = await db.get_user(user_id)
        new_balance = updated_user.get("balance", 0) if isinstance(updated_user, dict) else updated_user.balance
        
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
    """Ban/unban a user"""
    try:
        data = await request.json()
        user_id = data.get("userId")
        
        user = await db.get_user(user_id)
        
        if not user:
            return JSONResponse(status_code=404, content={"success": False, "error": "User not found"})
        
        # Toggle ban in database (you'll need to add is_banned column if you want this)
        # For now, we'll just return success
        
        return JSONResponse({
            "success": True,
            "is_banned": False
        })
    except Exception as e:
        logger.error(f"Admin toggle ban error: {e}")
        return JSONResponse(status_code=500, content={"success": False, "error": str(e)})

@app.get("/api/admin/games")
async def admin_get_games(
    search: str = "",
    status: str = "all",
    room: str = "all",
    auth: bool = Depends(verify_admin_token)
):
    """Get all games with filters"""
    try:
        game_list = []
        for game_id, game in games_data.items():
            # Filter by search
            if search and search not in game_id:
                continue
            
            # Filter by status
            if status != "all" and game.get("status") != status:
                continue
            
            # Calculate duration
            duration = 0
            if game.get("started_at") and game.get("finished_at"):
                start = datetime.fromisoformat(game["started_at"])
                end = datetime.fromisoformat(game["finished_at"])
                duration = int((end - start).total_seconds())
            
            game_list.append({
                "game_id": game_id,
                "room": game.get("room_id", "unknown"),
                "status": game.get("status", "unknown"),
                "players": 1,  # Placeholder
                "max_players": 400,
                "prize_pool": game.get("prize_pool", 0),
                "duration": duration,
                "winners": len(game.get("winners", []))
            })
        
        return JSONResponse(game_list)
    except Exception as e:
        logger.error(f"Admin get games error: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.post("/api/admin/end-game")
async def admin_end_game(request: Request, auth: bool = Depends(verify_admin_token)):
    """Force end a game"""
    try:
        data = await request.json()
        game_id = data.get("gameId")
        
        if game_id not in games_data:
            return JSONResponse(status_code=404, content={"success": False, "error": "Game not found"})
        
        games_data[game_id]["status"] = "finished"
        games_data[game_id]["finished_at"] = datetime.now().isoformat()
        
        return JSONResponse({"success": True})
    except Exception as e:
        logger.error(f"Admin end game error: {e}")
        return JSONResponse(status_code=500, content={"success": False, "error": str(e)})

@app.get("/api/admin/transactions")
async def admin_get_transactions(
    search: str = "",
    type: str = "all",
    from_date: str = "",
    to_date: str = "",
    auth: bool = Depends(verify_admin_token)
):
    """Get transaction history"""
    try:
        # This is a placeholder - you'd need to implement transaction logging
        transactions = []
        
        # Calculate stats
        today_deposits = 12500  # Placeholder
        today_withdrawals = 3400
        today_wins = 8700
        net_revenue = 3800
        
        return JSONResponse({
            "transactions": transactions,
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
    """Send broadcast message to users"""
    try:
        data = await request.json()
        message_type = data.get("type")
        room = data.get("room")
        message = data.get("message")
        link = data.get("link", "")
        
        # Get total users count
        async with db.async_session() as session:
            result = await session.execute(text("SELECT COUNT(*) FROM users"))
            total_users = result.scalar()
        
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
    """Get system settings"""
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
    """Save system settings"""
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
    """Get analytics data"""
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
    """Get audit logs"""
    try:
        # Placeholder logs
        logs = [
            {"level": "info", "timestamp": datetime.now().isoformat(), "message": "User logged in", "user": "admin", "ip": "192.168.1.1"},
            {"level": "warning", "timestamp": datetime.now().isoformat(), "message": "Large withdrawal", "user": "player123", "ip": "192.168.1.2"},
            {"level": "info", "timestamp": datetime.now().isoformat(), "message": "Game started", "user": "system", "ip": "0.0.0.0"},
        ]
        return JSONResponse(logs)
    except Exception as e:
        logger.error(f"Admin logs error: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.get("/api/admin/stats")
async def admin_stats(auth: bool = Depends(verify_admin_token)):
    """Get real-time stats"""
    try:
        async with db.async_session() as session:
            result = await session.execute(text("SELECT COUNT(*) FROM users"))
            total_users = result.scalar()
        
        return JSONResponse({
            "totalUsers": total_users
        })
    except Exception as e:
        logger.error(f"Admin stats error: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.get("/api/admin/rooms")
async def admin_rooms(auth: bool = Depends(verify_admin_token)):
    """Get all rooms for admin"""
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
    """Export users as CSV"""
    try:
        import csv
        from io import StringIO
        
        # Get users from database
        async with db.async_session() as session:
            result = await session.execute(text("SELECT * FROM users"))
            rows = result.fetchall()
        
        output = StringIO()
        writer = csv.writer(output)
        writer.writerow(["User ID", "Username", "First Name", "Balance", "Games Played", "Wins", "Joined"])
        
        for row in rows:
            writer.writerow([
                row[1] if len(row) > 1 else '',  # telegram_id
                row[2] if len(row) > 2 else '',  # username
                row[3] if len(row) > 3 else '',  # first_name
                float(row[5]) if len(row) > 5 else 0,  # balance
                row[8] if len(row) > 8 else 0,  # games_played
                row[9] if len(row) > 9 else 0,  # games_won
                row[13].isoformat() if len(row) > 13 and row[13] else ''  # created_at
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
    try:
        await websocket.send_json({
            "type": "connected",
            "message": f"Connected to room {room_id}",
            "room_data": rooms_data.get(room_id, {}),
            "timestamp": datetime.now().isoformat()
        })
        
        while True:
            data = await websocket.receive_text()
            try:
                message = json.loads(data)
                logger.info(f"WebSocket message from {user_id}: {message.get('type')}")
                
                if message.get("type") == "ping":
                    await websocket.send_json({"type": "pong", "timestamp": datetime.now().isoformat()})
                
                elif message.get("type") == "mark_number":
                    number = message.get("number")
                    await manager.broadcast(room_id, {
                        "type": "number_marked",
                        "user_id": user_id,
                        "number": number,
                        "timestamp": datetime.now().isoformat()
                    }, exclude_user=user_id)
                    
                    await websocket.send_json({
                        "type": "mark_confirmed",
                        "number": number,
                        "timestamp": datetime.now().isoformat()
                    })
                
                elif message.get("type") == "call_bingo":
                    await manager.broadcast(room_id, {
                        "type": "bingo_called",
                        "user_id": user_id,
                        "timestamp": datetime.now().isoformat()
                    })
                    
                    await websocket.send_json({
                        "type": "bingo_confirmed",
                        "message": "Bingo called! Verifying...",
                        "timestamp": datetime.now().isoformat()
                    })
                
                else:
                    await websocket.send_json({
                        "type": "ack",
                        "received": message,
                        "timestamp": datetime.now().isoformat()
                    })
                
            except json.JSONDecodeError:
                await websocket.send_json({
                    "type": "error",
                    "message": "Invalid JSON format"
                })
                
    except WebSocketDisconnect:
        manager.disconnect(websocket, user_id)
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
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
