"""
B/C/E作業: Geminiでタイトル・コピー・本文・メタ情報を生成し、
WordPress投入用のHTMLに変換する。
"""
from __future__ import annotations

import re

from .config import get_rules
from .gemini_client import GeminiClient
from .models import Article, Product
from . import prompts


def _stars_md(n: float) -> str:
    full = int(n)
    half = 1 if (n - full) >= 0.5 else 0
    empty = 5 - full - half
    return "★" * full + ("☆" if half else "") + "☆" * empty


def _build_trust_block(product: Product, gemini: GeminiClient, rules: dict) -> tuple[str, float | None]:
    """信頼度評価ブロックのMarkdownと総合点を生成。"""
    try:
        raw = gemini.generate(prompts.trust_rating_prompt(product, rules),
                              temperature=0.5, thinking_budget=0)
        data = prompts.parse_json_response(raw)
    except Exception:
        return "", None

    lines: list[str] = []
    for r in data.get("ratings", []):
        stars = float(r.get("stars", 3))
        lines.append(f"{r.get('axis','')}: {_stars_md(stars)}（{stars}/5.0）")
        if r.get("comment"):
            lines.append(r["comment"])
        lines.append("")
    total = data.get("total")
    if total is not None:
        lines.append(f"**【総合評価】{_stars_md(float(total))}（{total}/5.0）**")
    if data.get("total_comment"):
        lines.append("")
        lines.append(data["total_comment"])
    return "\n".join(lines), (float(total) if total is not None else None)


_HEADING_RE = re.compile(r"^(#{2,4})\s+(.*)$")


def markdown_to_html(md: str) -> str:
    """軽量Markdown→HTML変換（##→h2, ###→h3, 箇条書き, 段落）。"""
    html: list[str] = []
    in_list = False

    def close_list() -> None:
        nonlocal in_list
        if in_list:
            html.append("</ul>")
            in_list = False

    for line in md.splitlines():
        raw = line.rstrip()
        stripped = raw.strip()
        if not stripped:
            close_list()
            continue
        m = _HEADING_RE.match(stripped)
        if m:
            close_list()
            level = len(m.group(1))
            text = _inline(m.group(2))
            html.append(f"<h{level}>{text}</h{level}>")
            continue
        if stripped.startswith(("- ", "* ", "・")):
            if not in_list:
                html.append("<ul>")
                in_list = True
            item = stripped[1:].strip() if stripped[0] in "-*" else stripped[1:].strip()
            html.append(f"<li>{_inline(item)}</li>")
            continue
        close_list()
        html.append(f"<p>{_inline(stripped)}</p>")
    close_list()
    return "\n".join(html)


def _inline(text: str) -> str:
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    text = text.replace("**", "")  # 対になっていない ** を除去
    text = re.sub(r"\[(.+?)\]\((.+?)\)", r'<a href="\2" target="_blank" rel="nofollow noopener">\1</a>', text)
    return text


def generate_article(product: Product, gemini: GeminiClient | None = None) -> Article:
    """記事一式を生成。"""
    gemini = gemini or GeminiClient()
    rules = get_rules()
    article = Article()

    # 1) タイトル・コピー・メタ
    meta_raw = gemini.generate(prompts.title_and_meta_prompt(product),
                               temperature=0.85, thinking_budget=0)
    try:
        meta = prompts.parse_json_response(meta_raw)
        # カテゴリー未指定なら推定値で補完（本文・見出しの品質確保）
        if not product.category and meta.get("category"):
            product.category = str(meta["category"]).strip()
        article.title = meta.get("title", "").strip()
        article.catch_copy = meta.get("catch_copy", "").strip()
        article.meta_description = meta.get("meta_description", "").strip()
        kw = meta.get("meta_keywords", [])
        article.meta_keywords = [k.strip() for k in kw if str(k).strip()]
    except Exception:
        article.raw_sections["meta_error"] = meta_raw

    # 2) 信頼度評価ブロック
    trust_md, trust_total = _build_trust_block(product, gemini, rules)
    article.trust_total = trust_total

    # 3) 本文
    body_md = gemini.generate(
        prompts.article_body_prompt(product, rules, trust_md), temperature=0.85
    )
    article.raw_sections["body_md"] = body_md
    article.body_html = markdown_to_html(body_md)

    # タイトル未取得時のフォールバック
    if not article.title:
        article.title = f"{product.brand}はどこの国のメーカー？{product.category}を徹底レビュー"

    return article
