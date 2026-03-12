from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse
import asyncio
import json
import logging
from typing import Dict, Set
from datetime import datetime
import jwt
from pydantic import BaseModel

from game_engine import game_manager, GameMode
from database import db
from config import *

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Joy Bingo API")

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve static files
app.mount("/static", StaticFiles(directory="static"), name="static")
app.mount("/webapp", StaticFiles(directory="webapp"), name="webapp")

# WebSocket connections
class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[str, Set[WebSocket]] = {}  # room_id -> set of websockets
        self.user_connections: Dict[str, WebSocket] = {}  # user_id -> websocket
        self.user_rooms: Dict[str, str] = {}  # user_id -> room_id
    
    async def connect(self, websocket: WebSocket, user_id: str, room_id: str):
        await websocket.accept()
        
        if room_id not in self.active_connections:
            self.active_connections[room_id] = set()
        
        self.active_connections[room_id].add(websocket)
        self.user_connections[user_id] = websocket
        self.user_rooms[user_id] = room_id
        
        logger.info(f"User {user_id} connected to room {room_id}")
    
    def disconnect(self, websocket: WebSocket, user_id: str):
        room_id = self.user_rooms.get(user_id)
        
        if room_id and room_id in self.active_connections:
            self.active_connections[room_id].discard(websocket)
        
        if user_id in self.user_connections:
            del self.user_connections[user_id]
        
        if user_id in self.user_rooms:
            del self.user_rooms[user_id]
    
    async def broadcast_to_room(self, room_id: str, message: dict, exclude_user: str = None):
        """Broadcast message to all users in a room"""
        if room_id not in self.active_connections:
            return
        
        disconnected = set()
        
        for connection in self.active_connections[room_id]:
            try:
                # Skip excluded user
                user_id = self.get_user_by_connection(connection)
                if exclude_user and user_id == exclude_user:
                    continue
                
                await connection.send_json(message)
            except:
                disconnected.add(connection)
        
        # Cleanup disconnected
        for conn in disconnected:
            self.active_connections[room_id].discard(conn)
    
    async def send_to_user(self, user_id: str, message: dict):
        """Send message to specific user"""
        if user_id in self.user_connections:
            try:
                await self.user_connections[user_id].send_json(message)
            except:
                pass
    
    def get_user_by_connection(self, websocket: WebSocket) -> str:
        """Get user ID from connection"""
        for uid, conn in self.user_connections.items():
            if conn == websocket:
                return uid
        return None

manager = ConnectionManager()

# API Models
class JoinGameRequest(BaseModel):
    user_id: str
    username: str
    room_id: str
    card_number: str

class MarkNumberRequest(BaseModel):
    user_id: str
    number: int

class BingoCallRequest(BaseModel):
    user_id: str

@app.get("/")
async def root():
    return HTMLResponse(content=open("webapp/lobby.html").read(), status_code=200)

@app.get("/api/rooms")
async def get_rooms():
    """Get all active rooms"""
    rooms = []
    for room_id, room in game_manager.rooms.items():
        rooms.append({
            'room_id': room_id,
            'name': room.name,
            'players': len(room.current_game.players) if room.current_game else 0,
            'status': room.current_game.status.value if room.current_game else 'idle',
            'prize_pool': room.current_game.prize_pool if room.current_game else 0,
            'card_price': room.card_price
        })
    return JSONResponse(rooms)

@app.get("/api/game/{game_id}")
async def get_game_state(game_id: str, user_id: str = None):
    """Get game state"""
    game = game_manager.games.get(game_id)
    if not game:
        raise HTTPException(status_code=404, detail="Game not found")
    
    return JSONResponse(game.get_state(user_id))

@app.post("/api/game/join")
async def join_game(request: JoinGameRequest):
    """Join a game"""
    success, msg, game = await game_manager.join_game(
        request.user_id,
        request.username,
        request.room_id,
        request.card_number
    )
    
    if success and game:
        # Notify room
        await manager.broadcast_to_room(
            request.room_id,
            {
                'type': 'player_joined',
                'user_id': request.user_id,
                'username': request.username,
                'player_count': len(game.players)
            }
        )
    
    return JSONResponse({
        'success': success,
        'message': msg,
        'game_state': game.get_state(request.user_id) if game else None
    })

@app.post("/api/game/mark")
async def mark_number(request: MarkNumberRequest):
    """Mark a number"""
    result = await game_manager.player_action(
        request.user_id,
        'mark',
        {'number': request.number}
    )
    
    if result['success']:
        # Notify others in room
        room_id = manager.user_rooms.get(request.user_id)
        if room_id:
            await manager.broadcast_to_room(
                room_id,
                {
                    'type': 'number_marked',
                    'user_id': request.user_id,
                    'number': request.number,
                    'game_state': result.get('state')
                },
                exclude_user=request.user_id
            )
        
        # If bingo achieved, notify everyone
        if result.get('bingo'):
            await manager.broadcast_to_room(
                room_id,
                {
                    'type': 'bingo_achieved',
                    'user_id': request.user_id,
                    'message': f"Player achieved BINGO!"
                }
            )
    
    return JSONResponse(result)

@app.post("/api/game/bingo")
async def call_bingo(request: BingoCallRequest):
    """Call bingo"""
    result = await game_manager.player_action(
        request.user_id,
        'bingo',
        {}
    )
    
    if result['success']:
        # Notify everyone
        room_id = manager.user_rooms.get(request.user_id)
        if room_id:
            await manager.broadcast_to_room(
                room_id,
                {
                    'type': 'game_finished',
                    'winners': [request.user_id],
                    'prize': result['win_amount'],
                    'game_state': result.get('state')
                }
            )
    
    return JSONResponse(result)

@app.get("/api/leaderboard")
async def leaderboard(days: int = 7):
    """Get leaderboard"""
    leaderboard = await db.get_leaderboard(days)
    return JSONResponse(leaderboard)

@app.websocket("/ws/{room_id}/{user_id}")
async def websocket_endpoint(websocket: WebSocket, room_id: str, user_id: str):
    """WebSocket connection for real-time updates"""
    await manager.connect(websocket, user_id, room_id)
    
    try:
        # Send initial state
        game = game_manager.games.get(game_manager.user_game.get(user_id))
        if game:
            await websocket.send_json({
                'type': 'game_state',
                'data': game.get_state(user_id)
            })
        
        # Listen for messages
        while True:
            data = await websocket.receive_text()
            message = json.loads(data)
            
            # Process client messages
            if message['type'] == 'mark':
                result = await game_manager.player_action(
                    user_id,
                    'mark',
                    {'number': message['number']}
                )
                await websocket.send_json({
                    'type': 'mark_result',
                    'data': result
                })
            
            elif message['type'] == 'bingo':
                result = await game_manager.player_action(
                    user_id,
                    'bingo',
                    {}
                )
                await websocket.send_json({
                    'type': 'bingo_result',
                    'data': result
                })
            
            elif message['type'] == 'ping':
                await websocket.send_json({'type': 'pong'})
    
    except WebSocketDisconnect:
        manager.disconnect(websocket, user_id)
        logger.info(f"User {user_id} disconnected from room {room_id}")
    
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        manager.disconnect(websocket, user_id)

# Startup events
@app.on_event("startup")
async def startup_event():
    """Initialize on startup"""
    # Create default rooms
    game_manager.create_room(
        "classic",
        "🎲 Classic Bingo",
        mode=GameMode.CLASSIC,
        card_price=10,
        min_players=2,
        max_players=400
    )
    
    game_manager.create_room(
        "high_roller",
        "💎 High Roller",
        mode=GameMode.CLASSIC,
        card_price=100,
        min_players=2,
        max_players=100
    )
    
    game_manager.create_room(
        "blackout",
        "⬛ Blackout",
        mode=GameMode.BLACKOUT,
        card_price=20,
        min_players=2,
        max_players=200
    )
    
    # Start background tasks
    asyncio.create_task(game_manager.cleanup_old_games())
    
    logger.info("Server started successfully")

@app.on_event("shutdown")
async def shutdown_event():
    """Cleanup on shutdown"""
    logger.info("Server shutting down")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)