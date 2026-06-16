"""
カテゴリ比較記事「{category}のおすすめN選」生成（流入B: 高ボリューム検索意図）。

ランキング(Amazon)または楽天ジャンルから上位N件を取り、1本の比較記事にまとめる。
各商品にアフィリエイトカード（Amazon=自タグカード / 楽天=もしも）を差し込む。
単一商品レビュー(pipeline.run)とは別タイプの記事。
"""
from __future__ import annotations

import html as _html
import json
import re

from . import (affiliate, amazon_rank, eyecatch, moshimo_link, product_extractor,
               prompts, rakuten, site_setup, wordpress)
from .config import get_rules, get_settings
from .gemini_client import GeminiClient
from .models import Article


def _strip_fences(t: str) -> str:
    t = (t or "").strip()
    t = re.sub(r"^```[a-zA-Z]*\s*", "", t)
    t = re.sub(r"\s*```$", "", t)
    return t.strip()


def _get_products(source: dict, count: int) -> list[dict]:
    """source={'type':'amazon','node':..} or {'type':'rakuten','genre':..} から上位を取得。"""
    if source.get("type") == "rakuten":
        items = rakuten.genre_items(source["genre"], hits=count * 2)
    else:
        items = amazon_rank.ranking_candidates(source["node"], limit=count * 2)
    out: list[dict] = []
    for it in items:
        name = (it.get("title") or it.get("name") or "").strip()
        if name and it.get("url"):
            it["name"] = name
            out.append(it)
        if len(out) >= count:
            break
    return out


def _affiliate_block(p: dict) -> str:
    """商品1件のアフィリエイトカードHTML。"""
    name, url, img = p.get("name", ""), p.get("url", ""), p.get("image", "")
    label = get_rules().get("affiliate", {}).get("amazon_button_label", "Amazonで見る")
    if "rakuten" in url:
        res = moshimo_link.build_rakuten_link_from_item(name, url, img)
        return res["html"] if res else ""
    tag = get_settings().amazon_associate_tag
    az = product_extractor.amazon_affiliate_url(url, tag) if tag else url
    if img:
        return affiliate.build_amazon_card(az, name, img, label=label)
    return affiliate.build_amazon_button(az, label=label)


def _featured(article: Article, category: str) -> int | None:
    """1位商品の画像でアイキャッチを作る（失敗時は商品画像そのまま/None）。"""
    urls = article.product_image_urls
    if not urls or not urls[0]:
        return None
    try:
        import requests
        r = requests.get(urls[0], headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
        r.raise_for_status()
        png = eyecatch.build_eyecatch(
            f"おすすめ厳選", r.content, brand=category,
            site_name=get_rules().get("eeat", {}).get("site_name", ""))
        if png:
            media = wordpress.upload_image_bytes(
                png, filename=f"roundup-{abs(hash(category)) % 10**8}.png",
                content_type="image/png")
            return media.get("id")
        media = wordpress.upload_image_from_url(urls[0])
        return media.get("id")
    except Exception:  # noqa: BLE001
        return None


def build_and_post(*, category: str, source: dict, count: int = 5,
                   post_to_wp: bool = True, wp_status: str = "draft",
                   gemini: GeminiClient | None = None) -> dict:
    """比較記事を生成してWP下書きへ。 {ok, wp_post_id, title, products} を返す。"""
    products = _get_products(source, count)
    if len(products) < 2:
        return {"ok": False, "error": f"商品が足りません（{len(products)}件）"}

    gemini = gemini or GeminiClient()
    raw = gemini.generate(prompts.roundup_prompt(category, products), temperature=0.8)
    try:
        data = json.loads(_strip_fences(raw))
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"AI出力の解析に失敗: {e}"}
    picks = data.get("picks", []) or []

    parts: list[str] = [f"<h2>はじめに</h2>\n<p>{_html.escape(data.get('intro', ''))}</p>"]
    if data.get("criteria"):
        parts.append(f"<h3>{_html.escape(category)}の選び方</h3>\n<p>{_html.escape(data['criteria'])}</p>")
    parts.append(f"<h2>{_html.escape(category)}のおすすめ{len(products)}選</h2>")
    for i, p in enumerate(products):
        pk = picks[i] if i < len(picks) else {}
        parts.append(f"<h3>{i + 1}位. {_html.escape(p['name'])}</h3>")
        if pk.get("catch"):
            parts.append(f"<p><strong>{_html.escape(pk['catch'])}</strong></p>")
        parts.append(_affiliate_block(p))
        if pk.get("review"):
            parts.append(f"<p>{_html.escape(pk['review'])}</p>")
        gb = []
        if pk.get("good"):
            gb.append(f"<li>👍 {_html.escape(pk['good'])}</li>")
        if pk.get("bad"):
            gb.append(f"<li>🤔 {_html.escape(pk['bad'])}</li>")
        if gb:
            parts.append("<ul>" + "".join(gb) + "</ul>")
        if pk.get("recommend"):
            parts.append(f"<p>こんな人におすすめ：{_html.escape(pk['recommend'])}</p>")
    if data.get("comparison"):
        parts.append(f"<h2>結局どれを選ぶ？</h2>\n<p>{_html.escape(data['comparison'])}</p>")
    if data.get("conclusion"):
        parts.append(f"<h2>まとめ</h2>\n<p>{_html.escape(data['conclusion'])}</p>")

    body = site_setup.append_author_box("\n".join(parts))
    article = Article(
        title=data.get("title") or f"{category}のおすすめ{len(products)}選",
        body_html=body, meta_description=data.get("meta_description", ""),
        product_image_urls=[products[0].get("image", "")])

    if not post_to_wp:
        return {"ok": True, "article": article, "products": len(products)}
    featured = _featured(article, category)
    wp = wordpress.create_draft(article, status=wp_status, featured_media=featured)
    return {"ok": True, "wp_post_id": wp["id"], "edit_link": wp.get("edit_link", ""),
            "title": article.title, "products": len(products)}
