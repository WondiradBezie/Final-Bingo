import asyncpg
import os
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL")

class DatabaseManager:
    async def get_user(self, telegram_id: str):
        """Get user by telegram ID"""
        try:
            conn = await asyncpg.connect(DATABASE_URL)
            try:
                row = await conn.fetchrow(
                    "SELECT * FROM users WHERE telegram_id = $1",
                    telegram_id
                )
                return dict(row) if row else None
            finally:
                await conn.close()
        except Exception as e:
            logger.error(f"Error getting user: {e}")
            return None
    
    async def create_user(self, telegram_id: str, username=None, first_name=None, last_name=None):
        """Create new user"""
        try:
            conn = await asyncpg.connect(DATABASE_URL)
            try:
                row = await conn.fetchrow(
                    """INSERT INTO users 
                    (telegram_id, username, first_name, last_name, balance, total_deposits, 
                     total_withdrawals, total_wins, games_played, games_won, created_at, last_seen)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
                    RETURNING *""",
                    telegram_id, username, first_name, last_name, 0, 0, 0, 0, 0, 0,
                    datetime.now(), datetime.now()
                )
                return dict(row) if row else None
            finally:
                await conn.close()
        except Exception as e:
            logger.error(f"Error creating user: {e}")
            return None
    
    async def update_balance(self, user_id: int, amount: float, transaction_type: str, description: str = ""):
        """Update user balance"""
        try:
            conn = await asyncpg.connect(DATABASE_URL)
            try:
                async with conn.transaction():
                    # Get current user
                    user = await conn.fetchrow("SELECT * FROM users WHERE id = $1", user_id)
                    if not user:
                        return False
                    
                    new_balance = user['balance'] + amount
                    
                    # Update user balance
                    await conn.execute(
                        """UPDATE users SET 
                        balance = $1, 
                        total_deposits = total_deposits + $2,
                        total_withdrawals = total_withdrawals + $3,
                        last_seen = $4
                        WHERE id = $5""",
                        new_balance,
                        amount if amount > 0 else 0,
                        -amount if amount < 0 else 0,
                        datetime.now(),
                        user_id
                    )
                    
                    # Record transaction
                    await conn.execute(
                        """INSERT INTO transactions 
                        (user_id, type, amount, balance_after, description, created_at)
                        VALUES ($1, $2, $3, $4, $5, $6)""",
                        user_id, transaction_type, abs(amount), new_balance, description, datetime.now()
                    )
                    
                    return True
            finally:
                await conn.close()
        except Exception as e:
            logger.error(f"Error updating balance: {e}")
            return False
    
    async def get_leaderboard(self, days: int = 30, limit: int = 10):
        """Get top players"""
        try:
            conn = await asyncpg.connect(DATABASE_URL)
            try:
                rows = await conn.fetch(
                    """SELECT username, first_name, games_won, balance 
                    FROM users 
                    ORDER BY games_won DESC, balance DESC 
                    LIMIT $1""",
                    limit
                )
                
                leaderboard = []
                for row in rows:
                    leaderboard.append({
                        "username": row['username'] or row['first_name'] or "Unknown",
                        "wins": row['games_won'],
                        "winnings": row['balance']
                    })
                return leaderboard
            finally:
                await conn.close()
        except Exception as e:
            logger.error(f"Error getting leaderboard: {e}")
            return []

# Global database instance
db = DatabaseManager()
