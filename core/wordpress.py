"""
E作業: WordPress REST API で投稿（既定は下書き）。
認証はアプリケーションパスワード（Basic認証）。
SEOメタはRank Math / Yoast のメタキーに対応。
"""
from __future__ import annotations

import base64
import re

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


_IMG_UA = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}


def upload_image_bytes(data: bytes, *, filename: str, content_type: str = "image/jpeg",
                       timeout: int = 30) -> dict:
    """画像バイト列をWPメディアに登録。 {id, source_url} を返す。"""
    s = get_settings()
    resp = requests.post(
        f"{s.wp_base_url}/wp-json/wp/v2/media",
        headers={
            **_auth_header(),
            "Content-Type": content_type,
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
        data=data,
        timeout=timeout,
    )
    resp.raise_for_status()
    d = resp.json()
    return {"id": d.get("id"), "source_url": d.get("source_url", "")}


def upload_image_from_url(image_url: str, *, filename: str = "", timeout: int = 30) -> dict:
    """画像URLをダウンロードしてWPメディアに登録。 {id, source_url} を返す。

    アイキャッチ(featured image)設定用（Issue #42）。
    """
    img = requests.get(image_url, headers=_IMG_UA, timeout=timeout)
    img.raise_for_status()
    ctype = img.headers.get("content-type", "image/jpeg").split(";")[0].strip()
    ext = "png" if "png" in ctype else "webp" if "webp" in ctype else "jpg"
    if not filename:
        filename = f"product-{abs(hash(image_url)) % 10**10}.{ext}"
    return upload_image_bytes(img.content, filename=filename, content_type=ctype, timeout=timeout)


def first_image_src(html: str) -> str:
    """本文HTMLから最初の<img src>を返す（サムネ補完用）。無ければ空。"""
    m = re.search(r'<img[^>]+src="([^"]+)"', html or "")
    return m.group(1) if m else ""


def list_posts(*, statuses: str = "publish,draft", per_page: int = 100,
               fields: str = "id,title,status,featured_media", timeout: int = 30) -> list[dict]:
    """投稿一覧を返す（サムネ補完バッチ等で使用）。"""
    s = get_settings()
    resp = requests.get(
        f"{s.wp_base_url}/wp-json/wp/v2/posts",
        params={"status": statuses, "per_page": per_page, "context": "edit", "_fields": fields},
        headers=_auth_header(), timeout=timeout,
    )
    resp.raise_for_status()
    return resp.json()


def get_media_url(media_id: int, *, timeout: int = 30) -> str:
    """メディアIDから画像URLを取得（承認画面のサムネ用）。"""
    if not media_id:
        return ""
    s = get_settings()
    r = requests.get(
        f"{s.wp_base_url}/wp-json/wp/v2/media/{media_id}",
        params={"_fields": "source_url"}, headers=_auth_header(), timeout=timeout,
    )
    return r.json().get("source_url", "") if r.status_code == 200 else ""


def set_post_status(post_id: int, status: str, *, timeout: int = 30) -> dict:
    """投稿のステータスを変更（publish/draft 等）。"""
    s = get_settings()
    r = requests.post(
        f"{s.wp_base_url}/wp-json/wp/v2/posts/{post_id}", json={"status": status},
        headers={**_auth_header(), "Content-Type": "application/json"}, timeout=timeout,
    )
    r.raise_for_status()
    return r.json()


def trash_post(post_id: int, *, timeout: int = 30) -> dict:
    """投稿をゴミ箱へ（却下用・force無しなので復元可能）。"""
    s = get_settings()
    r = requests.delete(
        f"{s.wp_base_url}/wp-json/wp/v2/posts/{post_id}", headers=_auth_header(), timeout=timeout,
    )
    r.raise_for_status()
    return r.json()


def set_featured_media(post_id: int, media_id: int, *, timeout: int = 30) -> dict:
    """既存投稿のアイキャッチ(featured_media)を更新。"""
    s = get_settings()
    resp = requests.post(
        f"{s.wp_base_url}/wp-json/wp/v2/posts/{post_id}",
        json={"featured_media": media_id},
        headers={**_auth_header(), "Content-Type": "application/json"}, timeout=timeout,
    )
    resp.raise_for_status()
    return resp.json()


def list_categories(*, timeout: int = 30) -> list[dict]:
    """カテゴリ一覧を返す。 [{id, name, slug}]。"""
    s = get_settings()
    resp = requests.get(
        f"{s.wp_base_url}/wp-json/wp/v2/categories",
        params={"per_page": 100, "_fields": "id,name,slug"},
        headers=_auth_header(), timeout=timeout,
    )
    resp.raise_for_status()
    return [{"id": c["id"], "name": c["name"], "slug": c["slug"]} for c in resp.json()]


def get_page_by_slug(slug: str, *, timeout: int = 30) -> dict | None:
    """スラッグで固定ページを取得（無ければNone）。"""
    s = get_settings()
    resp = requests.get(
        f"{s.wp_base_url}/wp-json/wp/v2/pages",
        params={"slug": slug, "status": "publish,draft", "_fields": "id,slug,status,link"},
        headers=_auth_header(), timeout=timeout,
    )
    resp.raise_for_status()
    items = resp.json()
    return items[0] if items else None


def create_page(title: str, content: str, *, slug: str = "", status: str = "draft",
                timeout: int = 30) -> dict:
    """固定ページを作成。 {id, link, status} を返す。"""
    s = get_settings()
    if not s.wordpress_ready:
        raise RuntimeError("WordPress接続情報(.env)が未設定です。")
    payload = {"title": title, "content": content, "status": status}
    if slug:
        payload["slug"] = slug
    resp = requests.post(
        f"{s.wp_base_url}/wp-json/wp/v2/pages",
        json=payload, headers={**_auth_header(), "Content-Type": "application/json"},
        timeout=timeout,
    )
    resp.raise_for_status()
    d = resp.json()
    return {"id": d.get("id"), "link": d.get("link", ""), "status": d.get("status", status)}


def create_draft(article: Article, *, status: str | None = None,
                 featured_media: int | None = None,
                 categories: list[int] | None = None, timeout: int = 30) -> dict:
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
    if featured_media:
        payload["featured_media"] = featured_media   # アイキャッチ（Issue #42）
    # カテゴリ: 明示指定 > config.category_id（Issue #44）
    cat_id = get_rules().get("wordpress", {}).get("category_id")
    if categories:
        payload["categories"] = categories
    elif cat_id:
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


def list_published_since(after_iso: str = "", *, per_page: int = 50, timeout: int = 30) -> list[dict]:
    """公開済み記事を新しい順で取得。after_iso(ISO8601)より後のものだけ返す。

    返り値: [{id, link, date_gmt}] （date_gmt昇順）
    """
    s = get_settings()
    params = {"status": "publish", "per_page": per_page, "orderby": "date", "order": "desc",
              "_fields": "id,link,date_gmt"}
    if after_iso:
        params["after"] = after_iso
    resp = requests.get(
        f"{s.wp_base_url}/wp-json/wp/v2/posts", params=params,
        headers=_auth_header(), timeout=timeout,
    )
    resp.raise_for_status()
    posts = [{"id": p["id"], "link": p["link"], "date_gmt": p.get("date_gmt", "")} for p in resp.json()]
    posts.sort(key=lambda p: p["date_gmt"])
    return posts


def upload_text_file(filename: str, content: str, *, timeout: int = 30) -> dict:
    """テキストファイルをメディアとしてアップロード（IndexNowキーファイル設置用）。

    返り値: {id, source_url}
    """
    s = get_settings()
    resp = requests.post(
        f"{s.wp_base_url}/wp-json/wp/v2/media",
        headers={
            **_auth_header(),
            "Content-Type": "text/plain",
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
        data=content.encode("utf-8"),
        timeout=timeout,
    )
    resp.raise_for_status()
    d = resp.json()
    return {"id": d.get("id"), "source_url": d.get("source_url", "")}


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


def get_post(post_id: int, *, fields: str = "id,title,content,link",
             timeout: int = 30) -> dict:
    """単一投稿を取得（承認画面のプレビュー用・raw本文）。"""
    s = get_settings()
    r = requests.get(
        f"{s.wp_base_url}/wp-json/wp/v2/posts/{post_id}",
        params={"context": "edit", "_fields": fields}, headers=_auth_header(), timeout=timeout,
    )
    r.raise_for_status()
    return r.json()
