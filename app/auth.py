import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl, unquote
from fastapi import Header, HTTPException, status
from app import config


def verify_telegram_init_data(init_data_str: str) -> dict:
    """Проверяет подпись Telegram WebApp initData по HMAC-SHA256."""
    if not config.BOT_TOKEN:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="BOT_TOKEN не настроен на сервере",
        )

    try:
        parsed_data = dict(parse_qsl(init_data_str, keep_blank_values=True))
    except Exception:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Неверный формат initData")

    if "hash" not in parsed_data:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Отсутствует хеш в initData")

    received_hash = parsed_data.pop("hash")

    # Срок действия initData (не старше 24 часов)
    auth_date = int(parsed_data.get("auth_date", 0))
    if time.time() - auth_date > 86400:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Срок действия авторизации истёк")

    # Формируем строку проверки data_check_string
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(parsed_data.items()))

    # secret_key = HMAC_SHA256("WebAppData", bot_token)
    secret_key = hmac.new(b"WebAppData", config.BOT_TOKEN.encode("utf-8"), hashlib.sha256).digest()
    computed_hash = hmac.new(secret_key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()

    if not hmac.compare_digest(computed_hash, received_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Подпись Telegram не совпадает")

    user_raw = parsed_data.get("user")
    if not user_raw:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="В initData нет объекта user")

    try:
        user_data = json.loads(unquote(user_raw))
    except Exception:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Не удалось распарсить user")

    user_id = user_data.get("id")
    if user_id != config.ALLOWED_USER_ID:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Доступ запрещён")

    return user_data


def get_current_user(
    x_telegram_init_data: str | None = Header(None, alias="X-Telegram-Init-Data")
) -> dict:
    """Dependency для защиты эндпоинтов."""
    if config.DEV_MODE and not x_telegram_init_data:
        # Локальный режим разработки без Telegram WebView
        return {
            "id": config.ALLOWED_USER_ID,
            "first_name": "DevUser",
            "is_dev": True,
        }

    if not x_telegram_init_data:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Заголовок авторизации отсутствует")

    return verify_telegram_init_data(x_telegram_init_data)
