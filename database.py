# database.py
import asyncpg
import os
from datetime import datetime, timedelta
import logging
from typing import Optional, List, Dict, Any
import json

logger = logging.getLogger(__name__)

# Get database URL from environment
DATABASE_URL = os.getenv("DATABASE_URL")

class DatabaseManager:
    def __init__(self):
        self.pool = None
        self.initialized = False
    
    async def init_pool(self):
        """Initialize database connection pool"""
        try:
            if not DATABASE_URL:
                logger.error("❌ DATABASE_URL not set in environment variables")
                return False
            
            self.pool = await asyncpg.create_pool(
                DATABASE_URL,
                min_size=1,
                max_size=10,
                command_timeout=60
            )
            self.initialized = True
            logger.info("✅ Database connection pool initialized")
            
            # Create tables if they don't exist
            await self.create_tables()
            return True
            
        except Exception as e:
            logger.error(f"❌ Failed to initialize database: {e}")
            return False
    
    async def create_tables(self):
        """Create all necessary tables if they don't exist"""
        try:
            async with self.pool.acquire() as conn:
                # Users table
                await conn.execute("""
                    CREATE TABLE IF NOT EXISTS users (
                        id SERIAL PRIMARY KEY,
                        telegram_id TEXT UNIQUE NOT NULL,
                        username TEXT,
                        first_name TEXT,
                        last_name TEXT,
                        balance DECIMAL(12,2) DEFAULT 0,
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
                
                # Transactions table
                await conn.execute("""
                    CREATE TABLE IF NOT EXISTS transactions (
                        id SERIAL PRIMARY KEY,
                        user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                        type TEXT NOT NULL,
                        amount DECIMAL(12,2) NOT NULL,
                        balance_after DECIMAL(12,2) NOT NULL,
                        status TEXT DEFAULT 'completed',
                        reference TEXT,
                        description TEXT,
                        created_at TIMESTAMP DEFAULT NOW()
                    )
                """)
                
                # Games table
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
                        created_at TIMESTAMP DEFAULT NOW(),
                        started_at TIMESTAMP,
                        finished_at TIMESTAMP
                    )
                """)
                
                # Game Players table
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
                
                # Rooms table
                await conn.execute("""
                    CREATE TABLE IF NOT EXISTS rooms (
                        id SERIAL PRIMARY KEY,
                        room_id TEXT UNIQUE NOT NULL,
                        name TEXT NOT NULL,
                        description TEXT,
                        card_price DECIMAL(10,2),
                        prize_percentage INTEGER,
                        call_interval FLOAT DEFAULT 2.0,
                        selection_time INTEGER DEFAULT 20,
                        is_active BOOLEAN DEFAULT TRUE,
                        current_game_id TEXT,
                        created_at TIMESTAMP DEFAULT NOW()
                    )
                """)
                
                # Audit logs table
                await conn.execute("""
                    CREATE TABLE IF NOT EXISTS audit_logs (
                        id SERIAL PRIMARY KEY,
                        user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                        action TEXT NOT NULL,
                        details JSONB,
                        ip_address TEXT,
                        user_agent TEXT,
                        created_at TIMESTAMP DEFAULT NOW()
                    )
                """)
                
                # Withdrawal requests table
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
                
                # Create indexes for better performance
                await conn.execute("CREATE INDEX IF NOT EXISTS idx_users_telegram_id ON users(telegram_id)")
                await conn.execute("CREATE INDEX IF NOT EXISTS idx_users_balance ON users(balance DESC)")
                await conn.execute("CREATE INDEX IF NOT EXISTS idx_transactions_user_id ON transactions(user_id)")
                await conn.execute("CREATE INDEX IF NOT EXISTS idx_transactions_created_at ON transactions(created_at DESC)")
                await conn.execute("CREATE INDEX IF NOT EXISTS idx_games_status ON games(status)")
                await conn.execute("CREATE INDEX IF NOT EXISTS idx_withdrawal_requests_status ON withdrawal_requests(status)")
                
                logger.info("✅ Database tables created/verified")
                
        except Exception as e:
            logger.error(f"❌ Failed to create tables: {e}")
    
    # ============= USER MANAGEMENT =============
    
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
    
    async def get_user_by_id(self, user_id: int) -> Optional[Dict]:
        """Get user by database ID"""
        try:
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT * FROM users WHERE id = $1",
                    user_id
                )
                return dict(row) if row else None
        except Exception as e:
            logger.error(f"Error getting user by id: {e}")
            return None
    
    async def create_user(self, telegram_id: str, username: str = None, 
                          first_name: str = None, last_name: str = None,
                          referred_by: str = None) -> Dict:
        """Create new user with starting balance"""
        try:
            async with self.pool.acquire() as conn:
                async with conn.transaction():
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
    
    # ============= WALLET / BALANCE MANAGEMENT =============
    
    async def update_balance(self, user_id: int, amount: float, 
                             transaction_type: str, description: str = "",
                             reference: str = None) -> bool:
        """Update user balance with transaction record"""
        try:
            async with self.pool.acquire() as conn:
                async with conn.transaction():
                    # Get current user with lock for update
                    user = await conn.fetchrow(
                        "SELECT * FROM users WHERE id = $1 FOR UPDATE",
                        user_id
                    )
                    if not user:
                        logger.error(f"User {user_id} not found")
                        return False
                    
                    current_balance = float(user['balance'])
                    new_balance = current_balance + amount
                    
                    # Update user balance
                    update_query = """
                        UPDATE users 
                        SET balance = $1, last_seen = $2
                    """
                    params = [new_balance, datetime.now()]
                    
                    # Update totals based on transaction type
                    if amount > 0:
                        if transaction_type in ['deposit', 'welcome_bonus']:
                            update_query += ", total_deposits = total_deposits + $3"
                            params.append(amount)
                        elif transaction_type == 'win':
                            update_query += ", total_wins = total_wins + $3"
                            params.append(amount)
                    else:
                        if transaction_type == 'withdrawal':
                            update_query += ", total_withdrawals = total_withdrawals + $3"
                            params.append(abs(amount))
                    
                    update_query += " WHERE id = $4"
                    params.append(user_id)
                    
                    await conn.execute(update_query, *params)
                    
                    # Record transaction
                    await conn.execute("""
                        INSERT INTO transactions 
                        (user_id, type, amount, balance_after, description, reference, created_at)
                        VALUES ($1, $2, $3, $4, $5, $6, $7)
                    """, user_id, transaction_type, abs(amount), new_balance, 
                    description, reference or f"{transaction_type}_{datetime.now().timestamp()}", 
                    datetime.now())
                    
                    # If this is a win, update games_won count
                    if transaction_type == 'win':
                        await conn.execute("""
                            UPDATE users 
                            SET games_won = games_won + 1 
                            WHERE id = $1
                        """, user_id)
                    
                    logger.info(f"✅ Balance updated for user {user_id}: {current_balance} -> {new_balance} ({transaction_type}: {amount})")
                    return True
                    
        except Exception as e:
            logger.error(f"Error updating balance: {e}")
            return False
    
    async def get_balance(self, telegram_id: str) -> float:
        """Get user balance"""
        try:
            user = await self.get_user(telegram_id)
            return float(user['balance']) if user else 0.0
        except Exception as e:
            logger.error(f"Error getting balance: {e}")
            return 0.0
    
    async def deduct_balance(self, user_id: int, amount: float, 
                             description: str = "") -> bool:
        """Deduct balance from user (for bets, card purchases)"""
        if amount <= 0:
            return False
        return await self.update_balance(user_id, -amount, 'bet', description)
    
    async def add_balance(self, user_id: int, amount: float, 
                          transaction_type: str, description: str = "") -> bool:
        """Add balance to user (deposits, wins, bonuses)"""
        if amount <= 0:
            return False
        return await self.update_balance(user_id, amount, transaction_type, description)
    
    # ============= DEPOSIT & WITHDRAWAL =============
    
    async def create_withdrawal_request(self, user_id: int, amount: float, 
                                         payment_method: str = "telegram",
                                         payment_details: str = None) -> Dict:
        """Create a withdrawal request"""
        try:
            async with self.pool.acquire() as conn:
                async with conn.transaction():
                    # Check if user has enough balance
                    user = await conn.fetchrow(
                        "SELECT balance FROM users WHERE id = $1 FOR UPDATE",
                        user_id
                    )
                    if not user or float(user['balance']) < amount:
                        return None
                    
                    # Create withdrawal request
                    row = await conn.fetchrow("""
                        INSERT INTO withdrawal_requests 
                        (user_id, amount, payment_method, payment_details, created_at)
                        VALUES ($1, $2, $3, $4, $5)
                        RETURNING *
                    """, user_id, amount, payment_method, payment_details, datetime.now())
                    
                    # Deduct balance (pending until processed)
                    await self.update_balance(user_id, -amount, 'withdrawal_pending', 
                                             f"Withdrawal request: {amount} Birr")
                    
                    logger.info(f"✅ Withdrawal request created for user {user_id}: {amount} Birr")
                    return dict(row)
                    
        except Exception as e:
            logger.error(f"Error creating withdrawal request: {e}")
            return None
    
    async def process_withdrawal(self, request_id: int, admin_id: int, 
                                  approve: bool = True) -> bool:
        """Process a withdrawal request (approve or reject)"""
        try:
            async with self.pool.acquire() as conn:
                async with conn.transaction():
                    request = await conn.fetchrow(
                        "SELECT * FROM withdrawal_requests WHERE id = $1 FOR UPDATE",
                        request_id
                    )
                    if not request:
                        return False
                    
                    if approve:
                        # Mark as completed
                        await conn.execute("""
                            UPDATE withdrawal_requests 
                            SET status = 'completed', processed_by = $1, processed_at = $2
                            WHERE id = $3
                        """, admin_id, datetime.now(), request_id)
                        
                        # Update transaction record
                        await conn.execute("""
                            UPDATE transactions 
                            SET status = 'completed', 
                                description = description || ' - Approved'
                            WHERE user_id = $1 AND amount = $2 
                            AND type = 'withdrawal_pending'
                            ORDER BY created_at DESC LIMIT 1
                        """, request['user_id'], request['amount'])
                        
                        logger.info(f"✅ Withdrawal request {request_id} approved")
                    else:
                        # Reject - refund the amount
                        await conn.execute("""
                            UPDATE withdrawal_requests 
                            SET status = 'rejected', processed_by = $1, processed_at = $2
                            WHERE id = $3
                        """, admin_id, datetime.now(), request_id)
                        
                        # Refund the amount back to user
                        await self.update_balance(request['user_id'], request['amount'], 
                                                 'refund', f"Withdrawal rejected - Refund")
                        
                        logger.info(f"✅ Withdrawal request {request_id} rejected, refunded")
                    
                    return True
                    
        except Exception as e:
            logger.error(f"Error processing withdrawal: {e}")
            return False
    
    async def get_withdrawal_requests(self, status: str = "pending") -> List[Dict]:
        """Get withdrawal requests by status"""
        try:
            async with self.pool.acquire() as conn:
                rows = await conn.fetch("""
                    SELECT w.*, u.username, u.first_name, u.last_name, u.telegram_id
                    FROM withdrawal_requests w
                    JOIN users u ON w.user_id = u.id
                    WHERE w.status = $1
                    ORDER BY w.created_at DESC
                """, status)
                return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"Error getting withdrawal requests: {e}")
            return []
    
    # ============= GAME MANAGEMENT =============
    
    async def create_game(self, game_id: str, room_id: str, **kwargs) -> Dict:
        """Create a new game record"""
        try:
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow("""
                    INSERT INTO games 
                    (game_id, room_id, card_price, prize_percentage, min_players, max_players, created_at)
                    VALUES ($1, $2, $3, $4, $5, $6, $7)
                    RETURNING *
                """, game_id, room_id, 
                kwargs.get('card_price', 10),
                kwargs.get('prize_percentage', 80),
                kwargs.get('min_players', 2),
                kwargs.get('max_players', 400),
                datetime.now())
                return dict(row) if row else None
        except Exception as e:
            logger.error(f"Error creating game: {e}")
            return None
    
    async def add_player_to_game(self, game_id: int, user_id: int, 
                                  card_number: str, card_data: list) -> bool:
        """Add player to game"""
        try:
            async with self.pool.acquire() as conn:
                async with conn.transaction():
                    await conn.execute("""
                        INSERT INTO game_players 
                        (game_id, user_id, card_number, card_data, joined_at)
                        VALUES ($1, $2, $3, $4, $5)
                    """, game_id, user_id, str(card_number), json.dumps(card_data), datetime.now())
                    
                    # Update user's games_played count
                    await conn.execute("""
                        UPDATE users SET games_played = games_played + 1, last_game = $1
                        WHERE id = $2
                    """, datetime.now(), user_id)
                    
                    return True
        except Exception as e:
            logger.error(f"Error adding player to game: {e}")
            return False
    
    async def update_game_winner(self, game_id: int, user_id: int, win_amount: float):
        """Update game winner"""
        try:
            async with self.pool.acquire() as conn:
                async with conn.transaction():
                    # Update game_players
                    await conn.execute("""
                        UPDATE game_players 
                        SET is_winner = TRUE, win_amount = $1, bingo_called = TRUE, bingo_time = $2
                        WHERE game_id = $3 AND user_id = $4
                    """, win_amount, datetime.now(), game_id, user_id)
                    
                    # Update game record
                    await conn.execute("""
                        UPDATE games 
                        SET status = 'finished', finished_at = $1, 
                            winners = winners || $2::jsonb
                        WHERE id = $3
                    """, datetime.now(), json.dumps([user_id]), game_id)
                    
                    return True
        except Exception as e:
            logger.error(f"Error updating game winner: {e}")
            return False
    
    # ============= LEADERBOARD & STATS =============
    
    async def get_leaderboard(self, days: int = 30, limit: int = 10) -> List[Dict]:
        """Get top players by wins and balance"""
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
    
    async def get_user_stats(self, user_id: int) -> Dict:
        """Get comprehensive user statistics"""
        try:
            async with self.pool.acquire() as conn:
                # Get user stats
                user = await conn.fetchrow("""
                    SELECT * FROM users WHERE id = $1
                """, user_id)
                
                if not user:
                    return {}
                
                # Get recent transactions
                transactions = await conn.fetch("""
                    SELECT type, amount, balance_after, created_at, description
                    FROM transactions
                    WHERE user_id = $1
                    ORDER BY created_at DESC
                    LIMIT 10
                """, user_id)
                
                # Get game history
                games = await conn.fetch("""
                    SELECT g.game_id, g.room_id, gp.card_number, gp.win_amount, gp.is_winner, gp.joined_at
                    FROM game_players gp
                    JOIN games g ON gp.game_id = g.id
                    WHERE gp.user_id = $1
                    ORDER BY gp.joined_at DESC
                    LIMIT 10
                """, user_id)
                
                return {
                    "user": dict(user),
                    "recent_transactions": [dict(t) for t in transactions],
                    "recent_games": [dict(g) for g in games]
                }
        except Exception as e:
            logger.error(f"Error getting user stats: {e}")
            return {}
    
    # ============= TRANSACTION HISTORY =============
    
    async def get_transaction_history(self, user_id: int, limit: int = 20) -> List[Dict]:
        """Get transaction history for a user"""
        try:
            async with self.pool.acquire() as conn:
                rows = await conn.fetch("""
                    SELECT id, type, amount, balance_after, description, reference, created_at, status
                    FROM transactions
                    WHERE user_id = $1
                    ORDER BY created_at DESC
                    LIMIT $2
                """, user_id, limit)
                return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"Error getting transaction history: {e}")
            return []
    
    async def get_all_transactions(self, limit: int = 100, offset: int = 0) -> List[Dict]:
        """Get all transactions (admin only)"""
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
    
    # ============= AUDIT LOGS =============
    
    async def add_audit_log(self, user_id: int, action: str, 
                             details: Dict = None, ip: str = None, 
                             user_agent: str = None) -> bool:
        """Add audit log entry"""
        try:
            async with self.pool.acquire() as conn:
                await conn.execute("""
                    INSERT INTO audit_logs (user_id, action, details, ip_address, user_agent, created_at)
                    VALUES ($1, $2, $3, $4, $5, $6)
                """, user_id, action, json.dumps(details) if details else None, 
                ip, user_agent, datetime.now())
                return True
        except Exception as e:
            logger.error(f"Error adding audit log: {e}")
            return False
    
    async def get_audit_logs(self, limit: int = 100) -> List[Dict]:
        """Get audit logs"""
        try:
            async with self.pool.acquire() as conn:
                rows = await conn.fetch("""
                    SELECT a.*, u.username, u.first_name
                    FROM audit_logs a
                    LEFT JOIN users u ON a.user_id = u.id
                    ORDER BY a.created_at DESC
                    LIMIT $1
                """, limit)
                return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"Error getting audit logs: {e}"
                         f"Error getting audit logs: {e}")
            return []
    
    # ============= ADMIN FUNCTIONS =============
    
    async def get_all_users(self, limit: int = 100, offset: int = 0) -> List[Dict]:
        """Get all users (admin only)"""
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
    
    async def get_user_count(self) -> int:
        """Get total user count"""
        try:
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow("SELECT COUNT(*) FROM users")
                return row[0] if row else 0
        except Exception as e:
            logger.error(f"Error getting user count: {e}")
            return 0
    
    async def get_total_balance(self) -> float:
        """Get total balance across all users"""
        try:
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow("SELECT COALESCE(SUM(balance), 0) FROM users")
                return float(row[0]) if row else 0.0
        except Exception as e:
            logger.error(f"Error getting total balance: {e}")
            return 0.0

# Global database instance
db = DatabaseManager()

# Initialize database on import
import asyncio
async def init_db():
    await db.init_pool()

# Run initialization
try:
    loop = asyncio.get_event_loop()
    if loop.is_running():
        asyncio.create_task(init_db())
    else:
        loop.run_until_complete(init_db())
except Exception as e:
    logger.warning(f"Database initialization will run on first request: {e}")
