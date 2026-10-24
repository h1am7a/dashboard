import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl, unquote
from fastapi import Header, HTTPException, status
from app import config


def verify_telegram_init_data(init_data_str: str) -> dict:
    """Validate Telegram WebApp initData HMAC-SHA256 signature."""
    if not config.BOT_TOKEN:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="BOT_TOKEN is not configured on the server",
        )

    try:
        parsed_data = dict(parse_qsl(init_data_str, keep_blank_values=True))
    except Exception:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid initData format")

    if "hash" not in parsed_data:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing hash in initData")

    received_hash = parsed_data.pop("hash")

    # initData expiration check (maximum 24 hours)
    auth_date = int(parsed_data.get("auth_date", 0))
    if time.time() - auth_date > 86400:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authorization expired")

    # Construct verification string
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(parsed_data.items()))

    # secret_key = HMAC_SHA256("WebAppData", bot_token)
    secret_key = hmac.new(b"WebAppData", config.BOT_TOKEN.encode("utf-8"), hashlib.sha256).digest()
    computed_hash = hmac.new(secret_key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()

    if not hmac.compare_digest(computed_hash, received_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Telegram signature mismatch")

    user_raw = parsed_data.get("user")
    if not user_raw:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing user object in initData")

    try:
        user_data = json.loads(unquote(user_raw))
    except Exception:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Failed to parse user JSON")

    user_id = user_data.get("id")
    if user_id != config.ALLOWED_USER_ID:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access forbidden")

    return user_data


def get_current_user(
    x_telegram_init_data: str | None = Header(None, alias="X-Telegram-Init-Data")
) -> dict:
    """Dependency for endpoint authorization."""
    if config.DEV_MODE and not x_telegram_init_data:
        # Local development mode without Telegram WebView
        return {
            "id": config.ALLOWED_USER_ID,
            "first_name": "DevUser",
            "is_dev": True,
        }

    if not x_telegram_init_data:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing authorization header")

    return verify_telegram_init_data(x_telegram_init_data)
