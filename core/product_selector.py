"""
A作業: 商品選定ルールの判定。
スプレッドシートの選定基準（3000円以上 / 消え物・化粧品・薬品除外 / 在庫あり 等）を適用。
"""
from __future__ import annotations

from .config import get_rules
from .models import Product


def screen(cand: dict) -> tuple[bool, str]:
    """クロール候補(dict)を選定基準で足切り。 (ok, 理由) を返す。

    選定シートの機械化可能ルール:
      価格 >= min_price / 在庫あり / 除外カテゴリ(消え物等) / 大手ブランド除外。
    価格・在庫が不明(None)のときは弾かない（スワイプ担当者が最終判断）。
    """
    rules = get_rules().get("selection", {})
    title = cand.get("title", "") or ""
    brand = cand.get("brand", "") or ""
    price = cand.get("price")
    hay = f"{title} {brand}"

    min_price = rules.get("min_price", 3000)
    if price is not None and price < min_price:
        return False, f"価格 {price}円 < 最低 {min_price}円"

    if rules.get("require_in_stock", True) and cand.get("in_stock") is False:
        return False, "在庫切れ"

    for kw in rules.get("exclude_keywords", []) or []:
        if kw and kw in hay:
            return False, f"除外カテゴリ該当「{kw}」"

    for b in rules.get("exclude_brands", []) or []:
        if b and (b.lower() in brand.lower() or b in title):
            return False, f"大手ブランド「{b}」"

    return True, "ok"


def evaluate(product: Product) -> tuple[bool, str]:
    """選定基準を満たすか判定。 (ok, 理由) を返す。"""
    rules = get_rules().get("selection", {})
    reasons: list[str] = []
    ok = True

    min_price = rules.get("min_price", 3000)
    if product.price is not None and product.price < min_price:
        ok = False
        reasons.append(f"価格 {product.price}円 < 最低 {min_price}円")

    if rules.get("require_in_stock", True) and not product.in_stock:
        ok = False
        reasons.append("在庫切れ")

    haystack = f"{product.product_name} {product.category} {product.brand}"
    for kw in rules.get("exclude_keywords", []):
        if kw in haystack:
            ok = False
            reasons.append(f"除外カテゴリ該当: 「{kw}」")

    for b in rules.get("exclude_brands", []) or []:
        if b and (b.lower() in (product.brand or "").lower() or b in product.product_name):
            ok = False
            reasons.append(f"大手ブランド該当: 「{b}」")
            break

    if product.price is None:
        reasons.append("価格未取得（要確認）")

    if ok and not reasons:
        return True, "選定基準を満たしています。"
    if ok:
        return True, "OK（注意: " + " / ".join(reasons) + "）"
    return False, " / ".join(reasons)
