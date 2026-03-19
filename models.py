from sqlalchemy import create_engine, Column, Integer, String, Float, Boolean, DateTime, ForeignKey, JSON, Text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship, sessionmaker
from datetime import datetime
import json

Base = declarative_base()

class User(Base):
    __tablename__ = 'users'
    
    id = Column(Integer, primary_key=True)
    telegram_id = Column(String(50), unique=True, index=True)
    username = Column(String(100))
    first_name = Column(String(100))
    last_name = Column(String(100))
    
    # Wallet
    balance = Column(Float, default=0.0)
    total_deposits = Column(Float, default=0.0)
    total_withdrawals = Column(Float, default=0.0)
    total_wins = Column(Float, default=0.0)
    
    # Stats
    games_played = Column(Integer, default=0)
    games_won = Column(Integer, default=0)
    bingos_called = Column(Integer, default=0)
    
    # Status
    is_active = Column(Boolean, default=True)
    is_admin = Column(Boolean, default=False)
    is_banned = Column(Boolean, default=False)
    
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    last_seen = Column(DateTime, default=datetime.utcnow)
    last_game = Column(DateTime)
    
    # Relationships
    transactions = relationship("Transaction", back_populates="user")
    game_players = relationship("GamePlayer", back_populates="user")
    
    def to_dict(self):
        return {
            'id': self.telegram_id,
            'username': self.username,
            'balance': self.balance,
            'games_played': self.games_played,
            'games_won': self.games_won,
            'win_rate': (self.games_won / self.games_played * 100) if self.games_played > 0 else 0
        }

class Transaction(Base):
    __tablename__ = 'transactions'
    
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'))
    type = Column(String(20))  # deposit, withdrawal, bet, win, refund
    amount = Column(Float)
    balance_after = Column(Float)
    status = Column(String(20), default='completed')  # pending, completed, failed
    reference = Column(String(100))
    description = Column(Text)
    
    created_at = Column(DateTime, default=datetime.utcnow)
    
    user = relationship("User", back_populates="transactions")

class Game(Base):
    __tablename__ = 'games'
    
    id = Column(Integer, primary_key=True)
    game_id = Column(String(50), unique=True, index=True)
    room_id = Column(String(50), index=True)
    status = Column(String(20))  # waiting, active, finished, cancelled
    
    # Settings
    card_price = Column(Float, default=10.0)
    prize_percentage = Column(Integer, default=80)
    min_players = Column(Integer, default=2)
    max_players = Column(Integer, default=400)
    
    # Game state
    called_numbers = Column(JSON, default=list)
    winners = Column(JSON, default=list)
    
    # Financial
    total_bet = Column(Float, default=0.0)
    prize_pool = Column(Float, default=0.0)
    commission = Column(Float, default=0.0)
    
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    started_at = Column(DateTime)
    finished_at = Column(DateTime)
    
    # Relationships
    players = relationship("GamePlayer", back_populates="game")
    
    def to_dict(self):
        return {
            'game_id': self.game_id,
            'room_id': self.room_id,
            'status': self.status,
            'players': len(self.players),
            'max_players': self.max_players,
            'prize_pool': self.prize_pool,
            'called_numbers': self.called_numbers[-10:] if self.called_numbers else []
        }

class GamePlayer(Base):
    __tablename__ = 'game_players'
    
    id = Column(Integer, primary_key=True)
    game_id = Column(Integer, ForeignKey('games.id'))
    user_id = Column(Integer, ForeignKey('users.id'))
    
    card_number = Column(String(10))
    card_data = Column(JSON)  # The actual bingo card
    marked_numbers = Column(JSON, default=list)
    
    bingo_called = Column(Boolean, default=False)
    bingo_time = Column(DateTime)
    
    win_amount = Column(Float, default=0.0)
    is_winner = Column(Boolean, default=False)
    
    joined_at = Column(DateTime, default=datetime.utcnow)
    
    game = relationship("Game", back_populates="players")
    user = relationship("User", back_populates="game_players")

class Room(Base):
    __tablename__ = 'rooms'
    
    id = Column(Integer, primary_key=True)
    room_id = Column(String(50), unique=True, index=True)
    name = Column(String(100))
    description = Column(Text)
    
    # Settings
    card_price = Column(Float)
    prize_percentage = Column(Integer)
    call_interval = Column(Float, default=2.0)
    selection_time = Column(Integer, default=20)
    
    # Status
    is_active = Column(Boolean, default=True)
    current_game_id = Column(String(50))
    
    created_at = Column(DateTime, default=datetime.utcnow)

class AuditLog(Base):
    __tablename__ = 'audit_logs'
    
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'))
    action = Column(String(50))
    details = Column(JSON)
    ip_address = Column(String(50))
    user_agent = Column(Text)
    
    created_at = Column(DateTime, default=datetime.utcnow)

# Database setup
DATABASE_URL = "sqlite:///./data/database.db"  # Change to PostgreSQL in production

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine)

def init_db():
    Base.metadata.create_all(bind=engine)