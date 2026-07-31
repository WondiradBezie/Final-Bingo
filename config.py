"""Validated application configuration."""
import os
from dotenv import load_dotenv

load_dotenv()


def required(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Required environment variable {name} is missing")
    return value


BOT_TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = required("DATABASE_URL")
REDIS_URL = os.getenv("REDIS_URL")
WEBAPP_URL = required("WEBAPP_URL")
PORT = int(os.getenv("PORT", "8000"))

CARD_PRICE = 10
PRIZE_PERCENT = 80
MIN_PLAYERS = 2
MAX_PLAYERS = 400
CALL_INTERVAL = float(os.getenv("CALL_INTERVAL", "2"))
SELECTION_TIME = int(os.getenv("SELECTION_TIME", "20"))
MIN_WITHDRAWAL = 100
MAX_DEPOSIT = 10000

raw_admin_ids = os.getenv("ADMIN_IDS", "")
ADMIN_IDS = [int(x.strip()) for x in raw_admin_ids.split(",") if x.strip().isdigit()]
if not ADMIN_IDS:
    raise RuntimeError("ADMIN_IDS must contain at least one Telegram user ID")

ADMIN_SECRET_KEY = required("ADMIN_SECRET_KEY")
ADMIN_PASSWORD = required("ADMIN_PASSWORD")
ALLOWED_ORIGINS = [x.strip() for x in os.getenv("ALLOWED_ORIGINS", WEBAPP_URL).split(",") if x.strip()]
