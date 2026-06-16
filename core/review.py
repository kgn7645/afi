"""
Issue #12: 承認Webアプリのロジック。
- Cookie署名トークン（依存追加なし・HMAC）
- 下書き一覧の組み立て（タイトル/抜粋/サムネ/QA結果）
ルーティングは app.py、本モジュールはテスト可能な純ロジックに集約。
"""
from __future__ import annotations

import hashlib
import hmac
import re
import time

from . import qa, wordpress
from .config import get_settings
from .models import Article, Product


def _secret() -> bytes:
    s = get_settings()
    raw = s.session_secret or (s.review_password + s.wp_app_password) or "insecure-dev"
    return raw.encode()


def make_token(ttl: int = 7 * 86400, now: float | None = None) -> str:
    """有効期限付きの署名トークンを発行。"""
    exp = str(int((now if now is not None else time.time()) + ttl))
    sig = hmac.new(_secret(), exp.encode(), hashlib.sha256).hexdigest()
    return f"{exp}.{sig}"


def valid_token(token: str, now: float | None = None) -> bool:
    try:
        exp, sig = (token or "").split(".", 1)
    except ValueError:
        return False
    good = hmac.new(_secret(), exp.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, good):
        return False
    try:
        return int(exp) > (now if now is not None else time.time())
    except ValueError:
        return False


def enabled() -> bool:
    """REVIEW_PASSWORD が設定されていれば承認画面は有効。"""
    return bool(get_settings().review_password)


def check_password(pw: str) -> bool:
    s = get_settings()
    return bool(s.review_password) and hmac.compare_digest(pw or "", s.review_password)


def _strip(html_text: str) -> str:
    return re.sub(r"<[^>]+>", "", html_text or "").strip()


STATUS_LABELS = {"draft": "承認待ち", "publish": "公開済み", "trash": "却下"}


def list_review_items(status: str = "draft") -> list[dict]:
    """指定ステータスの記事一覧（draft/publish/trash）。各件にQA件数を付与。"""
    if status not in STATUS_LABELS:
        status = "draft"
    items: list[dict] = []
    for d in wordpress.list_posts(
        statuses=status, fields="id,title,excerpt,link,featured_media,content"
    ):
        title = (d.get("title", {}) or {}).get("rendered") \
            or (d.get("title", {}) or {}).get("raw", "")
        body = (d.get("content", {}) or {}).get("raw", "")
        issues = qa.check_article(Article(title=title, body_html=body), Product())
        pm = re.search(r"<!--\s*price:(\d+)\s*-->", body)
        items.append({
            "id": d["id"],
            "title": title,
            "price": int(pm.group(1)) if pm else None,
            "excerpt": _strip((d.get("excerpt", {}) or {}).get("rendered", ""))[:120],
            "thumb": wordpress.get_media_url(d.get("featured_media") or 0),
            "errors": sum(1 for i in issues if i["level"] == "error"),
            "warns": sum(1 for i in issues if i["level"] == "warn"),
            "qa": issues,
            "link": d.get("link", ""),
            "status": status,
        })
    return items


def get_preview(post_id: int) -> dict:
    """1件の下書きプレビュー（タイトル＋本文HTML＋QA）。"""
    p = wordpress.get_post(post_id, fields="id,title,content,link")
    title = (p.get("title", {}) or {}).get("rendered") \
        or (p.get("title", {}) or {}).get("raw", "")
    body = (p.get("content", {}) or {}).get("raw", "")
    issues = qa.check_article(Article(title=title, body_html=body), Product())
    return {"id": post_id, "title": title, "body": body, "qa": issues}
