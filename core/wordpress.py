"""
E作業: WordPress REST API で投稿（既定は下書き）。
認証はアプリケーションパスワード（Basic認証）。
SEOメタはRank Math / Yoast のメタキーに対応。
"""
from __future__ import annotations

import base64

import requests

from .config import get_rules, get_settings
from .models import Article


def _auth_header() -> dict:
    s = get_settings()
    token = base64.b64encode(f"{s.wp_username}:{s.wp_app_password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def _seo_meta(article: Article) -> dict:
    plugin = get_rules().get("wordpress", {}).get("seo_plugin", "rankmath")
    kw = ", ".join(article.meta_keywords)
    if plugin == "rankmath":
        return {
            "rank_math_description": article.meta_description,
            "rank_math_focus_keyword": kw,
        }
    if plugin == "yoast":
        return {
            "_yoast_wpseo_metadesc": article.meta_description,
            "_yoast_wpseo_focuskw": article.meta_keywords[0] if article.meta_keywords else "",
            "_yoast_wpseo_metakeywords": kw,
        }
    return {}


def create_draft(article: Article, *, status: str | None = None, timeout: int = 30) -> dict:
    """記事を投稿（既定draft）。 {id, link, edit_link} 等を返す。"""
    s = get_settings()
    if not s.wordpress_ready:
        raise RuntimeError("WordPress接続情報(.env)が未設定です。")

    status = status or s.wp_default_status
    endpoint = f"{s.wp_base_url}/wp-json/wp/v2/posts"

    payload: dict = {
        "title": article.title,
        "content": article.body_html,
        "status": status,
        "excerpt": article.meta_description,
    }
    cat_id = get_rules().get("wordpress", {}).get("category_id")
    if cat_id:
        payload["categories"] = [cat_id]

    meta = _seo_meta(article)
    if meta:
        payload["meta"] = meta

    resp = requests.post(
        endpoint, json=payload, headers={**_auth_header(), "Content-Type": "application/json"},
        timeout=timeout,
    )
    resp.raise_for_status()
    data = resp.json()
    post_id = data.get("id")
    return {
        "id": post_id,
        "link": data.get("link", ""),
        "edit_link": f"{s.wp_base_url}/wp-admin/post.php?post={post_id}&action=edit" if post_id else "",
        "status": data.get("status", status),
    }


def test_connection(timeout: int = 15) -> tuple[bool, str]:
    s = get_settings()
    if not s.wordpress_ready:
        return False, "WordPress接続情報が未設定です。"
    try:
        r = requests.get(
            f"{s.wp_base_url}/wp-json/wp/v2/users/me",
            headers=_auth_header(), timeout=timeout,
        )
        if r.status_code == 200:
            return True, f"接続OK: {r.json().get('name','')}"
        return False, f"認証失敗 HTTP {r.status_code}: {r.text[:200]}"
    except Exception as e:  # noqa: BLE001
        return False, f"接続エラー: {e}"
