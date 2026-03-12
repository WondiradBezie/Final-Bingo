import asyncio
import random
import json
from datetime import datetime
from typing import Dict, List, Optional, Set
from dataclasses import dataclass, field
from enum import Enum
import logging
from collections import defaultdict

logger = logging.getLogger(__name__)

class GameStatus(Enum):
    WAITING = "waiting"
    SELECTING = "selecting"
    ACTIVE = "active"
    FINISHED = "finished"
    CANCELLED = "cancelled"

class GameMode(Enum):
    CLASSIC = "classic"        # First to get bingo
    BLACKOUT = "blackout"      # Must mark all numbers
    X_PATTERN = "x_pattern"    # X shape
    FOUR_CORNERS = "corners"   # Four corners only
    LINE = "line"              # Any line

@dataclass
class Player:
    user_id: str
    username: str
    card: Dict[str, List[int]]
    marked: Set[int] = field(default_factory=set)
    bingo_called: bool = False
    bingo_time: Optional[float] = None
    win_amount: float = 0.0

@dataclass
class BingoRoom:
    room_id: str
    name: str
    mode: GameMode = GameMode.CLASSIC
    card_price: float = 10.0
    prize_percentage: int = 80
    min_players: int = 2
    max_players: int = 400
    call_interval: float = 2.0
    selection_time: int = 20
    
    # Current game
    current_game: Optional['BingoGame'] = None
    games_played: int = 0
    total_players: int = 0
    total_bet: float = 0.0
    total_paid: float = 0.0

class BingoGame:
    def __init__(self, game_id: str, room: BingoRoom):
        self.game_id = game_id
        self.room = room
        self.status = GameStatus.WAITING
        self.mode = room.mode
        
        # Players
        self.players: Dict[str, Player] = {}
        self.player_count = 0
        
        # Game state
        self.called_numbers: List[int] = []
        self.winners: List[str] = []
        self.winner_data: List[Dict] = []
        
        # Financial
        self.total_bet = 0.0
        self.prize_pool = 0.0
        self.commission = 0.0
        
        # Timing
        self.created_at = datetime.utcnow()
        self.started_at: Optional[datetime] = None
        self.finished_at: Optional[datetime] = None
        self.last_call: Optional[float] = None
        
        # Locks
        self._lock = asyncio.Lock()
        
        # Cards database (pre-generated)
        self.cards_db = self.load_cards()
    
    def load_cards(self) -> Dict[str, List[int]]:
        """Load pre-generated bingo cards"""
        try:
            with open("data/cards.json", 'r') as f:
                return json.load(f)
        except:
            # Generate 400 cards if not exist
            cards = {}
            for i in range(1, 401):
                cards[str(i)] = self.generate_card()
            return cards
    
    def generate_card(self) -> List[int]:
        """Generate a random bingo card"""
        card = []
        # B: 1-15
        card.extend(random.sample(range(1, 16), 5))
        # I: 16-30
        card.extend(random.sample(range(16, 31), 5))
        # N: 31-45
        card.extend(random.sample(range(31, 46), 5))
        # G: 46-60
        card.extend(random.sample(range(46, 61), 5))
        # O: 61-75
        card.extend(random.sample(range(61, 76), 5))
        return card
    
    async def add_player(self, user_id: str, username: str, card_number: str) -> tuple[bool, str]:
        """Add player to game"""
        async with self._lock:
            if self.status != GameStatus.WAITING:
                return False, "Game already started"
            
            if len(self.players) >= self.room.max_players:
                return False, "Game is full"
            
            # Check if card exists
            if card_number not in self.cards_db:
                return False, "Invalid card number"
            
            # Check if card already taken
            for player in self.players.values():
                if player.card == self.cards_db[card_number]:
                    return False, "Card already taken"
            
            # Create player
            self.players[user_id] = Player(
                user_id=user_id,
                username=username,
                card=self.cards_db[card_number]
            )
            
            # Update financials
            self.total_bet += self.room.card_price
            self.prize_pool = self.total_bet * self.room.prize_percentage / 100
            self.commission = self.total_bet - self.prize_pool
            
            return True, "Player added"
    
    async def start_game(self) -> bool:
        """Start the game"""
        async with self._lock:
            if len(self.players) < self.room.min_players:
                return False
            
            self.status = GameStatus.ACTIVE
            self.started_at = datetime.utcnow()
            self.last_call = asyncio.get_event_loop().time()
            
            return True
    
    async def call_number(self) -> Optional[int]:
        """Call next number"""
        async with self._lock:
            if self.status != GameStatus.ACTIVE:
                return None
            
            # Get available numbers
            available = [n for n in range(1, 76) if n not in self.called_numbers]
            
            if not available:
                # No more numbers - game ends in draw
                self.status = GameStatus.FINISHED
                self.finished_at = datetime.utcnow()
                return None
            
            # Pick random number
            number = random.choice(available)
            self.called_numbers.append(number)
            self.last_call = asyncio.get_event_loop().time()
            
            # Auto-check winners
            await self.check_winners()
            
            return number
    
    async def mark_number(self, user_id: str, number: int) -> tuple[bool, str, bool]:
        """Player marks a number"""
        async with self._lock:
            if user_id not in self.players:
                return False, "Player not in game", False
            
            player = self.players[user_id]
            
            # Validate
            if number not in self.called_numbers:
                return False, "Number not called yet", False
            
            if number in player.marked:
                return False, "Already marked", False
            
            if number not in player.card:
                return False, "Number not on your card", False
            
            # Mark number
            player.marked.add(number)
            
            # Check for bingo
            has_bingo = await self.check_player_bingo(user_id)
            
            return True, "Marked", has_bingo
    
    async def check_player_bingo(self, user_id: str) -> bool:
        """Check if player has bingo"""
        player = self.players[user_id]
        
        if self.mode == GameMode.CLASSIC:
            # Check all numbers
            return all(num in player.marked for num in player.card)
        
        elif self.mode == GameMode.LINE:
            # Check any line (rows, columns, diagonals)
            card = player.card
            marked = player.marked
            
            # Check rows
            for i in range(0, 25, 5):
                if all(card[i+j] in marked for j in range(5)):
                    return True
            
            # Check columns
            for i in range(5):
                if all(card[i+j*5] in marked for j in range(5)):
                    return True
            
            # Check diagonals
            if all(card[i*6] in marked for i in range(5)):
                return True
            if all(card[i*4+4] in marked for i in range(5)):
                return True
            
            return False
        
        elif self.mode == GameMode.FOUR_CORNERS:
            corners = [0, 4, 20, 24]
            return all(player.card[i] in player.marked for i in corners)
        
        return False
    
    async def check_winners(self) -> List[str]:
        """Check all players for bingo"""
        async with self._lock:
            new_winners = []
            
            for user_id, player in self.players.items():
                if not player.bingo_called:
                    if await self.check_player_bingo(user_id):
                        player.bingo_called = True
                        player.bingo_time = asyncio.get_event_loop().time()
                        new_winners.append(user_id)
            
            if new_winners:
                self.winners.extend(new_winners)
                
                # Calculate prizes
                if self.winners:
                    prize_per_winner = self.prize_pool / len(self.winners)
                    for winner_id in self.winners:
                        self.players[winner_id].win_amount = prize_per_winner
                    
                    self.status = GameStatus.FINISHED
                    self.finished_at = datetime.utcnow()
            
            return new_winners
    
    async def call_bingo(self, user_id: str) -> tuple[bool, float]:
        """Player calls bingo"""
        async with self._lock:
            if user_id not in self.players:
                return False, 0
            
            player = self.players[user_id]
            
            # Verify bingo
            if not await self.check_player_bingo(user_id):
                # False alarm - penalty?
                return False, 0
            
            if not player.bingo_called:
                player.bingo_called = True
                player.bingo_time = asyncio.get_event_loop().time()
                
                if user_id not in self.winners:
                    self.winners.append(user_id)
                    
                    # Recalculate prizes
                    prize_per_winner = self.prize_pool / len(self.winners)
                    for winner_id in self.winners:
                        self.players[winner_id].win_amount = prize_per_winner
                    
                    self.status = GameStatus.FINISHED
                    self.finished_at = datetime.utcnow()
            
            return True, player.win_amount
    
    def get_state(self, user_id: Optional[str] = None) -> dict:
        """Get game state for client"""
        state = {
            'game_id': self.game_id,
            'status': self.status.value,
            'mode': self.mode.value,
            'players': len(self.players),
            'max_players': self.room.max_players,
            'called_numbers': self.called_numbers[-20:],  # Last 20 numbers
            'recent_calls': self.called_numbers[-5:],  # Last 5 numbers
            'prize_pool': self.prize_pool,
            'card_price': self.room.card_price,
            'last_call': self.last_call,
            'winners': self.winners
        }
        
        # Add player-specific data
        if user_id and user_id in self.players:
            player = self.players[user_id]
            state['player'] = {
                'card': player.card,
                'marked': list(player.marked),
                'has_bingo': player.bingo_called,
                'win_amount': player.win_amount
            }
        
        return state

class GameManager:
    """Manages multiple rooms and games"""
    
    def __init__(self):
        self.rooms: Dict[str, BingoRoom] = {}
        self.games: Dict[str, BingoGame] = {}
        self.user_room: Dict[str, str] = {}  # user_id -> room_id
        self.user_game: Dict[str, str] = {}  # user_id -> game_id
        
        # Background tasks
        self.game_loop_task = None
        self.cleanup_task = None
        
        # Statistics
        self.stats = defaultdict(int)
    
    def create_room(self, room_id: str, name: str, **kwargs) -> BingoRoom:
        """Create a new bingo room"""
        room = BingoRoom(room_id=room_id, name=name, **kwargs)
        self.rooms[room_id] = room
        return room
    
    async def create_game(self, room_id: str) -> BingoGame:
        """Create new game in room"""
        if room_id not in self.rooms:
            raise ValueError(f"Room {room_id} not found")
        
        room = self.rooms[room_id]
        game_id = f"{room_id}_{len(self.games)}_{int(datetime.utcnow().timestamp())}"
        
        game = BingoGame(game_id, room)
        self.games[game_id] = game
        room.current_game = game
        room.games_played += 1
        
        return game
    
    async def join_game(self, user_id: str, username: str, 
                       room_id: str, card_number: str) -> tuple[bool, str, Optional[BingoGame]]:
        """User joins a game"""
        
        # Get or create game
        room = self.rooms.get(room_id)
        if not room:
            return False, "Room not found", None
        
        # Check if user already in a game
        if user_id in self.user_game:
            game_id = self.user_game[user_id]
            if game_id in self.games:
                game = self.games[game_id]
                if game.status in [GameStatus.WAITING, GameStatus.ACTIVE]:
                    return False, "Already in a game", game
        
        # Find available game in room
        game = room.current_game
        if not game or game.status != GameStatus.WAITING:
            # Create new game
            game = await self.create_game(room_id)
        
        # Add player
        success, msg = await game.add_player(user_id, username, card_number)
        
        if success:
            self.user_room[user_id] = room_id
            self.user_game[user_id] = game.game_id
            self.stats['total_joins'] += 1
            
            # Auto-start if enough players
            if len(game.players) >= room.min_players and game.status == GameStatus.WAITING:
                asyncio.create_task(self.start_game(game.game_id))
        
        return success, msg, game
    
    async def start_game(self, game_id: str):
        """Start a game"""
        game = self.games.get(game_id)
        if not game:
            return
        
        # Wait for selection time
        await asyncio.sleep(game.room.selection_time)
        
        if game.status == GameStatus.WAITING and len(game.players) >= game.room.min_players:
            await game.start_game()
            self.stats['games_started'] += 1
            
            # Start number calling loop
            asyncio.create_task(self.game_loop(game_id))
    
    async def game_loop(self, game_id: str):
        """Main game loop - calls numbers periodically"""
        game = self.games.get(game_id)
        if not game:
            return
        
        while game.status == GameStatus.ACTIVE:
            # Call number
            number = await game.call_number()
            
            if number:
                self.stats['numbers_called'] += 1
            
            # Wait for next call
            await asyncio.sleep(game.room.call_interval)
        
        # Game finished - process winners
        if game.status == GameStatus.FINISHED:
            await self.process_winners(game_id)
    
    async def process_winners(self, game_id: str):
        """Process winners and distribute prizes"""
        game = self.games.get(game_id)
        if not game:
            return
        
        # Winners already have win_amount set
        for winner_id in game.winners:
            player = game.players[winner_id]
            self.stats['total_winners'] += 1
            self.stats['total_paid'] += player.win_amount
            
            # Update room stats
            game.room.total_paid += player.win_amount
        
        game.room.total_bet += game.total_bet
        
        # Log game
        logger.info(f"Game {game_id} finished. Winners: {game.winners}, Prize: {game.prize_pool}")
    
    async def player_action(self, user_id: str, action: str, data: dict) -> dict:
        """Process player action"""
        result = {'success': False, 'message': 'Unknown action'}
        
        # Get user's current game
        game_id = self.user_game.get(user_id)
        if not game_id or game_id not in self.games:
            return {'success': False, 'message': 'Not in a game'}
        
        game = self.games[game_id]
        
        if action == 'mark':
            number = data.get('number')
            success, msg, bingo = await game.mark_number(user_id, number)
            result = {
                'success': success,
                'message': msg,
                'bingo': bingo,
                'state': game.get_state(user_id) if success else None
            }
        
        elif action == 'bingo':
            success, amount = await game.call_bingo(user_id)
            result = {
                'success': success,
                'message': 'Bingo called!' if success else 'Invalid bingo',
                'win_amount': amount,
                'state': game.get_state(user_id) if success else None
            }
        
        return result
    
    async def get_leaderboard(self) -> List[dict]:
        """Get global leaderboard"""
        # This would come from database in production
        return [
            {'username': 'Player1', 'wins': 10, 'winnings': 5000},
            {'username': 'Player2', 'wins': 8, 'winnings': 4000},
            {'username': 'Player3', 'wins': 6, 'winnings': 3000},
        ]
    
    async def cleanup_old_games(self):
        """Background task to clean up old games"""
        while True:
            await asyncio.sleep(3600)  # Run every hour
            
            current_time = asyncio.get_event_loop().time()
            to_remove = []
            
            for game_id, game in self.games.items():
                if game.status == GameStatus.FINISHED:
                    if game.finished_at:
                        age = (datetime.utcnow() - game.finished_at).total_seconds()
                        if age > 3600:  # 1 hour
                            to_remove.append(game_id)
            
            for game_id in to_remove:
                del self.games[game_id]
                self.stats['games_cleaned'] += 1
            
            logger.info(f"Cleaned up {len(to_remove)} old games")

# Global game manager
game_manager = GameManager()