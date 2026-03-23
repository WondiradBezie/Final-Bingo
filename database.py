# database.py - COMPLETE FIXED VERSION with Atomic Transactions
import asyncpg
import os
from datetime import datetime, timedelta
import logging
from typing import Optional, List, Dict, Any
import json
import asyncio

logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://bingo:secure_password@localhost/bingo")

class DatabaseManager:
    def __init__(self):
        self.pool = None
        self.initialized = False
    
    async def init_pool(self):
        """Initialize database connection pool with retry"""
        try:
            if not DATABASE_URL:
                logger.error("❌ DATABASE_URL not set")
                return False
            
            # Try to connect with retries
            for attempt in range(3):
                try:
                    self.pool = await asyncpg.create_pool(
                        DATABASE_URL,
                        min_size=2,
                        max_size=20,
                        command_timeout=60,
                        max_inactive_connection_lifetime=300
                    )
                    self.initialized = True
                    logger.info("✅ Database connection pool initialized")
                    
                    await self.create_tables()
                    return True
                except Exception as e:
                    logger.warning(f"Database connection attempt {attempt + 1} failed: {e}")
                    if attempt < 2:
                        await asyncio.sleep(2)
                    else:
                        raise
            return False
            
        except Exception as e:
            logger.error(f"❌ Failed to initialize database: {e}")
            return False
    
    async def create_tables(self):
        """Create all necessary tables with proper indexes"""
        try:
            async with self.pool.acquire() as conn:
                async with conn.transaction():
                    # Users table with constraints
                    await conn.execute("""
                        CREATE TABLE IF NOT EXISTS users (
                            id SERIAL PRIMARY KEY,
                            telegram_id TEXT UNIQUE NOT NULL,
                            username TEXT,
                            first_name TEXT,
                            last_name TEXT,
                            balance DECIMAL(12,2) DEFAULT 0 CHECK (balance >= 0),
                            total_deposits DECIMAL(12,2) DEFAULT 0,
                            total_withdrawals DECIMAL(12,2) DEFAULT 0,
                            total_wins DECIMAL(12,2) DEFAULT 0,
                            games_played INTEGER DEFAULT 0,
                            games_won INTEGER DEFAULT 0,
                            bingos_called INTEGER DEFAULT 0,
                            is_active BOOLEAN DEFAULT TRUE,
                            is_admin BOOLEAN DEFAULT FALSE,
                            is_banned BOOLEAN DEFAULT FALSE,
                            is_vip BOOLEAN DEFAULT FALSE,
                            referral_code TEXT,
                            referred_by TEXT,
                            created_at TIMESTAMP DEFAULT NOW(),
                            last_seen TIMESTAMP DEFAULT NOW(),
                            last_game TIMESTAMP
                        )
                    """)
                    
                    # Transactions table with atomic logging
                    await conn.execute("""
                        CREATE TABLE IF NOT EXISTS transactions (
                            id SERIAL PRIMARY KEY,
                            user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                            type TEXT NOT NULL CHECK (type IN ('deposit', 'withdrawal', 'bet', 'win', 'refund', 'welcome_bonus', 'admin_deposit', 'admin_withdrawal', 'withdrawal_pending')),
                            amount DECIMAL(12,2) NOT NULL,
                            balance_after DECIMAL(12,2) NOT NULL,
                            status TEXT DEFAULT 'completed',
                            reference TEXT,
                            description TEXT,
                            created_at TIMESTAMP DEFAULT NOW()
                        )
                    """)
                    
                    # Games table with indexes
                    await conn.execute("""
                        CREATE TABLE IF NOT EXISTS games (
                            id SERIAL PRIMARY KEY,
                            game_id TEXT UNIQUE NOT NULL,
                            room_id TEXT NOT NULL,
                            status TEXT DEFAULT 'waiting',
                            card_price DECIMAL(10,2) DEFAULT 10,
                            prize_percentage INTEGER DEFAULT 80,
                            min_players INTEGER DEFAULT 2,
                            max_players INTEGER DEFAULT 400,
                            called_numbers JSONB DEFAULT '[]',
                            winners JSONB DEFAULT '[]',
                            total_bet DECIMAL(12,2) DEFAULT 0,
                            prize_pool DECIMAL(12,2) DEFAULT 0,
                            commission DECIMAL(12,2) DEFAULT 0,
                            server_seed TEXT,
                            game_hash TEXT,
                            created_at TIMESTAMP DEFAULT NOW(),
                            started_at TIMESTAMP,
                            finished_at TIMESTAMP
                        )
                    """)
                    
                    # Game players table
                    await conn.execute("""
                        CREATE TABLE IF NOT EXISTS game_players (
                            id SERIAL PRIMARY KEY,
                            game_id INTEGER REFERENCES games(id) ON DELETE CASCADE,
                            user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                            card_number TEXT,
                            card_data JSONB,
                            marked_numbers JSONB DEFAULT '[]',
                            bingo_called BOOLEAN DEFAULT FALSE,
                            bingo_time TIMESTAMP,
                            win_amount DECIMAL(10,2) DEFAULT 0,
                            is_winner BOOLEAN DEFAULT FALSE,
                            joined_at TIMESTAMP DEFAULT NOW()
                        )
                    """)
                    
                    # Withdrawal requests
                    await conn.execute("""
                        CREATE TABLE IF NOT EXISTS withdrawal_requests (
                            id SERIAL PRIMARY KEY,
                            user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                            amount DECIMAL(12,2) NOT NULL,
                            status TEXT DEFAULT 'pending',
                            payment_method TEXT,
                            payment_details TEXT,
                            processed_by INTEGER REFERENCES users(id),
                            processed_at TIMESTAMP,
                            created_at TIMESTAMP DEFAULT NOW()
                        )
                    """)
                    
                    # Create indexes for performance
                    await conn.execute("CREATE INDEX IF NOT EXISTS idx_users_telegram_id ON users(telegram_id)")
                    await conn.execute("CREATE INDEX IF NOT EXISTS idx_users_balance ON users(balance DESC)")
                    await conn.execute("CREATE INDEX IF NOT EXISTS idx_transactions_user_id ON transactions(user_id)")
                    await conn.execute("CREATE INDEX IF NOT EXISTS idx_transactions_created_at ON transactions(created_at DESC)")
                    await conn.execute("CREATE INDEX IF NOT EXISTS idx_games_status ON games(status)")
                    await conn.execute("CREATE INDEX IF NOT EXISTS idx_withdrawal_requests_status ON withdrawal_requests(status)")
                    
                    logger.info("✅ Database tables created/verified")
                
        except Exception as e:
            logger.error(f"❌ Failed to create tables: {e}")
            raise
    
    async def update_balance(self, user_id: int, amount: float, 
                             transaction_type: str, description: str = "",
                             reference: str = None) -> bool:
        """Atomic balance update with transaction logging"""
        if amount == 0:
            return True
        
        try:
            async with self.pool.acquire() as conn:
                async with conn.transaction():
                    # Get current user with row lock
                    user = await conn.fetchrow(
                        "SELECT * FROM users WHERE id = $1 FOR UPDATE",
                        user_id
                    )
                    if not user:
                        logger.error(f"User {user_id} not found")
                        return False
                    
                    current_balance = float(user['balance'])
                    new_balance = current_balance + amount
                    
                    if new_balance < 0:
                        logger.error(f"Insufficient balance for user {user_id}: {current_balance} < {-amount}")
                        return False
                    
                    # Update user balance
                    await conn.execute("""
                        UPDATE users 
                        SET balance = $1, last_seen = $2
                        WHERE id = $3
                    """, new_balance, datetime.now(), user_id)
                    
                    # Update totals based on transaction type
                    if amount > 0:
                        if transaction_type in ['deposit', 'welcome_bonus', 'admin_deposit']:
                            await conn.execute("""
                                UPDATE users 
                                SET total_deposits = total_deposits + $1
                                WHERE id = $2
                            """, amount, user_id)
                        elif transaction_type == 'win':
                            await conn.execute("""
                                UPDATE users 
                                SET total_wins = total_wins + $1, games_won = games_won + 1
                                WHERE id = $2
                            """, amount, user_id)
                    else:
                        if transaction_type in ['withdrawal', 'admin_withdrawal']:
                            await conn.execute("""
                                UPDATE users 
                                SET total_withdrawals = total_withdrawals + $1
                                WHERE id = $2
                            """, -amount, user_id)
                    
                    # Record transaction
                    await conn.execute("""
                        INSERT INTO transactions 
                        (user_id, type, amount, balance_after, description, reference, created_at)
                        VALUES ($1, $2, $3, $4, $5, $6, $7)
                    """, user_id, transaction_type, abs(amount), new_balance, 
                    description, reference or f"{transaction_type}_{datetime.now().timestamp()}", 
                    datetime.now())
                    
                    logger.info(f"✅ Balance updated: user {user_id}: {current_balance} -> {new_balance} ({transaction_type}: {amount})")
                    return True
                    
        except Exception as e:
            logger.error(f"Error updating balance: {e}")
            return False
    
    async def get_user(self, telegram_id: str) -> Optional[Dict]:
        """Get user by telegram ID"""
        try:
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT * FROM users WHERE telegram_id = $1",
                    telegram_id
                )
                return dict(row) if row else None
        except Exception as e:
            logger.error(f"Error getting user: {e}")
            return None
    
    async def create_user(self, telegram_id: str, username: str = None, 
                          first_name: str = None, last_name: str = None,
                          referred_by: str = None) -> Dict:
        """Create new user with atomic transaction"""
        try:
            async with self.pool.acquire() as conn:
                async with conn.transaction():
                    # Check if user exists
                    existing = await conn.fetchrow(
                        "SELECT id FROM users WHERE telegram_id = $1",
                        telegram_id
                    )
                    if existing:
                        user = await conn.fetchrow(
                            "SELECT * FROM users WHERE telegram_id = $1",
                            telegram_id
                        )
                        return dict(user)
                    
                    # Generate referral code
                    referral_code = f"REF{telegram_id[-6:]}{datetime.now().strftime('%m%d')}"
                    
                    row = await conn.fetchrow("""
                        INSERT INTO users 
                        (telegram_id, username, first_name, last_name, balance, 
                         total_deposits, referral_code, referred_by, created_at, last_seen)
                        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                        RETURNING *
                    """, telegram_id, username, first_name, last_name, 
                    20.0, 20.0, referral_code, referred_by, 
                    datetime.now(), datetime.now())
                    
                    user = dict(row)
                    
                    # Record welcome bonus transaction
                    await conn.execute("""
                        INSERT INTO transactions 
                        (user_id, type, amount, balance_after, description, created_at)
                        VALUES ($1, $2, $3, $4, $5, $6)
                    """, user['id'], 'welcome_bonus', 20.0, 20.0, 
                    'Welcome bonus - 20 Birr', datetime.now())
                    
                    logger.info(f"✅ Created new user: {telegram_id} with 20 Birr bonus")
                    return user
                    
        except Exception as e:
            logger.error(f"Error creating user: {e}")
            return None
    
    async def get_active_games(self) -> List[Dict]:
        """Get active games for recovery"""
        try:
            async with self.pool.acquire() as conn:
                rows = await conn.fetch("""
                    SELECT * FROM games 
                    WHERE status IN ('waiting', 'active')
                    ORDER BY created_at DESC
                """)
                return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"Error getting active games: {e}")
            return []
    
    async def get_game_players(self, game_id: int) -> List[Dict]:
        """Get players for a game"""
        try:
            async with self.pool.acquire() as conn:
                rows = await conn.fetch("""
                    SELECT gp.*, u.username, u.first_name
                    FROM game_players gp
                    JOIN users u ON gp.user_id = u.id
                    WHERE gp.game_id = $1
                """, game_id)
                return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"Error getting game players: {e}")
            return []
    
    async def update_game_status(self, game_id: str, status: str, timestamp: datetime = None):
        """Update game status"""
        try:
            async with self.pool.acquire() as conn:
                if status == "active":
                    await conn.execute("""
                        UPDATE games 
                        SET status = $1, started_at = $2
                        WHERE game_id = $3
                    """, status, timestamp or datetime.now(), game_id)
                elif status == "finished":
                    await conn.execute("""
                        UPDATE games 
                        SET status = $1, finished_at = $2
                        WHERE game_id = $3
                    """, status, timestamp or datetime.now(), game_id)
                else:
                    await conn.execute("""
                        UPDATE games SET status = $1 WHERE game_id = $2
                    """, status, game_id)
        except Exception as e:
            logger.error(f"Error updating game status: {e}")
    
    async def update_game_finished(self, game_id: str, winners: List[str], finished_at: datetime):
        """Update game as finished with winners"""
        try:
            async with self.pool.acquire() as conn:
                await conn.execute("""
                    UPDATE games 
                    SET status = 'finished', winners = $1, finished_at = $2
                    WHERE game_id = $3
                """, json.dumps(winners), finished_at, game_id)
        except Exception as e:
            logger.error(f"Error updating game finished: {e}")
    
    async def add_player_to_game(self, game_id: str, user_id: int, card_number: str, card_data: list) -> bool:
        """Add player to game"""
        try:
            async with self.pool.acquire() as conn:
                async with conn.transaction():
                    # Get game ID
                    game_row = await conn.fetchrow(
                        "SELECT id FROM games WHERE game_id = $1",
                        game_id
                    )
                    if not game_row:
                        # Create game record if not exists
                        game_row = await conn.fetchrow("""
                            INSERT INTO games (game_id, room_id, status, created_at)
                            VALUES ($1, 'classic', 'waiting', $2)
                            RETURNING id
                        """, game_id, datetime.now())
                    
                    game_db_id = game_row['id']
                    
                    await conn.execute("""
                        INSERT INTO game_players 
                        (game_id, user_id, card_number, card_data, joined_at)
                        VALUES ($1, $2, $3, $4, $5)
                    """, game_db_id, user_id, card_number, json.dumps(card_data), datetime.now())
                    
                    # Update user's games_played count
                    await conn.execute("""
                        UPDATE users SET games_played = games_played + 1, last_game = $1
                        WHERE id = $2
                    """, datetime.now(), user_id)
                    
                    return True
        except Exception as e:
            logger.error(f"Error adding player to game: {e}")
            return False
    
    async def get_user_count(self) -> int:
        """Get total user count"""
        try:
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow("SELECT COUNT(*) FROM users")
                return row[0] if row else 0
        except Exception as e:
            logger.error(f"Error getting user count: {e}")
            return 0
    
    async def get_all_users(self, limit: int = 100, offset: int = 0) -> List[Dict]:
        """Get all users"""
        try:
            async with self.pool.acquire() as conn:
                rows = await conn.fetch("""
                    SELECT id, telegram_id, username, first_name, last_name, 
                           balance, total_deposits, total_withdrawals, total_wins,
                           games_played, games_won, is_active, is_banned, is_vip,
                           referral_code, referred_by, created_at, last_seen
                    FROM users
                    ORDER BY created_at DESC
                    LIMIT $1 OFFSET $2
                """, limit, offset)
                return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"Error getting all users: {e}")
            return []
    
    async def get_all_transactions(self, limit: int = 100, offset: int = 0) -> List[Dict]:
        """Get all transactions"""
        try:
            async with self.pool.acquire() as conn:
                rows = await conn.fetch("""
                    SELECT t.*, u.username, u.first_name, u.telegram_id
                    FROM transactions t
                    JOIN users u ON t.user_id = u.id
                    ORDER BY t.created_at DESC
                    LIMIT $1 OFFSET $2
                """, limit, offset)
                return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"Error getting all transactions: {e}")
            return []
    
    async def get_leaderboard(self, days: int = 30, limit: int = 10) -> List[Dict]:
        """Get leaderboard"""
        try:
            async with self.pool.acquire() as conn:
                rows = await conn.fetch("""
                    SELECT username, first_name, games_won, balance, total_wins, games_played
                    FROM users
                    WHERE games_played > 0
                    ORDER BY games_won DESC, balance DESC
                    LIMIT $1
                """, limit)
                
                leaderboard = []
                for row in rows:
                    leaderboard.append({
                        "username": row['username'] or row['first_name'] or "Unknown",
                        "wins": row['games_won'],
                        "winnings": float(row['total_wins']),
                        "balance": float(row['balance']),
                        "games_played": row['games_played']
                    })
                return leaderboard
        except Exception as e:
            logger.error(f"Error getting leaderboard: {e}")
            return []

# Global database instance
db = DatabaseManager()
