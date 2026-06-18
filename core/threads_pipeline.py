"""Threadsアフィリの生成→承認→キュー→公開パイプライン（#95系・画像投稿）。

フロー: 商品選定(楽天) → 投稿作成(キャプション＋画像候補を複数取得) → 承認(担当者が画像選択)
        → 公開キュー(スケジュール) → スケジューラが時刻に公開(画像メイン＋リンクをリプライ)。

保存は overrides(WP共有ページ)に名前空間化:
  _threads_drafts : 承認待ちドラフト [{id, account, product, price, link, caption, images[], created}]
  _threads_queue  : 承認済み [{id, account, caption, image, link, scheduled_at, status, ...}]
  _threads_posted : 公開ログ（直近のみ）
"""
from __future__ import annotations

import json
import random
import re
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

from . import overrides, threads_client
from .config import get_rules, get_settings

_RAKUTEN = "https://openapi.rakuten.co.jp/ichibams/api/IchibaItem/Search/20260401"
_GEMINI = ("https://generativelanguage.googleapis.com/v1beta/"
           "models/{m}:generateContent?key={k}")
_NG = ["名入れ", "名前入り", "オーダー", "ギフト", "プレゼント", "記念", "中古", "訳あり",
       "医薬品", "薬", "育毛", "増毛", "サプリ", "化粧水", "美容液"]  # 薬機法リスクも除外


# ---------- 共有ストア ----------
def _load(key: str) -> list:
    return overrides.load(force=True).get(key, []) or []


def _save(key: str, items: list) -> bool:
    return overrides.update({key: items})


def drafts() -> list:
    return _load("_threads_drafts")


def queue() -> list:
    return _load("_threads_queue")


# ---------- 楽天 商品選定 ----------
def _rakuten_search(genre: str, e: dict) -> list[dict]:
    p = {"applicationId": e["RAKUTEN_APP_ID"], "accessKey": e["RAKUTEN_ACCESS_KEY"],
         "affiliateId": e.get("RAKUTEN_AFFILIATE_ID", ""), "genreId": str(genre),
         "hits": 30, "format": "json", "imageFlag": 1, "availability": 1,
         "sort": "-reviewCount"}
    url = f"{_RAKUTEN}?{urllib.parse.urlencode(p)}"
    with urllib.request.urlopen(url, timeout=30) as r:
        return [w.get("Item", w) for w in json.load(r).get("Items", [])]


def _score(it: dict) -> float:
    price = it.get("itemPrice") or 0
    rc = it.get("reviewCount") or 0
    if not (1500 <= price <= 30000) or rc < 30:
        return -1
    if any(ng in it.get("itemName", "") for ng in _NG):
        return -1
    return rc * (it.get("reviewAverage") or 3.0)


def _env() -> dict:
    s = get_settings()
    return {"RAKUTEN_APP_ID": s.rakuten_app_id, "RAKUTEN_ACCESS_KEY": s.rakuten_access_key,
            "RAKUTEN_AFFILIATE_ID": s.rakuten_affiliate_id, "GEMINI_API_KEY": s.gemini_api_key,
            "GEMINI_MODEL": s.gemini_model}


# ---------- 画像候補（商品ページから複数取得） ----------
_IMG_RE = re.compile(r"https://(?:thumbnail\.)?image\.rakuten\.co\.jp/[^\s\"'\\<>]+?\.(?:jpg|jpeg|png)",
                     re.IGNORECASE)


def image_candidates(item: dict, *, limit: int = 12) -> list[str]:
    """API画像＋商品ページのギャラリー画像を集めて候補URL配列に（文字入りも含む）。"""
    cands: list[str] = []
    for img in item.get("mediumImageUrls", []):
        u = (img.get("imageUrl") if isinstance(img, dict) else img) or ""
        if u:
            cands.append(u.split("?_ex=")[0] + "?_ex=500x500")
    try:
        req = urllib.request.Request(item.get("itemUrl", ""),
                                     headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=20) as r:
            html = r.read().decode("utf-8", "ignore")
        for u in _IMG_RE.findall(html):
            base = u.split("?")[0]
            if "/cabinet/" in base or "/" + str(item.get("shopCode", "x")) + "/" in base:
                cands.append(base)
    except Exception:  # noqa: BLE001
        pass
    # 正規化＆重複除去（拡張子前のサイズ指定差を吸収）
    seen, out = set(), []
    for u in cands:
        key = u.split("?")[0]
        if key not in seen:
            seen.add(key)
            out.append(u)
    return out[:limit]


# ---------- キャプション生成 ----------
def _gemini_json(prompt: str, e: dict) -> dict:
    body = {"contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.9, "thinkingConfig": {"thinkingBudget": 0}}}
    req = urllib.request.Request(_GEMINI.format(m=e.get("GEMINI_MODEL", "gemini-3.1-flash-lite"),
                                                k=e["GEMINI_API_KEY"]),
                                 data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        d = json.load(r)
    t = "".join(p.get("text", "") for p in d["candidates"][0]["content"]["parts"]
                if not p.get("thought")).strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1].rsplit("```", 1)[0]
    return json.loads(t[t.find("{"):t.rfind("}") + 1])


def _make_caption(persona: str, item: dict, e: dict) -> dict:
    prompt = f"""あなたはThreadsで商品を紹介する日本語アフィリエイターです。
人格・口調: {persona or "親しみやすく絵文字を適度に使う"}

# 商品
- 商品名(楽天生データ): {item.get('itemName','')}
- 価格: {item.get('itemPrice')}円 / レビュー: ★{item.get('reviewAverage')}（{item.get('reviewCount')}件）

# 出力(JSONのみ・コードフェンス禁止)
{{
  "clean_name": "宣伝文句を除いた簡潔な商品名(20字以内)",
  "caption": "Threads投稿の本文。1行目に思わず読みたくなるフック(絵文字可)。2〜4行で誰にどんな場面で役立つかを具体的に。誇大/断定(最高/絶対/必ず)禁止。URLは入れない。200字以内。改行で読みやすく。末尾は付けない(リンクと#PRは後で機械付与)。"
}}
"""
    return _gemini_json(prompt, e)


# ---------- ドラフト生成 ----------
def generate_drafts(account: dict, count: int) -> int:
    e = _env()
    if not (e["RAKUTEN_APP_ID"] and e["GEMINI_API_KEY"]):
        raise RuntimeError("RAKUTEN/GEMINI のキーが未設定です。")
    genres = account.get("genres") or ["564277"]
    existing = drafts() + queue()
    seen_codes = {d.get("id", "").split("::")[-1] for d in existing}

    items: list[dict] = []
    for g in genres:
        try:
            items += [it for it in _rakuten_search(g, e) if _score(it) > 0]
        except Exception:  # noqa: BLE001
            continue
    items.sort(key=_score, reverse=True)

    made, cur = 0, drafts()
    for it in items:
        if made >= count:
            break
        code = it.get("itemCode", "")
        if not code or code in seen_codes:
            continue
        seen_codes.add(code)
        imgs = image_candidates(it)
        if not imgs:
            continue
        try:
            cap = _make_caption(account.get("persona", ""), it, e)
        except Exception:  # noqa: BLE001
            continue
        cur.append({
            "id": f"{account['id']}::{code}",
            "account": account["id"],
            "product": cap.get("clean_name") or it.get("itemName", "")[:30],
            "price": it.get("itemPrice"),
            "review": {"avg": it.get("reviewAverage"), "count": it.get("reviewCount")},
            "link": it.get("affiliateUrl") or it.get("itemUrl", ""),
            "caption": cap.get("caption", "").strip(),
            "images": imgs,
            "created": int(time.time()),
        })
        made += 1
    if made:
        _save("_threads_drafts", cur[-200:])
    return made


# ---------- スケジュール ----------
def _next_slot(account_id: str, q: list, hours: list[int]) -> int:
    taken = {x["scheduled_at"] for x in q if x.get("account") == account_id}
    now = datetime.now()
    for day in range(0, 60):
        d = (now + timedelta(days=day)).date()
        for h in sorted(hours or [9, 13, 20]):
            slot = datetime(d.year, d.month, d.day, h, random.randint(0, 25))
            ts = int(slot.timestamp())
            if slot > now and ts not in taken:
                return ts
    return int((now + timedelta(hours=1)).timestamp())


# ---------- 承認 / 却下 ----------
def approve(draft_id: str, image_url: str, caption: str, *, when: int | None = None) -> bool:
    ds = drafts()
    d = next((x for x in ds if x["id"] == draft_id), None)
    if not d:
        return False
    rules = (get_rules().get("threads", {}) or {})
    hours = (rules.get("schedule", {}) or {}).get("hours", [9, 13, 20])
    q = queue()
    ts = when or _next_slot(d["account"], q, hours)
    q.append({"id": draft_id, "account": d["account"], "caption": caption.strip(),
              "image": image_url, "link": d.get("link", ""), "product": d.get("product", ""),
              "scheduled_at": ts, "status": "pending", "created": int(time.time())})
    _save("_threads_queue", q)
    _save("_threads_drafts", [x for x in ds if x["id"] != draft_id])
    return True


def reject(draft_id: str) -> bool:
    ds = drafts()
    _save("_threads_drafts", [x for x in ds if x["id"] != draft_id])
    return True


# ---------- 公開（スケジューラ） ----------
def publish_due(*, limit: int = 1) -> list[dict]:
    """scheduled_at<=now の pending を公開（画像メイン＋リンクをリプライ）。"""
    if not threads_client.enabled():
        return []
    q = queue()
    now = int(time.time())
    due = [x for x in q if x.get("status") == "pending" and x.get("scheduled_at", 0) <= now]
    due.sort(key=lambda x: x.get("scheduled_at", 0))
    results = []
    uid = None
    for item in due[:limit]:
        try:
            if uid is None:
                uid = threads_client.me().get("id", "me")
            caption = item["caption"]
            if "#PR" not in caption:
                caption += "\n\n#PR"
            res = threads_client.post_with_link(caption, item["image"], item.get("link", ""),
                                                user_id=uid)
            item["status"] = "published"
            item["permalink"] = (res.get("main") or {}).get("permalink")
            item["published_at"] = now
            results.append({"id": item["id"], "ok": True, "permalink": item["permalink"]})
        except Exception as ex:  # noqa: BLE001
            item["status"] = "error"
            item["error"] = str(ex)[:200]
            results.append({"id": item["id"], "ok": False, "error": str(ex)[:200]})
    _save("_threads_queue", q[-300:])
    return results
