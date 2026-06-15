"""
簡易Web UI（FastAPI）。
フォームから商品URL/手動情報を入力 → 記事生成 → プレビュー → WordPress下書き。

起動: uvicorn app:app --reload  （または python app.py）
"""
from __future__ import annotations

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from core import (candidates, internal_links, overrides, pipeline, prompts,
                  review, sheet_log, wordpress)
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
        "review_preview.html", {"request": request, "post": data})


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
    try:
        items = candidates.list_by_status("pending", limit=50)
        err = ""
    except Exception as e:  # noqa: BLE001
        items, err = [], f"候補の取得に失敗: {e}"
    return templates.TemplateResponse(
        "select.html",
        {"request": request, "disabled": False, "items": items, "error": err,
         "configured": candidates.enabled()},
    )


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
    d = {
        "min_price": sel.get("min_price", 3000),
        "exclude_keywords": "\n".join(sel.get("exclude_keywords", []) or []),
        "min_chars": art.get("min_chars", 6000),
        "reviews_each": art.get("reviews_each", 5),
        "tone": art.get("tone", ""),
        "competitor_brands": "\n".join(art.get("competitor_brands", []) or []),
        "ground_company": bool(art.get("ground_company", True)),
        "style_guide": pr.get("style_guide") or prompts.STYLE_GUIDE_DEFAULT,
        "extra_instructions": pr.get("extra_instructions", ""),
        "keywords": "\n".join(cand.get("keywords", []) or []),
        "ranking_nodes": "\n".join(cand.get("ranking_nodes", []) or []),
        "per_source": cand.get("per_source", 10),
        "max_total": cand.get("max_total", 40),
    }
    return templates.TemplateResponse(
        "settings.html",
        {"request": request, "d": d, "saved": saved, "can_save": overrides.enabled()})


@app.post("/settings")
def settings_save(
    request: Request,
    min_price: str = Form("3000"), exclude_keywords: str = Form(""),
    min_chars: str = Form("6000"), reviews_each: str = Form("5"), tone: str = Form(""),
    competitor_brands: str = Form(""), ground_company: str = Form(""),
    style_guide: str = Form(""), extra_instructions: str = Form(""),
    keywords: str = Form(""), ranking_nodes: str = Form(""),
    per_source: str = Form("10"), max_total: str = Form("40"),
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
                      "exclude_keywords": _lines(exclude_keywords)},
        "article": {"min_chars": _int(min_chars, 6000), "reviews_each": _int(reviews_each, 5),
                    "tone": tone.strip(), "competitor_brands": _lines(competitor_brands),
                    "ground_company": ground_company == "on"},
        "prompts": {"style_guide": style_guide.strip(),
                    "extra_instructions": extra_instructions.strip()},
        "candidates": {"keywords": _lines(keywords), "ranking_nodes": _lines(ranking_nodes),
                       "per_source": _int(per_source, 10), "max_total": _int(max_total, 40)},
    }
    ok = overrides.update(ov)   # 他項目(_crawl_request等)を壊さず部分更新
    return RedirectResponse("/settings?saved=" + ("1" if ok else "fail"), status_code=303)


@app.post("/crawl/request")
def crawl_request(request: Request):
    """手動クロールを予約（Xserverが数分以内に実行）。"""
    if not _authed(request):
        return RedirectResponse("/review/login", status_code=303)
    import time
    ok = overrides.update({"_crawl_request": int(time.time())})
    return RedirectResponse("/settings?saved=" + ("crawl" if ok else "fail"), status_code=303)


if __name__ == "__main__":
    import uvicorn

    s = get_settings()
    uvicorn.run("app:app", host=s.app_host, port=s.app_port, reload=True)
