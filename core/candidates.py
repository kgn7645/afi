"""
Issue #3/#12: 商品候補プールのストア・クライアント。
Xserverのクローラ(書込)とRenderのスワイプUI(読み書き)が共有するため、
Google Apps Script Web App(=スプレッドシート) を介する（#4/#32と同じ思想）。

CANDIDATES_WEBHOOK_URL 未設定なら no-op / 空。失敗してもクロールは止めない。

ステータス遷移: pending(クロール直後) → approved(スワイプ承認) / rejected(却下)
              → queued(生成キュー投入) → generated(記事化済み)
"""
from __future__ import annotations

import time

import requests

from .config import get_settings

# 候補プール読み取りの短時間キャッシュ（Render Web UIの体感改善・Issue #103）。
# 書き込み(set_status/push)でクリアするので、スワイプ操作後は即反映される。
_CACHE_TTL = 10.0
_cache: dict = {}


def _cache_get(key):
    v = _cache.get(key)
    if v and time.time() - v[0] < _CACHE_TTL:
        return v[1]
    return None


def _cache_put(key, data) -> None:
    _cache[key] = (time.time(), data)


def _cache_clear() -> None:
    _cache.clear()


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
        _cache_clear()
        return True
    except Exception:  # noqa: BLE001
        return False


def set_status(asin: str, status: str) -> bool:
    if not _url():
        return False
    try:
        requests.post(_url(), json={"action": "status", "asin": asin, "status": status},
                      timeout=15)
        _cache_clear()
        return True
    except Exception:  # noqa: BLE001
        return False


def list_by_status(status: str = "pending", *, limit: int = 100) -> list[dict]:
    """指定ステータスの候補を取得（スワイプUI・バッチが利用）。10秒キャッシュ。"""
    if not _url():
        return []
    ck = ("list", status, limit)
    cached = _cache_get(ck)
    if cached is not None:
        return cached
    try:
        r = requests.get(_url(), params={"status": status, "limit": limit}, timeout=15)
        r.raise_for_status()
        data = r.json()
        items = data.get("items", data) if isinstance(data, dict) else data
        _cache_put(ck, items)
        return items
    except Exception:  # noqa: BLE001
        return []


def known_asins() -> set[str]:
    """既に登録済みの全ASIN（クロールの重複除外用）。"""
    if not _url():
        return set()
    cached = _cache_get(("known",))
    if cached is not None:
        return cached
    try:
        r = requests.get(_url(), params={"status": "all", "limit": 1000}, timeout=20)
        r.raise_for_status()
        data = r.json()
        items = data.get("items", data) if isinstance(data, dict) else data
        known = {it.get("asin", "") for it in items if it.get("asin")}
        _cache_put(("known",), known)
        return known
    except Exception:  # noqa: BLE001
        return set()
