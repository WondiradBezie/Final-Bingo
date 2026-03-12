# free_deploy.py
import os
import json
import asyncio
import logging
from datetime import datetime
from typing import Dict, Set, Optional
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

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

# Health check endpoint
@app.get("/")
async def root():
    return {
        "status": "online",
        "service": "Joy Bingo",
        "mode": "free-hosting",
        "timestamp": datetime.now().isoformat()
    }

@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat()
    }

@app.get("/ping")
async def ping():
    return {"pong": True}

# Simple game rooms
rooms_data = {
    "classic": {
        "id": "classic",
        "name": "Classic Bingo",
        "players": 0,
        "status": "waiting",
        "prize_pool": 0
    },
    "blackout": {
        "id": "blackout",
        "name": "Blackout",
        "players": 0,
        "status": "waiting",
        "prize_pool": 0
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

# Simple WebSocket connection manager
class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[str, Set[WebSocket]] = {}
    
    async def connect(self, websocket: WebSocket, room_id: str, user_id: str):
        await websocket.accept()
        if room_id not in self.active_connections:
            self.active_connections[room_id] = set()
        self.active_connections[room_id].add(websocket)
        logger.info(f"User {user_id} connected to room {room_id}")
        
        # Update player count
        if room_id in rooms_data:
            rooms_data[room_id]["players"] = len(self.active_connections[room_id])
    
    def disconnect(self, websocket: WebSocket, room_id: str, user_id: str):
        if room_id in self.active_connections:
            self.active_connections[room_id].discard(websocket)
            if room_id in rooms_data:
                rooms_data[room_id]["players"] = len(self.active_connections[room_id])
        logger.info(f"User {user_id} disconnected from room {room_id}")
    
    async def broadcast(self, room_id: str, message: dict):
        if room_id in self.active_connections:
            disconnected = set()
            for connection in self.active_connections[room_id]:
                try:
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
            "room_data": rooms_data.get(room_id, {})
        })
        
        while True:
            # Wait for messages
            data = await websocket.receive_text()
            try:
                message = json.loads(data)
                
                # Echo back for now (you can add game logic later)
                await websocket.send_json({
                    "type": "ack",
                    "received": message,
                    "timestamp": datetime.now().isoformat()
                })
                
                # Broadcast to room
                await manager.broadcast(room_id, {
                    "type": "user_action",
                    "user_id": user_id,
                    "data": message
                })
                
            except json.JSONDecodeError:
                await websocket.send_json({
                    "type": "error",
                    "message": "Invalid JSON"
                })
                
    except WebSocketDisconnect:
        manager.disconnect(websocket, room_id, user_id)
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        manager.disconnect(websocket, room_id, user_id)

# Simple webhook endpoint for Telegram (optional)
@app.post("/api/webhook")
async def telegram_webhook(request: Request):
    """Telegram bot webhook"""
    try:
        data = await request.json()
        logger.info(f"Received webhook: {data}")
        return {"ok": True}
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return {"ok": False, "error": str(e)}

# Serve static files if you have a webapp directory
try:
    app.mount("/webapp", StaticFiles(directory="webapp"), name="webapp")
    logger.info("Mounted webapp directory")
except:
    logger.warning("webapp directory not found, skipping")

# For local testing
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
