"""
A/B作業: 商品情報の取得。
- Amazon URLからの自動抽出（best-effort。bot対策で失敗しうる）
- 失敗時は手動入力にフォールバック
"""
from __future__ import annotations

import re
import time
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from .models import Product

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "ja,en;q=0.8",
}

_ASIN_RE = re.compile(r"/(?:dp|gp/product|gp/aw/d)/([A-Z0-9]{10})")


def extract_asin(url: str) -> str:
    m = _ASIN_RE.search(url)
    return m.group(1) if m else ""


def amazon_affiliate_url(url: str, tag: str) -> str:
    """Amazon商品URLを、アソシエイトタグ付きのクリーンなURLにする。

    例: https://www.amazon.co.jp/dp/<ASIN>?tag=<tag>
    ASINが取れない場合は元URLに ?tag= を付与してフォールバック。
    """
    asin = extract_asin(url)
    if asin:
        return f"https://www.amazon.co.jp/dp/{asin}?tag={tag}"
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}tag={tag}"


def amazon_url_alive(url: str, *, timeout: int = 15) -> bool:
    """Amazon商品ページが生きているか（404=リンク切れ）を判定。

    死んだASINのアフィリリンクを公開しないためのガード。
    404 のみを「死」と判定し、503/captcha等のbot対策レスポンスは
    （誤判定で有効リンクを捨てないよう）生きているものとして扱う。
    通信失敗時も True（保守的にブロックしない）。
    """
    try:
        r = requests.get(url, headers=_HEADERS, timeout=timeout, allow_redirects=True)
    except requests.RequestException:
        return True
    if r.status_code == 404:
        return False
    # 200でも「何かお探しですか？」ページ（無効ASIN）はリンク切れ扱い
    if r.status_code == 200 and "何かお探し" in r.text:
        return False
    return True


def fetch_product_html(url: str, *, timeout: int = 20, retries: int = 2) -> str:
    """商品ページHTMLを取得。Amazonの“空スタブ(api-services-support・極小)”はリトライ。

    無効ASIN(404/「何かお探し」)は空文字を返し、リトライしない。取得不能も空文字。
    """
    for attempt in range(retries + 1):
        try:
            r = requests.get(url, headers=_HEADERS, timeout=timeout, allow_redirects=True)
        except requests.RequestException:
            r = None
        if r is not None and r.status_code == 200:
            if "何かお探し" in r.text or "ページが見つかりません" in r.text:
                return ""  # 無効ページ＝リトライ不要
            if len(r.text) > 50_000 and "productTitle" in r.text:
                return r.text  # 通常ページ
        if attempt < retries:
            time.sleep(2.5 * (attempt + 1))  # スタブ/スロットリングはバックオフ再取得
    return ""


def fetch_amazon_product_card(url: str, *, timeout: int = 15) -> dict | None:
    """商品ページから商品名とメイン画像URLを取得し {title, image} を返す。

    noteのような自前カードHTMLを組むための材料。
    無効ASIN(404/「何かお探し」)・bot対策ブロック・画像/商品名欠落時は None。
    """
    text = fetch_product_html(url, timeout=max(timeout, 20))
    if not text:
        return None

    m = re.search(r'id="productTitle"[^>]*>\s*([^<]+)', text)
    title = m.group(1).strip() if m else ""
    if not title:
        m = re.search(r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)', text)
        title = m.group(1).strip() if m else ""

    m = (re.search(r'"hiRes":"(https://[^"]+\.jpg)"', text)
         or re.search(r'data-old-hires="(https://[^"]+\.jpg)"', text)
         or re.search(r'"large":"(https://[^"]+\.jpg)"', text))
    image = m.group(1) if m else ""

    if not (title and image):
        return None
    return {"title": title, "image": image}


def fetch_amazon_product_images(url: str, *, max_n: int = 4, timeout: int = 15) -> list[str]:
    """商品ページのギャラリーから高解像度画像URLを最大 max_n 枚（重複除去）取得。

    本文中に実写真を差し込む用途（Issue #90）。取得不可なら []。
    """
    try:
        r = requests.get(url, headers=_HEADERS, timeout=timeout, allow_redirects=True)
    except requests.RequestException:
        return []
    if r.status_code != 200 or "何かお探し" in r.text:
        return []
    text = r.text
    urls = re.findall(r'"hiRes":"(https://[^"]+\.jpg)"', text)
    if len(urls) < max_n:
        urls += re.findall(r'"large":"(https://[^"]+\.jpg)"', text)
    seen: set[str] = set()
    out: list[str] = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out[:max_n]


def _price_to_int(text: str) -> int | None:
    digits = re.sub(r"[^\d]", "", text or "")
    return int(digits) if digits else None


def extract_from_amazon(url: str, *, timeout: int = 15) -> tuple[Product, list[str]]:
    """Amazon商品ページから可能な範囲で情報抽出。warningsも返す。"""
    warnings: list[str] = []
    product = Product(source_url=url, model_number=extract_asin(url))

    if "amazon." not in urlparse(url).netloc:
        warnings.append("Amazon以外のURLです。手動入力での補完を推奨します。")
        return product, warnings

    try:
        resp = requests.get(url, headers=_HEADERS, timeout=timeout)
        resp.raise_for_status()
    except Exception as e:  # noqa: BLE001
        warnings.append(f"Amazonページ取得に失敗（手動入力で補完してください）: {e}")
        return product, warnings

    soup = BeautifulSoup(resp.text, "lxml")

    title_el = soup.select_one("#productTitle")
    if title_el:
        product.product_name = title_el.get_text(strip=True)

    # ブランド
    brand_el = soup.select_one("#bylineInfo") or soup.select_one("a#brand")
    if brand_el:
        bt = brand_el.get_text(strip=True)
        bt = re.sub(r"(ブランド:|のストアを表示|を表示|Visit the|Store)", "", bt).strip()
        product.brand = bt

    # 価格
    price_el = soup.select_one(".a-price .a-offscreen") or soup.select_one("#priceblock_ourprice")
    if price_el:
        product.price = _price_to_int(price_el.get_text())

    # 在庫
    avail = soup.select_one("#availability")
    if avail and ("在庫切れ" in avail.get_text() or "現在在庫切れ" in avail.get_text()):
        product.in_stock = False

    # スペック（仕様テーブル）
    specs: list[str] = []
    for row in soup.select("#productDetails_techSpec_section_1 tr, table.a-keyvalue tr"):
        k = row.select_one("th")
        v = row.select_one("td")
        if k and v:
            specs.append(f"{k.get_text(strip=True)}: {v.get_text(strip=True)}")
    # 箇条書き特徴
    for li in soup.select("#feature-bullets li span.a-list-item"):
        t = li.get_text(strip=True)
        if t:
            specs.append(t)
    product.specs = specs[:12]

    if not product.product_name:
        warnings.append(
            "商品名を取得できませんでした（Amazonのbot対策の可能性）。手動入力で補完してください。"
        )
    return product, warnings


def from_manual(
    *,
    brand: str,
    category: str,
    model_number: str,
    product_name: str,
    price: int | None = None,
    in_stock: bool = True,
    specs: list[str] | None = None,
    company_hint: str = "",
    source_url: str = "",
) -> Product:
    return Product(
        source_url=source_url,
        brand=brand,
        category=category,
        model_number=model_number,
        product_name=product_name,
        price=price,
        in_stock=in_stock,
        specs=specs or [],
        company_hint=company_hint,
    )


def merge(base: Product, override: Product) -> Product:
    """自動抽出(base)に手動入力(override)を上書きマージ。"""
    data = base.model_dump()
    for k, v in override.model_dump().items():
        if v not in (None, "", [], False) or k == "in_stock":
            # in_stock は明示Falseも反映
            if k == "in_stock":
                data[k] = v
            elif v not in (None, "", []):
                data[k] = v
    return Product(**data)
