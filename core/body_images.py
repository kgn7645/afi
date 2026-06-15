"""
Issue #90: 記事本文に画像を差し込む（amaviser風）。

- 商品の実写真ギャラリー（Amazonの複数画像）を「レビュー」節の直後に
- Pillow製のブランド名バナーを「メーカーの正体」節の直前に（Pillowを本文でも活用）

画像はWPメディアへ再ホスト（WP利用可能時）。失敗時は元URLを直接参照／スキップ。
本文生成を止めないよう、各処理は例外を握りつぶして可能な範囲で差し込む。
"""
from __future__ import annotations

import html
import re

from . import eyecatch, product_extractor, wordpress
from .config import get_rules, get_settings


def _host_image_url(img_url: str, filename: str) -> str:
    """画像URLをWPメディアへ再ホストし source_url を返す。失敗時は元URL。"""
    try:
        if get_settings().wordpress_ready:
            media = wordpress.upload_image_from_url(img_url, filename=filename)
            return media.get("source_url") or img_url
    except Exception:  # noqa: BLE001
        pass
    return img_url


def _host_image_bytes(data: bytes, filename: str) -> str:
    try:
        if get_settings().wordpress_ready:
            media = wordpress.upload_image_bytes(
                data, filename=filename, content_type="image/png")
            return media.get("source_url") or ""
    except Exception:  # noqa: BLE001
        pass
    return ""


def _gallery_html(urls: list[str], alt: str) -> str:
    figs = []
    for u in urls:
        src = html.escape(u, quote=True)
        figs.append(
            '<figure style="margin:0;flex:1 1 240px;max-width:320px;">'
            f'<img src="{src}" alt="{html.escape(alt)}" loading="lazy" '
            'style="width:100%;height:auto;border-radius:10px;border:1px solid #eee;">'
            "</figure>")
    return ('\n<div class="product-gallery" style="display:flex;flex-wrap:wrap;gap:12px;'
            'justify-content:center;margin:24px 0;">\n' + "\n".join(figs) + "\n</div>\n")


def _insert_after_h2(body: str, anchor: str, block: str) -> tuple[str, bool]:
    idx = body.find(anchor)
    if idx == -1:
        return body, False
    end = body.find("</h2>", idx)
    if end == -1:
        return body, False
    pos = end + len("</h2>")
    return body[:pos] + block + body[pos:], True


def enrich(article, product, result=None) -> None:
    """article.body_html に商品画像ギャラリー＋ブランドバナーを差し込む。"""
    cfg = get_rules().get("body_images", {})
    if not cfg.get("enabled", True):
        return
    body = article.body_html or ""
    src = product.source_url or ""

    # 1) 商品の実写真ギャラリー（Amazon）→「おすすめ商品…レビュー」節の直後
    if "amazon." in src:
        try:
            imgs = product_extractor.fetch_amazon_product_images(
                src, max_n=int(cfg.get("max_photos", 3)))
        except Exception:  # noqa: BLE001
            imgs = []
        if imgs:
            base = abs(hash(product.product_name)) % 10 ** 6
            hosted = [_host_image_url(u, f"prod-{base}-{i}.jpg") for i, u in enumerate(imgs)]
            alt = (f"{product.brand} {product.category}".strip() or product.product_name)
            gallery = _gallery_html(hosted, alt)
            body, ok = _insert_after_h2(body, "<h2>おすすめ商品", gallery)
            if not ok:
                body, ok = _insert_after_h2(body, "<h2>おすすめ", gallery)
            if ok and result is not None:
                result.warnings.append(f"本文に商品画像{len(hosted)}枚を挿入")

    # 2) Pillowブランドバナー →「メーカーの正体／…とは？」節の直前
    if cfg.get("brand_header", True) and (product.brand or "").strip():
        png = None
        try:
            png = eyecatch.build_brand_header(
                product.brand, subtitle="ってどんなメーカー？")
        except Exception:  # noqa: BLE001
            png = None
        burl = _host_image_bytes(png, f"brand-{abs(hash(product.brand)) % 10 ** 6}.png") if png else ""
        if burl:
            banner = (
                '\n<div style="text-align:center;margin:26px 0;">'
                f'<img src="{html.escape(burl, quote=True)}" alt="{html.escape(product.brand)}" '
                'loading="lazy" style="width:100%;max-width:760px;height:auto;border-radius:12px;">'
                "</div>\n")
            m = re.search(r"<h2>[^<]*(?:メーカーの正体|とは？)[^<]*</h2>", body)
            if m:
                body = body[:m.start()] + banner + body[m.start():]
                if result is not None:
                    result.warnings.append("本文にブランドバナーを挿入")

    article.body_html = body
