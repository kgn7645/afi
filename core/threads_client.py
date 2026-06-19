"""Threads(Meta) API クライアント。画像メイン投稿＋リンクをリプライにぶら下げる方式。

トークンは引数 token（アカウント別）優先。未指定なら env THREADS_ACCESS_TOKEN にフォールバック。
公式API(graph.threads.net)で検証済み: コンテナ作成→(動画は処理待ち)→publish。画像は公開URL必須。"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request

from .config import get_settings

API = "https://graph.threads.net/v1.0"


def enabled() -> bool:
    return bool(get_settings().threads_access_token)


def _token(token: str | None = None) -> str:
    t = (token or "").strip() or get_settings().threads_access_token
    if not t:
        raise RuntimeError("Threadsアクセストークンが未設定です（アカウント設定 or .env）。")
    return t


def _req(method: str, path: str, params: dict, *, timeout: int = 40) -> dict:
    url = f"{API}/{path}"
    if method == "GET":
        url = f"{url}?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(url, method="GET")
    else:
        req = urllib.request.Request(url, data=urllib.parse.urlencode(params).encode(),
                                     method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "ignore")
        try:
            msg = json.loads(body).get("error", {}).get("message", body)
        except json.JSONDecodeError:
            msg = body
        raise RuntimeError(f"Threads API {e.code}: {msg[:300]}")


def me(token: str | None = None) -> dict:
    """トークンのアカウント情報（id, username）。接続確認に使う。"""
    return _req("GET", "me", {"fields": "id,username", "access_token": _token(token)})


def _user_id(token: str | None = None) -> str:
    return me(token).get("id", "me")


def publish_image(text: str, image_url: str, *, user_id: str | None = None,
                  token: str | None = None) -> dict:
    """画像つきメイン投稿（リンクは入れない）。返り: {id, permalink}。"""
    tok = _token(token)
    uid = user_id or _user_id(token)
    c = _req("POST", f"{uid}/threads",
             {"access_token": tok, "media_type": "IMAGE",
              "image_url": image_url, "text": text})
    cid = c.get("id")
    if not cid:
        raise RuntimeError(f"コンテナ作成失敗: {c}")
    time.sleep(2)
    pub = _req("POST", f"{uid}/threads_publish", {"access_token": tok, "creation_id": cid})
    return _req("GET", str(pub.get("id")),
                {"fields": "id,permalink,timestamp", "access_token": tok})


def publish_text(text: str, *, user_id: str | None = None, token: str | None = None) -> dict:
    tok = _token(token)
    uid = user_id or _user_id(token)
    c = _req("POST", f"{uid}/threads",
             {"access_token": tok, "media_type": "TEXT", "text": text})
    pub = _req("POST", f"{uid}/threads_publish",
               {"access_token": tok, "creation_id": c.get("id")})
    return _req("GET", str(pub.get("id")),
                {"fields": "id,permalink", "access_token": tok})


def reply(parent_id: str, text: str, *, user_id: str | None = None,
          token: str | None = None) -> dict:
    """親投稿へのリプライ（リンクのぶら下げ用）。"""
    tok = _token(token)
    uid = user_id or _user_id(token)
    c = _req("POST", f"{uid}/threads",
             {"access_token": tok, "media_type": "TEXT", "text": text,
              "reply_to_id": parent_id})
    time.sleep(2)
    pub = _req("POST", f"{uid}/threads_publish", {"access_token": tok, "creation_id": c.get("id")})
    return _req("GET", str(pub.get("id")), {"fields": "id,permalink", "access_token": tok})


def publish_carousel(caption: str, image_urls: list[str], *,
                     user_id: str | None = None, token: str | None = None) -> dict:
    """複数画像（カルーセル）投稿。2枚未満ならIMAGE/TEXTにフォールバック。"""
    tok = _token(token)
    uid = user_id or _user_id(token)
    urls = [u for u in image_urls if u][:20]
    if len(urls) <= 1:
        return (publish_image(caption, urls[0], user_id=uid, token=token) if urls
                else publish_text(caption, user_id=uid, token=token))
    children = []
    for u in urls:
        c = _req("POST", f"{uid}/threads",
                 {"access_token": tok, "media_type": "IMAGE", "image_url": u,
                  "is_carousel_item": "true"})
        if c.get("id"):
            children.append(str(c["id"]))
        time.sleep(1)
    if len(children) < 2:
        return publish_image(caption, urls[0], user_id=uid, token=token)
    cont = _req("POST", f"{uid}/threads",
                {"access_token": tok, "media_type": "CAROUSEL",
                 "children": ",".join(children), "text": caption})
    time.sleep(3)
    pub = _req("POST", f"{uid}/threads_publish",
               {"access_token": tok, "creation_id": cont.get("id")})
    return _req("GET", str(pub.get("id")),
                {"fields": "id,permalink,timestamp", "access_token": tok})


def post_set(caption: str, image_urls: list[str], reply_text: str, link: str, *,
             user_id: str | None = None, token: str | None = None) -> dict:
    """1セット投稿: メイン(画像複数＋文章) → リプライ(軽い文章＋URL)。検証済みの勝ち型。

    返り: {"main": {...}, "reply": {...}}。caption は #PR を含める想定。
    """
    uid = user_id or _user_id(token)
    imgs = [u for u in (image_urls or []) if u]
    if len(imgs) >= 2:
        main = publish_carousel(caption, imgs, user_id=uid, token=token)
    elif len(imgs) == 1:
        main = publish_image(caption, imgs[0], user_id=uid, token=token)
    else:
        main = publish_text(caption, user_id=uid, token=token)
    rep = None
    body = (reply_text.strip() + ("\n" + link if link else "")).strip()
    if body:
        rep = reply(main.get("id"), body, user_id=uid, token=token)
    return {"main": main, "reply": rep}
