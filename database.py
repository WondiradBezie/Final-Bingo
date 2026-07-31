"""PostgreSQL data access for JOY BINGO.

All wallet mutations are serialized with PostgreSQL row locks and transaction
references are idempotent. The database is the single source of truth for
money; JSON files are never used for balances.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Dict, List, Optional

import asyncpg

logger = logging.getLogger(__name__)
DATABASE_URL = os.getenv("DATABASE_URL")


def money(value) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


class DatabaseManager:
    def __init__(self):
        self.pool: Optional[asyncpg.Pool] = None
        self.initialized = False

    async def init_pool(self) -> bool:
        if not DATABASE_URL:
            raise RuntimeError("DATABASE_URL is required")
        last_error = None
        for attempt in range(1, 4):
            try:
                self.pool = await asyncpg.create_pool(
                    DATABASE_URL,
                    min_size=2,
                    max_size=int(os.getenv("DB_POOL_MAX", "20")),
                    command_timeout=30,
                    max_inactive_connection_lifetime=300,
                )
                await self.create_tables()
                self.initialized = True
                logger.info("Database pool initialized")
                return True
            except Exception as exc:
                last_error = exc
                logger.warning("Database connection attempt %s failed: %s", attempt, exc)
                if attempt < 3:
                    await asyncio.sleep(attempt * 2)
        raise RuntimeError(f"Database initialization failed: {last_error}")

    async def create_tables(self):
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute("""
                    CREATE TABLE IF NOT EXISTS users (
                        id SERIAL PRIMARY KEY,
                        telegram_id TEXT UNIQUE NOT NULL,
                        username TEXT, first_name TEXT, last_name TEXT,
                        balance DECIMAL(12,2) NOT NULL DEFAULT 0 CHECK (balance >= 0),
                        total_deposits DECIMAL(12,2) NOT NULL DEFAULT 0,
                        total_withdrawals DECIMAL(12,2) NOT NULL DEFAULT 0,
                        total_wins DECIMAL(12,2) NOT NULL DEFAULT 0,
                        games_played INTEGER NOT NULL DEFAULT 0,
                        games_won INTEGER NOT NULL DEFAULT 0,
                        bingos_called INTEGER NOT NULL DEFAULT 0,
                        is_active BOOLEAN NOT NULL DEFAULT TRUE,
                        is_admin BOOLEAN NOT NULL DEFAULT FALSE,
                        is_banned BOOLEAN NOT NULL DEFAULT FALSE,
                        is_vip BOOLEAN NOT NULL DEFAULT FALSE,
                        referral_code TEXT, referred_by TEXT,
                        created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                        last_seen TIMESTAMP NOT NULL DEFAULT NOW(),
                        last_game TIMESTAMP
                    )
                """)
                await conn.execute("""
                    CREATE TABLE IF NOT EXISTS transactions (
                        id SERIAL PRIMARY KEY,
                        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                        type TEXT NOT NULL,
                        amount DECIMAL(12,2) NOT NULL CHECK (amount >= 0),
                        balance_after DECIMAL(12,2) NOT NULL CHECK (balance_after >= 0),
                        status TEXT NOT NULL DEFAULT 'completed',
                        reference TEXT,
                        description TEXT,
                        created_at TIMESTAMP NOT NULL DEFAULT NOW()
                    )
                """)
                await conn.execute("""
                    CREATE TABLE IF NOT EXISTS games (
                        id SERIAL PRIMARY KEY,
                        game_id TEXT UNIQUE NOT NULL,
                        room_id TEXT NOT NULL,
                        status TEXT NOT NULL DEFAULT 'waiting',
                        card_price DECIMAL(10,2) NOT NULL DEFAULT 10,
                        prize_percentage INTEGER NOT NULL DEFAULT 80,
                        min_players INTEGER NOT NULL DEFAULT 2,
                        max_players INTEGER NOT NULL DEFAULT 400,
                        called_numbers JSONB NOT NULL DEFAULT '[]',
                        winners JSONB NOT NULL DEFAULT '[]',
                        total_bet DECIMAL(12,2) NOT NULL DEFAULT 0,
                        prize_pool DECIMAL(12,2) NOT NULL DEFAULT 0,
                        commission DECIMAL(12,2) NOT NULL DEFAULT 0,
                        server_seed TEXT, game_hash TEXT,
                        created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                        started_at TIMESTAMP, finished_at TIMESTAMP
                    )
                """)
                await conn.execute("""
                    CREATE TABLE IF NOT EXISTS game_players (
                        id SERIAL PRIMARY KEY,
                        game_id INTEGER NOT NULL REFERENCES games(id) ON DELETE CASCADE,
                        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                        card_number TEXT NOT NULL,
                        card_data JSONB NOT NULL,
                        marked_numbers JSONB NOT NULL DEFAULT '[]',
                        bingo_called BOOLEAN NOT NULL DEFAULT FALSE,
                        bingo_time TIMESTAMP,
                        win_amount DECIMAL(10,2) NOT NULL DEFAULT 0,
                        is_winner BOOLEAN NOT NULL DEFAULT FALSE,
                        payout_status TEXT NOT NULL DEFAULT 'pending',
                        joined_at TIMESTAMP NOT NULL DEFAULT NOW(),
                        UNIQUE(game_id, user_id),
                        UNIQUE(game_id, card_number)
                    )
                """)
                await conn.execute("""
                    CREATE TABLE IF NOT EXISTS withdrawal_requests (
                        id SERIAL PRIMARY KEY,
                        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                        amount DECIMAL(12,2) NOT NULL CHECK (amount > 0),
                        status TEXT NOT NULL DEFAULT 'pending',
                        payment_method TEXT NOT NULL,
                        payment_details TEXT NOT NULL,
                        reference TEXT UNIQUE,
                        processed_by INTEGER REFERENCES users(id),
                        processed_at TIMESTAMP,
                        created_at TIMESTAMP NOT NULL DEFAULT NOW()
                    )
                """)
                await conn.execute("""
                    CREATE TABLE IF NOT EXISTS deposit_requests (
                        id SERIAL PRIMARY KEY,
                        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                        amount DECIMAL(12,2) NOT NULL CHECK (amount > 0),
                        payment_method TEXT NOT NULL,
                        external_reference TEXT NOT NULL,
                        status TEXT NOT NULL DEFAULT 'pending',
                        processed_by INTEGER REFERENCES users(id),
                        processed_at TIMESTAMP,
                        created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                        UNIQUE(payment_method, external_reference)
                    )
                """)
                await conn.execute("""
                    CREATE TABLE IF NOT EXISTS audit_logs (
                        id SERIAL PRIMARY KEY,
                        user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                        action TEXT NOT NULL,
                        details JSONB,
                        ip_address TEXT,
                        user_agent TEXT,
                        created_at TIMESTAMP NOT NULL DEFAULT NOW()
                    )
                """)
                # Safe migrations for databases created by earlier versions.
                for sql in (
                    "ALTER TABLE transactions ADD COLUMN IF NOT EXISTS reference TEXT",
                    "ALTER TABLE games ADD COLUMN IF NOT EXISTS server_seed TEXT",
                    "ALTER TABLE games ADD COLUMN IF NOT EXISTS game_hash TEXT",
                    "ALTER TABLE game_players ADD COLUMN IF NOT EXISTS payout_status TEXT NOT NULL DEFAULT 'pending'",
                ):
                    await conn.execute(sql)
                await conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_transactions_reference ON transactions(reference) WHERE reference IS NOT NULL")
                await conn.execute("CREATE INDEX IF NOT EXISTS idx_transactions_user_created ON transactions(user_id, created_at DESC)")
                await conn.execute("CREATE INDEX IF NOT EXISTS idx_games_status ON games(status)")
                await conn.execute("CREATE INDEX IF NOT EXISTS idx_game_players_game ON game_players(game_id)")
                await conn.execute("CREATE INDEX IF NOT EXISTS idx_withdrawal_status ON withdrawal_requests(status)")
                await conn.execute("CREATE INDEX IF NOT EXISTS idx_deposit_status ON deposit_requests(status)")
                await conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_logs(created_at DESC)")

    async def get_user(self, telegram_id: str) -> Optional[Dict]:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM users WHERE telegram_id=$1", str(telegram_id))
            return dict(row) if row else None

    async def create_user(self, telegram_id: str, username=None, first_name=None, last_name=None, referred_by=None) -> Optional[Dict]:
        telegram_id = str(telegram_id)
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                existing = await conn.fetchrow("SELECT * FROM users WHERE telegram_id=$1", telegram_id)
                if existing:
                    return dict(existing)
                referral_code = f"REF{telegram_id[-8:]}"
                row = await conn.fetchrow("""
                    INSERT INTO users(telegram_id,username,first_name,last_name,balance,total_deposits,referral_code,referred_by)
                    VALUES($1,$2,$3,$4,0,0,$5,$6) RETURNING *
                """, telegram_id, username, first_name, last_name, referral_code, referred_by)
                return dict(row)

    async def _mutate_balance(self, conn, user_id: int, delta: Decimal, transaction_type: str, description: str, reference: str) -> bool:
        if delta == 0:
            return True
        existing = await conn.fetchrow("SELECT id FROM transactions WHERE reference=$1", reference)
        if existing:
            return True
        user = await conn.fetchrow("SELECT id,balance FROM users WHERE id=$1 FOR UPDATE", user_id)
        if not user:
            return False
        current = money(user["balance"])
        new = current + delta
        if new < 0:
            return False
        await conn.execute("UPDATE users SET balance=$1,last_seen=NOW() WHERE id=$2", new, user_id)
        if delta > 0 and transaction_type in {"deposit", "welcome_bonus", "admin_deposit"}:
            await conn.execute("UPDATE users SET total_deposits=total_deposits+$1 WHERE id=$2", delta, user_id)
        if delta > 0 and transaction_type == "win":
            await conn.execute("UPDATE users SET total_wins=total_wins+$1,games_won=games_won+1 WHERE id=$2", delta, user_id)
        if delta < 0 and transaction_type in {"withdrawal", "admin_withdrawal"}:
            await conn.execute("UPDATE users SET total_withdrawals=total_withdrawals+$1 WHERE id=$2", -delta, user_id)
        await conn.execute("""
            INSERT INTO transactions(user_id,type,amount,balance_after,status,reference,description)
            VALUES($1,$2,$3,$4,'completed',$5,$6)
        """, user_id, transaction_type, abs(delta), new, reference, description)
        return True

    async def update_balance(self, user_id: int, amount, transaction_type: str, description: str = "", reference: str = None) -> bool:
        reference = reference or f"{transaction_type}:{user_id}:{datetime.utcnow().timestamp()}"
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                return await self._mutate_balance(conn, int(user_id), money(amount), transaction_type, description, reference)

    async def reserve_bet_by_telegram_id(self, telegram_id: str, amount, reference: str, description: str) -> bool:
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                user = await conn.fetchrow("SELECT id FROM users WHERE telegram_id=$1 AND is_active=TRUE AND is_banned=FALSE", str(telegram_id))
                if not user:
                    return False
                return await self._mutate_balance(conn, user["id"], -money(amount), "bet", description, reference)

    async def refund_bet_by_telegram_id(self, telegram_id: str, amount, reference: str, description: str) -> bool:
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                user = await conn.fetchrow("SELECT id FROM users WHERE telegram_id=$1 FOR UPDATE", str(telegram_id))
                if not user:
                    return False
                return await self._mutate_balance(conn, user["id"], money(amount), "refund", description, reference + ":refund")

    async def pay_winner_once_by_telegram_id(self, telegram_id: str, amount, reference: str, description: str) -> bool:
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                user = await conn.fetchrow("SELECT id FROM users WHERE telegram_id=$1 FOR UPDATE", str(telegram_id))
                if not user:
                    return False
                return await self._mutate_balance(conn, user["id"], money(amount), "win", description, reference)

    async def create_game_record(self, game_id, room_id, card_price, prize_percentage, min_players, max_players, server_seed, game_hash):
        async with self.pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO games(game_id,room_id,status,card_price,prize_percentage,min_players,max_players,server_seed,game_hash)
                VALUES($1,$2,'waiting',$3,$4,$5,$6,$7,$8) ON CONFLICT(game_id) DO NOTHING
            """, game_id, room_id, card_price, prize_percentage, min_players, max_players, server_seed, game_hash)

    async def update_game_financials(self, game_id, total_bet, prize_pool, commission):
        async with self.pool.acquire() as conn:
            await conn.execute("UPDATE games SET total_bet=$1,prize_pool=$2,commission=$3 WHERE game_id=$4", total_bet, prize_pool, commission, game_id)

    async def update_game_called_numbers(self, game_id, numbers):
        async with self.pool.acquire() as conn:
            await conn.execute("UPDATE games SET called_numbers=$1 WHERE game_id=$2", json.dumps(numbers), game_id)

    async def update_game_status(self, game_id, status, timestamp=None):
        async with self.pool.acquire() as conn:
            if status == "active":
                await conn.execute("UPDATE games SET status='active',started_at=$1 WHERE game_id=$2", timestamp or datetime.utcnow(), game_id)
            elif status == "finished":
                await conn.execute("UPDATE games SET status='finished',finished_at=$1 WHERE game_id=$2", timestamp or datetime.utcnow(), game_id)
            else:
                await conn.execute("UPDATE games SET status=$1 WHERE game_id=$2", status, game_id)

    async def update_game_finished(self, game_id, winners, finished_at, server_seed=None, game_hash=None, called_numbers=None, winner_amounts=None):
        async with self.pool.acquire() as conn:
            await conn.execute("""
                UPDATE games SET status='finished',winners=$1,finished_at=$2,
                    server_seed=COALESCE($3,server_seed),game_hash=COALESCE($4,game_hash),
                    called_numbers=COALESCE($5,called_numbers)
                WHERE game_id=$6
            """, json.dumps(winners), finished_at, server_seed, game_hash, json.dumps(called_numbers) if called_numbers is not None else None, game_id)
            for uid in winners:
                amount = money((winner_amounts or {}).get(str(uid), 0))
                await conn.execute("""
                    UPDATE game_players SET is_winner=TRUE,bingo_called=TRUE,win_amount=$1,payout_status='pending',bingo_time=NOW()
                    WHERE game_id=(SELECT id FROM games WHERE game_id=$2) AND user_id=(SELECT id FROM users WHERE telegram_id=$3)
                """, amount, game_id, str(uid))

    async def add_player_to_game_by_telegram_id(self, game_id, telegram_id, card_number, card_data):
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                game = await conn.fetchrow("SELECT id FROM games WHERE game_id=$1 FOR UPDATE", game_id)
                user = await conn.fetchrow("SELECT id FROM users WHERE telegram_id=$1 FOR UPDATE", str(telegram_id))
                if not game or not user:
                    raise ValueError("Game or user not found")
                inserted = await conn.fetchval("""
                    INSERT INTO game_players(game_id,user_id,card_number,card_data,marked_numbers)
                    VALUES($1,$2,$3,$4,$5)
                    ON CONFLICT(game_id,user_id) DO NOTHING
                    RETURNING id
                """, game["id"], user["id"], str(card_number), json.dumps(card_data), json.dumps([card_data[12]]))
                if inserted is None:
                    raise ValueError("Player is already in this game")
                await conn.execute("UPDATE users SET games_played=games_played+1,last_game=NOW() WHERE id=$1", user["id"])

    async def update_player_marks(self, game_id, telegram_id, marked):
        async with self.pool.acquire() as conn:
            await conn.execute("""
                UPDATE game_players SET marked_numbers=$1
                WHERE game_id=(SELECT id FROM games WHERE game_id=$2)
                AND user_id=(SELECT id FROM users WHERE telegram_id=$3)
            """, json.dumps(marked), game_id, str(telegram_id))

    async def mark_payout_paid(self, game_id, telegram_id):
        async with self.pool.acquire() as conn:
            await conn.execute("""
                UPDATE game_players SET payout_status='paid'
                WHERE game_id=(SELECT id FROM games WHERE game_id=$1)
                  AND user_id=(SELECT id FROM users WHERE telegram_id=$2)
            """, game_id, str(telegram_id))

    async def get_unpaid_winners(self):
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT g.game_id,gp.win_amount,u.telegram_id
                FROM game_players gp
                JOIN games g ON g.id=gp.game_id
                JOIN users u ON u.id=gp.user_id
                WHERE g.status='finished' AND gp.is_winner=TRUE AND gp.payout_status='pending' AND gp.win_amount>0
            """)
            return [dict(r) for r in rows]

    async def get_active_games(self):
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("SELECT * FROM games WHERE status IN ('waiting','active') ORDER BY created_at DESC")
            return [dict(r) for r in rows]

    async def get_game_players(self, game_id):
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("SELECT id FROM games WHERE game_id=$1", game_id) if isinstance(game_id, str) else None
            db_id = row["id"] if row else game_id
            rows = await conn.fetch("""
                SELECT gp.*,u.telegram_id,u.username,u.first_name
                FROM game_players gp JOIN users u ON gp.user_id=u.id WHERE gp.game_id=$1
            """, db_id)
            return [dict(r) for r in rows]

    async def create_deposit_request(self, telegram_id, amount, method, external_reference):
        async with self.pool.acquire() as conn:
            user = await conn.fetchrow("SELECT id FROM users WHERE telegram_id=$1", str(telegram_id))
            if not user:
                return None
            try:
                row = await conn.fetchrow("""
                    INSERT INTO deposit_requests(user_id,amount,payment_method,external_reference)
                    VALUES($1,$2,$3,$4) RETURNING id,status
                """, user["id"], money(amount), method, external_reference)
                return dict(row)
            except asyncpg.UniqueViolationError:
                return None

    async def create_withdrawal_request(self, telegram_id, amount, method, details):
        amount = money(amount)
        reference = f"withdraw:{telegram_id}:{secrets_token()}"
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                user = await conn.fetchrow("SELECT id FROM users WHERE telegram_id=$1 FOR UPDATE", str(telegram_id))
                if not user:
                    return None
                if not await self._mutate_balance(conn, user["id"], -amount, "withdrawal_pending", "Withdrawal reserved", reference + ":reserve"):
                    return None
                row = await conn.fetchrow("""
                    INSERT INTO withdrawal_requests(user_id,amount,payment_method,payment_details,reference)
                    VALUES($1,$2,$3,$4,$5) RETURNING id,status
                """, user["id"], amount, method, details, reference)
                return dict(row)

    async def get_pending_deposits(self, limit=100):
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT d.*,u.telegram_id,u.username FROM deposit_requests d JOIN users u ON u.id=d.user_id
                WHERE d.status='pending' ORDER BY d.created_at ASC LIMIT $1
            """, limit)
            return [dict(r) for r in rows]

    async def approve_deposit(self, request_id: int, admin_user_id: int) -> bool:
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                req = await conn.fetchrow("SELECT * FROM deposit_requests WHERE id=$1 FOR UPDATE", request_id)
                if not req or req["status"] != "pending":
                    return False
                reference = f"deposit:{req['payment_method']}:{req['external_reference']}"
                ok = await self._mutate_balance(conn, req["user_id"], money(req["amount"]), "deposit", "Approved deposit", reference)
                if not ok:
                    return False
                await conn.execute("UPDATE deposit_requests SET status='approved',processed_by=$1,processed_at=NOW() WHERE id=$2", admin_user_id, request_id)
                return True

    async def reject_deposit(self, request_id: int, admin_user_id: int) -> bool:
        async with self.pool.acquire() as conn:
            result = await conn.execute("UPDATE deposit_requests SET status='rejected',processed_by=$1,processed_at=NOW() WHERE id=$2 AND status='pending'", admin_user_id, request_id)
            return result.endswith("1")

    async def approve_withdrawal(self, request_id: int, admin_user_id: int) -> bool:
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                req = await conn.fetchrow("SELECT * FROM withdrawal_requests WHERE id=$1 FOR UPDATE", request_id)
                if not req or req["status"] != "pending":
                    return False
                await conn.execute("UPDATE withdrawal_requests SET status='approved',processed_by=$1,processed_at=NOW() WHERE id=$2", admin_user_id, request_id)
                await conn.execute("UPDATE users SET total_withdrawals=total_withdrawals+$1 WHERE id=$2", money(req["amount"]), req["user_id"])
                return True

    async def reject_withdrawal(self, request_id: int, admin_user_id: int) -> bool:
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                req = await conn.fetchrow("SELECT * FROM withdrawal_requests WHERE id=$1 FOR UPDATE", request_id)
                if not req or req["status"] != "pending":
                    return False
                user_id = req["user_id"]
                reference = f"withdraw-refund:{request_id}"
                if not await self._mutate_balance(conn, user_id, money(req["amount"]), "refund", "Rejected withdrawal refund", reference):
                    return False
                await conn.execute("UPDATE withdrawal_requests SET status='rejected',processed_by=$1,processed_at=NOW() WHERE id=$2", admin_user_id, request_id)
                return True

    async def get_pending_withdrawals(self, limit=100):
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT w.*,u.telegram_id,u.username FROM withdrawal_requests w JOIN users u ON u.id=w.user_id
                WHERE w.status='pending' ORDER BY w.created_at ASC LIMIT $1
            """, limit)
            return [dict(r) for r in rows]

    async def set_user_banned(self, telegram_id: str, banned: bool, admin_user_id: int):
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                user = await conn.fetchrow("SELECT id FROM users WHERE telegram_id=$1 FOR UPDATE", str(telegram_id))
                if not user:
                    return False
                await conn.execute("UPDATE users SET is_banned=$1 WHERE id=$2", bool(banned), user["id"])
                await conn.execute("INSERT INTO audit_logs(user_id,action,details) VALUES($1,$2,$3)", admin_user_id, "user_ban" if banned else "user_unban", json.dumps({"target_telegram_id": str(telegram_id)}))
                return True

    async def get_user_count(self):
        async with self.pool.acquire() as conn:
            return (await conn.fetchval("SELECT COUNT(*) FROM users")) or 0

    async def get_all_users(self, limit=100, offset=0):
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT id,telegram_id,username,first_name,last_name,balance,total_deposits,total_withdrawals,total_wins,
                       games_played,games_won,is_active,is_banned,is_vip,referral_code,referred_by,created_at,last_seen
                FROM users ORDER BY created_at DESC LIMIT $1 OFFSET $2
            """, min(max(int(limit),1),1000), max(int(offset),0))
            return [dict(r) for r in rows]

    async def get_all_transactions(self, limit=100, offset=0):
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT t.*,u.username,u.first_name,u.telegram_id FROM transactions t JOIN users u ON u.id=t.user_id
                ORDER BY t.created_at DESC LIMIT $1 OFFSET $2
            """, min(max(int(limit),1),1000), max(int(offset),0))
            return [dict(r) for r in rows]

    async def get_leaderboard(self, days=30, limit=10):
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT username,first_name,games_won,balance,total_wins,games_played
                FROM users WHERE games_played>0 ORDER BY games_won DESC,total_wins DESC LIMIT $1
            """, limit)
            return [{"username": r["username"] or r["first_name"] or "Unknown", "wins": r["games_won"],
                     "winnings": float(r["total_wins"]), "balance": float(r["balance"]), "games_played": r["games_played"]} for r in rows]

    async def get_audit_logs(self, limit=100):
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("SELECT * FROM audit_logs ORDER BY created_at DESC LIMIT $1", limit)
            return [dict(r) for r in rows]

    async def add_audit_log(self, user_id, action, details=None, ip_address=None, user_agent=None):
        async with self.pool.acquire() as conn:
            await conn.execute("INSERT INTO audit_logs(user_id,action,details,ip_address,user_agent) VALUES($1,$2,$3,$4,$5)", user_id, action, json.dumps(details or {}), ip_address, user_agent)


def secrets_token():
    import secrets
    return secrets.token_hex(12)


db = DatabaseManager()
