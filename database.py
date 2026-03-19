
from supabase import create_client
import os
from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv()

url: str = os.environ.get("SUPABASE_URL")
key: str = os.environ.get("SUPABASE_KEY")
supabase: Client = create_client(url, key)


supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

def save_player(player_id, card_id):
    return supabase.table("players").insert({
        "player_id": player_id,
        "card_id": card_id
    }).execute()
