"""
Issue #2拡張: 生成済み記事から note の下書きを作成（再利用関数）。
パイプライン（WP下書きと同時）と手動スクリプト note_post.py の両方から使う。

⚠️ noteのAPIは非公式・規約非推奨・仕様変更で壊れうる。NOTE_SESSION(Cookie)が必要で、
   期限切れしうる。未設定/失敗時は None を返し、WP側の処理は止めない。
"""
from __future__ import annotations

import uuid

import requests

from . import eyecatch, note_client, note_export, product_extractor
from .config import get_rules, get_settings

MAX_NOTE_IMAGES = 3


def _attach_eyecatch(note_id: int, article, product) -> None:
    """WPと同じアイキャッチをnote下書きの見出し画像に設定する（失敗は無視）。

    eyecatch有効＋フォント有＋キャッチコピー有＋商品画像有のときだけ生成する。
    pipeline._make_featured_media と同じ素材・同じ Pillow 合成を流用。
    """
    rules = get_rules()
    if not rules.get("eyecatch", {}).get("enabled", True):
        return
    if not (article.catch_copy or "").strip() or not article.product_image_urls:
        return
    try:
        r = requests.get(article.product_image_urls[0],
                         headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
        r.raise_for_status()
        png = eyecatch.build_eyecatch(
            article.catch_copy, r.content,
            brand=getattr(product, "brand", ""),
            site_name=rules.get("eeat", {}).get("site_name", ""),
            stars=getattr(article, "trust_total", None))
        if not png:
            return
        w, h = note_export.get_image_size(png)
        note_client.set_eyecatch(note_id, png, width=w, height=h)
    except Exception:  # noqa: BLE001
        pass


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
        # カード化するアフィリURLを決める:
        #  Amazon = 自タグ付きの商品URL / それ以外 = もしものクリックURL（楽天等・収益化）
        use_amazon = "amazon." in (source_url or "") and bool(s.amazon_associate_tag)
        if use_amazon:
            card_url = product_extractor.amazon_affiliate_url(source_url, s.amazon_associate_tag)
        else:
            card_url = article.affiliate_click_url or ""

        note = note_client.create_empty_note()
        nid, nkey = note["id"], note.get("key", "")

        # noteのネイティブ商品カードを3箇所ぶん生成（Amazon/楽天/もしも共通）。
        # 各箇所で固有キーが要るため3回呼ぶ。空/失敗が出たら打ち切り、画像にフォールバック。
        embeds: list[dict] = []
        if card_url:
            for _ in range(3):
                try:
                    emb = note_client.get_external_embed(nkey, card_url)
                except Exception:  # noqa: BLE001
                    break
                if not emb.get("html_for_embed"):
                    break
                embeds.append({"url": card_url, "key": emb["key"],
                               "html": emb["html_for_embed"]})

        if embeds:
            body_html, body_len = note_export.build_note_html(
                article, product, amazon_embeds=embeds)
        else:
            # カード化できない時は商品画像を本文に貼る（従来のもしも/楽天挙動）
            images = _upload_product_images(article.product_image_urls)
            body_html, body_len = note_export.build_note_html(article, product, images)
            if not use_amazon and result is not None:
                result.warnings.append("note: 楽天/もしものカード化に失敗し画像で代替")

        note_client.save_draft(nid, article.title, body_html, body_len)
        _attach_eyecatch(nid, article, product)
        return {"id": nid,
                "edit_url": f"https://editor.note.com/notes/{nkey}/edit/" if nkey else ""}
    except Exception as e:  # noqa: BLE001
        if result is not None:
            result.warnings.append(f"note下書き作成に失敗（WPは継続）: {e}")
        return None
