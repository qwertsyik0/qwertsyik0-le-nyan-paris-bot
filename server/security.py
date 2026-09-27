from __future__ import annotations

import hashlib
import hmac
import json
import time
import urllib.parse
from typing import Any

from fastapi import HTTPException

MAX_INIT_DATA_AGE_SECONDS = 60 * 60 * 24


def validate_webapp_init_data(init_data: str, bot_token: str) -> dict[str, Any]:
    if not init_data:
        raise HTTPException(status_code=401, detail="Mini App initData is empty")

    pairs = urllib.parse.parse_qsl(init_data, keep_blank_values=True)
    data = dict(pairs)
    received_hash = data.pop("hash", None)
    if not received_hash:
        raise HTTPException(status_code=401, detail="Mini App hash is missing")

    data_check_string = "\n".join(f"{key}={value}" for key, value in sorted(data.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()
    calculated_hash = hmac.new(secret_key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calculated_hash, received_hash):
        raise HTTPException(status_code=401, detail="Mini App initData is invalid")

    auth_date_raw = data.get("auth_date")
    if auth_date_raw:
        try:
            auth_date = int(auth_date_raw)
        except ValueError as exc:
            raise HTTPException(status_code=401, detail="Mini App auth_date is invalid") from exc
        if time.time() - auth_date > MAX_INIT_DATA_AGE_SECONDS:
            raise HTTPException(status_code=401, detail="Mini App initData expired")

    raw_user = data.get("user")
    if not raw_user:
        raise HTTPException(status_code=401, detail="Mini App user is missing")
    try:
        user = json.loads(raw_user)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=401, detail="Mini App user is invalid") from exc
    if not isinstance(user, dict) or not user.get("id"):
        raise HTTPException(status_code=401, detail="Mini App user id is missing")
    return user
