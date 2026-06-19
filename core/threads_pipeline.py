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
                 review_gist: str = "", cosme_images: list | None = None) -> bool:
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
                             label=p.get("label", ""), review_gist=p.get("review_gist", ""))
    q = genqueue()
    q.append({
        "id": p["id"], "account": account["id"], "type": "pr",
        "product": (it.get("itemName") or p.get("name", ""))[:40],
        "label": p.get("label", ""), "review_gist": p.get("review_gist", ""),
        "cosme_images": p.get("cosme_images", []), "source": p.get("source", ""),
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
                            review_gist=p.get("review_gist", ""), extra_images=extra)
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
                                    extra_images=extra, caps=caps)
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
            "メイクの仕上がり表現(盛れる/血色感/ツヤ/透明感)は可。スキンケアは『うるおう/個人の感想』の体に留める。")


_DEFAULT_PR_PROMPT = """あなたはThreadsでバズってる日本語の美容系インフルエンサーの“中の人”です。
人格・口調: [[persona]]

# 商品
- 商品名(楽天生データ): [[item_name]]
- 価格: [[price]]円 / レビュー: ★[[review_avg]]（[[review_count]]件）

# タスク: メイン投稿(1投稿目)の本文を [[n]]案、それぞれ別のフック型で作る。
[[styles]]

# 鉄則（美容系9アカ分析より）
- 各案、指定の型で書き出しを変える。**短く(50〜90字・最大2行)**、説明しすぎない。
- 値段で始めない(値段型のときだけ可)。スペック羅列禁止。
- **余韻で次を読ませる**: 文末を「…」「、、」「んだけど,」で途切れさせ続きをリプライへ。
- 美容語彙(盛れる/血色感/透明感/濡れツヤ/多幸感)＋絵文字。[[yakkiho]]

# 出力(JSONのみ・コードフェンス禁止)
{"clean_name":"簡潔な商品名(20字以内・宣伝文句除く)","captions":["案1の本文","案2の本文","案3の本文","案4の本文","案5の本文"],"reply":"リプライ(2投稿目)の軽い一言(15〜35字・絵文字可・URL無し)。例『これです🛒』『気になる人だけどうぞ👇』"}
"""


def pr_prompt_template() -> str:
    return ((get_rules().get("threads", {}) or {}).get("pr_prompt") or "").strip() or _DEFAULT_PR_PROMPT


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


def build_pr_prompt(persona: str, item: dict, n: int = 5, *,
                    tmpl: str = "", label: str = "", review_gist: str = "") -> str:
    """PR投稿の最終プロンプト文字列を組み立てる（Gemini/Claude共通）。"""
    styles = random.sample(_PR_HOOKS, min(n, len(_PR_HOOKS)))
    style_lines = "\n".join(f"  案{i+1}「{nm}」型: {ex}" for i, (nm, ex) in enumerate(styles))
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
                   tmpl: str = "", label: str = "", review_gist: str = "") -> dict:
    """1商品につき n 案（異なるフック型）のキャプションをGeminiで生成。返り {clean_name, captions[], reply}。"""
    prompt = build_pr_prompt(persona, item, n, tmpl=tmpl, label=label, review_gist=review_gist)
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


_DEFAULT_MUSING_PROMPT = """あなたはThreadsでバズってる日本語の美容/暮らし系の“中の人”です。
人格・口調: [[persona]]
世界観: [[niche]]

# タスク: 商品宣伝ではない「日常の共感つぶやき」を1つ。
- ネタの型: 「[[type_name]]」… [[type_ex]]
- **書き出しは毎回変える**。今回は「[[opener]]」始める。「結局、」で始めるのは禁止。
- 超口語＋感情＋等身大。短く(60字前後・最大80字)。思わず「わかる」と言いたくなる一言。
- 商品名・リンク・宣伝・#PR・ハッシュタグは入れない。
- 良い温度感: 「いつまでYouTube見とるんじゃあぁぁ！！」「努力でここまで変われるの尊い」

# 出力(JSONのみ)
{"caption": "つぶやき本文"}
"""


def musing_prompt_template() -> str:
    return ((get_rules().get("threads", {}) or {}).get("musing_prompt") or "").strip() or _DEFAULT_MUSING_PROMPT


def build_musing_prompt(account: dict, *, tmpl: str = "") -> str:
    """つぶやきの最終プロンプト文字列を組み立てる（毎回ネタ型/書き出しをランダム）。"""
    name, ex = random.choice(_MUSING_TYPES)
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
    item = {"itemName": "ロムアンド ジューシーラスティングティント 06 フィグフィグ 韓国コスメ",
            "itemPrice": 1100, "reviewAverage": 4.6, "reviewCount": 12000}
    demo_gist = ("発色が良く色持ちも高評価。みずみずしい質感で唇が荒れにくいという声が多い。\n"
                 "◎良い点: 色持ちが良い / みずみずしいツヤ感 / 落ちにくい\n△気になる点: 乾燥を感じる人も")
    result = {"model": e["GEMINI_MODEL"], "product": item["itemName"][:24],
              "captions": [], "reply": "", "musing": "", "error": ""}
    try:
        cap = _make_captions(account.get("persona", ""), item, e, n=5, tmpl=pr_tmpl,
                             review_gist=demo_gist)
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


# ---------- ドラフト生成 ----------
def _pr_draft_from_item(account: dict, it: dict, e: dict, *, label: str = "",
                        review_gist: str = "", extra_images: list | None = None,
                        caps: dict | None = None) -> dict | None:
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
                                 review_gist=review_gist)
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


def add_manual_url(account: dict, url: str, label: str = "") -> tuple[bool, str]:
    """楽天の商品URLを貼ると、AIで記事化してPRドラフトに追加。label指定でその文脈を文章に。"""
    e = _env()
    m = re.search(r"item\.rakuten\.co\.jp/([^/?#]+)/", url)
    if not m:
        return False, "楽天の商品URL(item.rakuten.co.jp/店舗/...)を貼ってください"
    shop = m.group(1)
    item_id = _page_item_id(url)
    if not item_id:
        return False, "商品ページから商品IDを取得できませんでした（URLを確認）"
    code = f"{shop}:{item_id}"
    it = _rakuten_by_code(code, e)
    if not it:
        return False, "商品が取得できませんでした（在庫切れ/販売終了の可能性）"
    if not _add_product(account, it, label, "manual"):
        return False, "この商品は既にリスト/投稿にあります"
    return True, f"商品選定に追加: {it.get('itemName','')[:24]}"


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
    is_musing = d.get("type") == "musing"
    imgs = [u for u in (images or []) if u][:20]  # Threadsカルーセル上限20枚まで
    rules = (get_rules().get("threads", {}) or {})
    hours = (rules.get("schedule", {}) or {}).get("hours", [9, 13, 20])
    q = queue()
    ts = when or _next_slot(d["account"], q, hours)
    q.append({"id": draft_id, "account": d["account"], "type": d.get("type", "pr"),
              "caption": caption.strip(),
              "images": [] if is_musing else imgs,
              "reply": "" if is_musing else (reply_text or d.get("reply", "")).strip(),
              "image": "" if is_musing else (imgs[0] if imgs else ""),  # 後方互換
              "link": "" if is_musing else d.get("link", ""),
              "product": d.get("product", ""),
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
            if item.get("type") == "musing":
                # つぶやき＝テキストのみ（#PR・リンク無し）
                res = {"main": threads_client.publish_text(item["caption"], user_id=uid)}
            else:
                caption = item["caption"]
                if "#PR" not in caption:
                    caption += "\n\n#PR"
                imgs = item.get("images") or ([item["image"]] if item.get("image") else [])
                imgs = _hosted_trimmed(imgs)   # 公開直前に白ふちトリム＋ホスティング
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
