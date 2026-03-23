# config.py
import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
REDIS_URL = os.getenv("REDIS_URL")
WEBAPP_URL = os.getenv("WEBAPP_URL")
PORT = int(os.getenv("PORT"))

CARD_PRICE = 10
PRIZE_PERCENT = 80
MIN_PLAYERS = 2
MAX_PLAYERS = 400
CALL_INTERVAL = 2
SELECTION_TIME = 20
