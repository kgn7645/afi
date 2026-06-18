"""Threads(Meta) API クライアント。画像メイン投稿＋リンクをリプライにぶら下げる方式。

トークンは env THREADS_ACCESS_TOKEN（settings.threads_access_token）。
公式API(graph.threads.net)で検証済み: コンテナ作成→(動画は処理待ち)→publish。
画像/動画は公開URLが必須。"""
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


def _token() -> str:
    t = get_settings().threads_access_token
    if not t:
        raise RuntimeError("THREADS_ACCESS_TOKEN が未設定です（.env）。")
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


def me() -> dict:
    """トークンのアカウント情報（id, username）。"""
    return _req("GET", "me", {"fields": "id,username", "access_token": _token()})


def _user_id() -> str:
    return me().get("id", "me")


def publish_image(text: str, image_url: str, *, user_id: str | None = None) -> dict:
    """画像つきメイン投稿（リンクは入れない）。返り: {id, permalink}。"""
    tok = _token()
    uid = user_id or _user_id()
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


def publish_text(text: str, *, user_id: str | None = None) -> dict:
    tok = _token()
    uid = user_id or _user_id()
    c = _req("POST", f"{uid}/threads",
             {"access_token": tok, "media_type": "TEXT", "text": text})
    pub = _req("POST", f"{uid}/threads_publish",
               {"access_token": tok, "creation_id": c.get("id")})
    return _req("GET", str(pub.get("id")),
                {"fields": "id,permalink", "access_token": tok})


def reply(parent_id: str, text: str, *, user_id: str | None = None) -> dict:
    """親投稿へのリプライ（リンクのぶら下げ用）。"""
    tok = _token()
    uid = user_id or _user_id()
    c = _req("POST", f"{uid}/threads",
             {"access_token": tok, "media_type": "TEXT", "text": text,
              "reply_to_id": parent_id})
    time.sleep(2)
    pub = _req("POST", f"{uid}/threads_publish", {"access_token": tok, "creation_id": c.get("id")})
    return _req("GET", str(pub.get("id")), {"fields": "id,permalink", "access_token": tok})


def publish_carousel(caption: str, image_urls: list[str], *,
                     user_id: str | None = None) -> dict:
    """複数画像（カルーセル）投稿。2枚未満ならIMAGE/TEXTにフォールバック。"""
    tok = _token()
    uid = user_id or _user_id()
    urls = [u for u in image_urls if u][:20]
    if len(urls) <= 1:
        return publish_image(caption, urls[0], user_id=uid) if urls else publish_text(caption, user_id=uid)
    children = []
    for u in urls:
        c = _req("POST", f"{uid}/threads",
                 {"access_token": tok, "media_type": "IMAGE", "image_url": u,
                  "is_carousel_item": "true"})
        if c.get("id"):
            children.append(str(c["id"]))
        time.sleep(1)
    if len(children) < 2:
        return publish_image(caption, urls[0], user_id=uid)
    cont = _req("POST", f"{uid}/threads",
                {"access_token": tok, "media_type": "CAROUSEL",
                 "children": ",".join(children), "text": caption})
    time.sleep(3)
    pub = _req("POST", f"{uid}/threads_publish",
               {"access_token": tok, "creation_id": cont.get("id")})
    return _req("GET", str(pub.get("id")),
                {"fields": "id,permalink,timestamp", "access_token": tok})


def post_set(caption: str, image_urls: list[str], reply_text: str, link: str, *,
             user_id: str | None = None) -> dict:
    """1セット投稿: メイン(画像複数＋文章) → リプライ(軽い文章＋URL)。検証済みの勝ち型。

    返り: {"main": {...}, "reply": {...}}。caption は #PR を含める想定。
    """
    uid = user_id or _user_id()
    imgs = [u for u in (image_urls or []) if u]
    if len(imgs) >= 2:
        main = publish_carousel(caption, imgs, user_id=uid)
    elif len(imgs) == 1:
        main = publish_image(caption, imgs[0], user_id=uid)
    else:
        main = publish_text(caption, user_id=uid)
    rep = None
    body = (reply_text.strip() + ("\n" + link if link else "")).strip()
    if body:
        rep = reply(main.get("id"), body, user_id=uid)
    return {"main": main, "reply": rep}
