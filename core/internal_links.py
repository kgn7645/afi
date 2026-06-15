"""
Issue #18: 内部リンク（関連記事）の自動挿入・相互リンク更新。
- 生成時: 同カテゴリの公開記事への「あわせて読みたい」を本文末尾に付与
- 公開時: 同カテゴリ全記事の関連ブロックを再計算して相互リンク化（被リンク更新）

マーカー <!-- related-links --> ... <!-- /related-links --> で囲み、
再計算時は古いブロックを置換するので重複しない。
"""
from __future__ import annotations

import html
import re

from . import wordpress
from .config import get_rules

_START = "<!-- related-links -->"
_END = "<!-- /related-links -->"
_BLOCK_RE = re.compile(re.escape(_START) + r".*?" + re.escape(_END) + r"\n?", re.S)


def _cfg() -> dict:
    return get_rules().get("internal_links", {})


def enabled() -> bool:
    return _cfg().get("enabled", True)


def _count() -> int:
    return int(_cfg().get("count", 4))


def build_block(related: list[dict]) -> str:
    """関連記事ブロックHTML（マーカー付き）。relatedが空なら空文字。"""
    if not related:
        return ""
    items = "".join(
        f'<li><a href="{html.escape(r["link"])}">{html.escape(r["title"])}</a></li>'
        for r in related)
    return (
        f"\n{_START}\n"
        '<div class="related-posts" style="margin:32px 0;padding:18px 20px;'
        'background:#faf7f0;border-radius:12px;">\n'
        '<p style="font-weight:bold;margin:0 0 10px;">🔗 あわせて読みたい</p>\n'
        f'<ul style="margin:0;padding-left:18px;line-height:2;">{items}</ul>\n'
        f"</div>\n{_END}\n"
    )


def strip_block(body: str) -> str:
    return _BLOCK_RE.sub("", body or "")


def upsert_block(body: str, related: list[dict]) -> str:
    """既存の関連ブロックを除去して付け直す（重複防止）。"""
    base = strip_block(body).rstrip()
    block = build_block(related)
    return (base + "\n" + block) if block else base


def _pick(posts: list[dict], exclude_id: int, limit: int) -> list[dict]:
    return [p for p in posts if p.get("id") != exclude_id][:limit]


def add_related(body: str, category_id: int, result=None, *, exclude_id: int = 0) -> str:
    """生成時: 同カテゴリの公開記事への関連リンクを本文に付与して返す。"""
    if not enabled() or not category_id:
        return body
    try:
        posts = wordpress.posts_in_category(category_id)
    except Exception as e:  # noqa: BLE001
        if result is not None:
            result.warnings.append(f"内部リンク取得に失敗（スキップ）: {e}")
        return body
    related = _pick(posts, exclude_id, _count())
    return upsert_block(body, related) if related else body


def refresh_after_publish(post_id: int) -> int:
    """公開時: 同カテゴリ全公開記事の関連ブロックを再計算して相互リンク化。

    更新した記事数を返す（被リンクが付く）。失敗は握りつぶす。
    """
    if not enabled():
        return 0
    try:
        post = wordpress.get_post(post_id, fields="id,categories")
        cats = post.get("categories", [])
        if not cats:
            return 0
        posts = wordpress.posts_in_category(cats[0], with_content=True)
        updated = 0
        for p in posts:
            related = _pick(posts, p["id"], _count())
            new_body = upsert_block(p.get("content", ""), related)
            if new_body.strip() != (p.get("content", "") or "").strip():
                wordpress.update_post_content(p["id"], new_body)
                updated += 1
        return updated
    except Exception:  # noqa: BLE001
        return 0
