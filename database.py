from supabase import create_client
import os
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

# FIX 3: Correct Supabase initialization
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    logger.error("❌ SUPABASE_URL or SUPABASE_KEY not set in environment variables")
    supabase = None
else:
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    logger.info("✅ Supabase client initialized")

class DatabaseManager:
    async def get_user(self, telegram_id: str):
        """Get user by telegram ID"""
        try:
            if not supabase:
                return None
            result = supabase.table("users").select("*").eq("telegram_id", telegram_id).execute()
            if result.data and len(result.data) > 0:
                return result.data[0]
            return None
        except Exception as e:
            logger.error(f"Error getting user: {e}")
            return None
    
    async def create_user(self, telegram_id: str, username=None, first_name=None, last_name=None):
        """Create new user"""
        try:
            if not supabase:
                return None
            user_data = {
                "telegram_id": telegram_id,
                "username": username,
                "first_name": first_name,
                "last_name": last_name,
                "balance": 0,
                "total_deposits": 0,
                "total_withdrawals": 0,
                "total_wins": 0,
                "games_played": 0,
                "games_won": 0,
                "created_at": datetime.now().isoformat(),
                "last_seen": datetime.now().isoformat()
            }
            result = supabase.table("users").insert(user_data).execute()
            if result.data and len(result.data) > 0:
                return result.data[0]
            return None
        except Exception as e:
            logger.error(f"Error creating user: {e}")
            return None
    
    async def update_balance(self, user_id: int, amount: float, transaction_type: str, description: str = ""):
        """Update user balance"""
        try:
            if not supabase:
                return False
            
            # Get current user
            user_result = supabase.table("users").select("*").eq("id", user_id).execute()
            if not user_result.data or len(user_result.data) == 0:
                return False
            
            user = user_result.data[0]
            new_balance = user["balance"] + amount
            
            # Update user balance
            update_data = {
                "balance": new_balance,
                "last_seen": datetime.now().isoformat()
            }
            
            if amount > 0:
                update_data["total_deposits"] = user.get("total_deposits", 0) + amount
            else:
                update_data["total_withdrawals"] = user.get("total_withdrawals", 0) + abs(amount)
            
            supabase.table("users").update(update_data).eq("id", user_id).execute()
            
            # Record transaction
            transaction_data = {
                "user_id": user_id,
                "type": transaction_type,
                "amount": abs(amount),
                "balance_after": new_balance,
                "description": description,
                "created_at": datetime.now().isoformat()
            }
            supabase.table("transactions").insert(transaction_data).execute()
            
            return True
        except Exception as e:
            logger.error(f"Error updating balance: {e}")
            return False
    
    async def get_leaderboard(self, days: int = 30, limit: int = 10):
        """Get top players"""
        try:
            if not supabase:
                return []
            result = supabase.table("users") \
                .select("username, first_name, games_won, balance") \
                .order("games_won", desc=True) \
                .limit(limit) \
                .execute()
            
            leaderboard = []
            for row in result.data:
                leaderboard.append({
                    "username": row.get("username") or row.get("first_name", "Unknown"),
                    "wins": row.get("games_won", 0),
                    "winnings": row.get("balance", 0)
                })
            return leaderboard
        except Exception as e:
            logger.error(f"Error getting leaderboard: {e}")
            return []

# Global database instance
db = DatabaseManager()
