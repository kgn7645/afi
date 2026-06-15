"""
Issue #4: 生成実績のスプレッドシート書き戻し。
Google Apps Script の Web App(webhook) に POST して「1記事=1行」を記録/更新する。
サービスアカウント不要・軽量（#32の公開CSVと同じ思想）。

SHEET_LOG_WEBHOOK_URL 未設定なら no-op。失敗してもパイプラインは止めない。
Apps Scriptの設置手順は docs/sheet-log.md を参照。
"""
from __future__ import annotations

import requests

from .config import get_settings


def enabled() -> bool:
    return bool(get_settings().sheet_log_webhook_url)


def _post(payload: dict, warnings: list[str] | None = None) -> bool:
    url = get_settings().sheet_log_webhook_url
    if not url:
        return False
    try:
        requests.post(url, json=payload, timeout=15)
        return True
    except Exception as e:  # noqa: BLE001
        if warnings is not None:
            warnings.append(f"スプレッドシート書き戻しに失敗（継続）: {e}")
        return False


def log_generation(*, post_id, datetime_iso: str, brand: str, category: str,
                   title: str, status: str, url: str = "",
                   warnings: list[str] | None = None) -> bool:
    """生成結果を1行upsert（post_idで突き合わせ）。"""
    return _post({
        "action": "upsert",
        "post_id": post_id or "",
        "datetime": datetime_iso,
        "brand": brand,
        "category": category,
        "title": title,
        "status": status,
        "url": url,
    }, warnings)


def log_status(post_id, status: str, warnings: list[str] | None = None) -> bool:
    """承認操作（publish/draft/trash）でステータス列だけ更新。"""
    return _post({"action": "status", "post_id": post_id, "status": status}, warnings)
