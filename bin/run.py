#!/usr/bin/env python3
"""Run FastAPI server for local development and testing of Telegram Mini App."""
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

import uvicorn
from app import config

if __name__ == "__main__":
    print(f"Starting Priest Life Mini App at http://{config.HOST}:{config.PORT}")
    print(f"DEV_MODE: {config.DEV_MODE}")
    print(f"Database: {config.PRIEST_DB}")
    uvicorn.run("app.main:app", host=config.HOST, port=config.PORT, reload=True)
