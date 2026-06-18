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

from . import overrides, threads_client, wordpress
from .config import get_rules, get_settings

_RAKUTEN = "https://openapi.rakuten.co.jp/ichibams/api/IchibaItem/Search/20260401"
_GEMINI = ("https://generativelanguage.googleapis.com/v1beta/"
           "models/{m}:generateContent?key={k}")
_GEMINI_IMG = "gemini-2.5-flash-image"   # 合成画像→クリーン商品画像の生成（nano-banana）
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


# ---------- 画像候補 ----------
def api_images(item: dict) -> list[str]:
    """商品自身の画像（楽天APIのmediumImageUrls）を大きめURLで返す。"""
    out = []
    for img in item.get("mediumImageUrls", []):
        u = (img.get("imageUrl") if isinstance(img, dict) else img) or ""
        if u:
            out.append(u.split("?_ex=")[0] + "?_ex=600x600")
    seen, dedup = set(), []
    for u in out:
        k = u.split("?")[0]
        if k not in seen:
            seen.add(k)
            dedup.append(u)
    return dedup


_GALLERY_RE = re.compile(
    r"https://(?:thumbnail\.)?image\.rakuten\.co\.jp/[^\s\"'\\<>]+?\.(?:jpg|jpeg|png)",
    re.IGNORECASE)


def gallery_images(item: dict, limit: int = 9) -> list[str]:
    """商品ページのギャラリー画像（文字入り含む・人が選ぶ前提）。店舗cabinet配下に限定。"""
    shop = item.get("shopCode") or ""
    try:
        req = urllib.request.Request(item.get("itemUrl", ""),
                                     headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=20) as r:
            html = r.read().decode("utf-8", "ignore")
    except Exception:  # noqa: BLE001
        return []
    out, seen = [], set()
    for u in _GALLERY_RE.findall(html):
        base = u.split("?")[0]
        if "/cabinet/" not in base:
            continue
        if shop and f"/{shop}/" not in base and f"@0_mall/{shop}/" not in base:
            continue
        if base not in seen:
            seen.add(base)
            out.append(base)
    return out[:limit]


def ai_clean_image(src_url: str, e: dict) -> str | None:
    """合成画像→商品単体のクリーン画像を生成し、WPメディアに上げて公開URLを返す。

    Threads投稿は公開URL必須のため WP(ouchibase.com) メディアにホスティングする。失敗時 None。
    """
    try:
        import base64
        req = urllib.request.Request(src_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=25) as r:
            src_b64 = base64.b64encode(r.read()).decode()
        prompt = (
            "From this product listing image, isolate ONLY the single main product (one unit, "
            "primary color). Remove ALL text, badges, price labels, watermarks, banners and other "
            "color variants. Place that product centered on a clean minimal soft-gradient studio "
            "background with gentle shadow, photorealistic high-end product photo. Keep its exact "
            "shape, proportions, color and any display/screen accurate. Square composition.")
        body = {"contents": [{"parts": [
            {"inline_data": {"mime_type": "image/jpeg", "data": src_b64}},
            {"text": prompt}]}]}
        rq = urllib.request.Request(_GEMINI.format(m=_GEMINI_IMG, k=e["GEMINI_API_KEY"]),
                                    data=json.dumps(body).encode(),
                                    headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(rq, timeout=120) as r:
            d = json.load(r)
        png = None
        for p in d["candidates"][0]["content"]["parts"]:
            idata = p.get("inline_data") or p.get("inlineData")
            if idata and idata.get("data"):
                png = base64.b64decode(idata["data"])
                break
        if not png:
            return None
        fn = f"th_{int(time.time())}_{random.randint(100,999)}.png"
        res = wordpress.upload_image_bytes(png, filename=fn, content_type="image/png")
        return res.get("source_url") or None
    except Exception:  # noqa: BLE001
        return None


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
  "caption": "メイン投稿(1投稿目)の本文。1行目に思わず読みたくなるフック(絵文字可)。2〜4行で誰にどんな場面で役立つかを具体的に。誇大/断定(最高/絶対/必ず)禁止。URLは入れない。200字以内。改行で読みやすく。末尾は付けない(リンクと#PRは後で機械付与)。",
  "reply": "リプライ(2投稿目)の軽い一言(20〜40字・絵文字可)。URLは入れない。例『気になる方はこちらから🛒』『使ってみたい人はチェック👇』。商品に合わせて自然に。"
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
        real = api_images(it)
        if not real:
            continue
        clean = ai_clean_image(real[0], e)   # AIクリーン商品画像（公開URL）を先頭候補に
        gal = gallery_images(it)             # 商品ページのギャラリー（文字入り含む）
        # 候補＝AIクリーン＋API実画像＋ギャラリー（重複除去・最大12枚）
        imgs, seen_u = [], set()
        for u in ([clean] if clean else []) + real + gal:
            if u and u.split("?")[0] not in seen_u:
                seen_u.add(u.split("?")[0])
                imgs.append(u)
        imgs = imgs[:12]
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
            "reply": cap.get("reply", "気になる方はこちらから🛒").strip(),
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
def approve(draft_id: str, images: list[str], caption: str, reply_text: str = "",
            *, when: int | None = None) -> bool:
    ds = drafts()
    d = next((x for x in ds if x["id"] == draft_id), None)
    if not d:
        return False
    imgs = [u for u in (images or []) if u][:3]   # 最大3枚（カルーセル）
    rules = (get_rules().get("threads", {}) or {})
    hours = (rules.get("schedule", {}) or {}).get("hours", [9, 13, 20])
    q = queue()
    ts = when or _next_slot(d["account"], q, hours)
    q.append({"id": draft_id, "account": d["account"], "caption": caption.strip(),
              "images": imgs, "reply": (reply_text or d.get("reply", "")).strip(),
              "image": imgs[0] if imgs else "",  # 後方互換
              "link": d.get("link", ""), "product": d.get("product", ""),
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
            imgs = item.get("images") or ([item["image"]] if item.get("image") else [])
            res = threads_client.post_set(caption, imgs, item.get("reply", ""),
                                          item.get("link", ""), user_id=uid)
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
