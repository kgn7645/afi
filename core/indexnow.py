"""
IndexNow クライアント（Issue #17）。
公開した記事URLを Bing / Yandex 等へ即時通知してインデックスを促進する。
無料・OAuth不要。サイト直下（または任意の場所）にキーファイルを設置する必要がある。

仕組み:
1. ランダムなキー（16〜128桁の英数字）を生成
2. `https://<host>/<key>.txt`（または keyLocation 指定先）に、中身=キー のファイルを設置
3. 公開URLを IndexNow API に POST すると、対応検索エンジンがキーファイルを検証して受理
"""
from __future__ import annotations

import secrets
from urllib.parse import urlsplit

import requests

ENDPOINT = "https://api.indexnow.org/indexnow"


def generate_key() -> str:
    """IndexNow用キー（32桁の16進）を生成。"""
    return secrets.token_hex(16)


def key_file_content(key: str) -> str:
    """設置するキーファイル(<key>.txt)の中身＝キー文字列そのもの。"""
    return key


def host_of(url: str) -> str:
    return urlsplit(url).netloc


def default_key_location(host: str, key: str) -> str:
    return f"https://{host}/{key}.txt"


def submit(
    urls: list[str],
    *,
    key: str,
    key_location: str = "",
    timeout: int = 20,
) -> dict:
    """URL群を IndexNow に送信。 {status, count, host} を返す。

    IndexNowの仕様: 1ホスト分のURLをまとめて1リクエストで送る。
    """
    urls = [u for u in urls if u]
    if not urls:
        return {"status": "noop", "count": 0, "host": ""}
    if not key:
        raise RuntimeError("INDEXNOW_KEY が未設定です（.env）。")

    host = host_of(urls[0])
    payload = {
        "host": host,
        "key": key,
        "urlList": urls,
    }
    if key_location:
        payload["keyLocation"] = key_location

    resp = requests.post(
        ENDPOINT, json=payload,
        headers={"Content-Type": "application/json; charset=utf-8"},
        timeout=timeout,
    )
    # IndexNow: 200/202=受理, 4xx=キー検証失敗など
    return {
        "status": resp.status_code,
        "count": len(urls),
        "host": host,
        "ok": resp.status_code in (200, 202),
        "body": resp.text[:300],
    }
