"""
売れ筋ランキングのカテゴリカタログ（部門→サブカテゴリ）。

Xserver(日本IP)がAmazonの売れ筋から「部門→サブカテゴリ」ツリーをクロールし、
共有ストア(overrides の `_ranking_catalog`)に保存する。設定画面(Render)はこれを
チェックボックスで表示し、選んだノードを candidates.ranking_nodes に保存する。

カタログ未取得/失敗時は部門トップ（スラッグが安定）だけは選べるようにシードを返す。
"""
from __future__ import annotations

import re
import time

import requests

from . import overrides
from .product_extractor import _HEADERS

# 部門トップ（amazon.co.jp の売れ筋スラッグ。安定しているのでシードに使う）。
# このサイトの題材（ガジェット/季節家電/消耗品）に関係する部門を中心に。
_DEPARTMENTS: list[tuple[str, str]] = [
    ("electronics", "家電&カメラ"),
    ("kitchen", "ホーム&キッチン"),
    ("hpc", "ドラッグストア"),
    ("beauty", "ビューティー"),
    ("food-beverage", "食品・飲料・お酒"),
    ("pet-supplies", "ペット用品"),
    ("diy", "DIY・工具・ガーデン"),
    ("sports", "スポーツ&アウトドア"),
    ("computers", "パソコン・周辺機器"),
    ("office-products", "文房具・オフィス用品"),
    ("baby", "ベビー&マタニティ"),
    ("toys", "おもちゃ"),
]

# /gp/bestsellers/<dept>/<nodeid> 形式のサブカテゴリリンク＋名称
_LINK_RE = re.compile(
    r'href="/gp/bestsellers/([a-z0-9\-]+/\d+)[^"]*"[^>]*>\s*([^<]{1,30}?)\s*</', re.I)


def _seed_items() -> list[dict]:
    return [{"node": n, "name": f"{nm}（全体）", "dept": nm, "dept_node": n}
            for n, nm in _DEPARTMENTS]


def _extract_subcategories(html: str, dept_node: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for m in _LINK_RE.finditer(html):
        path, name = m.group(1), re.sub(r"\s+", " ", m.group(2)).strip()
        if not name or not path.startswith(dept_node + "/") or path in seen:
            continue
        seen.add(path)
        out.append((path, name))
    return out


def crawl_catalog(*, throttle: float = 2.0, max_subcats: int = 40,
                  timeout: int = 25) -> list[dict]:
    """部門→サブカテゴリをクロールしてカタログ items を返す。失敗部門はスキップ。"""
    items: list[dict] = []
    for dept_node, dept_name in _DEPARTMENTS:
        items.append({"node": dept_node, "name": f"{dept_name}（全体）",
                      "dept": dept_name, "dept_node": dept_node})
        try:
            r = requests.get(f"https://www.amazon.co.jp/gp/bestsellers/{dept_node}",
                             headers=_HEADERS, timeout=timeout)
            if r.status_code == 200:
                for node, name in _extract_subcategories(r.text, dept_node)[:max_subcats]:
                    items.append({"node": node, "name": name,
                                  "dept": dept_name, "dept_node": dept_node})
        except requests.RequestException:
            pass
        time.sleep(throttle)
    return items


def update_store(items: list[dict]) -> bool:
    """カタログを共有ストアへ保存（部門数より十分多い時のみ＝空クロールで潰さない）。"""
    if len(items) <= len(_DEPARTMENTS):
        return False
    return overrides.update({"_ranking_catalog": {"updated_at": int(time.time()),
                                                  "items": items}})


def get_catalog() -> dict:
    """{updated_at, items[]} を返す。未取得時は部門シード。"""
    data: dict = {}
    try:
        data = overrides.load().get("_ranking_catalog") or {}
    except Exception:  # noqa: BLE001
        data = {}
    items = data.get("items") or _seed_items()
    return {"updated_at": data.get("updated_at", 0), "items": items}


def age_days() -> float:
    """カタログの最終更新からの経過日数（未取得は大きい値）。"""
    ts = get_catalog().get("updated_at", 0)
    return (time.time() - ts) / 86400 if ts else 9999.0
