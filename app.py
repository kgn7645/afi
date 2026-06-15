"""
簡易Web UI（FastAPI）。
フォームから商品URL/手動情報を入力 → 記事生成 → プレビュー → WordPress下書き。

起動: uvicorn app:app --reload  （または python app.py）
"""
from __future__ import annotations

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from core import pipeline, review, wordpress
from core.config import ROOT, get_settings

app = FastAPI(title="アフィリエイト記事 自動化ツール")
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
def review_list(request: Request, msg: str = ""):
    if not review.enabled():
        return templates.TemplateResponse(
            "review.html", {"request": request, "disabled": True, "items": [], "msg": ""})
    if not _authed(request):
        return RedirectResponse("/review/login", status_code=303)
    try:
        items = review.list_review_items()
        err = ""
    except Exception as e:  # noqa: BLE001
        items, err = [], f"下書きの取得に失敗: {e}"
    return templates.TemplateResponse(
        "review.html",
        {"request": request, "disabled": False, "items": items, "msg": msg, "error": err},
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
        msg = f"記事ID {post_id} を却下（ゴミ箱）しました。"
    except Exception as e:  # noqa: BLE001
        msg = f"却下に失敗: {e}"
    return RedirectResponse(f"/review?msg={msg}", status_code=303)


if __name__ == "__main__":
    import uvicorn

    s = get_settings()
    uvicorn.run("app:app", host=s.app_host, port=s.app_port, reload=True)
