"""
Issue #3/#12: 商品候補プールのストア・クライアント。
Xserverのクローラ(書込)とRenderのスワイプUI(読み書き)が共有するため、
Google Apps Script Web App(=スプレッドシート) を介する（#4/#32と同じ思想）。

CANDIDATES_WEBHOOK_URL 未設定なら no-op / 空。失敗してもクロールは止めない。

ステータス遷移: pending(クロール直後) → approved(スワイプ承認) / rejected(却下)
              → queued(生成キュー投入) → generated(記事化済み)
"""
from __future__ import annotations

import requests

from .config import get_settings


def enabled() -> bool:
    return bool(get_settings().candidates_webhook_url)


def _url() -> str:
    return get_settings().candidates_webhook_url


def push(candidates: list[dict]) -> bool:
    """候補をまとめて追加（Apps Script側でASIN重複は無視）。"""
    if not _url() or not candidates:
        return False
    try:
        requests.post(_url(), json={"action": "append", "candidates": candidates}, timeout=20)
        return True
    except Exception:  # noqa: BLE001
        return False


def set_status(asin: str, status: str) -> bool:
    if not _url():
        return False
    try:
        requests.post(_url(), json={"action": "status", "asin": asin, "status": status},
                      timeout=15)
        return True
    except Exception:  # noqa: BLE001
        return False


def list_by_status(status: str = "pending", *, limit: int = 100) -> list[dict]:
    """指定ステータスの候補を取得（スワイプUI・バッチが利用）。"""
    if not _url():
        return []
    try:
        r = requests.get(_url(), params={"status": status, "limit": limit}, timeout=20)
        r.raise_for_status()
        data = r.json()
        return data.get("items", data) if isinstance(data, dict) else data
    except Exception:  # noqa: BLE001
        return []


def known_asins() -> set[str]:
    """既に登録済みの全ASIN（クロールの重複除外用）。"""
    if not _url():
        return set()
    try:
        r = requests.get(_url(), params={"status": "all", "limit": 1000}, timeout=20)
        r.raise_for_status()
        data = r.json()
        items = data.get("items", data) if isinstance(data, dict) else data
        return {it.get("asin", "") for it in items if it.get("asin")}
    except Exception:  # noqa: BLE001
        return set()
