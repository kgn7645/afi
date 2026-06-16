"""
楽天ジャンルのカタログ（カテゴリ選択用）。

楽天のジャンルツリーAPIは現行の認証では使えないため、ニッチ題材向けの主要ジャンルを
シードで持つ（ジャンルIDはキーワード検索の結果から逆引き・実APIで検証済み）。
overrides の `_rakuten_catalog` に保存があればそれを優先（将来の自動更新用）。
"""
from __future__ import annotations

from . import overrides

# (genreId, 表示名) — 実APIで検証済み（タンブラー=402279 等）。家電/季節/消耗品中心。
_SEED: list[tuple[int, str]] = [
    (208375, "扇風機・ネッククーラー・季節家電"),
    (204546, "除湿機"),
    (204549, "加湿器"),
    (409927, "サーキュレーター"),
    (402279, "タンブラー・水筒"),
    (506687, "日傘・傘"),
    (564277, "モバイルバッテリー・充電器"),
    (502835, "ワイヤレスイヤホン"),
    (208522, "電動歯ブラシ・オーラルケア"),
    (216307, "化粧水・スキンケア"),
    (503054, "日焼け止め"),
    (567617, "プロテイン・健康食品"),
    (506539, "コーヒー"),
]


def _seed_items() -> list[dict]:
    return [{"genre_id": str(g), "name": n} for g, n in _SEED]


def get_catalog() -> list[dict]:
    """[{genre_id, name}] を返す。overrides に保存があれば優先、無ければシード。"""
    try:
        data = overrides.load().get("_rakuten_catalog") or {}
        items = data.get("items")
        if items:
            return items
    except Exception:  # noqa: BLE001
        pass
    return _seed_items()
