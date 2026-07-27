"""Production game service for JOY BINGO.

This module is the canonical game implementation used by free_deploy.py.
It keeps authoritative game state on the server, uses the fixed 400-card
catalog, and delegates all real-money balance changes to DatabaseManager.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import secrets
import time
import random
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


@dataclass
class GameRoom:
    room_id: str
    name: str
    card_price: int = 10
    prize_percentage: int = 80
    min_players: int = 2
    max_players: int = 400
    call_interval: float = 2.0
    selection_time: int = 20
    mode: str = "line"
    description: str = ""
    current_game_id: Optional[str] = None


@dataclass
class GamePlayer:
    user_id: str
    username: str
    card_number: int
    card: List[int]
    marked: Set[int] = field(default_factory=set)
    joined_at: float = field(default_factory=time.time)
    bingo_called: bool = False
    is_winner: bool = False
    win_amount: float = 0.0
    bet_reserved: bool = False


class ProductionGame:
    def __init__(self, game_id: str, room: GameRoom, db):
        self.game_id = game_id
        self.room = room
        self.db = db
        self.status = "waiting"
        self.players: Dict[str, GamePlayer] = {}
        self.called_numbers: List[int] = []
        self.winners: List[str] = []
        self.prize_pool = 0.0
        self.total_bet = 0.0
        self.commission = 0.0
        self.created_at = utcnow()
        self.started_at: Optional[datetime] = None
        self.finished_at: Optional[datetime] = None
        self.server_seed = secrets.token_hex(32)
        self.game_hash = hashlib.sha256(self.server_seed.encode()).hexdigest()
        self._number_order = list(range(1, 76))
        random.Random(self.server_seed).shuffle(self._number_order)
        self._next_number_index = 0
        self._lock = asyncio.Lock()
        self._start_task: Optional[asyncio.Task] = None
        self._loop_task: Optional[asyncio.Task] = None
        self._settled = False

    @property
    def used_cards(self) -> Set[int]:
        return {p.card_number for p in self.players.values()}

    def _winning_patterns(self, player: GamePlayer) -> List[List[int]]:
        c = player.card
        patterns = []
        mode = self.room.mode
        if mode in {"line", "classic"}:
            patterns.extend([[r * 5 + col for col in range(5)] for r in range(5)])
            patterns.extend([[r * 5 + col for r in range(5)] for col in range(5)])
            patterns.append([0, 6, 12, 18, 24])
            patterns.append([4, 8, 12, 16, 20])
        elif mode == "four_corners":
            patterns.append([0, 4, 20, 24])
        elif mode == "x_pattern":
            patterns.append([0, 4, 6, 8, 12, 16, 18, 20, 24])
        elif mode == "blackout":
            patterns.append(list(range(25)))
        return patterns

    def has_bingo(self, player: GamePlayer) -> bool:
        marked = player.marked
        return any(all(player.card[i] in marked for i in pattern) for pattern in self._winning_patterns(player))

    def has_bingo_from_called_numbers(self, player: GamePlayer) -> bool:
        # The server determines winners from the called numbers, not from a
        # client's clicks. The center/free square is always considered marked.
        eligible = set(self.called_numbers) | {player.card[12]}
        return any(all(player.card[i] in eligible for i in pattern) for pattern in self._winning_patterns(player))

    async def add_player(self, user_id: str, username: str, card_number: int) -> Tuple[bool, str, Optional[dict]]:
        async with self._lock:
            if self.status != "waiting":
                return False, "Game selection is closed.", None
            if len(self.players) >= self.room.max_players:
                return False, "Game is full.", None
            if user_id in self.players:
                return False, "You are already in this game.", None
            if card_number in self.used_cards:
                return False, "That card is already taken.", None

            card = self.catalog.get(str(card_number))
            if not card or len(card) != 25:
                return False, "Invalid card.", None

            # Reserve the bet before changing in-memory state. If the DB call
            # fails, no player is added and no money is lost.
            reference = f"bet:{self.game_id}:{user_id}"
            if not await self.db.reserve_bet_by_telegram_id(
                user_id, self.room.card_price, reference, f"Bingo card #{card_number}"
            ):
                return False, "Insufficient balance or account unavailable.", None

            player = GamePlayer(
                user_id=user_id,
                username=username or "Player",
                card_number=card_number,
                card=list(card),
                marked={card[12]},
                bet_reserved=True,
            )
            self.players[user_id] = player
            self.total_bet += self.room.card_price
            self.prize_pool = round(self.total_bet * self.room.prize_percentage / 100, 2)
            self.commission = round(self.total_bet - self.prize_pool, 2)

            try:
                await self.db.add_player_to_game_by_telegram_id(
                    self.game_id, user_id, card_number, card
                )
                await self.db.update_game_financials(
                    self.game_id, self.total_bet, self.prize_pool, self.commission
                )
            except Exception:
                # Money was reserved, so compensate before exposing a failed join.
                await self.db.refund_bet_by_telegram_id(
                    user_id, self.room.card_price, reference, f"Failed join refund {self.game_id}"
                )
                self.players.pop(user_id, None)
                self.total_bet -= self.room.card_price
                self.prize_pool = round(self.total_bet * self.room.prize_percentage / 100, 2)
                self.commission = round(self.total_bet - self.prize_pool, 2)
                return False, "Could not save your game entry. No money was charged.", None

            return True, "Card selected successfully.", self.get_state(user_id)

    async def start(self) -> bool:
        async with self._lock:
            if self.status != "waiting" or len(self.players) < self.room.min_players:
                return False
            self.status = "active"
            self.started_at = utcnow()
            await self.db.update_game_status(self.game_id, "active", self.started_at)
            self._loop_task = asyncio.create_task(self._run_loop(), name=f"game-loop-{self.game_id}")
            return True

    async def _run_loop(self):
        while True:
            await asyncio.sleep(self.room.call_interval)
            number = await self.call_next_number()
            if number is None:
                break
            if self.status != "active":
                break

    async def call_next_number(self) -> Optional[int]:
        winners_to_settle: List[str] = []
        async with self._lock:
            if self.status != "active":
                return None
            if self._next_number_index >= len(self._number_order):
                await self._finish_locked([])
                return None

            number = self._number_order[self._next_number_index]
            self._next_number_index += 1
            self.called_numbers.append(number)

            for user_id, player in self.players.items():
                if not player.bingo_called and self.has_bingo_from_called_numbers(player):
                    player.bingo_called = True
                    player.is_winner = True
                    winners_to_settle.append(user_id)

            if winners_to_settle:
                self.winners.extend(winners_to_settle)
                await self._finish_locked(winners_to_settle)
            else:
                await self.db.update_game_called_numbers(self.game_id, self.called_numbers)

            return number

    async def mark_number(self, user_id: str, number: int) -> Tuple[bool, str, bool]:
        async with self._lock:
            if self.status != "active":
                return False, "Game is not active.", False
            player = self.players.get(user_id)
            if not player:
                return False, "You are not in this game.", False
            if number not in self.called_numbers:
                return False, "That number has not been called yet.", False
            if number not in player.card:
                return False, "That number is not on your card.", False
            if number in player.marked:
                return False, "That number is already marked.", False
            player.marked.add(number)
            await self.db.update_player_marks(self.game_id, user_id, sorted(player.marked))
            bingo = self.has_bingo_from_called_numbers(player)
            # Winner settlement happens when the server calls a number. This
            # prevents the first player to click BINGO from gaining an unfair advantage.
            return True, "Number marked.", bingo

    async def _finish_locked(self, winner_ids: List[str]):
        if self.status == "finished" and self._settled:
            return
        self.status = "finished"
        self.finished_at = utcnow()
        if winner_ids:
            share = round(self.prize_pool / len(self.winners), 2)
            # Ensure rounding does not create money from nowhere.
            amounts = [share] * len(self.winners)
            amounts[-1] = round(self.prize_pool - sum(amounts[:-1]), 2)
            for uid, amount in zip(self.winners, amounts):
                self.players[uid].win_amount = amount
                self.players[uid].is_winner = True
            await self.db.update_game_finished(
                self.game_id, self.winners, self.finished_at,
                server_seed=self.server_seed,
                game_hash=self.game_hash,
                called_numbers=self.called_numbers,
                winner_amounts={str(uid): self.players[uid].win_amount for uid in self.winners},
            )
            for uid in self.winners:
                amount = self.players[uid].win_amount
                reference = f"win:{self.game_id}:{uid}"
                paid = await self.db.pay_winner_once_by_telegram_id(
                    uid, amount, reference, f"Bingo prize for game {self.game_id}"
                )
                if paid:
                    await self.db.mark_payout_paid(self.game_id, uid)
                else:
                    logger.critical("Prize payout pending for game %s winner %s", self.game_id, uid)
                    self._settled = False
        else:
            # No winner: preserve the prize pool for admin review rather than
            # silently paying or destroying it.
            await self.db.update_game_finished(
                self.game_id, [], self.finished_at,
                server_seed=self.server_seed,
                game_hash=self.game_hash,
                called_numbers=self.called_numbers,
            )
        self._settled = True

    def get_state(self, user_id: Optional[str] = None) -> dict:
        state = {
            "game_id": self.game_id,
            "status": self.status,
            "mode": self.room.mode,
            "players": len(self.players),
            "max_players": self.room.max_players,
            "called_numbers": list(self.called_numbers),
            "recent_calls": self.called_numbers[-5:],
            "prize_pool": self.prize_pool,
            "card_price": self.room.card_price,
            "winners": list(self.winners),
            "game_hash": self.game_hash,
        }
        if user_id in self.players:
            p = self.players[user_id]
            state["player"] = {
                "card": p.card,
                "marked": sorted(p.marked),
                "has_bingo": p.bingo_called,
                "win_amount": p.win_amount,
                "is_disqualified": False,
                "card_number": p.card_number,
            }
        return state

    @property
    def catalog(self) -> dict:
        return load_catalog()

    def verify_fairness(self) -> dict:
        return {
            "game_hash": self.game_hash,
            "server_seed": self.server_seed if self.status == "finished" else None,
            "called_numbers": list(self.called_numbers),
            "seed_revealed": self.status == "finished",
        }


_catalog_cache: Optional[dict] = None

def load_catalog() -> dict:
    global _catalog_cache
    if _catalog_cache is None:
        path = Path(__file__).with_name("cards.json")
        data = json.loads(path.read_text(encoding="utf-8"))
        if len(data) != 400:
            raise RuntimeError(f"cards.json must contain exactly 400 cards; found {len(data)}")
        for key, card in data.items():
            if len(card) != 25 or len(set(card)) != 25:
                raise RuntimeError(f"Invalid card {key}")
            for col in range(5):
                values = [card[row * 5 + col] for row in range(5)]
                lo, hi = col * 15 + 1, col * 15 + 15
                if not all(lo <= n <= hi for n in values):
                    raise RuntimeError(f"Card {key} has invalid {col} column")
        _catalog_cache = data
    return _catalog_cache


class GameService:
    def __init__(self, db):
        self.db = db
        self.rooms: Dict[str, GameRoom] = {}
        self.games: Dict[str, ProductionGame] = {}
        self.user_game: Dict[str, str] = {}
        self._lock = asyncio.Lock()

    def create_room(self, room: GameRoom):
        self.rooms[room.room_id] = room

    async def get_or_create_game(self, room_id: str) -> ProductionGame:
        room = self.rooms[room_id]
        if room.current_game_id:
            current = self.games.get(room.current_game_id)
            if current and current.status == "waiting" and len(current.players) < room.max_players:
                return current
        game_id = f"{room_id}_{int(time.time())}_{secrets.token_hex(4)}"
        game = ProductionGame(game_id, room, self.db)
        self.games[game_id] = game
        room.current_game_id = game_id
        await self.db.create_game_record(game_id, room.room_id, room.card_price, room.prize_percentage, room.min_players, room.max_players, game.server_seed, game.game_hash)
        return game

    async def join(self, user_id: str, username: str, room_id: str, card_number: int):
        if room_id not in self.rooms:
            return False, "Room not found.", None
        if not str(card_number).isdigit() or not 1 <= int(card_number) <= 400:
            return False, "Invalid card number.", None
        user = await self.db.get_user(user_id)
        if not user:
            return False, "Please register before playing.", None
        if user.get("is_banned") or not user.get("is_active", True):
            return False, "Your account cannot play at this time.", None
        existing = self.user_game.get(user_id)
        if existing and existing in self.games and self.games[existing].status in {"waiting", "active"}:
            return False, "You are already in a game.", self.games[existing].get_state(user_id)
        game = await self.get_or_create_game(room_id)
        ok, msg, state = await game.add_player(user_id, username, int(card_number))
        if ok:
            self.user_game[user_id] = game.game_id
            if len(game.players) >= game.room.min_players and game._start_task is None:
                game._start_task = asyncio.create_task(self._delayed_start(game), name=f"start-{game.game_id}")
        return ok, msg, state

    async def _delayed_start(self, game: ProductionGame):
        await asyncio.sleep(game.room.selection_time)
        await game.start()

    def state_for_user(self, user_id: str):
        gid = self.user_game.get(user_id)
        game = self.games.get(gid) if gid else None
        return game.get_state(user_id) if game else None

    async def mark(self, user_id: str, number: int):
        gid = self.user_game.get(user_id)
        game = self.games.get(gid) if gid else None
        if not game:
            return False, "Not in a game.", False, None
        ok, msg, bingo = await game.mark_number(user_id, int(number))
        return ok, msg, bingo, game.get_state(user_id)

    async def recover_state(self):
        """Rebuild active/waiting games after an application restart."""
        for row in await self.db.get_active_games():
            room = self.rooms.get(row["room_id"])
            if not room:
                continue
            game = ProductionGame(row["game_id"], room, self.db)
            game.status = row["status"]
            game.server_seed = row.get("server_seed") or game.server_seed
            game.game_hash = row.get("game_hash") or hashlib.sha256(game.server_seed.encode()).hexdigest()
            game._number_order = list(range(1, 76))
            random.Random(game.server_seed).shuffle(game._number_order)
            game.called_numbers = list(row.get("called_numbers") or [])
            game._next_number_index = len(game.called_numbers)
            game.total_bet = float(row.get("total_bet") or 0)
            game.prize_pool = float(row.get("prize_pool") or 0)
            game.commission = float(row.get("commission") or 0)
            players = await self.db.get_game_players(row["game_id"])
            for item in players:
                uid = str(item["telegram_id"])
                card = list(item["card_data"])
                player = GamePlayer(
                    user_id=uid, username=item.get("username") or item.get("first_name") or "Player",
                    card_number=int(item["card_number"]), card=card,
                    marked=set(item.get("marked_numbers") or [card[12]]),
                    bingo_called=bool(item.get("bingo_called")),
                    is_winner=bool(item.get("is_winner")),
                    win_amount=float(item.get("win_amount") or 0), bet_reserved=True,
                )
                game.players[uid] = player
                self.user_game[uid] = game.game_id
            self.games[game.game_id] = game
            room.current_game_id = game.game_id
            if game.status == "active":
                game._loop_task = asyncio.create_task(game._run_loop(), name=f"recover-loop-{game.game_id}")
            elif game.status == "waiting" and len(game.players) >= room.min_players:
                game._start_task = asyncio.create_task(self._delayed_start(game), name=f"recover-start-{game.game_id}")
        logger.info("Recovered %s active/waiting games", len(self.games))

    async def cancel_and_refund(self, game_id: str) -> bool:
        game = self.games.get(game_id)
        if not game or game.status == "finished":
            return False
        async with game._lock:
            if game.status == "finished":
                return False
            game.status = "cancelled"
            for uid, player in game.players.items():
                if player.bet_reserved:
                    ok = await self.db.refund_bet_by_telegram_id(
                        uid, self.rooms[game.room.room_id].card_price,
                        f"cancel-refund:{game.game_id}:{uid}",
                        f"Cancelled game refund {game.game_id}",
                    )
                    if not ok:
                        logger.critical("Failed to refund cancelled game %s player %s", game_id, uid)
                        return False
                    player.bet_reserved = False
            await self.db.update_game_status(game_id, "cancelled")
            return True

    async def retry_pending_payouts(self):
        for row in await self.db.get_unpaid_winners():
            reference = f"win:{row['game_id']}:{row['telegram_id']}"
            paid = await self.db.pay_winner_once_by_telegram_id(
                str(row['telegram_id']), float(row['win_amount']), reference, f"Bingo prize retry for game {row['game_id']}"
            )
            if paid:
                await self.db.mark_payout_paid(row['game_id'], str(row['telegram_id']))

    def rooms_state(self):
        result = []
        for room in self.rooms.values():
            game = self.games.get(room.current_game_id) if room.current_game_id else None
            result.append({
                "room_id": room.room_id, "name": room.name, "description": room.description,
                "mode": room.mode, "players": len(game.players) if game else 0,
                "max_players": room.max_players, "status": game.status if game else "waiting",
                "prize_pool": game.prize_pool if game else 0, "card_price": room.card_price,
                "min_players": room.min_players,
            })
        return result
