# database.py - JSON version (NO Supabase)
import json
import os
import random
import string
from datetime import datetime

class Database:
    def __init__(self):
        self.users_file = "users.json"
        self.pending_file = "pending_requests.json"
        self.load_data()
    
    def load_data(self):
        # Load users
        if os.path.exists(self.users_file):
            with open(self.users_file, 'r') as f:
                self.users = json.load(f)
        else:
            self.users = {}
            self.save_users()
        
        # Load pending requests
        if os.path.exists(self.pending_file):
            with open(self.pending_file, 'r') as f:
                self.pending = json.load(f)
        else:
            self.pending = {"deposits": [], "withdrawals": []}
            self.save_pending()
    
    def save_users(self):
        with open(self.users_file, 'w') as f:
            json.dump(self.users, f, indent=2)
    
    def save_pending(self):
        with open(self.pending_file, 'w') as f:
            json.dump(self.pending, f, indent=2)
    
    def get_user(self, user_id):
        user_id = str(user_id)
        if user_id not in self.users:
            self.users[user_id] = {
                "name": "",
                "phone": "",
                "balance": 0.0,
                "wallet": 0.0,
                "registered": False,
                "total_wins": 0,
                "total_earnings": 0.0,
                "is_locked": False,
                "current_game_card": None,
                "game_token": None
            }
            self.save_users()
        return self.users[user_id]
    
    def update_balance(self, user_id, amount, transaction_type, description):
        user_id = str(user_id)
        if user_id in self.users:
            self.users[user_id]["balance"] += amount
            self.users[user_id]["wallet"] = self.users[user_id]["balance"]
            self.save_users()
            return True
        return False
    
    def generate_game_token(self, user_id):
        import time
        token = f"{user_id}_{int(time.time())}_{random.randint(1000, 9999)}"
        self.get_user(user_id)["game_token"] = token
        self.save_users()
        return token
    
    def add_pending_deposit(self, user_id, amount, payment_method, transaction_id):
        request = {
            "id": len(self.pending["deposits"]) + 1,
            "user_id": str(user_id),
            "user_name": self.users.get(str(user_id), {}).get("name", "Unknown"),
            "user_phone": self.users.get(str(user_id), {}).get("phone", "Unknown"),
            "amount": amount,
            "payment_method": payment_method,
            "transaction_id": transaction_id,
            "timestamp": datetime.now().isoformat(),
            "status": "pending"
        }
        self.pending["deposits"].append(request)
        self.save_pending()
        return request
    
    def add_pending_withdrawal(self, user_id, amount, bank_info):
        request = {
            "id": len(self.pending["withdrawals"]) + 1,
            "user_id": str(user_id),
            "user_name": self.users.get(str(user_id), {}).get("name", "Unknown"),
            "user_phone": self.users.get(str(user_id), {}).get("phone", "Unknown"),
            "amount": amount,
            "bank_info": bank_info,
            "timestamp": datetime.now().isoformat(),
            "status": "pending"
        }
        self.pending["withdrawals"].append(request)
        self.save_pending()
        return request
    
    def approve_deposit(self, request_id):
        for request in self.pending["deposits"]:
            if request["id"] == request_id and request["status"] == "pending":
                request["status"] = "approved"
                request["approved_at"] = datetime.now().isoformat()
                user_id = request["user_id"]
                amount = request["amount"]
                self.update_balance(user_id, amount, "deposit", f"Deposit approved: {amount} Birr")
                self.save_pending()
                return request
        return None
    
    def reject_deposit(self, request_id, reason=""):
        for request in self.pending["deposits"]:
            if request["id"] == request_id and request["status"] == "pending":
                request["status"] = "rejected"
                request["rejected_at"] = datetime.now().isoformat()
                request["rejection_reason"] = reason
                self.save_pending()
                return request
        return None
    
    def approve_withdrawal(self, request_id):
        for request in self.pending["withdrawals"]:
            if request["id"] == request_id and request["status"] == "pending":
                request["status"] = "approved"
                request["approved_at"] = datetime.now().isoformat()
                self.save_pending()
                return request
        return None
    
    def reject_withdrawal(self, request_id, reason=""):
        for request in self.pending["withdrawals"]:
            if request["id"] == request_id and request["status"] == "pending":
                request["status"] = "rejected"
                request["rejected_at"] = datetime.now().isoformat()
                request["rejection_reason"] = reason
                user_id = request["user_id"]
                amount = request["amount"]
                self.update_balance(user_id, amount, "refund", f"Withdrawal rejected, refund: {amount} Birr")
                self.save_pending()
                return request
        return None
    
    def get_pending_deposits(self):
        return [r for r in self.pending["deposits"] if r["status"] == "pending"]
    
    def get_pending_withdrawals(self):
        return [r for r in self.pending["withdrawals"] if r["status"] == "pending"]
    
    def get_user_pending_requests(self, user_id):
        user_id = str(user_id)
        deposits = [r for r in self.pending["deposits"] 
                   if r["user_id"] == user_id and r["status"] == "pending"]
        withdrawals = [r for r in self.pending["withdrawals"] 
                      if r["user_id"] == user_id and r["status"] == "pending"]
        return deposits, withdrawals

db = Database()
