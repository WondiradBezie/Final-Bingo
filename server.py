from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse
import asyncio
import json
import logging
from typing import Dict, Set, Optional, List
from datetime import datetime
import random
from pydantic import BaseModel

from game_engine import GameManager, GameMode, BingoGame, PatternBingoGame, ProgressiveBingoGame, TeamBingoGame, SpeedBingoGame, TournamentBingoGame, JackpotBingoGame
from database import db
from config import *

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Joy Bingo API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory="static"), name="static")
app.mount("/webapp", StaticFiles(directory="webapp"), name="webapp")

class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[str, Set[WebSocket]] = {}
        self.user_connections: Dict[str, WebSocket] = {}
        self.user_rooms: Dict[str, str] = {}
    
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

game_manager = GameManager()

@app.get("/")
async def root():
    return HTMLResponse(content=open("webapp/lobby.html").read(), status_code=200)

@app.get("/api/rooms")
async def get_rooms():
    rooms = []
    for room_id, room in game_manager.rooms.items():
        rooms.append({
            'room_id': room_id,
            'name': room.name,
            'description': getattr(room, 'description', ''),
            'mode': room.mode.value if hasattr(room, 'mode') else 'classic',
            'players': len(room.current_game.players) if room.current_game else 0,
            'max_players': room.max_players,
            'status': room.current_game.status.value if room.current_game else 'waiting',
            'prize_pool': room.current_game.prize_pool if room.current_game else 0,
            'card_price': room.card_price,
            'min_players': room.min_players
        })
    return JSONResponse(rooms)

@app.get("/api/game/{game_id}")
async def get_game_state(game_id: str, user_id: str = None):
    game = game_manager.games.get(game_id)
    if not game:
        raise HTTPException(status_code=404, detail="Game not found")
    return JSONResponse(game.get_state(user_id))

@app.post("/api/game/join")
async def join_game(request: JoinGameRequest):
    success, msg, game = await game_manager.join_game(
        request.user_id,
        request.username,
        request.room_id,
        request.card_number
    )
    if success and game:
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
    result = await game_manager.player_action(
        request.user_id,
        'mark',
        {'number': request.number}
    )
    if result['success']:
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
    result = await game_manager.player_action(
        request.user_id,
        'bingo',
        {}
    )
    if result['success']:
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
    leaderboard = await db.get_leaderboard(days)
    return JSONResponse(leaderboard)

@app.websocket("/ws/{room_id}/{user_id}")
async def websocket_endpoint(websocket: WebSocket, room_id: str, user_id: str):
    await manager.connect(websocket, user_id, room_id)
    try:
        game = game_manager.games.get(game_manager.user_game.get(user_id))
        if game:
            await websocket.send_json({
                'type': 'game_state',
                'data': game.get_state(user_id)
            })
        while True:
            data = await websocket.receive_text()
            message = json.loads(data)
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

@app.on_event("startup")
async def startup_event():
    game_manager.create_room(
        "classic",
        "🎲 Classic Bingo",
        mode=GameMode.CLASSIC,
        card_price=10,
        min_players=2,
        max_players=400,
        description="Traditional bingo - mark all numbers to win!"
    )
    game_manager.create_room(
        "blackout",
        "⬛ Blackout",
        mode=GameMode.BLACKOUT,
        card_price=20,
        min_players=2,
        max_players=200,
        description="Fill your entire card to win!"
    )
    game_manager.create_room(
        "x_pattern",
        "❌ X Pattern",
        mode=GameMode.X_PATTERN,
        card_price=15,
        min_players=2,
        max_players=300,
        description="Form an X shape on your card to win!"
    )
    game_manager.create_room(
        "four_corners",
        "📦 Four Corners",
        mode=GameMode.FOUR_CORNERS,
        card_price=12,
        min_players=2,
        max_players=350,
        description="Get all four corners to win!"
    )
    game_manager.create_room(
        "line",
        "📏 Line Bingo",
        mode=GameMode.LINE,
        card_price=10,
        min_players=2,
        max_players=400,
        description="Complete any line (row, column, or diagonal) to win!"
    )
    game_manager.create_room(
        "two_lines",
        "📐 Double Line",
        mode=GameMode.TWO_LINES,
        card_price=18,
        min_players=2,
        max_players=300,
        description="Complete any two lines to win!"
    )
    game_manager.create_room(
        "l_shape",
        "🅻 L Shape",
        mode=GameMode.L_SHAPE,
        card_price=15,
        min_players=2,
        max_players=300,
        description="Form an L shape on your card to win!"
    )
    game_manager.create_room(
        "diamond",
        "💎 Diamond",
        mode=GameMode.DIAMOND,
        card_price=20,
        min_players=2,
        max_players=250,
        description="Form a diamond pattern to win!"
    )
    game_manager.create_room(
        "heart",
        "❤️ Heart Bingo",
        mode=GameMode.HEART,
        card_price=25,
        min_players=2,
        max_players=200,
        description="Special Valentine's pattern - form a heart to win!"
    )
    game_manager.create_room(
        "star",
        "⭐ Star Bingo",
        mode=GameMode.STAR,
        card_price=30,
        min_players=2,
        max_players=150,
        description="Form a star pattern to win the jackpot!"
    )
    game_manager.create_room(
        "checkerboard",
        "♟️ Checkerboard",
        mode=GameMode.CHECKERBOARD,
        card_price=22,
        min_players=2,
        max_players=250,
        description="Mark all checkerboard squares to win!"
    )
    game_manager.create_room(
        "speed",
        "⚡ Speed Bingo",
        mode=GameMode.CLASSIC,
        card_price=15,
        min_players=2,
        max_players=300,
        call_interval=1.0,
        description="Fast-paced bingo with 1-second calls!"
    )
    game_manager.create_room(
        "teams",
        "👥 Team Battle",
        mode=GameMode.CLASSIC,
        card_price=20,
        min_players=6,
        max_players=30,
        team_mode=True,
        description="Red vs Blue vs Green - which team wins?"
    )
    game_manager.create_room(
        "tournament",
        "🏆 Tournament",
        mode=GameMode.CLASSIC,
        card_price=50,
        min_players=8,
        max_players=64,
        tournament_mode=True,
        description="Multi-round tournament - survive to win big!"
    )
    game_manager.create_room(
        "jackpot",
        "💰 Progressive Jackpot",
        mode=GameMode.CLASSIC,
        card_price=100,
        min_players=4,
        max_players=100,
        progressive=True,
        description="Win the growing jackpot at random numbers!"
    )
    game_manager.create_room(
        "high_roller",
        "👑 High Roller",
        mode=GameMode.CLASSIC,
        card_price=500,
        min_players=2,
        max_players=50,
        description="For serious players - huge stakes, huge wins!"
    )
    game_manager.create_room(
        "practice",
        "🎯 Practice Room",
        mode=GameMode.CLASSIC,
        card_price=0,
        min_players=1,
        max_players=100,
        description="Free practice games - no real money"
    )
    asyncio.create_task(game_manager.cleanup_old_games())
    logger.info("Server started successfully with 18 game modes")

@app.on_event("shutdown")
async def shutdown_event():
    logger.info("Server shutting down")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
