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


def queue() -> list:
    return _load("_threads_queue")


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


def _make_captions(persona: str, item: dict, e: dict, n: int = 5) -> dict:
    """1商品につき n 案（異なるフック型）のキャプションを生成。返り {clean_name, captions[], reply}。"""
    styles = random.sample(_PR_HOOKS, min(n, len(_PR_HOOKS)))
    style_lines = "\n".join(f"  案{i+1}「{nm}」型: {ex}" for i, (nm, ex) in enumerate(styles))
    prompt = f"""あなたはThreadsでバズってる日本語の美容系インフルエンサーの“中の人”です。
人格・口調: {persona or "美容好きの等身大。正直レビュー、絵文字多め(✨🥹🥰)、盛れる/血色感などの美容語彙"}

# 商品
- 商品名(楽天生データ): {item.get('itemName','')}
- 価格: {item.get('itemPrice')}円 / レビュー: ★{item.get('reviewAverage')}（{item.get('reviewCount')}件）

# タスク: メイン投稿(1投稿目)の本文を {n}案、それぞれ別のフック型で作る。
{style_lines}

# 鉄則（美容系9アカ分析より）
- 各案、指定の型で書き出しを変える。**短く(50〜90字・最大2行)**、説明しすぎない。
- 値段で始めない(値段型のときだけ可)。スペック羅列禁止。
- **余韻で次を読ませる**: 文末を「…」「、、」「んだけど,」で途切れさせ続きをリプライへ。
- 美容語彙(盛れる/血色感/透明感/濡れツヤ/多幸感)＋絵文字。{_YAKKIHO}

# 出力(JSONのみ・コードフェンス禁止)
{{
  "clean_name": "簡潔な商品名(20字以内・宣伝文句除く)",
  "captions": ["案1の本文","案2の本文","案3の本文","案4の本文","案5の本文"],
  "reply": "リプライ(2投稿目)の軽い一言(15〜35字・絵文字可・URL無し)。例『これです🛒』『気になる人だけどうぞ👇』"
}}
"""
    out = _gemini_json(prompt, e)
    caps = [c.strip() for c in (out.get("captions") or []) if c and c.strip()]
    out["captions"] = caps[:n] or [out.get("caption", "")]
    return out


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


def _make_musing(account: dict, e: dict) -> dict:
    persona = account.get("persona", "")
    niche = account.get("name", "暮らし")
    name, ex = random.choice(_MUSING_TYPES)
    opener = random.choice(["結局/つまり以外で", "問いかけで", "情景描写で", "感情の一言で",
                            "『え、』『うそ、』等の驚きで", "ぼやき/ひとりごとで"])
    prompt = f"""あなたはThreadsでバズってる日本語の暮らし系の“中の人”です。
人格・口調: {persona or "親しみやすく絵文字。正直で等身大"}
世界観: {niche}

# タスク: 商品宣伝ではない「日常の共感つぶやき」を1つ。
- ネタの型: 「{name}」… {ex}
- **書き出しは毎回変える**。今回は「{opener}」始める。「結局、」で始めるのは禁止。
- 超口語＋感情＋等身大。短く(60字前後・最大80字)。思わず「わかる」と言いたくなる一言。
- 商品名・リンク・宣伝・#PR・ハッシュタグは入れない。
- 良い温度感: 「いつまでYouTube見とるんじゃあぁぁ！！」「努力でここまで変われるの尊い」

# 出力(JSONのみ)
{{"caption": "つぶやき本文"}}
"""
    return _gemini_json(prompt, e)


def generate_musings(account: dict, count: int) -> int:
    """日常つぶやきドラフトを生成（画像・リンク無し）。"""
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


# ---------- ドラフト生成 ----------
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
        real = api_images(it)
        if not real:
            continue
        gal = gallery_images(it)             # 商品ページのギャラリー
        # 候補＝API実画像＋ギャラリー（重複除去）→ AI不使用で文字バナー除外＋きれい順
        dedup, seen_u = [], set()
        for u in real + gal:
            if u and u.split("?")[0] not in seen_u:
                seen_u.add(u.split("?")[0])
                dedup.append(u)
        try:
            imgs = image_pick.rank_urls(dedup, limit=10)
        except Exception:  # noqa: BLE001
            imgs = dedup[:10]
        if not imgs:
            continue
        try:
            cap = _make_captions(account.get("persona", ""), it, e, n=5)
        except Exception:  # noqa: BLE001
            continue
        opts = cap.get("captions") or []
        if not opts:
            continue
        cur.append({
            "id": f"{account['id']}::{code}",
            "account": account["id"], "type": "pr",
            "product": cap.get("clean_name") or it.get("itemName", "")[:30],
            "price": it.get("itemPrice"),
            "review": {"avg": it.get("reviewAverage"), "count": it.get("reviewCount")},
            "link": it.get("affiliateUrl") or it.get("itemUrl", ""),
            "caption": opts[0].strip(),
            "caption_options": [o.strip() for o in opts],
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
