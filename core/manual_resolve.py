"""
手動選定の短縮リンク解決（Xserver側）。

RenderのWeb UIはAmazonに到達できないため、Amazonアプリの共有で出る短縮リンク
(amzn.asia / amzn.to / a.co) はそのまま `overrides._manual_pending` に積まれる。
Xserver(日本IP)がリダイレクトを辿ってASINを得て、選定済み(approved)候補として
候補プールへ投入する。リダイレクト先URLからASINを取るだけなので、Amazonの本文が
503でブロック中でも解決できる（商品詳細の取得は build_candidate に任せ、失敗時は
最小候補で投入＝生成時に再取得）。
"""
from __future__ import annotations

import re

import requests

from . import amazon_rank, candidates, overrides
from .product_extractor import _HEADERS

_DP = re.compile(r"/(?:dp|gp/product)/([A-Z0-9]{10})")


def _resolve_asin(short_url: str, *, timeout: int = 20) -> str:
    """短縮リンクのリダイレクト先URLからASINを取得。失敗時は空。"""
    try:
        r = requests.get(short_url, headers=_HEADERS, timeout=timeout, allow_redirects=True)
    except requests.RequestException:
        return ""
    m = _DP.search(r.url) or _DP.search(r.text or "")
    return m.group(1) if m else ""


def resolve_pending(max_n: int = 20) -> int:
    """`_manual_pending` の短縮リンクを解決して選定済み候補に投入。投入件数を返す。"""
    if not overrides.enabled():
        return 0
    pending = overrides.load(force=True).get("_manual_pending", []) or []
    if not pending:
        return 0

    remaining: list[str] = []
    resolved_asins: list[str] = []
    added = 0
    for url in pending[:max_n]:
        asin = _resolve_asin(url)
        if not asin:
            remaining.append(url)          # 解決できなければ次回再試行
            continue
        cand = amazon_rank.build_candidate(asin) or {
            "asin": asin, "url": f"https://www.amazon.co.jp/dp/{asin}"}
        cand["source"] = "manual"
        candidates.push([cand])
        if candidates.set_status(asin, "approved"):
            added += 1
            resolved_asins.append(asin)

    remaining += pending[max_n:]           # 未処理ぶんも残す
    try:
        patch: dict = {"_manual_pending": remaining}
        if resolved_asins:  # 手動選定として記録（生成時の足切りバイパス）
            cur = overrides.load().get("_manual_asins", []) or []
            patch["_manual_asins"] = list(dict.fromkeys([*cur, *resolved_asins]))[-300:]
        overrides.update(patch)
    except Exception:  # noqa: BLE001
        pass
    return added
