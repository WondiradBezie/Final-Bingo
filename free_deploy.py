# free_deploy.py
import os
import json
import asyncio
import logging
from datetime import datetime
from typing import Dict, Set, Optional, Any
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
import uvicorn
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import Application, CommandHandler, ContextTypes
import httpx
import random

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

# Initialize bot application
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
WEBAPP_URL = os.getenv("WEBAPP_URL", "https://conceptual-debby-wond-7482233b.koyeb.app")

# Create bot application
bot_app = None

@app.on_event("startup")
async def startup_event():
    """Initialize bot on startup"""
    global bot_app
    if BOT_TOKEN:
        # Build bot application
        bot_app = Application.builder().token(BOT_TOKEN).build()
        
        # Add handlers
        bot_app.add_handler(CommandHandler("start", start_command))
        bot_app.add_handler(CommandHandler("help", help_command))
        bot_app.add_handler(CommandHandler("play", play_command))
        
        # Initialize bot
        await bot_app.initialize()
        logger.info("✅ Bot application initialized")
    else:
        logger.warning("⚠️ BOT_TOKEN not set, bot commands disabled")

# Command handlers
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command"""
    user = update.effective_user
    logger.info(f"User {user.id} (@{user.username}) started the bot")
    
    keyboard = [
        [InlineKeyboardButton("🎮 Play Bingo", web_app=WebAppInfo(url=f"{WEBAPP_URL}/webapp/lobby.html"))],
        [InlineKeyboardButton("❓ Help", callback_data="help"),
         InlineKeyboardButton("💰 Balance", callback_data="balance")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        f"🎉 Welcome to Joy Bingo, {user.first_name}!\n\n"
        f"Click the button below to start playing:",
        reply_markup=reply_markup
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /help command"""
    help_text = """
🎮 **How to Play Bingo:**
1. Click "Play Bingo"
2. Select a card (cost varies by room)
3. Numbers are called every 2 seconds
4. Mark numbers on your card
5. Get BINGO to win!

📋 **Commands:**
/start - Main menu
/help - This help
/play - Open game directly

🏆 **Game Modes:**
• Classic - Mark all numbers
• Blackout - Fill entire card
• Line - Complete any line
• Four Corners - Get all corners

Need help? Contact @admin
"""
    await update.message.reply_text(help_text, parse_mode='Markdown')

async def play_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /play command"""
    keyboard = [[InlineKeyboardButton("🎮 Open Game", web_app=WebAppInfo(url=f"{WEBAPP_URL}/webapp/lobby.html"))]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "Click below to enter the game lobby:",
        reply_markup=reply_markup
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
    """Select a bingo card"""
    try:
        data = await request.json()
        user_id = data.get("user_id")
        room_id = data.get("room_id")
        card_number = data.get("card_number")
        
        # Simple validation
        if not all([user_id, room_id, card_number]):
            return JSONResponse(
                status_code=400,
                content={"success": False, "error": "Missing required fields"}
            )
        
        # Store card selection
        game_key = f"game:{room_id}:{user_id}"
        games_data[game_key] = {
            "user_id": user_id,
            "room_id": room_id,
            "card_number": card_number,
            "marked_numbers": [],
            "selected_at": datetime.now().isoformat()
        }
        
        return JSONResponse({
            "success": True,
            "message": f"Card #{card_number} selected!",
            "game_state": {
                "card": generate_sample_card(card_number),
                "marked": []
            }
        })
        
    except Exception as e:
        logger.error(f"Error selecting card: {e}")
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": str(e)}
        )

def generate_sample_card(card_number):
    """Generate a sample bingo card"""
    card = []
    for col in range(5):
        start = col * 15 + 1
        numbers = random.sample(range(start, start + 15), 5)
        card.extend(numbers)
    return card

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
        
        # Add to marked numbers
        if number not in games_data[game_key]["marked_numbers"]:
            games_data[game_key]["marked_numbers"].append(number)
        
        # Check for bingo (simplified)
        marked_count = len(games_data[game_key]["marked_numbers"])
        has_bingo = marked_count >= 5  # Simplified condition
        
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
        
        # Verify bingo (simplified)
        marked_count = len(games_data[game_key]["marked_numbers"])
        is_valid = marked_count >= 5
        
        if is_valid:
            return JSONResponse({
                "success": True,
                "message": "BINGO! You win!",
                "prize": 100  # Placeholder prize
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

# ============= NEW ENDPOINTS ADDED HERE =============

@app.get("/api/leaderboard")
async def get_leaderboard():
    """Get top players leaderboard"""
    # Simple placeholder leaderboard
    leaderboard = [
        {"username": "Player1", "wins": 10, "winnings": 5000},
        {"username": "Player2", "wins": 8, "winnings": 4000},
        {"username": "Player3", "wins": 6, "winnings": 3000},
        {"username": "Player4", "wins": 5, "winnings": 2500},
        {"username": "Player5", "wins": 4, "winnings": 2000},
    ]
    return JSONResponse(leaderboard)

@app.get("/bingo_game.html")
async def bingo_game_redirect(request: Request):
    """Serve the bingo game page"""
    try:
        with open("webapp/bingo_game.html", "r") as f:
            content = f.read()
        return HTMLResponse(content=content, status_code=200)
    except FileNotFoundError:
        return HTMLResponse(content="<h1>Bingo Game Page Not Found</h1><p>Please ensure bingo_game.html exists in the webapp folder.</p>", status_code=404)

# ============= END OF NEW ENDPOINTS =============

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
        
        # Update player count
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
                    # Find user for this connection (simplified)
                    await connection.send_json(message)
                except:
                    disconnected.add(connection)
            
            # Clean up disconnected
            for conn in disconnected:
                self.active_connections[room_id].discard(conn)
            
            if room_id in rooms_data:
                rooms_data[room_id]["players"] = len(self.active_connections[room_id])

manager = ConnectionManager()

@app.websocket("/ws/{room_id}/{user_id}")
async def websocket_endpoint(websocket: WebSocket, room_id: str, user_id: str):
    await manager.connect(websocket, room_id, user_id)
    try:
        # Send welcome message
        await websocket.send_json({
            "type": "connected",
            "message": f"Connected to room {room_id}",
            "room_data": rooms_data.get(room_id, {}),
            "timestamp": datetime.now().isoformat()
        })
        
        while True:
            # Wait for messages
            data = await websocket.receive_text()
            try:
                message = json.loads(data)
                logger.info(f"WebSocket message from {user_id}: {message.get('type')}")
                
                # Handle different message types
                if message.get("type") == "ping":
                    await websocket.send_json({"type": "pong", "timestamp": datetime.now().isoformat()})
                
                elif message.get("type") == "mark_number":
                    # Handle number marking
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
                    # Handle bingo call
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
                    # Echo back for now
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
