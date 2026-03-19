# models.py - Data models for the application

class User:
    """User model class"""
    def __init__(self, id, username=None, first_name=None, last_name=None, balance=0):
        self.id = id
        self.username = username
        self.first_name = first_name
        self.last_name = last_name
        self.balance = balance
        self.is_admin = False
        self.is_locked = False
    
    def to_dict(self):
        """Convert to dictionary"""
        return {
            'id': self.id,
            'username': self.username,
            'first_name': self.first_name,
            'last_name': self.last_name,
            'balance': self.balance,
            'is_admin': self.is_admin,
            'is_locked': self.is_locked
        }
    
    @classmethod
    def from_dict(cls, data):
        """Create from dictionary"""
        user = cls(
            id=data.get('id'),
            username=data.get('username'),
            first_name=data.get('first_name'),
            last_name=data.get('last_name'),
            balance=data.get('balance', 0)
        )
        user.is_admin = data.get('is_admin', False)
        user.is_locked = data.get('is_locked', False)
        return user


class Transaction:
    """Transaction model class"""
    def __init__(self, id, user_id, amount, type, status, timestamp=None):
        self.id = id
        self.user_id = user_id
        self.amount = amount
        self.type = type  # 'deposit', 'withdrawal', 'win', 'game'
        self.status = status  # 'pending', 'approved', 'rejected'
        self.timestamp = timestamp
    
    def to_dict(self):
        """Convert to dictionary"""
        return {
            'id': self.id,
            'user_id': self.user_id,
            'amount': self.amount,
            'type': self.type,
            'status': self.status,
            'timestamp': self.timestamp
        }
    
    @classmethod
    def from_dict(cls, data):
        """Create from dictionary"""
        return cls(
            id=data.get('id'),
            user_id=data.get('user_id'),
            amount=data.get('amount'),
            type=data.get('type'),
            status=data.get('status'),
            timestamp=data.get('timestamp')
        )


class Game:
    """Game model class"""
    def __init__(self, id, number, phase, players=None):
        self.id = id
        self.number = number
        self.phase = phase  # 'selection', 'playing', 'win_display'
        self.players = players or {}
        self.called_numbers = []
        self.winner = None
        self.winning_card = None
        self.prize_pool = 0
    
    def to_dict(self):
        """Convert to dictionary"""
        return {
            'id': self.id,
            'number': self.number,
            'phase': self.phase,
            'players': self.players,
            'called_numbers': self.called_numbers,
            'winner': self.winner,
            'winning_card': self.winning_card,
            'prize_pool': self.prize_pool
        }
