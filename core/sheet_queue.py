"""
Googleスプレッドシートをキューとして読む（Issue #32）。

方式: スプレッドシートを「ウェブに公開（CSV）」し、その公開CSV URLを取得するだけ。
Google APIのキーやサービスアカウントは不要。レビュー担当はブラウザでシートに
商品を追記する → サーバーのバッチが公開CSVを読んで生成する。

中身は商品キーワードのみで機密ではないため、公開しても問題ない。
"""
from __future__ import annotations

import csv
import io

import requests


def fetch_rows(url: str, *, timeout: int = 20) -> list[dict]:
    """公開CSV URL（Googleスプレッドシート等）を取得し、行の辞書リストで返す。"""
    resp = requests.get(url, timeout=timeout)
    resp.raise_for_status()
    text = resp.content.decode("utf-8-sig")  # BOM除去
    return [dict(row) for row in csv.DictReader(io.StringIO(text))]


def is_url(source: str) -> bool:
    return source.startswith("http://") or source.startswith("https://")
