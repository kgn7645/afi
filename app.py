"""
簡易Web UI（FastAPI）。
フォームから商品URL/手動情報を入力 → 記事生成 → プレビュー → WordPress下書き。

起動: uvicorn app:app --reload  （または python app.py）
"""
from __future__ import annotations

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from core import pipeline, wordpress
from core.config import ROOT, get_settings

app = FastAPI(title="アフィリエイト記事 自動化ツール")
templates = Jinja2Templates(directory=str(ROOT / "web" / "templates"))


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


if __name__ == "__main__":
    import uvicorn

    s = get_settings()
    uvicorn.run("app:app", host=s.app_host, port=s.app_port, reload=True)
