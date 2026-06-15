"""
Issue #2拡張: 生成済み記事から note の下書きを作成（再利用関数）。
パイプライン（WP下書きと同時）と手動スクリプト note_post.py の両方から使う。

⚠️ noteのAPIは非公式・規約非推奨・仕様変更で壊れうる。NOTE_SESSION(Cookie)が必要で、
   期限切れしうる。未設定/失敗時は None を返し、WP側の処理は止めない。
"""
from __future__ import annotations

import uuid

import requests

from . import note_client, note_export, product_extractor
from .config import get_settings

MAX_NOTE_IMAGES = 3


def _upload_product_images(image_urls: list[str]) -> list[tuple[str, int, int]]:
    out: list[tuple[str, int, int]] = []
    for src in image_urls[:MAX_NOTE_IMAGES]:
        try:
            resp = requests.get(src, timeout=20)
            resp.raise_for_status()
            data = resp.content
            ctype = resp.headers.get("content-type", "image/jpeg").split(";")[0]
            ext = "png" if "png" in ctype else "jpg"
            w, h = note_export.get_image_size(data)
            note_url = note_client.upload_image(data, f"{uuid.uuid4().hex}.{ext}", ctype)
            out.append((note_url, w, h))
        except Exception:  # noqa: BLE001
            pass
    return out


def create_note_draft(article, product, *, source_url: str = "",
                      result=None) -> dict | None:
    """生成済みの article/product から note 下書きを作成。

    返り値: {id, edit_url} / NOTE_SESSION未設定・失敗時は None。
    """
    s = get_settings()
    if not s.note_ready:
        return None
    try:
        use_amazon = "amazon." in (source_url or "") and bool(s.amazon_associate_tag)
        if use_amazon:
            amazon_url = product_extractor.amazon_affiliate_url(source_url, s.amazon_associate_tag)
            note = note_client.create_empty_note()
            embeds = []
            for _ in range(3):  # 3箇所それぞれ固有キーのAmazonカードでEnter不要・自タグ収益化
                emb = note_client.get_external_embed(note["key"], amazon_url)
                embeds.append({"url": amazon_url, "key": emb["key"],
                               "html": emb["html_for_embed"]})
            body_html, body_len = note_export.build_note_html(
                article, product, amazon_embeds=embeds)
            note_client.save_draft(note["id"], article.title, body_html, body_len)
            return {"id": note["id"],
                    "edit_url": f"https://editor.note.com/notes/{note['key']}/edit/"}
        # もしも/楽天モード: 商品画像をnoteへ上げて本文に
        images = _upload_product_images(article.product_image_urls)
        body_html, body_len = note_export.build_note_html(article, product, images)
        res = note_client.create_draft(article.title, body_html, body_len)
        return {"id": res["id"], "edit_url": res["edit_url"]}
    except Exception as e:  # noqa: BLE001
        if result is not None:
            result.warnings.append(f"note下書き作成に失敗（WPは継続）: {e}")
        return None
