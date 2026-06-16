"""
LINE Messaging API クライアント（最小）。
公式アカウントにAmazon商品を共有→選定リストに追加する用途。

要: LINE_CHANNEL_SECRET（署名検証）/ LINE_CHANNEL_ACCESS_TOKEN（返信）。
"""
from __future__ import annotations

import base64
import hashlib
import hmac

import requests

from .config import get_settings

_REPLY_URL = "https://api.line.me/v2/bot/message/reply"


def enabled() -> bool:
    s = get_settings()
    return bool(s.line_channel_secret and s.line_channel_access_token)


def verify(body: bytes, signature: str) -> bool:
    """X-Line-Signature を検証（HMAC-SHA256 + base64）。"""
    secret = get_settings().line_channel_secret
    if not secret or not signature:
        return False
    mac = hmac.new(secret.encode(), body, hashlib.sha256).digest()
    expected = base64.b64encode(mac).decode()
    return hmac.compare_digest(expected, signature)


def allowed(user_id: str) -> bool:
    """許可ユーザー設定があればその範囲のみ。空なら全員許可（個人運用）。"""
    allow = get_settings().line_allowed_user_ids
    return (not allow) or (user_id in allow)


def reply(reply_token: str, text: str, *, timeout: int = 10) -> None:
    """返信メッセージを送る（失敗は握りつぶす）。"""
    token = get_settings().line_channel_access_token
    if not (token and reply_token):
        return
    try:
        requests.post(
            _REPLY_URL,
            headers={"Authorization": f"Bearer {token}",
                     "Content-Type": "application/json"},
            json={"replyToken": reply_token,
                  "messages": [{"type": "text", "text": text[:1900]}]},
            timeout=timeout)
    except Exception:  # noqa: BLE001
        pass
