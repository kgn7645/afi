"""メンズ美容の商品発見（収集元の追加）。発見した商品名を返し、収益化は楽天で照合する。

- m-cosme（エムコスメ・Shopify）: 各ジャンルの上位商品を products.json から取得
- @cosme メンズコスメpickup(1014): ピックアップ商品名を取得
クチコミ本文等は扱わず、商品名だけを発見に使う（@cosme商品ページ追加時の口コミ補強は別経路）。
"""
from __future__ import annotations

import html as _html
import json
import re

import requests

from . import threads_pipeline as tp

_MCOSME = "https://www.m-cosme.com"
# 取得対象ジャンル（brand-/all-items等は除外）。homepageから動的取得＋既定フォールバック
_MCOSME_FALLBACK = ["skincare", "skincare-all-in-one", "haircare-shampoo", "haircare-styling",
                    "haircare-treatment", "bodycare-body-wash", "bodycare-body-mist", "bodycare",
                    "fragrance", "makeup", "oralcare", "skincare-eye-care"]


def _get(url: str, referer: str = "") -> str:
    try:
        host = "https://" + url.split("/")[2] + "/"
        s = requests.Session()
        s.get(host, headers=tp._BROWSER_HEADERS, timeout=20)
        r = s.get(url, headers={**tp._BROWSER_HEADERS, "Referer": referer or host}, timeout=25)
        return r.text if r.status_code == 200 else ""
    except Exception:  # noqa: BLE001
        return ""


def _mcosme_genres() -> list:
    html = _get(_MCOSME + "/")
    cols = re.findall(r"/collections/([a-z0-9-]+)", html)
    genres = [c for c in dict.fromkeys(cols)
              if not c.startswith(("brand", "feature")) and c not in ("all-items", "new-item", "setitem")]
    return genres or _MCOSME_FALLBACK


def discover_mcosme(per_genre: int = 1, max_genres: int = 12) -> list:
    """各ジャンルの上位 per_genre 件の商品名（m-cosme）。"""
    out = []
    for g in _mcosme_genres()[:max_genres]:
        try:
            r = requests.get(f"{_MCOSME}/collections/{g}/products.json?limit={max(per_genre, 1)}",
                             headers=tp._BROWSER_HEADERS, timeout=20)
            if r.status_code != 200:
                continue
            for p in json.loads(r.text).get("products", [])[:per_genre]:
                t = (p.get("title") or "").strip()
                if t:
                    out.append(t)
        except Exception:  # noqa: BLE001
            continue
    return out


def discover_cosme_pickup() -> list:
    """@cosme メンズコスメpickup(1014) のピックアップ商品名。"""
    html = _get("https://www.cosme.net/categories/pickup/1014/", "https://www.cosme.net/")
    if not html:
        return []
    names = re.findall(r'<img[^>]+alt=["\']([^"\']{5,46})["\']', html)
    out, seen = [], set()
    bad = ("@cosme", "アイコン", "icon", "ロゴ", "バナー", "ポイント", "当たる", "特集", "クチコミ",
           "ランキング", "トレンド", "ベスコス", "予測", "まとめ", "新作コスメ")
    for n in names:
        n = _html.unescape(re.sub(r"\s+", " ", n).strip())
        if any(b in n for b in bad) or n in seen:
            continue
        # ブランド名/商品名らしい（英字 or カナを含む・記事的でない）
        if not re.search(r"[A-Za-zァ-ヶ]", n):
            continue
        seen.add(n)
        out.append(n)
    return out


def mens_product_names(per_genre: int = 1) -> list:
    """メンズ各ソースの商品名を統合（重複除去）。"""
    names = discover_mcosme(per_genre=per_genre) + discover_cosme_pickup()
    out, seen = [], set()
    for n in names:
        k = re.sub(r"\s+", "", n)[:20]
        if n and k not in seen:
            seen.add(k)
            out.append(n)
    return out
