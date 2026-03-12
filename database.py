from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy import select, update, delete, and_, or_
from datetime import datetime, timedelta
import json
from typing import Optional, List, Dict
from models import *
import logging

logger = logging.getLogger(__name__)

# Async database URL (for PostgreSQL)
ASYNC_DATABASE_URL = "postgresql+asyncpg://user:pass@localhost/bingo"

class DatabaseManager:
    def __init__(self):
        self.engine = create_async_engine(
            ASYNC_DATABASE_URL,
            echo=False,
            pool_size=20,
            max_overflow=40
        )
        self.async_session = sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=False
        )
    
    async def get_user(self, telegram_id: str) -> Optional[User]:
        """Get user by telegram ID"""
        async with self.async_session() as session:
            result = await session.execute(
                select(User).where(User.telegram_id == str(telegram_id))
            )
            return result.scalar_one_or_none()
    
    async def create_user(self, telegram_id: str, **kwargs) -> User:
        """Create new user"""
        async with self.async_session() as session:
            user = User(telegram_id=str(telegram_id), **kwargs)
            session.add(user)
            await session.commit()
            await session.refresh(user)
            return user
    
    async def update_balance(self, user_id: int, amount: float, 
                            transaction_type: str, description: str = "") -> bool:
        """Update user balance with transaction record"""
        async with self.async_session() as session:
            try:
                # Get user with lock
                result = await session.execute(
                    select(User).where(User.id == user_id).with_for_update()
                )
                user = result.scalar_one_or_none()
                
                if not user:
                    return False
                
                # Update balance
                old_balance = user.balance
                user.balance += amount
                
                # Create transaction record
                transaction = Transaction(
                    user_id=user_id,
                    type=transaction_type,
                    amount=abs(amount),
                    balance_after=user.balance,
                    description=description,
                    reference=f"{transaction_type}_{datetime.utcnow().timestamp()}"
                )
                
                session.add(transaction)
                
                # Update totals based on type
                if amount > 0:
                    if transaction_type == 'win':
                        user.total_wins += amount
                    elif transaction_type == 'deposit':
                        user.total_deposits += amount
                else:
                    if transaction_type == 'withdrawal':
                        user.total_withdrawals += abs(amount)
                
                user.last_seen = datetime.utcnow()
                
                await session.commit()
                
                logger.info(f"Balance updated for user {user_id}: {old_balance} -> {user.balance}")
                return True
                
            except Exception as e:
                logger.error(f"Error updating balance: {e}")
                await session.rollback()
                return False
    
    async def create_game(self, room_id: str, **settings) -> Game:
        """Create new game"""
        async with self.async_session() as session:
            game = Game(
                game_id=f"{room_id}_{int(datetime.utcnow().timestamp())}",
                room_id=room_id,
                status='waiting',
                **settings
            )
            session.add(game)
            await session.commit()
            await session.refresh(game)
            return game
    
    async def add_player_to_game(self, game_id: int, user_id: int, 
                                 card_number: str, card_data: dict) -> bool:
        """Add player to game"""
        async with self.async_session() as session:
            try:
                # Check if player already in game
                result = await session.execute(
                    select(GamePlayer).where(
                        and_(GamePlayer.game_id == game_id, 
                             GamePlayer.user_id == user_id)
                    )
                )
                if result.scalar_one_or_none():
                    return False
                
                # Add player
                game_player = GamePlayer(
                    game_id=game_id,
                    user_id=user_id,
                    card_number=card_number,
                    card_data=card_data
                )
                session.add(game_player)
                
                # Update game total bet
                game = await session.get(Game, game_id)
                if game:
                    game.total_bet += game.card_price
                    game.prize_pool = game.total_bet * game.prize_percentage / 100
                    game.commission = game.total_bet - game.prize_pool
                
                await session.commit()
                return True
                
            except Exception as e:
                logger.error(f"Error adding player to game: {e}")
                await session.rollback()
                return False
    
    async def get_active_games(self, limit: int = 10) -> List[Game]:
        """Get active games"""
        async with self.async_session() as session:
            result = await session.execute(
                select(Game).where(
                    Game.status.in_(['waiting', 'active'])
                ).order_by(Game.created_at.desc()).limit(limit)
            )
            return result.scalars().all()
    
    async def get_leaderboard(self, days: int = 7, limit: int = 10) -> List[Dict]:
        """Get leaderboard for last N days"""
        async with self.async_session() as session:
            cutoff = datetime.utcnow() - timedelta(days=days)
            
            result = await session.execute(
                select(
                    User.telegram_id,
                    User.username,
                    func.sum(Transaction.amount).label('total_winnings'),
                    func.count(GamePlayer.id).label('games_won')
                )
                .join(Transaction)
                .join(GamePlayer)
                .where(
                    and_(
                        Transaction.type == 'win',
                        Transaction.created_at >= cutoff
                    )
                )
                .group_by(User.id)
                .order_by(func.sum(Transaction.amount).desc())
                .limit(limit)
            )
            
            return [{
                'user_id': r[0],
                'username': r[1],
                'winnings': float(r[2]),
                'wins': r[3]
            } for r in result]

# Global database instance
db = DatabaseManager()