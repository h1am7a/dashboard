import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent

# Database path
PRIEST_DB = os.getenv("PRIEST_DB", str(BASE_DIR / "db" / "life.db"))

# Auth & Telegram
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ALLOWED_USER_ID = int(os.getenv("ALLOWED_USER_ID", "483445453"))
DEV_MODE = os.getenv("DEV_MODE", "true").lower() in ("true", "1", "yes")

# App settings
HOST = os.getenv("HOST", "127.0.0.1")
PORT = int(os.getenv("PORT", "8000"))
TIMEZONE_OFFSET_HOURS = 3  # MSK is UTC+3
