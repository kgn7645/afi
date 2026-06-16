"""
Issue #3: Amazon売れ筋ランキング/検索から商品候補を収集（クロール）。
担当者のスワイプ選定UI（Issue #12拡張）に流す候補プールの元データを作る。

クロールは Xserver(日本IP) で実行する前提（クラウドの海外IPはAmazonにブロックされやすい）。

選定シート（商品選定マニュアル）の基準を移植:
  - 価格3,000円以上 / 在庫あり / 消え物・化粧品・薬品を除外（product_selector.screen）
  - 中華・無名・マイナーを優先＝大手ブランドは除外（selection.exclude_brands）
  - 季節物を優先（seasonal_keywords を自動追加＆上位表示）
  - Amazon以外の参照元（マイベスト/360LIFE/LDK/GET NAVI/トレンド記事）からも
    Amazonリンク(/dp/ASIN, amzn.to)を抽出（extract_asins_from_url）
"""
from __future__ import annotations

import re
import time
from datetime import datetime
from urllib.parse import quote

import requests

from .product_extractor import _HEADERS, fetch_product_html

_ASIN_IN_ATTR = re.compile(r'data-asin="([A-Z0-9]{10})"')
_ASIN_IN_DP = re.compile(r"/dp/([A-Z0-9]{10})")
_ASIN_IN_GP = re.compile(r"/gp/product/([A-Z0-9]{10})")
_AMZN_SHORT = re.compile(r"https?://amzn\.to/[A-Za-z0-9]+")

# 季節物の優先キーワード（日本の月別）。selection.seasonal_boost が真のとき
# クロールのキーワードに自動追加し、該当候補を上位に並べる。
_SEASON: dict[int, list[str]] = {
    1: ["加湿器", "電気毛布", "着る毛布", "セラミックヒーター", "結露対策"],
    2: ["加湿器", "花粉対策", "空気清浄機", "セラミックヒーター"],
    3: ["花粉対策", "空気清浄機", "ロボット掃除機"],
    4: ["新生活 家電", "掃除機 コードレス", "衣類スチーマー"],
    5: ["除湿機", "サーキュレーター", "ネッククーラー"],
    6: ["除湿機 コンパクト", "衣類乾燥除湿機", "サーキュレーター", "防カビ"],
    7: ["卓上扇風機", "ハンディファン", "ネッククーラー", "冷風機"],
    8: ["ハンディファン", "ネッククーラー", "冷風機", "携帯扇風機"],
    9: ["サーキュレーター", "除湿機", "防災グッズ"],
    10: ["加湿器", "防災グッズ", "セラミックヒーター"],
    11: ["加湿器", "電気毛布", "セラミックヒーター", "足元ヒーター"],
    12: ["加湿器", "電気毛布", "着る毛布", "セラミックヒーター", "結露対策"],
}


def seasonal_keywords(month: int | None = None) -> list[str]:
    """今（または指定月）の季節キーワードを返す。"""
    m = month or datetime.now().month
    return list(_SEASON.get(m, []))


def _uniq(seq: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for x in seq:
        if x and x not in seen:
            seen.add(x)
            out.append(x)
    return out


def search_asins(keyword: str, *, limit: int = 15, timeout: int = 25) -> list[str]:
    """検索結果ページからASINを抽出（スポンサー枠もdata-asinに含まれる）。"""
    url = "https://www.amazon.co.jp/s?k=" + quote(keyword)
    r = requests.get(url, headers=_HEADERS, timeout=timeout)
    if r.status_code != 200:
        return []
    return _uniq(_ASIN_IN_ATTR.findall(r.text))[:limit]


def ranking_asins(node: str, *, limit: int = 15, timeout: int = 25,
                  retries: int = 2) -> list[str]:
    """売れ筋ランキングページからASINを抽出。node は 'kitchen/4083001' 形式 or フルURL。

    Amazonが時々返す“空スタブ”対策で、ASINが取れない/応答が極端に小さい時はリトライ。
    /dp/ に加え data-asin もフォールバックで拾う。
    """
    url = node if node.startswith("http") else f"https://www.amazon.co.jp/gp/bestsellers/{node}"
    for attempt in range(retries + 1):
        try:
            r = requests.get(url, headers=_HEADERS, timeout=timeout)
        except requests.RequestException:
            time.sleep(2)
            continue
        if r.status_code == 200 and len(r.text) > 50_000:
            asins = _uniq(_ASIN_IN_DP.findall(r.text) + _ASIN_IN_ATTR.findall(r.text))
            if asins:
                return asins[:limit]
        if attempt < retries:
            time.sleep(2.5 * (attempt + 1))  # スタブ/スロットリングはバックオフして再取得
    return []


def extract_asins_from_url(url: str, *, limit: int = 15, timeout: int = 25) -> list[str]:
    """任意のページ（まとめ/ランキング/トレンド記事）からAmazon ASINを抽出する。

    本文中の /dp/ASIN・/gp/product/ASIN を拾い、短縮URL amzn.to はリダイレクトを
    たどって最終URLからASINを得る。マイベスト等のアフィリ記事はAmazonリンクを
    多数貼るため、トレンド/まとめ由来の候補発掘に使える。
    """
    try:
        r = requests.get(url, headers=_HEADERS, timeout=timeout)
    except requests.RequestException:
        return []
    if r.status_code != 200:
        return []
    text = r.text
    asins: list[str] = _ASIN_IN_DP.findall(text) + _ASIN_IN_GP.findall(text)
    for short in _uniq(_AMZN_SHORT.findall(text))[:limit]:
        try:
            rr = requests.get(short, headers=_HEADERS, timeout=timeout, allow_redirects=True)
            m = _ASIN_IN_DP.search(rr.url) or _ASIN_IN_GP.search(rr.url)
            if m:
                asins.append(m.group(1))
        except requests.RequestException:
            pass
    return _uniq(asins)[:limit]


def _extract_price(html: str) -> int | None:
    m = (re.search(r'"priceAmount":\s*([0-9.]+)', html)
         or re.search(r'a-price-whole">([0-9,]+)', html))
    if not m:
        return None
    try:
        return int(float(m.group(1).replace(",", "")))
    except ValueError:
        return None


def _extract_brand(html: str) -> str:
    """商品ページからブランド名を抽出（bylineInfo → JSON brand の順）。"""
    m = re.search(r'id="bylineInfo"[^>]*>([^<]+)</a>', html)
    if m:
        b = m.group(1).strip()
        b = re.sub(r"^(ブランド|Brand)[:：]\s*", "", b)
        b = re.sub(r"^Visit the\s+", "", b)
        b = re.sub(r"(のストアを表示|のストア|を表示|\s*Store|ストア)\s*$", "", b).strip()
        if b:
            return b
    m = re.search(r'"brand"\s*:\s*"([^"]+)"', html)
    return m.group(1).strip() if m else ""


def _extract_stock(html: str) -> bool | None:
    """在庫状態を推定。True=在庫あり / False=在庫切れ / None=不明（判定不能）。"""
    neg = ("現在在庫切れ", "在庫切れです", "一時的に在庫切れ", "入荷時期は未定",
           "お取り扱いできません", "Currently unavailable", "現在お取り扱いできません")
    if any(n in html for n in neg):
        return False
    m = re.search(r'id="availability".{0,300}?>([^<]+)<', html, re.S)
    if m and "在庫" in m.group(1) and "切れ" not in m.group(1):
        return True
    if "add-to-cart-button" in html or "buy-now-button" in html:
        return True
    return None


def build_candidate(asin: str, *, timeout: int = 20) -> dict | None:
    """ASINから候補1件 {asin,title,price,brand,in_stock,image,url} を作る。取得不可ならNone。"""
    url = f"https://www.amazon.co.jp/dp/{asin}"
    text = fetch_product_html(url, timeout=max(timeout, 20))  # 空スタブはリトライ
    if not text:
        return None
    m = re.search(r'id="productTitle"[^>]*>\s*([^<]+)', text)
    title = m.group(1).strip() if m else ""
    if not title:
        return None
    m = (re.search(r'"hiRes":"(https://[^"]+\.jpg)"', text)
         or re.search(r'data-old-hires="(https://[^"]+\.jpg)"', text)
         or re.search(r'"large":"(https://[^"]+\.jpg)"', text))
    image = m.group(1) if m else ""
    return {"asin": asin, "title": title, "price": _extract_price(text),
            "brand": _extract_brand(text), "in_stock": _extract_stock(text),
            "image": image, "url": url}


def _kw_match(text: str, keyword_filters: list[str]) -> bool:
    """キーワード絞り込み: いずれかのキーワード（空白区切りは全語AND）が text に含まれる。"""
    for kw in keyword_filters:
        words = kw.split()
        if words and all(w in text for w in words):
            return True
    return False


def collect(*, keywords: list[str] | None = None, nodes: list[str] | None = None,
            source_urls: list[str] | None = None, rakuten_genres: list | None = None,
            per_source: int = 10, max_total: int = 40,
            exclude_asins: set[str] | None = None,
            season: bool = False, screen: bool = True,
            report: list | None = None) -> list[dict]:
    """ランキング＋参照元URLから候補を集め、キーワードで絞り込み＆選定基準で足切り。

    方針: Amazon検索(/s)はbot対策で塞がれやすいため**収集元にしない**。
    - 収集元 = 売れ筋ランキング(nodes) ＋ 参照元URL(source_urls)
    - keywords = **タイトル絞り込みフィルタ**（設定時、タイトル/ブランドに含むものだけ採用）
    - screen = 価格/在庫/除外カテゴリ/大手/雑誌書籍 の足切り
    - season = 季節該当を上位に並べ替え（絞り込みはしない）
    report に dict を渡すと不採用候補 {asin,reason,title} を追記する。
    """
    from . import product_selector

    exclude = exclude_asins or set()
    kw_filters = [k for k in (keywords or []) if k and k.strip()]

    asins: list[str] = []
    for nd in (nodes or []):
        asins += ranking_asins(nd, limit=per_source)
    for u in (source_urls or []):
        asins += extract_asins_from_url(u, limit=per_source)

    # キーワード絞り込み＋足切りで減るぶん、ビルド対象は広めに確保
    cap = max_total * 3 if (screen or kw_filters) else max_total
    asins = [a for a in _uniq(asins) if a not in exclude][:cap]

    out: list[dict] = []
    for a in asins:
        c = build_candidate(a)
        if not c:
            continue
        title = c.get("title", "")
        if kw_filters and not _kw_match(f"{title} {c.get('brand', '')}", kw_filters):
            if report is not None:
                report.append({"asin": a, "reason": "キーワード不一致", "title": title[:40]})
            continue
        if screen:
            ok, reason = product_selector.screen(c)
            if not ok:
                if report is not None:
                    report.append({"asin": a, "reason": reason, "title": title[:40]})
                continue
        out.append(c)
        if len(out) >= max_total:
            break

    # 楽天ジャンル（公式API・bot対策無し）。候補は完成形dictなのでbuild不要
    if rakuten_genres and len(out) < max_total:
        from . import rakuten
        for gid in rakuten_genres:
            if len(out) >= max_total:
                break
            try:
                items = rakuten.genre_items(gid, hits=per_source)
            except Exception:  # noqa: BLE001
                continue
            for c in items:
                if c.get("asin") in exclude:
                    continue
                title = c.get("title", "")
                if kw_filters and not _kw_match(f"{title} {c.get('brand', '')}", kw_filters):
                    if report is not None:
                        report.append({"asin": c.get("asin"), "reason": "キーワード不一致",
                                       "title": title[:40]})
                    continue
                if screen:
                    ok, reason = product_selector.screen(c)
                    if not ok:
                        if report is not None:
                            report.append({"asin": c.get("asin"), "reason": reason,
                                           "title": title[:40]})
                        continue
                out.append(c)
                if len(out) >= max_total:
                    break

    if season:  # 季節該当を先頭へ（安定ソート・絞り込みはしない）
        sk = seasonal_keywords()
        out.sort(key=lambda c: 0 if any(k.split()[0] in c.get("title", "") for k in sk) else 1)
    return out
