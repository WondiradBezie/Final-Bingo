
from supabase import create_client
import os

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

def save_player(player_id, card_id):
    return supabase.table("players").insert({
        "player_id": player_id,
        "card_id": card_id
    }).execute()
