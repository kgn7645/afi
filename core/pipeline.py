"""
全工程のオーケストレーション。
A(選定) → B(基本情報) → C(記事生成) → D(リンク) → E(WP下書き) → ログ。
"""
from __future__ import annotations

import csv
from datetime import datetime, timezone
from pathlib import Path

from . import (affiliate, body_images, canva, content_generator, eyecatch,
               internal_links, moshimo_link, note_publish, product_extractor,
               product_selector, prompts, qa, sheet_log, site_setup, wordpress)
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
    rakuten_item: dict | None = None,
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

    gemini = gemini or GeminiClient()

    # B+: 企業情報グラウンディング（Issue #15: どこの国の誤生成対策）
    if (get_rules().get("article", {}).get("ground_company", True)
            and product.brand and not product.company_hint):
        product.company_hint = _ground_company(product, gemini, result)

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
    elif rakuten_item:  # 楽天候補: その商品で直接もしもリンク（キーワード再検索しない）
        res = moshimo_link.build_rakuten_link_from_item(
            rakuten_item.get("name", ""), rakuten_item.get("url", ""),
            rakuten_item.get("image", ""))
        if res:
            article.affiliate_click_url = res["click_url"]
            if rakuten_item.get("image"):
                article.product_image_urls = [rakuten_item["image"]]
            article.body_html = affiliate.insert_into_body(article.body_html, res["html"])
        else:
            result.warnings.append("楽天もしもリンク生成に失敗（プレースホルダで継続）")
    elif amazon_ready and _embed_amazon(article, product, s, result):
        pass  # Amazonカード/ボタンを埋め込み済み
    else:
        link_html, click_url, image_urls = _auto_affiliate_link(product, result)
        article.affiliate_click_url = click_url
        article.product_image_urls = image_urls
        article.body_html = affiliate.insert_into_body(article.body_html, link_html)

    # 著者情報ボックス（Issue #44: E-E-A-T）を本文末尾に付与
    article.body_html = site_setup.append_author_box(article.body_html)
    # 承認一覧で金額を出すための価格マーカー（本文末尾の非表示コメント）
    if product.price:
        article.body_html += f"\n<!-- price:{int(product.price)} -->"
    result.article = article

    # QA（Issue #16）: 禁止表現・構成・整形を検査
    qa_rules = get_rules().get("qa", {})
    if qa_rules.get("enabled", True):
        issues = qa.check_article(article, product)
        result.qa_issues = issues
        result.warnings.extend(qa.format_issues(issues))
        effective_status = wp_status or get_settings().wp_default_status
        if (qa.has_errors(issues) and qa_rules.get("block_publish_on_error", True)
                and effective_status == "publish"):
            wp_status = "draft"
            result.warnings.append("QA: error検出のため公開を中止し下書きにしました")

    # E: WordPress下書き
    if post_to_wp:
        try:
            # アイキャッチ（#42 商品画像 / #5 デザイン生成）
            featured_id = _make_featured_media(article, product, result)
            # カテゴリ自動割当（Issue #44: 未分類を避ける）
            category_ids = _pick_category_ids(product, result, gemini)
            # 内部リンク（Issue #18）: 同カテゴリの公開記事への関連リンクを本文に
            if category_ids:
                article.body_html = internal_links.add_related(
                    article.body_html, category_ids[0], result)
            # 本文に画像を差し込む（Issue #90: 商品実写真ギャラリー＋Pillowブランドバナー）
            body_images.enrich(article, product, result)
            wp = wordpress.create_draft(article, status=wp_status,
                                        featured_media=featured_id, categories=category_ids)
            result.wp_post_id = wp["id"]
            result.wp_edit_link = wp["edit_link"]
            # note 同時下書き（Issue #2拡張・NOTE_SESSION設定時のみ）
            if get_rules().get("note", {}).get("enabled", True):
                nd = note_publish.create_note_draft(
                    article, product, source_url=product.source_url, result=result)
                if nd:
                    result.note_id = nd["id"]
                    result.note_edit_url = nd["edit_url"]
            _log(result, wp_status=wp.get("status", ""))
        except Exception as e:  # noqa: BLE001
            result.warnings.append(f"WordPress投稿に失敗: {e}")
            _log(result, wp_status="failed")
    else:
        _log(result, wp_status="not_posted")

    return result


def _make_featured_media(article, product: Product, result: PipelineResult) -> int | None:
    """アイキャッチを用意してメディアIDを返す（Issue #5 / #42）。

    1. eyecatch有効＋フォント有＋コピー有 → デザイン画像を生成して使用
    2. それ以外/失敗 → 商品画像そのまま（#42）
    """
    urls = article.product_image_urls
    if not urls:
        return None
    rules = get_rules()
    site = rules.get("eeat", {}).get("site_name", "")
    has_copy = bool((article.catch_copy or "").strip())

    # 商品画像のバイト列（Canva/Pillow合成に必要）
    img_bytes = b""
    if rules.get("eyecatch", {}).get("enabled", True) and has_copy:
        try:
            import requests
            r = requests.get(urls[0], headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
            r.raise_for_status()
            img_bytes = r.content
        except Exception as e:  # noqa: BLE001
            result.warnings.append(f"商品画像の取得に失敗（アイキャッチ簡易化）: {e}")

    if img_bytes:
        # 1) Canva（設定時）→ 2) Pillow合成 の順で試す
        png = None
        if canva.available():
            png = canva.build_eyecatch(article.catch_copy, img_bytes,
                                       brand=product.brand, site_name=site)
            if not png:
                result.warnings.append("Canvaアイキャッチ生成に失敗（Pillowで代替）")
        if not png:
            png = eyecatch.build_eyecatch(article.catch_copy, img_bytes,
                                          brand=product.brand, site_name=site,
                                          stars=article.trust_total)
        if png:
            try:
                fid = abs(hash(article.title)) % 10**8
                media = wordpress.upload_image_bytes(
                    png, filename=f"eyecatch-{fid}.png", content_type="image/png")
                return media.get("id")
            except Exception as e:  # noqa: BLE001
                result.warnings.append(f"アイキャッチ画像のアップロードに失敗: {e}")

    # 3) フォールバック: 商品画像そのまま（#42）
    try:
        media = wordpress.upload_image_from_url(urls[0])
        return media.get("id")
    except Exception as e:  # noqa: BLE001
        result.warnings.append(f"アイキャッチ設定に失敗（投稿は継続）: {e}")
        return None


def _ground_company(product: Product, gemini: GeminiClient,
                    result: PipelineResult) -> str:
    """ブランドの企業情報をWeb検索で裏付けてcompany_hintを返す（Issue #15）。

    失敗時は空文字（ヒント無しで継続）。
    """
    try:
        info = gemini.generate_grounded(
            prompts.company_grounding_prompt(product.brand, product.category))
        info = (info or "").strip()
        if info:
            result.warnings.append("企業情報をWeb検索でグラウンディングしました")
            return info
    except Exception as e:  # noqa: BLE001
        result.warnings.append(f"企業情報グラウンディングに失敗（ヒント無しで継続）: {e}")
    return ""


def _pick_category_ids(product: Product, result: PipelineResult,
                       gemini: GeminiClient | None) -> list[int] | None:
    """既存カテゴリから記事に合うものを選びIDで返す（Issue #44）。

    auto_category無効/未設定や判定失敗時は default_category_slug、
    それも無ければ None（= configのcategory_id/未指定にフォールバック）。
    """
    rules = get_rules()
    if not rules.get("wordpress", {}).get("auto_category", False):
        return None
    try:
        cats = wordpress.list_categories()
    except Exception as e:  # noqa: BLE001
        result.warnings.append(f"カテゴリ取得に失敗（既定運用）: {e}")
        return None
    by_slug = {c["slug"]: c["id"] for c in cats}
    pickable = [c for c in cats if c["slug"] != "uncategorized"]
    default_slug = rules.get("eeat", {}).get("default_category_slug", "")

    chosen = ""
    if pickable:
        try:
            g = gemini or GeminiClient()
            concept = rules.get("eeat", {}).get("site_concept", "")
            raw = g.generate(prompts.category_pick_prompt(product, pickable, concept),
                             temperature=0.0)
            chosen = raw.strip().split()[0].strip().strip('"').strip("`") if raw else ""
        except Exception as e:  # noqa: BLE001
            result.warnings.append(f"カテゴリ自動判定に失敗（既定運用）: {e}")
    if chosen not in by_slug:
        chosen = default_slug
    return [by_slug[chosen]] if chosen in by_slug else None


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
    dt = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    title = result.article.title if result.article else ""
    with LOG_PATH.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=LOG_FIELDS)
        if new_file:
            w.writeheader()
        w.writerow({
            "datetime": dt,
            "brand": result.product.brand,
            "category": result.product.category,
            "model_number": result.product.model_number,
            "title": title,
            "selection_ok": result.selection_ok,
            "selection_reason": result.selection_reason,
            "wp_post_id": result.wp_post_id or "",
            "wp_status": wp_status,
            "source_url": result.product.source_url,
        })
    # スプレッドシート書き戻し（Issue #4）。未設定なら no-op。
    if sheet_log.enabled():
        sheet_log.log_generation(
            post_id=result.wp_post_id, datetime_iso=dt, brand=result.product.brand,
            category=result.product.category, title=title, status=wp_status,
            url=result.wp_edit_link, warnings=result.warnings)
