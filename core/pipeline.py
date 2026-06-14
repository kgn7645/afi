"""
全工程のオーケストレーション。
A(選定) → B(基本情報) → C(記事生成) → D(リンク) → E(WP下書き) → ログ。
"""
from __future__ import annotations

import csv
from datetime import datetime, timezone
from pathlib import Path

from . import affiliate, content_generator, moshimo_link, product_extractor, product_selector, wordpress
from .config import ROOT, get_rules, get_settings
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

    # D: アフィリエイトリンク（Issue #41: 既定はAmazon自タグに統一。noteと同じ）
    #   優先度: 明示指定(もしも等) > Amazonモード > 楽天検索＋もしも自動生成
    s = get_settings()
    mode = get_rules().get("affiliate", {}).get("mode", "amazon")
    amazon_ready = (mode == "amazon" and "amazon." in product.source_url
                    and bool(s.amazon_associate_tag))

    if affiliate_link_html:
        article.body_html = affiliate.insert_into_body(article.body_html, affiliate_link_html)
    elif amazon_ready and _embed_amazon(article, product, s, result):
        pass  # Amazonカード/ボタンを埋め込み済み
    else:
        link_html, click_url, image_urls = _auto_affiliate_link(product, result)
        article.affiliate_click_url = click_url
        article.product_image_urls = image_urls
        article.body_html = affiliate.insert_into_body(article.body_html, link_html)

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


def _embed_amazon(product_article, product: Product, s, result: PipelineResult) -> bool:
    """Amazon自タグのカード/ボタンを本文に埋め込む（Issue #41）。

    段階フォールバック:
      1. 商品画像が取れる → noteのような商品カード
      2. 取れないがページは生存 → CTAボタンのみ
      3. 無効(404等) → 何も貼らず False（楽天＋もしもにフォールバック）
    """
    label = get_rules().get("affiliate", {}).get("amazon_button_label", "Amazonで見る")
    amazon_url = product_extractor.amazon_affiliate_url(
        product.source_url, s.amazon_associate_tag)

    card = product_extractor.fetch_amazon_product_card(product.source_url)
    if card:
        block = affiliate.build_amazon_card(
            amazon_url, card["title"], card["image"], label=label)
        product_article.affiliate_click_url = amazon_url
        product_article.product_image_urls = [card["image"]]
        product_article.body_html = affiliate.insert_amazon_cards(
            product_article.body_html, block)
        return True

    if product_extractor.amazon_url_alive(product.source_url):
        product_article.affiliate_click_url = amazon_url
        product_article.body_html = affiliate.insert_amazon_buttons(
            product_article.body_html, amazon_url, label=label)
        result.warnings.append("Amazon商品画像が取得できずボタンのみ配置（カード化失敗）")
        return True

    result.warnings.append(
        f"Amazon商品ページが無効のためAmazonリンクをスキップ: {product.source_url}")
    return False


def _auto_affiliate_link(product: Product, result: PipelineResult) -> tuple[str, str, list[str]]:
    """楽天検索＋もしもでリンクを自動生成。 (カードHTML, プレーン成果URL, 商品画像URL群) を返す。

    失敗時は ("", "", [])（プレースホルダ運用にフォールバック）。
    """
    s = get_settings()
    if not (s.moshimo_aid and s.rakuten_app_id and s.rakuten_access_key):
        return "", "", []  # 未設定ならプレースホルダ挿入にフォールバック
    keyword = " ".join(p for p in (product.brand, product.category) if p) or product.product_name
    if not keyword:
        return "", "", []
    try:
        res = moshimo_link.build_rakuten_link_by_keyword(keyword)
        if res:
            p = res.get("product", {})
            domain = p.get("image_domain", "")
            images = [domain + path for path in p.get("image_paths", [])] if domain else []
            return res["html"], res.get("click_url", ""), images
        result.warnings.append(f"楽天で該当商品なし（リンク未生成）: {keyword}")
    except Exception as e:  # noqa: BLE001
        result.warnings.append(f"もしもリンク自動生成に失敗（プレースホルダで継続）: {e}")
    return "", "", []


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
