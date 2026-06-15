"""
Issue: 設定/プロンプトの外部編集。
config.yaml の値を上書きする「オーバーライド設定」を WordPress の非公開ページに
JSONで保存し、生成(Xserver)・Web管理(Render)の両方から読み書きする。
（新しいApps Script不要・WP RESTで完結。Renderの揮発FSに依存しない＝永続）

WPのコンテンツフィルタでJSONが壊れないよう base64 で格納する。
"""
from __future__ import annotations

import base64
import json
import re
import time

import requests

from . import wordpress
from .config import get_settings

_SLUG = "tool-config-overrides"
_MARK_RE = re.compile(r"\[\[CFG\]\]([A-Za-z0-9+/=\s]+)\[\[/CFG\]\]")
_TTL = 60.0
_cache: dict = {"data": None, "ts": 0.0}


def _base() -> str:
    return get_settings().wp_base_url


def _encode(data: dict) -> str:
    raw = base64.b64encode(json.dumps(data, ensure_ascii=False).encode()).decode()
    return f"[[CFG]]{raw}[[/CFG]]"


def _decode(content: str) -> dict:
    m = _MARK_RE.search(content or "")
    if not m:
        return {}
    try:
        return json.loads(base64.b64decode(re.sub(r"\s", "", m.group(1))).decode())
    except Exception:  # noqa: BLE001
        return {}


def _find_page_id() -> int | None:
    r = requests.get(
        f"{_base()}/wp-json/wp/v2/pages",
        params={"slug": _SLUG, "status": "draft,private,publish", "_fields": "id"},
        headers=wordpress._auth_header(), timeout=15)
    items = r.json() if r.status_code == 200 else []
    return items[0]["id"] if items else None


def enabled() -> bool:
    s = get_settings()
    return bool(s.config_overrides and s.wordpress_ready)


def load(force: bool = False) -> dict:
    """オーバーライド設定を取得（60秒キャッシュ）。未設定/失敗時は {}。"""
    if not enabled():
        return {}
    now = time.time()
    if not force and _cache["data"] is not None and now - _cache["ts"] < _TTL:
        return _cache["data"]
    data: dict = {}
    try:
        r = requests.get(
            f"{_base()}/wp-json/wp/v2/pages",
            params={"slug": _SLUG, "status": "draft,private,publish",
                    "context": "edit", "_fields": "id,content"},
            headers=wordpress._auth_header(), timeout=15)
        items = r.json() if r.status_code == 200 else []
        if items:
            data = _decode((items[0].get("content", {}) or {}).get("raw", ""))
    except Exception:  # noqa: BLE001
        data = {}
    _cache["data"] = data
    _cache["ts"] = now
    return data


def update(partial: dict) -> bool:
    """既存のオーバーライドに partial を deep-merge して保存（他項目を壊さない）。"""
    from .config import _deep_merge
    return save(_deep_merge(load(force=True), partial))


def save(data: dict) -> bool:
    """オーバーライド設定を保存（WP非公開ページにbase64 JSON）。"""
    if not get_settings().wordpress_ready:
        return False
    try:
        content = _encode(data)
        pid = _find_page_id()
        hdr = {**wordpress._auth_header(), "Content-Type": "application/json"}
        if pid:
            requests.post(f"{_base()}/wp-json/wp/v2/pages/{pid}",
                          json={"content": content, "status": "draft"},
                          headers=hdr, timeout=20).raise_for_status()
        else:
            requests.post(f"{_base()}/wp-json/wp/v2/pages",
                          json={"title": "tool-config-overrides", "slug": _SLUG,
                                "content": content, "status": "draft"},
                          headers=hdr, timeout=20).raise_for_status()
        _cache["data"] = data
        _cache["ts"] = time.time()
        return True
    except Exception:  # noqa: BLE001
        return False
