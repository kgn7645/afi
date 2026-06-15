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
_PRESIGN = "https://note.com/api/v3/images/upload/presigned_post"
# content-type は付けない（json= / files= で requests が自動設定する）
_HEADERS = {
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


def upload_image(image_bytes: bytes, filename: str, content_type: str = "image/jpeg",
                 *, timeout: int = 30) -> str:
    """画像をnoteにアップロードし、公開URL(assets.st-note.com/...)を返す。

    note方式: ①presigned_postで署名付きS3 POST情報を取得 → ②S3へ実ファイルをPOST。
    """
    sess = _session()
    # ① 署名付きPOST情報を取得（multipartで filename を送る）
    r = sess.post(_PRESIGN, files={"filename": (None, filename)}, timeout=timeout)
    r.raise_for_status()
    d = r.json()["data"]
    action, post_fields, final_url = d["action"], d["post"], d["url"]

    # ② S3 へ実ファイルをアップロード（noteのCookieは送らない）
    s3 = requests.post(
        action,
        data=post_fields,
        files={"file": (filename, image_bytes, content_type)},
        timeout=timeout,
    )
    if s3.status_code not in (200, 201, 204):
        raise RuntimeError(f"画像アップロード(S3)失敗 {s3.status_code}: {s3.text[:200]}")
    return final_url


def create_empty_note(*, timeout: int = 30) -> dict:
    """空の下書きを作成し {id, key} を返す。

    note内部APIの仕様変更で、ボディは空 {} を送る（旧 {"template_key": null} は422）。
    """
    sess = _session()
    r = sess.post(_BASE, json={}, timeout=timeout)
    r.raise_for_status()
    d = r.json().get("data", r.json())
    if not d.get("id"):
        raise RuntimeError(f"下書きidの取得に失敗: {r.text[:200]}")
    return {"id": d.get("id"), "key": d.get("key", "")}


def delete_note(note_id: int, *, timeout: int = 30) -> bool:
    """下書き/記事を削除（DELETE /api/v1/notes/{id}）。"""
    sess = _session()
    r = sess.delete(f"https://note.com/api/v1/notes/{note_id}", timeout=timeout)
    return r.status_code == 200


def save_draft(note_id: int, title: str, body_html: str, body_length: int, *, timeout: int = 30) -> None:
    """本文を下書き保存する。"""
    sess = _session()
    r = sess.post(
        f"{_BASE}/draft_save",
        params={"id": note_id, "is_temp_saved": "true"},
        json={"body": body_html, "body_length": body_length, "name": title,
              "index": False, "is_lead_form": False},
        timeout=timeout,
    )
    r.raise_for_status()


def set_eyecatch(note_id: int, image_bytes: bytes, *, width: int, height: int,
                 filename: str = "eyecatch.png", content_type: str = "image/png",
                 timeout: int = 30) -> bool:
    """下書きの見出し画像（アイキャッチ）を設定する。

    ブラウザのキャプチャから判明した内部API:
      POST /api/v1/image_upload/note_eyecatch
      multipart: note_id / file(画像) / width / height
    サーバ側で note_id の下書きに直接ひも付くため、draft_save の前後どちらでもよい。
    成功時 True。失敗してもWP/note本文は止めない想定で例外は投げない。
    """
    sess = _session()
    r = sess.post(
        "https://note.com/api/v1/image_upload/note_eyecatch",
        data={"note_id": str(note_id), "width": str(width), "height": str(height)},
        files={"file": (filename, image_bytes, content_type)},
        timeout=timeout,
    )
    return r.status_code in (200, 201)


def get_external_embed(note_key: str, url: str, *, timeout: int = 25) -> dict:
    """外部URLのカード（リンクカード）情報を生成し {key, html_for_embed} を返す。

    note内部API: GET /api/v2/embed_by_external_api。URLに既にAmazonタグが付いていれば
    そのタグでカードが作られる（自分のタグで収益化）。
    """
    sess = _session()
    r = sess.get(
        "https://note.com/api/v2/embed_by_external_api",
        params={"url": url, "service": "external-article",
                "embeddable_key": note_key, "embeddable_type": "Note"},
        timeout=timeout,
    )
    r.raise_for_status()
    d = r.json()["data"]
    return {"key": d["key"], "html_for_embed": d["html_for_embed"]}


def create_draft(title: str, body_html: str, body_length: int, *, timeout: int = 30) -> dict:
    """空下書き作成→本文保存をまとめて行う。 {id, key, edit_url} を返す。"""
    note = create_empty_note(timeout=timeout)
    save_draft(note["id"], title, body_html, body_length, timeout=timeout)
    return {
        "id": note["id"], "key": note["key"],
        "edit_url": f"https://editor.note.com/notes/{note['key']}/edit/" if note["key"] else "",
    }


def test_connection(timeout: int = 15) -> tuple[bool, str]:
    """空下書きを作成→削除してセッション有効性を確認（旧 /api/v1/nu/ は廃止済み）。"""
    s = get_settings()
    if not s.note_ready:
        return False, "NOTE_SESSION 未設定"
    try:
        note = create_empty_note(timeout=timeout)
        delete_note(note["id"], timeout=timeout)   # テスト用の空下書きは消す
        return True, "note接続OK（下書き作成→削除を確認）"
    except Exception as e:  # noqa: BLE001
        return False, f"認証失敗/接続エラー: {e}"
