"""
楽天市場 商品検索API（楽天ウェブサービス・無料）クライアント。
キーワードから商品URL・画像・価格を取得し、もしもリンク生成の入力にする。

要: RAKUTEN_APP_ID（https://webservice.rakuten.co.jp/ で無料発行）
"""
from __future__ import annotations

from urllib.parse import urlsplit

import requests

from .config import get_settings

_ENDPOINT = "https://app.rakuten.co.jp/services/api/IchibaItem/Search/20220601"


def _split_image_url(url: str) -> tuple[str, str]:
    """画像URLを (ドメイン, パス) に分割（もしものd/p形式に合わせる）。"""
    parts = urlsplit(url)
    domain = f"{parts.scheme}://{parts.netloc}"
    path = parts.path + (f"?{parts.query}" if parts.query else "")
    return domain, path


def search_item(keyword: str, *, timeout: int = 15) -> dict | None:
    """キーワードで楽天商品を1件検索。見つからなければ None。

    返り値: {name, url, price, image_domain, image_paths}
    """
    s = get_settings()
    if not s.rakuten_app_id:
        raise RuntimeError("RAKUTEN_APP_ID が未設定です（.env）。")

    params = {
        "applicationId": s.rakuten_app_id,
        "keyword": keyword,
        "hits": 1,
        "format": "json",
        "imageFlag": 1,          # 画像ありのみ
        "availability": 1,       # 在庫ありのみ
        "sort": "standard",
    }
    resp = requests.get(_ENDPOINT, params=params, timeout=timeout)
    resp.raise_for_status()
    items = resp.json().get("Items", [])
    if not items:
        return None
    item = items[0]["Item"]

    image_domain = ""
    image_paths: list[str] = []
    for img in item.get("mediumImageUrls", []):
        url = img.get("imageUrl", "")
        # サムネイルサイズ指定(?_ex=128x128)を外して大きめ画像に
        url = url.split("?_ex=")[0]
        if url:
            d, p = _split_image_url(url)
            image_domain = d
            image_paths.append(p)

    return {
        "name": item.get("itemName", ""),
        "url": item.get("itemUrl", ""),
        "price": item.get("itemPrice"),
        "image_domain": image_domain,
        "image_paths": image_paths,
    }
