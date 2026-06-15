"""
Issue #3: Amazon売れ筋ランキング/検索から商品候補を収集（クロール）。
担当者のスワイプ選定UI（Issue #12拡張）に流す候補プールの元データを作る。

クロールは Xserver(日本IP) で実行する前提（クラウドの海外IPはAmazonにブロックされやすい）。
"""
from __future__ import annotations

import re
from urllib.parse import quote

import requests

from .product_extractor import _HEADERS

_ASIN_IN_ATTR = re.compile(r'data-asin="([A-Z0-9]{10})"')
_ASIN_IN_DP = re.compile(r"/dp/([A-Z0-9]{10})")


def _uniq(seq: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for x in seq:
        if x and x not in seen:
            seen.add(x)
            out.append(x)
    return out


def search_asins(keyword: str, *, limit: int = 15, timeout: int = 25) -> list[str]:
    """検索結果ページからASINを抽出。"""
    url = "https://www.amazon.co.jp/s?k=" + quote(keyword)
    r = requests.get(url, headers=_HEADERS, timeout=timeout)
    if r.status_code != 200:
        return []
    return _uniq(_ASIN_IN_ATTR.findall(r.text))[:limit]


def ranking_asins(node: str, *, limit: int = 15, timeout: int = 25) -> list[str]:
    """売れ筋ランキングページからASINを抽出。node は 'kitchen/4083001' 形式 or フルURL。"""
    url = node if node.startswith("http") else f"https://www.amazon.co.jp/gp/bestsellers/{node}"
    r = requests.get(url, headers=_HEADERS, timeout=timeout)
    if r.status_code != 200:
        return []
    return _uniq(_ASIN_IN_DP.findall(r.text))[:limit]


def _extract_price(html: str) -> int | None:
    m = (re.search(r'"priceAmount":\s*([0-9.]+)', html)
         or re.search(r'a-price-whole">([0-9,]+)', html))
    if not m:
        return None
    try:
        return int(float(m.group(1).replace(",", "")))
    except ValueError:
        return None


def build_candidate(asin: str, *, timeout: int = 20) -> dict | None:
    """ASINから候補1件 {asin,title,price,image,url} を作る。取得不可ならNone。"""
    url = f"https://www.amazon.co.jp/dp/{asin}"
    try:
        r = requests.get(url, headers=_HEADERS, timeout=timeout)
    except requests.RequestException:
        return None
    if r.status_code != 200 or "何かお探し" in r.text:
        return None
    text = r.text
    m = re.search(r'id="productTitle"[^>]*>\s*([^<]+)', text)
    title = m.group(1).strip() if m else ""
    if not title:
        return None
    m = (re.search(r'"hiRes":"(https://[^"]+\.jpg)"', text)
         or re.search(r'data-old-hires="(https://[^"]+\.jpg)"', text)
         or re.search(r'"large":"(https://[^"]+\.jpg)"', text))
    image = m.group(1) if m else ""
    return {"asin": asin, "title": title, "price": _extract_price(text),
            "image": image, "url": url}


def collect(*, keywords: list[str] | None = None, nodes: list[str] | None = None,
            per_source: int = 10, max_total: int = 40,
            exclude_asins: set[str] | None = None) -> list[dict]:
    """検索＋ランキングから候補を収集（既出ASINは除外）。"""
    exclude = exclude_asins or set()
    asins: list[str] = []
    for kw in (keywords or []):
        asins += search_asins(kw, limit=per_source)
    for nd in (nodes or []):
        asins += ranking_asins(nd, limit=per_source)
    asins = [a for a in _uniq(asins) if a not in exclude][:max_total]
    out: list[dict] = []
    for a in asins:
        c = build_candidate(a)
        if c:
            out.append(c)
    return out
