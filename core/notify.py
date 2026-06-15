"""
Issue #21: 運用通知。Slack/Discord 等の Incoming Webhook にメッセージを送る。
NOTIFY_WEBHOOK_URL 未設定なら no-op。送信失敗してもバッチは止めない。
"""
from __future__ import annotations

import requests

from .config import get_settings


def enabled() -> bool:
    return bool(get_settings().notify_webhook_url)


def send(text: str) -> bool:
    """通知を送信。Slack({"text"}) と Discord({"content"}) の両方に対応。"""
    url = get_settings().notify_webhook_url
    if not url:
        return False
    try:
        requests.post(url, json={"text": text, "content": text}, timeout=15)
        return True
    except Exception:  # noqa: BLE001
        return False


def summarize_batch(stats: dict) -> str:
    """バッチ結果dictを通知用メッセージに整形。"""
    head = (f"📝 記事バッチ完了: 生成 {stats.get('generated', 0)} / "
            f"重複スキップ {stats.get('skipped_dup', 0)} / 失敗 {stats.get('failed', 0)}")
    lines = [head]
    for it in stats.get("items", []):
        st = it.get("status")
        if st == "selection_ng":
            lines.append(f"⛔ 選定NG: {it.get('key', '')} - {it.get('reason', '')}")
        elif st not in ("ok", "skipped_dup"):
            lines.append(f"❌ 失敗: {it.get('key', '')} - {it.get('error', st)}")
    warn_total = sum(len(it.get("warnings", [])) for it in stats.get("items", []))
    if warn_total:
        lines.append(f"⚠ 警告 計{warn_total}件（QA/取得など）")
    return "\n".join(lines)
