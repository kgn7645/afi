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
from datetime import datetime, timedelta, timezone

_JST = timezone(timedelta(hours=9))   # 公開スケジュールは日本時間で固定（サーバーTZ非依存）

from . import image_pick, overrides, threads_client, wordpress
from .config import get_rules, get_settings


def crop_image(draft_id: str, image_url: str, x: float, y: float, w: float, h: float) -> str | None:
    """画像を指定領域(ピクセル)でトリミング→WPに上げ公開URL化→ドラフトの該当画像を差し替え。"""
    import io
    try:
        img = image_pick._fetch(image_url, timeout=20)
        if img is None:
            return None
        x, y, w, h = int(x), int(y), int(w), int(h)
        box = (max(0, x), max(0, y), min(img.width, x + w), min(img.height, y + h))
        if box[2] - box[0] < 10 or box[3] - box[1] < 10:
            return None
        buf = io.BytesIO()
        img.crop(box).save(buf, format="PNG")
        res = wordpress.upload_image_bytes(buf.getvalue(),
                                           filename=f"thcrop_{int(time.time()*1000)}.png",
                                           content_type="image/png")
        new = res.get("source_url")
        if not new:
            return None
        ds = drafts()
        for d in ds:
            if d.get("id") == draft_id and image_url in (d.get("images") or []):
                d["images"] = [new if u == image_url else u for u in d["images"]]
        _save("_threads_drafts", ds)
        return new
    except Exception:  # noqa: BLE001
        return None


def _hosted_trimmed(urls: list[str]) -> list[str]:
    """選択画像を白ふちトリムしてWPメディアに上げ公開URL化（失敗は原URLで温存）。"""
    out = []
    for u in (urls or [])[:20]:
        try:
            png = image_pick.trim_white_bytes(u)
            if png:
                fn = f"th_{int(time.time()*1000)}.png"
                res = wordpress.upload_image_bytes(png, filename=fn, content_type="image/png")
                out.append(res.get("source_url") or u)
            else:
                out.append(u)
        except Exception:  # noqa: BLE001
            out.append(u)
    return out

_RAKUTEN = "https://openapi.rakuten.co.jp/ichibams/api/IchibaItem/Search/20260401"
_GEMINI = ("https://generativelanguage.googleapis.com/v1beta/"
           "models/{m}:generateContent?key={k}")
_GEMINI_IMG = "gemini-2.5-flash-image"   # 合成画像→クリーン商品画像の生成（nano-banana）
# 美容特化のNG。化粧水/美容液/コスメは許可、薬機法リスク高(医薬品/育毛/サプリ)＋非美容を除外
_NG = ["名入れ", "名前入り", "オーダー", "中古", "訳あり", "ジャンク",
       "医薬品", "第1類", "第2類", "第3類", "育毛", "増毛", "発毛", "サプリメント",
       "木製", "無垢材", "チーク材", "家具", "フレーム", "ポスター", "テーブル", "収納"]


# ---------- 共有ストア ----------
def _load(key: str) -> list:
    return overrides.load(force=True).get(key, []) or []


def _save(key: str, items: list) -> bool:
    return overrides.update({key: items})


def drafts() -> list:
    return _load("_threads_drafts")


# ---------- ラベル（登録してタップ選択） ----------
def labels() -> list:
    return _load("_threads_labels")


def add_label(label: str) -> None:
    label = (label or "").strip()
    if not label:
        return
    ls = labels()
    if label not in ls:
        ls.append(label)
        _save("_threads_labels", ls[-50:])


def remove_label(label: str) -> None:
    _save("_threads_labels", [x for x in labels() if x != (label or "").strip()])


def queue() -> list:
    return _load("_threads_queue")


def products() -> list:
    """商品選定の候補（記事化前・キャプション未生成）。"""
    return _load("_threads_products")


def _trim_item(it: dict) -> dict:
    return {k: it.get(k) for k in ("itemCode", "itemName", "itemPrice", "reviewAverage",
                                   "reviewCount", "itemUrl", "affiliateUrl",
                                   "mediumImageUrls", "shopCode")}


def _add_product(account: dict, it: dict, label: str = "", source: str = "rakuten",
                 review_gist: str = "", cosme_images: list | None = None,
                 source_url: str = "") -> bool:
    code = it.get("itemCode", "")
    if not code:
        return False
    pid = f"{account['id']}::{code}"
    if any(p.get("id") == pid for p in products()) or \
       any(d.get("id") == pid for d in drafts() + queue()):
        return False
    cimgs = [u for u in (cosme_images or []) if u]
    imgs = api_images(it)
    cur = products()
    cur.append({
        "id": pid, "itemCode": code, "account": account["id"],
        "name": (it.get("itemName", "") or "")[:50], "price": it.get("itemPrice"),
        "review": {"avg": it.get("reviewAverage"), "count": it.get("reviewCount")},
        "image": imgs[0] if imgs else (cimgs[0] if cimgs else ""),  # 一覧サムネは表示の安定する楽天優先
        "link": it.get("affiliateUrl") or it.get("itemUrl", ""),
        # 取得元（貼った@cosme/LIPS/楽天ページ。未指定なら楽天商品ページ）。アフィリのlinkとは別物
        "source_url": (source_url or it.get("itemUrl", "")).strip(),
        "label": (label or "").strip(), "source": source,
        "review_gist": (review_gist or "").strip(), "cosme_images": cimgs[:4],
        "item": _trim_item(it), "created": int(time.time()),
    })
    _save("_threads_products", cur[-80:])
    return True


def reject_product(account_id: str, product_id: str) -> bool:
    _save("_threads_products", [p for p in products() if p.get("id") != product_id])
    return True


def genqueue() -> list:
    """Claude Codeモードの生成待ち（プロンプトのみ保持・APIは叩かない）。"""
    return _load("_threads_genqueue")


def _enqueue_pr(account: dict, p: dict) -> None:
    """PR記事化を生成待ちへ。プロンプトと画像/口コミ素材を保持（Gemini不使用）。"""
    it = p.get("item") or {}
    prompt = build_pr_prompt(account.get("persona", ""), it, n=5,
                             label=p.get("label", ""), review_gist=p.get("review_gist", ""),
                             acc_id=account.get("id", ""))
    q = genqueue()
    q.append({
        "id": p["id"], "account": account["id"], "type": "pr",
        "product": (it.get("itemName") or p.get("name", ""))[:40],
        "label": p.get("label", ""), "review_gist": p.get("review_gist", ""),
        "cosme_images": p.get("cosme_images", []), "source": p.get("source", ""),
        "source_url": p.get("source_url", ""),
        "item": it, "prompt": prompt, "created": int(time.time()),
    })
    _save("_threads_genqueue", q[-200:])


def articleize(account: dict, product_id: str) -> str:
    """商品選定でOK→投稿作成。返り 'done'(API生成済) / 'queued'(Claude待ち) / 'fail'。"""
    ps = products()
    p = next((x for x in ps if x.get("id") == product_id), None)
    if not p:
        return "fail"
    if gen_mode() == "claude":
        _enqueue_pr(account, p)
        _save("_threads_products", [x for x in ps if x.get("id") != product_id])
        return "queued"
    e = _env()
    it = p.get("item") or _rakuten_by_code(p.get("itemCode", ""), e)
    if not it:
        return "fail"
    extra = _host_external(p.get("cosme_images", []))  # @cosme/LIPS公式画像をWPへ
    d = _pr_draft_from_item(account, it, e, label=p.get("label", ""),
                            review_gist=p.get("review_gist", ""), extra_images=extra,
                            source_url=p.get("source_url", ""))
    if not d:
        return "fail"
    d["source"] = p.get("source", "")
    d["cosme_image_count"] = len(extra)
    cur = drafts()
    cur.append(d)
    _save("_threads_drafts", cur[-200:])
    _save("_threads_products", [x for x in ps if x.get("id") != product_id])
    return "done"


def pending_generation() -> list:
    """Claude Codeが生成すべきプロンプト一覧（id/type/product/prompt）。"""
    return [{"id": x["id"], "type": x.get("type", "pr"), "product": x.get("product", ""),
             "prompt": x.get("prompt", "")} for x in genqueue()]


def apply_generation(results: dict) -> int:
    """Claude Codeが生成した結果を取り込み→投稿ドラフト化。

    results = {id: {captions:[...], reply, clean_name}}（PR） or {id:{caption}}（つぶやき）。
    """
    q = genqueue()
    by_id = {x["id"]: x for x in q}
    cur = drafts()
    applied, made = [], 0
    for gid, r in (results or {}).items():
        item = by_id.get(gid)
        if not item or not isinstance(r, dict):
            continue
        if item.get("type") == "musing":
            cap = (r.get("caption") or "").strip()
            if not cap:
                continue
            cur.append({"id": gid, "account": item["account"], "type": "musing",
                        "product": "💬 つぶやき", "caption": cap, "created": int(time.time())})
        else:
            caps = {"clean_name": r.get("clean_name", ""),
                    "captions": r.get("captions") or [], "reply": r.get("reply", "")}
            if not [c for c in caps["captions"] if c and c.strip()]:
                continue
            it = item.get("item") or {}
            extra = _host_external(item.get("cosme_images", []))
            d = _pr_draft_from_item({"id": item["account"]}, it, _env(),
                                    label=item.get("label", ""),
                                    review_gist=item.get("review_gist", ""),
                                    extra_images=extra, caps=caps,
                                    source_url=item.get("source_url", ""))
            if not d:
                continue
            d["source"] = item.get("source", "")
            d["cosme_image_count"] = len(extra)
            cur.append(d)
        applied.append(gid)
        made += 1
    if made:
        _save("_threads_drafts", cur[-200:])
    if applied:
        _save("_threads_genqueue", [x for x in q if x["id"] not in applied])
    return made


def collect_products_rakuten(account: dict, count: int) -> int:
    """楽天キーワードから商品候補を収集（キャプション未生成＝Gemini不使用・速い）。"""
    e = _env()
    keywords = account.get("keywords") or _BEAUTY_KEYWORDS
    seen = {p.get("itemCode") for p in products()}
    items: list[dict] = []
    for kw in keywords:
        try:
            items += [it for it in _rakuten_search(kw, e, by_keyword=True) if _score(it) > 0]
        except Exception:  # noqa: BLE001
            continue
    items.sort(key=_score, reverse=True)
    made = 0
    for it in items:
        if made >= count:
            break
        if it.get("itemCode") in seen:
            continue
        if _add_product(account, it, "", "rakuten"):
            seen.add(it.get("itemCode"))
            made += 1
    return made


def collect_mens_discovery(account: dict, count: int) -> int:
    """メンズ収集元（m-cosme/@cosmeメンズ）で発見した商品名を楽天照合→選定に追加。"""
    from . import mens_discover
    e = _env()
    names = mens_discover.mens_product_names(per_genre=1)
    seen = {p.get("itemCode") for p in products()}
    made = 0
    for name in names:
        if made >= count:
            break
        try:
            it = _rakuten_best_match(name, e)
        except Exception:  # noqa: BLE001
            continue
        if not it or it.get("itemCode") in seen:
            continue
        if _add_product(account, it, "", "mens", source_url=""):
            seen.add(it.get("itemCode"))
            made += 1
    return made


# ---------- 楽天 商品選定 ----------
# 美容の既定キーワード（アカウントにkeywords/genres未指定時に使用）
_BEAUTY_KEYWORDS = ["リップティント", "アイシャドウ パレット", "チーク 頬紅", "マスカラ コスメ",
                    "フェイスパウダー", "化粧下地", "アイブロウ ペンシル", "コンシーラー コスメ",
                    "ハイライター コスメ", "クレンジングバーム", "美容液 スキンケア", "ヘアオイル 洗い流さない"]


def _rakuten_search(genre_or_kw: str, e: dict, *, by_keyword: bool = False) -> list[dict]:
    p = {"applicationId": e["RAKUTEN_APP_ID"], "accessKey": e["RAKUTEN_ACCESS_KEY"],
         "affiliateId": e.get("RAKUTEN_AFFILIATE_ID", ""),
         "hits": 30, "format": "json", "imageFlag": 1, "availability": 1,
         "sort": "-reviewCount"}
    if by_keyword:
        p["keyword"] = genre_or_kw
    else:
        p["genreId"] = str(genre_or_kw)
    url = f"{_RAKUTEN}?{urllib.parse.urlencode(p)}"
    with urllib.request.urlopen(url, timeout=30) as r:
        return [w.get("Item", w) for w in json.load(r).get("Items", [])]


def _score(it: dict) -> float:
    price = it.get("itemPrice") or 0
    rc = it.get("reviewCount") or 0
    # 美容はプチプラ(¥500〜)も対象。レビューは10件以上で社会的証明
    if not (500 <= price <= 30000) or rc < 10:
        return -1
    if any(ng in it.get("itemName", "") for ng in _NG):
        return -1
    return rc * (it.get("reviewAverage") or 3.0)


def threads_model() -> str:
    """Threads専用モデル（threads.gemini_model）> 全体のモデル。媒体別AI設定。"""
    t = ((get_rules().get("threads", {}) or {}).get("gemini_model") or "").strip()
    if t:
        return t
    from .gemini_client import resolve_model
    return resolve_model()


def _env(model: str | None = None) -> dict:
    s = get_settings()
    return {"RAKUTEN_APP_ID": s.rakuten_app_id, "RAKUTEN_ACCESS_KEY": s.rakuten_access_key,
            "RAKUTEN_AFFILIATE_ID": s.rakuten_affiliate_id, "GEMINI_API_KEY": s.gemini_api_key,
            "GEMINI_MODEL": model or threads_model()}


def _render_prompt(tmpl: str, **kw) -> str:
    """[[key]] 形式のプレースホルダを置換（編集可能プロンプト用・壊れにくい）。"""
    for k, v in kw.items():
        tmpl = tmpl.replace(f"[[{k}]]", str(v))
    return tmpl


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


# 美容PR投稿のフック型30種（美容系9アカの実分析。毎回ランダムに割り当て単調化を防ぐ）
_PR_HOOKS = [
    ("全女子へ宣言", "『全女子へ。◯◯したいときは これを塗るのです。』全女子/全人類への呼びかけ＋断定"),
    ("逆説・買わないで", "『良すぎるから、これマジで買わないで…✋』良すぎて困る、の逆説で煽る"),
    ("先生の証言", "『ピラティス(エステ/皮膚科)の先生に教わったこれ、』プロからの又聞きで権威付け"),
    ("店員の証言", "『◯◯のスタッフに絶対使ってって勧められたやつ、』店員のおすすめ体で"),
    ("美人の証言", "『職場の爆モテ美女が使ってたやつ、』身近な美人が使ってた設定"),
    ("質問された設定", "『「それ何使ってるの？」って聞かれるようになった…』他人に褒められた証明"),
    ("競合名指し全否定", "『これ買ってから、◯◯も△△も存在忘れた😂』他ブランドを忘れた＝最強宣言"),
    ("即効性の驚き", "『使った瞬間から◯◯が変わる』『秒で盛れる』即効性を強調"),
    ("ビフォーアフター実感", "『毎日続けてたら見え方変わってきた』続けた結果の変化を実感ベースで"),
    ("今までの◯◯は何だったの", "『今までの◯◯ってなんだったんだろう…』過去を否定するほどの感動"),
    ("メタ・このアカ褒め", "『このアカ、美容の裏話ぶっちゃけすぎ』アカウント自体を推す型"),
    ("多幸感・感動", "『良いなんてもんじゃない、日に日に怖いくらい』語彙が追いつかない感動"),
    ("コスパ/タイパ最強", "『プチプラなのにこの仕上がり、タイパもコスパも優勝』安いのに高見え"),
    ("バズ乗っかり", "『◯◯でバズってるアレ、知ってる？🥹』話題化を入口に"),
    ("正直レビュー", "『正直レビューします。◯◯は△△だけど…』忖度なし宣言から"),
    ("悩み提起", "『毛穴(くすみ/乾燥/クマ)、もう諦めてた人だけ見て』悩みを名指しで刺す"),
    ("プロ vs プチプラ", "『デパコス級なのにこの値段は反則…』高級品と比較して持ち上げる"),
    ("こんな人に刺さる", "『一重さん(ブルベ/敏感肌)に全力でおすすめしたい』属性を名指しで"),
    ("ぶっちゃけ結論", "『結論、もう◯◯はこれでいい。』先に結論を言い切る"),
    ("時短・ズボラ", "『忙しい朝でも5秒で完成、ズボラの救世主』手抜きでも盛れる"),
    ("リピート告白", "『何個リピしたか分からん…無くなると不安になるやつ』リピ買いの愛"),
    ("意外性ギャップ", "『見た目地味なのに、実力えぐい』地味だけど強い、のギャップ"),
    ("季節の必需品", "『この時期これ無いと無理、毎年戻ってくる』季節×定番"),
    ("自分史上最高", "『人生で一番◯◯だったかも』自分史上更新の主観マックス"),
    ("詩的・感覚", "『鏡を見るのが、ちょっと楽しみになる』情景・感覚でそっと"),
    ("お祝い・ご褒美", "『頑張った自分へのご褒美、これにした』自分へのご褒美文脈"),
    ("失敗からの救済", "『何回失敗したか…やっと辿り着いた正解がこれ』遠回りの末の正解"),
    ("みんな使ってる安心", "『気づいたら周りみんな持ってた』社会的証明で安心させる"),
    ("ツッコミ・ブランドへ", "『◯◯さん、あのさ、これはやりすぎでは…？』ブランドに愛のツッコミ"),
    ("値段の驚き(たまに)", "『え、この値段で良いの…？二度見した』値段はたまに、毎回使わない"),
]


_YAKKIHO = ("【薬機法・厳守】医薬品的な効果効能の断定は禁止。NG例『シミ/シワが消える』『毛穴がなくなる』"
            "『治る』『アンチエイジング』『細胞が〜』『美白(医薬部外品以外)』。"
            "仕上がり表現(盛れる/血色感/ツヤ/透明感/清潔感/垢抜け)は可。"
            "スキンケアは『うるおう/肌が整う/個人の感想』の体に留める。")


_DEFAULT_PR_PROMPT = """あなたはThreadsで人気の日本語の美容・グルーミング系の発信者の“中の人”です。
下の人物に**完全に憑依**して、その人の性別・世界観・語彙・絵文字感で書く: [[persona]]

# 商品
- 商品名(楽天生データ): [[item_name]]
- 価格: [[price]]円 / レビュー: ★[[review_avg]]（[[review_count]]件）

# タスク: メイン投稿(1投稿目)の本文を [[n]]案、それぞれ別のフック型で作る。
[[styles]]

# 鉄則（人気アカ分析より）
- 各案、指定の型で書き出しを変える。**短く(50〜90字・最大2行)**、説明しすぎない。
- 値段で始めない(値段型のときだけ可)。スペック羅列禁止。
- **余韻で次を読ませる**: 文末を「…」「、、」「んだけど,」で途切れさせ続きをリプライへ。
- **語彙・絵文字はペルソナに合わせる**（女性メイク語彙＝盛れる/血色感/濡れツヤ と、メンズ語彙＝清潔感/垢抜け/モテ/自己投資 を取り違えない）。[[yakkiho]]

# 出力(JSONのみ・コードフェンス禁止)
{"clean_name":"簡潔な商品名(20字以内・宣伝文句除く)","captions":["案1の本文","案2の本文","案3の本文","案4の本文","案5の本文"],"reply":"リプライ(2投稿目)の軽い一言(15〜35字・絵文字可・URL無し)。例『これです🛒』『気になる人だけどうぞ👇』"}
"""


def pr_prompt_template() -> str:
    from . import prompt_presets
    return prompt_presets.active_value("th_pr_prompt")


# 口コミ→傾向の抽象要約（原文は保持しない＝転載回避。事実/傾向のみ・薬機法配慮）
_REVIEW_SUMMARY_PROMPT = """以下は美容商品「[[name]]」への第三者の口コミ（@cosme/LIPS等）の断片です。
これを参考に、**事実・傾向だけ**を日本語で抽出してください。

# 厳守
- 原文の言い回し・文をそのまま使わない（引用・転載は禁止）。固有の表現は捨て、傾向だけ一般化する。
- 多くの人が触れている点を優先。少数の極端な意見・無関係なノイズは除く。
- 化粧品の使用感（発色/色持ち/うるおい/質感/香り/塗り心地/コスパ 等）に限定。
- [[yakkiho]]

# 出力(JSONのみ・コードフェンス禁止)
{"pros":["良い点を一般化した短句",... 最大4],"cons":["気になる点",... 最大2],"gist":"全体傾向を中立に1〜2文"}

# 口コミ断片
[[snippets]]
"""


def summarize_reviews(name: str, snippets: list, e: dict) -> dict:
    """口コミ断片 → 傾向の抽象要約。原文は保持・返却しない。返り {pros[],cons[],gist}。"""
    clean = [re.sub(r"\s+", " ", str(s)).strip()[:200] for s in (snippets or []) if s and str(s).strip()]
    clean = [s for s in clean if len(s) >= 8][:30]
    if len(clean) < 3:
        return {}
    prompt = _render_prompt(_REVIEW_SUMMARY_PROMPT, name=name, yakkiho=_YAKKIHO,
                            snippets="\n".join(f"- {s}" for s in clean))
    try:
        out = _gemini_json(prompt, e)
    except Exception:  # noqa: BLE001
        return {}
    return {"pros": [str(x).strip() for x in (out.get("pros") or [])][:4],
            "cons": [str(x).strip() for x in (out.get("cons") or [])][:2],
            "gist": (out.get("gist") or "").strip()}


def gist_text(g) -> str:
    """要約dict → プロンプト/保存用のテキスト1本に整形。"""
    if not g:
        return ""
    if isinstance(g, str):
        return g.strip()
    parts = []
    if g.get("gist"):
        parts.append(g["gist"])
    if g.get("pros"):
        parts.append("◎良い点: " + " / ".join(g["pros"]))
    if g.get("cons"):
        parts.append("△気になる点: " + " / ".join(g["cons"]))
    return "\n".join(parts)


def gen_mode() -> str:
    """文章生成のモード。api=Gemini自動 / claude=Claude Codeが生成（API不使用）。"""
    m = ((get_rules().get("threads", {}) or {}).get("gen_mode") or "api").strip()
    return "claude" if m == "claude" else "api"


def publish_mode() -> str:
    """公開モード。live=Threadsへ実投稿 / draft_only=投稿せず承認済みを溜めるだけ（温め運用）。

    既定は draft_only（安全側）。新規アカ温め中の誤爆BAN防止。
    """
    m = ((get_rules().get("threads", {}) or {}).get("publish_mode") or "draft_only").strip()
    return "live" if m == "live" else "draft_only"


# ---------- アカウント（媒体）管理 ----------
def accounts() -> list:
    """Threadsアカウント（媒体）一覧。"""
    accs = (get_rules().get("threads", {}) or {}).get("accounts") or []
    return accs if accs else [{"id": "mmmtreees", "name": "アカウント1"}]


def get_account(acc_id: str = "") -> dict:
    accs = accounts()
    for a in accs:
        if a.get("id") == acc_id:
            return a
    return accs[0]


def account_token(acc) -> str:
    """アカウント別の公開トークン。空なら threads_client が env にフォールバック。"""
    if isinstance(acc, str):
        acc = get_account(acc)
    return (acc.get("token") or "").strip()


def account_publish_mode(acc) -> str:
    """アカウント別の公開モード。未設定は全体設定にフォールバック。"""
    if isinstance(acc, str):
        acc = get_account(acc)
    m = (acc.get("publish_mode") or "").strip()
    return m if m in ("live", "draft_only") else publish_mode()


# ---------- スタイル型（フック/ネタ型）の管理：媒体ごと・UIから確認/追加/削除 ----------
def style_types(kind: str, acc=None) -> list:
    """スタイル型のdictリスト [{name,ex},...]。媒体の設定があればそれ、無ければ既定（コード）。

    kind: 'pr'=PR投稿のフック型 / 'musing'=つぶやきのネタ型。acc=媒体id or dict（媒体別）。
    """
    key = "pr_hooks" if kind == "pr" else "musing_types"
    if acc is not None:
        a = get_account(acc) if isinstance(acc, str) else acc
        cfg = (a or {}).get(key)
        if cfg:
            out = [{"name": (h.get("name") or "").strip(), "ex": (h.get("ex") or "").strip()}
                   for h in cfg if (h.get("name") or "").strip()]
            if out:
                return out
    defaults = _PR_HOOKS if kind == "pr" else _MUSING_TYPES
    return [{"name": n, "ex": e} for n, e in defaults]


def _save_account_styles(acc_id: str, kind: str, lst: list) -> bool:
    key = "pr_hooks" if kind == "pr" else "musing_types"
    accts = accounts()
    for a in accts:
        if a.get("id") == acc_id:
            a[key] = lst
            return overrides.update({"threads": {"accounts": accts}})
    return False


def add_style(acc_id: str, kind: str, name: str, ex: str) -> bool:
    """媒体にスタイル型を追加（同名は上書き）。未設定時は既定を確定してから追加。"""
    name = (name or "").strip()
    if not name:
        return False
    cur = style_types(kind, acc_id)
    for h in cur:
        if h["name"] == name:
            h["ex"] = (ex or "").strip()
            return _save_account_styles(acc_id, kind, cur)
    cur.append({"name": name, "ex": (ex or "").strip()})
    return _save_account_styles(acc_id, kind, cur)


def delete_style(acc_id: str, kind: str, name: str) -> bool:
    cur = [h for h in style_types(kind, acc_id) if h["name"] != name]
    if not cur:                                  # 全消し防止
        return False
    return _save_account_styles(acc_id, kind, cur)


def reset_style(acc_id: str, kind: str) -> bool:
    """媒体の設定を消して既定（コードの30種/8種）に戻す。"""
    key = "pr_hooks" if kind == "pr" else "musing_types"
    accts = accounts()
    changed = False
    for a in accts:
        if a.get("id") == acc_id and key in a:
            a.pop(key)
            changed = True
    return overrides.update({"threads": {"accounts": accts}}) if changed else True


def build_pr_prompt(persona: str, item: dict, n: int = 5, *,
                    tmpl: str = "", label: str = "", review_gist: str = "",
                    acc_id: str = "") -> str:
    """PR投稿の最終プロンプト文字列を組み立てる（Gemini/Claude共通）。媒体別スタイル型を使用。"""
    hooks = style_types("pr", acc_id or None) or [{"name": n, "ex": e} for n, e in _PR_HOOKS]
    styles = random.sample(hooks, min(n, len(hooks)))
    style_lines = "\n".join(f"  案{i+1}「{h['name']}」型: {h['ex']}" for i, h in enumerate(styles))
    prompt = _render_prompt(
        tmpl or pr_prompt_template(),
        persona=persona or "美容好きの等身大。正直レビュー、絵文字多め。盛れる/血色感などの美容語彙",
        item_name=item.get("itemName", ""), price=item.get("itemPrice"),
        review_avg=item.get("reviewAverage"), review_count=item.get("reviewCount"),
        n=n, styles=style_lines, yakkiho=_YAKKIHO)
    if review_gist.strip():
        prompt += ("\n\n# 口コミの傾向（@cosme/LIPSの多数意見をAIが要約した参考メモ。"
                   "**原文の引用・転載は禁止。自分の言葉で書く**。効能効果の断定もしない）\n"
                   + review_gist.strip()
                   + "\n→ この傾向を踏まえて具体性とリアルさを出す（例: 色持ち・発色・質感など実際に支持されている点）。")
    if label.strip():
        lb = label.strip()
        prompt += (f"\n\n# ラベル指定（重要）\n{n}案のうち**最低1案**は「{lb}」を自然に織り込む"
                   f"（例『{lb}も愛用してるらしい』『{lb}おすすめの』『{lb}っぽい雰囲気』）。"
                   f"全案には入れない。文脈に無理がないように。")
    return prompt


def _norm_caps(out: dict, n: int = 5) -> dict:
    caps = [c.strip() for c in (out.get("captions") or []) if c and c.strip()]
    out["captions"] = caps[:n] or [out.get("caption", "")]
    return out


def _make_captions(persona: str, item: dict, e: dict, n: int = 5, *,
                   tmpl: str = "", label: str = "", review_gist: str = "", acc_id: str = "") -> dict:
    """1商品につき n 案（異なるフック型）のキャプションをGeminiで生成。返り {clean_name, captions[], reply}。"""
    prompt = build_pr_prompt(persona, item, n, tmpl=tmpl, label=label,
                             review_gist=review_gist, acc_id=acc_id)
    return _norm_caps(_gemini_json(prompt, e), n)


# つぶやきのネタ型（参考アカ分析より・毎回ランダムで単調化を防ぐ）
_MUSING_TYPES = [
    ("あるある共感", "日常の小さな困りごと/つい笑う瞬間。『◯◯しがちじゃない？』"),
    ("正直なグチ・本音", "嫌味にならない軽さで。『もう◯◯なのよ、ほんと』"),
    ("小さな発見・幸せ", "最近知って良かった/ちょっと嬉しかった事。『◯◯、地味に最高』"),
    ("問いかけ", "みんなはどう？とコメントを誘う。『◯◯派？△△派？』"),
    ("失敗談・自虐", "共感を呼ぶ自虐。『またやってしまった…』"),
    ("季節・天気・時事に乗っかり", "『急に寒い』『連休、もう終わる』等の軽い時事"),
    ("自分ルール/こだわり告白", "『私、◯◯だけは譲れない』ちょっとした主義主張"),
    ("ニュース/トレンドにツッコミ", "話題の物事に一言。『◯◯コラボ、誰狙い？』"),
]


_DEFAULT_MUSING_PROMPT = """あなたはThreadsで人気の日本語の美容・グルーミング系の“中の人”です。
下の人物に**完全に憑依**して、その人の性別・語彙・絵文字感で書く: [[persona]]
世界観: [[niche]]

# タスク: 商品宣伝ではない「日常の共感つぶやき」を1つ。
- ネタの型: 「[[type_name]]」… [[type_ex]]
- **書き出しは毎回変える**。今回は「[[opener]]」始める。「結局、」で始めるのは禁止。
- 超口語＋感情＋等身大。短く(60字前後・最大80字)。思わず「わかる」と言いたくなる一言。
- **語彙・トーンはペルソナに合わせる**（女性らしさ/男っぽさを人格に沿って）。
- 商品名・リンク・宣伝・#PR・ハッシュタグは入れない。
- 良い温度感: 「いつまでYouTube見とるんじゃあぁぁ！！」「努力でここまで変われるの尊い」

# 出力(JSONのみ)
{"caption": "つぶやき本文"}
"""


def musing_prompt_template() -> str:
    from . import prompt_presets
    return prompt_presets.active_value("th_musing_prompt")


def build_musing_prompt(account: dict, *, tmpl: str = "") -> str:
    """つぶやきの最終プロンプト文字列を組み立てる（毎回ネタ型/書き出しをランダム）。"""
    types = style_types("musing", account.get("id")) or [{"name": n, "ex": e} for n, e in _MUSING_TYPES]
    pick = random.choice(types)
    name, ex = pick["name"], pick["ex"]
    opener = random.choice(["結局/つまり以外で", "問いかけで", "情景描写で", "感情の一言で",
                            "『え、』『うそ、』等の驚きで", "ぼやき/ひとりごとで"])
    return _render_prompt(
        tmpl or musing_prompt_template(),
        persona=account.get("persona", "") or "親しみやすく絵文字。正直で等身大",
        niche=account.get("name", "美容・暮らし"),
        type_name=name, type_ex=ex, opener=opener)


def _make_musing(account: dict, e: dict, *, tmpl: str = "") -> dict:
    return _gemini_json(build_musing_prompt(account, tmpl=tmpl), e)


def test_generate(account: dict, *, model: str = "", pr_tmpl: str = "",
                  musing_tmpl: str = "") -> dict:
    """現在のモデル/プロンプトで、固定商品に対するサンプル文を生成（保存しない）。"""
    e = _env(model or None)
    item = {"itemName": "薬用 オールインワン保湿ジェル 化粧水 メンズ レディース 兼用",
            "itemPrice": 1980, "reviewAverage": 4.5, "reviewCount": 850}
    demo_gist = ("うるおいが続きベタつかない使用感が高評価。手軽さとコスパで支持されている。\n"
                 "◎良い点: しっとりするのにベタつかない / 1本で時短 / コスパが良い\n"
                 "△気になる点: 乾燥が強い時は物足りない人も")
    result = {"model": e["GEMINI_MODEL"], "product": item["itemName"][:24],
              "captions": [], "reply": "", "musing": "", "error": ""}
    try:
        cap = _make_captions(account.get("persona", ""), item, e, n=5, tmpl=pr_tmpl,
                             review_gist=demo_gist, acc_id=account.get("id", ""))
        result["captions"] = cap.get("captions", [])
        result["reply"] = cap.get("reply", "")
    except Exception as ex:  # noqa: BLE001
        result["error"] = f"PR生成エラー: {ex}"
    try:
        mus = _make_musing(account, e, tmpl=musing_tmpl)
        result["musing"] = mus.get("caption", "")
    except Exception as ex:  # noqa: BLE001
        result["error"] += f" / つぶやきエラー: {ex}"
    return result


def generate_musings(account: dict, count: int) -> int:
    """日常つぶやきドラフトを生成（画像・リンク無し）。claudeモードは生成待ちへ積む。"""
    if gen_mode() == "claude":
        q = genqueue()
        base = int(time.time() * 1000)
        for i in range(max(0, count)):
            q.append({
                "id": f"{account['id']}::musing::{base}::{i}", "account": account["id"],
                "type": "musing", "product": "💬 つぶやき",
                "prompt": build_musing_prompt(account), "created": int(time.time()),
            })
        _save("_threads_genqueue", q[-200:])
        return count
    e = _env()
    if not e["GEMINI_API_KEY"]:
        return 0
    cur, made = drafts(), 0
    for _ in range(count):
        try:
            m = _make_musing(account, e)
        except Exception:  # noqa: BLE001
            continue
        cap = (m.get("caption") or "").strip()
        if not cap:
            continue
        cur.append({
            "id": f"{account['id']}::musing::{int(time.time()*1000)}::{made}",
            "account": account["id"], "type": "musing",
            "product": "💬 つぶやき", "caption": cap, "created": int(time.time()),
        })
        made += 1
        time.sleep(0.3)
    if made:
        _save("_threads_drafts", cur[-200:])
    return made


def _host_external(urls: list, limit: int = 4) -> list:
    """外部画像URL（@cosme/LIPS等）をDLしてWP(ouchibase)へホスト。

    Threadsは公開URL必須＋元CDNのホットリンク回避のため、自社メディアに載せ替える。失敗分はスキップ。
    """
    import io
    from PIL import Image
    out = []
    for u in (urls or [])[:limit]:
        if not u:
            continue
        try:
            req = urllib.request.Request(
                u, headers={"User-Agent": "Mozilla/5.0",
                            "Accept": "image/avif,image/webp,image/png,image/jpeg,*/*"})
            with urllib.request.urlopen(req, timeout=25) as r:
                data = r.read()
            # webp/avif/png をJPEGに正規化（Threadsは JPEG/PNG のみ・公開URL必須）
            im = Image.open(io.BytesIO(data))
            if im.mode not in ("RGB", "L"):
                im = im.convert("RGB")
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=88)
            fn = f"th_ext_{int(time.time())}_{random.randint(100, 999)}.jpg"
            res = wordpress.upload_image_bytes(buf.getvalue(), filename=fn,
                                               content_type="image/jpeg")
            su = res.get("source_url")
            if su:
                out.append(su)
        except Exception:  # noqa: BLE001
            continue
    return out


def _name_match(prod_name: str, query: str, need: int = 2) -> bool:
    """検索結果の商品名 prod_name が query と十分一致するか（無関係画像の混入防止）。"""
    toks = [t for t in re.split(r"[\s　/・]+", query) if len(t) >= 2]
    ov = sum(1 for t in toks if t in (prod_name or ""))
    return ov >= min(need, max(1, len(toks)))


def _mcosme_image_search(name: str, limit: int = 4) -> list:
    """m-cosmeで商品名検索→一致した商品の公式画像（Shopify products.json）。"""
    import requests
    try:
        r = requests.get("https://www.m-cosme.com/search",
                         params={"q": name, "type": "product"}, headers=_BROWSER_HEADERS, timeout=20)
        handles = list(dict.fromkeys(re.findall(r"/products/([a-z0-9-]+)", r.text)))[:3]
    except Exception:  # noqa: BLE001
        return []
    out = []
    for h in handles:
        try:
            pr = requests.get(f"https://www.m-cosme.com/products/{h}.json",
                              headers=_BROWSER_HEADERS, timeout=15)
            if pr.status_code != 200:
                continue
            p = json.loads(pr.text).get("product", {})
            if not _name_match(p.get("title", ""), name):    # 名前一致のみ採用
                continue
            for im in p.get("images", [])[:limit]:
                s = im.get("src") if isinstance(im, dict) else im
                if s:
                    out.append(s)
            if out:
                break                                        # 最初の一致商品で十分
        except Exception:  # noqa: BLE001
            continue
    return out[:limit]


def _lips_image_search(name: str, limit: int = 4) -> list:
    """LIPSで商品名検索→一致した商品の公式画像（JSON-LD）。"""
    import requests
    try:
        s = requests.Session()
        s.get("https://lipscosme.com/", headers=_BROWSER_HEADERS, timeout=20)
        r = s.get("https://lipscosme.com/search", params={"query": name},
                  headers={**_BROWSER_HEADERS, "Referer": "https://lipscosme.com/"}, timeout=20)
        ids = list(dict.fromkeys(re.findall(r"/products/(\d+)", r.text)))[:3]
    except Exception:  # noqa: BLE001
        return []
    for pid in ids:
        info = _crawl_review_http(f"https://lipscosme.com/products/{pid}")
        if info and info.get("name") and _name_match(info["name"], name):
            return (info.get("images") or [])[:limit]
    return []


def fetch_more_images(draft_id: str) -> int:
    """画像候補を 楽天＋取得元と別の美容媒体(m-cosme/LIPS) から名前一致で取得して追加。

    商品名で各媒体を検索し、名前が一致した商品の公式画像だけ採用（無関係画像の混入を防ぐ）。
    取得元の媒体は重複なので除外する。
    """
    ds = drafts()
    d = next((x for x in ds if x.get("id") == draft_id), None)
    if not d or d.get("type") == "musing":
        return 0
    e = _env()
    code = draft_id.split("::", 1)[-1]
    it = _rakuten_by_code(code, e) or {}
    src = (d.get("source") or "").lower()
    name = re.sub(r"[【】\[\]★●（）()]|ポイント\d+倍|送料無料|医薬部外品|[0-9]+(?:ml|g|mL|個|袋|本)",
                  " ", d.get("product") or it.get("itemName", ""))
    name = re.sub(r"\s+", " ", name).strip()[:30]
    cands = []
    cands += api_images(it) + gallery_images(it)             # 楽天（リンク商品自身のギャラリー）
    cands += _mcosme_image_search(name)                      # 別媒体①: m-cosme公式（名前一致のみ）
    if src != "lips":                                        # LIPS由来なら既に公式画像あり→重複回避
        cands += _lips_image_search(name)                    # 別媒体②: LIPS公式（名前一致のみ）
    seen, uniq = set(), []
    for u in cands:
        if not (u or "").startswith("http"):
            continue
        k = u.split("?")[0].rsplit("/", 1)[-1]
        if k and k not in seen:
            seen.add(k)
            uniq.append(u)
    external = [u for u in uniq if not re.search(r"rakuten|r10s", u)]
    rakuten = [u for u in uniq if re.search(r"rakuten|r10s", u)]
    hosted = _host_external(external, limit=8)               # 外部はWPへホスト（表示&Threads対応）
    new_imgs = hosted + rakuten
    merged, mseen = [], set()
    for u in new_imgs + (d.get("images") or []):
        k = u.split("?")[0].rsplit("/", 1)[-1]
        if u and k and k not in mseen:
            mseen.add(k)
            merged.append(u)
    d["images"] = merged[:16]
    _save("_threads_drafts", ds)
    return len(new_imgs)


# ---------- ドラフト生成 ----------
def _pr_draft_from_item(account: dict, it: dict, e: dict, *, label: str = "",
                        review_gist: str = "", extra_images: list | None = None,
                        caps: dict | None = None, source_url: str = "") -> dict | None:
    """楽天itemから PRドラフト(画像ランク＋5案キャプション) を組み立てる。

    extra_images（@cosme/LIPSの公式商品画像をWPにホスト済みのURL）があれば先頭に置く。
    caps（{clean_name,captions[],reply}）を渡すとGemini生成をスキップ（Claude Codeモード用）。
    """
    code = it.get("itemCode", "")
    extra = [u for u in (extra_images or []) if u]
    real = api_images(it)
    if not real and not extra:
        return None
    gal = gallery_images(it)
    dedup, seen_u = [], set()
    for u in real + gal:
        if u and u.split("?")[0] not in seen_u:
            seen_u.add(u.split("?")[0])
            dedup.append(u)
    try:
        ranked = image_pick.rank_urls(dedup, limit=10) if dedup else []
    except Exception:  # noqa: BLE001
        ranked = dedup[:10]
    imgs = (extra + ranked)[:12]   # 公式画像（@cosme/LIPS）を先頭・楽天は補完
    if not imgs:
        return None
    if caps is not None:
        cap = _norm_caps(dict(caps), 5)
    else:
        try:
            cap = _make_captions(account.get("persona", ""), it, e, n=5, label=label,
                                 review_gist=review_gist, acc_id=account.get("id", ""))
        except Exception:  # noqa: BLE001
            return None
    opts = [o.strip() for o in (cap.get("captions") or []) if o.strip()]
    if not opts:
        return None
    return {
        "id": f"{account['id']}::{code}",
        "account": account["id"], "type": "pr", "label": label.strip(),
        "product": cap.get("clean_name") or it.get("itemName", "")[:30],
        "price": it.get("itemPrice"),
        "review": {"avg": it.get("reviewAverage"), "count": it.get("reviewCount")},
        "link": it.get("affiliateUrl") or it.get("itemUrl", ""),
        "source_url": (source_url or it.get("itemUrl", "")).strip(),   # 取得元ページ（表示用）
        "caption": opts[0], "caption_options": opts,
        "reply": cap.get("reply", "気になる方はこちらから🛒").strip(),
        "images": imgs, "review_gist": review_gist.strip(), "created": int(time.time()),
    }


def _rakuten_by_code(item_code: str, e: dict) -> dict | None:
    p = {"applicationId": e["RAKUTEN_APP_ID"], "accessKey": e["RAKUTEN_ACCESS_KEY"],
         "affiliateId": e.get("RAKUTEN_AFFILIATE_ID", ""), "itemCode": item_code,
         "hits": 1, "format": "json"}
    try:
        with urllib.request.urlopen(f"{_RAKUTEN}?{urllib.parse.urlencode(p)}", timeout=30) as r:
            items = [w.get("Item", w) for w in json.load(r).get("Items", [])]
        return items[0] if items else None
    except Exception:  # noqa: BLE001
        return None


def _page_item_id(url: str) -> str:
    """楽天商品ページから数値itemIdを抽出（EUC-JP/UTF-8両対応）。"""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        raw = urllib.request.urlopen(req, timeout=20).read()
    except Exception:  # noqa: BLE001
        return ""
    for enc in ("euc-jp", "utf-8", "shift_jis"):
        try:
            h = raw.decode(enc)
        except Exception:  # noqa: BLE001
            continue
        for pat in (r'"itemId"\s*:\s*"?(\d{3,})', r'item_id["\'\s:=]+(\d{3,})'):
            mm = re.search(pat, h)
            if mm:
                return mm.group(1)
    return ""


# ---------- @cosme / LIPS からの取り込み ----------
def _is_review_site(url: str) -> str:
    """URLが@cosme/LIPSなら 'cosme'/'lips' を返す。違えば ''。"""
    host = urllib.parse.urlparse(url).netloc.lower()
    if "cosme.net" in host or "cosme.com" in host:   # 新旧ドメイン両対応
        return "cosme"
    if "lips.jp" in host or "lipscosme.com" in host:
        return "lips"
    return ""


def _clean_review_title(raw: str) -> str:
    """og:title/JSON-LD名から商品名を抽出（サイト名・クチコミ等の付帯語を除去）。"""
    t = re.sub(r"\s+", " ", raw or "").strip()
    t = re.split(r"\s*[｜|]\s*", t)[0]                       # 区切り以降（サイト名）を捨てる
    t = re.sub(r"の(クチコミ|口コミ|商品情報|効果|評判|人気色|カラー|イエベ).*$", "", t)
    t = re.sub(r"【[^】]*】", "", t)
    toks = t.split()
    if len(toks) >= 2 and toks[0] == toks[1]:               # 「SUQQU SUQQU …」等の先頭ブランド重複を解消
        toks = toks[1:]
    return " ".join(toks).strip()[:60]


def _ld_image_urls(img) -> list[str]:
    """JSON-LDの image（str / ImageObject dict / それらのlist）からURL文字列だけ取り出す。"""
    def one(x):
        if isinstance(x, str):
            return x
        if isinstance(x, dict):
            return x.get("contentUrl") or x.get("url") or ""
        return ""
    items = img if isinstance(img, list) else [img]
    return [u for u in (one(x) for x in items) if u]


def _is_product_image(u: str) -> bool:
    """商品写真として使えるURLか。LIPSの共有カード(/api/og/)は除外。"""
    return bool(u) and u.startswith("http") and "/api/og/" not in u


# 口コミ本文の抽出条件（Playwrightの_PW_REVIEW_JSと同条件のPython版）。傾向要約の材料用。
_REV_OPN = re.compile(r"(思|使っ|塗っ|発色|色持ち|似合|高見え|落ち|乾燥|うるお|質感|テクスチャ|なじ|"
                      r"リピ|可愛|くすま|ヨレ|密着|ラメ|粉|ぼかし|香り|しっとり|サラサラ|お気に入り|"
                      r"買っ|つっぱ|毛穴|スクラブ|ツルツル|なめらか|ハリ|もっちり)")
_REV_END = re.compile(r"[。！!？?…♡♥]")
_REV_NG = re.compile(r"(ログイン|会員登録|アプリ|ランキング|クーポン|送料|利用規約|GooglePlay|AppStore|"
                     r"もっと見る|ピックアップ|バリエーション|肌質|ユーザー|色見本|チェック|動画|"
                     r"使い方|並び替|検索|カテゴリ|公式|通報|規約|プライバシー)")


def _extract_review_snippets(html: str, limit: int = 30) -> list:
    """HTMLから口コミ本文らしい行を抽出（意見語＋文末必須・UI/広告除外）。原文は要約の材料のみ。"""
    import html as _html
    t = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.S)
    t = re.sub(r"<style[^>]*>.*?</style>", "", t, flags=re.S)
    t = _html.unescape(re.sub(r"<[^>]+>", "\n", t))
    out, seen = [], set()
    for ln in t.split("\n"):
        ln = re.sub(r"\s+", " ", ln).strip()
        if not (25 <= len(ln) <= 400):
            continue
        if not _REV_OPN.search(ln) or not _REV_END.search(ln) or _REV_NG.search(ln):
            continue
        if ln in seen:
            continue
        seen.add(ln)
        out.append(ln)
        if len(out) >= limit:
            break
    return out


_BROWSER_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
    "Accept": ("text/html,application/xhtml+xml,application/xml;q=0.9,"
               "image/avif,image/webp,*/*;q=0.8"),
    "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document", "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Site": "same-origin",
}


def _crawl_review_http(url: str) -> dict:
    """@cosme/LIPSの商品ページを素のHTTPで取得し {name, images[], snippets[]} を抽出（速い）。

    @cosme/LIPSはbot検知が強いので、セッションでトップを先に踏んでCookieを得てから本体を取得。
    LIPSはJSチャレンジで弾かれることが多い（その場合は空dict→Playwrightへフォールバック）。
    """
    import requests
    try:
        origin = "{0.scheme}://{0.netloc}/".format(urllib.parse.urlparse(url))
        s = requests.Session()
        s.get(origin, headers=_BROWSER_HEADERS, timeout=20)          # Cookie/セッション確立
        r = s.get(url, headers={**_BROWSER_HEADERS, "Referer": origin},
                  timeout=20, allow_redirects=True)
        html = r.text if r.status_code == 200 and len(r.text) > 10_000 else ""
    except Exception:  # noqa: BLE001
        html = ""
    if not html:
        return {}
    name, brand, images, snippets = "", "", [], []
    # 1) JSON-LD(Product/Review)を最優先
    for block in re.findall(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', html, re.S):
        try:
            data = json.loads(block.strip())
        except Exception:  # noqa: BLE001
            continue
        for node in (data if isinstance(data, list) else [data]):
            if not isinstance(node, dict):
                continue
            t = node.get("@type", "")
            if t == "Product" or (isinstance(t, list) and "Product" in t):
                name = name or (node.get("name") or "")
                b = node.get("brand")
                brand = brand or (b.get("name") if isinstance(b, dict) else (b or ""))
                images += _ld_image_urls(node.get("image"))   # str / ImageObject / その混在list
            for rv in (node.get("review") or []) if isinstance(node, dict) else []:
                body = rv.get("reviewBody") or rv.get("description") or "" if isinstance(rv, dict) else ""
                if body:
                    snippets.append(body)
    # 2) OGメタで補完
    if not name:
        m = re.search(r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)', html)
        name = m.group(1) if m else ""
    name = _clean_review_title(name)
    for m in re.finditer(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)', html):
        images.append(m.group(1))
    # 3) 商品画像CDNから補完（@cosme=cache-cdn/fitter.cosme.net・LIPS=cdn.lipscosme/cloudfront）
    images += re.findall(
        r'https://[^"\']*(?:cosme\.net/media|cdn\.lipscosme\.com|cloudfront\.net/[^"\']*lips)'
        r'[^"\']*\.(?:jpg|jpeg|png|webp)', html)
    seen, uniq = set(), []
    for u in images:
        k = u.split("?")[0].rsplit("/", 1)[-1]   # 同一画像が別CDNホストで重複するのでファイル名で除去
        if _is_product_image(u) and k and k not in seen:
            seen.add(k)
            uniq.append(u)
    # @cosmeは商品ページに口コミ本文が無い→ /review/ ページをHTTP取得して傾向材料にする
    # （Playwright不要でXserverでも口コミ傾向を拾える）。LIPSはJSON-LDで取得済み。
    if not snippets and "cosme.net" in url:
        m = re.search(r"/products?/(?:product_id/)?(\d+)", url)
        if m:
            rev = "https://www.cosme.net/products/{0}/review/".format(m.group(1))
            try:
                rr = s.get(rev, headers={**_BROWSER_HEADERS, "Referer": url}, timeout=20)
                if rr.status_code == 200:
                    snippets += _extract_review_snippets(rr.text)
            except Exception:  # noqa: BLE001
                pass
    full = (brand + " " + name).strip() if brand and brand not in name else name
    return {"name": full[:60], "images": uniq[:4],
            "snippets": [re.sub(r"\s+", " ", s).strip() for s in snippets if s][:30]}


# Playwright(ヘッドレス)で取得するJS。SPA描画後のDOMから抽出する。
_PW_UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
          "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148")
# og:title→h1 の順で商品名
_PW_NAME_JS = r"""()=>{
  const og=document.querySelector('meta[property="og:title"]');
  if(og&&og.content&&og.content.trim().length>=4)return og.content.trim();
  const h=document.querySelector('h1,h2');
  return h?(h.innerText||'').replace(/\s+/g,' ').trim():'';
}"""
# 公式商品画像のみ（LIPS=product-show-image__image / @cosme=og:image+商品CDN）。クチコミ写真は除外
_PW_IMG_JS = r"""()=>{
  const out=[]; const seen=new Set();
  const push=(src)=>{ if(!src||!src.startsWith('http'))return;
    const k=src.split('?')[0].split('/').pop(); if(!k||seen.has(k))return; seen.add(k); out.push(src); };
  const og=document.querySelector('meta[property="og:image"]'); if(og)push(og.content);
  for(const im of document.querySelectorAll('img')){
    const c=im.closest('[class]'); const cls=c?(c.className+''):'';
    const src=im.currentSrc||im.src||im.getAttribute('data-src')||'';
    const w=im.naturalWidth||im.width||0;
    if(/product-show-image__image/i.test(cls)&&!/emblem/i.test(cls)){ if(!(w&&w<400))push(src); continue; }
    if(/cosme\.net\/media|cdn\.lipscosme\.com/.test(src)&&!(w&&w<300))push(src);
  }
  return out.slice(0,6);
}"""
# 口コミ本文（意見語＋文末記号必須でUI/広告/色名を除外）。原文は保持せず後段で傾向へ抽象化
_PW_REVIEW_JS = r"""()=>{
  const out=[]; const seen=new Set();
  const OPN=/(思|使っ|塗っ|発色|色持ち|似合|高見え|落ち|乾燥|うるお|質感|テクスチャ|なじ|リピ|可愛|くすま|ヨレ|密着|ラメ|粉|ぼかし|香り|しっとり|サラサラ|お気に入り|買っ)/;
  const END=/[。！!？?…♡♥]/;
  const NG=/(ログイン|会員登録|アプリ|ランキング|クーポン|送料|利用規約|GooglePlay|AppStore|もっと見る|ピックアップ|バリエーション|肌質|ユーザー|色見本|チェック|動画|HowTo|使い方を紹介|画像をもっと|公式|通報)/;
  for(const el of document.querySelectorAll('p,span,div')){
    if(el.children.length>2)continue;
    let t=(el.innerText||'').replace(/\s+/g,' ').trim();
    if(t.length<25||t.length>400)continue;
    if(NG.test(t))continue; if(!OPN.test(t)||!END.test(t))continue;
    t=t.replace(/\s*\S+さんのクチコミより引用.*$/,'').trim();
    if(t.length<20||seen.has(t))continue; seen.add(t); out.push(t);
  }
  return out.slice(0,40);
}"""


def _browser_crawl(url: str) -> dict:
    """Playwright(ヘッドレス)で商品ページをレンダリングし {name, images[], snippets[]} を取得。

    LIPSのJSチャレンジ/SPA描画を突破する手段。Playwright(＋chromium)未導入の環境では
    ImportError等で静かに {} を返し、呼び出し側はHTTP結果のまま劣化動作する。
    """
    try:
        from playwright.sync_api import sync_playwright
    except Exception:  # noqa: BLE001
        return {}
    try:
        with sync_playwright() as p:
            b = p.chromium.launch(headless=True)
            try:
                pg = b.new_context(user_agent=_PW_UA,
                                   viewport={"width": 480, "height": 900}).new_page()
                pg.goto(url, wait_until="domcontentloaded", timeout=30000)
                pg.wait_for_timeout(3000)
                for _ in range(6):                       # 遅延ロード(画像/クチコミ)を出すためスクロール
                    pg.mouse.wheel(0, 3000)
                    pg.wait_for_timeout(800)
                name = pg.evaluate(_PW_NAME_JS)
                images = pg.evaluate(_PW_IMG_JS)
                snippets = pg.evaluate(_PW_REVIEW_JS)
                # LIPSは商品ページのクチコミが薄い→最多クチコミの色別ページへ1回ホップして補強
                if "lipscosme.com/products/" in url:
                    best = pg.evaluate(r"""()=>{
                      let best=null,max=0;
                      for(const a of document.querySelectorAll('a[href*="/product_patterns/"]')){
                        const m=(a.innerText||'').match(/クチコミ数[：:]\s*([\d,]+)/);
                        const n=m?parseInt(m[1].replace(/,/g,'')):0;
                        if(n>max){max=n;best=a.getAttribute('href');}
                      } return best;
                    }""")
                    if best:
                        href = best if best.startswith("http") else \
                            "https://lipscosme.com/" + best.lstrip("/")
                        pg.goto(href, wait_until="domcontentloaded", timeout=30000)
                        pg.wait_for_timeout(2000)
                        for _ in range(4):
                            pg.mouse.wheel(0, 3000)
                            pg.wait_for_timeout(800)
                        snippets = (snippets or []) + pg.evaluate(_PW_REVIEW_JS)
            finally:
                b.close()
    except Exception:  # noqa: BLE001
        return {}
    seen, uniq = set(), []
    for u in images or []:
        k = u.split("?")[0].rsplit("/", 1)[-1]
        if _is_product_image(u) and k and k not in seen:
            seen.add(k)
            uniq.append(u)
    sseen, sn = set(), []
    for t in snippets or []:
        t = re.sub(r"\s+", " ", t or "").strip()
        if t and t not in sseen:
            sseen.add(t)
            sn.append(t)
    return {"name": _clean_review_title(name or ""), "images": uniq[:4], "snippets": sn[:30]}


def _crawl_review_site(url: str) -> dict:
    """@cosme/LIPS商品ページ → {name, images[], snippets[]}。HTTPで先に試しPlaywrightで補完。

    @cosmeは大抵HTTPで名前・公式画像が取れる（速い）。LIPSはJSチャレンジでHTTPが空になりがちなので
    Playwrightへフォールバック。口コミ本文はSPA描画依存のためPlaywrightでのみ拾える（任意）。
    """
    info = _crawl_review_http(url) or {}
    need_browser = (not info.get("name") or not info.get("images") or not info.get("snippets"))
    if need_browser:
        br = _browser_crawl(url)
        if br:
            info["name"] = info.get("name") or br.get("name", "")
            info["images"] = info.get("images") or br.get("images", [])
            if not info.get("snippets"):
                info["snippets"] = br.get("snippets", [])
    return info if info.get("name") else {}


def _name_overlap(it: dict, toks: list) -> int:
    nm = it.get("itemName", "") or ""
    return sum(1 for t in toks if t in nm)


def _rakuten_best_match(name: str, e: dict) -> dict | None:
    """商品名で楽天を検索し、名前の一致度＋人気で最良の1件を返す（収益化リンク用）。

    手動で選んだ商品なので、レビュー数/価格帯の足切り(_score)はしない（楽天にあるのに
    レビューが少なくて『該当なし』になるのを防ぐ）。NGワード品だけ除外し、名前の一致を最優先。
    検索語は広さを変えて複数回試す（先頭4語で出ない商品も3語/2語で拾う）。
    """
    if not name:
        return None
    toks = [t for t in re.split(r"[\s　/・]+", name) if len(t) >= 2]
    words = name.split()
    tries: list[str] = []
    for kw in (name, " ".join(words[:4]), " ".join(words[:3]), " ".join(words[:2])):
        kw = kw.strip()
        if kw and kw not in tries:
            tries.append(kw)
    items, seen = [], set()
    strong = max(2, len(toks) - 1)               # 「ほぼ全トークン一致」の目安
    for kw in tries:
        try:
            res = _rakuten_search(kw, e, by_keyword=True)
        except Exception:  # noqa: BLE001
            continue
        for it in res:
            c = it.get("itemCode")
            if not c or c in seen:
                continue
            if any(ng in (it.get("itemName", "") or "") for ng in _NG):
                continue                          # NGワード品のみ除外
            seen.add(c)
            items.append(it)
        if any(_name_overlap(it, toks) >= strong for it in items):
            break                                 # 十分な一致が取れたら追加検索は省く
    if not items:
        return None
    best = max(items, key=lambda it: (_name_overlap(it, toks),
                                      it.get("reviewCount") or 0,
                                      it.get("reviewAverage") or 0))
    need = 2 if len(toks) >= 3 else 1             # ブランド名だけの誤爆を防ぐ最低一致
    return best if _name_overlap(best, toks) >= need else None


# ---------- @cosme/LIPS 取得待ちキュー（日本IPのXserverで処理） ----------
def fetchqueue() -> list:
    """@cosme/LIPSの取得待ち（RenderなどIP制限環境で積み、Xserver cronがクロール）。"""
    return _load("_threads_fetchqueue")


def _enqueue_fetch(account: dict, url: str, label: str) -> None:
    q = fetchqueue()
    if any(x.get("url") == url and x.get("account") == account["id"] for x in q):
        return  # 重複
    q.append({"account": account["id"], "url": url, "label": (label or "").strip(),
              "tries": 0, "created": int(time.time())})
    _save("_threads_fetchqueue", q[-100:])


def process_fetch_queue(limit: int = 10, max_tries: int = 5) -> dict:
    """取得待ちのThreads URL(@cosme/LIPS/楽天)を処理→選定に追加（Xserver=日本IPのcron用）。

    クロール失敗/楽天該当なしは一時不調(429等)もありうるので max_tries まで温存・再試行。
    重複は恒久要因として即キューから外す。
    """
    q = fetchqueue()
    if not q:
        return {"done": 0, "failed": 0, "left": 0}
    e = _env()
    done, failed, head, keep = 0, 0, q[:limit], []
    for item in head:
        url, acc = item.get("url", ""), {"id": item["account"]}
        kind = _is_threads_url(url)
        if not kind:
            continue  # 不正URLは破棄
        if kind == "rakuten":
            ok, msg = _add_rakuten_url(acc, url, item.get("label", ""), e)
        else:
            ok, msg = _add_from_review_site(acc, url, kind, item.get("label", ""),
                                            e, allow_enqueue=False)
        if ok:
            done += 1
            continue
        failed += 1
        item["tries"] = item.get("tries", 0) + 1
        # 重複は恒久→破棄。それ以外(取得失敗/楽天該当なし)は max_tries まで再試行
        if "既に" not in msg and item["tries"] < max_tries:
            keep.append(item)
    rest = keep + q[limit:]
    _save("_threads_fetchqueue", rest)
    return {"done": done, "failed": failed, "left": len(rest)}


def _add_from_review_site(account: dict, url: str, source: str, label: str,
                          e: dict, *, allow_enqueue: bool = True) -> tuple[bool, str]:
    """@cosme/LIPSのURL → 公式画像・口コミ傾向を取り込み、商品名で楽天を自動マッチして追加。

    allow_enqueue=True かつ取得に失敗（=IP制限の可能性）なら、取得待ちキューに積んで
    日本IPのXserver cronに委譲する（Web UIから貼っても後で選定に並ぶ）。
    """
    site = "@cosme" if source == "cosme" else "LIPS"
    info = _crawl_review_site(url)
    if not info or not info.get("name"):
        if allow_enqueue:
            _enqueue_fetch(account, url, label)
            return True, f"{site}を取得待ちに追加しました（日本IPのサーバーが数分以内に取得→選定に並びます）"
        return False, f"{site}ページを取得できませんでした（アクセス制限/一時不調の可能性）"
    it = _rakuten_best_match(info["name"], e)
    if not it:
        # 楽天マッチ不可。Render等は楽天APIキー未設定/IPで失敗しがちなので、
        # キー＋日本IPを持つXserverに再マッチを委譲（取得待ちへ）。
        if allow_enqueue:
            _enqueue_fetch(account, url, label)
            return True, (f"「{info['name'][:20]}」を取得待ちに追加しました"
                          "（日本IPのサーバーが数分以内に楽天マッチ→選定に並びます）")
        return False, f"「{info['name'][:24]}」に一致する楽天商品が見つかりませんでした"
    gist = ""
    if info.get("snippets") and e.get("GEMINI_API_KEY"):
        gist = gist_text(summarize_reviews(info["name"], info["snippets"], e))
    if not _add_product(account, it, label, source, review_gist=gist,
                        cosme_images=info.get("images"), source_url=url):
        return False, "この商品は既にリスト/投稿にあります"
    note = "（口コミ傾向も取込）" if gist else ""
    return True, f"{site}→楽天マッチ: {it.get('itemName', '')[:24]}{note}"


def _add_rakuten_url(account: dict, url: str, label: str, e: dict) -> tuple[bool, str]:
    """楽天の商品URL → 商品ID取得 → APIで取得 → 選定に追加。"""
    m = re.search(r"item\.rakuten\.co\.jp/([^/?#]+)/", url)
    if not m:
        return False, "楽天 / @cosme / LIPS の商品URLを貼ってください"
    shop = m.group(1)
    item_id = _page_item_id(url)
    if not item_id:
        return False, "商品ページから商品IDを取得できませんでした（URLを確認）"
    it = _rakuten_by_code(f"{shop}:{item_id}", e)
    if not it:
        return False, "商品が取得できませんでした（在庫切れ/販売終了の可能性）"
    if not _add_product(account, it, label, "manual", source_url=url):
        return False, "この商品は既にリスト/投稿にあります"
    return True, f"商品選定に追加: {it.get('itemName','')[:24]}"


def _is_threads_url(url: str) -> str:
    """ThreadsのURL種別を返す: 'cosme'/'lips'/'rakuten'/''。"""
    src = _is_review_site(url)
    if src:
        return src
    if re.search(r"item\.rakuten\.co\.jp/[^/?#]+/", url):
        return "rakuten"
    return ""


def enqueue_threads_url(account: dict, url: str, label: str = "") -> bool:
    """ThreadsのURL(楽天/@cosme/LIPS)を取得待ちに積む（LINE等・Xserverが処理）。種別不一致はFalse。"""
    if not _is_threads_url(url):
        return False
    _enqueue_fetch(account, url, label)
    return True


def add_manual_url(account: dict, url: str, label: str = "") -> tuple[bool, str]:
    """商品URL(楽天 / @cosme / LIPS)を貼ると記事化候補に追加。label指定でその文脈を文章に。

    @cosme/LIPSは公式画像・口コミ傾向を取り込み、収益化リンクは商品名で楽天を自動マッチ。
    """
    e = _env()
    src = _is_review_site(url)
    if src:
        return _add_from_review_site(account, url, src, label, e)
    return _add_rakuten_url(account, url, label, e)


def generate_drafts(account: dict, count: int) -> int:
    e = _env()
    if not (e["RAKUTEN_APP_ID"] and e["GEMINI_API_KEY"]):
        raise RuntimeError("RAKUTEN/GEMINI のキーが未設定です。")
    keywords = account.get("keywords") or []
    genres = account.get("genres") or []
    if not keywords and not genres:
        keywords = _BEAUTY_KEYWORDS  # 既定=美容キーワード
    existing = drafts() + queue()
    seen_codes = {d.get("id", "").split("::")[-1] for d in existing}

    items: list[dict] = []
    for kw in keywords:
        try:
            items += [it for it in _rakuten_search(kw, e, by_keyword=True) if _score(it) > 0]
        except Exception:  # noqa: BLE001
            continue
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
        d = _pr_draft_from_item(account, it, e)
        if not d:
            continue
        cur.append(d)
        made += 1
    if made:
        _save("_threads_drafts", cur[-200:])
    return made


# ---------- スケジュール ----------
def _next_slot(account_id: str, q: list, hours: list[int]) -> int:
    taken = {x["scheduled_at"] for x in q if x.get("account") == account_id}
    now = datetime.now(_JST)                       # 日本時間で枠を作る（サーバーTZ非依存）
    for day in range(0, 60):
        d = (now + timedelta(days=day)).date()
        for h in sorted(hours or [9, 13, 20]):
            slot = datetime(d.year, d.month, d.day, h, random.randint(0, 25), tzinfo=_JST)
            ts = int(slot.timestamp())
            if slot > now and ts not in taken:
                return ts
    return int((now + timedelta(hours=1)).timestamp())


def reschedule_overdue() -> int:
    """期限切れの公開待ちを、各媒体の次の空き枠（未来）に振り直す。

    下書きストックモードで公開されないまま放置されると予定時刻が過去になるため、
    一覧表示時に未来へ繰り上げて「経過済み」表示を防ぐ。
    """
    q = queue()
    now = int(time.time())
    overdue = [x for x in q if x.get("status") == "pending" and x.get("scheduled_at", 0) <= now]
    if not overdue:
        return 0
    hours = ((get_rules().get("threads", {}) or {}).get("schedule", {}) or {}).get("hours", [9, 13, 20])
    for item in sorted(overdue, key=lambda x: x.get("scheduled_at", 0)):
        item["scheduled_at"] = _next_slot(item.get("account", ""), q, hours)  # qは参照で更新される
    _save("_threads_queue", q)
    return len(overdue)


# ---------- 承認 / 却下 ----------
def approve(draft_id: str, images: list[str], caption: str, reply_text: str = "",
            *, when: int | None = None) -> bool:
    ds = drafts()
    d = next((x for x in ds if x["id"] == draft_id), None)
    if not d:
        return False
    is_musing = d.get("type") == "musing"
    imgs = [u for u in (images or []) if u][:20]  # Threadsカルーセル上限20枚まで
    rules = (get_rules().get("threads", {}) or {})
    hours = (rules.get("schedule", {}) or {}).get("hours", [9, 13, 20])
    q = queue()
    ts = when or _next_slot(d["account"], q, hours)
    q.append({"id": draft_id, "account": d["account"], "type": d.get("type", "pr"),
              "caption": caption.strip(),
              "images": [] if is_musing else imgs,
              "reply": (reply_text or d.get("reply", "")).strip(),
              "image": "" if is_musing else (imgs[0] if imgs else ""),  # 後方互換
              "link": "" if is_musing else d.get("link", ""),
              "source_url": "" if is_musing else d.get("source_url", ""),
              "product": d.get("product", ""),
              "_draft": d,   # 取り下げ時に承認待ちドラフトへ完全復元するための元データ
              "scheduled_at": ts, "status": "pending", "created": int(time.time())})
    _save("_threads_queue", q)
    _save("_threads_drafts", [x for x in ds if x["id"] != draft_id])
    return True


def withdraw(item_id: str) -> bool:
    """公開キューの未公開(pending)を取り下げ→承認待ちドラフトに戻す（再編集可）。即削除ではない。"""
    q = queue()
    item = next((x for x in q if x.get("id") == item_id and x.get("status") == "pending"), None)
    if not item:
        return False
    base = item.get("_draft")
    if base:                                   # 元ドラフトを復元しつつ承認時の編集を引き継ぐ
        d = dict(base)
        d["caption"] = item.get("caption", d.get("caption", ""))
        if item.get("type") != "musing":
            d["reply"] = item.get("reply", d.get("reply", ""))
    else:                                      # 旧データ(_draft無し)はキュー項目から再構成
        d = {"id": item["id"], "account": item.get("account", ""),
             "type": item.get("type", "pr"), "product": item.get("product", ""),
             "caption": item.get("caption", ""),
             "caption_options": [item.get("caption", "")],
             "reply": item.get("reply", ""),
             "images": item.get("images") or ([item["image"]] if item.get("image") else []),
             "link": item.get("link", ""), "source_url": item.get("source_url", ""),
             "created": int(time.time())}
    ds = drafts()
    if not any(x.get("id") == d["id"] for x in ds):
        ds.append(d)
    _save("_threads_drafts", ds[-200:])
    _save("_threads_queue", [x for x in q if x.get("id") != item_id])
    return True


def reject(draft_id: str) -> bool:
    ds = drafts()
    _save("_threads_drafts", [x for x in ds if x["id"] != draft_id])
    return True


# ---------- 公開（スケジューラ） ----------
def _publish_item(item: dict, uid_cache: dict | None = None) -> dict:
    """キュー項目1件を実際にThreadsへ公開（媒体別トークン）。itemを更新し結果dictを返す。"""
    uid_cache = uid_cache if uid_cache is not None else {}
    acc = get_account(item.get("account", ""))
    tok = account_token(acc)
    now = int(time.time())
    try:
        if not (tok or threads_client.enabled()):
            raise RuntimeError("このアカウントの公開トークンが未設定です")
        acc_id = acc.get("id", "")
        if acc_id not in uid_cache:
            uid_cache[acc_id] = threads_client.me(tok).get("id", "me")
        uid = uid_cache[acc_id]
        if item.get("type") == "musing":
            main = threads_client.publish_text(item["caption"], user_id=uid, token=tok)
            res = {"main": main}
            rep_text = (item.get("reply", "") or "").strip()
            if rep_text:  # 返信詳細文があれば親投稿へのリプライとして投稿（スレッド化）
                link = (item.get("link", "") or "").strip()
                body = (rep_text + ("\n" + link if link else "")).strip()
                res["reply"] = threads_client.reply(main.get("id"), body, user_id=uid, token=tok)
        else:
            # メイン(1投稿目)にはPR表記を入れず、2投稿目(リプライ)に小文字prを入れる
            caption = re.sub(r"\s*#PR\b", "", item["caption"]).rstrip()
            imgs = item.get("images") or ([item["image"]] if item.get("image") else [])
            imgs = _hosted_trimmed(imgs)   # 公開直前に白ふちトリム＋ホスティング
            reply_text = (item.get("reply", "") or "").strip()
            if not re.search(r"(?<![A-Za-z])pr(?![A-Za-z])", reply_text, re.I):
                reply_text = (reply_text + "\npr").strip()   # 2投稿目に小文字prを担保
            res = threads_client.post_set(caption, imgs, reply_text,
                                          item.get("link", ""), user_id=uid, token=tok)
        item["status"] = "published"
        item["permalink"] = (res.get("main") or {}).get("permalink")
        item["published_at"] = now
        return {"id": item["id"], "ok": True, "permalink": item["permalink"]}
    except Exception as ex:  # noqa: BLE001
        item["status"] = "error"
        item["error"] = str(ex)[:200]
        return {"id": item["id"], "ok": False, "error": str(ex)[:200]}


def publish_now(item_id: str) -> dict:
    """指定の公開待ち1件を今すぐ手動公開（スケジュール/公開モード問わず・本人操作）。"""
    q = queue()
    item = next((x for x in q if x.get("id") == item_id and x.get("status") == "pending"), None)
    if not item:
        return {"ok": False, "error": "対象が見つかりません（既に公開済み/取り下げ済みの可能性）"}
    r = _publish_item(item)
    _save("_threads_queue", q[-300:])
    return r


def publish_due(*, limit: int = 1) -> list[dict]:
    """scheduled_at<=now の pending を公開（画像メイン＋リンクをリプライ）。

    アカウント別: そのアカウントの公開モードが live のものだけ、アカウント別トークンで公開。
    """
    q = queue()
    now = int(time.time())
    due = [x for x in q if x.get("status") == "pending" and x.get("scheduled_at", 0) <= now]
    due.sort(key=lambda x: x.get("scheduled_at", 0))
    results = []
    uids: dict = {}                 # account_id -> user_id キャッシュ
    published = 0
    for item in due:
        if published >= limit:
            break
        if account_publish_mode(get_account(item.get("account", ""))) != "live":
            continue                # 下書きストックモードのアカウントはスキップ（溜めるだけ）
        r = _publish_item(item, uids)
        results.append(r)
        if r.get("ok"):
            published += 1
    _save("_threads_queue", q[-300:])
    return results
