"""
簡易Web UI（FastAPI）。
フォームから商品URL/手動情報を入力 → 記事生成 → プレビュー → WordPress下書き。

起動: uvicorn app:app --reload  （または python app.py）
"""
from __future__ import annotations

import re

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from core import (candidates, internal_links, overrides, pipeline, prompts,
                  ranking_catalog, rakuten_catalog, review, reviser, sheet_log,
                  wordpress)
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
    """死活監視・Renderコールドスタート防止用の軽量エンドポイント（認証/外部API無し）。"""
    return JSONResponse({"ok": True, "service": "affiliate-automation"})


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
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
        try:
            pr = int(re.sub(r"[^0-9]", "", price)) if price else None
        except ValueError:
            pr = None
        cand = {"asin": asin, "title": (title or "")[:200], "price": pr,
                "image": image or "", "brand": (brand or "")[:60],
                "url": src or f"https://www.amazon.co.jp/dp/{asin}", "source": "manual"}
        candidates.push([cand])
        ok = candidates.set_status(asin, "approved")
        if ok:
            _mark_manual([asin])
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


@app.post("/manual/paste")
def manual_paste(request: Request, bulk: str = Form("")):
    """URL/ASINを複数貼り付け→まとめて選定済みへ。短縮リンクはXserverで展開予約。"""
    if not _authed(request):
        return RedirectResponse("/review/login", status_code=303)
    asins: list[str] = []
    shorts: list[str] = []
    for raw in (bulk or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        m = re.search(r"/(?:dp|gp/product)/([A-Z0-9]{10})", line)
        if m:
            asins.append(m.group(1))
            continue
        sm = _SHORT_RE.search(line)
        if sm:                      # 短縮リンク（Amazonアプリの共有等）はXserverで解決
            shorts.append(sm.group(0))
            continue
        bm = re.fullmatch(r"[A-Z0-9]{10}", line)
        if bm:
            asins.append(line)

    seen: set[str] = set()
    added_asins: list[str] = []
    n = 0
    for a in asins:
        if a in seen:
            continue
        seen.add(a)
        candidates.push([{"asin": a, "url": f"https://www.amazon.co.jp/dp/{a}",
                          "source": "manual"}])
        if candidates.set_status(a, "approved"):
            n += 1
            added_asins.append(a)
    _mark_manual(added_asins)

    q = 0
    if shorts:
        try:
            cur = overrides.load(force=True).get("_manual_pending", []) or []
            merged = list(dict.fromkeys([*cur, *shorts]))
            overrides.update({"_manual_pending": merged})
            q = len(shorts)
        except Exception:  # noqa: BLE001
            q = 0

    if n == 0 and q == 0:
        return RedirectResponse("/manual?msg=no_asin", status_code=303)
    return RedirectResponse(f"/manual?added={n}&pending={q}", status_code=303)


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
    return templates.TemplateResponse(
        "settings.html",
        {"request": request, "d": d, "saved": saved, "can_save": overrides.enabled(),
         "crawl": _crawl_status(), "catalog_groups": groups,
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
        "prompts": {"style_guide": style_guide.strip(),
                    "extra_instructions": extra_instructions.strip(),
                    "title_format": title_format.strip()},
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
