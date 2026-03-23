# server.py - COMPLETE FIXED VERSION with Rate Limiting and Security
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, Depends, HTTPException, Header, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import asyncio
import json
import logging
from typing import Dict, Set, Optional, List
from datetime import datetime, timedelta
import random
import secrets
import hashlib
import bcrypt
import jwt
from pydantic import BaseModel, Field
import time
from collections import defaultdict

from game_engine import GameManager, GameMode, BingoGame
from database import db
from config import *

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Security configuration
SECRET_KEY = os.getenv("SECRET_KEY", secrets.token_urlsafe(32))
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24  # 24 hours

# Rate limiting
rate_limits: Dict[str, List[float]] = defaultdict(list)
RATE_LIMIT_PER_SECOND = 5
RATE_LIMIT_PER_MINUTE = 60

def check_rate_limit(identifier: str, limit_per_second: int = 5, limit_per_minute: int = 60) -> bool:
    """Check if request is within rate limits"""
    now = time.time()
    
    # Clean old entries
    rate_limits[identifier] = [t for t in rate_limits[identifier] if t > now - 60]
    
    if len(rate_limits[identifier]) >= limit_per_minute:
        return False
    
    # Check per second
    recent = [t for t in rate_limits[identifier] if t > now - 1]
    if len(recent) >= limit_per_second:
        return False
    
    rate_limits[identifier].append(now)
    return True

# Admin configuration
ADMIN_IDS = [int(os.getenv("ADMIN_IDS", "8576569079"))]
ADMIN_SECRET_KEY = os.getenv("ADMIN_SECRET_KEY", secrets.token_urlsafe(32))
ADMIN_PASSWORD_HASH = os.getenv("ADMIN_PASSWORD_HASH", bcrypt.hashpw(b"JoyBingo@2025Admin", bcrypt.gensalt()))

# JWT Security
security = HTTPBearer()

def create_access_token(data: dict, expires_delta: timedelta = None):
    """Create JWT access token"""
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

def verify_token(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """Verify JWT token"""
    token = credentials.credentials
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id = payload.get("sub")
        if user_id is None:
            raise HTTPException(status_code=401, detail="Invalid token")
        return {"user_id": user_id, "is_admin": payload.get("is_admin", False)}
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Invalid token")

def verify_admin_token(authorization: Optional[str] = Header(None)):
    """Verify admin token from header (legacy)"""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    token = authorization.replace("Bearer ", "")
    
    if token != ADMIN_SECRET_KEY:
        # Try JWT verification
        try:
            payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
            if payload.get("is_admin"):
                return True
        except:
            pass
        raise HTTPException(status_code=401, detail="Invalid token")
    
    return True

def is_admin_user(user_id: int) -> bool:
    """Check if a Telegram user is admin"""
    return user_id in ADMIN_IDS

# Create FastAPI app
app = FastAPI(title="Joy Bingo API", version="2.0.0")

# Add security middleware
app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=["*"]  # Configure in production
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Configure in production
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["*"],
)

# Global state
selection_start_time = None
SELECTION_DURATION = 20
disqualified_players = set()

# Initialize game manager with database
game_manager = GameManager(db_manager=db)

def start_selection_phase():
    """Start the global selection timer"""
    global selection_start_time
    selection_start_time = time.time()
    disqualified_players.clear()

def selection_open():
    """Check if selection phase is still open"""
    if selection_start_time is None:
        return False
    return (time.time() - selection_start_time) < SELECTION_DURATION

# ============= AUTHENTICATION ENDPOINTS =============

class LoginRequest(BaseModel):
    user_id: int
    password: str

@app.post("/api/auth/login")
async def login(request: LoginRequest):
    """Authenticate user and return JWT token"""
    if not is_admin_user(request.user_id):
        raise HTTPException(status_code=403, detail="Not authorized")
    
    if not bcrypt.checkpw(request.password.encode(), ADMIN_PASSWORD_HASH):
        raise HTTPException(status_code=401, detail="Invalid password")
    
    token = create_access_token(
        data={"sub": str(request.user_id), "is_admin": True},
        expires_delta=timedelta(hours=24)
    )
    
    return {"access_token": token, "token_type": "bearer"}

@app.post("/api/auth/refresh")
async def refresh_token(auth: dict = Depends(verify_token)):
    """Refresh JWT token"""
    new_token = create_access_token(
        data={"sub": auth["user_id"], "is_admin": auth["is_admin"]}
    )
    return {"access_token": new_token, "token_type": "bearer"}

# ============= HEALTH & MONITORING =============

@app.get("/")
async def root():
    return {
        "status": "online",
        "service": "Joy Bingo",
        "version": "2.0.0",
        "mode": "production",
        "bot_configured": bool(BOT_TOKEN),
        "timestamp": datetime.now().isoformat()
    }

@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "active_games": len(game_manager.games),
        "active_players": sum(len(g.players) for g in game_manager.games.values()),
        "db_connected": db.initialized if db else False
    }

@app.get("/api/game/verify/{game_id}")
async def verify_game_fairness(game_id: str, user_id: str):
    """Verify game fairness (provably fair)"""
    game = game_manager.games.get(game_id)
    if not game:
        raise HTTPException(status_code=404, detail="Game not found")
    
    result = game.verify_fairness(user_id)
    return JSONResponse(result)

# ============= GAME ENDPOINTS =============

class JoinGameRequest(BaseModel):
    user_id: str
    username: str
    room_id: str
    card_number: Optional[int] = None
    client_seed: Optional[str] = None

class MarkNumberRequest(BaseModel):
    user_id: str
    number: int
    timestamp: Optional[float] = None

@app.post("/api/game/join")
async def join_game(request: JoinGameRequest):
    """Join a game with anti-cheat validation"""
    
    # Rate limiting
    if not check_rate_limit(request.user_id):
        raise HTTPException(status_code=429, detail="Too many requests")
    
    success, msg, game, player_data = await game_manager.join_game(
        request.user_id,
        request.username,
        request.room_id,
        request.card_number,
        request.client_seed
    )
    
    if not success:
        raise HTTPException(status_code=400, detail=msg)
    
    return JSONResponse({
        'success': True,
        'message': msg,
        'game_state': game.get_state(request.user_id) if game else None,
        'player_data': player_data
    })

@app.post("/api/game/mark")
async def mark_number(request: MarkNumberRequest):
    """Mark a number with server-side validation"""
    
    # Rate limiting
    if not check_rate_limit(request.user_id):
        raise HTTPException(status_code=429, detail="Too many requests")
    
    timestamp = request.timestamp or time.time()
    
    result = await game_manager.player_action(
        request.user_id,
        'mark',
        {'number': request.number},
        timestamp
    )
    
    if not result['success']:
        raise HTTPException(status_code=400, detail=result['message'])
    
    return JSONResponse(result)

@app.post("/api/game/bingo")
async def call_bingo(request: Request):
    """Manual bingo call - server validates"""
    data = await request.json()
    user_id = data.get("user_id")
    
    if not check_rate_limit(user_id):
        raise HTTPException(status_code=429, detail="Too many requests")
    
    result = await game_manager.player_action(user_id, 'bingo', {})
    
    if not result['success']:
        raise HTTPException(status_code=400, detail=result['message'])
    
    return JSONResponse(result)

@app.get("/api/game/state/{user_id}")
async def get_game_state(user_id: str):
    """Get current game state for user"""
    game_id = game_manager.user_game.get(user_id)
    if not game_id or game_id not in game_manager.games:
        return JSONResponse({"in_game": False})
    
    game = game_manager.games[game_id]
    return JSONResponse({
        "in_game": True,
        "state": game.get_state(user_id)
    })

@app.get("/api/game/taken_cards/{room_id}")
async def get_taken_cards(room_id: str):
    """Get cards already taken in room"""
    try:
        taken_cards = []
        for game in game_manager.games.values():
            if game.room.room_id == room_id:
                for player in game.players.values():
                    taken_cards.append(str(player.card_number))
        
        return JSONResponse({
            "success": True,
            "taken_cards": taken_cards
        })
    except Exception as e:
        logger.error(f"Error getting taken cards: {e}")
        return JSONResponse({"success": False, "taken_cards": []})

@app.post("/api/game/select_card")
async def select_card(request: Request):
    """DEPRECATED: Use /api/game/join instead"""
    data = await request.json()
    return await join_game(JoinGameRequest(**data))

@app.post("/api/game/check_bingo")
async def check_bingo(request: Request):
    """Server-side bingo verification endpoint"""
    try:
        data = await request.json()
        user_id = data.get("user_id")
        room_id = data.get("room_id")
        marked = data.get("marked", [])
        
        # Rate limiting
        if not check_rate_limit(user_id):
            return {"status": "error", "message": "Too many requests"}
        
        # Get user's game
        game_id = game_manager.user_game.get(user_id)
        if not game_id or game_id not in game_manager.games:
            return {"status": "error", "message": "Not in a game"}
        
        game = game_manager.games[game_id]
        
        # Check if player is disqualified
        if user_id in disqualified_players:
            return {"status": "blocked", "message": "You are disqualified"}
        
        # Get player
        player = game.players.get(user_id)
        if not player:
            return {"status": "error", "message": "Player not found"}
        
        # Verify actual marks from server
        actual_marked = player.marked
        
        # Compare client marks with server marks
        client_marked_set = set(marked)
        if client_marked_set != actual_marked:
            # Client mismatch - possible cheating
            logger.warning(f"Client-server mismatch for {user_id}: client={len(client_marked_set)}, server={len(actual_marked)}")
            disqualified_players.add(user_id)
            return {
                "status": "disqualified",
                "message": "Invalid game state detected"
            }
        
        # Check for bingo
        has_bingo = await game.check_player_bingo(user_id)
        
        if has_bingo:
            # Auto-process win
            await game._auto_bingo(user_id)
            return {
                "status": "win",
                "prize": player.win_amount,
                "message": f"BINGO! You win {player.win_amount} Birr!"
            }
        else:
            # Check if player claimed bingo but doesn't have it
            if "bingo" in data and data.get("bingo"):
                disqualified_players.add(user_id)
                return {
                    "status": "disqualified",
                    "message": "False BINGO call - Disqualified"
                }
            
            return {
                "status": "no_bingo",
                "message": "No bingo yet"
            }
            
    except Exception as e:
        logger.error(f"Bingo check error: {e}")
        return {"status": "error", "message": str(e)}

@app.get("/api/game/selected_count/{room_id}")
async def get_selected_players_count(room_id: str):
    """Get count of players who have selected cards"""
    try:
        count = 0
        for game in game_manager.games.values():
            if game.room.room_id == room_id:
                count += len(game.players)
        return {"count": count}
    except Exception as e:
        logger.error(f"Error getting selected count: {e}")
        return {"count": 0}

# ============= ROOM ENDPOINTS =============

@app.get("/api/rooms")
async def get_rooms():
    """Get all available game rooms"""
    rooms = []
    for room_id, room in game_manager.rooms.items():
        current_game = room.current_game
        rooms.append({
            'room_id': room_id,
            'name': room.name,
            'description': room.description,
            'mode': room.mode.value if hasattr(room, 'mode') else 'classic',
            'players': len(current_game.players) if current_game else 0,
            'max_players': room.max_players,
            'status': current_game.status.value if current_game else 'waiting',
            'prize_pool': current_game.prize_pool if current_game else 0,
            'card_price': room.card_price,
            'min_players': room.min_players
        })
    return JSONResponse(rooms)

@app.get("/api/rooms/{room_id}")
async def get_room(room_id: str):
    """Get specific room details"""
    room = game_manager.rooms.get(room_id)
    if not room:
        raise HTTPException(status_code=404, detail="Room not found")
    
    current_game = room.current_game
    return JSONResponse({
        'room_id': room_id,
        'name': room.name,
        'description': room.description,
        'players': len(current_game.players) if current_game else 0,
        'max_players': room.max_players,
        'status': current_game.status.value if current_game else 'waiting',
        'prize_pool': current_game.prize_pool if current_game else 0
    })

# ============= LEADERBOARD =============

@app.get("/api/leaderboard")
async def get_leaderboard(days: int = 30):
    """Get leaderboard from database"""
    try:
        leaderboard = await game_manager.get_leaderboard()
        return JSONResponse(leaderboard)
    except Exception as e:
        logger.error(f"Leaderboard error: {e}")
        return JSONResponse([])

# ============= ADMIN API ENDPOINTS (Protected) =============

@app.post("/api/admin/login")
async def admin_login(request: Request):
    """Admin login endpoint"""
    try:
        data = await request.json()
        password = data.get("password")
        user_id = data.get("user_id")
        
        if not is_admin_user(user_id):
            raise HTTPException(status_code=403, detail="Not authorized")
        
        if not bcrypt.checkpw(password.encode(), ADMIN_PASSWORD_HASH):
            raise HTTPException(status_code=401, detail="Invalid password")
        
        # Create JWT token
        token = create_access_token(
            data={"sub": str(user_id), "is_admin": True}
        )
        
        return JSONResponse({
            "success": True,
            "token": token
        })
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Admin login error: {e}")
        return JSONResponse(status_code=500, content={"success": False, "error": str(e)})

@app.get("/api/admin/dashboard")
async def admin_dashboard(auth: dict = Depends(verify_token)):
    """Get admin dashboard data"""
    if not auth.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admin access required")
    
    try:
        total_users = await db.get_user_count() if db else 0
        active_games = len([g for g in game_manager.games.values() if g.status == GameStatus.ACTIVE])
        total_volume = sum(g.total_bet for g in game_manager.games.values())
        total_commission = total_volume * 0.2
        
        revenue = {
            "labels": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
            "values": [1200, 1900, 1500, 2200, 2800, 3500, 4000]
        }
        
        games_history = {
            "labels": ["12AM", "4AM", "8AM", "12PM", "4PM", "8PM"],
            "values": [3, 1, 4, 6, 8, 5]
        }
        
        return JSONResponse({
            "totalUsers": total_users,
            "activeGames": active_games,
            "totalVolume": total_volume,
            "totalCommission": total_commission,
            "userChange": 12,
            "volumeChange": 8.5,
            "revenue": revenue,
            "gamesHistory": games_history
        })
    except Exception as e:
        logger.error(f"Admin dashboard error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/admin/users")
async def admin_get_users(
    search: str = "", 
    status: str = "all", 
    sort: str = "balance_desc",
    auth: dict = Depends(verify_token)
):
    """Get users list (admin only)"""
    if not auth.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admin access required")
    
    try:
        users_list = await db.get_all_users(limit=100, offset=0) if db else []
        
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
        
        total_balance = sum(u.get('balance', 0) for u in filtered_users)
        
        return JSONResponse({
            "total": len(filtered_users),
            "activeToday": len(filtered_users),
            "newToday": 0,
            "totalBalance": total_balance,
            "list": filtered_users[:50]
        })
    except Exception as e:
        logger.error(f"Admin get users error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/admin/adjust-balance")
async def admin_adjust_balance(request: Request, auth: dict = Depends(verify_token)):
    """Admin adjust user balance"""
    if not auth.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admin access required")
    
    try:
        data = await request.json()
        user_id = data.get("userId")
        amount = float(data.get("amount"))
        type_op = data.get("type")
        reason = data.get("reason", "")
        
        user = await db.get_user(user_id) if db else None
        
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        
        current = user.get("balance", 0)
        
        if type_op == "add":
            user_id_val = user.get("id")
            await db.update_balance(
                user_id=user_id_val,
                amount=amount,
                transaction_type='admin_deposit',
                description=f'Admin adjustment: {reason}'
            )
        elif type_op == "subtract":
            if current < amount:
                return JSONResponse({"success": False, "error": "Insufficient balance"})
            user_id_val = user.get("id")
            await db.update_balance(
                user_id=user_id_val,
                amount=-amount,
                transaction_type='admin_withdrawal',
                description=f'Admin adjustment: {reason}'
            )
        elif type_op == "set":
            diff = amount - current
            if diff != 0:
                user_id_val = user.get("id")
                await db.update_balance(
                    user_id=user_id_val,
                    amount=diff,
                    transaction_type='admin_deposit' if diff > 0 else 'admin_withdrawal',
                    description=f'Admin set balance to {amount}: {reason}'
                )
        
        updated_user = await db.get_user(user_id)
        new_balance = updated_user.get("balance", 0)
        
        logger.info(f"Admin adjusted balance for user {user_id}: {current} -> {new_balance}")
        
        return JSONResponse({
            "success": True,
            "new_balance": float(new_balance)
        })
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Admin adjust balance error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# ============= WEBSOCKET ENDPOINT =============

class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[str, Set[WebSocket]] = {}
        self.user_connections: Dict[str, WebSocket] = {}
        self.user_rooms: Dict[str, str] = {}
        self.connection_times: Dict[str, float] = {}
    
    async def connect(self, websocket: WebSocket, user_id: str, room_id: str):
        await websocket.accept()
        if room_id not in self.active_connections:
            self.active_connections[room_id] = set()
        self.active_connections[room_id].add(websocket)
        self.user_connections[user_id] = websocket
        self.user_rooms[user_id] = room_id
        self.connection_times[user_id] = time.time()
        logger.info(f"User {user_id} connected to room {room_id}")
    
    def disconnect(self, websocket: WebSocket, user_id: str):
        room_id = self.user_rooms.get(user_id)
        if room_id and room_id in self.active_connections:
            self.active_connections[room_id].discard(websocket)
        if user_id in self.user_connections:
            del self.user_connections[user_id]
        if user_id in self.user_rooms:
            del self.user_rooms[user_id]
        if user_id in self.connection_times:
            del self.connection_times[user_id]
    
    async def broadcast_to_room(self, room_id: str, message: dict, exclude_user: str = None):
        if room_id not in self.active_connections:
            return
        disconnected = set()
        for connection in self.active_connections[room_id]:
            try:
                user_id = self.get_user_by_connection(connection)
                if exclude_user and user_id == exclude_user:
                    continue
                await connection.send_json(message)
            except:
                disconnected.add(connection)
        for conn in disconnected:
            self.active_connections[room_id].discard(conn)
    
    async def send_to_user(self, user_id: str, message: dict):
        if user_id in self.user_connections:
            try:
                await self.user_connections[user_id].send_json(message)
            except:
                pass
    
    def get_user_by_connection(self, websocket: WebSocket) -> str:
        for uid, conn in self.user_connections.items():
            if conn == websocket:
                return uid
        return None

manager = ConnectionManager()

@app.websocket("/ws/{room_id}/{user_id}")
async def websocket_endpoint(websocket: WebSocket, room_id: str, user_id: str):
    await manager.connect(websocket, user_id, room_id)
    try:
        # Send initial game state
        game_id = game_manager.user_game.get(user_id)
        if game_id and game_id in game_manager.games:
            game = game_manager.games[game_id]
            await websocket.send_json({
                'type': 'game_state',
                'data': game.get_state(user_id)
            })
        
        while True:
            # Set receive timeout
            data = await asyncio.wait_for(websocket.receive_text(), timeout=60.0)
            message = json.loads(data)
            
            # Rate limiting per connection
            if not check_rate_limit(user_id):
                await websocket.send_json({
                    'type': 'error',
                    'message': 'Rate limited. Please slow down.'
                })
                continue
            
            msg_type = message.get('type')
            
            if msg_type == 'mark':
                number = message.get('number')
                timestamp = message.get('timestamp', time.time())
                result = await game_manager.player_action(
                    user_id, 'mark', {'number': number}, timestamp
                )
                await websocket.send_json({
                    'type': 'mark_result',
                    'data': result
                })
                
                # Broadcast to room if valid mark
                if result.get('success') and room_id:
                    await manager.broadcast_to_room(
                        room_id,
                        {
                            'type': 'number_marked',
                            'user_id': user_id,
                            'number': number,
                            'timestamp': timestamp
                        },
                        exclude_user=user_id
                    )
                    
                    if result.get('bingo'):
                        await manager.broadcast_to_room(
                            room_id,
                            {
                                'type': 'bingo_achieved',
                                'user_id': user_id,
                                'message': f"Player got BINGO!"
                            }
                        )
            
            elif msg_type == 'bingo':
                result = await game_manager.player_action(user_id, 'bingo', {})
                await websocket.send_json({
                    'type': 'bingo_result',
                    'data': result
                })
                
                if result.get('success') and room_id:
                    await manager.broadcast_to_room(
                        room_id,
                        {
                            'type': 'game_finished',
                            'winners': [user_id],
                            'prize': result.get('win_amount', 0)
                        }
                    )
            
            elif msg_type == 'ping':
                await websocket.send_json({'type': 'pong', 'timestamp': time.time()})
            
            elif msg_type == 'get_state':
                if game_id and game_id in game_manager.games:
                    game = game_manager.games[game_id]
                    await websocket.send_json({
                        'type': 'game_state',
                        'data': game.get_state(user_id)
                    })
    
    except asyncio.TimeoutError:
        logger.info(f"WebSocket timeout for user {user_id}")
        manager.disconnect(websocket, user_id)
    except WebSocketDisconnect:
        manager.disconnect(websocket, user_id)
        logger.info(f"User {user_id} disconnected from room {room_id}")
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        manager.disconnect(websocket, user_id)

# ============= SERVER STARTUP =============

@app.on_event("startup")
async def startup_event():
    """Initialize all rooms and recover state on startup"""
    logger.info("Starting Joy Bingo Server v2.0...")
    
    # Initialize database
    if db:
        await db.init_pool()
    
    # Create all game rooms
    rooms_config = [
        ("classic", "🎲 Classic Bingo", GameMode.CLASSIC, 10, 2, 400, 2.0, 20, "Traditional bingo - mark all numbers to win!"),
        ("blackout", "⬛ Blackout", GameMode.BLACKOUT, 20, 2, 200, 2.0, 20, "Fill your entire card to win!"),
        ("x_pattern", "❌ X Pattern", GameMode.X_PATTERN, 15, 2, 300, 2.0, 20, "Form an X shape on your card to win!"),
        ("four_corners", "📦 Four Corners", GameMode.FOUR_CORNERS, 12, 2, 350, 2.0, 20, "Get all four corners to win!"),
        ("line", "📏 Line Bingo", GameMode.LINE, 10, 2, 400, 2.0, 20, "Complete any line (row, column, or diagonal) to win!"),
    ]
    
    for room_id, name, mode, price, min_players, max_players, interval, selection_time, desc in rooms_config:
        game_manager.create_room(
            room_id, name,
            mode=mode,
            card_price=price,
            min_players=min_players,
            max_players=max_players,
            call_interval=interval,
            selection_time=selection_time,
            description=desc
        )
    
    logger.info(f"✅ Created {len(rooms_config)} game rooms")
    
    # Start background tasks
    asyncio.create_task(game_manager.cleanup_old_games())
    
    # Recover game state from database if available
    if db:
        await game_manager.recover_state()
    
    logger.info("✅ Server started successfully")

@app.on_event("shutdown")
async def shutdown_event():
    """Cleanup on shutdown"""
    logger.info("Shutting down server...")
    if db and db.pool:
        await db.pool.close()
        logger.info("Database connection closed")

# ============= STATIC FILES =============

try:
    from fastapi.staticfiles import StaticFiles
    app.mount("/webapp", StaticFiles(directory="webapp"), name="webapp")
    logger.info("✅ Mounted webapp directory")
except Exception as e:
    logger.warning(f"⚠️ webapp directory not found: {e}")

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    logger.info(f"Starting server on port {port}")
    uvicorn.run(app, host="0.0.0.0", port=port)
