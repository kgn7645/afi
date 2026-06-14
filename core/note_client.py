"""
note 非公式API クライアント（Issue #2）。

ブラウザのキャプチャから判明した内部APIを使い、note下書きを自動作成する。
⚠️ 非公式・規約上非推奨・仕様変更で壊れうる。あくまで自分のアカウントの自動化用。

フロー:
  1. POST /api/v1/text_notes  {"template_key": null}            → 下書きid取得
  2. POST /api/v1/text_notes/draft_save?id={id}&is_temp_saved=true
       {"body": <HTML>, "body_length": N, "name": <title>, "index": false, "is_lead_form": false}
認証: Cookie `_note_session_v5` ＋ ヘッダ `x-requested-with: XMLHttpRequest`
"""
from __future__ import annotations

import requests

from .config import get_settings

_BASE = "https://note.com/api/v1/text_notes"
_HEADERS = {
    "content-type": "application/json",
    "origin": "https://editor.note.com",
    "referer": "https://editor.note.com/",
    "x-requested-with": "XMLHttpRequest",
    "accept": "*/*",
    "user-agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/149.0 Safari/537.36"
    ),
}


def _session() -> requests.Session:
    s = get_settings()
    if not s.note_ready:
        raise RuntimeError("NOTE_SESSION（_note_session_v5の値）が未設定です（.env）。")
    sess = requests.Session()
    sess.headers.update(_HEADERS)
    sess.cookies.set("_note_session_v5", s.note_session, domain=".note.com")
    return sess


def create_draft(title: str, body_html: str, body_length: int, *, timeout: int = 30) -> dict:
    """note下書きを作成する。 {id, key, edit_url} を返す。"""
    sess = _session()

    # 1) 空の下書きを作成して id を得る
    r1 = sess.post(_BASE, json={"template_key": None}, timeout=timeout)
    r1.raise_for_status()
    data1 = r1.json().get("data", r1.json())
    note_id = data1.get("id")
    note_key = data1.get("key", "")
    if not note_id:
        raise RuntimeError(f"下書きidの取得に失敗: {r1.text[:200]}")

    # 2) 本文を保存
    payload = {
        "body": body_html,
        "body_length": body_length,
        "name": title,
        "index": False,
        "is_lead_form": False,
    }
    r2 = sess.post(
        f"{_BASE}/draft_save",
        params={"id": note_id, "is_temp_saved": "true"},
        json=payload,
        timeout=timeout,
    )
    r2.raise_for_status()

    return {
        "id": note_id,
        "key": note_key,
        "edit_url": f"https://editor.note.com/notes/{note_key}/edit/" if note_key else "",
    }


def test_connection(timeout: int = 15) -> tuple[bool, str]:
    """ログインユーザー情報の取得でセッション有効性を確認。"""
    s = get_settings()
    if not s.note_ready:
        return False, "NOTE_SESSION 未設定"
    try:
        sess = _session()
        r = sess.get("https://note.com/api/v1/nu/", timeout=timeout)
        if r.status_code == 200 and r.json().get("data"):
            name = r.json()["data"].get("nickname") or r.json()["data"].get("urlname", "")
            return True, f"note接続OK: {name}"
        return False, f"認証失敗 HTTP {r.status_code}: {r.text[:120]}"
    except Exception as e:  # noqa: BLE001
        return False, f"接続エラー: {e}"
