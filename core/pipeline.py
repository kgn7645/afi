"""
全工程のオーケストレーション。
A(選定) → B(基本情報) → C(記事生成) → D(リンク) → E(WP下書き) → ログ。
"""
from __future__ import annotations

import csv
from datetime import datetime, timezone
from pathlib import Path

from . import affiliate, content_generator, product_extractor, product_selector, wordpress
from .config import ROOT
from .gemini_client import GeminiClient
from .models import PipelineResult, Product

LOG_PATH = ROOT / "data" / "articles_log.csv"
LOG_FIELDS = [
    "datetime", "brand", "category", "model_number", "title",
    "selection_ok", "selection_reason", "wp_post_id", "wp_status", "source_url",
]


def resolve_product(
    *,
    url: str = "",
    manual: dict | None = None,
) -> tuple[Product, list[str]]:
    """URL自動抽出と手動入力をマージして商品を確定。"""
    warnings: list[str] = []
    base = Product()
    if url:
        base, warnings = product_extractor.extract_from_amazon(url)
    if manual:
        override = product_extractor.from_manual(
            brand=manual.get("brand", ""),
            category=manual.get("category", ""),
            model_number=manual.get("model_number", ""),
            product_name=manual.get("product_name", ""),
            price=manual.get("price"),
            in_stock=manual.get("in_stock", True),
            specs=manual.get("specs", []),
            company_hint=manual.get("company_hint", ""),
            source_url=url,
        )
        base = product_extractor.merge(base, override) if url else override
    return base, warnings


def run(
    *,
    url: str = "",
    manual: dict | None = None,
    affiliate_link_html: str = "",
    post_to_wp: bool = True,
    wp_status: str | None = None,
    skip_selection_gate: bool = False,
    gemini: GeminiClient | None = None,
) -> PipelineResult:
    product, warnings = resolve_product(url=url, manual=manual)

    # A: 選定判定
    ok, reason = product_selector.evaluate(product)
    result = PipelineResult(
        product=product, selection_ok=ok, selection_reason=reason, warnings=warnings,
    )
    if not ok and not skip_selection_gate:
        _log(result, wp_status="")
        return result

    # C: 記事生成
    article = content_generator.generate_article(product, gemini=gemini)

    # D: アフィリエイトリンク埋め込み
    article.body_html = affiliate.insert_into_body(article.body_html, affiliate_link_html)
    result.article = article

    # E: WordPress下書き
    if post_to_wp:
        try:
            wp = wordpress.create_draft(article, status=wp_status)
            result.wp_post_id = wp["id"]
            result.wp_edit_link = wp["edit_link"]
            _log(result, wp_status=wp.get("status", ""))
        except Exception as e:  # noqa: BLE001
            result.warnings.append(f"WordPress投稿に失敗: {e}")
            _log(result, wp_status="failed")
    else:
        _log(result, wp_status="not_posted")

    return result


def _log(result: PipelineResult, *, wp_status: str) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    new_file = not LOG_PATH.exists()
    with LOG_PATH.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=LOG_FIELDS)
        if new_file:
            w.writeheader()
        w.writerow({
            "datetime": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
            "brand": result.product.brand,
            "category": result.product.category,
            "model_number": result.product.model_number,
            "title": result.article.title if result.article else "",
            "selection_ok": result.selection_ok,
            "selection_reason": result.selection_reason,
            "wp_post_id": result.wp_post_id or "",
            "wp_status": wp_status,
            "source_url": result.product.source_url,
        })
