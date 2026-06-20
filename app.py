"""
簡易Web UI（FastAPI）。
フォームから商品URL/手動情報を入力 → 記事生成 → プレビュー → WordPress下書き。

起動: uvicorn app:app --reload  （または python app.py）
"""
from __future__ import annotations

import json
import re

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from core import (candidates, gemini_client, internal_links, overrides, pipeline,
                  prompts, ranking_catalog, rakuten_catalog, review, reviser,
                  sheet_log, threads_pipeline, wordpress)
from core.config import ROOT, get_rules, get_settings

app = FastAPI(title="アフィリエイト記事 自動化ツール")
app.mount("/static", StaticFiles(directory=str(ROOT / "web" / "static")), name="static")
templates = Jinja2Templates(directory=str(ROOT / "web" / "templates"))

_COOKIE = "review_auth"


def _authed(request: Request) -> bool:
    return review.valid_token(request.cookies.get(_COOKIE, ""))


def _set_auth_cookie(resp: RedirectResponse, request: Request) -> None:
    resp.set_cookie(_COOKIE, review.make_token(), max_age=7 * 86400, httponly=True,
                    samesite="lax", secure=(request.url.scheme == "https"))


def _ago(ts: int) -> str:
    """epoch秒 → 「3分前」等の相対表記。"""
    import time
    if not ts:
        return ""
    d = int(time.time()) - int(ts)
    if d < 60:
        return f"{d}秒前"
    if d < 3600:
        return f"{d // 60}分前"
    if d < 86400:
        return f"{d // 3600}時間前"
    return f"{d // 86400}日前"


def _mark_manual(asins: list[str]) -> None:
    """手動選定したASINを記録（生成時に足切りをバイパスするため・Issue対応）。"""
    asins = [a for a in asins if a]
    if not asins:
        return
    try:
        cur = overrides.load(force=True).get("_manual_asins", []) or []
        merged = list(dict.fromkeys([*cur, *asins]))[-300:]  # 直近300件を保持
        overrides.update({"_manual_asins": merged})
    except Exception:  # noqa: BLE001
        pass


def _public_base(request: Request) -> str:
    """ブックマークレットに焼き込む公開URL（Render等はhttpsに正規化）。"""
    b = str(request.base_url).rstrip("/")
    if b.startswith("http://") and "localhost" not in b and "127.0.0.1" not in b:
        b = "https://" + b[len("http://"):]
    return b


def _bookmarklet(base: str) -> str:
    """Amazon商品ページでASIN/商品名/価格/画像/ブランドを抽出し /select/add を開くJS。"""
    js = (
        "javascript:(function(){"
        "var h=location.href;var m=h.match(/\\/(?:dp|gp\\/product)\\/([A-Z0-9]{10})/);"
        "var a=m?m[1]:'';if(!a){var e=document.querySelector('[data-asin]');if(e)a=e.getAttribute('data-asin');}"
        "if(!a){alert('ASINが見つかりません。Amazon商品ページで実行してください');return;}"
        "var t=(document.getElementById('productTitle')||{}).textContent||document.title;"
        "var p='';var pe=document.querySelector('.a-price .a-price-whole');if(pe)p=pe.textContent.replace(/[^0-9]/g,'');"
        "var im=document.getElementById('landingImage')||document.querySelector('#imgTagWrapperId img');"
        "var ig=im?(im.getAttribute('data-old-hires')||im.src||''):'';"
        "var be=document.getElementById('bylineInfo');"
        "var br=be?be.textContent.replace(/ブランド:|のストアを表示|Visit the| Store/g,'').trim():'';"
        "var u='" + base + "/select/add?asin='+a+'&title='+encodeURIComponent((t||'').trim().slice(0,200))"
        "+'&price='+p+'&image='+encodeURIComponent(ig)+'&brand='+encodeURIComponent(br.slice(0,60));"
        "window.open(u,'_blank');})();"
    )
    return js


_stats_cache: dict = {"ts": 0.0, "data": {}}


def _compute_stats() -> dict:
    """作業指標（選定待ち/選定済み/承認待ち/本日公開）を集計。各項目は失敗時None。"""
    import time
    from datetime import datetime, timedelta, timezone
    s: dict = {}
    try:
        s["pending"] = len(candidates.list_by_status("pending", limit=300))
    except Exception:  # noqa: BLE001
        s["pending"] = None
    try:
        s["approved"] = len(candidates.list_by_status("approved", limit=300))
    except Exception:  # noqa: BLE001
        s["approved"] = None
    try:
        s["drafts"] = len(wordpress.list_posts(statuses="draft", fields="id"))
    except Exception:  # noqa: BLE001
        s["drafts"] = None
    try:  # 手動追加の未反映（Xserver補完待ち）件数
        s["queue"] = len(overrides.load().get("_manual_pending", []) or [])
    except Exception:  # noqa: BLE001
        s["queue"] = None
    try:
        jst = timezone(timedelta(hours=9))
        midnight = datetime.now(jst).replace(hour=0, minute=0, second=0, microsecond=0)
        s["published_today"] = len(
            wordpress.list_published_since(midnight.replace(tzinfo=None).isoformat()))
    except Exception:  # noqa: BLE001
        s["published_today"] = None
    _stats_cache["ts"] = time.time()
    _stats_cache["data"] = s
    return s


def _crawl_status() -> dict:
    """クロール状況（Xserverが書いた _crawl_status）＋相対時刻を返す。"""
    st = {}
    try:
        if overrides.enabled():
            st = dict(overrides.load().get("_crawl_status") or {})  # 60秒キャッシュ利用
    except Exception:  # noqa: BLE001
        st = {}
    st["started_ago"] = _ago(st.get("started_at", 0))
    st["finished_ago"] = _ago(st.get("finished_at", 0))
    return st


@app.get("/stats")
def stats(request: Request):
    """全タブ共通ヘッダーの作業指標（45秒キャッシュ・非同期取得用）。"""
    if not _authed(request):
        return JSONResponse({"ok": False, "error": "auth"}, status_code=401)
    import time
    if time.time() - _stats_cache["ts"] < 45 and _stats_cache["data"]:
        return JSONResponse({"ok": True, "stats": _stats_cache["data"]})
    return JSONResponse({"ok": True, "stats": _compute_stats()})


@app.get("/health")
def health():
    """死活監視・Renderコールドスタート防止用の軽量エンドポイント（認証/外部API無し）。

    commit は稼働中のデプロイ確認用（Renderは RENDER_GIT_COMMIT を自動設定）。
    """
    import os
    commit = (os.environ.get("RENDER_GIT_COMMIT")
              or os.environ.get("GIT_COMMIT") or "")[:7]
    return JSONResponse({"ok": True, "service": "affiliate-automation", "commit": commit})


def _emoji_svg(emoji: str, bg: str) -> Response:
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
           f'<rect width="64" height="64" rx="14" fill="{bg}"/>'
           f'<text x="32" y="44" font-size="38" text-anchor="middle">{emoji}</text></svg>')
    return Response(content=svg, media_type="image/svg+xml",
                    headers={"Cache-Control": "public, max-age=86400"})


def _icon_response(name: str, emoji: str, bg: str) -> Response:
    """web/static/icons/<name>.(png|svg|ico) があれば配信、無ければ絵文字SVG。"""
    import mimetypes
    base = ROOT / "web" / "static" / "icons"
    for ext in ("svg", "png", "ico"):
        f = base / f"{name}.{ext}"
        if f.exists():
            ctype = mimetypes.types_map.get("." + ext, "image/png")
            return Response(content=f.read_bytes(), media_type=ctype,
                            headers={"Cache-Control": "public, max-age=86400"})
    return _emoji_svg(emoji, bg)


@app.get("/favicon.ico")
def favicon():
    """おうちベース全体のファビコン（icons/favicon.* 優先・無ければ🏠）。"""
    return _icon_response("favicon", "🏠", "#ff9f1c")


@app.get("/favicon-threads")
def favicon_threads():
    """Threads画面用ファビコン（icons/threads.* 優先・無ければ🧵）。"""
    return _icon_response("threads", "🧵", "#7a6fb0")


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    """ホーム＝媒体ハブ（おうちベース / Threads を選択）。軽量＝WP実通信はしない。"""
    if review.enabled() and not _authed(request):
        return RedirectResponse("/review/login", status_code=303)
    s = get_settings()
    return templates.TemplateResponse("home.html", {
        "request": request, "gemini_ready": s.gemini_ready,
        "wp_ok": s.wordpress_ready, "wp_status": "設定済み" if s.wordpress_ready else "未設定"})


@app.get("/blog/single", response_class=HTMLResponse)
def blog_single(request: Request):
    """旧・単品記事生成フォーム（補助ツール）。"""
    if review.enabled() and not _authed(request):
        return RedirectResponse("/review/login", status_code=303)
    s = get_settings()
    wp_ok, wp_msg = (False, "未設定")
    if s.wordpress_ready:
        wp_ok, wp_msg = wordpress.test_connection()
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "gemini_ready": s.gemini_ready,
            "wp_ready": s.wordpress_ready,
            "wp_status": wp_msg,
            "wp_ok": wp_ok,
            "result": None,
        },
    )


@app.post("/generate", response_class=HTMLResponse)
def generate(
    request: Request,
    url: str = Form(""),
    brand: str = Form(""),
    category: str = Form(""),
    model_number: str = Form(""),
    product_name: str = Form(""),
    price: str = Form(""),
    company_hint: str = Form(""),
    specs: str = Form(""),
    affiliate_link_html: str = Form(""),
    post_to_wp: str = Form(""),
    wp_status: str = Form("draft"),
    skip_selection_gate: str = Form(""),
):
    manual = {
        "brand": brand.strip(),
        "category": category.strip(),
        "model_number": model_number.strip(),
        "product_name": product_name.strip(),
        "company_hint": company_hint.strip(),
        "price": int(price) if price.strip().isdigit() else None,
        "specs": [s.strip() for s in specs.splitlines() if s.strip()],
    }
    result = pipeline.run(
        url=url.strip(),
        manual=manual,
        affiliate_link_html=affiliate_link_html,
        post_to_wp=bool(post_to_wp),
        wp_status=wp_status or "draft",
        skip_selection_gate=bool(skip_selection_gate),
    )
    s = get_settings()
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "gemini_ready": s.gemini_ready,
            "wp_ready": s.wordpress_ready,
            "wp_status": "",
            "wp_ok": s.wordpress_ready,
            "result": result,
            "form": {
                "url": url, "brand": brand, "category": category,
                "model_number": model_number, "product_name": product_name,
                "price": price, "company_hint": company_hint, "specs": specs,
                "affiliate_link_html": affiliate_link_html,
            },
        },
    )


# ============================================================
# 承認Webアプリ（Issue #12）: スマホ/PCで下書きを確認→公開/却下
# ============================================================
@app.get("/review", response_class=HTMLResponse)
def review_list(request: Request, status: str = "draft", msg: str = ""):
    if not review.enabled():
        return templates.TemplateResponse(
            "review.html", {"request": request, "disabled": True, "items": [], "msg": "",
                            "status": "draft"})
    if not _authed(request):
        return RedirectResponse("/review/login", status_code=303)
    if status not in review.STATUS_LABELS:
        status = "draft"
    try:
        items = review.list_review_items(status)
        err = ""
    except Exception as e:  # noqa: BLE001
        items, err = [], f"記事の取得に失敗: {e}"
    return templates.TemplateResponse(
        "review.html",
        {"request": request, "disabled": False, "items": items, "msg": msg,
         "error": err, "status": status},
    )


@app.get("/review/login", response_class=HTMLResponse)
def review_login_form(request: Request, error: str = ""):
    if not review.enabled():
        return RedirectResponse("/review", status_code=303)
    return templates.TemplateResponse(
        "review_login.html", {"request": request, "error": error})


@app.post("/review/login")
def review_login(request: Request, password: str = Form("")):
    if review.check_password(password):
        resp = RedirectResponse("/review", status_code=303)
        _set_auth_cookie(resp, request)
        return resp
    return RedirectResponse("/review/login?error=1", status_code=303)


@app.get("/review/logout")
def review_logout():
    resp = RedirectResponse("/review/login", status_code=303)
    resp.delete_cookie(_COOKIE)
    return resp


@app.get("/review/{post_id}", response_class=HTMLResponse)
def review_preview(request: Request, post_id: int):
    if not _authed(request):
        return RedirectResponse("/review/login", status_code=303)
    try:
        data = review.get_preview(post_id)
    except Exception as e:  # noqa: BLE001
        return RedirectResponse(f"/review?msg=取得失敗: {e}", status_code=303)
    return templates.TemplateResponse(
        "review_preview.html",
        {"request": request, "post": data,
         "revise_options": reviser.REVISE_OPTIONS,
         "revise_recommended": reviser.recommended_keys(data.get("qa"))})


@app.post("/review/{post_id}/revise")
def review_revise(request: Request, post_id: int,
                  revise_cb: list[str] = Form([]), revise_note: str = Form("")):
    """差し戻し（リライト）: 選択した修正項目で記事をリライトして下書き更新。"""
    if not _authed(request):
        return RedirectResponse("/review/login", status_code=303)
    _ok, msg = reviser.revise_post(post_id, set(revise_cb), revise_note)
    return RedirectResponse(f"/review?msg={msg}", status_code=303)


@app.post("/review/{post_id}/publish")
def review_publish(request: Request, post_id: int):
    if not _authed(request):
        return RedirectResponse("/review/login", status_code=303)
    try:
        wordpress.set_post_status(post_id, "publish")
        sheet_log.log_status(post_id, "publish")
        # 内部リンク（#18）: 同カテゴリ記事を相互リンク更新（被リンク付与）
        internal_links.refresh_after_publish(post_id)
        msg = f"記事ID {post_id} を公開しました。"
    except Exception as e:  # noqa: BLE001
        msg = f"公開に失敗: {e}"
    return RedirectResponse(f"/review?msg={msg}", status_code=303)


@app.post("/review/{post_id}/reject")
def review_reject(request: Request, post_id: int):
    if not _authed(request):
        return RedirectResponse("/review/login", status_code=303)
    try:
        wordpress.trash_post(post_id)
        sheet_log.log_status(post_id, "trash")
        msg = f"記事ID {post_id} を却下（ゴミ箱）しました。"
    except Exception as e:  # noqa: BLE001
        msg = f"却下に失敗: {e}"
    return RedirectResponse(f"/review?msg={msg}", status_code=303)


@app.post("/review/{post_id}/to_draft")
def review_to_draft(request: Request, post_id: int):
    """公開済みを下書きに戻す / ゴミ箱から復元（承認待ちへ）。"""
    if not _authed(request):
        return RedirectResponse("/review/login", status_code=303)
    try:
        wordpress.set_post_status(post_id, "draft")
        sheet_log.log_status(post_id, "draft")
        msg = f"記事ID {post_id} を承認待ち（下書き）に戻しました。"
    except Exception as e:  # noqa: BLE001
        msg = f"操作に失敗: {e}"
    return RedirectResponse(f"/review?msg={msg}", status_code=303)


# ============================================================
# 商品選定スワイプUI（Issue #3/#12）: 候補をスワイプで承認/却下
# ============================================================
@app.get("/select", response_class=HTMLResponse)
def select_list(request: Request):
    if not review.enabled():
        return templates.TemplateResponse(
            "select.html", {"request": request, "disabled": True, "items": []})
    if not _authed(request):
        return RedirectResponse("/review/login", status_code=303)
    # 候補リストは描画後に /select/items で非同期ロード（ページは即表示・Issue #103）
    return templates.TemplateResponse(
        "select.html",
        {"request": request, "disabled": False, "error": "",
         "configured": candidates.enabled(), "crawl": _crawl_status()},
    )


@app.get("/select/items")
def select_items(request: Request):
    """スワイプ候補(pending)をJSONで返す（/select の非同期ロード用）。"""
    if not _authed(request):
        return JSONResponse({"ok": False, "error": "auth"}, status_code=401)
    try:
        items = candidates.list_by_status("pending", limit=50)
        return JSONResponse({"ok": True, "items": items})
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": str(e), "items": []})


@app.get("/crawl/status")
def crawl_status_json(request: Request):
    """クロール状況＋候補プール件数をJSONで返す（UIのポーリング用）。"""
    if not _authed(request):
        return JSONResponse({"ok": False, "error": "auth"}, status_code=401)
    st = _crawl_status()
    try:
        st["pending"] = len(candidates.list_by_status("pending", limit=200))
        st["approved"] = len(candidates.list_by_status("approved", limit=200))
    except Exception:  # noqa: BLE001
        pass
    return JSONResponse({"ok": True, "crawl": st})


@app.get("/manual", response_class=HTMLResponse)
def manual_select(request: Request, added: str = "", pending: str = "", msg: str = ""):
    """手動選定: ブックマークレットの導入＋URL/ASIN貼り付け＋選定済みリスト。"""
    if not review.enabled():
        return RedirectResponse("/review", status_code=303)
    if not _authed(request):
        return RedirectResponse("/review/login", status_code=303)
    try:
        approved = candidates.list_by_status("approved", limit=50)
    except Exception:  # noqa: BLE001
        approved = []
    return templates.TemplateResponse(
        "manual.html",
        {"request": request, "bookmarklet": _bookmarklet(_public_base(request)),
         "approved": approved, "added": added, "pending": pending, "msg": msg,
         "configured": candidates.enabled()})


@app.get("/select/add", response_class=HTMLResponse)
def select_add(request: Request, asin: str = "", title: str = "", price: str = "",
               image: str = "", brand: str = "", src: str = ""):
    """ブックマークレットからの1件追加（選定済み=approvedで候補プールへ）。"""
    if not _authed(request):
        return RedirectResponse("/review/login", status_code=303)
    asin = (asin or "").strip().upper()
    ok = False
    if re.fullmatch(r"[A-Z0-9]{10}", asin):
        url = src or f"https://www.amazon.co.jp/dp/{asin}"
        if (title or "").strip():
            # ブックマークレットがDOMから商品情報を取得済み → そのまま登録
            try:
                pr = int(re.sub(r"[^0-9]", "", price)) if price else None
            except ValueError:
                pr = None
            candidates.push([{"asin": asin, "title": title[:200], "price": pr,
                              "image": image or "", "brand": (brand or "")[:60],
                              "url": url, "source": "manual"}])
            ok = candidates.set_status(asin, "approved")
            if ok:
                _mark_manual([asin])
        else:
            # 商品情報なし → Xserverで補完してから登録（title/price/image欠落を防ぐ）
            ok = _queue_manual_urls([url]) > 0
    body = f"""<!doctype html><html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>選定追加</title><style>body{{font-family:-apple-system,sans-serif;background:#f1eef6;
margin:0;display:grid;place-items:center;min-height:100dvh;padding:20px;text-align:center;color:#2b2b3a}}
.box{{background:#fff;border-radius:16px;padding:28px 22px;box-shadow:0 6px 20px rgba(40,30,70,.1);max-width:420px}}
.ic{{font-size:46px}}.t{{font-weight:800;margin:8px 0}}.s{{font-size:13px;color:#888;word-break:break-all}}
a{{display:inline-block;margin-top:16px;background:#ff9f1c;color:#fff;text-decoration:none;
padding:11px 22px;border-radius:11px;font-weight:800}}</style></head><body><div class="box">
<div class="ic">{'✅' if ok else '⚠️'}</div>
<div class="t">{'選定リストに追加しました' if ok else '追加できませんでした'}</div>
<div class="s">{(title or asin) if ok else 'ASINが不正、または候補プール未設定です'}</div>
<a href="javascript:window.close()">閉じる</a> &nbsp;
<a href="{_public_base(request)}/manual" style="background:#6b6b8a">選定リストを見る</a>
</div></body></html>"""
    return HTMLResponse(body)


_SHORT_RE = re.compile(r"https?://(?:amzn\.asia|amzn\.to|a\.co)/\S+")


def _queue_manual_urls(urls: list[str]) -> int:
    """手動追加URLをXserver補完キュー(_manual_pending)へ。Xserverが商品データ取得→登録。"""
    urls = [u for u in dict.fromkeys(urls) if u]
    if not urls:
        return 0
    try:
        cur = overrides.load(force=True).get("_manual_pending", []) or []
        overrides.update({"_manual_pending": list(dict.fromkeys([*cur, *urls]))})
        return len(urls)
    except Exception:  # noqa: BLE001
        return 0


def _add_manual_text(text: str) -> int:
    """テキストからAmazon URL/ASIN/短縮リンクを抽出してXserver補完キューへ。受付件数を返す。

    Renderはamazonを読めない(title/price/image欠落)ため、即pushせず全てXserverで
    商品データを補完してから登録する（/manual/paste と /line/webhook が共用）。
    """
    urls: list[str] = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        for m in re.finditer(r"https?://\S*?/(?:dp|gp/product)/[A-Z0-9]{10}\S*", line):
            urls.append(m.group(0))
        for sm in _SHORT_RE.finditer(line):
            urls.append(sm.group(0))
        if not re.search(r"/(?:dp|gp/product)/", line) and not _SHORT_RE.search(line):
            bm = re.fullmatch(r"[A-Z0-9]{10}", line)
            if bm:
                urls.append(f"https://www.amazon.co.jp/dp/{bm.group(0)}")
    return _queue_manual_urls(urls)


# Threads用URL（楽天/@cosme/LIPS）。Amazonとはドメインが別なので自動振り分けできる
_THREADS_URL_RE = re.compile(
    r"https?://\S*?(?:item\.rakuten\.co\.jp|cosme\.net|cosme\.com|lipscosme\.com|lips\.jp)\S*")


def _add_threads_text(text: str) -> int:
    """テキストから Threads用URL(楽天/@cosme/LIPS) を抽出→取得待ちに積む。受付件数を返す。"""
    urls = list(dict.fromkeys(m.group(0) for m in _THREADS_URL_RE.finditer(text or "")))
    if not urls:
        return 0
    acc = _threads_acc()
    n = 0
    for u in urls:
        try:
            if threads_pipeline.enqueue_threads_url(acc, u.strip()):
                n += 1
        except Exception:  # noqa: BLE001
            continue
    return n


@app.post("/manual/paste")
def manual_paste(request: Request, bulk: str = Form("")):
    """URL/ASINを複数貼り付け→まとめて選定済みへ。短縮リンクはXserverで展開予約。"""
    if not _authed(request):
        return RedirectResponse("/review/login", status_code=303)
    q = _add_manual_text(bulk)
    if q == 0:
        return RedirectResponse("/manual?msg=no_asin", status_code=303)
    return RedirectResponse(f"/manual?pending={q}", status_code=303)


@app.post("/line/webhook")
async def line_webhook(request: Request):
    """LINE公式アカウントへAmazon商品を共有→選定リストに追加（Messaging API）。"""
    from core import line_client

    raw = await request.body()
    if not line_client.verify(raw, request.headers.get("X-Line-Signature", "")):
        return JSONResponse({"ok": False}, status_code=400)
    try:
        events = json.loads(raw).get("events", [])
    except Exception:  # noqa: BLE001
        events = []
    for ev in events:
        if ev.get("type") != "message" or (ev.get("message") or {}).get("type") != "text":
            continue
        token = ev.get("replyToken", "")
        user_id = (ev.get("source") or {}).get("userId", "")
        if not line_client.allowed(user_id):
            line_client.reply(token, "このアカウントからの追加は許可されていません。")
            continue
        msg = ev["message"]["text"]
        # URLの種類で自動振り分け: Amazon→ブログ / 楽天・@cosme・LIPS→Threads
        blog_n = _add_manual_text(msg)
        threads_n = _add_threads_text(msg)
        parts = []
        if blog_n:
            parts.append(f"🛒 ブログ {blog_n}件")
        if threads_n:
            parts.append(f"🧵 Threads {threads_n}件")
        if parts:
            line_client.reply(token, "✅ " + " / ".join(parts) + " を受け付けました！\n"
                              "数分以内に商品情報を取得して選定リストに反映します📝")
        else:
            line_client.reply(token, "商品リンクが見つかりませんでした🙏\n"
                              "・ブログ用: Amazonの商品リンク/ASIN\n"
                              "・Threads用: 楽天 / @cosme / LIPS の商品リンク\n"
                              "を「共有」して送ってください。")
    return JSONResponse({"ok": True})


@app.post("/select/{asin}/approve")
def select_approve(request: Request, asin: str):
    if not _authed(request):
        return JSONResponse({"ok": False, "error": "auth"}, status_code=401)
    ok = candidates.set_status(asin, "approved")
    return JSONResponse({"ok": bool(ok)})


@app.post("/select/{asin}/reject")
def select_reject(request: Request, asin: str):
    if not _authed(request):
        return JSONResponse({"ok": False, "error": "auth"}, status_code=401)
    ok = candidates.set_status(asin, "rejected")
    return JSONResponse({"ok": bool(ok)})


# ============================================================
# 設定/プロンプトのWeb編集（外部編集）
# ============================================================
@app.get("/settings", response_class=HTMLResponse)
def settings_form(request: Request, saved: str = ""):
    if not review.enabled():
        return RedirectResponse("/review", status_code=303)
    if not _authed(request):
        return RedirectResponse("/review/login", status_code=303)
    r = get_rules()
    sel = r.get("selection", {}) or {}
    art = r.get("article", {}) or {}
    pr = r.get("prompts", {}) or {}
    cand = r.get("candidates", {}) or {}
    gen = r.get("generation", {}) or {}
    d = {
        "interval_minutes": gen.get("interval_minutes", 20),
        "per_run": gen.get("per_run", 2),
        "min_price": sel.get("min_price", 3000),
        "exclude_keywords": "\n".join(sel.get("exclude_keywords", []) or []),
        "exclude_brands": "\n".join(sel.get("exclude_brands", []) or []),
        "seasonal_boost": bool(sel.get("seasonal_boost", True)),
        "min_chars": art.get("min_chars", 6000),
        "reviews_each": art.get("reviews_each", 5),
        "tone": art.get("tone", ""),
        "competitor_brands": "\n".join(art.get("competitor_brands", []) or []),
        "ground_company": bool(art.get("ground_company", True)),
        "style_guide": pr.get("style_guide") or prompts.STYLE_GUIDE_DEFAULT,
        "extra_instructions": pr.get("extra_instructions", ""),
        "title_format": pr.get("title_format") or prompts.DEFAULT_TITLE_FORMAT,
        "keywords": "\n".join(cand.get("keywords", []) or []),
        "source_urls": "\n".join(cand.get("source_urls", []) or []),
        "per_source": cand.get("per_source", 10),
        "max_total": cand.get("max_total", 40),
    }
    # 売れ筋ランキング: カタログ(部門→サブカテゴリ)をチェックボックス化
    cat = ranking_catalog.get_catalog()
    selected = set(cand.get("ranking_nodes", []) or [])
    groups: dict[str, list[dict]] = {}
    for it in cat["items"]:
        groups.setdefault(it["dept"], []).append(it)
    catalog_nodes = {it["node"] for it in cat["items"]}
    d["ranking_nodes"] = "\n".join(n for n in selected if n not in catalog_nodes)  # 手入力=カタログ外のみ
    # 楽天ジャンル（チェックボックス）
    rk_catalog = rakuten_catalog.get_catalog()
    rk_selected = {str(x) for x in (cand.get("rakuten_genres", []) or [])}
    from core import prompt_presets
    presets = {f: prompt_presets.view(f) for f in prompt_presets.BLOG_FIELDS}
    return templates.TemplateResponse(
        "settings.html",
        {"request": request, "d": d, "saved": saved, "can_save": overrides.enabled(),
         "crawl": _crawl_status(), "catalog_groups": groups, "presets": presets,
         "selected_nodes": selected, "catalog_updated": cat["updated_at"],
         "rakuten_catalog": rk_catalog, "rakuten_selected": rk_selected})


@app.post("/settings")
def settings_save(
    request: Request,
    min_price: str = Form("3000"), exclude_keywords: str = Form(""),
    exclude_brands: str = Form(""), seasonal_boost: str = Form(""),
    min_chars: str = Form("6000"), reviews_each: str = Form("5"), tone: str = Form(""),
    competitor_brands: str = Form(""), ground_company: str = Form(""),
    style_guide: str = Form(""), extra_instructions: str = Form(""),
    title_format: str = Form(""),
    keywords: str = Form(""), ranking_nodes: str = Form(""), source_urls: str = Form(""),
    ranking_nodes_cb: list[str] = Form([]),
    rakuten_genres_cb: list[str] = Form([]),
    per_source: str = Form("10"), max_total: str = Form("40"),
    interval_minutes: str = Form("20"), per_run: str = Form("2"),
):
    if not _authed(request):
        return RedirectResponse("/review/login", status_code=303)

    def _int(v, dflt):
        try:
            return int(str(v).strip())
        except ValueError:
            return dflt

    def _lines(v):
        return [ln.strip() for ln in v.splitlines() if ln.strip()]

    ov = {
        "selection": {"min_price": _int(min_price, 3000),
                      "exclude_keywords": _lines(exclude_keywords),
                      "exclude_brands": _lines(exclude_brands),
                      "seasonal_boost": seasonal_boost == "on"},
        "article": {"min_chars": _int(min_chars, 6000), "reviews_each": _int(reviews_each, 5),
                    "tone": tone.strip(), "competitor_brands": _lines(competitor_brands),
                    "ground_company": ground_company == "on"},
        # プロンプト(style_guide/extra/title)は prompt_presets で個別管理（ここでは触らない）
        "candidates": {"keywords": _lines(keywords),
                       "ranking_nodes": list(dict.fromkeys(
                           [*ranking_nodes_cb, *_lines(ranking_nodes)])),  # チェック＋手入力
                       "source_urls": _lines(source_urls),
                       "rakuten_genres": rakuten_genres_cb,
                       "per_source": _int(per_source, 10), "max_total": _int(max_total, 40)},
        "generation": {"interval_minutes": max(5, _int(interval_minutes, 20)),
                       "per_run": _int(per_run, 2)},
    }
    ok = overrides.update(ov)   # 他項目(_crawl_request等)を壊さず部分更新
    return RedirectResponse("/settings?saved=" + ("1" if ok else "fail"), status_code=303)


@app.post("/prompt-preset/{action}")
def prompt_preset_op(action: str, request: Request, field: str = Form(""),
                     name: str = Form(""), content: str = Form(""),
                     back: str = Form("/settings")):
    """プロンプトのプリセット操作（保存/追加/切替/削除）。項目ごとにA/B/C管理。"""
    if not _authed(request):
        return RedirectResponse("/review/login", status_code=303)
    from core import prompt_presets
    if action == "save":
        prompt_presets.save_content(field, name, content)
    elif action == "add":
        prompt_presets.add(field, name)
    elif action == "switch":
        prompt_presets.set_active(field, name)
    elif action == "delete":
        prompt_presets.delete(field, name)
    dest = back if back in ("/settings", "/threads/ai") else "/settings"
    return RedirectResponse(dest + "?saved=pp", status_code=303)


@app.get("/ai-settings", response_class=HTMLResponse)
def ai_settings_form(request: Request, saved: str = ""):
    """AI設定（モデル選択＋Gemini消費の概算/残）。検索系の/settingsとは分離。"""
    if not review.enabled():
        return RedirectResponse("/review", status_code=303)
    if not _authed(request):
        return RedirectResponse("/review/login", status_code=303)
    from datetime import datetime
    g = get_rules().get("gemini", {}) or {}
    usage = overrides.load(force=True).get("_gemini_usage") or {}
    rate = float(g.get("usd_jpy", 150) or 150)
    budget = float(g.get("budget_jpy", 0) or 0)
    cost_usd = float(usage.get("cost_usd", 0.0) or 0.0)
    spent = cost_usd * rate
    today = datetime.now().astimezone().strftime("%Y-%m-%d")
    today_jpy = float((usage.get("by_day") or {}).get(today, 0.0) or 0.0) * rate
    pct = min(100, round(spent / budget * 100)) if budget > 0 else 0
    days = [{"date": d, "jpy": round(c * rate, 1)}
            for d, c in sorted((usage.get("by_day") or {}).items(), reverse=True)[:14]]
    return templates.TemplateResponse("ai_settings.html", {
        "request": request, "saved": saved, "can_save": overrides.enabled(),
        "model": gemini_client.resolve_model(),
        "model_choices": gemini_client.MODEL_CHOICES,
        "budget_jpy": int(budget), "usd_jpy": int(rate),
        "calls": int(usage.get("calls", 0) or 0), "tokens": int(usage.get("tokens", 0) or 0),
        "cost_usd": round(cost_usd, 4), "spent_jpy": round(spent, 1),
        "remaining_jpy": round(budget - spent, 1), "today_jpy": round(today_jpy, 1),
        "pct": pct, "days": days, "updated": usage.get("updated", "")})


@app.post("/ai-settings")
def ai_settings_save(request: Request, gemini_model: str = Form(""),
                     budget_jpy: str = Form("")):
    if not _authed(request):
        return RedirectResponse("/review/login", status_code=303)
    valid = {m for m, _ in gemini_client.MODEL_CHOICES}
    g: dict = {}
    if gemini_model.strip() in valid:
        g["model"] = gemini_model.strip()
    try:
        g["budget_jpy"] = max(0, int(str(budget_jpy).strip()))
    except ValueError:
        pass
    ok = overrides.update({"gemini": g}) if g else True
    return RedirectResponse("/ai-settings?saved=" + ("1" if ok else "fail"), status_code=303)


@app.post("/ai-settings/reset")
def ai_settings_reset(request: Request):
    """消費カウンタ(_gemini_usage)をゼロに（請求実体ではなく自前集計のリセット）。"""
    if not _authed(request):
        return RedirectResponse("/review/login", status_code=303)
    data = overrides.load(force=True)
    data["_gemini_usage"] = {"calls": 0, "tokens": 0, "cost_usd": 0.0, "by_day": {}, "updated": ""}
    overrides.save(data)
    return RedirectResponse("/ai-settings?saved=reset", status_code=303)


@app.get("/threads", response_class=HTMLResponse)
def threads_home(request: Request):
    """Threadsトップ＝アカウント（媒体）選択。媒体を選ぶ→各媒体のメニューへ。追加もここ。"""
    if not review.enabled():
        return RedirectResponse("/review", status_code=303)
    if not _authed(request):
        return RedirectResponse("/review/login", status_code=303)
    aid = _active_acc_id(request)
    cards = []
    prods, drs, q = (threads_pipeline.products(), threads_pipeline.drafts(),
                     threads_pipeline.queue())
    for a in _threads_accounts():
        i = a.get("id")
        cards.append({
            "id": i, "name": a.get("name", i),
            "mode": threads_pipeline.account_publish_mode(a),
            "has_token": bool(threads_pipeline.account_token(a)),
            "select": sum(1 for p in prods if p.get("account") == i),
            "drafts": sum(1 for d in drs if d.get("account") == i),
            "queue": sum(1 for x in q if x.get("status") == "pending" and x.get("account") == i),
        })
    return templates.TemplateResponse("threads_accounts.html", {
        "request": request, "accounts": cards, "active": aid,
        "can_save": overrides.enabled()})


@app.get("/threads/posts", response_class=HTMLResponse)
def threads_review(request: Request, saved: str = "", view: str = "pr"):
    """Threads投稿の承認UI（媒体ごと）。view=pr=商品PR / musing=つぶやき でタブ分け。"""
    if not review.enabled():
        return RedirectResponse("/review", status_code=303)
    if not _authed(request):
        return RedirectResponse("/review/login", status_code=303)
    aid = _active_acc_id(request)
    threads_pipeline.reschedule_overdue()      # 期限切れの公開待ちを未来枠へ繰り上げ
    ds = [d for d in threads_pipeline.drafts() if d.get("account") == aid]
    pr_d = [d for d in ds if d.get("type") != "musing"]
    mu_d = [d for d in ds if d.get("type") == "musing"]
    view = "musing" if view == "musing" else "pr"
    q = [x for x in threads_pipeline.queue()
         if x.get("status") == "pending" and x.get("account") == aid]
    q.sort(key=lambda x: x.get("scheduled_at", 0))
    import datetime as _dt
    _jst = _dt.timezone(_dt.timedelta(hours=9))     # 公開時刻は日本時間で表示（サーバーTZ非依存）
    for x in q:
        x["when"] = _dt.datetime.fromtimestamp(x.get("scheduled_at", 0), _jst).strftime("%m/%d %H:%M")
    return templates.TemplateResponse("threads.html", {
        "request": request, "drafts": (mu_d if view == "musing" else pr_d),
        "queued": q, "saved": saved, "can_save": overrides.enabled(),
        "view": view, "pr_count": len(pr_d), "mu_count": len(mu_d),
        "publish_mode": threads_pipeline.account_publish_mode(aid)})


@app.get("/threads/stats")
def threads_stats(request: Request):
    """Threadsの各数値（ヘッダーのチップ／タブのバッジ／媒体切替・JS が取得）。操作中アカウントで集計。"""
    if not _authed(request):
        return JSONResponse({"ok": False}, status_code=401)
    aid = _active_acc_id(request)
    ds = [d for d in threads_pipeline.drafts() if d.get("account") == aid]
    pr_d = sum(1 for d in ds if d.get("type") != "musing")
    mu_d = sum(1 for d in ds if d.get("type") == "musing")
    qn = sum(1 for x in threads_pipeline.queue()
             if x.get("status") == "pending" and x.get("account") == aid)
    return JSONResponse({"ok": True,
        "accounts": [{"id": a.get("id"), "name": a.get("name", a.get("id"))}
                     for a in _threads_accounts()],
        "active": aid,
        "stats": {
            "select": sum(1 for p in threads_pipeline.products() if p.get("account") == aid),
            "gen": sum(1 for g in threads_pipeline.genqueue() if g.get("account") == aid),
            "drafts": pr_d + mu_d, "pr": pr_d, "musing": mu_d,
            "queue": qn,
            "fetch": sum(1 for f in threads_pipeline.fetchqueue() if f.get("account") == aid),
        }})


def _threads_accounts() -> list:
    return threads_pipeline.accounts()


def _threads_acc(account_id: str = "") -> dict:
    return threads_pipeline.get_account(account_id)


def _active_acc_id(request: Request) -> str:
    """操作中アカウント。Cookie th_acc 優先・無効なら先頭。"""
    ids = [a.get("id") for a in _threads_accounts()]
    cid = request.cookies.get("th_acc", "")
    return cid if cid in ids else (ids[0] if ids else "mmmtreees")


def _save_accounts(accs: list) -> bool:
    return overrides.update({"threads": {"accounts": accs}})


@app.post("/threads/account/switch")
def threads_account_switch(request: Request, acc_id: str = Form(""), back: str = Form("/threads")):
    if not _authed(request):
        return RedirectResponse("/review/login", status_code=303)
    dest = back if back.startswith("/threads") else "/threads"
    resp = RedirectResponse(dest, status_code=303)
    if acc_id in [a.get("id") for a in _threads_accounts()]:
        resp.set_cookie("th_acc", acc_id, max_age=60 * 60 * 24 * 365, httponly=True, samesite="lax")
    return resp


@app.post("/threads/account/add")
def threads_account_add(request: Request, name: str = Form("")):
    """新しい媒体（アカウント）を追加。idは自動採番。"""
    if not _authed(request):
        return RedirectResponse("/review/login", status_code=303)
    accs = list(_threads_accounts())
    existing = {a.get("id") for a in accs}
    n = 1                                       # 内部id=m{n}（アカウント名に紐づけない）
    while f"m{n}" in existing:
        n += 1
    new_id = f"m{n}"
    accs.append({"id": new_id, "name": (name.strip() or f"媒体{n}"),
                 "persona": "", "keywords": [], "genres": [], "per_run": 3,
                 "musing_per_run": 3, "token": "", "publish_mode": "draft_only"})
    _save_accounts(accs)
    resp = RedirectResponse(f"/threads/settings?acc={new_id}&saved=1", status_code=303)
    resp.set_cookie("th_acc", new_id, max_age=60 * 60 * 24 * 365, httponly=True, samesite="lax")
    return resp


@app.post("/threads/account/delete")
def threads_account_delete(request: Request, acc_id: str = Form("")):
    if not _authed(request):
        return RedirectResponse("/review/login", status_code=303)
    accs = [a for a in _threads_accounts() if a.get("id") != acc_id]
    if accs:                                   # 最後の1つは消さない
        _save_accounts(accs)
    return RedirectResponse("/threads/settings?saved=del", status_code=303)


@app.post("/threads/account/check")
def threads_account_check(request: Request, acc_id: str = Form(""), token: str = Form("")):
    """トークンを媒体に保存してから接続確認（me()でユーザー名取得・投稿はしない）。

    「保存」を押し忘れてもトークンが残るよう、接続確認＝保存も兼ねる。
    """
    if not _authed(request):
        return RedirectResponse("/review/login", status_code=303)
    tok = token.strip()
    if tok:                                    # 入力中トークンをその媒体に保存（保存忘れ防止）
        accts = list(_threads_accounts())
        for a in accts:
            if a.get("id") == acc_id:
                a["token"] = tok
                break
        _save_accounts(accts)
    else:
        tok = threads_pipeline.account_token(_threads_acc(acc_id))
    from core import threads_client
    try:
        info = threads_client.me(tok)
        un = info.get("username") or info.get("id") or "?"
        msg = f"ok:@{un}"
    except Exception as ex:  # noqa: BLE001
        msg = "ng:" + str(ex)[:120]
    import urllib.parse
    return RedirectResponse(f"/threads/settings?acc={acc_id}&chk=" + urllib.parse.quote(msg),
                            status_code=303)


@app.post("/threads/generate")
def threads_generate(request: Request, kind: str = Form("musing")):
    """つぶやき生成（商品は段階分離のため /threads/collect で候補収集→商品選定）。"""
    if not _authed(request):
        return RedirectResponse("/review/login", status_code=303)
    acc = _threads_acc(_active_acc_id(request))    # 操作中の媒体に生成
    try:
        made = threads_pipeline.generate_musings(acc, int(acc.get("musing_per_run", 3)))
    except Exception:  # noqa: BLE001
        made = 0
    # claudeモードは「生成待ち」に積むだけ＝/createで文章化。誤解を避けて別コードで案内
    code = (f"genmuq{made}" if threads_pipeline.gen_mode() == "claude" else f"genmu{made}")
    return RedirectResponse(f"/threads/posts?view=musing&saved={code}", status_code=303)


@app.post("/threads/collect")
def threads_collect(request: Request):
    """楽天キーワードから商品候補を収集 → 商品選定リストへ（キャプション未生成）。"""
    if not _authed(request):
        return RedirectResponse("/review/login", status_code=303)
    acc = _threads_acc(_active_acc_id(request))    # 操作中の媒体へ収集
    try:
        made = threads_pipeline.collect_products_rakuten(acc, int(acc.get("per_run", 6)))
    except Exception:  # noqa: BLE001
        made = 0
    return RedirectResponse(f"/threads/select?saved=col{made}", status_code=303)


@app.get("/threads/select", response_class=HTMLResponse)
def threads_select(request: Request, saved: str = "", m: str = ""):
    """商品選定タブ：取得した商品候補を見て、記事化する/却下を判断。"""
    if not review.enabled():
        return RedirectResponse("/review", status_code=303)
    if not _authed(request):
        return RedirectResponse("/review/login", status_code=303)
    aid = _active_acc_id(request)
    ps = sorted([p for p in threads_pipeline.products() if p.get("account") == aid],
                key=lambda p: p.get("created", 0), reverse=True)
    ds = [d for d in threads_pipeline.drafts() if d.get("account") == aid]
    return templates.TemplateResponse("threads_select.html", {
        "request": request, "saved": saved, "msg": m, "products": ps,
        "gen_mode": threads_pipeline.gen_mode(),
        "gen_pending": sum(1 for g in threads_pipeline.genqueue() if g.get("account") == aid),
        "pending": len([d for d in ds if d.get("type") == "pr"]),
        "musings": len([d for d in ds if d.get("type") == "musing"])})


@app.post("/threads/articleize")
def threads_articleize(request: Request, product_id: str = Form("")):
    """商品選定でOK→投稿作成（AIで5案ドラフト生成→投稿タブへ）。"""
    if not _authed(request):
        return RedirectResponse("/review/login", status_code=303)
    acc = _threads_acc(product_id.split("::")[0] if "::" in product_id else "")
    status = threads_pipeline.articleize(acc, product_id)
    code = {"done": "art", "queued": "artq", "fail": "artfail"}.get(status, "artfail")
    return RedirectResponse(f"/threads/select?saved={code}", status_code=303)


@app.post("/threads/product/reject")
def threads_product_reject(request: Request, product_id: str = Form("")):
    if not _authed(request):
        return RedirectResponse("/review/login", status_code=303)
    threads_pipeline.reject_product("", product_id)
    return RedirectResponse("/threads/select?saved=prej", status_code=303)


@app.get("/threads/add", response_class=HTMLResponse)
def threads_add(request: Request, saved: str = ""):
    """商品追加タブ：楽天URLを手動で貼って商品選定に追加・ラベル管理。"""
    if not review.enabled():
        return RedirectResponse("/review", status_code=303)
    if not _authed(request):
        return RedirectResponse("/review/login", status_code=303)
    return templates.TemplateResponse("threads_add.html", {
        "request": request, "saved": saved, "can_save": overrides.enabled(),
        "labels": threads_pipeline.labels(),
        "candidates": len(threads_pipeline.products())})


@app.get("/threads/products")
def threads_products_redirect():
    return RedirectResponse("/threads/select", status_code=303)


@app.post("/threads/labels/add")
def threads_label_add(request: Request, label: str = Form("")):
    if not _authed(request):
        return RedirectResponse("/review/login", status_code=303)
    threads_pipeline.add_label(label)
    return RedirectResponse("/threads/add?saved=label", status_code=303)


@app.post("/threads/labels/remove")
def threads_label_remove(request: Request, label: str = Form("")):
    if not _authed(request):
        return RedirectResponse("/review/login", status_code=303)
    threads_pipeline.remove_label(label)
    return RedirectResponse("/threads/add?saved=label", status_code=303)


@app.post("/threads/manual")
def threads_manual(request: Request, url: str = Form(""), label: str = Form("")):
    """楽天 / @cosme / LIPS の商品URL（＋ラベル）を貼ると、クロールして商品選定リストに追加。"""
    if not _authed(request):
        return RedirectResponse("/review/login", status_code=303)
    acc = _threads_acc(_active_acc_id(request))
    ok, msg = threads_pipeline.add_manual_url(acc, url.strip(), label.strip())
    import urllib.parse
    qs = "saved=" + ("man" if ok else "manfail") + "&m=" + urllib.parse.quote(msg or "")
    return RedirectResponse("/threads/select?" + qs, status_code=303)


@app.get("/threads/img-proxy")
def threads_img_proxy(request: Request, url: str):
    """画像を同一オリジンで配信（cropper.jsのCORS回避用）。楽天/自社ドメインのみ許可。"""
    if not _authed(request):
        return Response(status_code=401)
    import urllib.parse
    import urllib.request
    host = urllib.parse.urlparse(url).netloc
    if not (host.endswith("rakuten.co.jp") or host.endswith("ouchibase.com")):
        return Response(status_code=403)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=20) as r:
            data = r.read()
            ctype = r.headers.get("Content-Type", "image/jpeg")
        return Response(content=data, media_type=ctype,
                        headers={"Cache-Control": "public, max-age=600"})
    except Exception:  # noqa: BLE001
        return Response(status_code=502)


@app.post("/threads/crop")
def threads_crop(request: Request, draft_id: str = Form(...), image_url: str = Form(...),
                 x: float = Form(0), y: float = Form(0), w: float = Form(0), h: float = Form(0)):
    if not _authed(request):
        return JSONResponse({"ok": False}, status_code=401)
    new = threads_pipeline.crop_image(draft_id, image_url, x, y, w, h)
    return JSONResponse({"ok": bool(new), "url": new})


@app.post("/threads/approve")
def threads_approve(request: Request, draft_id: str = Form(...),
                    image_url: list[str] = Form([]), caption: str = Form(""),
                    reply_text: str = Form("")):
    if not _authed(request):
        return RedirectResponse("/review/login", status_code=303)
    d = next((x for x in threads_pipeline.drafts() if x["id"] == draft_id), None)
    is_musing = bool(d and d.get("type") == "musing")
    v = "musing" if is_musing else "pr"
    if not is_musing and not image_url:
        return RedirectResponse(f"/threads/posts?view={v}&saved=noimg", status_code=303)
    ok = threads_pipeline.approve(draft_id, image_url, caption, reply_text)
    return RedirectResponse(f"/threads/posts?view={v}&saved=" + ("ok" if ok else "fail"), status_code=303)


@app.post("/threads/reject")
def threads_reject(request: Request, draft_id: str = Form(...)):
    if not _authed(request):
        return RedirectResponse("/review/login", status_code=303)
    d = next((x for x in threads_pipeline.drafts() if x["id"] == draft_id), None)
    v = "musing" if (d and d.get("type") == "musing") else "pr"
    threads_pipeline.reject(draft_id)
    return RedirectResponse(f"/threads/posts?view={v}&saved=rej", status_code=303)


@app.post("/threads/draft/images")
def threads_draft_images(request: Request, draft_id: str = Form(...)):
    """ドラフトの画像候補を 参照元/楽天/Web検索 から取得し直して追加。"""
    if not _authed(request):
        return RedirectResponse("/review/login", status_code=303)
    n = threads_pipeline.fetch_more_images(draft_id)
    return RedirectResponse(f"/threads/posts?view=pr&saved=img{n}", status_code=303)


@app.post("/threads/withdraw")
def threads_withdraw(request: Request, item_id: str = Form(...)):
    """公開キューの未公開を取り下げ→承認待ちドラフトに戻す（再編集可・削除ではない）。"""
    if not _authed(request):
        return RedirectResponse("/review/login", status_code=303)
    item = next((x for x in threads_pipeline.queue() if x.get("id") == item_id), None)
    v = "musing" if (item and item.get("type") == "musing") else "pr"
    ok = threads_pipeline.withdraw(item_id)
    return RedirectResponse(f"/threads/posts?view={v}&saved=" + ("wd" if ok else "fail"), status_code=303)


def _threads_ai_ctx(request, *, model="", saved="", test=None):
    from core import prompt_presets
    _aid = _active_acc_id(request)
    return {
        "request": request, "saved": saved, "test": test,
        "can_save": overrides.enabled(),
        "gen_mode": threads_pipeline.gen_mode(),
        "gen_pending": len(threads_pipeline.genqueue()),
        "model": model or threads_pipeline.threads_model(),
        "model_choices": gemini_client.MODEL_CHOICES,
        "pr_preset": prompt_presets.view("th_pr_prompt"),
        "musing_preset": prompt_presets.view("th_musing_prompt"),
        "pr_styles": threads_pipeline.style_types("pr", _aid),
        "musing_styles": threads_pipeline.style_types("musing", _aid),
        "acc_name": _threads_acc(_aid).get("name", _aid),
    }


@app.post("/threads/style/{op}")
def threads_style_op(op: str, request: Request, kind: str = Form("pr"),
                     name: str = Form(""), ex: str = Form("")):
    """操作中の媒体のスタイル型（フック/ネタ型）を追加・削除・既定リセット。"""
    if not _authed(request):
        return RedirectResponse("/review/login", status_code=303)
    aid = _active_acc_id(request)
    k = "pr" if kind == "pr" else "musing"
    if op == "add":
        threads_pipeline.add_style(aid, k, name, ex)
    elif op == "delete":
        threads_pipeline.delete_style(aid, k, name)
    elif op == "reset":
        threads_pipeline.reset_style(aid, k)
    return RedirectResponse("/threads/ai?saved=style", status_code=303)


@app.get("/threads/ai", response_class=HTMLResponse)
def threads_ai_form(request: Request, saved: str = ""):
    """Threads専用のAI設定（モデル・プロンプト編集・テスト生成）。"""
    if not review.enabled():
        return RedirectResponse("/review", status_code=303)
    if not _authed(request):
        return RedirectResponse("/review/login", status_code=303)
    return templates.TemplateResponse("threads_ai.html", _threads_ai_ctx(request, saved=saved))


@app.post("/threads/ai")
def threads_ai_save(request: Request, gemini_model: str = Form(""),
                    gen_mode: str = Form("api")):
    """モデル・生成モードを保存（プロンプトはプリセットで個別管理）。"""
    if not _authed(request):
        return RedirectResponse("/review/login", status_code=303)
    valid = {m for m, _ in gemini_client.MODEL_CHOICES}
    g: dict = {"gen_mode": "claude" if gen_mode == "claude" else "api"}
    if gemini_model.strip() in valid:
        g["gemini_model"] = gemini_model.strip()
    overrides.update({"threads": g})
    return RedirectResponse("/threads/ai?saved=1", status_code=303)


@app.post("/threads/ai/test", response_class=HTMLResponse)
def threads_ai_test(request: Request, gemini_model: str = Form("")):
    """操作中の媒体のペルソナ/スタイル型でテスト生成（保存済みプロンプトを使う）。"""
    if not _authed(request):
        return RedirectResponse("/review/login", status_code=303)
    acc = _threads_acc(_active_acc_id(request))   # 操作中の媒体（m2=KAI等）で生成
    test = threads_pipeline.test_generate(
        acc, model=gemini_model.strip(),
        pr_tmpl=threads_pipeline.pr_prompt_template(),
        musing_tmpl=threads_pipeline.musing_prompt_template())
    return templates.TemplateResponse("threads_ai.html", _threads_ai_ctx(
        request, model=gemini_model, test=test))


@app.get("/threads/settings", response_class=HTMLResponse)
def threads_settings_form(request: Request, saved: str = "", acc: str = "", chk: str = ""):
    """Threads媒体の設定（複数アカウント＝媒体ごとに編集・切替・追加・削除）。"""
    if not review.enabled():
        return RedirectResponse("/review", status_code=303)
    if not _authed(request):
        return RedirectResponse("/review/login", status_code=303)
    t = get_rules().get("threads", {}) or {}
    accts = _threads_accounts()
    cur_id = acc if acc in [a.get("id") for a in accts] else _active_acc_id(request)
    a = _threads_acc(cur_id)
    sch = t.get("schedule", {}) or {}
    d = {
        "enabled": bool(t.get("enabled", False)),
        "acc_id": a.get("id"), "name": a.get("name", "アカウント"),
        "persona": a.get("persona", ""),
        "keywords": "\n".join(a.get("keywords") or []),
        "genres": "\n".join(str(g) for g in (a.get("genres") or [])),
        "per_run": a.get("per_run", 3),
        "musing_per_run": a.get("musing_per_run", 3),
        "token": a.get("token", ""),
        "publish_mode": threads_pipeline.account_publish_mode(a),
        "hours": ",".join(str(h) for h in (sch.get("hours") or [8, 12, 20])),
        "pub_per_run": sch.get("per_run", 1),
    }
    return templates.TemplateResponse("threads_settings.html", {
        "request": request, "d": d, "saved": saved, "chk": chk,
        "accounts": [{"id": x.get("id"), "name": x.get("name", x.get("id"))} for x in accts],
        "can_save": overrides.enabled()})


@app.post("/threads/settings")
def threads_settings_save(
    request: Request, acc_id: str = Form(""), enabled: str = Form(""), name: str = Form(""),
    persona: str = Form(""), keywords: str = Form(""), genres: str = Form(""),
    per_run: str = Form("3"), musing_per_run: str = Form("3"),
    token: str = Form(""), publish_mode: str = Form("draft_only"),
    hours: str = Form("8,12,20"), pub_per_run: str = Form("1"),
):
    if not _authed(request):
        return RedirectResponse("/review/login", status_code=303)

    def _ints(v, sep):
        return [int(x) for x in re.split(sep, v) if x.strip().isdigit()]

    accts = list(_threads_accounts())
    fields = {"name": name.strip() or "アカウント", "persona": persona.strip(),
              "keywords": [k.strip() for k in keywords.splitlines() if k.strip()],
              "genres": [g.strip() for g in genres.splitlines() if g.strip()],
              "per_run": int(per_run) if per_run.isdigit() else 3,
              "musing_per_run": int(musing_per_run) if musing_per_run.isdigit() else 3,
              "token": token.strip(),
              "publish_mode": "live" if publish_mode == "live" else "draft_only"}
    found = False
    for a in accts:
        if a.get("id") == acc_id:
            a.update(fields)
            found = True
            break
    if not found:                              # 該当が無ければ先頭を更新（後方互換）
        if accts:
            accts[0].update(fields)
        else:
            accts = [{"id": acc_id or "mmmtreees", **fields}]
    ov = {"threads": {"enabled": enabled == "on", "accounts": accts,
                      "schedule": {"hours": _ints(hours, r"[,\s]+") or [8, 12, 20],
                                   "per_run": int(pub_per_run) if pub_per_run.isdigit() else 1}}}
    ok = overrides.update(ov)
    dest = f"/threads/settings?acc={acc_id}&saved=" + ("1" if ok else "fail")
    return RedirectResponse(dest, status_code=303)


@app.post("/crawl/request")
def crawl_request(request: Request):
    """手動クロールを予約（Xserverが数分以内に実行）。"""
    if not _authed(request):
        return RedirectResponse("/review/login", status_code=303)
    import time
    now = int(time.time())
    # 予約状態を即座に表示できるよう _crawl_status も requested に（Xserverが拾うと running→done）
    ok = overrides.update({"_crawl_request": now,
                           "_crawl_status": {"state": "requested", "started_at": 0,
                                             "finished_at": 0, "requested_at": now,
                                             "message": "クロールを予約しました（数分以内に実行）"}})
    return RedirectResponse("/settings?saved=" + ("crawl" if ok else "fail"), status_code=303)


if __name__ == "__main__":
    import uvicorn

    s = get_settings()
    uvicorn.run("app:app", host=s.app_host, port=s.app_port, reload=True)
