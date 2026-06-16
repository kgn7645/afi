"""
構造化データ（JSON-LD）。検索結果に★評価・価格を出してCTRを上げる（流入施策A）。

RankMath等が Article/Person schema を自動付与するため、ここでは重複を避け
**Product＋Review(★)＋Offer(価格)** のみを本文に埋め込む。
"""
from __future__ import annotations

import json

from .config import get_rules


def build_jsonld(article, product) -> str:
    """Product(+Review/Offer)のJSON-LD <script> を返す。データ不足なら空文字。"""
    if not get_rules().get("seo", {}).get("structured_data", True):
        return ""
    eeat = get_rules().get("eeat", {})
    author = eeat.get("author_name") or eeat.get("site_name", "")
    image = (article.product_image_urls or [""])[0]
    name = (getattr(product, "full_name", "") or product.product_name or article.title).strip()
    if not name or not image:
        return ""  # name/imageが無いとリッチリザルト対象外

    node: dict = {"@context": "https://schema.org", "@type": "Product",
                  "name": name[:150], "image": [image]}
    if product.brand:
        node["brand"] = {"@type": "Brand", "name": product.brand}
    if product.category:
        node["category"] = product.category
    if product.model_number:
        node["mpn"] = product.model_number

    if article.trust_total:  # ★評価（当ブログの企業信頼度評価）
        node["review"] = {
            "@type": "Review",
            "reviewRating": {"@type": "Rating",
                             "ratingValue": round(float(article.trust_total), 1),
                             "bestRating": 5, "worstRating": 1},
            "author": {"@type": "Organization", "name": author},
        }
    if product.price:  # 価格（Offer）
        offer: dict = {"@type": "Offer", "price": int(product.price),
                       "priceCurrency": "JPY",
                       "availability": "https://schema.org/InStock"}
        if article.affiliate_click_url:
            offer["url"] = article.affiliate_click_url
        node["offers"] = offer

    # review も offers も無ければリッチリザルトにならないので埋めない
    if "review" not in node and "offers" not in node:
        return ""

    raw = json.dumps(node, ensure_ascii=False).replace("<", "\\u003c")  # </script>突破防止
    return f'\n<script type="application/ld+json">{raw}</script>\n'
