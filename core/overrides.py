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


def _fetch_remote() -> tuple[bool, dict]:
    """リモートを1回取得。(ok, data)。ok=True は取得成功（data空も本当に空＝未作成）。
    ok=False は取得失敗（ネットワーク/非200/デコード不能）。失敗と"本当に空"を区別する。"""
    try:
        r = requests.get(
            f"{_base()}/wp-json/wp/v2/pages",
            params={"slug": _SLUG, "status": "draft,private,publish",
                    "context": "edit", "_fields": "id,content"},
            headers=wordpress._auth_header(), timeout=15)
        if r.status_code != 200:
            return False, {}
        items = r.json()
        if not items:
            return True, {}    # ページ未作成＝本当に空
        raw = (items[0].get("content", {}) or {}).get("raw", "")
        if not _MARK_RE.search(raw or ""):
            return False, {}   # マーカー無し＝壊れた読み取り（空扱いにしない）
        return True, _decode(raw)
    except Exception:  # noqa: BLE001
        return False, {}


def load(force: bool = False) -> dict:
    """オーバーライド設定を取得（60秒キャッシュ）。取得失敗時は {} で上書きせず
    直近の良好キャッシュを返す（フレーキー読みでのデータ全損を防ぐ）。"""
    if not enabled():
        return {}
    now = time.time()
    if not force and _cache["data"] is not None and now - _cache["ts"] < _TTL:
        return _cache["data"]
    ok, data = False, {}
    for attempt in range(3):                      # フレーキー対策に最大3回
        ok, data = _fetch_remote()
        if ok:
            break
        time.sleep(1.5)
    if not ok:
        # 取得失敗: キャッシュを{}で汚染しない。直近goodがあればそれを返す
        return _cache["data"] if _cache["data"] is not None else {}
    _cache["data"] = data
    _cache["ts"] = now
    return data


def update(partial: dict) -> bool:
    """既存のオーバーライドに partial を deep-merge して保存（他項目を壊さない）。
    ベースが空のまま保存すると全消しになるため、空ベース時は確実な再取得で保護する。"""
    from .config import _deep_merge
    base = load(force=True)
    if not base:
        # baseが空＝(a)本当に初期 or (b)フレーキー失敗。破壊防止のため確実に再確認。
        ok, remote = _fetch_remote()
        if not ok:
            return False           # 取得不能：空ベースへの保存=全消しリスク→中断
        if remote:
            base = remote          # 実はデータがあった（フレーキーだった）→それにマージ
        # ok and not remote ＝本当に空（初期）→ partialをそのまま保存してよい
    return save(_deep_merge(base, partial))


def save(data: dict, *, allow_shrink: bool = False) -> bool:
    """オーバーライド設定を保存（WP非公開ページにbase64 JSON）。
    バックストップ: 直近キャッシュよりトップキーが半減以下になる保存は事故とみなし拒否
    （フルリストアなど意図的な置換は allow_shrink=True）。"""
    if not get_settings().wordpress_ready:
        return False
    prev = _cache.get("data")
    if (not allow_shrink and isinstance(prev, dict) and len(prev) >= 6
            and len(data) < len(prev) // 2):
        return False    # キー激減＝フレーキー上書きの疑い → データ保護のため拒否
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
