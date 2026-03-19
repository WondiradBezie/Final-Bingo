from enum import Enum
import random
from typing import List, Set, Dict
import numpy as np

class GameMode(Enum):
    CLASSIC = "classic"          # Traditional bingo - all numbers
    BLACKOUT = "blackout"        # Must mark all numbers on card
    X_PATTERN = "x_pattern"      # X shape across card
    FOUR_CORNERS = "corners"     # Only the four corners
    LINE = "line"                # Any single line (row/column/diag)
    TWO_LINES = "two_lines"      # Any two lines
    FULL_HOUSE = "full_house"    # All numbers (same as blackout but different)
    L_SHAPE = "l_shape"          # L shape on card
    ZIGZAG = "zigzag"            # Zigzag pattern
    DIAMOND = "diamond"          # Diamond shape
    POSTAGE_STAMP = "stamp"      # 2x2 square in corner
    PYRAMID = "pyramid"          # Pyramid shape
    ARROW = "arrow"              # Arrow pattern
    HEART = "heart"              # Heart shape (for special events)
    STAR = "star"                # Star pattern
    CHECKERBOARD = "checkerboard" # Alternate squares

class PatternBingoGame(BingoGame):
    """Extended Bingo Game with multiple patterns"""
    
    def __init__(self, game_id: str, room: BingoRoom):
        super().__init__(game_id, room)
        self.pattern = self.get_pattern(room.mode)
        self.pattern_name = room.mode.value
    
    def get_pattern(self, mode: GameMode) -> List[int]:
        """Get pattern indices for the given mode"""
        # Indices for 5x5 grid (0-24)
        patterns = {
            GameMode.CLASSIC: list(range(25)),  # All numbers
            GameMode.BLACKOUT: list(range(25)),  # All numbers
            GameMode.X_PATTERN: [0, 4, 6, 8, 12, 16, 18, 20, 24],  # X shape
            GameMode.FOUR_CORNERS: [0, 4, 20, 24],  # Four corners
            GameMode.LINE: self.get_all_lines(),  # Any line
            GameMode.TWO_LINES: self.get_all_lines(),  # Any two lines
            GameMode.L_SHAPE: [0,1,2,3,4,9,14,19,24],  # L shape
            GameMode.ZIGZAG: [0,5,6,11,12,17,18,23,24],  # Zigzag
            GameMode.DIAMOND: [6,7,8,11,13,16,17,18],  # Diamond
            GameMode.POSTAGE_STAMP: [0,1,5,6],  # Top-left 2x2
            GameMode.PYRAMID: [0,1,2,3,4,5,9,10,14,15,20],  # Pyramid
            GameMode.ARROW: [2,7,10,11,12,13,14,17,22],  # Arrow
            GameMode.HEART: self.get_heart_pattern(),  # Heart shape
            GameMode.STAR: self.get_star_pattern(),  # Star pattern
            GameMode.CHECKERBOARD: self.get_checkerboard()  # Checkerboard
        }
        return patterns.get(mode, list(range(25)))
    
    def get_all_lines(self) -> List[List[int]]:
        """Get all possible lines (rows, columns, diagonals)"""
        lines = []
        # Rows
        for i in range(5):
            lines.append([i*5 + j for j in range(5)])
        # Columns
        for i in range(5):
            lines.append([i + j*5 for j in range(5)])
        # Diagonals
        lines.append([i*6 for i in range(5)])  # Main diagonal
        lines.append([i*4 + 4 for i in range(5)])  # Other diagonal
        return lines
    
    def get_heart_pattern(self) -> List[int]:
        """Heart shape pattern"""
        return [1,2,3,5,7,9,10,11,12,13,14,15,19,20,24]
    
    def get_star_pattern(self) -> List[int]:
        """Star pattern"""
        return [0,2,4,6,8,12,16,18,20,22,24]
    
    def get_checkerboard(self) -> List[int]:
        """Checkerboard pattern (alternating squares)"""
        return [i for i in range(25) if (i // 5 + i % 5) % 2 == 0]
    
    async def check_player_bingo(self, user_id: str) -> bool:
        """Check bingo based on game mode"""
        player = self.players[user_id]
        
        if self.mode == GameMode.CLASSIC:
            # All numbers
            return len(player.marked) == 25
        
        elif self.mode == GameMode.BLACKOUT:
            # All numbers
            return len(player.marked) == 25
        
        elif self.mode == GameMode.X_PATTERN:
            # X shape
            indices = self.pattern
            return all(player.card[i] in player.marked for i in indices)
        
        elif self.mode == GameMode.FOUR_CORNERS:
            # Four corners only
            corners = [0, 4, 20, 24]
            return all(player.card[i] in player.marked for i in corners)
        
        elif self.mode == GameMode.LINE:
            # Any single line
            lines = self.get_all_lines()
            for line in lines:
                if all(player.card[i] in player.marked for i in line):
                    return True
            return False
        
        elif self.mode == GameMode.TWO_LINES:
            # Any two lines
            lines = self.get_all_lines()
            completed_lines = 0
            for line in lines:
                if all(player.card[i] in player.marked for i in line):
                    completed_lines += 1
            return completed_lines >= 2
        
        elif self.mode == GameMode.L_SHAPE:
            # L shape
            l_shape = [0,1,2,3,4,9,14,19,24]
            return all(player.card[i] in player.marked for i in l_shape)
        
        elif self.mode == GameMode.ZIGZAG:
            # Zigzag pattern
            zigzag = [0,5,6,11,12,17,18,23,24]
            return all(player.card[i] in player.marked for i in zigzag)
        
        elif self.mode == GameMode.DIAMOND:
            # Diamond shape
            diamond = [6,7,8,11,13,16,17,18]
            return all(player.card[i] in player.marked for i in diamond)
        
        elif self.mode == GameMode.POSTAGE_STAMP:
            # 2x2 square in corner
            stamp = [0,1,5,6]  # Top-left corner
            return all(player.card[i] in player.marked for i in stamp)
        
        elif self.mode == GameMode.PYRAMID:
            # Pyramid shape
            pyramid = [0,1,2,3,4,5,9,10,14,15,20]
            return all(player.card[i] in player.marked for i in pyramid)
        
        elif self.mode == GameMode.ARROW:
            # Arrow shape
            arrow = [2,7,10,11,12,13,14,17,22]
            return all(player.card[i] in player.marked for i in arrow)
        
        elif self.mode == GameMode.HEART:
            # Heart shape
            heart = self.get_heart_pattern()
            return all(player.card[i] in player.marked for i in heart)
        
        elif self.mode == GameMode.STAR:
            # Star pattern
            star = self.get_star_pattern()
            return all(player.card[i] in player.marked for i in star)
        
        elif self.mode == GameMode.CHECKERBOARD:
            # Checkerboard pattern
            checker = self.get_checkerboard()
            return all(player.card[i] in player.marked for i in checker)
        
        return False

class ProgressiveBingoGame(BingoGame):
    """Progressive jackpot bingo"""
    
    def __init__(self, game_id: str, room: BingoRoom):
        super().__init__(game_id, room)
        self.progressive_jackpot = 0
        self.jackpot_trigger = random.randint(50, 100)  # Random trigger number
    
    async def call_number(self) -> Optional[int]:
        """Call number with progressive jackpot chance"""
        number = await super().call_number()
        
        if number and len(self.called_numbers) == self.jackpot_trigger:
            # Progressive jackpot! Any player who has this number wins big
            await self.trigger_progressive_jackpot(number)
        
        return number
    
    async def trigger_progressive_jackpot(self, number: int):
        """Trigger progressive jackpot"""
        winners = []
        for user_id, player in self.players.items():
            if number in player.card:
                winners.append(user_id)
        
        if winners:
            jackpot_share = self.progressive_jackpot / len(winners)
            for winner in winners:
                self.players[winner].win_amount += jackpot_share
                self.winners.append(winner)
            
            # Reset jackpot after win
            self.progressive_jackpot = 0

class TeamBingoGame(BingoGame):
    """Team-based bingo"""
    
    def __init__(self, game_id: str, room: BingoRoom):
        super().__init__(game_id, room)
        self.teams: Dict[str, List[str]] = {'red': [], 'blue': [], 'green': []}
        self.team_scores: Dict[str, int] = {'red': 0, 'blue': 0, 'green': 0}
    
    async def add_player(self, user_id: str, username: str, card_number: str) -> tuple[bool, str]:
        """Add player to team"""
        success, msg = await super().add_player(user_id, username, card_number)
        
        if success:
            # Assign to team with fewest players
            team = min(self.teams.items(), key=lambda x: len(x[1]))[0]
            self.teams[team].append(user_id)
            self.players[user_id].team = team
        
        return success, msg
    
    async def check_winners(self) -> List[str]:
        """Check winners - team with most bingos wins"""
        new_winners = await super().check_winners()
        
        # Update team scores
        for winner in new_winners:
            team = self.players[winner].team
            self.team_scores[team] += 1
        
        # Check if any team has reached winning threshold
        winning_team = max(self.team_scores.items(), key=lambda x: x[1])
        if winning_team[1] >= 3:  # Team needs 3 bingos to win
            self.status = GameStatus.FINISHED
            self.winners = self.teams[winning_team[0]]
        
        return new_winners

class SpeedBingoGame(BingoGame):
    """Fast-paced bingo with shorter intervals"""
    
    def __init__(self, game_id: str, room: BingoRoom):
        super().__init__(game_id, room)
        self.speed_multiplier = 1.0
        self.consecutive_calls = 0
    
    async def call_number(self) -> Optional[int]:
        """Call number with increasing speed"""
        number = await super().call_number()
        
        if number:
            self.consecutive_calls += 1
            
            # Increase speed every 10 calls
            if self.consecutive_calls % 10 == 0:
                self.speed_multiplier *= 0.9  # 10% faster
                self.room.call_interval = max(0.5, 2.0 * self.speed_multiplier)
        
        return number

class TournamentBingoGame(BingoGame):
    """Multi-round tournament bingo"""
    
    def __init__(self, game_id: str, room: BingoRoom):
        super().__init__(game_id, room)
        self.round = 1
        max_rounds = 3
        self.qualified_players: Set[str] = set()
        self.round_winners: Dict[int, List[str]] = {}
    
    async def check_winners(self) -> List[str]:
        """Check winners for current round"""
        winners = await super().check_winners()
        
        if winners:
            self.round_winners[self.round] = winners
            
            # Qualified for next round
            self.qualified_players.update(winners)
            
            # Start next round if more rounds remain
            if self.round < self.max_rounds:
                self.round += 1
                await self.start_next_round()
            else:
                # Tournament finished
                self.status = GameStatus.FINISHED
                self.winners = list(self.qualified_players)
        
        return winners
    
    async def start_next_round(self):
        """Start next tournament round"""
        # Reset game state for next round
        self.called_numbers = []
        self.winners = []
        
        # Keep only qualified players
        self.players = {
            uid: player for uid, player in self.players.items()
            if uid in self.qualified_players
        }
        
        # Start new round
        self.status = GameStatus.ACTIVE
        self.started_at = datetime.utcnow()

class JackpotBingoGame(BingoGame):
    """Multi-level jackpot bingo"""
    
    def __init__(self, game_id: str, room: BingoRoom):
        super().__init__(game_id, room)
        self.jackpot_levels = {
            'mini': {'trigger': 20, 'amount': 100, 'winners': []},
            'minor': {'trigger': 40, 'amount': 500, 'winners': []},
            'major': {'trigger': 60, 'amount': 1000, 'winners': []},
            'grand': {'trigger': 75, 'amount': 5000, 'winners': []}
        }
    
    async def call_number(self) -> Optional[int]:
        """Call number with jackpot checks"""
        number = await super().call_number()
        
        if number:
            # Check each jackpot level
            for level, config in self.jackpot_levels.items():
                if len(self.called_numbers) == config['trigger']:
                    await self.trigger_jackpot(level, number)
        
        return number
    
    async def trigger_jackpot(self, level: str, number: int):
        """Trigger specific jackpot level"""
        winners = []
        for user_id, player in self.players.items():
            if number in player.card:
                winners.append(user_id)
        
        if winners:
            config = self.jackpot_levels[level]
            share = config['amount'] / len(winners)
            
            for winner in winners:
                self.players[winner].win_amount += share
                config['winners'].append(winner)
            
            # Add to game winners
            self.winners.extend(winners)
            
            # Log jackpot win
            logger.info(f"{level.capitalize()} jackpot won by {len(winners)} players: {share} each")

# ==== FIXES ADDED ====
import time

selection_start_time = None
SELECTION_DURATION = 20
disqualified_players = set()

def start_selection_phase():
    global selection_start_time
    selection_start_time = time.time()

def selection_open():
    if selection_start_time is None:
        return False
    return (time.time() - selection_start_time) < SELECTION_DURATION

def check_bingo(player_id, is_valid):
    if player_id in disqualified_players:
        return "blocked"
    if is_valid:
        return "win"
    else:
        disqualified_players.add(player_id)
        return "disqualified"
